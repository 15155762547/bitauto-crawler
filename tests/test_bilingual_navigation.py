import json

import pytest

from bitauto.bilingual_navigation import official_labels, records
from bitauto.category_names import category_table_map


@pytest.mark.parametrize("name", ["Body", "车身 Body"])
def test_renamed_table_keeps_legacy_and_bilingual_aliases(name):
    result = category_table_map([{"id": "tblBody", "name": name}])
    assert result["Body"] == result["车身 Body"] == "tblBody"


@pytest.mark.parametrize("name", ["Battery／Recharge", "电池／补能 Battery／Recharge"])
def test_slash_category_resolves_source_and_display_names(name):
    result = category_table_map([{"id": "tblBattery", "name": name}])
    assert result["Battery/Recharge"] == result["Battery／Recharge"] == "tblBattery"
    assert result["电池／补能 Battery／Recharge"] == "tblBattery"


def test_duplicate_aliases_do_not_silently_choose_a_table():
    with pytest.raises(ValueError, match="Ambiguous"):
        category_table_map(
            [{"id": "tblOld", "name": "Body"}, {"id": "tblNew", "name": "车身 Body"}]
        )


def test_descriptions_require_same_source_identity(tmp_path):
    def response(car, name):
        return {
            "data": {
                "carList": [{"carId": car, "serialId": 1000}],
                "paramList": [{"name": "Body", "items": [{"id": 17, "name": name}]}],
            }
        }

    (tmp_path / "params_ae_car_en.json").write_text(json.dumps(response(1, "Wheelbase[mm]")))
    target = tmp_path / "params_ae_car_zh.json"
    target.write_text(json.dumps(response(2, "轴距[mm]")))
    assert not official_labels(tmp_path)[0]
    target.write_text(json.dumps(response(1, "轴距[mm]")))
    candidates, evidence = official_labels(tmp_path)
    assert candidates[("Body", "Wheelbase(mm)")] == {"轴距[mm]"}
    assert evidence[(("Body", "Wheelbase(mm)"), "轴距[mm]")]["parameter_id"] == 17


def test_existing_bilingual_full_table_is_not_recreated_or_reinserted(monkeypatch):
    from bitauto import build_category_sheets as builder

    monkeypatch.setattr(builder, "CATEGORIES_ORDER", [("Body", "车身 Body")])
    monkeypatch.setattr(
        builder,
        "load_all_car_params",
        lambda _: ({"AE_1": {}}, {"Body": {}}, {"AE_1": {"Body": {}}}),
    )
    commands = []

    def cli(command, args):
        commands.append(command)
        assert command == "+table-list"
        return {"data": {"tables": [{"id": "tblBody", "name": "车身 Body", "records_count": 1}]}}

    monkeypatch.setattr(builder, "run_cli", cli)
    builder.build_and_sync_all_categories("unused")
    assert commands == ["+table-list", "+table-list"]


def test_repeated_verification_preserves_previous_export(tmp_path):
    class Release:
        audit = tmp_path

        def cli(self, command, args):
            assert command == "+record-list"
            output = tmp_path / args[args.index("--output") + 1]
            assert not output.exists()
            output.write_text(json.dumps({"record_id": "rec1", "Wheelbase(mm)": "2920"}))
            return {"has_more": False}

    first = records(Release(), "tblBody", ["fldWheelbase"], "after")
    assert records(Release(), "tblBody", ["fldWheelbase"], "after") == first
    assert len(list(tmp_path.glob("*.ndjson"))) == 2
