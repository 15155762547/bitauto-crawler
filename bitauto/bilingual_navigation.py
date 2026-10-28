from bitauto.config import DATA_DIR

"""Bilingual sheet navigation and short official Chinese field descriptions."""

import argparse
import hashlib
import json
import os
import re
import time
from collections import defaultdict
from pathlib import Path

from bitauto.build_category_sheets import clean_field_name
from bitauto.category_names import CATEGORIES_ORDER, category_table_map
from bitauto.internal_release import Release


def official_labels(raw):
    candidates = defaultdict(set)
    evidence = {}
    sources = {source for source, _ in CATEGORIES_ORDER}
    for en_path in sorted(Path(raw).glob("params_*_en.json")):
        zh_path = en_path.with_name(en_path.name.removesuffix("_en.json") + "_zh.json")
        if not zh_path.exists():
            continue
        en = json.loads(en_path.read_text()).get("data") or {}
        zh = json.loads(zh_path.read_text()).get("data") or {}
        identities = lambda data: {
            (c.get("carId"), c.get("serialId"))
            for c in data.get("carList", [])
            if c.get("serialId")
        }
        if not identities(en).intersection(identities(zh)):
            continue
        zh_items = defaultdict(set)
        for group in zh.get("paramList", []):
            for item in group.get("items", []):
                name = item.get("name") or ""
                if re.search(r"[\u4e00-\u9fff]", name):
                    zh_items[item["id"]].add(name)
        for group in en.get("paramList", []):
            if group.get("name") not in sources:
                continue
            for item in group.get("items", []):
                if not item.get("name") or len(zh_items[item["id"]]) != 1:
                    continue
                key = (group["name"], clean_field_name(item["name"], item.get("unit")))
                label = next(iter(zh_items[item["id"]]))
                candidates[key].add(label)
                evidence_key = (key, label)
                if evidence_key not in evidence:
                    evidence[evidence_key] = {
                        "parameter_id": item["id"],
                        "en_path": str(en_path),
                        "zh_path": str(zh_path),
                        "en_sha256": hashlib.sha256(en_path.read_bytes()).hexdigest(),
                        "zh_sha256": hashlib.sha256(zh_path.read_bytes()).hexdigest(),
                    }

    return candidates, evidence


def record_hash(rows):
    return hashlib.sha256(
        json.dumps(
            sorted(rows, key=lambda r: r["record_id"]),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def records(release, tid, fields, label):
    label = f"{label}-{time.time_ns()}"
    merged = {}
    for part in range(0, len(fields), 50):
        offset = 0
        ids = set()
        while True:
            file = f"{tid}-{label}-{part}-{offset}.ndjson"
            result = release.cli(
                "+record-list",
                [
                    "--table-id",
                    tid,
                    "--offset",
                    str(offset),
                    "--limit",
                    "1000",
                    "--format",
                    "ndjson",
                    "--output",
                    file,
                    *[x for field in fields[part : part + 50] for x in ["--field-id", field]],
                ],
            )
            for line in (release.audit / file).read_text().splitlines():
                row = json.loads(line)
                ids.add(row["record_id"])
                merged.setdefault(row["record_id"], {}).update(row)
            if not result["has_more"]:
                break
            offset = result["next_offset"]
        assert ids == set(merged), (tid, "projection record IDs differ")
    return list(merged.values())


def prepare(release, raw):
    if (release.audit / "plan.json").exists():
        return json.loads((release.audit / "plan.json").read_text())
    tables = release.cli("+table-list", [])["data"]["tables"]
    release.save("tables-before.json", tables)
    candidates, evidence = official_labels(raw)
    lookup = category_table_map(tables)
    plan = []
    for source, display in CATEGORIES_ORDER:
        tid = lookup[display]
        schema = release.schema(tid)
        release.save(f"{tid}-schema-before.json", schema)
        descriptions = []
        unresolved = []
        for field in schema["fields"]:
            labels = candidates.get((source, field["name"]), set())
            if len(labels) == 1 and not field.get("description"):
                label = next(iter(labels))
                descriptions.append(
                    {
                        "field": field,
                        "description": label,
                        "evidence": evidence[((source, field["name"]), label)],
                    }
                )
            elif len(labels) > 1:
                unresolved.append({"name": field["name"], "official_candidates": sorted(labels)})
        rows = records(release, tid, [f["id"] for f in schema["fields"]], "before")
        plan.append(
            {
                "source": source,
                "table_id": tid,
                "old_name": schema["table"]["name"],
                "name": display,
                "descriptions": descriptions,
                "ambiguous": unresolved,
                "records": len(rows),
                "record_values_sha256": record_hash(rows),
            }
        )
        print("Backed up", display, len(descriptions), "Chinese labels", flush=True)
    for table in tables:
        if table["id"] not in {p["table_id"] for p in plan}:
            release.save(f"{table['id']}-schema-before.json", release.schema(table["id"]))
    release.save("plan.json", plan)
    return plan


def apply(release, plan):
    for table in plan:
        tid = table["table_id"]
        current = release.schema(tid)
        if current["table"]["name"] != table["name"]:
            assert current["table"]["name"] == table["old_name"]
            release.cli("+table-update", ["--table-id", tid, "--name", table["name"]])
        by_id = {f["id"]: f for f in current["fields"]}
        for item in table["descriptions"]:
            old = item["field"]
            actual = by_id[old["id"]]
            if actual.get("description") == item["description"]:
                continue
            assert actual == old, (tid, old["name"], "field changed after preview")
            definition = {k: v for k, v in old.items() if k != "id"}
            definition["description"] = item["description"]
            for delay in (30, 60, 120, None):
                try:
                    release.cli(
                        "+field-update",
                        [
                            "--table-id",
                            tid,
                            "--field-id",
                            old["id"],
                            "--json",
                            json.dumps(definition, ensure_ascii=False),
                            "--yes",
                        ],
                    )
                    break
                except RuntimeError as error:
                    if "800004135" not in str(error) or delay is None:
                        raise
                    print("Field method quota; waiting", delay, flush=True)
                    time.sleep(delay)
            time.sleep(0.5)
        print("Updated", table["name"], flush=True)
    time.sleep(3)
    before = json.loads((release.audit / "tables-before.json").read_text())
    after = release.cli("+table-list", [])["data"]["tables"]
    assert {t["id"]: t["records_count"] for t in before} == {
        t["id"]: t["records_count"] for t in after
    }
    category = {p["table_id"]: p for p in plan}
    checked = 0
    for table in after:
        tid = table["id"]
        schema = release.schema(tid)
        release.save(f"{tid}-schema-after.json", schema)
        original = json.loads((release.audit / f"{tid}-schema-before.json").read_text())
        changes = {
            d["field"]["id"]: d["description"]
            for d in category.get(tid, {}).get("descriptions", [])
        }
        expected = json.loads(json.dumps(original))
        for field in expected["fields"]:
            if field["id"] in changes:
                field["description"] = changes[field["id"]]
        if tid in category:
            expected["table"]["name"] = category[tid]["name"]
        assert schema["table"] == expected["table"], tid
        assert {f["id"]: f for f in schema["fields"]} == {f["id"]: f for f in expected["fields"]}, (
            tid
        )
        assert schema["views"] == original["views"], (tid, "view settings changed")
        if tid in category:
            rows = records(release, tid, [f["id"] for f in schema["fields"]], "after")
            assert record_hash(rows) == category[tid]["record_values_sha256"], tid
            checked += len(rows)
    release.save(
        "verification.json",
        {
            "verified": True,
            "renamed_categories": len(plan),
            "chinese_field_descriptions": sum(len(p["descriptions"]) for p in plan),
            "ambiguous_labels_not_guessed": sum(len(p["ambiguous"]) for p in plan),
            "parameter_records_compared": checked,
            "table_ids_and_counts_unchanged": True,
            "field_names_types_options_and_ids_unchanged": True,
            "record_values_unchanged": True,
            "views_unchanged": True,
        },
    )
    print("VERIFIED", checked, "parameter rows", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-dir", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    release = Release(args.audit_dir)
    os.chdir(release.audit)
    raw = DATA_DIR / "raw"
    plan = prepare(release, raw)
    print(
        "Preview",
        len(plan),
        "tables",
        sum(len(p["descriptions"]) for p in plan),
        "labels",
        flush=True,
    )
    if args.apply:
        apply(release, plan)


if __name__ == "__main__":
    main()
