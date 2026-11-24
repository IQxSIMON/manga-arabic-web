"""Capture current speech-mask/OCR diagnostic counts on saved page 02."""

from __future__ import annotations

import hashlib
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ocr_engine import LocalRapidOCR

ARCHIVE = ROOT / "QUALITY_REVIEW_SAMPLES.zip"
OUTPUT = ROOT / "translator_engine/data/benchmark_runs/seam_retry_regression_20261112/page02_mask_diagnostics.json"


def main():
    if OUTPUT.exists():
        raise FileExistsError(OUTPUT)
    with zipfile.ZipFile(ARCHIVE) as archive:
        image = archive.read("page_02/original_english.webp")
    result = LocalRapidOCR().extract(image, include_external=False)
    summary = {
        "source_sha256": hashlib.sha256(image).hexdigest(),
        "region_count": len(result.get("regions", [])),
        "speech_bubble_mask_count": result["speech_bubble_mask_count"],
        "speech_bubble_masks_with_primary_ocr": result["speech_bubble_masks_with_primary_ocr"],
        "speech_bubble_masks_without_primary_ocr": result["speech_bubble_masks_without_primary_ocr"],
        "bubble_detection_and_ocr_seconds": result.get("bubble_detection_and_ocr_seconds"),
        "ocr_text": [region.get("text", "") for region in result.get("regions", [])],
    }
    OUTPUT.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
