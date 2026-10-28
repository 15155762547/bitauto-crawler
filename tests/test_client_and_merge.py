import hashlib
import json

import pytest

from bitauto.client import BitAutoClient
from bitauto.merge import (
    clean_val,
    merge_station_series,
    normalize_numeric_str,
)
from bitauto.models.entities import (
    CarInfoItem,
    ParamDetailData,
    ParamGroup,
    ParamItem,
    ParamValueItem,
)
from bitauto.models.station import get_all_stations, get_station


def test_station_definitions():
    stations = get_all_stations()
    assert len(stations) == 9

    global_st = get_station("global")
    assert global_st.host == "global-api.bitauto.com"
    assert global_st.locale_en == "global"
    assert global_st.locale_zh == "zh-global"
    assert global_st.country_id == 117

    hk = get_station("hk")
    assert hk.host == "global-api.bitauto.hk"
    assert hk.locale_en == "en-hk"
    assert hk.locale_zh == "zh-hk"
    assert hk.country_id == 118

    ae = get_station("ae")
    assert ae.host == "global-api.bitauto.com"
    assert ae.locale_en == "en-ae"
    assert ae.country_id == 102

    with pytest.raises(KeyError):
        get_station("unknown_country")


def test_client_signing():
    client = BitAutoClient()
    params = {"serialId": "", "carId": "1062", "serialUrlSpell": "seal"}
    headers = client._make_headers(params, "en-ae")

    assert headers["x-platform"] == "pc"
    assert headers["cid"] == "508"
    assert headers["n-lang"] == "en-ae"
    assert "x-timestamp" in headers
    assert "x-sign" in headers

    # Verify signature computation manually
    ts = headers["x-timestamp"]
    expected_sign = hashlib.md5(
        f"cid=508&param={json.dumps(params)}test-signing-secret{ts}".encode()
    ).hexdigest()
    assert headers["x-sign"] == expected_sign


def test_clean_val():
    assert clean_val(None) == ""
    assert clean_val("-") == ""
    assert clean_val("TBC") == ""
    assert clean_val(" 4800 ") == "4800"


def test_normalize_numeric_str():
    assert normalize_numeric_str("4,800") == "4800"
    assert normalize_numeric_str(" 1875 ") == "1875"


def test_merge_station_series_complement_and_diff():
    station = get_station("ae")

    # Construct mock data for EN and ZH
    car_en = CarInfoItem(
        carId=1062,
        carName="570km RWD Design",
        year=2025,
        serialId=1161,
        serialName="Seal",
        referPriceWithUnit="AED 149,900",
        imgUrl="https://example.com/en.png",
    )
    car_zh = CarInfoItem(
        carId=1062,
        carName="570km 后驱设计版",
        year=2025,
        serialId=1161,
        serialName="海豹",
        referPriceWithUnit="AED 149,900",
        imgUrl="https://example.com/zh.png",
    )

    # Param 1: Battery capacity - present in EN, missing in ZH
    # Param 2: Warranty - missing in EN, present in ZH
    # Param 3: Power - present in both, identical
    # Param 4: Range - present in both, different (diff detection)
    group_en = ParamGroup(
        name="Specs",
        items=[
            ParamItem(
                id=1,
                name="Battery Capacity[kWh]",
                paramValues=[ParamValueItem(id=1062, value="82.5")],
            ),
            ParamItem(
                id=3, name="Total Power[kW]", paramValues=[ParamValueItem(id=1062, value="230")]
            ),
            ParamItem(id=4, name="Range[km]", paramValues=[ParamValueItem(id=1062, value="570")]),
        ],
    )
    group_zh = ParamGroup(
        name="规格参数",
        items=[
            ParamItem(
                id=2, name="整车质保", paramValues=[ParamValueItem(id=1062, value="6年或15万公里")]
            ),
            ParamItem(
                id=3, name="驱动电机总功率[kW]", paramValues=[ParamValueItem(id=1062, value="230")]
            ),
            ParamItem(
                id=4, name="纯电续航[km]", paramValues=[ParamValueItem(id=1062, value="550")]
            ),
        ],
    )

    data_en = ParamDetailData(carList=[car_en], paramList=[group_en])
    data_zh = ParamDetailData(carList=[car_zh], paramList=[group_zh])

    merged_trims, all_diffs = merge_station_series(
        station=station,
        serial_url_spell="seal",
        data_en=data_en,
        data_zh=data_zh,
        crawl_time="2026-10-22T00:00:00Z",
    )

    assert len(merged_trims) == 1
    t = merged_trims[0]

    assert t.stable_key == "ae_1062"
    assert t.car_name_en == "570km RWD Design"
    assert t.car_name_zh == "570km 后驱设计版"
    assert t.serial_name_en == "Seal"
    assert t.serial_name_zh == "海豹"

    # Param 1: complemented from EN
    assert t.parameters[1]["value"] == "82.5"
    assert t.parameters[1]["source_lang"] == "en"

    # Param 2: complemented from ZH
    assert t.parameters[2]["value"] == "6年或15万公里"
    assert t.parameters[2]["source_lang"] == "zh"

    # Param 3: identical in both
    assert t.parameters[3]["value"] == "230"
    assert t.parameters[3]["source_lang"] == "both"

    # Param 4: range differed (570 vs 550) -> registered in diffs
    assert len(all_diffs) == 1
    d = all_diffs[0]
    assert d.car_id == 1062
    assert d.param_id == 4
    assert d.val_en == "570"
    assert d.val_zh == "550"
