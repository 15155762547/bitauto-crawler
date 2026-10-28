import pytest

from bitauto.gallery import GalleryCrawler, image_identity, parse_gallery_page
from bitauto.sync_gallery import cell_json

BASE = "https://www.bitauto.com/global/byd/seal/"
FIRST = BASE + "image-1-s1146-g6-a1-p0/"
SECOND = BASE + "image-2-s1146-g6-a1-p0/"


def page(photo, index, next_url=""):
    return f'''<a data-groupid="6" data-albumtype="1" data-photocount="2"
        href="{FIRST}">Exterior</a>
        <a data-photoid="{photo}" data-index="{index}"
        href="{BASE}image-{photo}-s1146-g6-a1-p0/">
        <img data-original="https://global-img.bitauto.com/photo{photo}.jpg"></a>
        <div class="photo-list-next"><a href="{next_url or FIRST}"></a></div>'''


def test_preserves_pagination_count_and_actual_image_url():
    key, categories, images, links = parse_gallery_page(page(1, 1, SECOND), FIRST, BASE, 1146)
    assert key == "6:1"
    assert categories[key]["expected_count"] == 2
    assert images[0]["url"] == "https://global-img.bitauto.com/photo1.jpg"
    assert links == [SECOND]


@pytest.mark.parametrize(
    "url", [FIRST.replace("1146", "999"), FIRST.replace("/global/", "/en-ae/")]
)
def test_rejects_other_series_or_station(url):
    with pytest.raises(ValueError):
        image_identity(url, BASE, 1146)


def test_version_gallery_still_requires_same_series():
    assert image_identity(FIRST.replace("-g6", "-v123-g6"), BASE, 1146)[1] == "1146"


def test_gap_recovery_uses_published_neighbor_link(tmp_path):
    crawler = GalleryCrawler(tmp_path)
    crawler.get = lambda _: f'<div class="photo-next"><a href="{SECOND}"></a></div>'
    groups = {"6:1": {"expected_count": 2, "images": {"1": {"index": 1, "page_url": FIRST}}}}
    assert crawler.gap_pages(groups, BASE, {FIRST}) == [SECOND]


def test_cell_json_does_not_invent_original_urls():
    import json

    result = {
        "station": "GLOBAL",
        "series_id": 1146,
        "source_url": BASE,
        "collected_at": "2026-10-25",
        "status": "PARTIAL",
        "categories": [
            {
                "name": "Exterior",
                "group_id": "6",
                "album_type": "1",
                "expected_count": 2,
                "images": [{"url": "https://example.com/w300.jpg"}],
            }
        ],
    }
    value = json.loads(cell_json(result))
    assert value["status"] == "PARTIAL"
    assert value["categories"][0]["urls"] == ["https://example.com/w300.jpg"]


@pytest.mark.parametrize("second_exists,expected", [(True, "COMPLETE"), (False, "PARTIAL")])
def test_complete_requires_all_declared_images(tmp_path, second_exists, expected):
    (tmp_path / "results").mkdir()
    crawler = GalleryCrawler(tmp_path)
    responses = {
        BASE: f'''<script>var Bitauto={{serialId:"1146"}}</script><a href="{FIRST}"></a>''',
        FIRST: page(1, 1, SECOND if second_exists else FIRST),
        SECOND: page(2, 2),
    }
    crawler.get = responses.__getitem__
    result = crawler.collect(
        {"车系唯一键": "GLOBAL_1146", "来源车系ID": 1146, "来源车系页": BASE, "分站代码": "GLOBAL"}
    )
    assert result["status"] == expected
    assert len(result["categories"][0]["images"]) == (2 if second_exists else 1)
