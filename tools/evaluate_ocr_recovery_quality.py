"""Compare selected saved OCR recovery cases with manually checked source text."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GOLD = ROOT / "translator_engine" / "data" / "ocr_recovery_gold_cases.json"
DEFAULT_BASELINE = (
    ROOT / "translator_engine" / "data" / "benchmark_runs"
    / "ocr_saved_samples_20261111_110052.json"
)
DEFAULT_CURRENT = (
    ROOT / "translator_engine" / "data" / "benchmark_runs"
    / "ocr_pages_05_14_tight_crop_20261111.json"
)


def _words(text: str) -> list[str]:
    normalized = text.upper().replace("’", "'")
    return re.findall(r"[A-Z]+(?:'[A-Z]+)?", normalized)


def _letters(text: str) -> str:
    return "".join(character for character in text.upper() if character.isalpha())


def _edit_distance(first: list | str, second: list | str) -> int:
    previous = list(range(len(second) + 1))
    for row, left in enumerate(first, 1):
        current = [row]
        for column, right in enumerate(second, 1):
            current.append(min(
                current[-1] + 1,
                previous[column] + 1,
                previous[column - 1] + (left != right),
            ))
        previous = current
    return previous[-1]


def _page_map(report: dict) -> dict[str, dict]:
    return {page["page"]: page for page in report["results"]}


def _contains_word_fragment(text: str, fragment: str) -> bool:
    words = _words(text)
    wanted = _words(fragment)
    return any(words[index:index + len(wanted)] == wanted
               for index in range(len(words) - len(wanted) + 1))


def evaluate(gold: dict, baseline: dict, current: dict) -> dict:
    baseline_pages = _page_map(baseline)
    current_pages = _page_map(current)
    results = []
    for case in gold["cases"]:
        page_id = case["page"]
        if page_id not in baseline_pages or page_id not in current_pages:
            raise ValueError(f"Missing saved OCR result for {page_id}")
        old_regions = baseline_pages[page_id]["regions"]
        new_regions = current_pages[page_id]["regions"]
        old_matches = [region["text"] for region in old_regions
                       if _contains_word_fragment(region["text"], case["baseline_fragment"])]
        if not old_matches:
            raise ValueError(f"Baseline fragment not found for {case['id']}")
        recovered = [region["text"] for region in new_regions
                     if region.get("ocr_recovered")]
        if len(recovered) != 1:
            raise ValueError(f"Expected one recovered region for {case['id']}, got {len(recovered)}")

        reference = case["reference"]
        old_text = case["baseline_fragment"]
        new_text = recovered[0]
        reference_words = _words(reference)
        old_word_errors = _edit_distance(_words(old_text), reference_words)
        new_word_errors = _edit_distance(_words(new_text), reference_words)
        reference_letters = _letters(reference)
        old_char_errors = _edit_distance(_letters(old_text), reference_letters)
        new_char_errors = _edit_distance(_letters(new_text), reference_letters)
        results.append({
            "id": case["id"], "page": page_id,
            "reference": reference,
            "baseline_observed": old_text,
            "current_observed": new_text,
            "baseline_wer": round(old_word_errors / max(1, len(reference_words)), 4),
            "current_wer": round(new_word_errors / max(1, len(reference_words)), 4),
            "baseline_cer": round(old_char_errors / max(1, len(reference_letters)), 4),
            "current_cer": round(new_char_errors / max(1, len(reference_letters)), 4),
        })
    return {
        "schema_version": 1,
        "scope": "two targeted saved OCR cases; not a general model accuracy estimate",
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--current", type=Path, default=DEFAULT_CURRENT)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    gold, baseline, current = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in (args.gold, args.baseline, args.current)
    ]
    report = evaluate(gold, baseline, current)
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(encoded + "\n", encoding="utf-8")
        print(args.output)
    else:
        print(encoded)


if __name__ == "__main__":
    main()
