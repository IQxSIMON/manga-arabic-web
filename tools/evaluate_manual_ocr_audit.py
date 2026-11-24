#!/usr/bin/env python3
"""Score manually transcribed OCR regions against a saved OCR run."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import TypeVar


Token = TypeVar("Token")


def edit_distance(left: Sequence[Token], right: Sequence[Token]) -> int:
    previous = list(range(len(right) + 1))
    for row, left_char in enumerate(left, 1):
        current = [row]
        for column, right_char in enumerate(right, 1):
            current.append(min(
                current[-1] + 1,
                previous[column] + 1,
                previous[column - 1] + (left_char != right_char),
            ))
        previous = current
    return previous[-1]


def normalized(text: str) -> str:
    return re.sub(r"\s+", " ", text.casefold()).strip()


def score(ocr_path: Path, audit_path: Path, *, use_recovered_crops: bool = False) -> dict[str, object]:
    run = json.loads(ocr_path.read_text(encoding="utf-8"))
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    results = run.get("results", [])
    result = next((item for item in results if item.get("page") == audit["page"]), None)
    if result is None:
        raise ValueError(f"Page {audit['page']!r} not found in {ocr_path}")
    regions = result.get("regions", [])
    rows = []
    total_word_errors = total_reference_words = 0
    total_char_errors = total_reference_chars = 0
    exact_matches = 0
    for item in audit["regions"]:
        idx = int(item["region_index"])
        if idx >= len(regions):
            raise ValueError(f"Region index {idx} missing; OCR has {len(regions)} regions")
        baseline_actual = regions[idx].get("text", "")
        actual = item.get("recovered_text", baseline_actual) if use_recovered_crops else baseline_actual
        reference = item.get("reference")
        row = {"region_index": idx, "status": item["status"], "ocr": actual, "reference": reference}
        if use_recovered_crops and "recovered_text" in item:
            row["baseline_ocr"] = baseline_actual
        if reference is not None:
            ref_norm, actual_norm = normalized(reference), normalized(actual)
            ref_words, actual_words = ref_norm.split(), actual_norm.split()
            word_errors = edit_distance(ref_words, actual_words)
            char_errors = edit_distance(ref_norm, actual_norm)
            exact = ref_norm == actual_norm
            row.update({
                "exact_match": exact,
                "word_errors": word_errors,
                "word_error_rate": word_errors / max(1, len(ref_words)),
                "character_errors": char_errors,
                "character_error_rate": char_errors / max(1, len(ref_norm)),
            })
            exact_matches += int(exact)
            total_word_errors += word_errors
            total_reference_words += len(ref_words)
            total_char_errors += char_errors
            total_reference_chars += len(ref_norm)
        rows.append(row)
    count = sum(item.get("reference") is not None for item in audit["regions"])
    return {
        "page": audit["page"],
        "observation_variant": "expanded_edge_crop_recovery" if use_recovered_crops else "saved_ocr_baseline",
        "scored_clear_regions": count,
        "exact_region_matches": exact_matches,
        "exact_region_rate": exact_matches / max(1, count),
        "word_error_rate": total_word_errors / max(1, total_reference_words),
        "character_error_rate": total_char_errors / max(1, total_reference_chars),
        "rows": rows,
        "limitations": audit["limitations"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ocr_json", type=Path)
    parser.add_argument("audit_json", type=Path)
    parser.add_argument("--output", type=Path, help="Optionally save the JSON result atomically.")
    parser.add_argument("--use-recovered-crops", action="store_true", help="Score recorded crop-recovery text overrides, when present.")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    output = json.dumps(score(args.ocr_json, args.audit_json, use_recovered_crops=args.use_recovered_crops), ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=f".{args.output.name}.", suffix=".tmp", dir=args.output.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(output)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, args.output)
        except BaseException:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise
    print(output, end="")


if __name__ == "__main__":
    main()
