import hashlib
import json
import logging
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

from bitauto.client import BitAutoAccessPausedError, BitAutoClient
from bitauto.config import DATA_DIR, require_feishu
from bitauto.diff_rules import RULE_VERSION, compare_values, source_fingerprint
from bitauto.feishu_sync import (
    BASE_TOKEN,
    TABLE_SERIES,
    TABLE_TRIMS,
    FeishuSyncManager,
)
from bitauto.merge import merge_station_series
from bitauto.models.entities import SeriesItem
from bitauto.models.station import get_station

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("bitauto.pipeline")


class BitautoPipeline:
    def __init__(
        self,
        base_dir: Path | None = None,
        request_interval_sec: float = 0.25,
        allowed_sale_statuses: set[int] | None = None,
    ):
        self.base_dir = base_dir or Path(__file__).resolve().parent.parent.parent
        self.data_dir = DATA_DIR
        self.raw_dir = self.data_dir / "raw"
        self.audit_dir = self.data_dir / "audit"
        self.pause_flag = self.data_dir / "PAUSED.json"

        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.audit_dir.mkdir(parents=True, exist_ok=True)

        self.client = BitAutoClient(
            raw_storage_dir=self.raw_dir,
            pause_flag_file=self.pause_flag,
            request_interval_sec=request_interval_sec,
        )
        self.sync_mgr = FeishuSyncManager(
            base_token=BASE_TOKEN, local_review_dir=self.data_dir / "reviews" / "parameter-review"
        )
        # saleStatus: 1=在售, 2/8=预售/未上市, 4=待上市/暂无报价
        self.allowed_sale_statuses = allowed_sale_statuses or {1, 2, 4, 8}

    def load_existing_keys(self) -> dict[str, set[str]]:
        logger.info("Loading catalog keys and local review keys to prevent duplicates...")
        existing = {
            "series": set(),
            "trims": set(),
            "diffs": set(),
        }

        try:
            series_rows = self.sync_mgr.search_records(TABLE_SERIES)
            for r in series_rows:
                key = r.get("车系唯一键")
                if key:
                    existing["series"].add(key.lower())
            logger.info(f"Loaded {len(existing['series'])} existing series keys.")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Could not load existing series keys: {e}")

        try:
            trim_rows = self.sync_mgr.search_records(TABLE_TRIMS)
            for r in trim_rows:
                key = r.get("款型唯一键")
                if key:
                    existing["trims"].add(key.lower())
            logger.info(f"Loaded {len(existing['trims'])} existing trim keys.")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Could not load existing trim keys: {e}")

        try:
            diff_rows = self.sync_mgr.local_diff_records()
            for r in diff_rows:
                key = r.get("差异唯一键")
                if key:
                    existing["diffs"].add(key.lower())
            logger.info(f"Loaded {len(existing['diffs'])} existing local review keys.")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Could not load local review keys: {e}")

        return existing

    def run_station(
        self,
        station_code: str,
        existing_keys: dict[str, set[str]],
        max_series: int | None = None,
    ) -> dict[str, Any]:
        st = get_station(station_code)
        logger.info(f"========== Starting Station: {st.name} ({st.code}) ==========")

        stats = {
            "station": st.code,
            "station_name": st.name,
            "brands_total": 0,
            "serials_scanned": 0,
            "nev_serials_found": 0,
            "series_created": 0,
            "trims_created": 0,
            "diffs_created": 0,
            "diffs_updated": 0,
            "skipped_series": 0,
            "errors": [],
        }

        # 1. Fetch brands in EN and ZH
        logger.info(f"[{st.code}] Fetching brands...")
        brands_en = self.client.get_master_brand_list(st, lang="en")
        brands_zh = self.client.get_master_brand_list(st, lang="zh")
        stats["brands_total"] = len(brands_en)

        zh_brand_map = {b.master_id: b.master_name for b in brands_zh}

        # 2. Iterate brands and collect NEV series
        all_nev_series: list[SeriesItem] = []
        for b in brands_en:
            try:
                serials = self.client.get_serial_list(st, b.master_id, lang="en")
                stats["serials_scanned"] += len(serials)
                for s in serials:
                    if s.is_new_energy == 1 and s.sale_status in self.allowed_sale_statuses:
                        all_nev_series.append(s)
            except Exception as e:  # noqa: BLE001
                logger.error(
                    f"[{st.code}] Error fetching serials for brand {b.master_name} ({b.master_id}): {e}"
                )
                stats["errors"].append(f"Brand {b.master_id}: {e}")

        stats["nev_serials_found"] = len(all_nev_series)
        logger.info(f"[{st.code}] Found {len(all_nev_series)} NEV series in allowed sale statuses.")

        if max_series and len(all_nev_series) > max_series:
            all_nev_series = all_nev_series[:max_series]
            logger.info(f"[{st.code}] Capped to {max_series} series for this run.")

        # Also get ZH serial names for mapping
        zh_serial_name_map: dict[int, str] = {}
        for b in brands_zh:
            # Only fetch if brand has NEV
            if any(s.master_id == b.master_id for s in all_nev_series):
                try:
                    s_zh_list = self.client.get_serial_list(st, b.master_id, lang="zh")
                    for sz in s_zh_list:
                        zh_serial_name_map[sz.serial_id] = sz.serial_name or ""
                except Exception as e:  # noqa: BLE001
                    logger.debug(
                        f"[{st.code}] Failed to get ZH serials for brand {b.master_id}: {e}"
                    )

        # 3. Process each NEV series
        series_to_create = []
        trims_to_create = []
        diffs_to_create = []

        for idx, s in enumerate(all_nev_series, start=1):
            serial_key = f"{st.code}_{s.serial_id}"
            brand_zh = zh_brand_map.get(s.master_id, "")
            serial_zh = zh_serial_name_map.get(s.serial_id, "") or s.serial_name

            logger.info(
                f"[{st.code} {idx}/{len(all_nev_series)}] Processing {s.master_name} {s.serial_name} ({s.url_spell})..."
            )

            # Check if series needs insertion
            if serial_key not in existing_keys["series"]:
                price_range = ""
                if s.min_refer_price and s.max_refer_price:
                    unit = s.monetary_unit or s.monetary_unit_symbol or ""
                    if s.min_refer_price == s.max_refer_price:
                        price_range = f"{unit} {s.min_refer_price}".strip()
                    else:
                        price_range = f"{unit} {s.min_refer_price} - {s.max_refer_price}".strip()

                status_desc = (
                    "在售"
                    if s.sale_status == 1
                    else ("预售" if s.sale_status in (2, 8) else "待上市")
                )

                s_rec = {
                    "车系唯一键": serial_key,
                    "分站代码": st.code,
                    "分站名称": st.name.split(" (")[0],
                    "来源车系ID": s.serial_id,
                    "品牌ID": s.master_id,
                    "品牌英文名": s.master_name,
                    "品牌中文名": brand_zh,
                    "车系英文名": s.serial_name,
                    "车系中文名": serial_zh,
                    "车系Spell": s.url_spell,
                    "动力类型": "NEV",
                    "销售状态": status_desc,
                    "指导价区间": price_range,
                    "车系封面图": s.img_url or "",
                    "来源车系页": st.series_page_url(s.brand_url_spell or "", s.url_spell),
                    "更新时间": time.strftime("%Y-%m-%d"),
                }
                series_to_create.append(s_rec)
                existing_keys["series"].add(serial_key)

            # Fetch parameters in EN and ZH
            try:
                data_en = self.client.get_param_detail(st, s.url_spell, lang="en")
                data_zh = self.client.get_param_detail(st, s.url_spell, lang="zh")
            except Exception as e:  # noqa: BLE001
                logger.error(f"[{st.code}] Error fetching param details for {s.url_spell}: {e}")
                stats["errors"].append(f"Series {s.serial_id} ({s.url_spell}): {e}")
                continue

            merged_trims, diffs = merge_station_series(
                station=st,
                serial_url_spell=s.url_spell,
                data_en=data_en,
                data_zh=data_zh,
            )

            for t in merged_trims:
                if t.stable_key in existing_keys["trims"]:
                    continue

                params = t.parameters

                def get_val(pid: int, p_map: dict = params) -> str:
                    info = p_map.get(str(pid)) or p_map.get(pid) or {}
                    return info.get("value") or ""

                l = get_val(14)
                w = get_val(15)
                h = get_val(16)
                lwh = f"{l} / {w} / {h}" if (l or w or h) else ""

                full_name = f"{s.master_name} {s.serial_name} {t.car_name_en}".strip()

                rec = {
                    "款型唯一键": t.stable_key,
                    "分站代码": t.country_code,
                    "来源款型ID": t.car_id,
                    "来源车系ID": s.serial_id,
                    "车型全称": full_name,
                    "款型英文名": t.car_name_en,
                    "款型中文名": t.car_name_zh,
                    "车系英文名": s.serial_name,
                    "车系中文名": serial_zh,
                    "品牌英文名": s.master_name,
                    "品牌中文名": brand_zh,
                    "年款": t.year,
                    "指导价(EN)": t.price_en or "",
                    "指导价(ZH)": t.price_zh or "",
                    "货币代码": t.currency,
                    "款型外观图": t.img_url or "",
                    "长宽高(mm)": lwh,
                    "轴距(mm)": get_val(17),
                    "驱动形式": get_val(87),
                    "电机总功率(kW)": get_val(59) or get_val(37),
                    "纯电续航(km)": get_val(65) or get_val(27),
                    "最高车速(km/h)": get_val(-19),
                    "百公里加速(s)": get_val(-22),
                    "采集时间": t.crawl_time,
                }
                trims_to_create.append(rec)
                existing_keys["trims"].add(t.stable_key)

            # Revisit existing alerts even when this batch now agrees. Historical
            # rows are retained and excluded, rather than left permanently open.
            candidate_keys = {f"{d.country_code}_{d.car_id}_{d.param_id}".lower() for d in diffs}
            zh_cars = {car.car_id: car for car in data_zh.car_list}
            en_counts = Counter(car.car_id for car in data_en.car_list)
            zh_counts = Counter(car.car_id for car in data_zh.car_list)
            snapshot_proof = {}
            for lang in ("en", "zh"):
                snapshot = self.raw_dir / f"params_{st.code}_{s.url_spell}_{lang}.json"
                snapshot_proof[lang] = {
                    "path": str(snapshot),
                    "sha256": hashlib.sha256(snapshot.read_bytes()).hexdigest()
                    if snapshot.exists()
                    else None,
                    "captured_at": None,
                }
            for trim in merged_trims:
                for pid, parameter in trim.parameters.items():
                    diff_key = f"{trim.country_code}_{trim.car_id}_{pid}".lower()
                    if diff_key not in candidate_keys and diff_key not in existing_keys["diffs"]:
                        continue
                    car_zh = zh_cars.get(trim.car_id)
                    review = compare_values(
                        parameter["raw_val_en"],
                        parameter["raw_val_zh"],
                        param_id=pid,
                        name_en=parameter["param_name_en"],
                        name_zh=parameter["param_name_zh"],
                        identity_verified=bool(
                            car_zh
                            and car_zh.serial_id == trim.serial_id
                            and en_counts[trim.car_id] == zh_counts[trim.car_id] == 1
                        ),
                    )
                    full_name = (
                        f"{trim.master_name_en} {trim.serial_name_en} {trim.car_name_en}".strip()
                    )
                    parameter_name = parameter["param_name_zh"] or parameter["param_name_en"]
                    diffs_to_create.append(
                        {
                            "差异唯一键": diff_key.upper(),
                            "核查事项": f"{full_name}｜{st.name}｜{parameter_name}",
                            "分站代码": trim.country_code,
                            "款型ID": trim.car_id,
                            "参数ID": pid,
                            "参数分组(EN)": parameter["group_name_en"],
                            "参数分组(ZH)": parameter["group_name_zh"],
                            "参数名称(EN)": parameter["param_name_en"],
                            "参数名称(ZH)": parameter["param_name_zh"],
                            "英文原值": parameter["raw_val_en"] or "",
                            "中文原值": parameter["raw_val_zh"] or "",
                            "差异说明": review.reason,
                            "记录时间": time.strftime("%Y-%m-%d"),
                            "车型全称": full_name,
                            "国家": st.name,
                            "参数": parameter_name,
                            "判断结果": [review.result],
                            "判断说明": review.reason,
                            "处理状态": [
                                "已排除" if review.result in {"一致", "等价表达"} else "待核查"
                            ],
                            "复核英文值": parameter["raw_val_en"] or "",
                            "复核中文值": parameter["raw_val_zh"] or "",
                            "源值指纹": source_fingerprint(
                                f"{st.code}_{trim.serial_id}_{trim.car_id}",
                                pid,
                                parameter["raw_val_en"],
                                parameter["raw_val_zh"],
                            ),
                            "规则版本": RULE_VERSION,
                            "复核时间": trim.crawl_time,
                            "复核方式": "采集批次参数复核（可能命中本地缓存）",
                            "来源快照证据": json.dumps(snapshot_proof, ensure_ascii=False),
                            "快照时间说明": "批次时间不代表源响应抓取时间；未记录抓取时间时留空。",
                            "来源车系ID": trim.serial_id,
                            "来源页面": st.series_page_url(s.brand_url_spell or "", s.url_spell),
                        }
                    )
                    existing_keys["diffs"].add(diff_key)

            # Flush to Feishu periodically if buffer is large
            if len(series_to_create) >= 50:
                logger.info(f"[{st.code}] Flushing {len(series_to_create)} series to Feishu...")
                self.sync_mgr.batch_create_records(TABLE_SERIES, series_to_create)
                stats["series_created"] += len(series_to_create)
                series_to_create.clear()

            if len(trims_to_create) >= 100:
                logger.info(f"[{st.code}] Flushing {len(trims_to_create)} trims to Feishu...")
                self.sync_mgr.batch_create_records(TABLE_TRIMS, trims_to_create)
                stats["trims_created"] += len(trims_to_create)
                trims_to_create.clear()

            if len(diffs_to_create) >= 100:
                logger.info(
                    f"[{st.code}] Flushing {len(diffs_to_create)} reviews to the local ledger..."
                )
                changed = self.sync_mgr.upsert_diff_records(diffs_to_create)
                stats["diffs_created"] += changed["created"]
                stats["diffs_updated"] += changed["updated"]
                diffs_to_create.clear()

        # Flush remaining buffers
        if series_to_create:
            logger.info(
                f"[{st.code}] Flushing remaining {len(series_to_create)} series to Feishu..."
            )
            self.sync_mgr.batch_create_records(TABLE_SERIES, series_to_create)
            stats["series_created"] += len(series_to_create)

        if trims_to_create:
            logger.info(f"[{st.code}] Flushing remaining {len(trims_to_create)} trims to Feishu...")
            self.sync_mgr.batch_create_records(TABLE_TRIMS, trims_to_create)
            stats["trims_created"] += len(trims_to_create)

        if diffs_to_create:
            logger.info(
                f"[{st.code}] Flushing remaining {len(diffs_to_create)} reviews to the local ledger..."
            )
            changed = self.sync_mgr.upsert_diff_records(diffs_to_create)
            stats["diffs_created"] += changed["created"]
            stats["diffs_updated"] += changed["updated"]

        logger.info(
            f"[{st.code}] Completed: series_created={stats['series_created']}, "
            f"trims_created={stats['trims_created']}, diffs_created={stats['diffs_created']}"
        )
        return stats

    def run_all(self, stations: list[str] | None = None) -> list[dict[str, Any]]:
        # Recommended order: compact stations first, then medium, then large
        station_order = stations or ["ae", "sa", "sg", "au", "uk", "my", "hk", "us"]

        existing_keys = self.load_existing_keys()
        all_stats = []

        total_t0 = time.time()
        for code in station_order:
            t0 = time.time()
            try:
                st_stats = self.run_station(code, existing_keys)
                st_stats["duration_sec"] = round(time.time() - t0, 1)
                all_stats.append(st_stats)
            except BitAutoAccessPausedError as e:
                logger.error(f"PIPELINE PAUSED: {e}")
                break
            except Exception as e:
                logger.exception("Fatal error processing station %s", code)
                all_stats.append({"station": code, "error": str(e)})

        total_duration = round(time.time() - total_t0, 1)
        summary_file = self.audit_dir / "pipeline_run_summary.json"
        summary_file.write_text(
            json.dumps(all_stats, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info(f"Pipeline finished in {total_duration}s. Summary written to {summary_file}")
        return all_stats


if __name__ == "__main__":
    require_feishu()
    target_stations = sys.argv[1:] if len(sys.argv) > 1 else None
    pipeline = BitautoPipeline()
    pipeline.run_all(stations=target_stations)
