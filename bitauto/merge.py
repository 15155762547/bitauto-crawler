import datetime
import logging
import re
from collections import Counter
from typing import Any

from bitauto.diff_rules import RULE_VERSION, compare_values
from bitauto.models.entities import (
    CarInfoItem,
    MergedTrimRecord,
    ParamDetailData,
    ParamDiffRecord,
)
from bitauto.models.station import CountryStation
from bitauto.param_values import render_param_value

logger = logging.getLogger(__name__)


def clean_val(v: str | None) -> str:
    if v is None:
        return ""
    s = str(v).strip()
    if s in ("-", "null", "None", "TBC", "暂无", "待查"):
        return ""
    return s


def normalize_numeric_str(s: str) -> str:
    """Normalize numeric values (e.g. '4,800' vs '4800') for comparison."""
    return re.sub(r"[,\s]", "", s)


def is_genuine_discrepancy(val_en: str, val_zh: str, **context: Any) -> bool:
    """Compatibility helper: unresolved translations are not proven conflicts."""
    return compare_values(val_en, val_zh, **context).result == "疑似差异"


def merge_trim_pair(
    station: CountryStation,
    serial_url_spell: str,
    car_en: CarInfoItem,
    car_zh: CarInfoItem | None,
    params_en_map: dict[int, dict[str, Any]],  # param_id -> {group_name, param_name, value}
    params_zh_map: dict[int, dict[str, Any]],  # param_id -> {group_name, param_name, value}
    crawl_time: str | None = None,
    identity_verified: bool = True,
) -> MergedTrimRecord:
    now_str = crawl_time or datetime.datetime.now(datetime.UTC).isoformat()
    car_id = car_en.car_id

    stable_key = f"{station.code}_{car_id}"

    # Base metadata
    year = car_en.year or (car_zh.year if car_zh else None)
    car_name_en = car_en.car_name or ""
    car_name_zh = (car_zh.car_name if car_zh else "") or ""
    serial_name_en = car_en.serial_name or ""
    serial_name_zh = (car_zh.serial_name if car_zh else "") or ""
    master_name_en = car_en.master_name or ""
    master_name_zh = (car_zh.master_name if car_zh else "") or ""

    price_en = car_en.refer_price_with_unit
    price_zh = car_zh.refer_price_with_unit if car_zh else None
    img_url = car_en.img_url or (car_zh.img_url if car_zh else None)

    all_param_ids = sorted(set(params_en_map.keys()) | set(params_zh_map.keys()))

    merged_params: dict[int, dict[str, Any]] = {}
    diffs: list[ParamDiffRecord] = []

    for pid in all_param_ids:
        en_info = params_en_map.get(pid, {})
        zh_info = params_zh_map.get(pid, {})

        group_en = en_info.get("group_name", "")
        group_zh = zh_info.get("group_name", "")
        name_en = en_info.get("param_name", "")
        name_zh = zh_info.get("param_name", "")

        raw_val_en = en_info.get("value", "")
        raw_val_zh = zh_info.get("value", "")

        val_en = clean_val(raw_val_en)
        val_zh = clean_val(raw_val_zh)

        # Unified best value: preference for non-empty
        # If both are present:
        if val_en and not val_zh:
            best_val = val_en
            source_lang = "en"
        elif val_zh and not val_en:
            best_val = val_zh
            source_lang = "zh"
        elif val_en and val_zh:
            best_val = val_en
            source_lang = "both"

        else:
            best_val = ""
            source_lang = "none"

        explicit_pending = any(
            str(value or "").strip().casefold() in {"tbc", "待查", "待定"}
            for value in (raw_val_en, raw_val_zh)
        )
        if (val_en and val_zh) or explicit_pending:
            review = compare_values(
                raw_val_en,
                raw_val_zh,
                param_id=pid,
                name_en=name_en,
                name_zh=name_zh,
                identity_verified=bool(
                    identity_verified
                    and car_zh
                    and car_en.serial_id
                    and car_en.serial_id == car_zh.serial_id
                    and car_en.car_id == car_zh.car_id
                ),
            )
            if review.result in {"疑似差异", "资料不足", "身份待核"}:
                diffs.append(
                    ParamDiffRecord(
                        country_code=station.code,
                        serial_id=car_en.serial_id or 0,
                        car_id=car_id,
                        param_id=pid,
                        param_name_en=name_en,
                        param_name_zh=name_zh,
                        group_name_en=group_en,
                        group_name_zh=group_zh,
                        val_en=raw_val_en,
                        val_zh=raw_val_zh,
                        diff_reason=review.reason,
                        review_result=review.result,
                        rule_version=RULE_VERSION,
                    )
                )
        merged_params[pid] = {
            "param_id": pid,
            "group_name_en": group_en,
            "group_name_zh": group_zh,
            "param_name_en": name_en,
            "param_name_zh": name_zh,
            "value": best_val,
            "raw_val_en": raw_val_en,
            "raw_val_zh": raw_val_zh,
            "source_lang": source_lang,
        }

    return MergedTrimRecord(
        stable_key=stable_key,
        country_code=station.code,
        country_id=station.country_id,
        serial_id=car_en.serial_id or 0,
        serial_url_spell=serial_url_spell,
        car_id=car_id,
        year=year,
        car_name_en=car_name_en,
        car_name_zh=car_name_zh,
        serial_name_en=serial_name_en,
        serial_name_zh=serial_name_zh,
        master_name_en=master_name_en,
        master_name_zh=master_name_zh,
        price_en=price_en,
        price_zh=price_zh,
        currency=station.currency,
        img_url=img_url,
        parameters=merged_params,
        diffs=diffs,
        crawl_time=now_str,
    )


def extract_car_param_map(param_data: ParamDetailData, car_id: int) -> dict[int, dict[str, Any]]:
    res: dict[int, dict[str, Any]] = {}
    for grp in param_data.param_list:
        grp_name = grp.name or ""
        for it in grp.items:
            pid = it.param_id
            pname = it.name or ""
            # Find the value for this car_id
            val = ""
            for pv in it.param_values:
                if pv.car_id == car_id:
                    val = render_param_value({"value": pv.value, "subList": pv.sub_list})
                    break
            res[pid] = {
                "group_name": grp_name,
                "param_name": pname,
                "value": val,
            }
    return res


def merge_station_series(
    station: CountryStation,
    serial_url_spell: str,
    data_en: ParamDetailData,
    data_zh: ParamDetailData,
    crawl_time: str | None = None,
) -> tuple[list[MergedTrimRecord], list[ParamDiffRecord]]:
    """Merges all trims and parameters for a series in a station."""
    zh_car_map: dict[int, CarInfoItem] = {c.car_id: c for c in data_zh.car_list}
    en_counts = Counter(car.car_id for car in data_en.car_list)
    zh_counts = Counter(car.car_id for car in data_zh.car_list)
    merged_trims: list[MergedTrimRecord] = []
    all_diffs: list[ParamDiffRecord] = []

    for car_en in data_en.car_list:
        car_id = car_en.car_id
        car_zh = zh_car_map.get(car_id)

        params_en = extract_car_param_map(data_en, car_id)
        params_zh = extract_car_param_map(data_zh, car_id)

        record = merge_trim_pair(
            station=station,
            serial_url_spell=serial_url_spell,
            car_en=car_en,
            car_zh=car_zh,
            params_en_map=params_en,
            params_zh_map=params_zh,
            crawl_time=crawl_time,
            identity_verified=en_counts[car_id] == zh_counts[car_id] == 1,
        )
        merged_trims.append(record)
        all_diffs.extend(record.diffs)

    return merged_trims, all_diffs
