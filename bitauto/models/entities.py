from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class BrandItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    country_code: str
    country_id: int = Field(alias="countryId")
    master_id: int = Field(alias="masterId")
    master_name: str | None = Field(default="", alias="masterName")
    url_spell: str = Field(alias="urlSpell")
    logo_path: str | None = Field(default=None, alias="logoPath")
    logo_path_wp: str | None = Field(default=None, alias="logoPathWp")
    sale_status: int | None = Field(default=None, alias="saleStatus")
    initial: str | None = Field(default=None)


class SeriesItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    country_code: str
    country_id: int = Field(alias="countryId")
    master_id: int = Field(alias="masterId")
    master_name: str | None = Field(default="", alias="masterName")
    serial_id: int = Field(alias="serialId")
    serial_name: str | None = Field(default="", alias="serialName")
    url_spell: str = Field(alias="urlSpell")
    is_new_energy: int = Field(default=0, alias="isNewEnergy")
    sale_status: int = Field(default=0, alias="saleStatus")
    min_refer_price: str | None = Field(default=None, alias="minReferPrice")
    max_refer_price: str | None = Field(default=None, alias="maxReferPrice")
    monetary_unit: str | None = Field(default=None, alias="monetaryUnit")
    monetary_unit_symbol: str | None = Field(default=None, alias="monetaryUnitSymbol")
    img_url: str | None = Field(default=None, alias="imgUrl")
    brand_url_spell: str | None = Field(default=None, alias="brandUrlSpell")
    cn_serial_id: int | None = Field(default=None, alias="cnSerialId")
    cn_url_spell: str | None = Field(default=None, alias="cnUrlSpell")


class CarInfoItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    car_id: int = Field(alias="carId")
    car_name: str | None = Field(default="", alias="carName")
    year: int | None = Field(default=None)
    master_id: int | None = Field(default=None, alias="masterId")
    master_name: str | None = Field(default="", alias="masterName")
    master_url_spell: str | None = Field(default=None, alias="masterUrlSpell")
    serial_id: int | None = Field(default=None, alias="serialId")
    serial_name: str | None = Field(default="", alias="serialName")
    serial_url_spell: str | None = Field(default=None, alias="serialUrlSpell")
    img_url: str | None = Field(default=None, alias="imgUrl")
    refer_price_with_unit: str | None = Field(default=None, alias="referPriceWithUnit")


class ParamValueItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    car_id: int = Field(alias="id")
    value: str | None = Field(default="")
    sub_list: list[Any] = Field(default_factory=list, alias="subList")


class ParamItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    param_id: int = Field(alias="id")
    name: str | None = Field(default="")
    type: int | None = Field(default=0)
    param_values: list[ParamValueItem] = Field(default_factory=list, alias="paramValues")


class ParamGroup(BaseModel):
    name: str | None = Field(default="")
    items: list[ParamItem] = Field(default_factory=list)


class ParamDetailData(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    car_list: list[CarInfoItem] = Field(default_factory=list, alias="carList")
    param_list: list[ParamGroup] = Field(default_factory=list, alias="paramList")


class ParamDiffRecord(BaseModel):
    country_code: str
    serial_id: int
    car_id: int
    param_id: int
    param_name_en: str
    param_name_zh: str
    group_name_en: str
    group_name_zh: str
    val_en: str | None
    val_zh: str | None
    diff_reason: str
    review_result: str = "资料不足"
    rule_version: str = ""


class MergedTrimRecord(BaseModel):
    # Stable identity: country_code + car_id
    stable_key: str
    country_code: str
    country_id: int
    serial_id: int
    serial_url_spell: str
    car_id: int
    year: int | None = None

    # Dual language names
    car_name_en: str = ""
    car_name_zh: str = ""
    serial_name_en: str = ""
    serial_name_zh: str = ""
    master_name_en: str = ""
    master_name_zh: str = ""

    # Pricing & Currency
    price_en: str | None = None
    price_zh: str | None = None
    currency: str = ""

    # Image
    img_url: str | None = None

    # Merged parameters: param_id -> dict of values & names
    parameters: dict[int, dict[str, Any]] = Field(default_factory=dict)

    # Discrepancies detected between EN and ZH
    diffs: list[ParamDiffRecord] = Field(default_factory=list)

    # Source metadata
    crawl_time: str
