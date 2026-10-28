import logging
import sys
import time

from bitauto.client import BitAutoAccessPausedError, BitAutoClient
from bitauto.config import DATA_DIR
from bitauto.models.station import get_station

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("bitauto.crawl_global")

RAW_DIR = DATA_DIR / "raw"
ALLOWED_SALE_STATUSES = {1, 2, 4, 8}


def crawl_global():
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    client = BitAutoClient(
        raw_storage_dir=RAW_DIR, pause_flag_file=DATA_DIR / "PAUSED.json", request_interval_sec=0.2
    )
    st = get_station("global")

    logger.info("Starting Global station crawl: fetching master brands...")
    brands_en = client.get_master_brand_list(st, lang="en")
    client.get_master_brand_list(st, lang="zh")
    logger.info("Fetched %d brands for Global station.", len(brands_en))

    # Collect all NEV serials
    nev_serials = []
    for idx, b in enumerate(brands_en, start=1):
        try:
            serials_en = client.get_serial_list(st, b.master_id, lang="en")
            # Also fetch ZH serial list to cache it
            client.get_serial_list(st, b.master_id, lang="zh")

            for s in serials_en:
                if s.is_new_energy == 1 and s.sale_status in ALLOWED_SALE_STATUSES:
                    nev_serials.append(s)
        except BitAutoAccessPausedError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "Error fetching serials for brand %s (%s): %s", b.master_name, b.master_id, e
            )

        if idx % 20 == 0 or idx == len(brands_en):
            logger.info(
                "Scanned %d/%d brands. Found %d NEV serials so far.",
                idx,
                len(brands_en),
                len(nev_serials),
            )

    logger.info("Total NEV serials to crawl parameter details: %d", len(nev_serials))

    # Fetch parameter details for all NEV serials
    success_count = 0
    fail_count = 0
    start_time = time.time()

    for idx, s in enumerate(nev_serials, start=1):
        spell = s.url_spell
        if not spell:
            continue

        cache_en = RAW_DIR / f"params_global_{spell}_en.json"
        cache_zh = RAW_DIR / f"params_global_{spell}_zh.json"

        if cache_en.exists() and cache_zh.exists():
            success_count += 1
            if idx % 25 == 0 or idx == len(nev_serials):
                logger.info(
                    "[%d/%d] (Cached) %s %s", idx, len(nev_serials), s.master_name, s.serial_name
                )
            continue

        try:
            client.get_param_detail(st, spell, lang="en")
            client.get_param_detail(st, spell, lang="zh")
            success_count += 1
        except BitAutoAccessPausedError:
            raise
        except Exception as e:  # noqa: BLE001
            fail_count += 1
            logger.warning("Failed param detail for %s (%s): %s", s.serial_name, spell, e)

        if idx % 25 == 0 or idx == len(nev_serials):
            elapsed = time.time() - start_time
            rate = idx / elapsed if elapsed > 0 else 0
            logger.info(
                "[%d/%d] Success: %d, Failed: %d (%.1f items/sec)",
                idx,
                len(nev_serials),
                success_count,
                fail_count,
                rate,
            )

    logger.info("Global station crawl complete! Success: %d, Failed: %d", success_count, fail_count)


if __name__ == "__main__":
    crawl_global()
