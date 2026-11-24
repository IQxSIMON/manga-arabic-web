"""Compare speech-mask strip overlap settings on saved page-10 gold input."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cv2
import numpy as np

from ocr_engine.speech_bubbles import SpeechBubble, _detect_speech_bubbles_single, deduplicate_bubbles

IMAGE = ROOT / "translator_engine/data/benchmark_runs/page10_manual_extension_20261112/original_english.webp"


def run(page: np.ndarray, overlap: int) -> dict:
    height, width = page.shape[:2]
    strip_height = min(2400, max(1200, width * 2))
    last_start = max(0, height - strip_height)
    starts = list(range(0, last_start + 1, strip_height - overlap))
    if not starts or starts[-1] != last_start:
        starts.append(last_start)
    proposals = []
    start_time = time.perf_counter()
    for top in starts:
        bottom = min(height, top + strip_height)
        for bubble in _detect_speech_bubbles_single(page[top:bottom], 0.25):
            x1, y1, x2, y2 = bubble.bbox
            proposals.append(SpeechBubble((x1, y1 + top, x2, y2 + top), bubble.safe_mask, bubble.confidence))
    bubbles = deduplicate_bubbles(proposals)
    elapsed = time.perf_counter() - start_time
    return {
        "overlap": overlap,
        "strip_count": len(starts),
        "segmenter_seconds": round(elapsed, 3),
        "bubble_count": len(bubbles),
        "bubbles": [{"bbox": list(item.bbox), "confidence": round(item.confidence, 4)} for item in bubbles],
    }


def main() -> None:
    page = cv2.imread(str(IMAGE), cv2.IMREAD_COLOR)
    if page is None:
        raise FileNotFoundError(IMAGE)
    results = [run(page, overlap) for overlap in (160, 500)]
    contexts = []
    for padding in (350, 500, 700, 900):
        top, bottom = max(0, 3038 - padding), min(page.shape[0], 3038 + padding)
        started = time.perf_counter()
        bubbles = _detect_speech_bubbles_single(page[top:bottom], 0.25)
        contexts.append({
            "padding": padding,
            "crop_y": [top, bottom],
            "seconds": round(time.perf_counter() - started, 3),
            "bubbles": [{
                "bbox": [x1, y1 + top, x2, y2 + top],
                "confidence": round(item.confidence, 4),
            } for item in bubbles for x1, y1, x2, y2 in [item.bbox]],
        })
    print(json.dumps({"overlap_runs": results, "context_retries": contexts}, indent=2))


if __name__ == "__main__":
    main()
