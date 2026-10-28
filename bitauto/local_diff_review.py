"""External-drive review ledger with preserved human decisions; no cloud API."""

import csv
import json
from pathlib import Path
from typing import Any

from bitauto.config import DATA_DIR
from bitauto.diff_review import diff_update_patch, scalar

DEFAULT_REVIEW_DIR = DATA_DIR / "reviews" / "parameter-review"
MANUAL_FIELDS = ("人工备注", "人工结论", "处理状态")


def write_csv(path: Path, rows: list[dict], columns: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    key: json.dumps(value, ensure_ascii=False)
                    if isinstance(value, (list, dict))
                    else value
                    for key, value in row.items()
                }
            )


class LocalReviewStore:
    def __init__(self, directory: Path = DEFAULT_REVIEW_DIR):
        self.directory = Path(directory)
        self.records_file = self.directory / "records.json"
        self.manual_file = self.directory / "人工处理.csv"

    def read(self) -> list[dict[str, Any]]:
        rows = json.loads(self.records_file.read_text())
        index = {row["差异唯一键"].upper(): row for row in rows}
        if len(index) != len(rows):
            raise ValueError("Duplicate stable keys in local review ledger")
        if self.manual_file.exists():
            with self.manual_file.open(encoding="utf-8-sig", newline="") as stream:
                seen = set()
                for edited in csv.DictReader(stream):
                    key = edited["差异唯一键"].upper()
                    if key in seen or key not in index:
                        raise ValueError(f"Unknown or duplicate manual review key: {key}")
                    seen.add(key)
                    for field in MANUAL_FIELDS:
                        value = edited.get(field, "")
                        if field == "处理状态":
                            if value not in {"待核查", "待复核", "已排除", "已人工处理", ""}:
                                raise ValueError(f"Invalid manual review state: {value}")
                            index[key][field] = [value] if value else []
                        elif value or index[key].get(field):
                            index[key][field] = value or None
        return rows

    def save(self, rows: list[dict[str, Any]]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = self.directory / "records.json.tmp"
        temporary.write_text(json.dumps(rows, ensure_ascii=False, indent=2))
        temporary.replace(self.records_file)
        columns = list(dict.fromkeys(key for row in rows for key in row))
        write_csv(self.directory / "参数核查清单.csv", rows, columns)
        manual = [
            {
                "差异唯一键": row["差异唯一键"],
                "核查事项": row.get("核查事项", ""),
                "判断结果": scalar(row.get("判断结果")),
                "处理状态": scalar(row.get("处理状态")),
                "人工备注": row.get("人工备注"),
                "人工结论": row.get("人工结论"),
            }
            for row in rows
        ]
        write_csv(
            self.manual_file,
            manual,
            ["差异唯一键", "核查事项", "判断结果", "处理状态", "人工备注", "人工结论"],
        )
        expected = {row["差异唯一键"]: row for row in rows}
        if {row["差异唯一键"]: row for row in self.read()} != expected:
            raise RuntimeError("Local ledger write/read verification failed")

    def upsert(self, incoming: list[dict[str, Any]]) -> dict[str, int]:
        rows = self.read()
        index = {row["差异唯一键"].upper(): row for row in rows}
        created = updated = 0
        seen = set()
        for record in incoming:
            key = record["差异唯一键"].upper()
            if key in seen:
                raise ValueError(f"Duplicate incoming difference key: {key}")
            seen.add(key)
            if key in index:
                patch = diff_update_patch(index[key], record)
                if patch:
                    index[key].update(patch)
                    updated += 1
            else:
                row = {"record_id": f"local:{key}", **record, "差异唯一键": key}
                rows.append(row)
                index[key] = row
                created += 1
        self.save(rows)
        return {"created": created, "updated": updated}
