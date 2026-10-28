import json
import logging
import os
import subprocess
import time
from typing import Any

from bitauto.category_names import category_table_map
from bitauto.config import BASE_TOKEN as CONFIG_BASE_TOKEN
from bitauto.config import DATA_DIR, require_feishu
from bitauto.config import PROFILE as CONFIG_PROFILE

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

BASE_TOKEN = CONFIG_BASE_TOKEN
PROFILE = CONFIG_PROFILE
RAW_DIR = str(DATA_DIR / "raw")

SELECT_OPTIONS = [
    {"name": "Standard", "hue": "Green"},
    {"name": "Optional", "hue": "Orange"},
    {"name": "None", "hue": "Gray"},
]

VALUE_MAP = {
    "●": "Standard",
    "•": "Standard",
    "○": "Optional",
    "-": "None",
    "标配": "Standard",
    "选配": "Optional",
    "无": "None",
    "Standard": "Standard",
    "Optional": "Optional",
    "None": "None",
}

# The 12 tables and their corresponding categories
CATEGORY_TABLES = [
    ("Active Safety", "Active Safety"),
    ("Passive Safety", "Passive Safety"),
    ("Assistance/Control Features", "Assistance／Control Features"),
    ("ADAS Features", "ADAS Features"),
    ("External Features", "External Features"),
    ("Interior Features", "Interior Features"),
    ("Seat Features", "Seat Features"),
    ("Infotainment System/Connectivity", "Infotainment System／Connectivity"),
    ("Multimedia System", "Multimedia System"),
    ("Lighting Functions", "Lighting Functions"),
    ("Glass/Rearview Mirror", "Glass／Rearview Mirror"),
    ("Air Conditioning/Cooling", "Air Conditioning／Cooling"),
]


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


def load_table_records(table_id: str) -> list[dict[str, Any]]:
    for attempt in range(5):
        try:
            offset = 0
            all_records = []
            while True:
                cmd = [
                    "+record-list",
                    ["--table-id", table_id, "--offset", str(offset), "--limit", "500"],
                ]
                res = run_cli(cmd[0], cmd[1])
                rec_file = res.get("record_file")
                if not rec_file or not os.path.exists(rec_file):
                    break
                lines = []
                with open(rec_file, "r", encoding="utf-8") as fp:
                    for line in fp:
                        line = line.strip()
                        if line:
                            lines.append(json.loads(line))
                all_records.extend(lines)
                if not res.get("has_more"):
                    break
                offset = res.get("next_offset", offset + len(lines))
            return all_records
        except (RuntimeError, ValueError) as e:
            logger.warning(
                "Error loading records from %s (attempt %d/5): %s", table_id, attempt + 1, e
            )
            time.sleep(3.0)
    raise RuntimeError(f"Failed to load records from {table_id} after 5 attempts")


def get_target_fields(
    cat_orig_name: str, cat_schemas: dict[str, Any], car_values: dict[str, Any]
) -> list[str]:
    schema = cat_schemas.get(cat_orig_name, {})
    target_fields = []
    for f in schema:
        vals = set()
        for c_vals in car_values.values():
            v = c_vals.get(cat_orig_name, {}).get(f)
            if v:
                vals.add(v)
        if any(v in ["●", "○"] for v in vals) or (
            cat_orig_name in ["Passive Safety", "Active Safety"] and vals == {"-"}
        ):
            target_fields.append(f)
    return target_fields


def update_table_equipment_tags(
    cat_orig_name: str,
    table_name: str,
    table_id: str,
    target_fields: list[str],
    car_values: dict[str, Any],
) -> tuple[int, int]:
    logger.info(
        "Processing table [%s] (%s) - %d target fields", table_name, table_id, len(target_fields)
    )

    t_info = run_cli("+table-get", ["--table-id", table_id])
    fields = t_info["data"]["fields"]
    field_by_name = {f["name"]: f for f in fields}

    # 1. Update Schema of target fields to select
    updated_fields = 0
    for fname in target_fields:
        f_info = field_by_name.get(fname)
        if not f_info:
            logger.warning("Field [%s] not found in table [%s]", fname, table_name)
            continue

        source_values = {
            VALUE_MAP.get(v, v)
            for categories in car_values.values()
            if (v := categories.get(cat_orig_name, {}).get(fname))
        }
        old_options = f_info.get("options", []) if f_info.get("type") == "select" else []
        options_by_name = {o["name"]: o for o in SELECT_OPTIONS + old_options}
        for value in sorted(source_values):
            options_by_name.setdefault(
                value,
                {
                    "name": value,
                    "hue": "Orange" if "○" in value else "Green",
                    "lightness": "Lighter",
                },
            )
        if f_info.get("type") == "select" and source_values <= {o["name"] for o in old_options}:
            continue

        logger.info("Updating field [%s] (%s) to 'select'...", fname, f_info["id"])
        payload = {
            "name": fname,
            "type": "select",
            "multiple": False,
            "options": list(options_by_name.values()),
        }
        try:
            run_cli(
                "+field-update",
                [
                    "--table-id",
                    table_id,
                    "--field-id",
                    f_info["id"],
                    "--json",
                    json.dumps(payload),
                    "--yes",
                ],
            )
            updated_fields += 1
            time.sleep(0.3)
        except RuntimeError as e:
            logger.error("Failed to update field [%s] schema: %s", fname, e)

    if updated_fields > 0:
        logger.info("Updated %d field schemas. Sleeping 2s for backend indexing...", updated_fields)
        time.sleep(2.0)

    # 2. Load existing records
    records = load_table_records(table_id)
    logger.info("Loaded %d records from [%s]", len(records), table_name)

    # 3. Build update batch
    update_records: dict[str, dict[str, Any]] = {}
    total_cells_updated = 0

    for rec in records:
        rec_id = rec.get("record_id")
        car_key = rec.get("款型唯一键")
        if not rec_id or not car_key:
            continue

        raw_car_params = car_values.get(car_key.lower(), {}).get(cat_orig_name, {})
        if not raw_car_params:
            continue

        rec_patch: dict[str, Any] = {}
        for fname in target_fields:
            raw_val = raw_car_params.get(fname)
            if not raw_val:
                continue

            mapped_val = VALUE_MAP.get(raw_val.strip(), raw_val.strip())
            if mapped_val:
                # Check current record value to avoid redundant writes
                cur_val = rec.get(fname)
                if isinstance(cur_val, list):
                    cur_str = cur_val[0] if cur_val else None
                else:
                    cur_str = str(cur_val) if cur_val is not None else None

                if cur_str != mapped_val:
                    rec_patch[fname] = mapped_val

        if rec_patch:
            update_records[rec_id] = rec_patch
            total_cells_updated += len(rec_patch)

    logger.info(
        "Found %d records (%d cells) requiring update in [%s]",
        len(update_records),
        total_cells_updated,
        table_name,
    )

    # 4. Batch update in chunks of 100
    chunk_size = 100
    items = list(update_records.items())
    for i in range(0, len(items), chunk_size):
        chunk = dict(items[i : i + chunk_size])
        for attempt in range(5):
            try:
                run_cli(
                    "+record-batch-update",
                    [
                        "--table-id",
                        table_id,
                        "--json",
                        json.dumps({"update_records": chunk}),
                    ],
                )
                logger.info(
                    "Updated batch %d-%d / %d in [%s]",
                    i + 1,
                    min(i + chunk_size, len(items)),
                    len(items),
                    table_name,
                )
                break
            except (RuntimeError, ValueError) as e:
                logger.warning("Batch update error (attempt %d/5): %s", attempt + 1, e)
                time.sleep(2.0)
        time.sleep(0.5)

    return updated_fields, total_cells_updated


def main():

    from bitauto.build_category_sheets import load_all_car_params

    logger.info("Loading cached raw car parameters from %s...", RAW_DIR)
    _cars_meta, cat_schemas, car_values = load_all_car_params(RAW_DIR)

    logger.info("Fetching Base tables list...")
    cmd_res = run_cli("+table-list", [])
    tables = cmd_res["data"]["tables"]
    table_name_to_id = category_table_map(tables)

    total_fields_updated = 0
    total_cells_updated = 0

    for cat_orig, feishu_name in CATEGORY_TABLES:
        tid = table_name_to_id.get(feishu_name)
        if not tid:
            logger.error("Table [%s] not found in Base!", feishu_name)
            continue

        target_fields = get_target_fields(cat_orig, cat_schemas, car_values)
        if not target_fields:
            logger.info("No target fields for [%s]", feishu_name)
            continue

        f_cnt, c_cnt = update_table_equipment_tags(
            cat_orig, feishu_name, tid, target_fields, car_values
        )
        total_fields_updated += f_cnt
        total_cells_updated += c_cnt

    logger.info("==================================================")
    logger.info("ALL TABLES UPDATED SUCCESSFULLY!")
    logger.info("Total Field Schemas Converted to Select: %d", total_fields_updated)
    logger.info("Total Cells Updated to Standard/Optional/None: %d", total_cells_updated)
    logger.info("==================================================")


if __name__ == "__main__":
    main()
