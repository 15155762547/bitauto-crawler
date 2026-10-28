"""Remove review hints from daily views; retain catalog values and external evidence."""

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from bitauto.business_views import NOTE
from bitauto.complete_snapshot_data import read_rows
from bitauto.feishu_sync import TABLE_SERIES, TABLE_TRIMS
from bitauto.internal_release import Release

HIDDEN = {"核查状态", "问题说明", "图库状态"}
PREFIXES = (
    "存在参数疑似差异，请先核实；",
    "来源身份待核，请先核实；",
    "部分参数单边缺资料；",
    "部分参数单位口径待核；",
)


def clean_note(value):
    result = value
    for prefix in PREFIXES:
        if result.startswith(prefix):
            result = result.removeprefix(prefix)
    return result.replace("图集身份待核", "图集暂不可用").replace("图集状态待确认", "图集暂不可用")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-dir", type=Path, required=True)
    args = parser.parse_args()
    r = Release(args.audit_dir)
    tables = r.cli("+table-list", [])["data"]["tables"]
    r.save("tables-before.json", tables)

    def backup(table):
        tid = table["id"]
        schema = r.schema(tid)
        r.save(f"{tid}-schema-before.json", schema)
        visible = {}
        for view in schema["views"]:
            coords = ["--table-id", tid, "--view-id", view["id"]]
            visible[view["id"]] = r.cli("+view-get-visible-fields", coords)["data"][
                "visible_fields"
            ]
        r.save(f"{tid}-visible-before.json", visible)
        return schema, visible

    with ThreadPoolExecutor(max_workers=4) as pool:
        all_before = list(pool.map(backup, tables))
    plans = {}
    for tid in (TABLE_SERIES, TABLE_TRIMS):
        schema = next(s for s, _ in all_before if s["table"]["id"] == tid)
        ids = [f["id"] for f in schema["fields"] if f["name"] in {NOTE, "核查状态", "问题说明"}]
        rows = read_rows(r, tid, ids, "before-notes")
        r.save(f"{tid}-notes-before.json", rows)
        plans[tid] = {
            row["record_id"]: {NOTE: clean_note(row[NOTE])}
            for row in rows
            if row.get(NOTE) and clean_note(row[NOTE]) != row[NOTE]
        }
    r.save("note-plan.json", plans)
    for tid, patches in plans.items():
        items = list(patches.items())
        for start in range(0, len(items), 100):
            r.cli(
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
    desired = {}
    for schema, visible in all_before:
        tid = schema["table"]["id"]
        hide = HIDDEN | {
            f["name"]
            for f in schema["fields"]
            if any(w in f["name"] for w in ("待核", "复核", "问题说明"))
        }
        for vid, fields in visible.items():
            desired[(tid, vid)] = [f for f in fields if f not in hide]
            r.visible(tid, vid, desired[(tid, vid)])
    time.sleep(3)

    def verify(item):
        original, visible = item
        tid = original["table"]["id"]
        actual = r.schema(tid)
        assert {f["id"]: f for f in actual["fields"]} == {f["id"]: f for f in original["fields"]}
        for view in original["views"]:
            view["_meta"]["visible_fields"] = f"{len(desired[(tid, view['id'])])} fields"
        assert actual["views"] == original["views"]
        for vid in visible:
            values = r.cli("+view-get-visible-fields", ["--table-id", tid, "--view-id", vid])[
                "data"
            ]["visible_fields"]
            assert values == desired[(tid, vid)]
        if tid in plans:
            ids = [f["id"] for f in actual["fields"] if f["name"] in {NOTE, "核查状态", "问题说明"}]
            rows = read_rows(r, tid, ids, "after-notes")
            old = json.loads((r.audit / f"{tid}-notes-before.json").read_text())
            for row in old:
                row.update(plans[tid].get(row["record_id"], {}))
            assert {x["record_id"]: x for x in rows} == {x["record_id"]: x for x in old}
            assert all(
                not any(s in str(x.get(NOTE, "")) for s in ("待核", "待确认", "请先核实"))
                for x in rows
            )
        r.save(f"{tid}-schema-after.json", actual)

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(verify, all_before))
    after = r.cli("+table-list", [])["data"]["tables"]
    assert {t["id"]: (t["name"], t["records_count"]) for t in tables} == {
        t["id"]: (t["name"], t["records_count"]) for t in after
    }
    r.save(
        "verification.json",
        {
            "verified": True,
            "views_checked": len(desired),
            "note_rows_changed": sum(len(p) for p in plans.values()),
            "fields_and_records_retained": True,
            "review_evidence_preserved": True,
        },
    )
    print("VERIFIED", len(desired), "views; notes", sum(len(p) for p in plans.values()), flush=True)


if __name__ == "__main__":
    main()
