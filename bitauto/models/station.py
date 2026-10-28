from dataclasses import dataclass


@dataclass(frozen=True)
class CountryStation:
    code: str
    name: str
    country_id: int
    host: str
    prefix: str
    locale_en: str
    locale_zh: str
    currency: str

    def series_page_url(self, brand_spell: str, series_spell: str) -> str:
        """Build a public English page URL, independently of API host/locale."""
        if not brand_spell or not series_spell:
            return ""
        if self.code in {"hk", "my"}:
            origin = f"https://www.bitauto.{self.code}/en"
        else:
            origin = f"https://www.bitauto.com/{self.locale_en}"
        return f"{origin}/{brand_spell}/{series_spell}/"


STATIONS: dict[str, CountryStation] = {
    "hk": CountryStation(
        code="hk",
        name="Hong Kong (中国香港)",
        country_id=118,
        host="global-api.bitauto.hk",
        prefix="/api",
        locale_en="en-hk",
        locale_zh="zh-hk",
        currency="HKD",
    ),
    "my": CountryStation(
        code="my",
        name="Malaysia (马来西亚)",
        country_id=120,
        host="global-api.bitauto.my",
        prefix="/api",
        locale_en="en-my",
        locale_zh="zh-my",
        currency="MYR",
    ),
    "ae": CountryStation(
        code="ae",
        name="UAE (阿联酋)",
        country_id=102,
        host="global-api.bitauto.com",
        prefix="/caroverseasapi",
        locale_en="en-ae",
        locale_zh="zh-ae",
        currency="AED",
    ),
    "sa": CountryStation(
        code="sa",
        name="Saudi Arabia (沙特阿拉伯)",
        country_id=100,
        host="global-api.bitauto.com",
        prefix="/caroverseasapi",
        locale_en="en-sa",
        locale_zh="zh-sa",
        currency="SAR",
    ),
    "au": CountryStation(
        code="au",
        name="Australia (澳大利亚)",
        country_id=105,
        host="global-api.bitauto.com",
        prefix="/caroverseasapi",
        locale_en="au",
        locale_zh="zh-au",
        currency="AUD",
    ),
    "sg": CountryStation(
        code="sg",
        name="Singapore (新加坡)",
        country_id=107,
        host="global-api.bitauto.com",
        prefix="/caroverseasapi",
        locale_en="sg",
        locale_zh="zh-sg",
        currency="SGD",
    ),
    "uk": CountryStation(
        code="uk",
        name="United Kingdom (英国)",
        country_id=103,
        host="global-api.bitauto.com",
        prefix="/caroverseasapi",
        locale_en="uk",
        locale_zh="zh-uk",
        currency="GBP",
    ),
    "us": CountryStation(
        code="us",
        name="United States (美国)",
        country_id=123,
        host="global-api.bitauto.com",
        prefix="/caroverseasapi",
        locale_en="us",
        locale_zh="zh-us",
        currency="USD",
    ),
    "global": CountryStation(
        code="global",
        name="Global (全球主站)",
        country_id=117,
        host="global-api.bitauto.com",
        prefix="/caroverseasapi",
        locale_en="global",
        locale_zh="zh-global",
        currency="-",
    ),
}


def get_all_stations() -> list[CountryStation]:
    return list(STATIONS.values())


def get_station(code: str) -> CountryStation:
    code_lower = code.lower()
    if code_lower not in STATIONS:
        raise KeyError(f"Station {code} not supported. Must be one of {list(STATIONS.keys())}")
    return STATIONS[code_lower]
