"""Replay external OCR review hints on saved page-0 text; no OCR inference."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from ocr_engine import LocalRapidOCR

SAMPLE_DIR = ROOT / "translator_engine" / "data" / "benchmark_runs" / "magic_emperor_920_sample"


def main() -> int:
    output = SAMPLE_DIR / "chapter920_page0_external_review_hint_replay_v2.json"
    if output.exists():
        raise FileExistsError(output)
    saved = json.loads((SAMPLE_DIR / "chapter920_page0_full_ocr.json").read_text(encoding="utf-8"))
    rows = []
    for index, region in enumerate(saved.get("regions", []), 1):
        if region.get("region_type") != "external_text":
            continue
        replayed = LocalRapidOCR._add_external_ocr_review_hint(copy.deepcopy(region))
        rows.append({
            "region": index,
            "text": region.get("text"),
            "review_required": replayed.get("ocr_review_required", False),
            "reason": replayed.get("ocr_review_reason"),
            "suspicious_short_tokens": replayed.get("ocr_suspicious_short_uppercase_tokens", []),
            "text_unchanged": replayed.get("text") == region.get("text"),
        })
    result = {
        "mode": "review-only heuristic replay on saved OCR JSON; no OCR inference and no text rewriting",
        "external_regions": rows,
    }
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(rows, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
