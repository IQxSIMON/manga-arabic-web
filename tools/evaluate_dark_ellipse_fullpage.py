"""Cross-check dark-oval fallback candidates against whole saved long pages."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import cv2
import numpy as np

from evaluate_top_dark_ellipse_candidates import candidates

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "translator_engine/data/benchmark_runs/page10_manual_extension_20261112"
OUTPUT = RUN / "top_dark_ellipse_fullpage_probe_v2.json"
ARCHIVE = ROOT / "QUALITY_REVIEW_SAMPLES.zip"
SAMPLE_DIR = ROOT / "translator_engine/data/benchmark_runs/magic_emperor_920_sample"


def load_page(name: str, data: bytes) -> np.ndarray:
    image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Could not decode saved page: {name}")
    return image


def main() -> None:
    if OUTPUT.exists():
        raise FileExistsError(f"Refusing to overwrite {OUTPUT}")
    pages = []
    for number in range(3):
        path = SAMPLE_DIR / f"chapter920_page{number}.webp"
        pages.append((f"chapter920_page{number}", cv2.imread(str(path))))
    with zipfile.ZipFile(ARCHIVE) as archive:
        for number in (2, 3, 5, 10, 14):
            raw = archive.read(f"page_{number:02d}/original_english.webp")
            pages.append((f"visual_gold_page_{number:02d}", load_page(str(number), raw)))

    report = {"threshold": "grayscale < 30; candidate area >= 20000", "pages": []}
    for name, image in pages:
        if image is None:
            raise ValueError(f"Missing saved page: {name}")
        found = candidates(image)
        report["pages"].append({
            "page": name,
            "image_size": [int(image.shape[1]), int(image.shape[0])],
            "candidate_count": len(found),
            "candidates": found,
        })
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
