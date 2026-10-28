"""Conservative bilingual comparison; never changes the selected catalog value."""

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass

RULE_VERSION = "2026-10-26.1"
REVIEW_RESULTS = ("一致", "等价表达", "疑似差异", "资料不足", "身份待核")
MISSING = {"", "-", "null", "tbc", "暂无", "待查", "待定"}

# Exact official pairs, restricted to their parameter context. Do not use the
# global translation dictionary for drivetrain qualifiers such as motor count.
OFFICIAL_PAIRS = {
    49: {
        ("China 6", "中国第六阶段"),
        ("Euro 6", "欧洲第六阶段"),
    },
    89: {("5-Link Independent Suspension", "五连杆式独立悬架")},
    90: {
        ("5-Link Independent Suspension", "五连杆式独立悬架"),
        ("5-link Coil Spring Non-independent Suspension", "五连杆螺旋弹簧非独立悬架"),
    },
}
CONTEXTS = {
    49: ("Emission Standard", "环保标准"),
    87: ("Drivetrain", "驱动形式"),
    89: ("Front Suspension Type", "前悬架类型"),
    90: ("Rear Suspension Type", "后悬架类型"),
}


@dataclass(frozen=True)
class Comparison:
    result: str
    reason: str


def normalized(value: str | None) -> str:
    value = unicodedata.normalize("NFKC", str(value or "")).strip()
    # Only thousands separators, not decimal commas or arbitrary punctuation.
    value = re.sub(r"(?<=\d),(?=\d{3}(?:\D|$))", "", value)
    return re.sub(r"\s+", " ", value).casefold()


def source_fingerprint(identity: str, param_id: int, en: str | None, zh: str | None) -> str:
    body = json.dumps([identity.upper(), param_id, en, zh], ensure_ascii=False)
    return hashlib.sha256(body.encode()).hexdigest()


def compare_values(
    en: str | None,
    zh: str | None,
    *,
    param_id: int | None = None,
    name_en: str = "",
    name_zh: str = "",
    identity_verified: bool = True,
) -> Comparison:
    if not identity_verified:
        return Comparison("身份待核", "同分站、来源车系ID及款型ID未全部唯一匹配，停止值判断。")
    a, b = normalized(en), normalized(zh)
    if a in MISSING or b in MISSING:
        return Comparison(
            "资料不足", "至少一侧为空、未披露或待确认；保留原文，不将缺资料当成一致。"
        )
    units_a = re.findall(r"\[([^]]+)\]|\(([^)]+)\)", name_en)
    units_b = re.findall(r"\[([^]]+)\]|\(([^)]+)\)", name_zh)
    context_unit_a = next((x or y for x, y in units_a), "").casefold()
    context_unit_b = next((x or y for x, y in units_b), "").casefold()
    if re.fullmatch(r"[-+]?\d+(?:\.\d+)?", a) and context_unit_a != context_unit_b:
        return Comparison("资料不足", "参数名称单位不同或一侧缺失，不能只按数字判定。")
    if a == b:
        return Comparison("一致", "原文仅大小写、空白或数字千位分隔不同。")

    context = CONTEXTS.get(param_id)
    context_ok = context and (
        normalized(name_en) == normalized(context[0])
        and normalized(name_zh) == normalized(context[1])
    )
    if context_ok and any(
        (a, b) == (normalized(x), normalized(y)) for x, y in OFFICIAL_PAIRS.get(param_id, set())
    ):
        return Comparison(
            "等价表达", "同参数上下文的官方精确双语词对；数字、中英表述及全部限定条件对应。"
        )
    if (
        context_ok
        and param_id == 87
        and a == "4wd"
        and b in {"双电机四驱", "三电机四驱", "四电机四驱", "前置四驱", "中置四驱"}
    ):
        return Comparison(
            "资料不足",
            "两侧均为四驱，但英文未说明中文的电机数量或布置限定；仅凭词典不能证明完整等价或实际冲突。",
        )

    if context_ok and param_id in OFFICIAL_PAIRS:
        pairs = OFFICIAL_PAIRS[param_id]
        if a in {normalized(x) for x, _ in pairs} and b in {normalized(y) for _, y in pairs}:
            return Comparison(
                "疑似差异", "同参数的已知双语语义分别对应不同标准或结构，需人工核查。"
            )

    equipment = {
        "standard": "standard",
        "●": "standard",
        "标配": "standard",
        "optional": "optional",
        "○": "optional",
        "选配": "optional",
        "无": "absent",
        "not equipped": "absent",
        "none": "absent",
    }
    if a in equipment and b in equipment:
        if equipment[a] == equipment[b]:
            return Comparison("等价表达", "标配、选配或明确无的状态对应。")
        return Comparison("疑似差异", "标配／选配／明确无的配置状态不一致，需人工核查。")

    # Numeric equality alone never establishes equivalence: units, cycles,
    # equipment states and qualifiers remain part of the compared expression.
    quantity = r"([-+]?\d+(?:\.\d+)?)\s*([a-zA-Z%°/²³]+)?"
    qa, qb = re.fullmatch(quantity, a), re.fullmatch(quantity, b)
    if qa and qb:
        ua = qa.group(2) or ""
        ub = qb.group(2) or ""
        ua = ua or context_unit_a
        ub = ub or context_unit_b
        if ua != ub:
            return Comparison("资料不足", "数值的单位不同或一侧单位缺失；未作未经验证的单位换算。")
        if qa.group(1) != qb.group(1):
            return Comparison("疑似差异", "相同参数、相同单位的数值不同，需核实原始资料。")
        return Comparison("等价表达", "相同参数数值与单位对应。")
    return Comparison(
        "资料不足", "缺少保留全部单位、测试口径和限定条件的等价规则，暂不认定实际差异。"
    )
