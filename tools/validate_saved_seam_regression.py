"""Check current speech-mask counts on three saved long-strip pages."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ocr_engine.speech_bubbles import detect_speech_bubbles

SAMPLE = ROOT / "translator_engine/data/benchmark_runs/magic_emperor_920_sample"
OUTPUT = SAMPLE / "chapter920_seam_detector_regression.json"


def main() -> int:
    if OUTPUT.exists():
        raise FileExistsError(OUTPUT)
    pages = []
    for index, expected_count in enumerate((9, 8, 5)):
        path = SAMPLE / f"chapter920_page{index}.webp"
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if image is None:
            raise FileNotFoundError(path)
        started = time.perf_counter()
        bubbles = detect_speech_bubbles(image)
        pages.append({
            "page": index,
            "shape_hw": list(image.shape[:2]),
            "manual_word_bearing_bubbles": expected_count,
            "detected_mask_count": len(bubbles),
            "segmenter_seconds": round(time.perf_counter() - started, 3),
            "bboxes": [list(bubble.bbox) for bubble in bubbles],
        })
    OUTPUT.write_text(json.dumps({"pages": pages}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"pages": pages, "saved": str(OUTPUT)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
