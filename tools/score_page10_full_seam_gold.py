"""Word-error score for full page-10 OCR including the top external panel."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.evaluate_ocr_visual_gold import _tokens, score

SAMPLE = ROOT / "translator_engine/data/benchmark_runs/page10_manual_extension_20261112"
OUTPUT = SAMPLE / "full_seam_visual_gold_score_v2.json"


def main():
    if OUTPUT.exists():
        raise FileExistsError(OUTPUT)
    manual = json.loads((SAMPLE / "independent_manual_gold.json").read_text(encoding="utf-8"))
    current = json.loads((SAMPLE / "full_ocr_seam_retry_validation.json").read_text(encoding="utf-8"))
    gold = {"cases": [
        {
            "page": "page_10",
            "region_index": index,
            "reference": region["manual_source_transcript"],
            "class": "external" if region.get("full_pipeline_region_type") == "external_text" else "bubble",
        }
        for index, region in enumerate(manual["regions"], 1)
    ]}
    report = {"results": [{"page": "page_10", "regions": current["regions"]}]}
    scored = score(gold, report, report, anchor=report)
    rows = [{
        "region_index": row["region_index"],
        "reference": row["reference"],
        "recognized": row["current"],
        "region_type": current["regions"][row["region_index"] - 1].get("region_type"),
        "reference_word_count": len(_tokens(row["reference"])),
        "word_errors": row["current_word_errors"],
        "wer": row["current_wer"],
    } for row in scored["rows"]]
    result = {
        "scope": "one independent manually transcribed page-10 full OCR result",
        "region_count": len(rows),
        "speech_bubble_regions": sum(r["region_type"] == "speech_bubble" for r in rows),
        "external_text_regions": sum(r["region_type"] == "external_text" for r in rows),
        "reference_word_count": scored["reference_word_count"],
        "word_errors": scored["current_word_errors"],
        "word_error_rate": scored["current_wer"],
        "regions": rows,
        "limits": ["Single page only; no translation quality or mask-boundary IoU is measured."],
    }
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
