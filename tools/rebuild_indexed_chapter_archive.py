"""Rebuild one ignored CBZ from the page URLs in the verified OCR index.

Downloaded images are accepted only when their SHA-256 matches the index. The
source files and chapter index are never modified.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen
import zipfile


ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "ocr_test_results" / "chapter_index.json"
OUTPUT_DIR = ROOT / "ocr_test_results" / "source_archives"
ALLOWED_HOSTS = {"cdn.manhuaplus.cc", "manhuaplus.cc", "manhuaplus.org"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("chapter", help="Exact indexed archive filename")
    args = parser.parse_args()

    manifest = json.loads(INDEX.read_text(encoding="utf-8"))
    chapter = next((entry for entry in manifest["archives"]
                    if entry.get("chapter") == args.chapter
                    and entry.get("status") == "indexed"), None)
    if chapter is None:
        raise SystemExit("Chapter is not present as indexed in chapter_index.json.")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    archive_path = OUTPUT_DIR / args.chapter
    temporary_path = archive_path.with_suffix(archive_path.suffix + ".partial")
    if temporary_path.exists():
        temporary_path.unlink()

    try:
        with zipfile.ZipFile(temporary_path, "w", compression=zipfile.ZIP_STORED) as archive:
            for page in chapter["pages"]:
                parsed = urlparse(page.get("source_url", ""))
                if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
                    raise SystemExit(f"Refusing untrusted source URL for page {page['page']}.")
                request = Request(
                    page["source_url"],
                    headers={"User-Agent": "MangaArabicOCR-Research/1.0"},
                )
                with urlopen(request, timeout=45) as response:
                    data = response.read()
                    content_type = response.headers.get_content_type()
                digest = hashlib.sha256(data).hexdigest()
                if digest != page.get("sha256"):
                    raise SystemExit(
                        f"SHA-256 mismatch on page {page['page']}; refusing archive creation."
                    )
                if content_type not in {"image/webp", "image/jpeg", "image/png"}:
                    raise SystemExit(
                        f"Unexpected content type {content_type!r} on page {page['page']}."
                    )
                archive.writestr(page["member"], data)
                print(json.dumps({
                    "page": page["page"], "bytes": len(data),
                    "sha256_verified": True, "content_type": content_type,
                }), flush=True)
        temporary_path.replace(archive_path)
    finally:
        temporary_path.unlink(missing_ok=True)

    print(json.dumps({
        "archive": str(archive_path),
        "pages": chapter["page_count"],
        "archive_sha256_matches_original": False,
        "note": "ZIP container bytes differ; each extracted image matches its indexed SHA-256.",
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
