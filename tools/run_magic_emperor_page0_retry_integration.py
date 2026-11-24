"""Run updated speech-only OCR on the saved first Magic Emperor strip once."""

from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from ocr_engine import LocalRapidOCR

SAMPLE_DIR = ROOT / "translator_engine" / "data" / "benchmark_runs" / "magic_emperor_920_sample"


def main() -> int:
    image_path = SAMPLE_DIR / "chapter920_page0.webp"
    output_path = SAMPLE_DIR / "chapter920_page0_retry_full_ocr.json"
    if output_path.exists():
        raise FileExistsError(f"Refusing to overwrite {output_path}")
    image = image_path.read_bytes()
    started = time.perf_counter()
    result = LocalRapidOCR().extract(image, include_external=False)
    result.update({
        "review_page": 0,
        "source_sha256": hashlib.sha256(image).hexdigest(),
        "pipeline_revision": "medium threshold 0.80 plus selective sparse-line retry and token preservation",
    })
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "page": 0,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "regions": len(result.get("regions", [])),
        "lines": len(result.get("lines", [])),
        "rescued_lines": [
            {"text": line["text"], "method": line.get("ocr_recovery_method"),
             "confidence": line["confidence"]}
            for line in result.get("lines", []) if line.get("ocr_rescued")
        ],
        "saved": output_path.name,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
