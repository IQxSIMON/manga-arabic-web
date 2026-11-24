"""Run local speech-bubble OCR on the two saved Magic Emperor sample pages.

This is a one-off sample runner: it does not call translation or external OCR.
It refuses to overwrite previously saved OCR output.
"""

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


SAMPLE_DIR = (
    ROOT
    / "translator_engine"
    / "data"
    / "benchmark_runs"
    / "magic_emperor_920_sample"
)


def main() -> int:
    engine = LocalRapidOCR()
    for page in (1, 2):
        image_path = SAMPLE_DIR / f"chapter920_page{page}.webp"
        output_path = SAMPLE_DIR / f"chapter920_page{page}_ocr.json"
        if not image_path.is_file():
            raise FileNotFoundError(image_path)
        if output_path.exists():
            raise FileExistsError(f"Refusing to overwrite {output_path}")

        image = image_path.read_bytes()
        started = time.perf_counter()
        result = engine.extract(image, include_external=False)
        result["review_page"] = page
        result["source_sha256"] = hashlib.sha256(image).hexdigest()
        output_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    "page": page,
                    "elapsed_seconds": round(time.perf_counter() - started, 3),
                    "bubble_regions": len(result.get("regions", [])),
                    "ocr_lines": len(result.get("lines", [])),
                    "provider": result.get("provider"),
                    "saved": output_path.name,
                },
                ensure_ascii=False,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
