"""Preview/apply grid column ordering from cached source; never edit records."""

import argparse
import graphlib
import itertools
import json

from bitauto.build_category_sheets import CATEGORIES_ORDER, clean_field_name, run_cli
from bitauto.category_names import category_table_map
from bitauto.config import DATA_DIR


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    root = DATA_DIR
    audit = root / "audit/field-order-20261023"
    audit.mkdir(parents=True, exist_ok=True)
    plan_path = audit / "plan.json"
    if not args.apply:
        graphs = {name: {} for name, _ in CATEGORIES_ORDER}
        paths = sorted((root / "raw").glob("params_*_en.json"))
        for path in paths:
            for group in (json.loads(path.read_text()).get("data") or {}).get("paramList", []):
                if group["name"] not in graphs:
                    continue
                graph = graphs[group["name"]]
                names = [
                    clean_field_name(i["name"], i.get("unit"))
                    for i in group["items"]
                    if i.get("name")
                ]
                for name in names:
                    graph.setdefault(name, set())
                for before, after in itertools.pairwise(names):
                    if before != after:
                        graph[after].add(before)
        orders = {
            name: list(graphlib.TopologicalSorter(g).static_order()) for name, g in graphs.items()
        }
        tables = run_cli("+table-list", [])["data"]["tables"]
        (audit / "tables-before.json").write_text(json.dumps(tables, ensure_ascii=False, indent=2))
        by_name = category_table_map(tables)
        plan = []
        for source, name in CATEGORIES_ORDER:
            tid = by_name[name]
            info = run_cli("+table-get", ["--table-id", tid])["data"]
            (audit / f"{tid}-schema-before.json").write_text(
                json.dumps(info, ensure_ascii=False, indent=2)
            )
            fields = {f["id"]: f["name"] for f in info["fields"]}
            expected = (
                ["车型全称", "分站名称", "动力类型"]
                + orders[source]
                + ["品牌英文名", "车系英文名", "款型英文名", "分站代码", "款型唯一键"]
            )
            if set(fields.values()) - set(expected):
                raise ValueError(f"Unmapped fields: {name}: {set(fields.values()) - set(expected)}")
            for view in info["views"]:
                if view["type"] != "grid":
                    continue
                coords = ["--table-id", tid, "--view-id", view["id"]]
                before = run_cli("+view-get-visible-fields", coords)
                plan.append(
                    {
                        "table": name,
                        "coords": coords,
                        "before": before,
                        "fields": fields,
                        "desired_names": [n for n in expected if n in fields.values()],
                    }
                )
            print(f"Prepared {name}", flush=True)
        plan_path.write_text(
            json.dumps({"source_files": len(paths), "views": plan}, ensure_ascii=False, indent=2)
        )
        return
    plan = json.loads(plan_path.read_text())["views"]
    for item in plan:
        current = run_cli("+view-get-visible-fields", item["coords"])
        if current != item["before"]:
            raise ValueError(f"View changed since preview: {item['table']}")
        result = run_cli(
            "+view-set-visible-fields",
            item["coords"] + ["--json", json.dumps({"visible_fields": item["desired_names"]})],
        )
        if not result.get("ok"):
            raise RuntimeError(result)
        print(f"Updated {item['table']}", flush=True)
    for item in plan:
        after = run_cli("+view-get-visible-fields", item["coords"])
        if after["data"]["visible_fields"] != item["desired_names"]:
            raise ValueError(f"Readback mismatch: {item['table']}")
        (audit / f"{item['coords'][-1]}-after.json").write_text(
            json.dumps(after, ensure_ascii=False, indent=2)
        )
    (audit / "tables-after.json").write_text(
        json.dumps(run_cli("+table-list", []), ensure_ascii=False, indent=2)
    )


if __name__ == "__main__":
    main()
