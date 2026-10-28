from bitauto.complete_snapshot_data import fill_patch, resolve


def test_fill_preserves_existing_values_and_explicit_absence():
    fields = {name: {"type": "text"} for name in ("blank", "zero", "dash", "existing")}
    row = {"blank": "", "zero": 0, "dash": "-", "existing": "old"}
    assert fill_patch(row, fields, dict.fromkeys(fields, "new")) == {"blank": "new"}
    assert fill_patch({}, fields, {"blank": "-"}) == {}
    assert fill_patch({}, fields, {"blank": "TBC"}) == {"blank": "TBC"}


def test_fill_select_only_accepts_current_official_options():
    fields = {"Drive": {"type": "select", "options": [{"name": "RWD"}]}}
    assert fill_patch({}, fields, {"Drive": "RWD"}) == {"Drive": ["RWD"]}
    assert fill_patch({}, fields, {"Drive": "4WD"}) == {}


def test_identity_never_borrows_other_country_or_series():
    row = {"款型唯一键": "AE_1", "分站代码": ["AE"], "来源款型ID": 1, "来源车系ID": 10}
    entry = {"car": {"carId": 1, "serialId": 10}}
    assert resolve(row, {("HK", 1, "en"): [entry]}) == (None, None)
    assert resolve(row, {("AE", 1, "en"): [{"car": {"serialId": 11}}]}) == (None, None)
    assert resolve(row, {("AE", 1, "en"): [entry, entry]}) == (None, None)
    assert resolve(row, {("AE", 1, "en"): [entry]}) == (entry, None)
    assert resolve({**row, "款型唯一键": "HK_1"}, {("AE", 1, "en"): [entry]}) == (None, None)


def test_missing_and_translation_uncertainty_are_not_the_same():
    from bitauto.complete_snapshot_data import summarize

    def issue(a, b, reason="缺少等价规则"):
        return {
            "kind": "资料不足",
            "en": {"value": a},
            "zh": {"value": b},
            "parameter": "Drivetrain",
            "reason": reason,
        }

    assert summarize([issue("TBC", "TBC")])[0] == "源站待公布"
    assert summarize([issue("-", "-")])[0] == "双侧未披露"
    assert summarize([issue("500", "-")])[0] == "单边资料缺失"
    assert summarize([issue("Single", "单电机")])[0] == "双语释义待核"
    assert summarize([issue("5", "5", "参数名称单位不同或一侧缺失")])[0] == "单位口径待核"


def test_review_columns_are_hidden_without_removing_business_fields():
    from bitauto.complete_snapshot_data import status_columns

    old = ["车型全称", "核查状态", "问题说明", "分站名称", "轴距(mm)"]
    expected = ["车型全称", "分站名称", "轴距(mm)"]
    assert status_columns(old) == expected
    assert status_columns(expected) == expected
