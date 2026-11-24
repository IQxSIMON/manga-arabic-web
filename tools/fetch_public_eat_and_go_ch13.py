"""Fetch a publicly listed, vertically verified ManhuaPlus chapter for local OCR QA."""
from __future__ import annotations
import hashlib, json, re, zipfile
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from PIL import Image

ROOT = Path("D:/MangaArabic/manga-arabic-web")
PAGE_URL = "https://manhuaplus.org/manga/eat-and-go/chapter-13"
CHAPTER_ID = "80524"
OUT_DIR = ROOT / "ocr_test_results/site_web_samples_20261114/eat_and_go"
ARCHIVE = OUT_DIR / "eat_and_go_chapter13.cbz"
MANIFEST = ROOT / "ocr_test_results/chapter_index.json"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; MangaArabicOCR-QA/1.0)",
           "Accept": "application/json, image/webp, */*"}

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
    if CHAPTER_ID not in decoded_html or "Chapter 13" not in decoded_html:
        raise RuntimeError("Public chapter ID did not match the source page.")
    raw, _ = get("https://manhuaplus.org/ajax/image/list/chap/" + CHAPTER_ID, b"")
    response = json.loads(raw.decode("utf-8"))
    if response.get("status") is not True or not isinstance(response.get("html"), str):
        raise RuntimeError("Unexpected public page-list response.")
    pairs = sorted((int(i), u) for i, u in re.findall(
        r'data-index="([0-9]+)"[^>]*>[ \t\r\n]*<a href="([^"]+)"', response["html"]))
    if not pairs or [i for i, _ in pairs] != list(range(len(pairs))):
        raise RuntimeError("Page indexes are incomplete or duplicated.")
    if any(urlparse(u).scheme != "https" or urlparse(u).hostname != "cdn.manhuaplus.cc"
           or not urlparse(u).path.lower().endswith(".webp") for _, u in pairs):
        raise RuntimeError("Image URL is outside the page-listed public WebP CDN.")
    pages = []
    with zipfile.ZipFile(ARCHIVE, "w", compression=zipfile.ZIP_STORED) as archive:
        for index, url in pairs:
            data, content_type = get(url)
            with Image.open(BytesIO(data)) as im:
                fmt, (width, height) = im.format, im.size
                im.verify()
            if fmt != "WEBP" or width <= 0 or height <= width:
                raise RuntimeError(f"Non-vertical or invalid image at page {index+1}.")
            member = f"page_{index+1:03d}.webp"
            archive.writestr(member, data)
            pages.append({"page": index+1, "member": member, "bytes": len(data),
                          "sha256": hashlib.sha256(data).hexdigest(), "width": width,
                          "height": height, "format": "webp", "source_url": url,
                          "content_type": content_type})
    with zipfile.ZipFile(ARCHIVE) as archive:
        bad = archive.testzip()
        if bad:
            raise RuntimeError(f"CBZ integrity check failed: {bad}")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.is_file() else {
        "schema": 1, "source_unchanged": True,
        "supported_raster_suffixes": [".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"],
        "archives": []}
    manifest.setdefault("archives", [])
    manifest["archives"] = [x for x in manifest["archives"] if x.get("chapter") != ARCHIVE.name]
    manifest["archives"].append({
        "chapter": ARCHIVE.name, "status": "indexed", "archive_bytes": ARCHIVE.stat().st_size,
        "archive_sha256": hashlib.sha256(ARCHIVE.read_bytes()).hexdigest(),
        "page_count": len(pages), "formats": {".webp": len(pages)}, "source_page": PAGE_URL,
        "source_origin": "Public ManhuaPlus chapter page and its listed CDN",
        "story_series": "Eat and Go", "chapter_label": "Chapter 13",
        "vertical_layout_basis": "Long Strip listing and verified vertical image aspect ratios",
        "pages": pages})
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": "complete", "chapter": ARCHIVE.name, "pages": len(pages),
                      "archive_bytes": ARCHIVE.stat().st_size,
                      "dimensions": [[p["width"], p["height"]] for p in pages]},
                     ensure_ascii=False))

if __name__ == "__main__":
    main()
