import argparse
import hashlib
import json
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

import requests
from lxml import html

from bitauto.build_category_sheets import run_cli
from bitauto.config import TABLE_SERIES, TABLE_TRIMS, require_feishu


def canonicalize_names(names, raw):
    from collections import defaultdict

    cjk = lambda x: bool(re.search("[\u3400-\u9fff]", x or ""))
    brand = defaultdict(dict)
    aliases = defaultdict(dict)
    english = {}
    for p in sorted(raw.glob("brands_*_zh.json")):
        for x in json.loads(p.read_text()).get("data") or []:
            if cjk(x.get("masterName")):
                brand[x["urlSpell"]][x["masterName"]] = str(p)
    for p in sorted(raw.glob("serials_*_zh.json")):
        for x in json.loads(p.read_text()).get("data") or []:
            if (
                x.get("brandUrlSpell")
                and x.get("cnSerialId")
                and x.get("cnUrlSpell")
                and cjk(x.get("serialName"))
            ):
                aliases[(x["brandUrlSpell"], x["cnSerialId"], x["cnUrlSpell"])][x["serialName"]] = (
                    str(p)
                )
    for p in raw.glob("serials_*_en.json"):
        code = p.name.split("_")[1].upper()
        for x in json.loads(p.read_text()).get("data") or []:
            english[f"{code}_{x['serialId']}"] = x
    proof = []
    for k, n in names.items():
        x = english.get(k)
        if not x:
            continue
        b = x.get("brandUrlSpell")
        identity = (b, x.get("cnSerialId"), x.get("cnUrlSpell"))
        a = aliases.get(identity, {})
        if not cjk(n["brand"]) and len(brand.get(b, {})) == 1:
            value, path = next(iter(brand[b].items()))
            proof.append(
                {
                    "key": k,
                    "field": "brand",
                    "before": n["brand"],
                    "value": value,
                    "brand_spell": b,
                    "source": path,
                }
            )
            n["brand"] = value
        # Preserve the HK site's explicitly verified local product names, e.g. Seal.
        if not k.startswith("HK_") and not cjk(n["series"]) and len(a) == 1:
            value, path = next(iter(a.items()))
            proof.append(
                {
                    "key": k,
                    "field": "series",
                    "before": n["series"],
                    "value": value,
                    "official_china_reference": identity,
                    "source": path,
                }
            )
            n["series"] = value
    return proof


def main():
    parser = argparse.ArgumentParser(
        description="Resolve same-market Chinese names and upload source cover attachments."
    )
    parser.add_argument("--audit-dir", type=Path, required=True)
    args = parser.parse_args()
    root = args.audit_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    raw = root.parent.parent / "raw"
    root.joinpath("images").mkdir(exist_ok=True)

    def save(n, o):
        (root / n).write_text(json.dumps(o, ensure_ascii=False, indent=2))

    def cli(cmd, a):
        require_feishu()
        r = run_cli(cmd, a)
        if r.get("ok") is False:
            raise RuntimeError(r)
        return r

    def read(tid, label):
        rows = []
        off = 0
        while True:
            path = root / f"{tid}-{label}-{off}.ndjson"
            if label == "before" and path.exists():
                chunk = [json.loads(l) for l in path.read_text().splitlines()]
                rows.extend(chunk)
                if len(chunk) < 2000:
                    return rows
                off += len(chunk)
                continue
            r = cli(
                "+record-list",
                [
                    "--table-id",
                    tid,
                    "--offset",
                    str(off),
                    "--limit",
                    "2000",
                    "--format",
                    "ndjson",
                    "--output",
                    str(path),
                ],
            )
            rows.extend(json.loads(l) for l in path.read_text().splitlines())
            if not r["has_more"]:
                return rows
            off = r["next_offset"]

    def http(url):
        r = requests.get(url, timeout=25)
        if r.status_code in (401, 403, 429):
            save("ACCESS_PAUSED.json", {"url": url, "status": r.status_code})
            raise RuntimeError(f"Paused: HTTP {r.status_code}: {url}")
        r.raise_for_status()
        return r

    series = read(TABLE_SERIES, "before")
    trims = read(TABLE_TRIMS, "before")
    zhbrands = {}
    zhseries = {}
    carimages = {}
    for p in raw.glob("brands_*_zh.json"):
        code = p.name.split("_")[1].upper()
        for c in json.loads(p.read_text()).get("data") or []:
            if c.get("masterName"):
                zhbrands[(code, c["masterId"])] = c["masterName"]
    for p in raw.glob("serials_*_zh.json"):
        code = p.name.split("_")[1].upper()
        for c in json.loads(p.read_text()).get("data") or []:
            if c.get("serialName"):
                zhseries[(code, c["serialId"])] = c["serialName"]
    for p in raw.glob("params_*_en.json"):
        code = p.name.split("_")[1].upper()
        for c in (json.loads(p.read_text()).get("data") or {}).get("carList", []):
            if c.get("imgUrl"):
                carimages[(code, c["carId"], c.get("serialId"))] = c["imgUrl"]
    resolved = (
        json.loads((root / "names-progress.json").read_text())
        if (root / "names-progress.json").exists()
        else {}
    )
    web_evidence = []
    unresolved = []
    for ix, row in enumerate(series):
        if row["车系唯一键"] in resolved:
            continue
        code = row["分站代码"]
        sid = row["来源车系ID"]
        brand = zhbrands.get((code, row["品牌ID"]))
        name = zhseries.get((code, sid))
        source = "stored same-market Chinese catalog"
        if not brand or not name or not re.search("[\u3400-\u9fff]", brand):
            url = row["来源车系页"]
            u = urlsplit(url)
            parts = u.path.strip("/").split("/")
            parts[0] = "zh" if code in ["HK", "MY"] else "zh-" + code.lower()
            url = f"https://{u.netloc}/" + "/".join(parts) + "/"
            try:
                response = http(url)
                tree = html.fromstring(response.content)
                h1 = "".join(tree.xpath("(//h1)[1]//text()")).strip()
                brandhref = "/" + parts[0] + "/" + parts[1] + "/"
                candidates = [
                    "".join(a.itertext()).strip()
                    for a in tree.xpath("//a[@href]")
                    if a.attrib["href"].rstrip("/").endswith(brandhref.rstrip("/"))
                ]
                candidates = [x for x in candidates if x and len(x) < 60]
                pagebrand = candidates[0] if candidates else ""
                if pagebrand and h1.startswith(pagebrand):
                    brand = pagebrand
                    name = h1[len(pagebrand) :].strip()
                    source = url
                web_evidence.append(
                    {
                        "key": row["车系唯一键"],
                        "url": url,
                        "h1": h1,
                        "brand": pagebrand,
                        "sha256": hashlib.sha256(response.content).hexdigest(),
                    }
                )
                (root / f"page-{row['车系唯一键']}.html").write_bytes(response.content)
            except requests.RequestException as e:
                unresolved.append({"key": row["车系唯一键"], "error": str(e)})
            time.sleep(0.35)
        resolved[row["车系唯一键"]] = {"brand": brand, "series": name, "source": source}
        if (ix + 1) % 100 == 0:
            print("Chinese sources resolved", ix + 1, "/", len(series), flush=True)
            save("names-progress.json", resolved)
    save("canonical-name-evidence.json", canonicalize_names(resolved, raw))
    save("names-sources.json", resolved)
    save("web-evidence.json", web_evidence)
    save("unresolved-names.json", unresolved)
    for tid, rows, keyfield in [
        (TABLE_SERIES, series, "车系唯一键"),
        (TABLE_TRIMS, trims, "款型唯一键"),
    ]:
        changes = {}
        for row in rows:
            key = (
                row["车系唯一键"]
                if keyfield == "车系唯一键"
                else f"{row['分站代码']}_{row['来源车系ID']}"
            )
            src = resolved[key]
            delta = {}
            for f, v in [("品牌中文名", src["brand"]), ("车系中文名", src["series"])]:
                if v and row.get(f) != v:
                    delta[f] = v
            if delta:
                changes[row["record_id"]] = delta
        save(f"{tid}-names-plan.json", changes)
        pairs = list(changes.items())
        for i in range(0, len(pairs), 200):
            cli(
                "+record-batch-update",
                [
                    "--table-id",
                    tid,
                    "--json",
                    json.dumps({"update_records": dict(pairs[i : i + 200])}),
                ],
            )
        print("Names written", tid, len(changes), flush=True)
    # Separate attachment columns preserve the source URL fields and existing schema.
    media = (
        [json.loads(l) for l in (root / "media-receipts.jsonl").read_text().splitlines()]
        if (root / "media-receipts.jsonl").exists()
        else []
    )
    uploaded = {(x["table"], x["record_id"]) for x in media if x["status"] == "UPLOADED"}
    for tid, rows in [(TABLE_SERIES, series)]:
        rows = read(tid, f"media-current-{int(time.time())}")
        info = cli("+table-get", ["--table-id", tid])["data"]
        save(f"{tid}-schema-before.json", info)
        field = next((f for f in info["fields"] if f["name"] == "封面图片"), None)
        if field is None:
            result = cli(
                "+field-create",
                [
                    "--table-id",
                    tid,
                    "--json",
                    json.dumps({"name": "封面图片", "type": "attachment"}),
                ],
            )
            save(f"{tid}-attachment-create.json", result)
            time.sleep(3)
        else:
            assert field["type"] == "attachment"
        for i, row in enumerate(rows):
            if row.get("封面图片") or (tid, row["record_id"]) in uploaded:
                continue
            key = row.get("车系唯一键") or row.get("款型唯一键")
            url = (
                row.get("车系封面图")
                if tid == TABLE_SERIES
                else carimages.get((row["分站代码"], row["来源款型ID"], row["来源车系ID"]))
            )
            if not url:
                media.append({"key": key, "table": tid, "status": "SOURCE_IMAGE_NOT_DISCLOSED"})
                continue
            match = re.search(r"https?://[^\s)\]]+", str(url))
            url = match.group(0) if match else ""
            url = url.replace("{0}", "660")
            suffix = Path(urlsplit(url).path).suffix.lower()
            if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
                suffix = ".jpg"
            file = root / "images" / f"{hashlib.sha256(url.encode()).hexdigest()}{suffix}"
            try:
                if not file.exists():
                    response = http(url)
                    assert response.headers.get("Content-Type", "").startswith("image/"), url
                    assert len(response.content) > 500, url
                    file.write_bytes(response.content)
                    time.sleep(0.15)
                result = cli(
                    "+record-upload-attachment",
                    [
                        "--table-id",
                        tid,
                        "--record-id",
                        row["record_id"],
                        "--field-id",
                        "封面图片",
                        "--file",
                        str(file),
                    ],
                )
                media.append(
                    {
                        "key": key,
                        "table": tid,
                        "record_id": row["record_id"],
                        "url": url,
                        "path": str(file),
                        "sha256": hashlib.sha256(file.read_bytes()).hexdigest(),
                        "status": "UPLOADED",
                        "result": result,
                    }
                )
            except requests.RequestException as e:
                media.append(
                    {
                        "key": key,
                        "table": tid,
                        "url": url,
                        "status": "DOWNLOAD_FAILED",
                        "error": str(e),
                    }
                )
            with (root / "media-receipts.jsonl").open("a") as stream:
                stream.write(json.dumps(media[-1], ensure_ascii=False) + "\n")
            if (i + 1) % 50 == 0:
                save("media-progress.json", media)
            if (i + 1) % 50 == 0:
                print("Attachment progress", tid, i + 1, "/", len(rows), flush=True)
        save(f"{tid}-after.json", read(tid, "after"))
    save("media-results.json", media)

    reference = {
        "name": "封面图片",
        "type": "lookup",
        "from": "vehicle_models",
        "select": "封面图片",
        "aggregate": "raw_value",
        "where": {
            "logic": "and",
            "conditions": [
                ["分站代码", "==", {"type": "field_ref", "field": "分站代码"}],
                ["来源车系ID", "==", {"type": "field_ref", "field": "来源车系ID"}],
            ],
        },
    }
    info = cli("+table-get", ["--table-id", TABLE_TRIMS])["data"]
    existing = next((f for f in info["fields"] if f["name"] == "封面图片"), None)
    if existing is None:
        cli(
            "+field-create",
            [
                "--table-id",
                TABLE_TRIMS,
                "--i-have-read-guide",
                "--json",
                json.dumps(reference),
            ],
        )
    else:
        assert (
            existing["type"] == "lookup"
            and existing["from"] == "vehicle_models"
            and existing["select"] == "封面图片"
        )

    print(
        "Upload stage finished; inspect names and attachment readbacks before accepting.",
        flush=True,
    )


if __name__ == "__main__":
    main()
