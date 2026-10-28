from bitauto.config import DATA_DIR

"""Fill only empty catalog cells from identity-matched saved official snapshots."""
import argparse
import datetime
import json
import time
from collections import Counter, defaultdict
from pathlib import Path

from bitauto.bilingual_navigation import record_hash
from bitauto.build_category_sheets import clean_field_name
from bitauto.category_names import CATEGORIES_ORDER, category_table_map
from bitauto.diff_review import scalar, snapshot_index
from bitauto.diff_rules import MISSING, RULE_VERSION, compare_values, normalized
from bitauto.feishu_sync import TABLE_SERIES, TABLE_TRIMS
from bitauto.internal_release import Release
from bitauto.param_values import render_param_value

STATUS_FIELDS = ("核查状态", "问题说明")


def status_columns(fields):
    # Keep technical review columns out of daily views.
    return [field for field in fields if field not in {*STATUS_FIELDS, "图库状态"}]


def empty(value):
    return value is None or value == "" or value == []


def summarize(issues):
    groups = defaultdict(list)
    for issue in issues:
        kind = issue["kind"]
        if kind == "资料不足":
            en = normalized((issue.get("en") or {}).get("value"))
            zh = normalized((issue.get("zh") or {}).get("value"))
            if en in MISSING and zh in MISSING:
                kind = "源站待公布" if "tbc" in {en, zh} else "双侧未披露"
            elif en in MISSING or zh in MISSING:
                kind = "单边资料缺失"
            elif "单位" in issue["reason"] and "等价规则" not in issue["reason"]:
                kind = "单位口径待核"
            else:
                kind = "双语释义待核"
        groups[kind].append(issue["parameter"])
    priority = (
        "身份待核",
        "疑似差异",
        "单边资料缺失",
        "单位口径待核",
        "双语释义待核",
        "源站待公布",
        "双侧未披露",
    )
    status = next((kind for kind in priority if groups[kind]), "快照未见明确冲突")
    counts = "；".join(f"{k}{len(groups[k])}项" for k in priority if groups[k])
    examples = "、".join(dict.fromkeys(groups[status]))[:100]
    text = "非实时双语快照｜" + (counts or "已保存参数未见明确冲突")
    if examples:
        text += f"｜例：{examples}"
    return status, text + "｜原抓取时间未获证实"


def params(entry):
    result = defaultdict(list)
    cid = entry["car"]["carId"]
    for group in entry["snapshot"]["data"].get("paramList", []):
        for item in group.get("items", []):
            for val in item.get("paramValues", []):
                if val.get("id") == cid:
                    result[item["id"]].append(
                        {
                            "group": group["name"],
                            "name": item["name"],
                            "field": clean_field_name(item["name"], item.get("unit")),
                            "value": render_param_value(val),
                        }
                    )
    return {pid: values[0] for pid, values in result.items() if len(values) == 1}


def resolve(row, index):
    code = str(scalar(row.get("分站代码")) or "").upper()
    cid, sid = row.get("来源款型ID"), row.get("来源车系ID")
    if not cid or not sid or str(row.get("款型唯一键", "")).upper() != f"{code}_{int(cid)}":
        return None, None
    entries = []
    for lang in ("en", "zh"):
        matches = index.get((code, int(cid), lang), [])
        entries.append(
            matches[0] if len(matches) == 1 and matches[0]["car"].get("serialId") == sid else None
        )
    return tuple(entries)


def candidate(field, value):
    if empty(value) or value == "-":
        return None
    if field["type"] == "text":
        return str(value)
    if field["type"] == "select" and str(value) in {o["name"] for o in field["options"]}:
        return [str(value)]
    return None


def fill_patch(row, fields, values):
    return {
        name: converted
        for name, value in values.items()
        if name in fields
        and empty(row.get(name))
        and (converted := candidate(fields[name], value)) is not None
    }


def read_rows(release, tid, fields, label, limit=1000):
    merged = {}
    stamp = time.time_ns()
    for start in range(0, len(fields), 50):
        offset = 0
        seen = set()
        while True:
            filename = f"{tid}-{label}-{stamp}-{start}-{offset}.ndjson"
            result = release.cli(
                "+record-list",
                [
                    "--table-id",
                    tid,
                    "--offset",
                    str(offset),
                    "--limit",
                    str(limit),
                    "--format",
                    "ndjson",
                    "--output",
                    filename,
                    *[x for f in fields[start : start + 50] for x in ("--field-id", f)],
                ],
            )
            for line in (release.audit / filename).read_text().splitlines():
                row = json.loads(line)
                seen.add(row["record_id"])
                merged.setdefault(row["record_id"], {}).update(row)
            if not result["has_more"]:
                break
            offset = result["next_offset"]
        assert seen == set(merged), (tid, "projection identity mismatch")
    return list(merged.values())


def prepare(release, raw):
    path = release.audit / "plan.json"
    if path.exists():
        return json.loads(path.read_text())
    tables = release.cli("+table-list", [])["data"]["tables"]
    release.save("tables-before.json", tables)
    schemas, rows = {}, {}
    for table in tables:
        tid = table["id"]
        schemas[tid] = release.schema(tid)
        release.save(f"{tid}-schema-before.json", schemas[tid])
        fields = [f["id"] for f in schemas[tid]["fields"]]
        rows[tid] = read_rows(
            release, tid, fields, "before", 100 if tid in {TABLE_SERIES, TABLE_TRIMS} else 1000
        )
        assert len(rows[tid]) == table["records_count"]
        release.save(f"{tid}-rows-before.json", rows[tid])
        print("Backed up", table["name"], len(rows[tid]), flush=True)
    for field in schemas[TABLE_TRIMS]["fields"]:
        if field["name"] in STATUS_FIELDS:
            assert field["type"] == "text" and field.get("description", "").startswith(
                "程序核查已保存的同站同款双语快照"
            ), "Refuse to replace an unrelated existing status field"
    index = snapshot_index(raw)
    lookup = category_table_map(tables)
    category = {source: lookup[display] for source, display in CATEGORIES_ORDER}
    identities = {}
    for tid in category.values():
        counts = Counter(str(row.get("款型唯一键", "")).upper() for row in rows[tid])
        identities[tid] = {
            str(row.get("款型唯一键", "")).upper(): row
            for row in rows[tid]
            if counts[str(row.get("款型唯一键", "")).upper()] == 1
        }
    definitions = {tid: {f["name"]: f for f in s["fields"]} for tid, s in schemas.items()}
    patches = defaultdict(dict)
    evidence, reviews = [], []
    trimmed = Counter(str(t.get("款型唯一键", "")).upper() for t in rows[TABLE_TRIMS])
    for row in rows[TABLE_TRIMS]:
        key = str(row.get("款型唯一键", "")).upper()
        en, zh = resolve(row, index) if trimmed[key] == 1 else (None, None)
        en_params, zh_params = params(en) if en else {}, params(zh) if zh else {}
        field_counts = Counter((p["group"], p["field"]) for p in en_params.values())
        issues = []
        if not en or not zh:
            issues.append(
                {
                    "kind": "身份待核",
                    "parameter": "来源身份",
                    "reason": "缺少唯一匹配的同站同款中英文快照",
                }
            )
        blocked = set()
        for pid in en_params.keys() | zh_params.keys():
            a, b = en_params.get(pid), zh_params.get(pid)
            comparison = compare_values(
                a["value"] if a else None,
                b["value"] if b else None,
                param_id=pid,
                name_en=a["name"] if a else "",
                name_zh=b["name"] if b else "",
                identity_verified=bool(en and zh),
            )
            if comparison.result == "疑似差异":
                blocked.add(pid)
            if comparison.result not in {"一致", "等价表达"}:
                issues.append(
                    {
                        "kind": comparison.result,
                        "parameter": (a or b)["name"],
                        "parameter_id": pid,
                        "en": a,
                        "zh": b,
                        "reason": comparison.reason,
                    }
                )
            if (
                not en
                or pid in blocked
                or not a
                or a["group"] not in category
                or field_counts[(a["group"], a["field"])] != 1
            ):
                continue
            tid = category[a["group"]]
            target = identities[tid].get(key)
            if target:
                patch = fill_patch(target, definitions[tid], {a["field"]: a["value"]})
                if patch:
                    patches[tid].setdefault(target["record_id"], {}).update(patch)
                    evidence.append(
                        {
                            "table_id": tid,
                            "record_id": target["record_id"],
                            "key": key,
                            "parameter_id": pid,
                            "fields": patch,
                            "source": en["snapshot"]["path"],
                            "sha256": en["snapshot"]["sha256"],
                        }
                    )
        if en:
            values = {
                "款型英文名": en["car"].get("carName"),
                "指导价(EN)": en["car"].get("referPriceWithUnit"),
            }
            if zh:
                name = zh["car"].get("carName")
                if name and any("\u4e00" <= c <= "\u9fff" for c in name):
                    values["款型中文名"] = name
                values["指导价(ZH)"] = zh["car"].get("referPriceWithUnit")
            for field, pid in {
                "轴距(mm)": 17,
                "驱动形式": 87,
                "最高车速(km/h)": -19,
                "百公里加速(s)": -22,
            }.items():
                if pid in en_params and pid not in blocked:
                    values[field] = en_params[pid]["value"]
            patch = fill_patch(row, definitions[TABLE_TRIMS], values)
            if patch:
                patches[TABLE_TRIMS].setdefault(row["record_id"], {}).update(patch)
                evidence.append(
                    {
                        "table_id": TABLE_TRIMS,
                        "record_id": row["record_id"],
                        "key": key,
                        "fields": patch,
                        "source": en["snapshot"]["path"],
                        "sha256": en["snapshot"]["sha256"],
                        "zh_source": zh["snapshot"]["path"] if zh else None,
                    }
                )
        status, text = summarize(issues)
        patches[TABLE_TRIMS].setdefault(row["record_id"], {}).update(
            {"核查状态": status, "问题说明": text}
        )
        reviews.append(
            {
                "key": key,
                "record_id": row["record_id"],
                "status": status,
                "issues": issues,
                "en_source": en["snapshot"]["path"] if en else None,
                "zh_source": zh["snapshot"]["path"] if zh else None,
                "en_sha256": en["snapshot"]["sha256"] if en else None,
                "zh_sha256": zh["snapshot"]["sha256"] if zh else None,
                "captured_at": None,
            }
        )
    release.save("review-details.json", reviews)
    release.save("fill-evidence.json", evidence)
    plan = {
        "created_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "comparison_rule_version": RULE_VERSION,
        "summary_rule_version": "snapshot-quality-20261028.1",
        "patches": dict(patches),
        "status_counts": dict(Counter(r["status"] for r in reviews)),
        "filled_cells": sum(len(e["fields"]) for e in evidence),
        "fill_events": len(evidence),
    }
    release.save("plan.json", plan)
    return plan


def apply(release, plan):
    tables = json.loads((release.audit / "tables-before.json").read_text())
    s = release.schema(TABLE_TRIMS)
    visible_path = release.audit / "trim-views-before.json"
    if not visible_path.exists():
        release.save(
            visible_path.name,
            {
                v["id"]: release.cli(
                    "+view-get-visible-fields", ["--table-id", TABLE_TRIMS, "--view-id", v["id"]]
                )["data"]["visible_fields"]
                for v in s["views"]
            },
        )
    visible_before = json.loads(visible_path.read_text())
    existing = {f["name"] for f in s["fields"]}
    missing = [
        {
            "name": n,
            "type": "text",
            "description": "程序核查已保存的同站同款双语快照；非实时官网结果。完整证据保留于项目外盘。",
        }
        for n in STATUS_FIELDS
        if n not in existing
    ]
    if missing:
        release.cli(
            "+field-create",
            ["--table-id", TABLE_TRIMS, "--json", json.dumps(missing, ensure_ascii=False)],
        )
    for tid, changes in plan["patches"].items():
        original = {
            r["record_id"]: r
            for r in json.loads((release.audit / f"{tid}-rows-before.json").read_text())
        }
        schema = release.schema(tid)
        wanted = {"record_id", *[name for patch in changes.values() for name in patch]}
        fields = [f["id"] for f in schema["fields"] if f["name"] in wanted]
        current = {r["record_id"]: r for r in read_rows(release, tid, fields, "preflight", 1000)}
        updates = {}
        for rid, patch in changes.items():
            delta = {}
            for name, value in patch.items():
                actual = current[rid].get(name)
                if actual == value:
                    continue
                assert actual == original[rid].get(name) or (
                    empty(actual) and empty(original[rid].get(name))
                ), (tid, rid, name, "changed since backup")
                if name not in STATUS_FIELDS:
                    assert empty(actual), (tid, rid, name, "refuse overwrite")
                delta[name] = value
            if delta:
                updates[rid] = delta
        items = list(updates.items())
        for start in range(0, len(items), 100):
            release.cli(
                "+record-batch-update",
                [
                    "--table-id",
                    tid,
                    "--json",
                    json.dumps(
                        {"update_records": dict(items[start : start + 100])}, ensure_ascii=False
                    ),
                ],
            )
        print("Applied", tid, len(items), "rows", flush=True)
    # Add only the two authorized columns to existing normal trim views.
    before = json.loads((release.audit / f"{TABLE_TRIMS}-schema-before.json").read_text())
    changed_views = {}
    for view in before["views"]:
        old = visible_before[view["id"]]
        fields = status_columns(old) if view["name"] in {"表格", "配置查询"} else old
        release.visible(TABLE_TRIMS, view["id"], fields)
        changed_views[view["id"]] = fields
    after_tables = release.cli("+table-list", [])["data"]["tables"]
    assert {t["id"]: (t["name"], t["records_count"]) for t in tables} == {
        t["id"]: (t["name"], t["records_count"]) for t in after_tables
    }
    checked = 0
    for table in tables:
        tid = table["id"]
        original = json.loads((release.audit / f"{tid}-schema-before.json").read_text())
        actual = release.schema(tid)
        old_fields = {f["id"]: f for f in original["fields"]}
        new_fields = {f["id"]: f for f in actual["fields"]}
        assert all(new_fields.get(k) == v for k, v in old_fields.items()), tid
        added = [f for f in actual["fields"] if f["id"] not in old_fields]
        original_names = {f["name"] for f in original["fields"]}
        assert {f["name"] for f in added} == (
            set(STATUS_FIELDS) - original_names if tid == TABLE_TRIMS else set()
        )
        expected_views = json.loads(json.dumps(original["views"]))
        for v in expected_views:
            if v["id"] in changed_views:
                v["_meta"]["visible_fields"] = f"{len(changed_views[v['id']])} fields"
                out = release.cli(
                    "+view-get-visible-fields", ["--table-id", tid, "--view-id", v["id"]]
                )
                assert out["data"]["visible_fields"] == changed_views[v["id"]]
        assert actual["views"] == expected_views, tid
        rows = read_rows(
            release,
            tid,
            [f["id"] for f in actual["fields"]],
            "after",
            100 if tid in {TABLE_SERIES, TABLE_TRIMS} else 1000,
        )
        expected = json.loads((release.audit / f"{tid}-rows-before.json").read_text())
        for row in expected:
            row.update(plan["patches"].get(tid, {}).get(row["record_id"], {}))
        # Empty new status cells are absent from the pre-apply backup only.
        assert record_hash(rows) == record_hash(expected), (tid, "record values differ")
        checked += len(rows)
        release.save(f"{tid}-schema-after.json", actual)
        print("Verified", table["name"], len(rows), flush=True)
    release.save(
        "verification.json",
        {
            "verified": True,
            "rows_compared": checked,
            "filled_cells": plan["filled_cells"],
            "status_counts": plan["status_counts"],
            "source_mode": "saved official snapshots; no network",
            "non_target_values_and_original_fields_unchanged": True,
        },
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-dir", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    release = Release(args.audit_dir)
    plan = prepare(release, DATA_DIR / "raw")
    print("PREVIEW", plan["filled_cells"], "cells; statuses", plan["status_counts"], flush=True)
    if args.apply:
        apply(release, plan)


if __name__ == "__main__":
    main()
