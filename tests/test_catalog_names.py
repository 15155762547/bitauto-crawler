import json

from bitauto.enrich_names_and_covers import canonicalize_names


def test_chinese_alias_uses_explicit_official_china_identity(tmp_path):
    (tmp_path / "brands_global_zh.json").write_text(
        json.dumps({"data": [{"urlSpell": "byd", "masterName": "比亚迪"}]})
    )
    identity = {"brandUrlSpell": "byd", "cnSerialId": 7669, "cnUrlSpell": "oceanx"}
    (tmp_path / "serials_global_1_zh.json").write_text(
        json.dumps({"data": [dict(identity, serialName="海豹")]})
    )
    for code in ["my", "hk"]:
        (tmp_path / f"serials_{code}_1_en.json").write_text(
            json.dumps({"data": [dict(identity, serialId=1032)]})
        )
    names = {
        "MY_1032": {"brand": "BYD", "series": "Seal"},
        "HK_1032": {"brand": "比亞迪", "series": "Seal"},
    }
    proof = canonicalize_names(names, tmp_path)
    assert names["MY_1032"] == {"brand": "比亚迪", "series": "海豹"}
    assert names["HK_1032"] == {"brand": "比亞迪", "series": "Seal"}
    assert any(p.get("official_china_reference") == ("byd", 7669, "oceanx") for p in proof)


def test_different_china_slug_does_not_borrow_same_number_alias(tmp_path):
    (tmp_path / "serials_global_1_zh.json").write_text(
        json.dumps(
            {
                "data": [
                    {
                        "brandUrlSpell": "byd",
                        "cnSerialId": 7669,
                        "cnUrlSpell": "different",
                        "serialName": "海豹",
                    }
                ]
            }
        )
    )
    (tmp_path / "serials_my_1_en.json").write_text(
        json.dumps(
            {
                "data": [
                    {
                        "brandUrlSpell": "byd",
                        "cnSerialId": 7669,
                        "cnUrlSpell": "oceanx",
                        "serialId": 1032,
                    }
                ]
            }
        )
    )
    names = {"MY_1032": {"brand": "BYD", "series": "Seal"}}
    assert canonicalize_names(names, tmp_path) == []
    assert names["MY_1032"]["series"] == "Seal"
