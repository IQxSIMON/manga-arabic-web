"""Run complete local OCR once to audit a top-clipped speech bubble."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from ocr_engine import LocalRapidOCR

DIR = ROOT / "translator_engine" / "data" / "benchmark_runs" / "page10_manual_extension_20261112"


def main() -> int:
    output = DIR / "full_ocr_current.json"
    if output.exists():
        raise FileExistsError(output)
    image = (DIR / "original_english.webp").read_bytes()
    started = time.perf_counter()
    result = LocalRapidOCR().extract(image, include_external=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "elapsed_seconds": round(time.perf_counter()-started,3),
        "regions":len(result.get("regions",[])),
        "speech_regions":sum(r.get("region_type")=="speech_bubble" for r in result.get("regions",[])),
        "external_regions":sum(r.get("region_type")=="external_text" for r in result.get("regions",[])),
        "provider":result.get("provider"),"saved":output.name,
    }))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
