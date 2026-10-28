import csv

import pytest

from bitauto.feishu_sync import TABLE_DIFFS, FeishuSyncManager
from bitauto.local_diff_review import LocalReviewStore
from bitauto.sync_diff_review import main


def test_manual_csv_notes_and_prior_decision_survive_changed_source(tmp_path):
    store = LocalReviewStore(tmp_path)
    original = {
        "record_id": "old-cloud-id",
        "差异唯一键": "HK_37_90",
        "英文原值": "original",
        "中文原值": "原文",
        "人工备注": None,
        "人工结论": None,
        "处理状态": ["已排除"],
        "判断结果": ["等价表达"],
        "源值指纹": "old",
        "判断说明": "旧依据",
    }
    store.save([original])
    with store.manual_file.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    rows[0].update({"人工备注": "检查过原厂资料", "人工结论": "接受", "处理状态": "已人工处理"})
    with store.manual_file.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    store.upsert(
        [
            {
                "差异唯一键": "HK_37_90",
                "源值指纹": "new",
                "英文原值": "new source",
                "判断结果": ["资料不足"],
                "判断说明": "新依据",
                "处理状态": ["待核查"],
            }
        ]
    )
    saved = store.read()[0]
    assert saved["record_id"] == "old-cloud-id"
    assert saved["英文原值"] == "original" and saved["中文原值"] == "原文"
    assert saved["人工备注"] == "检查过原厂资料" and saved["人工结论"] == "接受"
    assert saved["处理状态"] == ["待复核"]
    assert "旧依据" in saved["历史处理依据"] and "已人工处理" in saved["历史处理依据"]


def test_retired_cloud_table_cannot_be_accessed_or_recreated(monkeypatch):
    manager = FeishuSyncManager()
    monkeypatch.setattr("subprocess.run", lambda *a, **kw: pytest.fail("Cloud call forbidden"))
    with pytest.raises(RuntimeError, match="已迁出"):
        manager.search_records(TABLE_DIFFS)
    with pytest.raises(RuntimeError, match="已迁出"):
        manager.batch_update_records(TABLE_DIFFS, {"old": {"人工备注": "x"}})
    with pytest.raises(RuntimeError, match="已迁出"):
        manager.batch_create_records(TABLE_DIFFS, [{"差异唯一键": "HK_1_90"}])


def test_old_apply_entry_stops_before_any_network_or_file_mutation(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["sync_diff_review", "--audit-dir", "old-audit", "--apply"])
    monkeypatch.setattr("subprocess.run", lambda *a, **kw: pytest.fail("Cloud call forbidden"))
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    assert "不能访问或重建飞书表" in capsys.readouterr().err
