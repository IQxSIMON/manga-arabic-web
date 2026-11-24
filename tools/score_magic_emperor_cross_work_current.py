"""Score all currently saved, independently gold-reviewed sample bubbles."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from score_magic_emperor_retry_full_run import distance, tokens

SAMPLE_DIR = ROOT / "translator_engine" / "data" / "benchmark_runs" / "magic_emperor_920_sample"


def page_result(page: int, gold: list[str], output_name: str) -> dict:
    output = json.loads((SAMPLE_DIR / output_name).read_text(encoding="utf-8"))
    if len(gold) != len(output.get("regions", [])):
        raise AssertionError(f"Page {page} region count differs from visual gold")
    rows = []
    total_errors = total_words = 0
    for index, (reference, region) in enumerate(zip(gold, output["regions"]), 1):
        ref_tokens, actual_tokens = tokens(reference), tokens(region["text"])
        errors, details = distance(ref_tokens, actual_tokens)
        total_errors += errors
        total_words += len(ref_tokens)
        rows.append({
            "region": index,
            "reference": reference,
            "ocr": region["text"],
            "confidence": region["confidence"],
            "word_errors": errors,
            "error_details": details,
        })
    return {
        "page": page,
        "ocr_artifact": output_name,
        "elapsed_seconds": output.get("elapsed_seconds"),
        "visual_gold_bubbles": len(gold),
        "detected_regions": len(rows),
        "reference_words": total_words,
        "word_errors": total_errors,
        "wer": round(total_errors / max(1, total_words), 6),
        "regions": rows,
    }


def main() -> int:
    page0_audit = json.loads((SAMPLE_DIR / "chapter920_page0_independent_review.json").read_text(encoding="utf-8"))
    pages12_audit = json.loads((SAMPLE_DIR / "chapter920_pages1_2_independent_review.json").read_text(encoding="utf-8"))
    current = [
        page_result(0, [item["manual_source_transcript"] for item in page0_audit["speech_bubbles"]], "chapter920_page0_retry_full_ocr.json"),
        page_result(1, pages12_audit["pages"][0]["manual_reference"], "chapter920_page1_retry_full_ocr_v5.json"),
        page_result(2, pages12_audit["pages"][1]["manual_reference"], "chapter920_page2_retry_full_ocr_v5.json"),
    ]
    baseline = [
        page_result(0, [item["manual_source_transcript"] for item in page0_audit["speech_bubbles"]], "chapter920_page0_ocr.json"),
        page_result(1, pages12_audit["pages"][0]["manual_reference"], "chapter920_page1_ocr.json"),
        page_result(2, pages12_audit["pages"][1]["manual_reference"], "chapter920_page2_ocr.json"),
    ]
    aggregate = {}
    for name, pages in (("baseline", baseline), ("current", current)):
        words = sum(page["reference_words"] for page in pages)
        errors = sum(page["word_errors"] for page in pages)
        aggregate[name] = {
            "pages": len(pages),
            "visual_gold_bubbles": sum(page["visual_gold_bubbles"] for page in pages),
            "detected_regions": sum(page["detected_regions"] for page in pages),
            "reference_words": words,
            "word_errors": errors,
            "wer": round(errors / max(1, words), 6),
        }
    result = {
        "mode": "speech-only full-page OCR scored against independent visual transcripts; punctuation ignored",
        "baseline": baseline,
        "current": current,
        "aggregate": aggregate,
        "limits": [
            "Three strips from one second work only; not a full-chapter or population estimate.",
            "The two crop-level retries have now also been verified within full-page OCR on pages 1 and 2.",
            "External lettering is excluded from this score.",
            "No translation or Index was invoked.",
        ],
    }
    output = SAMPLE_DIR / "chapter920_current_cross_work_visual_gold_score.json"
    if output.exists():
        raise FileExistsError(output)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(aggregate, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
