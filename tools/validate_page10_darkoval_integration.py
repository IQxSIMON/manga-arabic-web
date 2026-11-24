"""Validate current bubble fallback through full OCR on one saved gold page."""

from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ocr_engine import LocalRapidOCR
from tools.evaluate_ocr_visual_gold import _tokens, score

SAMPLE = ROOT / "translator_engine/data/benchmark_runs/page10_manual_extension_20261112"
OCR_OUTPUT = SAMPLE / "full_ocr_darkoval_fallback_20261112.json"
SCORE_OUTPUT = SAMPLE / "full_darkoval_visual_gold_score_20261112.json"


def main() -> None:
    if OCR_OUTPUT.exists() or SCORE_OUTPUT.exists():
        raise FileExistsError("Refusing to overwrite a previous dark-oval validation")
    source = SAMPLE / "original_english.webp"
    image_bytes = source.read_bytes()
    started = time.perf_counter()
    current = LocalRapidOCR().extract(image_bytes, include_external=True)
    elapsed = time.perf_counter() - started
    current.update({
        "source_sha256": hashlib.sha256(image_bytes).hexdigest(),
        "validation": "full OCR integration with dark oval bubble fallback",
    })
    manual = json.loads((SAMPLE / "independent_manual_gold.json").read_text(encoding="utf-8"))
    gold = {"cases": [
        {
            "page": "page_10",
            "region_index": index,
            "reference": region["manual_source_transcript"],
            "class": "external" if region.get("full_pipeline_region_type") == "external_text" else "bubble",
        }
        for index, region in enumerate(manual["regions"], 1)
    ]}
    report = {"results": [{"page": "page_10", "regions": current.get("regions", [])}]}
    scored = score(gold, report, report, anchor=report)
    rows = [{
        "region_index": row["region_index"],
        "reference": row["reference"],
        "recognized": row["current"],
        "region_type": current["regions"][row["region_index"] - 1].get("region_type"),
        "word_errors": row["current_word_errors"],
        "wer": row["current_wer"],
    } for row in scored["rows"]]
    summary = {
        "scope": "single saved page; full OCR only; no translation or Index",
        "elapsed_seconds": round(elapsed, 3),
        "region_count": len(current.get("regions", [])),
        "speech_bubble_regions": sum(r.get("region_type") == "speech_bubble" for r in current.get("regions", [])),
        "external_text_regions": sum(r.get("region_type") == "external_text" for r in current.get("regions", [])),
        "reference_word_count": scored["reference_word_count"],
        "word_errors": scored["current_word_errors"],
        "word_error_rate": scored["current_wer"],
        "regions": rows,
        "limits": ["Single page only; no mask-boundary IoU or chapter-wide score."],
    }
    OCR_OUTPUT.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    SCORE_OUTPUT.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
