"""Synthetic settings for offline tests only; never production credentials."""

import os

for name, value in {
    "BITAUTO_BASE_TOKEN": "test-base-token",
    "BITAUTO_LARK_PROFILE": "test-profile",
    "BITAUTO_TABLE_SERIES": "tblExampleSeries",
    "BITAUTO_TABLE_TRIMS": "tblExampleTrims",
    "BITAUTO_TABLE_STATIONS": "tblExampleStations",
    "BITAUTO_SIGNING_SECRET": "test-signing-secret",  # pragma: allowlist secret (synthetic fixture)
}.items():
    os.environ[name] = value
