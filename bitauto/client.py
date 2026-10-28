import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import requests

from bitauto.models.entities import BrandItem, ParamDetailData, SeriesItem
from bitauto.models.station import CountryStation

logger = logging.getLogger(__name__)


class BitAutoApiError(Exception):
    pass


class BitAutoAccessPausedError(BitAutoApiError):
    pass


class BitAutoClient:
    CID = "508"
    SECRET = os.environ.get("BITAUTO_SIGNING_SECRET", "")
    USER_AGENT = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )

    def __init__(
        self,
        raw_storage_dir: Path | None = None,
        pause_flag_file: Path | None = None,
        request_interval_sec: float = 0.2,
    ):
        self.raw_storage_dir = raw_storage_dir
        self.pause_flag_file = pause_flag_file
        self.request_interval_sec = request_interval_sec
        self.session = requests.Session()
        self.last_request_time = 0.0

        if self.raw_storage_dir:
            self.raw_storage_dir.mkdir(parents=True, exist_ok=True)

    def _check_pause(self):
        if self.pause_flag_file and self.pause_flag_file.exists():
            raise BitAutoAccessPausedError(
                f"Crawling paused due to flag file: {self.pause_flag_file}. "
                "Resolve the limit before removing this file."
            )

    def _trigger_pause(self, status_code: int, url: str, reason: str):
        msg = f"HTTP {status_code} ({reason}) encountered at {url}. Triggering pause flag."
        logger.error(msg)
        if self.pause_flag_file:
            self.pause_flag_file.parent.mkdir(parents=True, exist_ok=True)
            self.pause_flag_file.write_text(
                json.dumps(
                    {
                        "timestamp": time.time(),
                        "status_code": status_code,
                        "url": url,
                        "reason": reason,
                    },
                    indent=2,
                )
            )
        raise BitAutoAccessPausedError(msg)

    def _make_headers(self, params: dict[str, Any], n_lang: str) -> dict[str, str]:
        if not self.SECRET:
            raise BitAutoApiError("Set BITAUTO_SIGNING_SECRET before requesting the source API")
        ts = str(int(time.time() * 1000))
        param_str = json.dumps(params)
        sign_str = f"cid={self.CID}&param={param_str}{self.SECRET}{ts}"
        sign = hashlib.md5(sign_str.encode("utf-8")).hexdigest()

        return {
            "x-platform": "pc",
            "x-timestamp": ts,
            "x-sign": sign,
            "cid": self.CID,
            "n-lang": n_lang,
            "Content-Type": "application/json;charset=UTF-8",
            "User-Agent": self.USER_AGENT,
        }

    def call_api(
        self,
        station: CountryStation,
        endpoint_path: str,
        params: dict[str, Any],
        n_lang: str,
        cache_key: str | None = None,
    ) -> dict[str, Any]:
        self._check_pause()

        # Check raw storage cache if available
        if cache_key and self.raw_storage_dir:
            cache_file = self.raw_storage_dir / f"{cache_key}.json"
            if cache_file.exists():
                try:
                    return json.loads(cache_file.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as e:
                    logger.warning(f"Failed to read cache {cache_file}: {e}")

        # Rate limiting
        elapsed = time.time() - self.last_request_time
        if elapsed < self.request_interval_sec:
            time.sleep(self.request_interval_sec - elapsed)

        url = f"https://{station.host}{station.prefix}{endpoint_path}"
        resp = None
        for attempt in range(1, 4):
            headers = self._make_headers(params, n_lang)
            try:
                resp = self.session.get(url, params=params, headers=headers, timeout=15)
                self.last_request_time = time.time()
                break
            except (
                requests.exceptions.SSLError,
                requests.exceptions.ConnectionError,
                requests.exceptions.Timeout,
            ) as e:
                logger.warning(f"Transient error on attempt {attempt}/3 for {url}: {e}")
                self.session = requests.Session()
                time.sleep(1.0 * attempt)
            except requests.RequestException as e:
                logger.error(f"Network error requesting {url}: {e}")
                raise BitAutoApiError(f"Network error: {e}") from e

        if resp is None:
            raise BitAutoApiError(f"Failed to fetch {url} after 3 attempts")

        if resp.status_code in (401, 403, 429):
            self._trigger_pause(resp.status_code, url, "Anti-crawl or rate limit triggered")

        if not resp.ok:
            raise BitAutoApiError(f"HTTP {resp.status_code} from {url}")

        try:
            data = resp.json()
        except Exception as e:
            raise BitAutoApiError(f"Failed to parse JSON from {url}: {e}") from e

        if data.get("status") != 1:
            raise BitAutoApiError(
                f"API status {data.get('status')} ({data.get('message')}) from {url}"
            )

        # Write to raw storage if cache_key is provided
        if cache_key and self.raw_storage_dir:
            cache_file = self.raw_storage_dir / f"{cache_key}.json"
            try:
                cache_file.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            except (OSError, TypeError) as e:
                logger.warning(f"Failed to write cache {cache_file}: {e}")

        return data

    def get_master_brand_list(self, station: CountryStation, lang: str = "en") -> list[BrandItem]:
        n_lang = station.locale_en if lang == "en" else station.locale_zh
        cache_key = f"brands_{station.code}_{lang}"
        raw = self.call_api(
            station, "/brand/v1/getmasterbrandlist", {}, n_lang, cache_key=cache_key
        )
        items = raw.get("data") or []
        res = []
        for it in items:
            it["country_code"] = station.code
            res.append(BrandItem.model_validate(it))
        return res

    def get_serial_list(
        self,
        station: CountryStation,
        master_id: int,
        lang: str = "en",
    ) -> list[SeriesItem]:
        n_lang = station.locale_en if lang == "en" else station.locale_zh
        cache_key = f"serials_{station.code}_{master_id}_{lang}"
        params = {"masterId": str(master_id)}
        raw = self.call_api(
            station, "/serial/v1/getseriallist", params, n_lang, cache_key=cache_key
        )
        items = raw.get("data") or []
        res = []
        for it in items:
            it["country_code"] = station.code
            res.append(SeriesItem.model_validate(it))
        return res

    def get_param_detail(
        self,
        station: CountryStation,
        serial_url_spell: str,
        car_id: str = "",
        lang: str = "en",
    ) -> ParamDetailData:
        n_lang = station.locale_en if lang == "en" else station.locale_zh
        cache_suffix = f"_{car_id}" if car_id else ""
        cache_key = f"params_{station.code}_{serial_url_spell}{cache_suffix}_{lang}"
        params = {
            "serialId": "",
            "carId": str(car_id),
            "serialUrlSpell": serial_url_spell,
        }
        raw = self.call_api(
            station, "/param/v1/getparamdetail", params, n_lang, cache_key=cache_key
        )
        data = raw.get("data") or {}
        return ParamDetailData.model_validate(data)
