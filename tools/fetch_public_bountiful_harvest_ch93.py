"""Fetch a publicly listed vertical chapter from ManhuaPlus into an untouched-source CBZ."""
from __future__ import annotations

import hashlib
import json
import re
import zipfile
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from PIL import Image

ROOT = Path(r"D:\\MangaArabic\\manga-arabic-web")
PAGE_URL = "https://manhuaplus.org/manga/what-a-bountiful-harvest-demon-lord/chapter-93"
CHAPTER_ID = "105674"
OUT_DIR = ROOT / "ocr_test_results/site_web_samples_20261114/bountiful_harvest"
ARCHIVE = OUT_DIR / "what_a_bountiful_harvest_chapter93.cbz"
MANIFEST = ROOT / "ocr_test_results/chapter_index.json"
UA = "Mozilla/5.0 (compatible; MangaArabicOCR-QA/1.0)"
HEADERS = {"User-Agent": UA, "Accept": "application/json, image/webp, */*"}


def get(url: str, data: bytes | None = None) -> tuple[bytes, str]:
    request = Request(url, data=data, headers=HEADERS,
                      method="POST" if data is not None else "GET")
    with urlopen(request, timeout=45) as response:
        if response.status != 200:
            raise RuntimeError(f"HTTP {response.status} for public source")
        return response.read(), response.headers.get("Content-Type", "")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    page_html, _ = get(PAGE_URL)
    if f"CHAPTER_ID = {CHAPTER_ID}" not in page_html.decode("utf-8", errors="replace"):
        raise RuntimeError("Chapter ID no longer matches the public chapter page.")
    raw, _ = get("https://manhuaplus.org/ajax/image/list/chap/" + CHAPTER_ID, b"")
    response = json.loads(raw.decode("utf-8"))
    if response.get("status") is not True or not isinstance(response.get("html"), str):
        raise RuntimeError("Public image-list endpoint returned an unexpected response.")
    pairs = re.findall(r'data-index="(\d+)"[^>]*>\s*<a href="([^"]+)"', response["html"])
    pairs.sort(key=lambda pair: int(pair[0]))
    indexes = [int(index) for index, _ in pairs]
    if not pairs or indexes != list(range(len(pairs))):
        raise RuntimeError(f"Page indexes are incomplete or duplicated: {indexes}")
    for _, image_url in pairs:
        parsed = urlparse(image_url)
        if parsed.scheme != "https" or parsed.hostname != "cdn.manhuaplus.cc" or not parsed.path.lower().endswith(".webp"):
            raise RuntimeError("Image URL is not the page-listed public WebP CDN resource.")
    pages = []
    with zipfile.ZipFile(ARCHIVE, "w", compression=zipfile.ZIP_STORED) as archive:
        for index, image_url in pairs:
            data, content_type = get(image_url)
            with Image.open(BytesIO(data)) as image:
                image_format, width, height = image.format, *image.size
                image.verify()
            if image_format != "WEBP" or width <= 0 or height <= 0:
                raise RuntimeError(f"Unexpected image payload at page {index}.")
            member = f"page_{int(index)+1:03d}.webp"
            archive.writestr(member, data)
            pages.append({"page": int(index)+1, "member": member, "bytes": len(data),
                          "sha256": hashlib.sha256(data).hexdigest(), "width": width,
                          "height": height, "format": "webp", "source_url": image_url,
                          "content_type": content_type})
    vertical_count = sum(page["height"] > page["width"] for page in pages)
    if vertical_count < int(len(pages) * 0.8):
        raise RuntimeError(f"Chapter is not predominantly vertical: {vertical_count}/{len(pages)}")
    with zipfile.ZipFile(ARCHIVE) as archive:
        bad_member = archive.testzip()
        if bad_member:
            raise RuntimeError(f"CBZ integrity check failed at {bad_member}")
    manifest = {"schema": 1, "source_unchanged": True, "supported_raster_suffixes":
                [".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"], "archives": []}
    if MANIFEST.is_file():
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest.setdefault("archives", [])
    manifest["archives"] = [item for item in manifest["archives"]
                            if item.get("chapter") != ARCHIVE.name]
    manifest["archives"].append({
        "chapter": ARCHIVE.name, "status": "indexed", "archive_bytes": ARCHIVE.stat().st_size,
        "archive_sha256": hashlib.sha256(ARCHIVE.read_bytes()).hexdigest(),
        "page_count": len(pages), "formats": {".webp": len(pages)}, "source_page": PAGE_URL,
        "source_origin": "ManhuaPlus public chapter page and its page-listed CDN",
        "story_series": "What a Bountiful Harvest, Demon Lord!", "chapter_label": "Chapter 93",
        "vertical_layout_basis": "Long Strip listing; verified image aspect ratios",
        "pages": pages,
    })
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": "complete", "chapter": ARCHIVE.name, "pages": len(pages),
                      "vertical_pages": vertical_count, "dimensions":
                      [[page["width"], page["height"]] for page in pages],
                      "archive_bytes": ARCHIVE.stat().st_size,
                      "archive": str(ARCHIVE), "manifest": str(MANIFEST)},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
