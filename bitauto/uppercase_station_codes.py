"""Back up and migrate station codes/key prefixes, verifying every record."""

import argparse
import json
import time
from pathlib import Path

from bitauto.build_category_sheets import run_cli

FIELDS = {"分站代码", "车系唯一键", "款型唯一键", "差异唯一键"}


def uppercase_value(v):
    if isinstance(v, list):
        return [uppercase_value(x) for x in v]
    if isinstance(v, str):
        a, sep, b = v.partition("_")
        return a.upper() + sep + b
    return v


def normalize_payload(value):
    """Uppercase only designated Feishu fields, preserving source IDs and URLs."""
    if isinstance(value, dict):
        return {
            k: uppercase_value(v) if k in FIELDS else normalize_payload(v) for k, v in value.items()
        }
    if isinstance(value, list):
        return [normalize_payload(v) for v in value]
    return value


def read_rows(tid, audit, label):
    rows, offset = [], 0
    while True:
        out = audit / f"{tid}-{label}-{offset}.ndjson"
        r = run_cli(
            "+record-list",
            [
                "--table-id",
                tid,
                "--offset",
                str(offset),
                "--limit",
                "2000",
                "--format",
                "ndjson",
                "--output",
                str(out),
            ]
            + (["--overwrite"] if label == "after" else []),
        )
        if r.get("ok") is False:
            raise RuntimeError(r)
        rows.extend(json.loads(x) for x in out.read_text().splitlines() if x.strip())
        if not r["has_more"]:
            return rows
        offset = r["next_offset"]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--apply", action="store_true")
    p.add_argument("--only-table")
    p.add_argument("--verify-only", action="store_true")
    args = p.parse_args()
    apply = args.apply or args.verify_only
    audit = Path.cwd() / "uppercase-codes-20261023"
    audit.mkdir(exist_ok=True)
    planfile = audit / "plan.json"
    if not apply:
        plan = []
        for t in run_cli("+table-list", [])["data"]["tables"]:
            tid = t["id"]
            info = run_cli("+table-get", ["--table-id", tid])["data"]
            fields = [f for f in info["fields"] if f["name"] in FIELDS]
            rows = read_rows(tid, audit, "before")
            changes = {}
            for row in rows:
                delta = {
                    f["name"]: uppercase_value(row.get(f["name"]))
                    for f in fields
                    if uppercase_value(row.get(f["name"])) != row.get(f["name"])
                }
                if delta:
                    changes[row["record_id"]] = delta
            for f in fields:
                if f["name"] != "分站代码":
                    keys = [uppercase_value(r[f["name"]]) for r in rows if r.get(f["name"])]
                    assert len(keys) == len(set(keys)), f"Key collision: {t['name']}"
            plan.append({"table": t, "fields": fields, "updates": changes})
            print(f"Backup {t['name']}: {len(rows)} rows, {len(changes)} changes", flush=True)
        planfile.write_text(json.dumps(plan, ensure_ascii=False))
        return
    report = []
    for item in json.loads(planfile.read_text()):
        tid = item["table"]["id"]
        if args.verify_only or (args.only_table and tid != args.only_table):
            continue
        selects = [f for f in item["fields"] if f["type"] == "select"]
        for f in selects:
            d = {k: v for k, v in f.items() if k != "id"}
            d["options"] = f["options"] + [
                dict(o, name=o["name"].upper())
                for o in f["options"]
                if o["name"] != o["name"].upper()
            ]
            r = run_cli(
                "+field-update",
                ["--table-id", tid, "--field-id", f["id"], "--json", json.dumps(d), "--yes"],
            )
            assert r.get("ok"), r
        if selects:
            time.sleep(3)
        pairs = list(item["updates"].items())
        for i in range(0, len(pairs), 200):
            r = run_cli(
                "+record-batch-update",
                [
                    "--table-id",
                    tid,
                    "--json",
                    json.dumps({"update_records": dict(pairs[i : i + 200])}),
                ],
            )
            assert r.get("ok"), r
        if selects:
            time.sleep(3)
        for f in selects:
            d = {k: v for k, v in f.items() if k != "id"}
            d["options"] = [dict(o, name=o["name"].upper()) for o in f["options"]]
            r = run_cli(
                "+field-update",
                ["--table-id", tid, "--field-id", f["id"], "--json", json.dumps(d), "--yes"],
            )
            assert r.get("ok"), r
        print(f"Wrote {item['table']['name']}", flush=True)
    time.sleep(3)
    for item in json.loads(planfile.read_text()):
        tid = item["table"]["id"]
        pairs = list(item["updates"].items())
        before = {}
        for path in audit.glob(f"{tid}-before-*.ndjson"):
            for line in path.read_text().splitlines():
                row = json.loads(line)
                before[row["record_id"]] = row
        after = read_rows(tid, audit, "after")
        assert set(before) == {r["record_id"] for r in after}
        for row in after:
            expected = dict(before[row["record_id"]])
            expected.update(item["updates"].get(row["record_id"], {}))
            assert expected == row, f"Readback mismatch: {tid}/{row['record_id']}"
        report.append(
            {
                "table": item["table"]["name"],
                "rows": len(after),
                "changed": len(pairs),
                "verified": True,
            }
        )
        (audit / "verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(f"Updated and verified {item['table']['name']}: {len(pairs)}", flush=True)


if __name__ == "__main__":
    main()
