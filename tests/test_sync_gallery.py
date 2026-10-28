import json

import pytest

from bitauto import sync_gallery


def test_projected_pagination_shrinks_on_response_limit_and_obeys_has_more(tmp_path, monkeypatch):
    calls = []

    def cli(command, flags):
        assert command == "+record-list"
        assert flags[flags.index("--field-id") + 1] == "json-id"
        offset = int(flags[flags.index("--offset") + 1])
        limit = int(flags[flags.index("--limit") + 1])
        calls.append((offset, limit))
        if limit == 4:
            raise RuntimeError("response body too large: 10485761 bytes (max 10485760)")
        # A short page still has more: len(chunk) must not be the completion criterion.
        row = {"record_id": f"rec{offset}", "图片URL JSON": '{"urls": ["complete-value"]}'}
        (tmp_path / flags[flags.index("--output") + 1]).write_text(json.dumps(row) + "\n")
        return {"ok": True, "has_more": offset == 0, "next_offset": offset + 1}

    monkeypatch.setattr(sync_gallery, "run_cli", cli)
    rows = sync_gallery.read_table(tmp_path, "table", "test", ["json-id"], page_size=4)
    assert calls == [(0, 4), (0, 2), (1, 2)]
    assert [r["record_id"] for r in rows] == ["rec0", "rec1"]
    assert all(json.loads(r["图片URL JSON"])["urls"] == ["complete-value"] for r in rows)


def test_pagination_does_not_retry_access_or_other_errors(tmp_path, monkeypatch):
    def cli(*_):
        raise RuntimeError("HTTP 403")

    monkeypatch.setattr(sync_gallery, "run_cli", cli)
    with pytest.raises(RuntimeError, match="403"):
        sync_gallery.read_table(tmp_path, "table", "test", ["id"])
    assert not (tmp_path / "read-size-retries.jsonl").exists()


@pytest.mark.parametrize("status,label", list(sync_gallery.STATUSES.items()))
def test_readable_status_distinguishes_collection_states(status, label):
    assert sync_gallery.readable_status({"status": status}) == label


def test_identity_failure_preserves_failed_json_but_displays_identity_review():
    result = {
        "status": "FAILED",
        "error": "Series page identity missing or mismatched",
        "categories": [{"page_url": "https://wrong.example/"}],
    }
    assert sync_gallery.readable_status(result) == "身份待核"
    assert (
        sync_gallery.gallery_entry(result, {"来源车系页": "https://existing.example/"})
        == "https://existing.example/"
    )
    assert result["status"] == "FAILED"


def test_url_cell_markdown_export_is_same_url_not_a_new_write():
    url = "https://www.bitauto.my/en/byd/seal/"
    assert sync_gallery.same_cell(sync_gallery.ENTRY_FIELD, f"[Gallery]({url})", url)
    assert not sync_gallery.same_cell(sync_gallery.ENTRY_FIELD, f"[Gallery]({url})", url + "other/")
    assert not sync_gallery.same_cell(
        sync_gallery.FIELD, "[x](https://example.com)", "https://example.com"
    )


def test_new_empty_select_cell_is_not_a_concurrent_user_change():
    assert sync_gallery.same_cell(sync_gallery.STATUS_FIELD, [], None)
    assert not sync_gallery.same_cell(sync_gallery.STATUS_FIELD, ["人工核查"], None)
    assert not sync_gallery.same_cell(sync_gallery.STATUS_FIELD, ["采集失败"], ["已采集"])
