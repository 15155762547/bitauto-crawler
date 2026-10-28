import json
import logging
import subprocess
import time
from pathlib import Path
from typing import Any

from bitauto.config import BASE_TOKEN as CONFIG_BASE_TOKEN
from bitauto.config import PROFILE as CONFIG_PROFILE
from bitauto.config import TABLE_SERIES as CONFIG_TABLE_SERIES
from bitauto.config import TABLE_TRIMS, require_feishu

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

BASE_TOKEN = CONFIG_BASE_TOKEN
PROFILE = CONFIG_PROFILE

TABLE_MODELS = TABLE_TRIMS
TABLE_SERIES = CONFIG_TABLE_SERIES


def run_cli(subcmd: str, args: list[str]) -> dict[str, Any]:
    require_feishu()
    cmd = ["lark-cli", "--profile", PROFILE, "base", subcmd, "--base-token", BASE_TOKEN] + args
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if res.returncode != 0:
        raise RuntimeError(f"lark-cli error ({res.returncode}): {res.stderr or res.stdout}")
    try:
        return json.loads(res.stdout)
    except Exception as e:
        raise RuntimeError(f"Failed to parse lark-cli JSON: {res.stdout} / {res.stderr}") from e


def load_all_records_paginated(table_id: str) -> list[dict[str, Any]]:
    logger.info("Fetching all records (paginated) from table %s...", table_id)
    all_records = []
    offset = 0
    while True:
        args = ["--table-id", table_id, "--offset", str(offset), "--limit", "2000"]
        res = run_cli("+record-list", args)
        rec_file = Path(res["record_file"])
        lines = [
            json.loads(line) for line in rec_file.read_text("utf-8").splitlines() if line.strip()
        ]
        all_records.extend(lines)
        rec_file.unlink(missing_ok=True)
        Path(res["manifest_file"]).unlink(missing_ok=True)
        if not res.get("has_more"):
            break
        offset = res.get("next_offset", offset + len(lines))
    logger.info("Loaded %d total records from table %s.", len(all_records), table_id)
    return all_records


def main():
    logger.info("Linking 配置款型 to 新能源车系 via bidirectional link field...")

    # 1. Load Series records and index by 车系唯一键
    series_recs = load_all_records_paginated(TABLE_SERIES)
    series_by_key = {}
    for r in series_recs:
        rid = r.get("record_id")
        key = r.get("车系唯一键")
        if key and rid:
            series_by_key[key] = rid
            # Also index by (site, series_id)
            site = r.get("分站代码")
            sid = r.get("来源车系ID")
            if site and sid is not None:
                series_by_key[f"{site}_{sid}"] = rid

    logger.info("Indexed %d series keys.", len(series_by_key))

    # 2. Load Model records
    model_recs = load_all_records_paginated(TABLE_MODELS)

    # 3. Compute link updates
    updates = {}
    matched_count = 0
    for r in model_recs:
        rid = r.get("record_id")
        site = r.get("分站代码")
        sid = r.get("来源车系ID")
        if not rid or not site or sid is None:
            continue

        skey = f"{site}_{sid}"
        target_series_rid = series_by_key.get(skey)
        if target_series_rid:
            existing_links = r.get("所属车系") or []
            # existing_links could be [{'id': ...}] or ['rec...']
            has_link = any(
                (item.get("id") if isinstance(item, dict) else item) == target_series_rid
                for item in existing_links
            )
            if not has_link:
                updates[rid] = {"所属车系": [target_series_rid]}
                matched_count += 1

    logger.info("Found %d models needing link updates to their series.", len(updates))
    if not updates:
        logger.info("All models are already linked!")
        return

    # 4. Batch update in chunks of 100
    items = list(updates.items())
    chunk_size = 100
    total_chunks = (len(items) + chunk_size - 1) // chunk_size
    for i in range(0, len(items), chunk_size):
        chunk = dict(items[i : i + chunk_size])
        payload = {"update_records": chunk}
        try:
            res_up = run_cli(
                "+record-batch-update", ["--table-id", TABLE_MODELS, "--json", json.dumps(payload)]
            )
            if not res_up.get("ok"):
                logger.error(
                    "Failed batch update chunk %d/%d: %s", i // chunk_size + 1, total_chunks, res_up
                )
            else:
                logger.info(
                    "Batch %d/%d linked successfully in [配置款型].",
                    i // chunk_size + 1,
                    total_chunks,
                )
        except (RuntimeError, ValueError) as e:
            logger.error("Exception on link batch %d/%d: %s", i // chunk_size + 1, total_chunks, e)
        time.sleep(0.3)

    logger.info("Bidirectional linking completed successfully!")


if __name__ == "__main__":
    main()
