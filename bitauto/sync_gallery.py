"""Preview, synchronize and fully verify gallery JSON in the independent Base."""

import argparse
import hashlib
import json
import os
import re
import subprocess
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from bitauto.build_category_sheets import run_cli
from bitauto.config import PROFILE
from bitauto.feishu_sync import BASE_TOKEN, TABLE_SERIES, TABLE_TRIMS

FIELD = "图片URL JSON"
STATUS_FIELD = "图库状态"
ENTRY_FIELD = "图库入口"
TARGET_FIELDS = {FIELD, STATUS_FIELD, ENTRY_FIELD}
STATUSES = {
    "COMPLETE": "已采集",
    "PARTIAL": "部分采集",
    "PENDING": "待采集",
    "FAILED": "采集失败",
    "ACCESS_PAUSED": "来源暂停",
    "NOT_DISCLOSED": "未披露",
    "IDENTITY_REVIEW": "身份待核",
}
EXPRESSION = (
    "[vehicle_models].FILTER(AND(CurrentValue.[分站代码]=[分站代码],"
    "CurrentValue.[来源车系ID]=[来源车系ID])).[图片URL JSON].FIRST()"
)


def cell_json(result):
    """Compact JSON retains source categories, counts and exact published URLs."""
    value = {k: result[k] for k in ("station", "series_id", "source_url", "collected_at", "status")}
    if "error" in result:
        value["error"] = result["error"]
    value["categories"] = [
        {
            "name": c["name"],
            "group_id": c["group_id"],
            "album_type": c["album_type"],
            "expected_count": c["expected_count"],
            "urls": [image.get("original_url") or image["url"] for image in c["images"]],
        }
        for c in result["categories"]
    ]
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def read_table(audit, tid, label, field_ids=None, page_size=200):
    """Read complete projected cells, shrinking only on the API response-size limit."""
    if field_ids is None:
        schema = run_cli("+table-get", ["--table-id", tid])["data"]
        field_ids = [field["id"] for field in schema["fields"]]
    rows, offset = [], 0
    while True:
        path = audit / f"{tid}-{label}-{offset}-{page_size}.ndjson"
        flags = [
            "--table-id",
            tid,
            "--offset",
            str(offset),
            "--limit",
            str(page_size),
            "--format",
            "ndjson",
            "--output",
            path.name,
            "--as",
            "user",
        ]
        for field_id in field_ids:
            flags.extend(["--field-id", field_id])
        try:
            result = run_cli("+record-list", flags)
            assert result.get("ok") is not False, result
        except RuntimeError as error:
            if "response body too large" not in str(error) or page_size == 1:
                raise
            with (audit / "read-size-retries.jsonl").open("a") as stream:
                stream.write(
                    json.dumps(
                        {"table": tid, "offset": offset, "limit": page_size, "error": str(error)}
                    )
                    + "\n"
                )
            page_size = max(1, page_size // 2)
            continue
        chunk = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        rows.extend(chunk)
        if not result["has_more"]:
            assert len({r["record_id"] for r in rows}) == len(rows), "Duplicate records"
            return rows
        assert chunk, "Empty page with has_more=true"
        next_offset = result.get("next_offset", offset + len(chunk))
        assert next_offset > offset, "Pagination did not advance"
        offset = next_offset


def reference_expression(field):
    return EXPRESSION.replace(f"[{FIELD}]", f"[{field}]")


def readable_status(result):
    if result["status"] == "FAILED" and "identity" in result.get("error", "").lower():
        return STATUSES["IDENTITY_REVIEW"]
    return STATUSES[result["status"]]


def entry_url(value):
    """URL text cells are exported as Markdown links by the CLI."""
    match = re.search(r"\]\((https?://[^)]+)\)$", value or "")
    return match.group(1) if match else value


def same_cell(name, actual, expected):
    if actual in (None, "", []) and expected in (None, "", []):
        return True
    if name == ENTRY_FIELD:
        return entry_url(actual) == entry_url(expected)
    return actual == expected


def gallery_entry(result, row):
    # Only a published same-series gallery URL is eligible; a failed identity uses the existing page.
    if readable_status(result) != STATUSES["IDENTITY_REVIEW"]:
        for category in result["categories"]:
            if category.get("page_url"):
                return category["page_url"]
    return row["来源车系页"]


def ensure_fields(audit):
    fields = {}
    for tid in (TABLE_SERIES, TABLE_TRIMS):
        schema = run_cli("+table-get", ["--table-id", tid])["data"]
        schema_before = audit / f"{tid}-schema-before.json"
        if not schema_before.exists():
            schema_before.write_text(json.dumps(schema, ensure_ascii=False, indent=2))
        if tid == TABLE_SERIES:
            definitions = [
                {
                    "name": FIELD,
                    "type": "text",
                    "description": "官方图集分类URL及采集状态；不代表URL逐条HTTP有效。",
                },
                {
                    "name": STATUS_FIELD,
                    "type": "select",
                    "multiple": False,
                    "options": [{"name": label} for label in STATUSES.values()],
                    "description": "已采集仅指已披露图集数量对齐；来源暂停/身份待核不表示没有图片。",
                },
                {
                    "name": ENTRY_FIELD,
                    "type": "text",
                    "style": {"type": "url"},
                    "description": "官方已披露图库入口；未取得可靠图库入口时保留既有来源车系页。",
                },
            ]
        else:
            definitions = [
                {
                    "name": name,
                    "type": "formula",
                    "expression": reference_expression(name),
                    "description": "按分站代码及来源车系ID引用同站车系；图集不表示此款型专属实拍。",
                }
                for name in (FIELD, STATUS_FIELD, ENTRY_FIELD)
            ]
        missing = []
        for definition in definitions:
            existing = next((f for f in schema["fields"] if f["name"] == definition["name"]), None)
            if existing:
                assert existing["type"] == definition["type"], existing
                if definition["type"] == "formula":
                    assert existing["expression"] == definition["expression"], existing
            else:
                missing.append(definition)
        if missing:
            flags = ["--i-have-read-guide"] if tid == TABLE_TRIMS else []
            result = run_cli(
                "+field-create", ["--table-id", tid, "--json", json.dumps(missing), *flags]
            )
            assert result.get("ok"), result
            (audit / f"{tid}-field-create.receipt.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2)
            )
        for attempt in range(4):
            after = run_cli("+table-get", ["--table-id", tid])["data"]
            if TARGET_FIELDS <= {f["name"] for f in after["fields"]}:
                break
            if attempt == 3:
                raise RuntimeError(f"New gallery fields not visible: {tid}")
            time.sleep(2)
        (audit / f"{tid}-schema-after.json").write_text(
            json.dumps(after, ensure_ascii=False, indent=2)
        )
        fields[tid] = [
            {"id": f["id"], "name": f["name"], "type": f["type"]}
            for f in after["fields"]
            if f["name"] in TARGET_FIELDS
        ]
    (audit / "gallery-fields.json").write_text(json.dumps(fields, ensure_ascii=False, indent=2))
    return fields


def update_rows(audit, rows, values):
    for i in range(0, len(rows), 30):
        batch = rows[i : i + 30]
        path = audit / f"update-{time.time_ns()}.json"
        path.write_text(
            json.dumps(
                {"update_records": {r["record_id"]: values[r["车系唯一键"]] for r in batch}},
                ensure_ascii=False,
            )
        )
        command = [
            "lark-cli",
            "--profile",
            PROFILE,
            "base",
            "+record-batch-update",
            "--base-token",
            BASE_TOKEN,
            "--table-id",
            TABLE_SERIES,
            "--as",
            "user",
            "--json",
            f"@{path.name}",
        ]
        response = subprocess.run(command, capture_output=True, text=True, check=True)
        result = json.loads(response.stdout)
        assert result.get("ok"), result
        path.with_suffix(".receipt.json").write_text(response.stdout)
        print("Updated gallery", min(i + 30, len(rows)), "/", len(rows), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-dir", type=Path, required=True)
    parser.add_argument(
        "--results-dir", type=Path, help="Existing collection audit; never recrawls"
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument(
        "--allow-pending", action="store_true", help="Publish honest collection progress"
    )
    args = parser.parse_args()
    audit = args.audit_dir.resolve()
    source = (args.results_dir or audit).resolve()
    audit.mkdir(parents=True, exist_ok=True)
    os.chdir(audit)
    before = {}
    for tid in (TABLE_SERIES, TABLE_TRIMS):
        baseline = audit / f"{tid}-before.json"
        if baseline.exists():
            before[tid] = json.loads(baseline.read_text())
        else:
            before[tid] = read_table(audit, tid, f"before-{time.time_ns()}")
            baseline.write_text(json.dumps(before[tid], ensure_ascii=False))
    series, trims = before[TABLE_SERIES], before[TABLE_TRIMS]
    results, values = {}, {}
    manifest = []
    for row in series:
        key = row["车系唯一键"]
        path = source / "results" / f"{key}.json"
        if not path.exists():
            if not args.allow_pending:
                raise ValueError(f"Collection not finished: {key}")
            result = {
                "station": row["分站代码"],
                "series_id": row["来源车系ID"],
                "source_url": row["来源车系页"],
                "collected_at": datetime.now(UTC).isoformat(),
                "status": "PENDING",
                "categories": [],
            }
        else:
            data = path.read_bytes()
            result = json.loads(data)
            manifest.append(
                {"key": key, "path": str(path), "sha256": hashlib.sha256(data).hexdigest()}
            )
        assert result["series_id"] == row["来源车系ID"]
        assert result["station"] == row["分站代码"]
        results[key] = result
        values[key] = {
            FIELD: cell_json(result),
            STATUS_FIELD: [readable_status(result)],
            ENTRY_FIELD: gallery_entry(result, row),
        }
    assert len(values) == len(series), "Duplicate source series keys"
    assert len({(r["分站代码"], r["来源车系ID"]) for r in series}) == len(series)
    summary = {
        "series": len(series),
        "trims": len(trims),
        "statuses": dict(Counter(r["status"] for r in results.values())),
        "readable_statuses": dict(Counter(readable_status(r) for r in results.values())),
        "images": sum(len(c["images"]) for r in results.values() for c in r["categories"]),
        "max_json_characters": max(len(v[FIELD]) for v in values.values()),
        "collection_complete": all(
            r["status"] in {"COMPLETE", "NOT_DISCLOSED"} for r in results.values()
        ),
        "all_image_urls_http_validated": False,
    }
    (audit / "source-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    (audit / "sync-plan.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    (audit / "planned-values.json").write_text(json.dumps(values, ensure_ascii=False))
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    if not args.apply and not args.verify_only:
        return
    if args.apply:
        ensure_fields(audit)
        current = read_table(audit, TABLE_SERIES, f"current-{time.time_ns()}")
        original = {r["record_id"]: r for r in series}
        assert set(original) == {r["record_id"] for r in current}
        changed = []
        for row in current:
            key = row["车系唯一键"]
            # Refuse a concurrent user change; resuming our own planned value is safe.
            for name in TARGET_FIELDS:
                assert any(
                    same_cell(name, row.get(name), candidate)
                    for candidate in (original[row["record_id"]].get(name), values[key][name])
                ), (key, name, "Concurrent change")
            if any(
                not same_cell(name, row.get(name), value) for name, value in values[key].items()
            ):
                changed.append(row)
        update_rows(audit, changed, values)
        time.sleep(3)
    verified = {}
    for tid, original_rows in before.items():
        after = read_table(audit, tid, f"verified-{time.time_ns()}")
        original = {row["record_id"]: row for row in original_rows}
        assert set(original) == {r["record_id"] for r in after}
        for row in after:
            assert {k: v for k, v in row.items() if k not in TARGET_FIELDS} == {
                k: v for k, v in original[row["record_id"]].items() if k not in TARGET_FIELDS
            }, (row["record_id"], "Non-target field changed")
            key = row.get("车系唯一键") or f"{row['分站代码']}_{row['来源车系ID']}"
            expected = values[key]
            assert row.get(FIELD) == expected[FIELD], (row["record_id"], "JSON mismatch")
            payload = json.loads(row[FIELD])
            assert (payload["station"], payload["series_id"]) == (
                row["分站代码"],
                row["来源车系ID"],
            )
            status = row.get(STATUS_FIELD)
            assert status in (expected[STATUS_FIELD], expected[STATUS_FIELD][0]), (key, status)
            assert same_cell(ENTRY_FIELD, row.get(ENTRY_FIELD), expected[ENTRY_FIELD]), (
                key,
                "Entry mismatch",
            )
        verified[tid] = {
            "records": len(after),
            "non_target_fields_unchanged": True,
            "record_ids_unchanged": True,
            "json_matches_source_result": True,
            "source_identity_matches": True,
            "readable_fields_match": True,
        }
    summary.update({"verified": True, "tables": verified})
    (audit / "verification.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print("VERIFIED", json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
