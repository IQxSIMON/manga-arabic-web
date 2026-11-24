"""Re-run speech-only OCR on pages with visually confirmed surplus masks."""

from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ocr_engine import LocalRapidOCR
from tools.score_magic_emperor_retry_full_run import distance, tokens

BASE = ROOT / "translator_engine/data/benchmark_runs/magic_emperor_920_sample"
OUTPUT = BASE / "chapter920_current_false_mask_ocr_validation.json"
FALSE_MASKS = {
    1: (0, 8418, 208, 8504),
    2: (276, 4480, 435, 4583),
}


def _overlap_smaller(first, second):
    x1, y1, x2, y2 = first
    a1, b1, a2, b2 = second
    intersection = max(0, min(x2, a2) - max(x1, a1)) * max(0, min(y2, b2) - max(y1, b1))
    area_first = max(1, (x2 - x1) * (y2 - y1))
    area_second = max(1, (a2 - a1) * (b2 - b1))
    return intersection / min(area_first, area_second)


def main():
    if OUTPUT.exists():
        raise FileExistsError(OUTPUT)
    audit = json.loads((BASE / "chapter920_pages1_2_independent_review.json").read_text(encoding="utf-8"))
    pages = []
    engine = LocalRapidOCR()
    for item in audit["pages"]:
        index = item["page"]
        path = BASE / f"chapter920_page{index}.webp"
        image_bytes = path.read_bytes()
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
        started = time.perf_counter()
        current = engine.extract(image_bytes, include_external=False)
        elapsed = time.perf_counter() - started
        references = item["manual_reference"]
        regions = current.get("regions", [])
        rows = []
        total_errors = total_words = 0
        for ref, region in zip(references, regions):
            errors, details = distance(tokens(ref), tokens(region.get("text", "")))
            total_errors += errors
            total_words += len(tokens(ref))
            rows.append({
                "reference": ref,
                "ocr": region.get("text"),
                "word_errors": errors,
                "error_details": details,
                "bubble_bbox": region.get("bubble_bbox"),
            })
        false_mask = FALSE_MASKS[index]
        false_mask_regions = []
        for region_index, region in enumerate(regions, 1):
            box = region.get("bubble_bbox")
            if box and _overlap_smaller(box, false_mask) >= 0.25:
                false_mask_regions.append(region_index)
        pages.append({
            "page": index,
            "source_sha256": hashlib.sha256(image_bytes).hexdigest(),
            "elapsed_seconds": round(elapsed, 3),
            "manual_dialogue_bubbles": len(references),
            "ocr_speech_regions": len(regions),
            "speech_word_count": total_words,
            "word_errors": total_errors,
            "wer": round(total_errors / max(1, total_words), 6),
            "known_false_mask_bbox": list(false_mask),
            "false_mask_generated_ocr_regions": false_mask_regions,
            "regions": rows,
        })
    result = {
        "method": "current speech-only OCR; no Index, translation, or external lettering",
        "pages": pages,
        "aggregate": {
            "manual_bubbles": sum(p["manual_dialogue_bubbles"] for p in pages),
            "ocr_regions": sum(p["ocr_speech_regions"] for p in pages),
            "reference_words": sum(p["speech_word_count"] for p in pages),
            "word_errors": sum(p["word_errors"] for p in pages),
        },
        "limitations": ["Two pages from one work; not a chapter-wide or multi-work estimate."],
    }
    result["aggregate"]["wer"] = round(
        result["aggregate"]["word_errors"] / max(1, result["aggregate"]["reference_words"]), 6
    )
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"aggregate": result["aggregate"], "pages": [
        {k: v for k, v in page.items() if k != "regions"} for page in pages
    ], "saved": str(OUTPUT)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
