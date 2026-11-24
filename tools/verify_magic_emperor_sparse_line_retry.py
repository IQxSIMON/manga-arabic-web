"""Verify the targeted line-crop retry on the saved page-2 missed-word case."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from ocr_engine import LocalRapidOCR

SAMPLE_DIR = ROOT / "translator_engine" / "data" / "benchmark_runs" / "magic_emperor_920_sample"


def main() -> int:
    output = SAMPLE_DIR / "chapter920_page2_sparse_line_retry_verification_v2.json"
    if output.exists():
        raise FileExistsError(output)
    data = json.loads((SAMPLE_DIR / "chapter920_page2_ocr.json").read_text(encoding="utf-8"))
    region = data["regions"][3]
    line = next(row for row in data["lines"] if row["bubble_id"] == 5 and row["text"].strip() == "BUT  WORK")
    x1, y1, x2, y2 = map(int, region["bubble_bbox"])
    image = np.asarray(Image.open(SAMPLE_DIR / "chapter920_page2.webp").convert("RGB"))[:, :, ::-1].copy()
    crop = image[y1:y2, x1:x2].copy()
    started = time.perf_counter()
    result = LocalRapidOCR()._rescue_sparse_bubble_lines(
        crop, [line], origin=(x1, y1), scale=1.0
    )
    record = {
        "mode": "targeted sparse line retry on one saved bubble; no full-page OCR or translation",
        "manual_reference": "BUT WILL IT WORK?",
        "before": line,
        "after": result[0],
        "elapsed_seconds_in_fresh_process": round(time.perf_counter() - started, 4),
    }
    output.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(record, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
