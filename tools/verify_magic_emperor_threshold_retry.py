"""Exercise the selective production retry on one previously scored OCR error."""

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
    page, region_number = 1, 1
    report_path = SAMPLE_DIR / "chapter920_page1_threshold_retry_verification.json"
    if report_path.exists():
        raise FileExistsError(report_path)
    data = json.loads((SAMPLE_DIR / f"chapter920_page{page}_ocr.json").read_text(encoding="utf-8"))
    region = data["regions"][region_number - 1]
    image = np.asarray(Image.open(SAMPLE_DIR / f"chapter920_page{page}.webp").convert("RGB"))[:, :, ::-1].copy()
    x, y = int(region["left"]), int(region["top"])
    crop = image[y:y + int(region["height"]), x:x + int(region["width"])].copy()
    line = {
        "text": region["text"], "confidence": region["confidence"],
        "left": x, "top": y, "width": int(region["width"]),
        "height": int(region["height"]),
    }
    started = time.perf_counter()
    result = LocalRapidOCR()._rescue_bubble_lines(crop, [line])
    record = {
        "mode": "selective medium retry on one saved bubble crop; no full-page OCR or translation",
        "before": line,
        "after": result[0],
        "retry_seconds": round(time.perf_counter() - started, 4),
    }
    report_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(record, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
