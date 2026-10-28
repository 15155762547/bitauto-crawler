"""Review old alerts against saved bilingual snapshots, without network crawling."""

import datetime
import hashlib
import json
from pathlib import Path
from typing import Any

from bitauto.diff_rules import RULE_VERSION, compare_values, source_fingerprint
from bitauto.models.station import get_station
from bitauto.param_values import render_param_value

ORIGINAL_FIELDS = {
    "差异唯一键",
    "分站代码",
    "款型ID",
    "参数ID",
    "参数分组(EN)",
    "参数分组(ZH)",
    "参数名称(EN)",
    "参数名称(ZH)",
    "英文原值",
    "中文原值",
    "差异说明",
    "记录时间",
}
HUMAN_FIELDS = {"人工结论", "人工备注", "历史处理依据"}


def row_fields(row: dict[str, Any]) -> dict[str, Any]:
    return row.get("fields", row)


def scalar(value: Any) -> Any:
    return (value[0] if value else None) if isinstance(value, list) else value


def diff_update_patch(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """Original evidence is immutable; human decisions survive automated reruns."""
    patch = {
        key: value
        for key, value in new.items()
        if key not in ORIGINAL_FIELDS | HUMAN_FIELDS and old.get(key) != value
    }
    previous = old.get("源值指纹")
    changed = previous and previous != new.get("源值指纹")
    if changed:
        patch["处理状态"] = ["待复核"]
        history = {
            "源值指纹": previous,
            "判断结果": old.get("判断结果"),
            "判断说明": old.get("判断说明"),
            "人工结论": old.get("人工结论"),
            "处理状态": old.get("处理状态"),
            "规则版本": old.get("规则版本"),
        }
        patch["历史处理依据"] = (
            (old.get("历史处理依据") or "") + json.dumps(history, ensure_ascii=False) + "\n"
        )
    elif (
        old.get("人工结论")
        or old.get("人工备注")
        or scalar(old.get("处理状态")) in {"已人工处理", "待复核"}
    ):
        patch.pop("处理状态", None)
    return patch


def snapshot_index(raw_dir: Path) -> dict[tuple[str, int, str], list[dict[str, Any]]]:
    index: dict[tuple[str, int, str], list[dict[str, Any]]] = {}
    for path in sorted(raw_dir.glob("params_*.json")):
        pieces = path.stem.split("_")
        country, lang = pieces[1].upper(), pieces[-1]
        if lang not in {"en", "zh"}:
            continue
        body = path.read_bytes()
        data = json.loads(body).get("data") or {}
        entry = {
            "path": str(path.resolve()),
            "sha256": hashlib.sha256(body).hexdigest(),
            "data": data,
        }
        for car in data.get("carList", []):
            index.setdefault((country, int(car["carId"]), lang), []).append(
                {"car": car, "snapshot": entry}
            )
    return index


def parameter_value(data: dict[str, Any], car_id: int, param_id: int) -> dict[str, Any] | None:
    matches = []
    for group in data.get("paramList", []):
        for item in group.get("items", []):
            if item["id"] != param_id:
                continue
            for value in item.get("paramValues", []):
                if int(value["id"]) == car_id:
                    matches.append(
                        {
                            "name": item.get("name", ""),
                            "group": group.get("name", ""),
                            "value": render_param_value(value),
                            "raw": value,
                        }
                    )
    return matches[0] if len(matches) == 1 else None


def build_review_plan(
    rows: list[dict[str, Any]],
    trims: list[dict[str, Any]],
    series: list[dict[str, Any]],
    raw_dir: Path,
    *,
    reviewed_at: str | None = None,
) -> list[dict[str, Any]]:
    reviewed_at = reviewed_at or datetime.datetime.now(datetime.UTC).isoformat()
    index = snapshot_index(raw_dir)
    trim_index: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for trim in trims:
        fields = row_fields(trim)
        key = (str(scalar(fields["分站代码"])).upper(), int(fields["来源款型ID"]))
        trim_index.setdefault(key, []).append(trim)
    series_index = {
        (
            str(scalar(row_fields(x)["分站代码"])).upper(),
            int(row_fields(x)["来源车系ID"]),
        ): row_fields(x)
        for x in series
    }
    plan = []
    for row in rows:
        fields = row_fields(row)
        key = fields["差异唯一键"].upper()
        code = str(scalar(fields["分站代码"])).upper()
        car_id, pid = int(fields["款型ID"]), int(fields["参数ID"])
        linked = trim_index.get((code, car_id), [])
        trim = row_fields(linked[0]) if len(linked) == 1 else {}
        sid = int(trim.get("来源车系ID", 0))
        snapshots = [index.get((code, car_id, lang), []) for lang in ("en", "zh")]
        identity = bool(
            len(linked) == 1
            and sid
            and all(
                len(entries) == 1 and entries[0]["car"].get("serialId") == sid
                for entries in snapshots
            )
            and key == f"{code}_{car_id}_{pid}"
            and str(trim.get("款型唯一键", "")).upper() == f"{code}_{car_id}"
        )
        values = [
            parameter_value(entries[0]["snapshot"]["data"], car_id, pid)
            if len(entries) == 1
            else None
            for entries in snapshots
        ]
        en, zh = values
        comparison = compare_values(
            en["value"] if en else None,
            zh["value"] if zh else None,
            param_id=pid,
            name_en=en["name"] if en else fields["参数名称(EN)"],
            name_zh=zh["name"] if zh else fields["参数名称(ZH)"],
            identity_verified=identity,
        )
        fingerprint = source_fingerprint(
            f"{code}_{sid}_{car_id}",
            pid,
            en["value"] if en else None,
            zh["value"] if zh else None,
        )
        name = trim.get("车型全称", f"{code} 款型{car_id}")
        parameter = fields["参数名称(ZH)"] or fields["参数名称(EN)"]
        status = "已排除" if comparison.result in {"一致", "等价表达"} else "待核查"
        evidence = {
            lang: {
                "path": entries[0]["snapshot"]["path"],
                "sha256": entries[0]["snapshot"]["sha256"],
                "country": code,
                "serial_id": entries[0]["car"].get("serialId"),
                "car_id": car_id,
                "parameter": value,
                "captured_at": None,
            }
            if len(entries) == 1
            else {"matches": len(entries)}
            for lang, entries, value in zip(("en", "zh"), snapshots, values, strict=True)
        }
        new = {
            "核查事项": f"{name}｜{get_station(code.lower()).name}｜{parameter}",
            "差异唯一键": key,
            "车型全称": name,
            "国家": get_station(code.lower()).name,
            "参数": parameter,
            "判断结果": [comparison.result],
            "判断说明": comparison.reason,
            "处理状态": [status],
            "来源车系ID": sid or None,
            "来源页面": series_index.get((code, sid), {}).get("来源车系页", ""),
            "关联款型": [{"id": linked[0]["record_id"]}] if identity else [],
            "复核英文值": en["value"] if en else "",
            "复核中文值": zh["value"] if zh else "",
            "源值指纹": fingerprint,
            "规则版本": RULE_VERSION,
            "复核时间": reviewed_at,
            "复核方式": "存量双语快照复核（非实时）",
            "快照时间说明": "原响应未记录可验证抓取时间；不以文件修改时间或本次复核时间代替。",
            "来源快照证据": json.dumps(evidence, ensure_ascii=False),
        }
        plan.append(
            {
                "record_id": row["record_id"],
                "key": key,
                "fields": new,
                "original_values_match_snapshot": bool(
                    en
                    and zh
                    and en["value"] == fields["英文原值"]
                    and zh["value"] == fields["中文原值"]
                ),
                "identity_verified": identity,
                "patch": diff_update_patch(fields, new),
            }
        )
    return plan
