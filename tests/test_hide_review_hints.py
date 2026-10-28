from bitauto.hide_review_hints import clean_note


def test_remove_review_prefix_but_preserve_real_availability():
    original = "部分参数单位口径待核；报价待公布（TBC）；图集来源暂停，封面可查看；图片为车系级，非配置专属"
    assert (
        clean_note(original)
        == "报价待公布（TBC）；图集来源暂停，封面可查看；图片为车系级，非配置专属"
    )
    assert clean_note(clean_note(original)) == clean_note(original)


def test_unverified_gallery_is_still_unavailable_not_reported_complete():
    assert clean_note("图集身份待核，封面可查看") == "图集暂不可用，封面可查看"
    assert "已采集" not in clean_note("图集状态待确认")
