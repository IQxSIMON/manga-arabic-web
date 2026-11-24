"""Score four updated speech-only OCR runs against existing independent gold."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from score_magic_emperor_retry_full_run import distance, tokens

DATA = ROOT / "translator_engine" / "data" / "benchmark_runs"
RUN_DIR = DATA / "retry_regression_20261112"


def main() -> int:
    pages = []
    for page in (2, 3, 5, 14):
        gold_path = next(DATA.rglob(f"page{page:02d}_independent_manual_gold_20261112.json"))
        gold = json.loads(gold_path.read_text(encoding="utf-8"))
        manual_regions = [row for row in gold["regions"] if tokens(row["manual_source_transcript"])]
        current = json.loads((RUN_DIR / f"page_{page:02d}_speech_ocr.json").read_text(encoding="utf-8"))
        baseline = [row.get("ocr_text_after_correction", row.get("ocr_text", "")) for row in manual_regions]
        current_regions = current.get("regions", [])
        if len(manual_regions) != len(current_regions):
            raise AssertionError(
                f"Page {page}: {len(manual_regions)} word-bearing gold regions, "
                f"{len(current_regions)} current OCR regions"
            )
        rows = []
        page_ref_words = page_base_errors = page_current_errors = 0
        for number, (gold_region, current_region, old_text) in enumerate(
            zip(manual_regions, current_regions, baseline), 1
        ):
            reference = gold_region["manual_source_transcript"]
            ref_tokens = tokens(reference)
            current_tokens = tokens(current_region["text"])
            old_tokens = tokens(old_text)
            current_errors, current_edits = distance(ref_tokens, current_tokens)
            base_errors, base_edits = distance(ref_tokens, old_tokens)
            page_ref_words += len(ref_tokens)
            page_base_errors += base_errors
            page_current_errors += current_errors
            rows.append({
                "region": number,
                "reference": reference,
                "previous_saved_ocr": old_text,
                "current_ocr": current_region["text"],
                "current_confidence": current_region["confidence"],
                "baseline_word_errors": base_errors,
                "current_word_errors": current_errors,
                "current_error_details": current_edits,
            })
        pages.append({
            "page": page,
            "manual_word_bearing_bubbles": len(manual_regions),
            "current_detected_regions": len(current_regions),
            "reference_words": page_ref_words,
            "baseline_word_errors": page_base_errors,
            "baseline_wer": round(page_base_errors / max(1, page_ref_words), 6),
            "current_word_errors": page_current_errors,
            "current_wer": round(page_current_errors / max(1, page_ref_words), 6),
            "elapsed_seconds": current.get("elapsed_seconds"),
            "regions": rows,
        })
    total_words = sum(page["reference_words"] for page in pages)
    total_base_errors = sum(page["baseline_word_errors"] for page in pages)
    total_current_errors = sum(page["current_word_errors"] for page in pages)
    result = {
        "mode": "speech-only full-page OCR regression against independent manual transcripts; punctuation ignored",
        "pages": pages,
        "aggregate": {
            "pages": len(pages),
            "manual_word_bearing_bubbles": sum(p["manual_word_bearing_bubbles"] for p in pages),
            "current_detected_regions": sum(p["current_detected_regions"] for p in pages),
            "reference_words": total_words,
            "baseline_word_errors": total_base_errors,
            "baseline_wer": round(total_base_errors / max(1, total_words), 6),
            "current_word_errors": total_current_errors,
            "current_wer": round(total_current_errors / max(1, total_words), 6),
        },
        "limits": [
            "Four pages from one work; not a population estimate.",
            "Only word-bearing speech regions are scored; pause-only balloons and external lettering are excluded.",
            "Existing prior OCR text in the manual-gold files is the comparison baseline, not a controlled simultaneous run.",
            "No translation was invoked.",
        ],
    }
    output = RUN_DIR / "saved_pages_retry_regression_score.json"
    if output.exists():
        raise FileExistsError(output)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result["aggregate"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
