"""Compare current saved detector masks to saved dialogue OCR box geometry."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "translator_engine/data/benchmark_runs/magic_emperor_920_sample"
MASKS = BASE / "chapter920_seam_detector_regression.json"
OUTPUT = BASE / "chapter920_seam_geometry_comparison.json"


def overlap_ratio(first, second):
    x1, y1, x2, y2 = first
    a1, b1, a2, b2 = second
    intersection = max(0, min(x2, a2) - max(x1, a1)) * max(0, min(y2, b2) - max(y1, b1))
    area_first = max(1, (x2 - x1) * (y2 - y1))
    area_second = max(1, (a2 - a1) * (b2 - b1))
    return intersection / min(area_first, area_second)


def main():
    if OUTPUT.exists():
        raise FileExistsError(OUTPUT)
    masks = json.loads(MASKS.read_text(encoding="utf-8"))["pages"]
    pages = []
    for entry in masks:
        index = entry["page"]
        artifact = BASE / (
            "chapter920_page0_retry_full_ocr.json" if index == 0
            else f"chapter920_page{index}_retry_full_ocr_v5.json"
        )
        regions = json.loads(artifact.read_text(encoding="utf-8"))["regions"]
        boxes = entry["bboxes"]
        matches = []
        for region in regions:
            box = region.get("bubble_bbox")
            best = max((overlap_ratio(box, candidate) for candidate in boxes), default=0.0)
            matches.append({"text": region.get("text"), "best_mask_overlap": round(best, 4), "matched": best >= 0.50})
        pages.append({
            "page": index,
            "ocr_dialogue_regions": len(regions),
            "matched_to_current_mask": sum(item["matched"] for item in matches),
            "minimum_best_overlap": min((item["best_mask_overlap"] for item in matches), default=None),
            "regions": matches,
        })
    result = {"method": "max(intersection / smaller_bbox_area), threshold 0.50", "pages": pages}
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
