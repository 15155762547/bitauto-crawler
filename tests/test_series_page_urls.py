import pytest

from bitauto.models.station import get_station


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("AE", "https://www.bitauto.com/en-ae/byd/seal/"),
        ("SA", "https://www.bitauto.com/en-sa/byd/seal/"),
        ("SG", "https://www.bitauto.com/sg/byd/seal/"),
        ("AU", "https://www.bitauto.com/au/byd/seal/"),
        ("UK", "https://www.bitauto.com/uk/byd/seal/"),
        ("US", "https://www.bitauto.com/us/byd/seal/"),
        ("HK", "https://www.bitauto.hk/en/byd/seal/"),
        ("MY", "https://www.bitauto.my/en/byd/seal/"),
        ("GLOBAL", "https://www.bitauto.com/global/byd/seal/"),
    ],
)
def test_public_page_uses_frontend_host_and_page_locale(code, expected):
    station = get_station(code)
    assert station.series_page_url("byd", "seal") == expected
    assert station.host.startswith("global-api.")


def test_missing_brand_does_not_generate_malformed_link():
    assert get_station("HK").series_page_url("", "seal") == ""
