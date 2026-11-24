"""Validate seam-context bubble recovery on the saved independent page-10 gold."""

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
OUTPUT = SAMPLE / "speech_ocr_seam_retry_validation.json"


def main() -> int:
    if OUTPUT.exists():
        raise FileExistsError(OUTPUT)
    source_bytes = (SAMPLE / "original_english.webp").read_bytes()
    image = cv2.imread(str(SAMPLE / "original_english.webp"), cv2.IMREAD_COLOR)
    if image is None:
        raise RuntimeError("Could not load saved page-10 source")
    started = time.perf_counter()
    result = LocalRapidOCR().extract(source_bytes, include_external=False)
    result.update({
        "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "validation": "full speech-only OCR with targeted low-confidence strip-seam bubble retry",
    })
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "region_count": len(result.get("regions", [])),
        "regions": [{
            "text": region.get("text"),
            "region_type": region.get("region_type"),
            "bbox": [region.get(key) for key in ("left", "top", "width", "height")],
        } for region in result.get("regions", [])],
        "saved": str(OUTPUT),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
