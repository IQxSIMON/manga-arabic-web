"""Run speech-only OCR once on independently reviewed saved vertical pages."""

from __future__ import annotations

import hashlib
import json
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from ocr_engine import LocalRapidOCR

ARCHIVE = ROOT / "QUALITY_REVIEW_SAMPLES.zip"
OUTPUT_DIR = ROOT / "translator_engine" / "data" / "benchmark_runs" / "retry_regression_20261112"


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    engine = LocalRapidOCR()
    with zipfile.ZipFile(ARCHIVE) as archive:
        for page in (2, 3, 5, 14):
            member = f"page_{page:02d}/original_english.webp"
            name = f"page_{page:02d}_speech_ocr.json"
            output = OUTPUT_DIR / name
            if output.exists():
                raise FileExistsError(f"Refusing to overwrite {output}")
            image = archive.read(member)
            started = time.perf_counter()
            result = engine.extract(image, include_external=False)
            result.update({
                "source_member": member,
                "source_sha256": hashlib.sha256(image).hexdigest(),
                "pipeline_revision": "selective confidence retry + sparse-line retry + known-token preservation",
            })
            output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps({
                "page": page,
                "elapsed_seconds": round(time.perf_counter() - started, 3),
                "regions": len(result.get("regions", [])),
                "lines": len(result.get("lines", [])),
                "rescued_lines": sum(bool(line.get("ocr_rescued")) for line in result.get("lines", [])),
                "saved": name,
            }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
