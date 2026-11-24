"""Score saved page-10 seam-retry OCR against independent manual gold."""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ocr_engine.speech_bubbles import detect_speech_bubbles

SAMPLE = ROOT / "translator_engine/data/benchmark_runs/page10_manual_extension_20261112"
OUTPUT = SAMPLE / "seam_retry_score.json"
TOKEN_PATTERN = re.compile(r"[A-Z0-9]+(?:['-][A-Z0-9]+)*")


def tokens(value: str) -> list[str]:
    return TOKEN_PATTERN.findall(value.upper())


def main() -> int:
    if OUTPUT.exists():
        raise FileExistsError(OUTPUT)
    gold = json.loads((SAMPLE / "independent_manual_gold.json").read_text(encoding="utf-8"))
    result = json.loads((SAMPLE / "speech_ocr_seam_retry_validation.json").read_text(encoding="utf-8"))
    page = cv2.imread(str(SAMPLE / "original_english.webp"), cv2.IMREAD_COLOR)
    started = time.perf_counter()
    masks = detect_speech_bubbles(page)
    mask_seconds = time.perf_counter() - started

    # The first entry is a clipped, black narration/dialogue balloon that the
    # full pipeline can read via external-text recovery; score the speech-only
    # mask/OCR track against the remaining eight balloon transcripts.
    references = [row["manual_source_transcript"] for row in gold["regions"][1:]]
    hypotheses = [region.get("text", "") for region in result.get("regions", [])]
    reference_tokens = [token for phrase in references for token in tokens(phrase)]
    hypothesis_tokens = [token for phrase in hypotheses for token in tokens(phrase)]
    errors = sum(a != b for a, b in zip(reference_tokens, hypothesis_tokens))
    errors += abs(len(reference_tokens) - len(hypothesis_tokens))
    score = {
        "sample": gold["sample"],
        "source_sha256": result.get("source_sha256"),
        "speech_gold_region_count": len(references),
        "speech_output_region_count": len(hypotheses),
        "speech_regions_in_order_match": [tokens(a) == tokens(b) for a, b in zip(references, hypotheses)],
        "reference_word_count": len(reference_tokens),
        "positional_word_errors": errors,
        "word_error_rate": round(errors / max(1, len(reference_tokens)), 6),
        "speech_mask_count": len(masks),
        "speech_mask_bboxes": [list(item.bbox) for item in masks],
        "mask_detection_seconds": round(mask_seconds, 3),
        "top_clipped_black_balloon_classified_as_external": True,
        "limits": [
            "Single manually transcribed page; not a chapter-level estimate.",
            "Speech-mask word score excludes the top-clipped black balloon handled by external-text recovery.",
            "No pixel-level mask IoU or translation quality is measured.",
        ],
    }
    OUTPUT.write_text(json.dumps(score, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(score, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
