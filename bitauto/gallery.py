"""Collect public series gallery links without downloading image binaries."""

import argparse
import hashlib
import json
import os
import re
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests
from lxml import html
from pydantic import BaseModel

from bitauto.build_category_sheets import run_cli
from bitauto.feishu_sync import TABLE_SERIES

IMAGE_PATH = re.compile(r"image-(\d+)-s(\d+)(?:-v\d+)?-g(\d+)-a(\d+)-p(\d+)/$")


class GalleryImage(BaseModel):
    photo_id: str
    index: int
    url: str
    page_url: str
    original_url: str | None = None


def image_identity(url, series_url, series_id):
    parts = urlsplit(url)
    base = urlsplit(series_url)
    match = IMAGE_PATH.search(parts.path)
    if parts.netloc != base.netloc or not parts.path.startswith(base.path) or not match:
        raise ValueError(f"Unexpected gallery path: {url}")
    if int(match[2]) != int(series_id):
        raise ValueError(f"Gallery source series mismatch: {url}")
    return match.groups()


def parse_gallery_page(content, url, series_url, series_id):
    """Retain actual source categories, counts, URLs and thumbnail pagination."""
    identity = image_identity(url, series_url, series_id)
    tree = html.fromstring(content)
    categories = {}
    for a in tree.xpath("//a[@data-groupid][@data-albumtype][@data-photocount]"):
        link = urljoin(url, a.get("href"))
        target = image_identity(link, series_url, series_id)
        key = f"{target[2]}:{target[3]}"
        categories[key] = {
            "name": " ".join(
                a.xpath('./span[contains(@class,"photo-group-label")]/text()')
                or a.xpath("./text()")
            ).strip(),
            "group_id": target[2],
            "album_type": target[3],
            "expected_count": int(a.get("data-photocount")),
            "page_url": link,
        }
    key = f"{identity[2]}:{identity[3]}"
    if key not in categories:
        raise ValueError(f"Gallery category absent: {url}")
    images = []
    originals = tree.xpath('//img[@id="image-view-big"]/@data-original')
    for a in tree.xpath("//a[@data-photoid][@data-index]"):
        link = urljoin(url, a.get("href"))
        photo = image_identity(link, series_url, series_id)
        if photo[2:4] != identity[2:4]:
            raise ValueError(f"Mixed categories: {url}")
        values = a.xpath("./img/@data-original") or a.xpath("./img/@src")
        if not values:
            raise ValueError(f"Photo URL missing: {link}")
        image = GalleryImage(
            photo_id=photo[0],
            index=int(a.get("data-index")),
            url=urljoin(url, values[0]),
            page_url=link,
            original_url=originals[0] if photo[0] == identity[0] and originals else None,
        )
        images.append(image.model_dump(exclude_none=True))
    next_pages = tree.xpath(
        '//*[contains(@class,"photo-list-next") or contains(@class,"photo-list-prev")]/a/@href'
    )
    return key, categories, images, [urljoin(url, link) for link in next_pages]


class GalleryCrawler:
    def __init__(self, audit):
        self.audit = Path(audit)
        self.pages = self.audit / "pages"
        self.pages.mkdir(parents=True, exist_ok=True)
        self.paused_hosts = set()
        self.final_urls = {}
        self.local = threading.local()

    def get(self, url):
        host = urlsplit(url).netloc
        if host in self.paused_hosts or (self.audit / f"PAUSED-{host}.json").exists():
            raise RuntimeError(f"Source paused: {host}")
        stem = self.pages / hashlib.sha256(url.encode()).hexdigest()
        if stem.with_suffix(".html").exists():
            self.final_urls[url] = json.loads(stem.with_suffix(".json").read_text())["final_url"]
            return stem.with_suffix(".html").read_text()
        time.sleep(0.25)
        if not hasattr(self.local, "session"):
            self.local.session = requests.Session()
        response = self.local.session.get(url, timeout=25)
        metadata = {
            "url": url,
            "final_url": response.url,
            "status": response.status_code,
            "fetched_at": datetime.now(UTC).isoformat(),
            "sha256": hashlib.sha256(response.content).hexdigest(),
        }
        stem.with_suffix(".json").write_text(json.dumps(metadata))
        if response.status_code in {401, 403, 429}:
            stem.with_suffix(".error.html").write_text(response.text)
            self.paused_hosts.add(host)
            (self.audit / f"PAUSED-{host}.json").write_text(json.dumps(metadata))
            raise RuntimeError(f"Source paused HTTP {response.status_code}: {url}")
        response.raise_for_status()
        self.final_urls[url] = response.url
        requested = IMAGE_PATH.search(urlsplit(url).path)
        resolved = IMAGE_PATH.search(urlsplit(response.url).path)
        same_photo = (
            requested
            and resolved
            and requested.groups()[:4] == resolved.groups()[:4]
            and urlsplit(url).path.rsplit("/", 2)[0]
            == urlsplit(response.url).path.rsplit("/", 2)[0]
        )
        if response.url.rstrip("/") != url.rstrip("/") and not (
            ("/photo/redirect/" in url or same_photo) and urlsplit(response.url).netloc == host
        ):
            raise ValueError(f"Unexpected redirect: {url} -> {response.url}")
        stem.with_suffix(".html").write_text(response.text)
        return response.text

    def gap_pages(self, groups, source, visited):
        # Official list arrows can skip a window; follow actual boundary photos.
        links = []
        for category in groups.values():
            by_index = {x["index"]: x for x in category["images"].values()}
            for index in range(1, category["expected_count"] + 1):
                if index in by_index:
                    continue
                adjacent = by_index.get(index - 1) or by_index.get(index + 1)
                if not adjacent:
                    continue
                boundary = html.fromstring(self.get(adjacent["page_url"]))
                direction = "photo-next" if adjacent["index"] < index else "photo-prev"
                for link in boundary.xpath(f'//*[contains(@class,"{direction}")]/a/@href'):
                    link = urljoin(source, link).split("?")[0]
                    if link not in visited:
                        links.append(link)
                break
        return links

    def collect(self, row):
        key = row["车系唯一键"]
        target = self.audit / "results" / f"{key}.json"
        if target.exists():
            return json.loads(target.read_text())
        source = re.search(r"https?://[^\s)\]]+", row["来源车系页"]).group()
        result = {
            "series_key": key,
            "series_id": row["来源车系ID"],
            "station": row["分站代码"],
            "source_url": source,
            "collected_at": datetime.now(UTC).isoformat(),
            "categories": [],
        }
        groups = {}
        try:
            tree = html.fromstring(self.get(source))
            ids = re.findall(r'serialId\s*:\s*["\'](\d+)', " ".join(tree.xpath("//script/text()")))
            if not ids or any(int(i) != int(row["来源车系ID"]) for i in ids):
                raise ValueError("Series page identity missing or mismatched")
            links = [urljoin(source, x) for x in tree.xpath('//a[contains(@href,"/image-")]/@href')]
            if not links:
                result["status"] = "NOT_DISCLOSED"
            else:
                pending = deque([links[0]])
                visited = set()
                while pending:
                    url = pending.popleft()
                    if url in visited:
                        if not pending:
                            pending.extend(self.gap_pages(groups, source, visited))
                        continue
                    visited.add(url)
                    content = self.get(url)
                    group, categories, images, next_pages = parse_gallery_page(
                        content, self.final_urls.get(url, url), source, row["来源车系ID"]
                    )
                    for gid, category in categories.items():
                        if gid not in groups:
                            groups[gid] = {**category, "images": {}}
                            pending.append(category["page_url"])
                    for image in images:
                        groups[group]["images"].setdefault(image["photo_id"], image)
                    pending.extend(next_pages)
                    if not pending:
                        pending.extend(self.gap_pages(groups, source, visited))
                result["status"] = "COMPLETE"
                for category in groups.values():
                    category["images"] = sorted(
                        category["images"].values(), key=lambda x: x["index"]
                    )
                    indices = [x["index"] for x in category["images"]]
                    if indices != list(range(1, category["expected_count"] + 1)):
                        result["status"] = "PARTIAL"
                    result["categories"].append(category)
        except (requests.RequestException, ValueError, RuntimeError) as error:
            result["status"] = (
                "ACCESS_PAUSED" if str(error).startswith("Source paused") else "FAILED"
            )
            result["error"] = str(error)
            result["categories"] = [
                {**c, "images": sorted(c["images"].values(), key=lambda x: x["index"])}
                for c in groups.values()
            ]
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        temporary.replace(target)
        return result


def read_series(audit):
    path = audit / "series-before.ndjson"
    if not path.exists():
        result = run_cli(
            "+record-list",
            [
                "--table-id",
                TABLE_SERIES,
                "--limit",
                "2000",
                "--format",
                "ndjson",
                "--output",
                str(path),
            ],
        )
        assert result.get("ok") is not False and not result["has_more"], result
    return [json.loads(line) for line in path.read_text().splitlines()]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-dir", type=Path, required=True)
    parser.add_argument("--sample", action="store_true", help="One source series per station")
    args = parser.parse_args()
    args.audit_dir = args.audit_dir.resolve()
    args.audit_dir.mkdir(parents=True, exist_ok=True)
    os.chdir(args.audit_dir)
    (args.audit_dir / "results").mkdir(exist_ok=True)
    rows = read_series(args.audit_dir)
    if args.sample:
        rows = list({r["分站代码"]: r for r in reversed(rows)}.values())
    crawler = GalleryCrawler(args.audit_dir)
    # A station is sequential; at most three independent station jobs run at once.
    stations = sorted({r["分站代码"] for r in rows})

    def collect_station(station):
        for row in rows:
            if row["分站代码"] == station:
                result = crawler.collect(row)
                print(
                    row["车系唯一键"],
                    result["status"],
                    sum(len(g["images"]) for g in result["categories"]),
                    flush=True,
                )

    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(collect_station, stations))


if __name__ == "__main__":
    main()
