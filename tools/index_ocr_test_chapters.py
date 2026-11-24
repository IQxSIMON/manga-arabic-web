"""Index configured ZIP-based CBZ chapters without altering original archives."""

from __future__ import annotations

import hashlib
import json
import re
import zipfile
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUTS = [
    Path(r"C:\Users\BMW1975\Downloads") / name
    for name in (
        "10.cbz", "11.cbz", "931_Chapter_930.cbz",
        "932_Chapter_931.cbz", "933_Chapter_932.cbz", "12.cbz",
    )
]
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def natural_key(value: str):
    return [int(part) if part.isdigit() else part.casefold()
            for part in re.split(r"(\d+)", value)]


def main() -> None:
    output = ROOT / "ocr_test_results"
    output.mkdir(parents=True, exist_ok=True)
    chapters = []
    for archive in DEFAULT_INPUTS:
        if not archive.is_file():
            chapters.append({"chapter": archive.name, "status": "missing"})
            continue
        with zipfile.ZipFile(archive) as zf:
            members = [item for item in zf.infolist()
                       if Path(item.filename).suffix.casefold() in IMAGE_SUFFIXES]
            members.sort(key=lambda item: natural_key(item.filename))
            pages = []
            for index, item in enumerate(members, 1):
                digest = hashlib.sha256()
                with zf.open(item) as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(block)
                pages.append({"page": index, "member": item.filename,
                              "bytes": item.file_size, "sha256": digest.hexdigest()})
            chapters.append({"chapter": archive.name, "status": "indexed",
                             "archive_bytes": archive.stat().st_size,
                             "archive_sha256": file_sha256(archive),
                             "page_count": len(pages),
                             "formats": dict(Counter(Path(x.filename).suffix.casefold()
                                                     for x in members)),
                             "pages": pages})
    manifest = {"schema": 1, "source_unchanged": True,
                "supported_raster_suffixes": sorted(IMAGE_SUFFIXES),
                "archives": chapters}
    target = output / "chapter_index.json"
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"manifest": str(target), "chapters": [
        {k: chapter.get(k) for k in ("chapter", "status", "page_count", "formats")}
        for chapter in chapters]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
