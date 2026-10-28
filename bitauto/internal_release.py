"""Publish Chinese navigation and exact trim-to-parameter links; preserve source values."""

import argparse
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

from bitauto.build_category_sheets import CATEGORIES_ORDER
from bitauto.category_names import category_table_map
from bitauto.config import PROFILE, require_feishu
from bitauto.feishu_sync import BASE_TOKEN, TABLE_DIFFS, TABLE_SERIES, TABLE_STATIONS, TABLE_TRIMS


class Release:
    def __init__(self, audit):
        self.audit = Path(audit).resolve()
        self.audit.mkdir(parents=True, exist_ok=True)

    def save(self, name, value):
        (self.audit / name).write_text(json.dumps(value, ensure_ascii=False, indent=2))

    def cli(self, command, args):
        require_feishu()
        argv = [
            "lark-cli",
            "--profile",
            PROFILE,
            "base",
            command,
            "--base-token",
            BASE_TOKEN,
            "--as",
            "user",
            *args,
        ]
        for delay in (30, 60, 120, None):
            result = subprocess.run(
                argv, cwd=self.audit, capture_output=True, text=True, check=False
            )
            if result.returncode == 0:
                break
            try:
                error = json.loads(result.stderr or result.stdout)
            except json.JSONDecodeError:
                raise RuntimeError(result.stderr or result.stdout) from None
            limited = error.get("error", {}).get("code") == 800004135
            if command != "+view-set-visible-fields" or not limited or delay is None:
                raise RuntimeError(error)
            with (self.audit / "api-limits.jsonl").open("a") as stream:
                stream.write(json.dumps({"response": error, "wait_seconds": delay}) + "\n")
            print("View method quota; waiting", delay, "seconds", flush=True)
            time.sleep(delay)
        value = json.loads(result.stdout)
        if value.get("ok") is False:
            raise RuntimeError(value)
        if command not in {
            "+table-list",
            "+table-get",
            "+record-list",
            "+record-get",
            "+view-list",
            "+view-get",
            "+view-get-visible-fields",
            "+base-block-list",
        }:
            with (self.audit / "mutations.jsonl").open("a") as stream:
                stream.write(
                    json.dumps(
                        {"command": command, "args": args, "result": value}, ensure_ascii=False
                    )
                    + "\n"
                )
        return value

    def schema(self, tid):
        return self.cli("+table-get", ["--table-id", tid])["data"]

    def read(self, tid, fields, label):
        merged = {}
        for part in range(0, len(fields), 50):
            requested = fields[part : part + 50]
            offset = 0
            while True:
                filename = f"{tid}-{label}-fields{part}-{offset}.ndjson"
                path = self.audit / filename
                manifest = path.with_suffix(".manifest.json")
                if label == "parameters-before" and path.exists() and manifest.exists():
                    result = json.loads(manifest.read_text())
                else:
                    result = self.cli(
                        "+record-list",
                        [
                            "--table-id",
                            tid,
                            "--offset",
                            str(offset),
                            "--limit",
                            "150",
                            "--format",
                            "ndjson",
                            "--output",
                            filename,
                            *[x for name in requested for x in ["--field-id", name]],
                        ],
                    )
                for line in path.read_text().splitlines():
                    row = json.loads(line)
                    merged.setdefault(row["record_id"], {}).update(row)
                if not result["has_more"]:
                    break
                offset = result["next_offset"]
        return list(merged.values())

    def prepare(self):
        tables = self.cli("+table-list", [])["data"]["tables"]
        before = self.audit / "view-plan.json"
        if before.exists():
            return json.loads(before.read_text())
        plan = []
        for table in tables:
            tid = table["id"]
            if tid == TABLE_DIFFS:
                continue  # Independently backed up and maintained by the review migration.
            original_schema = self.audit / f"{tid}-views-schema-before.json"
            schema = (
                json.loads(original_schema.read_text())
                if original_schema.exists()
                else self.schema(tid)
            )
            if not original_schema.exists():
                self.save(original_schema.name, schema)
            definitions = {}
            for view in schema["views"]:
                definitions[view["id"]] = self.cli(
                    "+view-get", ["--table-id", tid, "--view-id", view["id"]]
                )
            if not (self.audit / f"{tid}-views-before.json").exists():
                self.save(f"{tid}-views-before.json", definitions)
            primary = next(
                f["name"] for f in schema["fields"] if f["id"] == schema["table"]["primary_field"]
            )
            grid = next(v for v in schema["views"] if v["type"] == "grid")
            original = self.cli(
                "+view-get-visible-fields", ["--table-id", tid, "--view-id", grid["id"]]
            )["data"]["visible_fields"]
            item = {
                "table": table,
                "primary": primary,
                "original_order": original,
                "fields": [f["name"] for f in schema["fields"]],
            }
            if tid not in {TABLE_SERIES, TABLE_TRIMS, TABLE_STATIONS}:
                rows = self.read(tid, item["fields"], "parameters-before")
                assert len(rows) == table["records_count"]
                assert len({r["款型唯一键"] for r in rows}) == len(rows)
                self.save(f"{tid}-parameters-before.json", rows)
                item["record_ids_sha256"] = hashlib.sha256(
                    json.dumps(sorted(r["record_id"] for r in rows)).encode()
                ).hexdigest()
            plan.append(item)
            print("Backed up", table["name"], flush=True)
        self.save("view-plan.json", plan)
        return plan

    def view(self, tid, name, fields, group=None):
        current = self.schema(tid)
        available = {f["name"] for f in current["fields"]}
        fields = [field for field in fields if field in available]
        matches = [v for v in current["views"] if v["name"] == name]
        assert len(matches) <= 1, (tid, name)
        if matches:
            vid = matches[0]["id"]
        else:
            self.cli(
                "+view-create",
                ["--table-id", tid, "--json", json.dumps({"name": name, "type": "grid"})],
            )
            for _ in range(8):
                matches = [v for v in self.schema(tid)["views"] if v["name"] == name]
                if matches:
                    break
                time.sleep(1)
            if not matches:
                raise RuntimeError(f"Created view not visible yet: {tid}/{name}")
            vid = matches[0]["id"]
        self.visible(tid, vid, fields)
        if group:
            self.cli(
                "+view-set-group",
                [
                    "--table-id",
                    tid,
                    "--view-id",
                    vid,
                    "--json",
                    json.dumps({"group_config": [{"field": group, "desc": False}]}),
                ],
            )
        return {
            "table_id": tid,
            "view_id": vid,
            "name": name,
            "fields": fields,
            "url": f"https://example.feishu.cn/base/{BASE_TOKEN}",
        }

    def visible(self, tid, vid, fields):
        coords = ["--table-id", tid, "--view-id", vid]
        existing = self.cli("+view-get-visible-fields", coords)["data"]["visible_fields"]
        if existing != fields:
            time.sleep(6)
            self.cli(
                "+view-set-visible-fields",
                coords + ["--json", json.dumps({"visible_fields": fields})],
            )

    def publish_views(self):
        # Business navigation now reuses existing view IDs only.
        from bitauto.business_views import configure_existing_views

        configure_existing_views(self)

    def link_plan(self):
        labels = [
            "基本信息",
            "车身参数",
            "电动机参数",
            "电池与充电",
            "发动机参数",
            "变速箱参数",
            "底盘转向",
            "车轮制动",
            "主动安全",
            "被动安全",
            "辅助与控制",
            "驾驶辅助功能",
            "驾驶辅助硬件",
            "外观配置",
            "内饰配置",
            "座椅配置",
            "互联与智能化",
            "多媒体配置",
            "灯光功能",
            "玻璃与后视镜",
            "空调与制冷",
        ]
        available = [
            p
            for p in self.prepare()
            if p["table"]["id"] not in {TABLE_SERIES, TABLE_TRIMS, TABLE_STATIONS}
        ]
        by_name = category_table_map([p["table"] for p in available])
        by_id = {p["table"]["id"]: p for p in available}
        items = [by_id[by_name[name]] for _, name in CATEGORIES_ORDER]
        assert len(items) == len(labels) == 21
        return [
            {"table_id": p["table"]["id"], "table_name": p["table"]["name"], "link_name": name}
            for p, name in zip(items, labels, strict=True)
        ]

    def link_parameters(self):
        plan = self.link_plan()
        self.save("parameter-links-plan.json", plan)
        schema = self.schema(TABLE_TRIMS)
        fields = {f["name"]: f for f in schema["fields"]}
        old_names = [name for name in fields if name != "图片URL JSON"]
        before = self.read(TABLE_TRIMS, old_names, f"links-before-{time.time_ns()}")
        index = {r["款型唯一键"]: r for r in before}
        assert len(index) == len(before) == 4152
        updates = {r["record_id"]: {} for r in before}
        for item in plan:
            rows = json.loads(
                (self.audit / f"{item['table_id']}-parameters-before.json").read_text()
            )
            by_key = {r["款型唯一键"]: r for r in rows}
            assert set(by_key) == set(index), item["table_name"]
            for key, trim in index.items():
                assert by_key[key]["分站代码"] == trim["分站代码"]
                updates[trim["record_id"]][item["link_name"]] = [{"id": by_key[key]["record_id"]}]
            if item["link_name"] in fields:
                assert fields[item["link_name"]]["type"] == "link"
                assert fields[item["link_name"]]["link_table"] == item["table_id"]
        missing = [
            {
                "name": item["link_name"],
                "type": "link",
                "link_table": item["table_id"],
                "bidirectional": False,
                "description": "同分站、同来源款型的官网分类参数；空白可能未披露或不适用。",
            }
            for item in plan
            if item["link_name"] not in fields
        ]
        if missing:
            self.cli("+field-create", ["--table-id", TABLE_TRIMS, "--json", json.dumps(missing)])
        time.sleep(3)
        by_id = {row["record_id"]: row for row in before}
        pairs = [
            (rid, value)
            for rid, value in updates.items()
            if any(by_id[rid].get(name) != ids for name, ids in value.items())
        ]
        for offset in range(0, len(pairs), 100):
            path = self.audit / f"links-update-{time.time_ns()}-{offset}.json"
            path.write_text(json.dumps({"update_records": dict(pairs[offset : offset + 100])}))
            self.cli("+record-batch-update", ["--table-id", TABLE_TRIMS, "--json", f"@{path.name}"])
            print("Parameter links", min(offset + 100, len(pairs)), "/", len(pairs), flush=True)
        time.sleep(3)
        names = list(dict.fromkeys(old_names + [item["link_name"] for item in plan]))
        after = self.read(TABLE_TRIMS, names, f"links-after-{time.time_ns()}")
        prior = {r["record_id"]: r for r in before}
        assert set(prior) == {r["record_id"] for r in after}
        added = {item["link_name"] for item in plan}
        for row in after:
            assert {k: v for k, v in row.items() if k not in added} == {
                k: v for k, v in prior[row["record_id"]].items() if k not in added
            }, row["record_id"]
            for name, ids in updates[row["record_id"]].items():
                assert row[name] == ids, (row["record_id"], name)
        self.save(
            "parameter-links-verification.json",
            {
                "verified": True,
                "trims": len(after),
                "parameter_categories": len(plan),
                "links_verified": len(after) * len(plan),
                "non_target_fields_unchanged": True,
                "image_json_untouched_by_this_stage": True,
            },
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-dir", type=Path, required=True)
    parser.add_argument("--views", action="store_true")
    parser.add_argument("--link-parameters", action="store_true")
    args = parser.parse_args()
    release = Release(args.audit_dir)
    os.chdir(release.audit)
    if args.link_parameters:
        release.link_parameters()
    elif args.views:
        release.publish_views()
    else:
        release.prepare()


if __name__ == "__main__":
    main()
