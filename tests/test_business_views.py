from bitauto.business_views import NOTE, RETIRED, configure_existing_views, note, order
from bitauto.feishu_sync import TABLE_TRIMS


def test_presale_missing_configs_is_not_reported_as_scraper_error():
    text = note({"销售状态": ["预售"], "旗下款型": [], "图库状态": ["已采集"], "指导价区间": "TBC"})
    assert "预售，当前未取得配置资料" in text
    assert "报价待公布（TBC）" in text
    assert "漏抓" not in text


def test_paused_gallery_preserves_cover_and_configuration_scope():
    text = note(
        {"图库状态": ["来源暂停"], "封面图片": [{"file_token": "cover"}], "指导价(EN)": "TBC"},
        trim=True,
    )
    assert "图集来源暂停，封面可查看" in text
    assert "图片为车系级，非配置专属" in text
    assert "币种未披露" in text


def test_business_fields_first_quality_fields_retained_last():
    old = ["车型全称", "核查状态", "问题说明", "分站名称", "基本信息", "轴距(mm)"]
    fields = set(old) | {NOTE}
    result = order(TABLE_TRIMS, old, fields)
    assert result[:3] == ["车型全称", "分站名称", NOTE]
    assert "核查状态" not in result and "问题说明" not in result
    assert set(result) == fields - {"核查状态", "问题说明"}
    assert order(TABLE_TRIMS, result, fields) == result


def test_deleted_views_are_not_recreated_by_normal_publish():
    class Release:
        def schema(self, _):
            return {"fields": [], "views": []}

        def cli(self, *args):
            raise AssertionError("No calls needed for missing views")

        def save(self, *args):
            pass

    assert configure_existing_views(Release()) == []
    assert "图片与来源" not in RETIRED
    assert "按车系跨国对比视图" not in RETIRED
    assert "按国家分站视图" not in RETIRED


def test_review_hints_are_not_published_as_business_notes():
    row = {"图库状态": ["已采集"], "指导价(EN)": "AED 100", "货币代码": ["AED"]}
    for status in ("单边资料缺失", "单位口径待核", "疑似差异", "双语释义待核"):
        assert note({**row, "核查状态": status}, trim=True) == note(row, trim=True)
    assert "图集暂不可用" in note({**row, "图库状态": ["身份待核"]}, trim=True)
