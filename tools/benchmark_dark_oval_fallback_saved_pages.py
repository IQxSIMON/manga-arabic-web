"""Measure dark-oval fallback impact on saved vertical comic pages only."""

from __future__ import annotations

import json
import time
import zipfile
import argparse
from pathlib import Path
import sys

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ocr_engine.speech_bubbles import detect_speech_bubbles, _detect_dark_oval_bubbles

RUN = ROOT / "translator_engine/data/benchmark_runs/page10_manual_extension_20261112"
SAMPLE_DIR = ROOT / "translator_engine/data/benchmark_runs/magic_emperor_920_sample"
ARCHIVE = ROOT / "QUALITY_REVIEW_SAMPLES.zip"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-name", default="dark_oval_production_detection_benchmark_20261112.json")
    args = parser.parse_args()
    output_name = Path(args.output_name)
    if output_name.name != str(output_name) or output_name.suffix.lower() != ".json":
        raise ValueError("Output name must be a simple JSON filename.")
    output = RUN / output_name
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite {output}")
    images: list[tuple[str, np.ndarray]] = []
    for number in range(3):
        path = SAMPLE_DIR / f"chapter920_page{number}.webp"
        image = cv2.imread(str(path))
        if image is None:
            raise FileNotFoundError(path)
        images.append((f"chapter920_page{number}", image))
    with zipfile.ZipFile(ARCHIVE) as archive:
        for number in (2, 3, 5, 10, 14):
            raw = archive.read(f"page_{number:02d}/original_english.webp")
            image = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
            if image is None:
                raise ValueError(f"Could not decode saved page {number}")
            images.append((f"visual_gold_page_{number:02d}", image))

    report = {
        "date": "2026-11-12",
        "scope": "bubble detection only; no OCR, translation, or Index",
        "pages": [],
    }
    for name, image in images:
        start = time.perf_counter()
        current = detect_speech_bubbles(image)
        total_seconds = time.perf_counter() - start
        fallback_start = time.perf_counter()
        dark_candidates = _detect_dark_oval_bubbles(image)
        fallback_seconds = time.perf_counter() - fallback_start
        report["pages"].append({
            "page": name,
            "size": [int(image.shape[1]), int(image.shape[0])],
            "dark_oval_candidates": [
                {"bbox": list(item.bbox), "confidence": item.confidence}
                for item in dark_candidates
            ],
            "candidate_count": len(dark_candidates),
            "production_mask_count": len(current),
            "dark_scan_seconds": round(fallback_seconds, 4),
            "cold_production_detector_seconds": round(total_seconds, 4),
            "production_boxes": [list(item.bbox) for item in current],
        })
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
