import subprocess
import sys
from pathlib import Path

import pytest

from bitauto import config
from bitauto.client import BitAutoApiError, BitAutoClient
from bitauto.feishu_sync import FeishuSyncManager


def test_missing_configuration_stops_before_cli(monkeypatch):
    monkeypatch.setattr(config, "BASE_TOKEN", "")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("CLI must not run"))
    with pytest.raises(RuntimeError, match="BITAUTO_BASE_TOKEN"):
        FeishuSyncManager()._run_cli("+table-list", [])


def test_missing_signing_material_stops_header_generation(monkeypatch):
    monkeypatch.setattr(BitAutoClient, "SECRET", "")
    with pytest.raises(BitAutoApiError, match="BITAUTO_SIGNING_SECRET"):
        BitAutoClient()._make_headers({}, "en-ae")


def test_repository_does_not_provide_default_deployment_credentials(monkeypatch):
    import os

    env = {k: v for k, v in os.environ.items() if not k.startswith("BITAUTO_")}
    code = "from bitauto import config; assert not config.BASE_TOKEN and not config.PROFILE and not config.TABLE_SERIES and not config.TABLE_TRIMS; config.require_feishu()"
    result = subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True, text=True, check=False
    )
    assert result.returncode != 0
    assert "Configure your own Feishu target first" in result.stderr


def test_data_default_is_repository_local_not_a_production_path():
    assert config.DATA_DIR == Path(config.__file__).resolve().parents[1] / ".data"
