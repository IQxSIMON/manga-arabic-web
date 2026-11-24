"""Score manually transcribed, visually reviewed OCR regions in saved reports."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEFAULT_GOLD = ROOT / "translator_engine" / "data" / "ocr_visual_gold_cases.json"
DEFAULT_CURRENT = (
    ROOT / "translator_engine" / "data" / "benchmark_runs"
    / "ocr_saved_samples_20261111_targeted_rescue.json"
)
DEFAULT_ANCHOR = DEFAULT_CURRENT
DEFAULT_BASELINE = (
    ROOT / "translator_engine" / "data" / "benchmark_runs"
    / "ocr_saved_samples_20261111_093800.json"
)


def _tokens(text: str) -> list[str]:
    return re.findall(r"[A-Z]+(?:'[A-Z]+)?|\d+", str(text).upper().replace("’", "'"))


def _distance(first: list[str], second: list[str]) -> int:
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


def _pages(report: dict) -> dict[str, list[dict]]:
    values = {}
    for page in report.get("results", []):
        values[page["page"]] = page.get("regions", [])
    return values


def _box(region: dict) -> tuple[float, float, float, float] | None:
    try:
        left, top = float(region["left"]), float(region["top"])
        right = left + float(region["width"])
        bottom = top + float(region["height"])
    except (KeyError, TypeError, ValueError):
        return None
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def _match_by_geometry(target: dict, candidates: list[dict]) -> tuple[int, dict]:
    target_box = _box(target)
    if target_box is None:
        raise ValueError("Annotated current region has no usable geometry")
    x1, y1, x2, y2 = target_box
    target_area = (x2 - x1) * (y2 - y1)
    ranked = []
    for index, candidate in enumerate(candidates, 1):
        candidate_box = _box(candidate)
        if candidate_box is None:
            continue
        a1, b1, a2, b2 = candidate_box
        intersection = max(0.0, min(x2, a2) - max(x1, a1)) * max(0.0, min(y2, b2) - max(y1, b1))
        candidate_area = (a2 - a1) * (b2 - b1)
        overlap = intersection / max(1.0, min(target_area, candidate_area))
        iou = intersection / max(1.0, target_area + candidate_area - intersection)
        ranked.append(((overlap, iou), index, candidate))
    if not ranked:
        raise ValueError("No baseline regions have usable geometry")
    (best_score, best_index, best_region) = max(ranked, key=lambda item: item[0])
    if best_score[0] < 0.20:
        raise ValueError("No spatially corresponding baseline region for annotated OCR region")
    return best_index, best_region


def score(
    gold: dict,
    baseline: dict,
    current: dict,
    anchor: dict | None = None,
    recovery: dict | None = None,
    normalize_external: bool = False,
) -> dict:
    before, after = _pages(baseline), _pages(current)
    anchors = _pages(anchor if anchor is not None else current)
    recovered = {}
    for item in (recovery or {}).get("overrides", []):
        key = (item["page"], int(item["region_index"]))
        if key in recovered:
            raise ValueError(f"Duplicate recovery override for {key}")
        recovered[key] = item
    rows = []
    for case in gold["cases"]:
        page_id, region_index = case["page"], int(case["region_index"])
        reference = _tokens(case["reference"])
        if page_id not in before or page_id not in after or page_id not in anchors:
            raise ValueError(f"Missing page in saved reports: {page_id}")
        if not 1 <= region_index <= len(anchors[page_id]):
            raise ValueError(f"Missing annotated anchor region: {page_id} #{region_index}")
        anchor_region = anchors[page_id][region_index - 1]
        baseline_index, baseline_region = _match_by_geometry(anchor_region, before[page_id])
        current_index, current_region = _match_by_geometry(anchor_region, after[page_id])
        old_text = str(baseline_region.get("text", ""))
        new_text = str(current_region.get("text", ""))
        if normalize_external and current_region.get("region_type") == "external_text":
            from ocr_engine.local_rapidocr import LocalRapidOCR
            new_text = str(LocalRapidOCR._normalize_external_ocr_region(current_region).get("text", ""))
        recovery_item = recovered.get((page_id, region_index))
        if recovery_item:
            new_text = str(recovery_item["text"])
        old_errors = _distance(_tokens(old_text), reference)
        new_errors = _distance(_tokens(new_text), reference)
        old_rate = old_errors / max(1, len(reference))
        new_rate = new_errors / max(1, len(reference))
        rows.append({
            "page": page_id, "region_index": region_index,
            "baseline_region_index": baseline_index,
            "current_region_index": current_index, "class": case["class"],
            "reference": case["reference"], "baseline": old_text, "current": new_text,
            "baseline_wer": round(old_rate, 4), "current_wer": round(new_rate, 4),
            "baseline_word_errors": old_errors, "current_word_errors": new_errors,
            "current_source": recovery_item.get("method") if recovery_item else "saved_full_page_ocr",
            "improved": new_rate < old_rate,
        })
    total_words = sum(len(_tokens(row["reference"])) for row in rows)
    baseline_errors = sum(row["baseline_word_errors"] for row in rows)
    current_errors = sum(row["current_word_errors"] for row in rows)
    return {
        "schema_version": 1,
        "scope": f"{len(rows)} manually transcribed clear regions from five saved pages; not a corpus-wide OCR estimate",
        "crop_recovery_overrides": len(recovered),
        "external_text_normalization": normalize_external,
        "case_count": len(rows),
        "reference_word_count": total_words,
        "baseline_word_errors": baseline_errors,
        "current_word_errors": current_errors,
        "baseline_wer": round(baseline_errors / max(1, total_words), 4),
        "current_wer": round(current_errors / max(1, total_words), 4),
        "improved_regions": sum(row["improved"] for row in rows),
        "rows": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--current", type=Path, default=DEFAULT_CURRENT)
    parser.add_argument("--anchor", type=Path, default=DEFAULT_ANCHOR)
    parser.add_argument("--recovery", type=Path, help="Saved crop-only region overrides to include in current-output scoring.")
    parser.add_argument("--normalize-current-external", action="store_true", help="Apply the production external-text OCR normalizer to saved current regions before scoring.")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = score(
        *[
            json.loads(path.read_text(encoding="utf-8"))
            for path in (args.gold, args.baseline, args.current, args.anchor)
        ],
        recovery=json.loads(args.recovery.read_text(encoding="utf-8")) if args.recovery else None,
        normalize_external=args.normalize_current_external,
    )
    encoded = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
        print(args.output)
    else:
        print(encoded)


if __name__ == "__main__":
    main()
