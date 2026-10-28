import glob
import json
import logging
import os
import subprocess
import time
from typing import Any

from bitauto.category_names import category_table_map
from bitauto.config import BASE_TOKEN as CONFIG_BASE_TOKEN
from bitauto.config import DATA_DIR, TABLE_TRIMS, require_feishu
from bitauto.config import PROFILE as CONFIG_PROFILE
from bitauto.config import TABLE_SERIES as CONFIG_TABLE_SERIES
from bitauto.param_values import ensure_select_options, render_param_value

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

BASE_TOKEN = CONFIG_BASE_TOKEN
PROFILE = CONFIG_PROFILE
RAW_DIR = str(DATA_DIR / "raw")

TABLE_SERIES = CONFIG_TABLE_SERIES
TABLE_MODELS = TABLE_TRIMS


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


def load_table_existing_keys(table_id: str, key_field_name: str) -> set[str]:
    logger.info("Loading existing keys (%s) from table %s...", key_field_name, table_id)
    keys = set()
    offset = 0
    while True:
        res = run_cli(
            "+record-list", ["--table-id", table_id, "--offset", str(offset), "--limit", "500"]
        )
        rec_file = res.get("record_file")
        if not rec_file or not os.path.exists(rec_file):
            break
        lines = []
        with open(rec_file, "r", encoding="utf-8") as fp:
            for line in fp:
                line = line.strip()
                if line:
                    lines.append(json.loads(line))
        for r in lines:
            val = r.get(key_field_name)
            if val:
                keys.add(val.lower())
        if not res.get("has_more"):
            break
        offset = res.get("next_offset", offset + len(lines))
    logger.info("Found %d existing keys in table %s", len(keys), table_id)
    return keys


def sync_global_series():
    logger.info("==========================================")
    logger.info("STEP 1: Sync Global Series to [新能源车系]...")
    logger.info("==========================================")

    existing_keys = load_table_existing_keys(TABLE_SERIES, "车系唯一键")

    # Load brands
    with open(os.path.join(RAW_DIR, "brands_global_en.json"), "r", encoding="utf-8") as f:
        brands_en = json.load(f).get("data", [])
    zh_brand_map = {}
    zh_brands_path = os.path.join(RAW_DIR, "brands_global_zh.json")
    if os.path.exists(zh_brands_path):
        with open(zh_brands_path, "r", encoding="utf-8") as f:
            for b in json.load(f).get("data", []):
                zh_brand_map[b["masterId"]] = b.get("masterName", "")

    # Load all serials
    series_records = []
    seen_serials = set()

    for b in brands_en:
        mid = b["masterId"]
        en_serials_path = os.path.join(RAW_DIR, f"serials_global_{mid}_en.json")
        zh_serials_path = os.path.join(RAW_DIR, f"serials_global_{mid}_zh.json")
        if not os.path.exists(en_serials_path):
            continue

        with open(en_serials_path, "r", encoding="utf-8") as f:
            serials_en = json.load(f).get("data", [])

        zh_serial_map = {}
        if os.path.exists(zh_serials_path):
            with open(zh_serials_path, "r", encoding="utf-8") as f:
                for s in json.load(f).get("data", []):
                    zh_serial_map[s["serialId"]] = s.get("serialName", "")

        for s in serials_en:
            if s.get("isNewEnergy") != 1:
                continue

            sid = s["serialId"]
            skey = f"global_{sid}"
            if skey in existing_keys or skey in seen_serials:
                continue
            seen_serials.add(skey)

            m_name_en = b.get("masterName", "").strip()
            s_name_en = s.get("serialName", "").strip()
            full_series_name = f"{m_name_en} {s_name_en}".strip()

            status = s.get("saleStatus", 1)
            status_desc = "在售" if status == 1 else ("预售" if status in (2, 8) else "待上市")

            rec = {
                "车系全称": full_series_name,
                "车系唯一键": skey,
                "分站代码": "global",
                "分站名称": "Global",
                "来源车系ID": sid,
                "品牌ID": mid,
                "品牌英文名": m_name_en,
                "品牌中文名": zh_brand_map.get(mid, m_name_en),
                "车系英文名": s_name_en,
                "车系中文名": zh_serial_map.get(sid, s_name_en),
                "车系Spell": s.get("urlSpell", ""),
                "动力类型": "NEV",
                "销售状态": status_desc,
                "指导价区间": "-",
                "车系封面图": s.get("imgUrl", "") or "",
                "来源车系页": f"https://www.bitauto.com/global/{s.get('brandUrlSpell', '')}/{s.get('urlSpell', '')}/",
                "更新时间": time.strftime("%Y-%m-%d"),
            }
            series_records.append(rec)

    logger.info("Prepared %d new Global series to insert into [新能源车系].", len(series_records))
    for i in range(0, len(series_records), 100):
        chunk = series_records[i : i + 100]
        run_cli(
            "+record-batch-create",
            ["--table-id", TABLE_SERIES, "--json", json.dumps({"create_records": chunk})],
        )
        logger.info(
            "Inserted series chunk %d-%d / %d",
            i + 1,
            min(i + 100, len(series_records)),
            len(series_records),
        )
        time.sleep(0.5)


def global_price_fields(car_en: dict, car_zh: dict) -> dict[str, str]:
    """Preserve source price/status text; missing disclosure stays blank."""
    return {
        "指导价(EN)": car_en.get("referPriceWithUnit") or "",
        "指导价(ZH)": car_zh.get("referPriceWithUnit") or "",
    }


def sync_global_models():
    logger.info("==========================================")
    logger.info("STEP 2: Sync Global Trims to [配置款型]...")
    logger.info("==========================================")

    existing_keys = load_table_existing_keys(TABLE_MODELS, "款型唯一键")

    files = sorted(glob.glob(os.path.join(RAW_DIR, "params_global_*_en.json")))
    trims_records = []
    seen_trims = set()

    for fpath in files:
        with open(fpath, "r", encoding="utf-8") as f:
            d = json.load(f)
        if not isinstance(d, dict) or not isinstance(d.get("data"), dict):
            continue

        zh_path = fpath.removesuffix("_en.json") + "_zh.json"
        zh_cars = {}
        if os.path.exists(zh_path):
            with open(zh_path, encoding="utf-8") as zh_file:
                zh_data = json.load(zh_file)
            zh_cars = {
                (car.get("carId"), car.get("serialId")): car
                for car in (zh_data.get("data") or {}).get("carList", [])
            }
        car_list = d["data"].get("carList", [])
        param_list = d["data"].get("paramList", [])

        # Build param map: param_id -> carId -> value
        param_map: dict[int, dict[int, str]] = {}
        for g in param_list:
            for it in g.get("items", []):
                pid = it.get("id")
                if pid is None:
                    continue
                if pid not in param_map:
                    param_map[pid] = {}
                for pv in it.get("paramValues", []):
                    cid = pv.get("id")
                    val = pv.get("value")
                    if cid and val:
                        param_map[pid][cid] = str(val).strip()

        for c in car_list:
            cid = c.get("carId")
            if not cid:
                continue
            ckey = f"global_{cid}"
            if ckey in existing_keys or ckey in seen_trims:
                continue
            seen_trims.add(ckey)

            m_name = (c.get("masterName") or "").strip()
            s_name = (c.get("serialName") or "").strip()
            c_name = (c.get("carName") or "").strip()
            full_model_name = f"{m_name} {s_name} {c_name}".strip()

            def gv(
                pid: int, current_cid: int = cid, current_map: dict[int, dict[int, str]] = param_map
            ) -> str:
                return current_map.get(pid, {}).get(current_cid, "")

            # Power type
            fuel_type = gv(-9)
            power_type = "BEV"
            if "phev" in fuel_type.lower() or "plug-in" in fuel_type.lower() or "插电" in fuel_type:
                power_type = "PHEV"
            elif (
                "erev" in fuel_type.lower()
                or "range-ext" in fuel_type.lower()
                or "增程" in fuel_type
            ):
                power_type = "EREV"
            elif (
                "hev" in fuel_type.lower()
                or "hybrid" in fuel_type.lower()
                or "油电混合" in fuel_type
            ):
                power_type = "HEV"

            # Drivetrain
            drivetrain = gv(87)
            dt_tag = None
            if drivetrain in ["4WD", "RWD", "FWD", "AWD"]:
                dt_tag = drivetrain

            l = gv(14)
            w = gv(15)
            h = gv(16)
            lwh = f"{l}*{w}*{h}" if l and w and h else (f"{l} x {w} x {h}" if l else "")

            # Range & Motor Power & Battery Capacity
            range_val = gv(69) or gv(27) or gv(65)
            power_val = gv(55) or gv(59) or gv(37)
            battery_val = gv(75) or gv(64)

            year_val = c.get("year")
            try:
                year_int = int(year_val) if year_val else None
            except ValueError:
                year_int = None

            sid_val = c.get("serialId")
            try:
                sid_int = int(sid_val) if sid_val else None
            except ValueError:
                sid_int = None

            rec = {
                "车型全称": full_model_name,
                "款型唯一键": ckey,
                "分站代码": "global",
                "分站名称": "Global",
                "来源款型ID": cid,
                "来源车系ID": sid_int,
                "品牌英文名": m_name,
                "品牌中文名": m_name,
                "车系英文名": s_name,
                "车系中文名": s_name,
                "款型英文名": c_name,
                "款型中文名": c_name,
                "动力类型": power_type,
                "驱动形式": dt_tag,
                "纯电续航(km)": range_val if range_val != "-" else "",
                "电池容量(kWh)": battery_val if battery_val != "-" else "",
                "电机总功率(kW)": power_val if power_val != "-" else "",
                "长宽高(mm)": lwh,
                "轴距(mm)": gv(17) if gv(17) != "-" else "",
                "最高车速(km/h)": gv(-19) if gv(-19) != "-" else "",
                "百公里加速(s)": gv(-22) if gv(-22) != "-" else "",
                "年款": year_int,
                **global_price_fields(c, zh_cars.get((cid, c.get("serialId")), {})),
                "款型外观图": c.get("logoPath") or c.get("logoPathWp") or "",
                "采集时间": time.strftime("%Y-%m-%d"),
            }
            # Clean None values from rec so lark-cli doesn't complain
            rec = {k: v for k, v in rec.items() if v is not None}
            trims_records.append(rec)

    logger.info("Prepared %d new Global trims to insert into [配置款型].", len(trims_records))
    for i in range(0, len(trims_records), 100):
        chunk = trims_records[i : i + 100]
        run_cli(
            "+record-batch-create",
            ["--table-id", TABLE_MODELS, "--json", json.dumps({"create_records": chunk})],
        )
        logger.info(
            "Inserted trims chunk %d-%d / %d",
            i + 1,
            min(i + 100, len(trims_records)),
            len(trims_records),
        )
        time.sleep(0.5)


def sync_global_category_sheets():
    logger.info("==========================================")
    logger.info("STEP 3: Sync Global Cars to 21 Category Sheets...")
    logger.info("==========================================")
    from bitauto.build_category_sheets import CATEGORIES_ORDER, clean_field_name

    cmd_res = run_cli("+table-list", [])
    tables = cmd_res["data"]["tables"]
    table_name_to_id = category_table_map(tables)

    # Load all cars including global
    files = sorted(glob.glob(os.path.join(RAW_DIR, "params_global_*_en.json")))

    global_cars_meta = {}
    global_car_values = {}

    for fpath in files:
        with open(fpath, "r", encoding="utf-8") as fp:
            d = json.load(fp)
        if not isinstance(d, dict) or not isinstance(d.get("data"), dict):
            continue

        car_list = d["data"].get("carList", [])
        param_list = d["data"].get("paramList", [])

        for c in car_list:
            cid = c.get("carId")
            if not cid:
                continue
            key = f"global_{cid}"
            m_name = (c.get("masterName") or "").strip()
            s_name = (c.get("serialName") or "").strip()
            c_name = (c.get("carName") or "").strip()
            full_name = f"{m_name} {s_name} {c_name}".strip()

            global_cars_meta[key] = {
                "车型全称": full_name,
                "款型唯一键": key,
                "分站代码": "global",
                "分站名称": "Global",
                "品牌英文名": m_name,
                "车系英文名": s_name,
                "款型英文名": c_name,
                "动力类型": "BEV",
            }
            if key not in global_car_values:
                global_car_values[key] = {orig: {} for orig, _ in CATEGORIES_ORDER}

        for g in param_list:
            orig_gname = g.get("name")
            if not orig_gname:
                continue

            for it in g.get("items", []):
                pname = it.get("name")
                if not pname:
                    continue
                punit = it.get("unit")
                fld_name = clean_field_name(pname, punit)

                for pv in it.get("paramValues", []):
                    cid = pv.get("id")
                    if not cid:
                        continue
                    key = f"global_{cid}"
                    if key not in global_car_values:
                        continue

                    val = render_param_value(pv)

                    if val is not None:
                        val_str = str(val).strip()
                        if orig_gname in global_car_values[key]:
                            global_car_values[key][orig_gname][fld_name] = val_str

    logger.info("Indexed %d Global cars for 21 category sheets.", len(global_cars_meta))

    value_map = {
        "●": "Standard",
        "•": "Standard",
        "○": "Optional",
        "-": "None",
        "标配": "Standard",
        "选配": "Optional",
        "无": "None",
    }

    for orig_cat, feishu_name in CATEGORIES_ORDER:
        tid = table_name_to_id.get(feishu_name)
        if not tid:
            logger.warning("Table [%s] not found!", feishu_name)
            continue

        existing_keys = load_table_existing_keys(tid, "款型唯一键")
        t_info = run_cli("+table-get", ["--table-id", tid])
        fields = t_info["data"]["fields"]
        writable_fields = {f["name"]: f for f in fields}

        records_to_create = []
        for key, meta in global_cars_meta.items():
            if key in existing_keys:
                continue

            rec = dict(meta)
            p_vals = global_car_values.get(key, {}).get(orig_cat, {})
            for fname, val in p_vals.items():
                if fname in writable_fields:
                    f_info = writable_fields[fname]
                    if f_info.get("type") == "select":
                        opts = [o["name"] for o in f_info.get("options", [])]
                        mapped = value_map.get(val, val)
                        if mapped in opts:
                            rec[fname] = mapped
                    else:
                        rec[fname] = val

            rec = {k: v for k, v in rec.items() if k in writable_fields and v is not None}
            records_to_create.append(rec)

        if records_to_create:
            logger.info("Inserting %d records into [%s]...", len(records_to_create), feishu_name)
            for i in range(0, len(records_to_create), 100):
                chunk = records_to_create[i : i + 100]
                run_cli(
                    "+record-batch-create",
                    ["--table-id", tid, "--json", json.dumps({"create_records": chunk})],
                )
                time.sleep(0.4)
        else:
            logger.info("Table [%s] already has all Global records.", feishu_name)


def main():
    sync_global_series()
    sync_global_models()
    sync_global_category_sheets()

    # Link series and trims
    logger.info("Linking Global series and trims...")
    from bitauto.link_series_and_models import main as link_series_and_models

    link_series_and_models()

    logger.info("All Global station data synced and linked successfully!")


if __name__ == "__main__":
    main()
