"""Fetch a public Long Strip chapter and preserve its page-listed order for OCR QA."""
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

ROOT = Path("D:/MangaArabic/manga-arabic-web")
PAGE_URL = "https://manhuaplus.org/manga/i-regressed-but-the-world-didn-t-end/chapter-19"
SERIES_TITLE = "I Regressed, but the World didn’t End"
CHAPTER_LABEL = "Chapter 19"
OUT_DIR = ROOT / "ocr_test_results/site_web_samples_20261114/i_regressed_but_world_didnt_end"
ARCHIVE = OUT_DIR / "i_regressed_but_the_world_didnt_end_chapter19.cbz"
MANIFEST = ROOT / "ocr_test_results/chapter_index.json"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; MangaArabicOCR-QA/1.0)",
    "Accept": "application/json, image/webp, image/png, image/jpeg, */*",
}
IMAGE_SUFFIXES = {"WEBP": ".webp", "JPEG": ".jpg", "PNG": ".png"}
MAX_PAGES = 100
MAX_IMAGE_BYTES = 30 * 1024 * 1024


def get(url: str, data: bytes | None = None):
    request = Request(url, data=data, headers=HEADERS,
                      method="POST" if data is not None else "GET")
    with urlopen(request, timeout=45) as response:
        if response.status != 200:
            raise RuntimeError(f"HTTP {response.status}")
        body = response.read(MAX_IMAGE_BYTES + 1)
        if len(body) > MAX_IMAGE_BYTES:
            raise RuntimeError("The response exceeded the bounded download size.")
        return body, response.headers.get("Content-Type", "")


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    html, _ = get(PAGE_URL)
    decoded_html = html.decode("utf-8", "replace")
    if CHAPTER_LABEL not in decoded_html or "i-regressed-but-the-world-didn-t-end" not in decoded_html:
        raise RuntimeError("The public page title did not match the selected series and chapter.")
    chapter_id_match = re.search(r"CHAPTER_ID\s*=\s*['\"]?(\d+)", decoded_html)
    if not chapter_id_match:
        raise RuntimeError("Could not identify the chapter ID from the public reader page.")
    chapter_id = chapter_id_match.group(1)

    raw, _ = get(f"https://manhuaplus.org/ajax/image/list/chap/{chapter_id}", b"")
    response = json.loads(raw.decode("utf-8"))
    if response.get("status") is not True or not isinstance(response.get("html"), str):
        raise RuntimeError("Unexpected public page-list response.")
    pairs = sorted(
        (int(index), "https:" + url if url.startswith("//") else url)
        for index, url in re.findall(
            r'data-index="([0-9]+)"[^>]*>[ \t\r\n]*<a href="([^"]+)"',
            response["html"],
        )
    )
    pairs = [(index, url) for index, url in pairs
             if url.strip().casefold() not in {"", "undefined", "null"}]
    if not pairs or len(pairs) > MAX_PAGES:
        raise RuntimeError(f"Unexpected page count: {len(pairs)}")
    indexes = [index for index, _ in pairs]
    if indexes != list(range(indexes[0], indexes[0] + len(indexes))):
        raise RuntimeError("Page indexes are incomplete or duplicated.")
    hosts = {urlparse(url).hostname for _, url in pairs}
    if hosts != {"cdn.manhuaplus.cc"} or any(urlparse(url).scheme != "https" for _, url in pairs):
        raise RuntimeError(f"Unexpected or insecure CDN metadata: {hosts}")

    pages = []
    with zipfile.ZipFile(ARCHIVE, "w", compression=zipfile.ZIP_STORED) as archive:
        for page_number, (source_index, url) in enumerate(pairs, 1):
            data, content_type = get(url)
            with Image.open(BytesIO(data)) as image:
                image_format, (width, height) = image.format, image.size
                image.verify()
            suffix = IMAGE_SUFFIXES.get(image_format)
            if suffix is None or width <= 0 or height <= 0:
                raise RuntimeError(
                    f"Unsupported or invalid image at page {page_number}: "
                    f"format={image_format} size={width}x{height}"
                )
            member = f"page_{page_number:03d}{suffix}"
            archive.writestr(member, data)
            pages.append({
                "page": page_number, "source_page_index": source_index,
                "member": member, "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(), "width": width,
                "height": height, "format": image_format.lower(), "source_url": url,
                "content_type": content_type,
                "orientation": "portrait" if height > width else "landscape" if width > height else "square",
            })
    with zipfile.ZipFile(ARCHIVE) as archive:
        bad_member = archive.testzip()
        if bad_member:
            raise RuntimeError(f"CBZ integrity check failed: {bad_member}")

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    archives = manifest.setdefault("archives", [])
    archives[:] = [item for item in archives if item.get("chapter") != ARCHIVE.name]
    archives.append({
        "chapter": ARCHIVE.name, "status": "indexed",
        "archive_bytes": ARCHIVE.stat().st_size,
        "archive_sha256": hashlib.sha256(ARCHIVE.read_bytes()).hexdigest(),
        "page_count": len(pages),
        "formats": {suffix: sum(Path(page["member"]).suffix == suffix for page in pages)
                    for suffix in sorted({Path(page["member"]).suffix for page in pages})},
        "source_page": PAGE_URL,
        "source_origin": "Public ManhuaPlus chapter page and its page-listed CDN",
        "story_series": SERIES_TITLE, "chapter_label": CHAPTER_LABEL,
        "vertical_layout_basis": "The series is explicitly tagged Long Strip and the public reader's ordered image list was used.",
        "pages": pages,
    })
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "status": "complete", "chapter_id": chapter_id, "chapter": ARCHIVE.name,
        "pages": len(pages), "archive_bytes": ARCHIVE.stat().st_size,
        "dimensions": [[page["width"], page["height"]] for page in pages],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
