"""Simplify existing business views without recreating retired navigation."""

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from bitauto.bilingual_navigation import record_hash
from bitauto.complete_snapshot_data import read_rows
from bitauto.diff_review import scalar
from bitauto.feishu_sync import TABLE_SERIES, TABLE_TRIMS
from bitauto.internal_release import Release

NOTE = "资料说明"
NOTE_DESCRIPTION = "阅读提示：根据现有记录生成，说明待公布配置/报价和车系级图集状态；非实时官网核验，不替代原值或人工结论。"
RETIRED = {
    "车系总览",
    "配置查询",
    "按国家查配置",
    "参数分类入口",
    "国家分站说明",
    "官网参数核对",
    "后台｜完整字段",
}
SERIES_FRONT = [
    "车系全称",
    "封面图片",
    "分站名称",
    "品牌中文名",
    "车系中文名",
    "动力类型",
    "销售状态",
    "指导价区间",
    "旗下款型",
    NOTE,
    "图库入口",
    "来源车系页",
    "更新时间",
]
TRIM_FRONT = [
    "车型全称",
    "封面图片",
    "分站名称",
    "品牌中文名",
    "车系中文名",
    "年款",
    "动力类型",
    "指导价(EN)",
    "货币代码",
    "纯电续航(km)",
    "电池容量(kWh)",
    "驱动形式",
    "所属车系",
    NOTE,
    "图库入口",
    "基本信息",
]


def note(row, *, trim=False):
    parts = []
    if not trim and not row.get("旗下款型"):
        stage = scalar(row.get("销售状态")) or ""
        parts.append(f"{stage}，当前未取得配置资料".strip("，"))
    price = row.get("指导价(EN)" if trim else "指导价区间")
    if str(price or "").strip().upper() == "TBC":
        parts.append("报价待公布（TBC）")
    elif price in (None, "", "-"):
        parts.append("报价未披露")
    if trim and not row.get("货币代码"):
        parts.append("币种未披露")
    state = scalar(row.get("图库状态"))
    labels = {
        "来源暂停": "图集来源暂停",
        "采集失败": "图集采集失败",
        "未披露": "来源未披露图集",
        "身份待核": "图集暂不可用",
    }
    if state in labels:
        parts.append(labels[state] + ("，封面可查看" if row.get("封面图片") else ""))
    elif state != "已采集":
        parts.append("图集暂不可用")
    parts.append("图片为车系级，非配置专属" if trim else "图片为车系级资料")
    return "；".join(parts)


def order(tid, old, available):
    front = SERIES_FRONT if tid == TABLE_SERIES else TRIM_FRONT
    quality = {"核查状态", "问题说明", "图库状态"}
    main = [f for f in front if f in available]
    main += [f for f in old if f in available and f not in main and f not in quality]
    return main


def configure_existing_views(release):
    """Reorder existing entrances only. Never create views, including old defaults."""
    entries = []
    for tid in (TABLE_SERIES, TABLE_TRIMS):
        schema = release.schema(tid)
        available = {f["name"] for f in schema["fields"]}
        for view in schema["views"]:
            names = (
                {"表格"}
                if tid == TABLE_SERIES
                else {"表格", "按车系跨国对比视图", "按国家分站视图"}
            )
            if view["name"] not in names:
                continue
            coords = ["--table-id", tid, "--view-id", view["id"]]
            old = release.cli("+view-get-visible-fields", coords)["data"]["visible_fields"]
            desired = order(tid, old, available)
            release.visible(tid, view["id"], desired)
            entries.append({"table_id": tid, "view_id": view["id"], "fields": desired})
    release.save("business-views.json", entries)
    return entries


def prepare(release):
    if (release.audit / "plan.json").exists():
        return json.loads((release.audit / "plan.json").read_text())
    tables = release.cli("+table-list", [])["data"]["tables"]
    release.save("tables-before.json", tables)

    def backup(table):
        tid = table["id"]
        schema = release.schema(tid)
        release.save(f"{tid}-schema-before.json", schema)
        views = {}
        for v in schema["views"]:
            coords = ["--table-id", tid, "--view-id", v["id"]]
            details = release.cli("+view-get", coords)
            fields = release.cli("+view-get-visible-fields", coords)["data"]["visible_fields"]
            views[v["id"]] = {"details": details, "fields": fields}
        release.save(f"{tid}-views-before.json", views)
        rows = read_rows(
            release,
            tid,
            [f["id"] for f in schema["fields"]],
            "before",
            100 if tid in {TABLE_SERIES, TABLE_TRIMS} else 1000,
        )
        assert len(rows) == table["records_count"]
        release.save(f"{tid}-rows-before.json", rows)
        if tid in {TABLE_SERIES, TABLE_TRIMS}:
            for f in schema["fields"]:
                if f["name"] == NOTE:
                    assert f["type"] == "text" and f.get("description") == NOTE_DESCRIPTION, (
                        "Unrelated note field"
                    )
        print("Backed up", table["name"], len(rows), flush=True)
        return {
            "table_id": tid,
            "delete": [v for v in schema["views"] if v["name"] in RETIRED],
            "patches": {r["record_id"]: {NOTE: note(r, trim=tid == TABLE_TRIMS)} for r in rows}
            if tid in {TABLE_SERIES, TABLE_TRIMS}
            else {},
        }

    with ThreadPoolExecutor(max_workers=4) as pool:
        plan = list(pool.map(backup, tables))
    release.save("plan.json", plan)
    return plan


def apply(release, plan):
    desired_views = {}
    for item in plan:
        tid = item["table_id"]
        schema = release.schema(tid)
        patches = item["patches"]
        if patches:
            if NOTE not in {f["name"] for f in schema["fields"]}:
                release.cli(
                    "+field-create",
                    [
                        "--table-id",
                        tid,
                        "--json",
                        json.dumps(
                            {"name": NOTE, "type": "text", "description": NOTE_DESCRIPTION},
                            ensure_ascii=False,
                        ),
                    ],
                )
            ids = [f["id"] for f in release.schema(tid)["fields"] if f["name"] == NOTE]
            current = {r["record_id"]: r for r in read_rows(release, tid, ids, "preflight")}
            old = {
                r["record_id"]: r
                for r in json.loads((release.audit / f"{tid}-rows-before.json").read_text())
            }
            updates = {}
            for rid, patch in patches.items():
                if current[rid].get(NOTE) == patch[NOTE]:
                    continue
                assert current[rid].get(NOTE) == old[rid].get(NOTE), "Note changed after preview"
                updates[rid] = patch
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
        # Delete only the specifically reviewed names at their backed-up IDs.
        current = {v["id"]: v for v in release.schema(tid)["views"]}
        for view in item["delete"]:
            if view["id"] in current:
                actual_view = json.loads(json.dumps(current[view["id"]]))
                old_view = json.loads(json.dumps(view))
                # Creating the one authorized note column may change counts.
                actual_view.get("_meta", {}).pop("visible_fields", None)
                old_view.get("_meta", {}).pop("visible_fields", None)
                assert actual_view == old_view, "View changed after preview"
                release.cli("+view-delete", ["--table-id", tid, "--view-id", view["id"], "--yes"])
        original = json.loads((release.audit / f"{tid}-schema-before.json").read_text())
        visibility = json.loads((release.audit / f"{tid}-views-before.json").read_text())
        available = {f["name"] for f in release.schema(tid)["fields"]}
        deleted = {v["id"] for v in item["delete"]}
        for view in original["views"]:
            if view["id"] in deleted:
                continue
            fields = visibility[view["id"]]["fields"]
            if tid in {TABLE_SERIES, TABLE_TRIMS} and view["name"] in {
                "表格",
                "按车系跨国对比视图",
                "按国家分站视图",
            }:
                fields = order(tid, fields, available)
            release.visible(tid, view["id"], fields)
            desired_views[(tid, view["id"])] = fields
        print("Applied", tid, "deleted", len(item["delete"]), flush=True)
    time.sleep(3)
    release.save(
        "desired-views.json",
        [{"table_id": t, "view_id": v, "fields": f} for (t, v), f in desired_views.items()],
    )
    verify(release, plan, desired_views)


def verify(release, plan, desired_views):
    before_tables = json.loads((release.audit / "tables-before.json").read_text())
    after_tables = release.cli("+table-list", [])["data"]["tables"]
    assert {t["id"]: (t["name"], t["records_count"]) for t in before_tables} == {
        t["id"]: (t["name"], t["records_count"]) for t in after_tables
    }

    def check(item):
        tid = item["table_id"]
        original = json.loads((release.audit / f"{tid}-schema-before.json").read_text())
        actual = release.schema(tid)
        release.save(f"{tid}-schema-after.json", actual)
        old_fields = {f["id"]: f for f in original["fields"]}
        fields = {f["id"]: f for f in actual["fields"]}
        assert all(fields.get(k) == v for k, v in old_fields.items()), (
            tid,
            "field definition changed",
        )
        new = {v["name"] for k, v in fields.items() if k not in old_fields}
        assert new == (
            {NOTE}
            if item["patches"] and NOTE not in {f["name"] for f in old_fields.values()}
            else set()
        )
        deleted = {v["id"] for v in item["delete"]}
        expected = [v for v in original["views"] if v["id"] not in deleted]
        for v in expected:
            v["_meta"]["visible_fields"] = f"{len(desired_views[(tid, v['id'])])} fields"
        assert actual["views"] == expected, (tid, "views changed")
        for v in expected:
            coords = ["--table-id", tid, "--view-id", v["id"]]
            visible = release.cli("+view-get-visible-fields", coords)["data"]["visible_fields"]
            assert visible == desired_views[(tid, v["id"])], (tid, v["id"])
        rows = read_rows(
            release, tid, list(fields), "after", 100 if tid in {TABLE_SERIES, TABLE_TRIMS} else 1000
        )
        expected_rows = json.loads((release.audit / f"{tid}-rows-before.json").read_text())
        for row in expected_rows:
            row.update(item["patches"].get(row["record_id"], {}))
        assert record_hash(rows) == record_hash(expected_rows), (tid, "data changed")
        print("Verified", tid, len(rows), flush=True)
        return len(rows), len(expected)

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(check, plan))
    release.save(
        "verification.json",
        {
            "verified": True,
            "rows_compared": sum(n for n, _ in results),
            "views_remaining": sum(n for _, n in results),
            "views_deleted": sum(len(p["delete"]) for p in plan),
            "note_rows": sum(len(p["patches"]) for p in plan),
            "original_values_fields_relations_preserved": True,
        },
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-dir", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    release = Release(args.audit_dir)
    plan = prepare(release)
    print("PREVIEW", sum(len(p["delete"]) for p in plan), "views to remove", flush=True)
    if args.apply:
        apply(release, plan)


if __name__ == "__main__":
    main()
