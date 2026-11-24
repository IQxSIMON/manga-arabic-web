"""Compare original strip segmentation with the targeted seam-retry path."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ocr_engine import speech_bubbles
from ocr_engine.speech_bubbles import SpeechBubble, deduplicate_bubbles

SAMPLE = ROOT / "translator_engine/data/benchmark_runs/page10_manual_extension_20261112/original_english.webp"
OUTPUT = SAMPLE.parent / "seam_retry_cost_comparison.json"


def clear_crop_cache():
    with speech_bubbles._bubble_cache_lock:
        speech_bubbles._bubble_cache.clear()
        speech_bubbles._bubble_cache_bytes = 0


def original_strip_path(page):
    height, width = page.shape[:2]
    strip_height = min(2400, max(1200, width * 2))
    overlap = min(160, strip_height // 8)
    last_start = max(0, height - strip_height)
    starts = list(range(0, last_start + 1, strip_height - overlap))
    if not starts or starts[-1] != last_start:
        starts.append(last_start)
    candidates = []
    for top in starts:
        bottom = min(height, top + strip_height)
        for bubble in speech_bubbles._detect_speech_bubbles_single(page[top:bottom], 0.25):
            x1, y1, x2, y2 = bubble.bbox
            candidates.append(SpeechBubble(
                (x1, y1 + top, x2, y2 + top), bubble.safe_mask, bubble.confidence,
            ))
    return deduplicate_bubbles(candidates)


def main():
    if OUTPUT.exists():
        raise FileExistsError(OUTPUT)
    page = cv2.imread(str(SAMPLE), cv2.IMREAD_COLOR)
    if page is None:
        raise FileNotFoundError(SAMPLE)

    started = time.perf_counter()
    speech_bubbles._session()
    model_ready_seconds = time.perf_counter() - started

    clear_crop_cache()
    started = time.perf_counter()
    baseline = original_strip_path(page)
    baseline_seconds = time.perf_counter() - started

    clear_crop_cache()
    started = time.perf_counter()
    current = speech_bubbles.detect_speech_bubbles(page)
    current_seconds = time.perf_counter() - started

    result = {
        "page": "saved page 10 vertical strip",
        "model_session_warmup_seconds_excluded_from_both_paths": round(model_ready_seconds, 3),
        "baseline": {
            "path": "original 160px strip overlap, no seam-context retry",
            "seconds": round(baseline_seconds, 3),
            "mask_count": len(baseline),
            "seam_balloon_box": next((list(b.bbox) for b in baseline if b.bbox[1] < 3100 < b.bbox[3]), None),
        },
        "current": {
            "path": "same strips plus targeted uncertain seam-context retry",
            "seconds": round(current_seconds, 3),
            "mask_count": len(current),
            "seam_balloon_box": next((list(b.bbox) for b in current if b.bbox[1] < 3100 < b.bbox[3]), None),
        },
        "delta_seconds": round(current_seconds - baseline_seconds, 3),
        "delta_percent": round((current_seconds / max(0.001, baseline_seconds) - 1) * 100, 2),
        "limitations": [
            "One process and one run per path after loading the model; CPU timing varies.",
            "Measures bubble segmentation only, not OCR, translation, or chapter time.",
        ],
    }
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
