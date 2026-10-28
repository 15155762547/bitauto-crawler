from bitauto.sync_global_to_feishu import global_price_fields


def test_preserves_tbc_in_each_source_language():
    assert global_price_fields({"referPriceWithUnit": "TBC"}, {"referPriceWithUnit": "TBC"}) == {
        "指导价(EN)": "TBC",
        "指导价(ZH)": "TBC",
    }


def test_preserves_real_prices_and_explicit_dash():
    assert global_price_fields({"referPriceWithUnit": "$ 25,000"}, {"referPriceWithUnit": "-"}) == {
        "指导价(EN)": "$ 25,000",
        "指导价(ZH)": "-",
    }


def test_does_not_invent_price_for_missing_language():
    assert global_price_fields({"referPriceWithUnit": "TBC"}, {}) == {
        "指导价(EN)": "TBC",
        "指导价(ZH)": "",
    }
