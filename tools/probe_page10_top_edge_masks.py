"""Probe short top-edge crops for the clipped black balloon on saved page 10."""

from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ocr_engine.speech_bubbles import _detect_speech_bubbles_single

SAMPLE = ROOT / "translator_engine/data/benchmark_runs/page10_manual_extension_20261112/original_english.webp"
OUTPUT = SAMPLE.parent / "top_edge_crop_probe.json"


def main():
    if OUTPUT.exists():
        raise FileExistsError(OUTPUT)
    image_bytes = SAMPLE.read_bytes()
    image = cv2.imread(str(SAMPLE), cv2.IMREAD_COLOR)
    if image is None:
        raise FileNotFoundError(SAMPLE)
    results = []
    for crop_height in (500, 700, 900, 1100, 1300, 1600):
        crop = np.ascontiguousarray(image[:crop_height])
        started = time.perf_counter()
        masks = _detect_speech_bubbles_single(crop, 0.25)
        results.append({
            "crop_height": crop_height,
            "seconds": round(time.perf_counter() - started, 3),
            "masks": [{"bbox": list(mask.bbox), "confidence": round(mask.confidence, 4)} for mask in masks],
        })
    report = {
        "source_sha256": hashlib.sha256(image_bytes).hexdigest(),
        "probe_scope": "bubble segmenter only on alternate top-edge crop heights; no OCR or translation",
        "results": results,
    }
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
