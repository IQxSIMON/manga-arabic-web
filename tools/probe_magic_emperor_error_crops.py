"""Compare installed small and medium OCR on two saved error crops only."""

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


def read(recognizer, crop):
    started = time.perf_counter()
    result = recognizer(crop)
    texts = getattr(result, "txts", None)
    scores = getattr(result, "scores", None)
    boxes = getattr(result, "boxes", None)
    return {
        "seconds": round(time.perf_counter() - started, 4),
        "text": list(texts) if texts is not None else [],
        "scores": [round(float(x), 4) for x in scores] if scores is not None else [],
        "box_count": len(boxes) if boxes is not None else 0,
    }


def main() -> int:
    wrapper = LocalRapidOCR()
    primary = wrapper._get_engine()
    medium = wrapper._get_medium_engine()
    if medium is None:
        raise RuntimeError("Installed PP-OCRv6 medium recognizer is unavailable")
    cases = ((1, 1), (2, 4))
    output = {"mode": "recognizer probe on two saved bubble rectangles only; no full-page OCR or translation", "cases": []}
    for page, region_number in cases:
        data = json.loads((SAMPLE_DIR / f"chapter920_page{page}_ocr.json").read_text(encoding="utf-8"))
        region = data["regions"][region_number - 1]
        image = np.asarray(Image.open(SAMPLE_DIR / f"chapter920_page{page}.webp").convert("RGB"))[:, :, ::-1].copy()
        x, y = int(region["left"]), int(region["top"])
        crop = image[y:y + int(region["height"]), x:x + int(region["width"])].copy()
        output["cases"].append({
            "page": page,
            "region": region_number,
            "saved_primary_text": region["text"],
            "saved_confidence": region["confidence"],
            "crop_size": [int(crop.shape[1]), int(crop.shape[0])],
            "small": read(primary, crop),
            "medium": read(medium, crop),
        })
    path = SAMPLE_DIR / "chapter920_pages1_2_error_crop_recognizer_probe.json"
    if path.exists():
        raise FileExistsError(path)
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
