"""Fetch a public Long Strip sample for local, read-only OCR validation."""
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
PAGE_URL = "https://manhuaplus.org/manga/spare-me-great-lord/chapter-836"
CHAPTER_ID = "103463"
OUT_DIR = ROOT / "ocr_test_results/site_web_samples_20261114/spare_me_great_lord"
ARCHIVE = OUT_DIR / "spare_me_great_lord_chapter836.cbz"
MANIFEST = ROOT / "ocr_test_results/chapter_index.json"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; MangaArabicOCR-QA/1.0)",
    "Accept": "application/json, image/webp, */*",
}
IMAGE_SUFFIXES = {"WEBP": ".webp", "JPEG": ".jpg", "PNG": ".png"}


def get(url: str, data: bytes | None = None):
    request = Request(url, data=data, headers=HEADERS,
                      method="POST" if data is not None else "GET")
    with urlopen(request, timeout=45) as response:
        if response.status != 200:
            raise RuntimeError(f"HTTP {response.status}")
        return response.read(), response.headers.get("Content-Type", "")


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    html, _ = get(PAGE_URL)
    decoded_html = html.decode("utf-8", "replace")
    if "CHAPTER_ID = " + CHAPTER_ID not in decoded_html or "Chapter 836" not in decoded_html:
        raise RuntimeError("The public page title or chapter ID did not match.")

    raw, _ = get("https://manhuaplus.org/ajax/image/list/chap/" + CHAPTER_ID, b"")
    response = json.loads(raw.decode("utf-8"))
    if response.get("status") is not True or not isinstance(response.get("html"), str):
        raise RuntimeError("Unexpected public page-list response.")
    pairs = sorted((int(i), "https:" + url if url.startswith("//") else url)
                   for i, url in re.findall(
        r'data-index="([0-9]+)"[^>]*>[ \t\r\n]*<a href="([^"]+)"', response["html"]))
    pairs = [(index, url) for index, url in pairs if url.strip().casefold() not in {"", "undefined", "null"}]
    if (not pairs or pairs[0][0] not in {0, 1}
            or [index for index, _ in pairs] != list(range(pairs[0][0], pairs[0][0] + len(pairs)))):
        raise RuntimeError("Page indexes are incomplete or duplicated.")
    hosts = {urlparse(url).hostname for _, url in pairs}
    schemes = {urlparse(url).scheme or "protocol-relative" for _, url in pairs}
    suffixes = {Path(urlparse(url).path).suffix.lower() for _, url in pairs}
    if any(urlparse(url).scheme not in {"https", ""} for _, url in pairs):
        raise RuntimeError(f"The page list contains an unsafe URL scheme: {sorted(schemes)}")
    if hosts != {"cdn.manhuaplus.cc"} or suffixes != {".webp"}:
        raise RuntimeError(f"Unexpected page-listed CDN metadata: hosts={sorted(map(str, hosts))} formats={sorted(suffixes)}")

    pages = []
    with zipfile.ZipFile(ARCHIVE, "w", compression=zipfile.ZIP_STORED) as archive:
        for index, url in pairs:
            data, content_type = get(url)
            with Image.open(BytesIO(data)) as image:
                image_format, (width, height) = image.format, image.size
                image.verify()
            suffix = IMAGE_SUFFIXES.get(image_format)
            if suffix is None or width <= 0 or height <= 0:
                parsed = urlparse(url)
                raise RuntimeError(
                    f"Unsupported or invalid image at page {index}: "
                    f"format={image_format} size={width}x{height} host={parsed.hostname}."
                )
            member = f"page_{index + 1:03d}{suffix}"
            archive.writestr(member, data)
            pages.append({
                "page": index + 1, "member": member, "bytes": len(data),
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
    manifest.setdefault("archives", [])
    manifest["archives"] = [
        item for item in manifest["archives"] if item.get("chapter") != ARCHIVE.name
    ]
    manifest["archives"].append({
        "chapter": ARCHIVE.name, "status": "indexed", "archive_bytes": ARCHIVE.stat().st_size,
        "archive_sha256": hashlib.sha256(ARCHIVE.read_bytes()).hexdigest(),
        "page_count": len(pages),
        "formats": {suffix: sum(Path(page["member"]).suffix == suffix for page in pages)
                    for suffix in sorted({Path(page["member"]).suffix for page in pages})},
        "source_page": PAGE_URL,
        "source_origin": "Public ManhuaPlus chapter page and its listed CDN",
        "story_series": "Spare Me, Great Lord!", "chapter_label": "Chapter 836",
        "vertical_layout_basis": "Series is listed Long Strip; page order is the chapter reader sequence. Some in-strip panels may be landscape.",
        "pages": pages,
    })
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "status": "complete", "chapter": ARCHIVE.name, "pages": len(pages),
        "archive_bytes": ARCHIVE.stat().st_size,
        "dimensions": [[page["width"], page["height"]] for page in pages],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
