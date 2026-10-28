"""Recompute the external-drive review ledger from saved bilingual snapshots only."""

import argparse
import json
from collections import Counter
from pathlib import Path

from bitauto.diff_review import build_review_plan
from bitauto.local_diff_review import DEFAULT_REVIEW_DIR, LocalReviewStore


def read_ndjson(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2))


def refresh_local(store: LocalReviewStore, identity_dir: Path, raw_dir: Path, *, refresh=False):
    rows = store.read()
    plan = build_review_plan(
        rows,
        read_ndjson(identity_dir / "trims.ndjson"),
        read_ndjson(identity_dir / "series.ndjson"),
        raw_dir,
    )
    store.directory.mkdir(parents=True, exist_ok=True)
    save(store.directory / "preview.json", plan)
    summary = {
        "records": len(plan),
        "classification_counts": dict(Counter(item["fields"]["判断结果"][0] for item in plan)),
        "identity_verified": sum(item["identity_verified"] for item in plan),
        "source_mode": "stored bilingual snapshots; no network requests",
        "destination": str(store.records_file),
        "applied": refresh,
    }
    if refresh:
        summary["changes"] = store.upsert([item["fields"] for item in plan])
    save(store.directory / "refresh-summary.json", summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local-dir", type=Path, default=DEFAULT_REVIEW_DIR)
    parser.add_argument("--identity-dir", type=Path)
    parser.add_argument("--raw-dir", type=Path)
    parser.add_argument(
        "--refresh", action="store_true", help="Update the local ledger and CSV files"
    )
    parser.add_argument("--apply", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--audit-dir", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.apply or args.audit_dir:
        parser.error(
            "云端参数核查表已迁出并禁用；旧--apply/--audit-dir不能访问或重建飞书表。请使用--refresh更新外盘本地清单。"
        )
    local_dir = args.local_dir.resolve()
    identity_dir = args.identity_dir or local_dir / "identities"
    raw_dir = args.raw_dir or DEFAULT_REVIEW_DIR.parents[1] / "raw"
    print(
        json.dumps(
            refresh_local(
                LocalReviewStore(local_dir),
                identity_dir,
                raw_dir,
                refresh=args.refresh,
            ),
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
