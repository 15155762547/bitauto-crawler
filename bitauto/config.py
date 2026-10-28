"""Explicit, deployment-local settings. No production account or table defaults."""

import os
from pathlib import Path

BASE_TOKEN = os.getenv("BITAUTO_BASE_TOKEN", "")
PROFILE = os.getenv("BITAUTO_LARK_PROFILE", "")
TABLE_SERIES = os.getenv("BITAUTO_TABLE_SERIES", "")
TABLE_TRIMS = os.getenv("BITAUTO_TABLE_TRIMS", "")
TABLE_STATIONS = os.getenv("BITAUTO_TABLE_STATIONS", "")
TABLE_DIFFS = "retired-cloud-review"
DATA_DIR = (
    Path(os.getenv("BITAUTO_DATA_DIR") or str(Path(__file__).resolve().parents[1] / ".data"))
    .expanduser()
    .resolve()
)


def require_feishu():
    required = {
        "BITAUTO_BASE_TOKEN": BASE_TOKEN,
        "BITAUTO_LARK_PROFILE": PROFILE,
        "BITAUTO_TABLE_SERIES": TABLE_SERIES,
        "BITAUTO_TABLE_TRIMS": TABLE_TRIMS,
        "BITAUTO_TABLE_STATIONS": TABLE_STATIONS,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise RuntimeError("Configure your own Feishu target first: " + ", ".join(missing))
