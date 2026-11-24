"""Score current full-page speech OCR against the independent saved gold."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = ROOT / "translator_engine" / "data" / "benchmark_runs" / "magic_emperor_920_sample"


def tokens(text: str) -> list[str]:
    return [token.casefold() for token in re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", text)]


def distance(reference: list[str], hypothesis: list[str]) -> tuple[int, list[dict]]:
    rows, cols = len(reference) + 1, len(hypothesis) + 1
    dp = [[0] * cols for _ in range(rows)]
    back = [[None] * cols for _ in range(rows)]
    for i in range(1, rows):
        dp[i][0], back[i][0] = i, "delete"
    for j in range(1, cols):
        dp[0][j], back[0][j] = j, "insert"
    for i in range(1, rows):
        for j in range(1, cols):
            options = [
                (dp[i-1][j] + 1, "delete"),
                (dp[i][j-1] + 1, "insert"),
                (dp[i-1][j-1] + (reference[i-1] != hypothesis[j-1]),
                 "match" if reference[i-1] == hypothesis[j-1] else "substitute"),
            ]
            dp[i][j], back[i][j] = min(options, key=lambda item: item[0])
    errors = []
    i, j = len(reference), len(hypothesis)
    while i or j:
        operation = back[i][j]
        if operation == "match":
            i, j = i-1, j-1
        elif operation == "substitute":
            errors.append({"type": operation, "reference": reference[i-1], "ocr": hypothesis[j-1]})
            i, j = i-1, j-1
        elif operation == "delete":
            errors.append({"type": operation, "reference": reference[i-1], "ocr": None})
            i -= 1
        else:
            errors.append({"type": operation, "reference": None, "ocr": hypothesis[j-1]})
            j -= 1
    errors.reverse()
    return dp[-1][-1], errors


def score(output_name: str, references: list[dict]) -> dict:
    output = json.loads((SAMPLE_DIR / output_name).read_text(encoding="utf-8"))
    if len(output.get("regions", [])) != len(references):
        raise AssertionError(f"Region-count mismatch for {output_name}")
    pages = []
    error_count = token_count = 0
    for index, (gold, region) in enumerate(zip(references, output["regions"]), 1):
        ref_tokens, ocr_tokens = tokens(gold), tokens(region["text"])
        errors, alignment = distance(ref_tokens, ocr_tokens)
        token_count += len(ref_tokens)
        error_count += errors
        pages.append({
            "region": index,
            "reference": gold,
            "ocr": region["text"],
            "confidence": region["confidence"],
            "word_errors": errors,
            "error_details": alignment,
            "recovery_methods": sorted({
                str(line.get("ocr_recovery_method"))
                for line in output["lines"]
                if line.get("bubble_id") == region.get("bubble_id") and line.get("ocr_recovery_method")
            }),
        })
    return {
        "source": output_name,
        "regions": pages,
        "reference_words": token_count,
        "word_errors": error_count,
        "wer": round(error_count / max(1, token_count), 6),
    }


def main() -> int:
    audit = json.loads((SAMPLE_DIR / "chapter920_pages1_2_independent_review.json").read_text(encoding="utf-8"))
    result = {
        "mode": "full-page speech-only OCR after selective retry; punctuation ignored; English words case-folded",
        "baseline": [
            score(f"chapter920_page{page}_ocr.json", entry["manual_reference"])
            for page, entry in zip((1, 2), audit["pages"])
        ],
        "current": [
            score(f"chapter920_page{page}_retry_full_ocr_v5.json", entry["manual_reference"])
            for page, entry in zip((1, 2), audit["pages"])
        ],
        "scope": "Two visually reviewed pages and their 13 word-bearing speech bubbles only; not a chapter-wide estimate.",
        "translation_or_index_run": False,
    }
    for key in ("baseline", "current"):
        total_words = sum(item["reference_words"] for item in result[key])
        total_errors = sum(item["word_errors"] for item in result[key])
        result[key + "_aggregate"] = {
            "reference_words": total_words,
            "word_errors": total_errors,
            "wer": round(total_errors / max(1, total_words), 6),
            "regions": sum(len(item["regions"]) for item in result[key]),
        }
    path = SAMPLE_DIR / "chapter920_pages1_2_retry_full_score_v5.json"
    if path.exists():
        raise FileExistsError(path)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"baseline": result["baseline_aggregate"], "current": result["current_aggregate"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
