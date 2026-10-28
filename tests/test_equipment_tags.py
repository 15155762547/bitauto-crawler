from bitauto.standardize_equipment_tags import (
    SELECT_OPTIONS,
    VALUE_MAP,
    get_target_fields,
)


def test_select_options_and_value_map():
    assert len(SELECT_OPTIONS) == 3
    option_names = [o["name"] for o in SELECT_OPTIONS]
    assert "Standard" in option_names
    assert "Optional" in option_names
    assert "None" in option_names

    # Check key mappings
    assert VALUE_MAP["●"] == "Standard"
    assert VALUE_MAP["•"] == "Standard"
    assert VALUE_MAP["○"] == "Optional"
    assert VALUE_MAP["-"] == "None"
    assert VALUE_MAP["标配"] == "Standard"
    assert VALUE_MAP["选配"] == "Optional"
    assert VALUE_MAP["无"] == "None"


def test_get_target_fields():
    cat_schemas = {
        "Active Safety": {
            "ABS": 1,
            "Seatbelt Reminder": 2,
            "Numeric Field": 3,
        }
    }
    car_values = {
        "car1": {
            "Active Safety": {
                "ABS": "●",
                "Seatbelt Reminder": "-",
                "Numeric Field": "120",
            }
        },
        "car2": {
            "Active Safety": {
                "ABS": "-",
                "Seatbelt Reminder": "-",
                "Numeric Field": "150",
            }
        },
    }

    target = get_target_fields("Active Safety", cat_schemas, car_values)
    assert "ABS" in target
    assert "Seatbelt Reminder" in target
    assert "Numeric Field" not in target
