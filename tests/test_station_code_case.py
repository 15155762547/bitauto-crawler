from bitauto.uppercase_station_codes import normalize_payload, uppercase_value


def test_uppercase_only_station_prefix():
    assert uppercase_value("ae_1021") == "AE_1021"
    assert uppercase_value("global_modelAbc") == "GLOBAL_modelAbc"
    assert uppercase_value(["ae"]) == ["AE"]
    assert uppercase_value(None) is None


def test_write_boundary_keeps_other_data_and_is_idempotent():
    original = {
        "update_records": {
            "recAbc": {
                "款型唯一键": "ae_1021",
                "分站代码": "ae",
                "官网入口": "https://example/ae/",
                "车型全称": "smart #1",
            }
        }
    }
    result = normalize_payload(original)
    assert result["update_records"]["recAbc"] == {
        "款型唯一键": "AE_1021",
        "分站代码": "AE",
        "官网入口": "https://example/ae/",
        "车型全称": "smart #1",
    }
    assert normalize_payload(result) == result
    assert original["update_records"]["recAbc"]["分站代码"] == "ae"


def test_pipeline_recognizes_existing_uppercase_keys():
    from bitauto.pipeline import BitautoPipeline

    class Manager:
        def search_records(self, table_id):
            assert table_id != "tblExample025"
            return [{"车系唯一键": "AE_10", "款型唯一键": "SG_20"}]

        def local_diff_records(self):
            return [{"差异唯一键": "MY_30_40"}]

    pipeline = BitautoPipeline.__new__(BitautoPipeline)
    pipeline.sync_mgr = Manager()
    assert pipeline.load_existing_keys() == {
        "series": {"ae_10"},
        "trims": {"sg_20"},
        "diffs": {"my_30_40"},
    }


def test_sync_manager_writes_uppercase(monkeypatch):
    import json

    from bitauto.feishu_sync import FeishuSyncManager

    manager = FeishuSyncManager()
    captured = []

    def fake_cli(command, args):
        if command == "+table-get":
            return {"data": {"fields": []}}
        captured.append(json.loads(args[args.index("--json") + 1]))
        return {"ok": True, "data": {"record_id_list": ["rec1"]}}

    monkeypatch.setattr(manager, "_run_cli", fake_cli)
    assert manager.batch_create_records(
        "tblTest", [{"分站代码": "ae", "款型唯一键": "ae_1021"}]
    ) == ["rec1"]
    assert captured == [{"create_records": [{"分站代码": "AE", "款型唯一键": "AE_1021"}]}]
