"""Run speech-only OCR on a saved page-10 vertical strip for manual review."""

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

SAMPLE_DIR = ROOT / "translator_engine" / "data" / "benchmark_runs" / "page10_manual_extension_20261112"


def main() -> int:
    output = SAMPLE_DIR / "speech_ocr_current.json"
    if output.exists():
        raise FileExistsError(output)
    image = (SAMPLE_DIR / "original_english.webp").read_bytes()
    started = time.perf_counter()
    result = LocalRapidOCR().extract(image, include_external=False)
    result.update({
        "source_sha256": hashlib.sha256(image).hexdigest(),
        "pipeline_revision": "selective confidence retry + sparse-line retry + known-token preservation",
    })
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "regions": len(result.get("regions", [])),
        "lines": len(result.get("lines", [])),
        "rescued_lines": sum(bool(line.get("ocr_rescued")) for line in result.get("lines", [])),
        "saved": output.name,
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
