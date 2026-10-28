from bitauto.param_values import render_param_value


def test_nested_option_value_without_name_is_preserved():
    assert (
        render_param_value({"value": "", "subList": [{"name": None, "value": "●Full-speed ACC"}]})
        == "●Full-speed ACC"
    )


def test_preserves_multiple_modes_and_optional_status():
    assert (
        render_param_value({"subList": [{"value": "●Sport"}, {"value": "○Snow"}]})
        == "●Sport；○Snow"
    )


def test_colors_keep_human_names():
    assert (
        render_param_value(
            {"subList": [{"name": "White", "value": "#fff"}, {"name": "Black", "value": "#000"}]}
        )
        == "White, Black"
    )


def test_missing_and_explicit_none_stay_distinct():
    assert render_param_value({}) == ""
    assert render_param_value({"value": "-"}) == "-"
    assert render_param_value({"value": "TBC"}) == "TBC"


def test_new_details_extend_select_without_losing_existing_options(monkeypatch):
    import json

    from bitauto.param_values import ensure_select_options

    calls = []
    existing = {
        "id": "fld1",
        "name": "Seatbelt Reminder",
        "type": "select",
        "multiple": False,
        "options": [{"name": "Standard", "hue": "Blue", "lightness": "Lighter"}],
    }

    def cli(command, args):
        if command == "+table-get":
            return {"data": {"fields": [existing]}}
        calls.append(json.loads(args[args.index("--json") + 1]))
        return {"ok": True}

    monkeypatch.setattr("time.sleep", lambda _: None)
    ensure_select_options(cli, "tbl1", [{"Seatbelt Reminder": "●All Seats"}])
    assert calls[0]["options"][0] == existing["options"][0]
    assert calls[0]["options"][1]["name"] == "●All Seats"
    assert calls[0]["multiple"] is False
    calls.clear()
    ensure_select_options(cli, "tbl1", [{"Seatbelt Reminder": "Standard"}])
    assert calls == []


def test_category_builder_and_pipeline_extract_nested_details(tmp_path):
    import json

    from bitauto.build_category_sheets import load_all_car_params
    from bitauto.merge import extract_car_param_map
    from bitauto.models.entities import ParamDetailData

    data = {
        "carList": [{"carId": 1}],
        "paramList": [
            {
                "name": "Active Safety",
                "items": [
                    {
                        "id": 210,
                        "name": "Seatbelt Reminder",
                        "paramValues": [
                            {
                                "id": 1,
                                "value": "",
                                "subList": [{"name": None, "value": "●All Seats"}],
                            }
                        ],
                    }
                ],
            }
        ],
    }
    (tmp_path / "params_my_sample_en.json").write_text(json.dumps({"data": data}))
    _, _, values = load_all_car_params(str(tmp_path))
    assert values["my_1"]["Active Safety"]["Seatbelt Reminder"] == "●All Seats"
    params = extract_car_param_map(ParamDetailData.model_validate(data), 1)
    assert params[210]["value"] == "●All Seats"
