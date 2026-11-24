"""Compare RapidOCR PP-OCRv6 small and medium on saved English manhwa crops."""

from __future__ import annotations

import json
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rapidocr import ModelType, RapidOCR


SAMPLE_CROPS = (
    ("page_14_upper_bubble", "page_14", (180, 4980, 575, 5250)),
    ("page_14_lower_bubble", "page_14", (0, 5260, 470, 5580)),
    ("page_14_decorative_purchase", "page_14", (0, 11280, 800, 11980)),
    ("page_05_quest_dialogue", "page_05", (40, 4400, 540, 4740)),
)


def run_engine(archive: zipfile.ZipFile, model_type: ModelType) -> dict:
    started = time.perf_counter()
    engine = RapidOCR(params={"Rec.model_type": model_type})
    initialized = time.perf_counter() - started
    results = []
    images = {}
    for name, page, (left, top, right, bottom) in SAMPLE_CROPS:
        if page not in images:
            images[page] = cv2.imdecode(
                np.frombuffer(archive.read(f"{page}/original_english.webp"), np.uint8),
                cv2.IMREAD_COLOR,
            )
        image = images[page]
        crop = image[top:min(bottom, image.shape[0]), left:min(right, image.shape[1])]
        started = time.perf_counter()
        output = engine(crop)
        elapsed = time.perf_counter() - started
        results.append({
            "sample": name,
            "elapsed_seconds": round(elapsed, 4),
            "lines": [
                {"text": str(text), "confidence": round(float(score), 4)}
                for text, score in zip(output.txts or [], output.scores or [])
            ] if output is not None else [],
        })
    return {
        "model_type": model_type.value,
        "initialization_seconds": round(initialized, 4),
        "results": results,
    }


def main() -> int:
    archive_path = ROOT / "QUALITY_REVIEW_SAMPLES.zip"
    output_dir = ROOT / "translator_engine" / "data" / "benchmark_runs"
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = output_dir / f"ocr_recognizer_compare_{stamp}.json"
    with zipfile.ZipFile(archive_path) as archive:
        report = {
            "schema_version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "archive": archive_path.name,
            "mode": "OCR crops only; no translation",
            "engines": [
                run_engine(archive, ModelType.SMALL),
                run_engine(archive, ModelType.MEDIUM),
            ],
        }
    temporary = output_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(output_path)
    print(json.dumps({
        "report": str(output_path.resolve()),
        "engines": report["engines"],
    }, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
