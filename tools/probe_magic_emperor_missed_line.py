"""Probe installed OCR recognizers on one manually verified missed-word line."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from ocr_engine import LocalRapidOCR

SAMPLE_DIR = ROOT / "translator_engine" / "data" / "benchmark_runs" / "magic_emperor_920_sample"


def recognize(engine, crop):
    started = time.perf_counter()
    result = engine(crop)
    return {
        "seconds": round(time.perf_counter() - started, 4),
        "texts": list(getattr(result, "txts", None) or []),
        "scores": [round(float(score), 4) for score in (getattr(result, "scores", None) or [])],
    }


def main() -> int:
    out = SAMPLE_DIR / "chapter920_page2_missed_line_scale_probe.json"
    if out.exists():
        raise FileExistsError(out)
    data = json.loads((SAMPLE_DIR / "chapter920_page2_ocr.json").read_text(encoding="utf-8"))
    line = next(row for row in data["lines"] if row["bubble_id"] == 5 and row["text"].strip() == "BUT  WORK")
    image = np.asarray(Image.open(SAMPLE_DIR / "chapter920_page2.webp").convert("RGB"))[:, :, ::-1].copy()
    x, y, width, height = map(int, (line["left"], line["top"], line["width"], line["height"]))
    pad_x, pad_y = 28, 20
    crop = image[max(0, y-pad_y):min(image.shape[0], y+height+pad_y),
                 max(0, x-pad_x):min(image.shape[1], x+width+pad_x)].copy()
    wrapper = LocalRapidOCR()
    engines = {"small": wrapper._get_engine(), "medium": wrapper._get_medium_engine()}
    results = []
    for scale in (1.0, 1.5, 2.0, 3.0):
        sized = crop if scale == 1 else cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        for name, engine in engines.items():
            result = recognize(engine, sized)
            results.append({"scale": scale, "recognizer": name, "size": [int(sized.shape[1]), int(sized.shape[0])], **result})
    report = {
        "mode": "one saved OCR line crop only; recognizer scaling probe, no full-page OCR or translation",
        "manual_reference": "BUT WILL IT WORK?",
        "saved_ocr_line": line["text"],
        "crop_padding_px": [pad_x, pad_y],
        "results": results,
    }
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
