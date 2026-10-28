import json
from types import SimpleNamespace

import pytest

from bitauto.diff_review import build_review_plan, diff_update_patch
from bitauto.diff_rules import compare_values, source_fingerprint
from bitauto.feishu_sync import FeishuSyncManager
from bitauto.merge import merge_trim_pair
from bitauto.models.entities import (
    CarInfoItem,
    ParamDetailData,
    ParamGroup,
    ParamItem,
    ParamValueItem,
)
from bitauto.models.station import get_station
from bitauto.pipeline import BitautoPipeline


@pytest.mark.parametrize(
    "pid,en,zh,name_en,name_zh",
    [
        (
            90,
            "5-Link Independent Suspension",
            "五连杆式独立悬架",
            "Rear Suspension Type",
            "后悬架类型",
        ),
        (
            89,
            "5-Link Independent Suspension",
            "五连杆式独立悬架",
            "Front Suspension Type",
            "前悬架类型",
        ),
        (49, "China 6", "中国第六阶段", "Emission Standard", "环保标准"),
        (49, "Euro 6", "欧洲第六阶段", "Emission Standard", "环保标准"),
        (
            90,
            "5-link Coil Spring Non-independent Suspension",
            "五连杆螺旋弹簧非独立悬架",
            "Rear Suspension Type",
            "后悬架类型",
        ),
    ],
)
def test_official_exact_pairs_respect_parameter_context(pid, en, zh, name_en, name_zh):
    assert (
        compare_values(en, zh, param_id=pid, name_en=name_en, name_zh=name_zh).result == "等价表达"
    )
    assert (
        compare_values(en, zh, param_id=999, name_en=name_en, name_zh=name_zh).result == "资料不足"
    )


@pytest.mark.parametrize("zh", ["双电机四驱", "三电机四驱", "四电机四驱", "前置四驱", "中置四驱"])
def test_dictionary_cannot_remove_drivetrain_qualifiers(zh):
    result = compare_values("4WD", zh, param_id=87, name_en="Drivetrain", name_zh="驱动形式")
    assert result.result == "资料不足"
    assert "限定" in result.reason


def test_units_equipment_states_missing_and_identity_are_not_silently_equal():
    assert compare_values("100 kW", "100 PS").result == "资料不足"
    assert (
        compare_values("100", "100", name_en="Power[kW]", name_zh="功率[PS]").result == "资料不足"
    )
    assert compare_values("100", "100", identity_verified=False).result == "身份待核"
    assert (
        compare_values("570", "550", name_en="Range[km]", name_zh="续航[km]").result == "疑似差异"
    )
    assert (
        compare_values("100", "101", name_en="Power[kW]", name_zh="功率[PS]").result == "资料不足"
    )
    assert compare_values("Standard", "选配").result == "疑似差异"
    assert compare_values("Standard", "None").result == "疑似差异"
    assert compare_values("●", "标配").result == "等价表达"
    assert compare_values("TBC", "TBC").result == "资料不足"
    assert compare_values(None, "100").result == "资料不足"
    assert compare_values("4800", "4,800").result == "一致"
    assert (
        compare_values(
            "5-Link Independent Suspension (Optional)",
            "五连杆式独立悬架",
            param_id=90,
            name_en="Rear Suspension Type",
            name_zh="后悬架类型",
        ).result
        == "资料不足"
    )


def test_update_preserves_original_values_human_decision_and_changed_source_history():
    old = {
        "差异唯一键": "HK_37_90",
        "英文原值": "original",
        "中文原值": "原值",
        "源值指纹": "old",
        "人工备注": "检查过",
        "人工结论": "接受",
        "处理状态": ["已人工处理"],
        "判断说明": "旧依据",
        "规则版本": "old-rule",
    }
    new = {
        "差异唯一键": "HK_37_90",
        "英文原值": "changed",
        "源值指纹": "new",
        "判断说明": "新依据",
        "处理状态": ["已排除"],
        "人工结论": "自动覆盖",
    }
    patch = diff_update_patch(old, new)
    assert "英文原值" not in patch and "人工结论" not in patch
    assert patch["处理状态"] == ["待复核"]
    assert "旧依据" in patch["历史处理依据"] and "接受" in patch["历史处理依据"]
    new["源值指纹"] = "old"
    assert "处理状态" not in diff_update_patch(old, new)
    assert source_fingerprint("HK_1_37", 90, "x", "y") != source_fingerprint(
        "MY_1_37", 90, "x", "y"
    )


def test_snapshot_plan_verifies_series_identity_and_keeps_source_timestamp_unknown(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    for lang, value in (("en", "5-Link Independent Suspension"), ("zh", "五连杆式独立悬架")):
        data = {
            "data": {
                "carList": [{"carId": 37, "serialId": 1003}],
                "paramList": [
                    {
                        "name": "Chassis",
                        "items": [
                            {
                                "id": 90,
                                "name": "Rear Suspension Type" if lang == "en" else "后悬架类型",
                                "paramValues": [{"id": 37, "value": value}],
                            }
                        ],
                    }
                ],
            }
        }
        (raw / f"params_hk_seal_{lang}.json").write_text(json.dumps(data))
    rows = [
        {
            "record_id": "diff",
            "差异唯一键": "HK_37_90",
            "分站代码": ["HK"],
            "款型ID": 37,
            "参数ID": 90,
            "英文原值": "5-Link Independent Suspension",
            "中文原值": "五连杆式独立悬架",
            "参数名称(EN)": "Rear Suspension Type",
            "参数名称(ZH)": "后悬架类型",
        }
    ]
    trims = [
        {
            "record_id": "trim",
            "款型唯一键": "HK_37",
            "分站代码": "HK",
            "来源款型ID": 37,
            "来源车系ID": 1003,
            "车型全称": "BYD Seal Dynamic",
        }
    ]
    result = build_review_plan(rows, trims, [], raw, reviewed_at="2026-10-26")[0]
    assert result["identity_verified"] and result["original_values_match_snapshot"]
    assert result["fields"]["判断结果"] == ["等价表达"]
    assert result["fields"]["关联款型"] == [{"id": "trim"}]
    assert json.loads(result["fields"]["来源快照证据"])["en"]["captured_at"] is None
    trims[0]["来源车系ID"] = 999
    assert build_review_plan(rows, trims, [], raw)[0]["fields"]["判断结果"] == ["身份待核"]


def test_stable_key_upsert_excludes_old_alert_without_changing_original_evidence(
    tmp_path, monkeypatch
):
    manager = FeishuSyncManager(local_review_dir=tmp_path)
    manager.local_reviews.save(
        [{"record_id": "old", "差异唯一键": "HK_37_90", "英文原值": "old value", "处理状态": []}]
    )
    monkeypatch.setattr(manager, "_run_cli", lambda *a, **kw: pytest.fail("Cloud call forbidden"))
    new = {
        "差异唯一键": "HK_37_90",
        "英文原值": "current value",
        "处理状态": ["已排除"],
        "判断结果": ["一致"],
        "源值指纹": "current",
    }
    assert manager.upsert_diff_records([new]) == {"created": 0, "updated": 1}
    assert manager.local_diff_records()[0]["英文原值"] == "old value"
    assert manager.local_diff_records()[0]["处理状态"] == ["已排除"]
    assert manager.upsert_diff_records([new]) == {"created": 0, "updated": 0}


def test_pipeline_revisits_old_alert_even_when_new_values_agree(tmp_path):
    car = CarInfoItem(
        carId=37, serialId=1003, carName="Dynamic", masterName="BYD", serialName="Seal"
    )
    data = ParamDetailData(
        carList=[car],
        paramList=[
            ParamGroup(
                name="Chassis",
                items=[
                    ParamItem(
                        id=90,
                        name="Rear Suspension Type",
                        paramValues=[ParamValueItem(id=37, value="same")],
                    )
                ],
            )
        ],
    )
    brand = SimpleNamespace(master_id=1, master_name="BYD")
    series = SimpleNamespace(
        serial_id=1003,
        master_id=1,
        is_new_energy=1,
        sale_status=1,
        url_spell="seal",
        serial_name="Seal",
        brand_url_spell="byd",
        master_name="BYD",
    )
    client = SimpleNamespace(
        get_master_brand_list=lambda *a, **kw: [brand],
        get_serial_list=lambda *a, **kw: [series],
        get_param_detail=lambda *a, **kw: data,
    )
    updated = []
    pipeline = BitautoPipeline.__new__(BitautoPipeline)
    pipeline.client = client
    pipeline.raw_dir = tmp_path
    pipeline.allowed_sale_statuses = {1}
    pipeline.sync_mgr = SimpleNamespace(
        upsert_diff_records=lambda rows: (
            updated.extend(rows) or {"created": 0, "updated": len(rows)}
        )
    )
    stats = pipeline.run_station(
        "hk", {"series": {"hk_1003"}, "trims": {"hk_37"}, "diffs": {"hk_37_90"}}
    )
    assert stats["diffs_updated"] == 1
    assert updated[0]["差异唯一键"] == "HK_37_90"
    assert updated[0]["处理状态"] == ["已排除"]


def test_tbc_is_reviewed_without_changing_existing_merge_selection():
    car = CarInfoItem(carId=37, serialId=1003)
    parameters = {1: {"param_name": "Pending parameter", "value": "TBC"}}
    trim = merge_trim_pair(get_station("hk"), "seal", car, car, parameters, parameters)
    assert trim.parameters[1]["value"] == ""  # Existing selection contract stays intact.
    assert trim.parameters[1]["raw_val_en"] == "TBC"
    assert trim.diffs[0].review_result == "资料不足"
