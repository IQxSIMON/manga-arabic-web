"""Run one full OCR integration check on saved page 10 after seam repair."""

from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ocr_engine import LocalRapidOCR

SAMPLE = ROOT / "translator_engine/data/benchmark_runs/page10_manual_extension_20261112"
OUTPUT = SAMPLE / "full_ocr_seam_retry_validation.json"


def main() -> int:
    if OUTPUT.exists():
        raise FileExistsError(OUTPUT)
    source = SAMPLE / "original_english.webp"
    image_bytes = source.read_bytes()
    image = cv2.imread(str(source), cv2.IMREAD_COLOR)
    if image is None:
        raise RuntimeError("Could not read saved page-10 source")
    started = time.perf_counter()
    result = LocalRapidOCR().extract(image_bytes, include_external=True)
    result.update({
        "source_sha256": hashlib.sha256(image_bytes).hexdigest(),
        "validation": "full OCR integration after targeted low-confidence seam recovery",
    })
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "region_count": len(result.get("regions", [])),
        "speech_bubble_regions": sum(r.get("region_type") == "speech_bubble" for r in result.get("regions", [])),
        "external_text_regions": sum(r.get("region_type") == "external_text" for r in result.get("regions", [])),
        "regions": [{
            "text": r.get("text"),
            "region_type": r.get("region_type"),
            "bbox": [r.get(key) for key in ("left", "top", "width", "height")],
        } for r in result.get("regions", [])],
        "saved": str(OUTPUT),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
