import glob
import json
import logging
import os
import subprocess
import time
from collections import OrderedDict
from typing import Any

from bitauto.category_names import CATEGORIES_ORDER, category_table_map
from bitauto.config import BASE_TOKEN as CONFIG_BASE_TOKEN
from bitauto.config import DATA_DIR, require_feishu
from bitauto.config import PROFILE as CONFIG_PROFILE
from bitauto.param_values import ensure_select_options, render_param_value

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

BASE_TOKEN = CONFIG_BASE_TOKEN
PROFILE = CONFIG_PROFILE

STATION_ENGLISH_NAMES = {
    "ae": "UAE",
    "sa": "Saudi Arabia",
    "sg": "Singapore",
    "au": "Australia",
    "uk": "United Kingdom",
    "my": "Malaysia",
    "hk": "Hong Kong",
    "us": "United States",
    "global": "Global",
}


def clean_field_name(name: str, unit: str | None) -> str:
    full = f"{name}[{unit}]" if unit and f"[{unit}]" not in name else name
    # Replace forbidden square brackets with round parentheses
    return full.replace("[", "(").replace("]", ")").replace('"', "").strip()


def run_cli(subcmd: str, args: list[str]) -> dict[str, Any]:
    require_feishu()
    from bitauto.uppercase_station_codes import normalize_payload

    if subcmd in {"+record-batch-create", "+record-batch-update"}:
        args = list(args)
        index = args.index("--json") + 1
        args[index] = json.dumps(normalize_payload(json.loads(args[index])))
        if subcmd == "+record-batch-create":
            ensure_select_options(
                run_cli,
                args[args.index("--table-id") + 1],
                json.loads(args[index])["create_records"],
            )
    cmd = ["lark-cli", "--profile", PROFILE, "base", subcmd, "--base-token", BASE_TOKEN] + args
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if res.returncode != 0:
        raise RuntimeError(f"lark-cli error ({res.returncode}): {res.stderr or res.stdout}")
    try:
        return json.loads(res.stdout)
    except Exception as e:
        raise RuntimeError(f"Failed to parse lark-cli JSON: {res.stdout} / {res.stderr}") from e


def load_all_car_params(raw_dir: str):
    logger.info("Loading and indexing all cached params JSON files...")
    files = sorted(glob.glob(os.path.join(raw_dir, "params_*_en.json")))

    # Track cars metadata: key -> dict
    cars_meta: dict[str, dict[str, Any]] = {}
    # Track category parameters schema: cat_orig_name -> OrderedDict of clean_field_name -> param_id
    cat_schemas: dict[str, OrderedDict[str, int]] = {
        orig: OrderedDict() for orig, _ in CATEGORIES_ORDER
    }
    # Track car values: key -> cat_orig_name -> clean_field_name -> value_str
    car_values: dict[str, dict[str, dict[str, str]]] = {}

    for fpath in files:
        fname = os.path.basename(fpath)
        parts = fname.split("_")
        station = parts[1]
        st_name = STATION_ENGLISH_NAMES.get(station, station)

        try:
            with open(fpath, "r", encoding="utf-8") as fp:
                d = json.load(fp)
        except (OSError, json.JSONDecodeError) as e:
            logger.debug("Failed reading %s: %s", fpath, e)
            continue

        if not isinstance(d, dict) or not isinstance(d.get("data"), dict):
            continue

        car_list = d["data"].get("carList", [])
        param_list = d["data"].get("paramList", [])

        # Store car metadata
        for c in car_list:
            cid = c.get("carId")
            if not cid:
                continue
            key = f"{station}_{cid}"
            if key not in cars_meta:
                cars_meta[key] = {
                    "款型唯一键": key,
                    "分站代码": station,
                    "分站名称": st_name,
                    "品牌英文名": c.get("masterName", "") or "",
                    "车系英文名": c.get("serialName", "") or "",
                    "款型英文名": c.get("carName", "") or "",
                    "动力类型": "",
                }
                car_values[key] = {orig: {} for orig, _ in CATEGORIES_ORDER}

        # Parse param groups
        for g in param_list:
            orig_gname = g.get("name")
            if orig_gname not in cat_schemas:
                continue

            for it in g.get("items", []):
                pname = it.get("name")
                if not pname:
                    continue
                punit = it.get("unit")
                pid = it.get("id")
                fld_name = clean_field_name(pname, punit)
                if fld_name not in cat_schemas[orig_gname]:
                    cat_schemas[orig_gname][fld_name] = pid

                # Collect values for each car
                for pv in it.get("paramValues", []):
                    cid = pv.get("id")
                    if not cid:
                        continue
                    key = f"{station}_{cid}"
                    if key not in car_values:
                        continue

                    val = render_param_value(pv)

                    if val:
                        car_values[key][orig_gname][fld_name] = str(val)

                        # If this is Fuel Type in General Information, update car power type
                        if orig_gname == "General Information" and "Fuel Type" in pname:
                            cars_meta[key]["动力类型"] = str(val)

    logger.info(
        "Indexed %d unique cars across %d categories.", len(cars_meta), len(CATEGORIES_ORDER)
    )
    return cars_meta, cat_schemas, car_values


def get_existing_tables():
    res = run_cli("+table-list", [])
    tables = res.get("data", {}).get("tables", [])
    return category_table_map(tables)


def build_and_sync_all_categories(raw_dir: str):
    cars_meta, cat_schemas, car_values = load_all_car_params(raw_dir)
    existing_tables = get_existing_tables()
    logger.info("Currently existing tables in Base: %s", list(existing_tables.keys()))

    sorted_keys = sorted(cars_meta.keys())

    for orig_cat, table_name in CATEGORIES_ORDER:
        fields_schema = cat_schemas[orig_cat]
        logger.info("\n==========================================")
        logger.info("Processing Category: [%s] -> Table: [%s]", orig_cat, table_name)
        logger.info("Category has %d parameter fields.", len(fields_schema))

        table_id = existing_tables.get(table_name)
        if table_id:
            logger.info(
                "Table [%s] already exists (id=%s). Checking record count...", table_name, table_id
            )
            res = run_cli("+table-list", [])
            complete = False
            for t in res.get("data", {}).get("tables", []):
                if t["id"] == table_id and t.get("records_count", 0) >= len(sorted_keys):
                    logger.info(
                        "Table [%s] already has %d records. Skipping.",
                        table_name,
                        t.get("records_count"),
                    )
                    complete = True
                    break
            if complete:
                continue
        else:
            # Construct field definitions
            # First field must be primary field
            fields_def = [{"name": "款型唯一键", "type": "text"}]
            fields_def.append({"name": "分站代码", "type": "text"})
            fields_def.append({"name": "分站名称", "type": "text"})
            fields_def.append({"name": "品牌英文名", "type": "text"})
            fields_def.append({"name": "车系英文名", "type": "text"})
            fields_def.append({"name": "款型英文名", "type": "text"})
            fields_def.append({"name": "动力类型", "type": "text"})

            for fld in fields_schema:
                fields_def.append({"name": fld, "type": "text"})

            logger.info("Creating table [%s] with %d total columns...", table_name, len(fields_def))
            res = run_cli(
                "+table-create", ["--name", table_name, "--fields", json.dumps(fields_def)]
            )
            if not res.get("ok"):
                logger.error("Failed to create table [%s]: %s", table_name, res)
                continue
            table_id = res["data"]["table"]["id"]
            logger.info("Successfully created table [%s] (id=%s)", table_name, table_id)
            existing_tables[table_name] = table_id
            time.sleep(1)

        # Prepare records for insertion
        records_to_insert = []
        for key in sorted_keys:
            meta = cars_meta[key]
            rec_fields = {
                "款型唯一键": meta["款型唯一键"],
                "分站代码": meta["分站代码"],
                "分站名称": meta["分站名称"],
                "品牌英文名": meta["品牌英文名"],
                "车系英文名": meta["车系英文名"],
                "款型英文名": meta["款型英文名"],
                "动力类型": meta["动力类型"],
            }
            # Fill parameter fields
            cat_vals = car_values[key].get(orig_cat, {})
            for fld in fields_schema:
                if fld in cat_vals:
                    rec_fields[fld] = cat_vals[fld]

            records_to_insert.append(rec_fields)

        # Batch insert in chunks of 150
        chunk_size = 150
        logger.info(
            "Inserting %d records into [%s] in chunks of %d...",
            len(records_to_insert),
            table_name,
            chunk_size,
        )
        total_batches = (len(records_to_insert) + chunk_size - 1) // chunk_size

        for b_idx in range(0, len(records_to_insert), chunk_size):
            chunk = records_to_insert[b_idx : b_idx + chunk_size]
            payload = {"create_records": chunk}
            batch_num = b_idx // chunk_size + 1
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    res_in = run_cli(
                        "+record-batch-create",
                        ["--table-id", table_id, "--json", json.dumps(payload)],
                    )
                    if res_in.get("ok"):
                        break
                    else:
                        logger.warning(
                            "Batch %d/%d failed (attempt %d): %s",
                            batch_num,
                            total_batches,
                            attempt + 1,
                            res_in,
                        )
                        time.sleep(2)
                except (RuntimeError, OSError) as e:
                    logger.warning(
                        "Batch %d/%d error (attempt %d): %s",
                        batch_num,
                        total_batches,
                        attempt + 1,
                        e,
                    )
                    time.sleep(2)
            else:
                logger.error(
                    "Failed to insert batch %d/%d into %s after %d retries",
                    batch_num,
                    total_batches,
                    table_name,
                    max_retries,
                )

            if batch_num % 5 == 0 or batch_num == total_batches:
                logger.info(
                    "Progress [%s]: Batch %d/%d inserted.", table_name, batch_num, total_batches
                )
            time.sleep(0.2)

        logger.info("Finished table [%s].", table_name)


if __name__ == "__main__":
    raw_directory = str(DATA_DIR / "raw")
    build_and_sync_all_categories(raw_directory)
