"""Fetch one publicly listed long-strip chapter from its public Server 1 image list."""
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

ROOT = Path(r"D:\MangaArabic\manga-arabic-web")
PAGE_URL = "https://manhuaplus.org/manga/the-heavenly-demon-wants-a-quiet-life/chapter-19"
CHAPTER_ID = 58870
CDN_HOST = "cdn.manhuaplus.cc"
OUT_DIR = ROOT / "ocr_test_results/site_web_samples_20261114/heavenly_demon"
ARCHIVE = OUT_DIR / "the_heavenly_demon_chapter19.cbz"
MANIFEST = ROOT / "ocr_test_results/chapter_index.json"
UA = "Mozilla/5.0 (compatible; MangaArabicOCR-QA/1.0)"


def get(url: str, data: bytes | None = None):
    request = Request(
        url, data=data,
        headers={"User-Agent": UA, "Accept": "application/json, image/webp, */*"},
        method="POST" if data is not None else "GET",
    )
    with urlopen(request, timeout=45) as response:
        if response.status != 200:
            raise RuntimeError(f"Public resource returned HTTP {response.status}.")
        return response.read(), response.headers.get("Content-Type", "")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    raw, _ = get(f"https://manhuaplus.org/ajax/image/list/chap/{CHAPTER_ID}", data=b"")
    response = json.loads(raw.decode("utf-8"))
    if response.get("status") is not True or not isinstance(response.get("html"), str):
        raise RuntimeError("Public Server 1 page list returned an unexpected response.")
    pairs = re.findall(r'data-index="(\d+)"[^>]*>\s*<a href="([^"]+)"', response["html"])
    pairs.sort(key=lambda pair: int(pair[0]))
    indexes = [int(index) for index, _ in pairs]
    if not pairs or indexes != list(range(len(pairs))):
        raise RuntimeError(f"Page sequence is incomplete or duplicated: {indexes}")
    for _, url in pairs:
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname != CDN_HOST or not parsed.path.lower().endswith(".webp"):
            raise RuntimeError("Stopped because a page URL fell outside the public WebP CDN host.")

    pages = []
    with zipfile.ZipFile(ARCHIVE, "w", compression=zipfile.ZIP_STORED) as archive:
        for index, url in pairs:
            data, content_type = get(url)
            with Image.open(BytesIO(data)) as image:
                image_format = image.format
                width, height = image.size
                image.verify()
            if image_format != "WEBP" or width <= 0 or height <= 0:
                raise RuntimeError(f"Unexpected page image at site index {index}.")
            member = f"page_{int(index) + 1:03d}.webp"
            archive.writestr(member, data)
            pages.append({
                "page": int(index) + 1, "member": member, "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(), "width": width,
                "height": height, "format": "webp", "source_url": url,
                "content_type": content_type,
            })
    with zipfile.ZipFile(ARCHIVE, "r") as archive:
        if archive.testzip():
            raise RuntimeError("CBZ integrity check failed.")
    vertical_count = sum(page["height"] >= page["width"] for page in pages)
    if vertical_count / len(pages) < 0.80:
        raise RuntimeError(f"Chapter does not appear predominantly vertical: {vertical_count}/{len(pages)} pages.")

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
        "page_count": len(pages), "formats": {".webp": len(pages)},
        "source_page": PAGE_URL, "source_origin": "Public ManhuaPlus chapter page and Server 1 page list",
        "story_series": "The Heavenly Demon Wants a Quiet Life", "chapter_label": "Chapter 19",
        "vertical_layout_basis": f"series is tagged Long Strip; {vertical_count}/{len(pages)} page images are taller than wide",
        "server_used": "public Server 1 only", "pages": pages,
    })
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "status": "complete", "chapter": ARCHIVE.name, "pages": len(pages),
        "archive_bytes": ARCHIVE.stat().st_size, "all_vertical": vertical_count == len(pages),
        "vertical_page_count": vertical_count,
        "dimensions": [[page["width"], page["height"]] for page in pages],
        "archive": str(ARCHIVE), "manifest": str(MANIFEST),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
