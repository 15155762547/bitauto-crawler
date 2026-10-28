import json
import logging
import subprocess
from pathlib import Path
from typing import Any

from bitauto.config import BASE_TOKEN as CONFIG_BASE_TOKEN
from bitauto.config import PROFILE, require_feishu
from bitauto.config import TABLE_DIFFS as CONFIG_TABLE_DIFFS
from bitauto.config import TABLE_SERIES as CONFIG_TABLE_SERIES
from bitauto.config import TABLE_STATIONS as CONFIG_TABLE_STATIONS
from bitauto.config import TABLE_TRIMS as CONFIG_TABLE_TRIMS
from bitauto.param_values import ensure_select_options

logger = logging.getLogger(__name__)

BASE_TOKEN = CONFIG_BASE_TOKEN

TABLE_STATIONS = CONFIG_TABLE_STATIONS
TABLE_SERIES = CONFIG_TABLE_SERIES
TABLE_TRIMS = CONFIG_TABLE_TRIMS
TABLE_DIFFS = CONFIG_TABLE_DIFFS


class FeishuSyncManager:
    def __init__(
        self,
        base_token: str = BASE_TOKEN,
        profile: str = PROFILE,
        local_review_dir: Path | None = None,
    ):
        self.base_token = base_token
        self.profile = profile
        from bitauto.local_diff_review import DEFAULT_REVIEW_DIR, LocalReviewStore

        self.local_reviews = LocalReviewStore(local_review_dir or DEFAULT_REVIEW_DIR)

    def _run_cli(self, subcmd: str, args: list[str]) -> dict[str, Any]:
        require_feishu()
        if any(value in {TABLE_DIFFS, "参数核查清单", "中英参数差异"} for value in args):
            raise RuntimeError("参数核查清单已迁出飞书；此表禁止访问或重建，请使用外盘本地清单。")
        cmd = [
            "lark-cli",
            "--profile",
            self.profile,
            "base",
            subcmd,
            "--base-token",
            self.base_token,
        ] + args
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if res.returncode != 0:
            raise RuntimeError(f"lark-cli error ({res.returncode}): {res.stderr or res.stdout}")
        try:
            return json.loads(res.stdout)
        except Exception as e:
            raise RuntimeError(f"Failed to parse lark-cli JSON response: {res.stdout}") from e

    def batch_create_records(self, table_id: str, records: list[dict[str, Any]]) -> list[str]:
        if not records:
            return []
        from bitauto.uppercase_station_codes import normalize_payload

        records = normalize_payload(records)
        ensure_select_options(self._run_cli, table_id, records)
        created_ids = []
        # Chunk into batches of max 100
        for i in range(0, len(records), 100):
            batch = records[i : i + 100]
            payload = {"create_records": normalize_payload(batch)}
            resp = self._run_cli(
                "+record-batch-create", ["--table-id", table_id, "--json", json.dumps(payload)]
            )
            if not resp.get("ok"):
                raise RuntimeError(f"Failed batch create on {table_id}: {resp}")
            created_ids.extend(resp.get("data", {}).get("record_id_list", []))
        return created_ids

    def search_records(self, table_id: str) -> list[dict[str, Any]]:
        records = []
        offset = 0
        while True:
            resp = self._run_cli(
                "+record-list", ["--table-id", table_id, "--offset", str(offset), "--limit", "500"]
            )
            if "record_file" in resp:
                records.extend(
                    json.loads(line)
                    for line in Path(resp["record_file"]).read_text().splitlines()
                    if line.strip()
                )
            elif resp.get("ok"):
                records.extend(resp.get("data", {}).get("records", []))
            else:
                raise RuntimeError(f"Failed record list on {table_id}: {resp}")
            if not resp.get("has_more", False):
                return records
            offset = resp["next_offset"]

    def batch_update_records(self, table_id: str, records: dict[str, dict[str, Any]]) -> None:
        items = list(records.items())
        for i in range(0, len(items), 100):
            payload = {"update_records": dict(items[i : i + 100])}
            result = self._run_cli(
                "+record-batch-update", ["--table-id", table_id, "--json", json.dumps(payload)]
            )
            if not result.get("ok"):
                raise RuntimeError(f"Failed batch update on {table_id}: {result}")

    def local_diff_records(self) -> list[dict[str, Any]]:
        return self.local_reviews.read()

    def upsert_diff_records(self, records: list[dict[str, Any]]) -> dict[str, int]:
        """Compatibility entry point: review rows now update the external-drive ledger."""
        return self.local_reviews.upsert(records)
