"""Run local OCR only on the five existing visual-review pages.

No translation model or network access is used. Results are written separately
from saved application data so this benchmark cannot replace reviewed pages.
"""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ocr_engine import LocalRapidOCR


DEFAULT_ZIP = ROOT / "QUALITY_REVIEW_SAMPLES.zip"
DEFAULT_OUTPUT_DIR = ROOT / "translator_engine" / "data" / "benchmark_runs"
DEFAULT_PAGES = ("page_02", "page_03", "page_05", "page_10", "page_14")


def evaluate(archive_path: Path, pages: tuple[str, ...]) -> dict:
    engine = LocalRapidOCR()
    results = []
    with zipfile.ZipFile(archive_path) as archive:
        for page_name in pages:
            member = f"{page_name}/original_english.webp"
            image_bytes = archive.read(member)
            result = engine.extract(image_bytes, include_external=True)
            results.append({
                "page": page_name,
                "provider": result.get("provider"),
                "input_bytes": len(image_bytes),
                "elapsed_seconds": result.get("elapsed_seconds"),
                "bubble_detection_and_ocr_seconds": result.get(
                    "bubble_detection_and_ocr_seconds"
                ),
                "external_ocr_seconds": result.get("external_ocr_seconds"),
                "comic_detector_seconds": result.get("comic_detector_seconds"),
                "comic_detector_region_count": result.get(
                    "comic_detector_region_count"
                ),
                "comic_detector_ocr_line_count": result.get(
                    "comic_detector_ocr_line_count"
                ),
                "line_count": len(result.get("lines", [])),
                "region_count": len(result.get("regions", [])),
                "lines": result.get("lines", []),
                "regions": result.get("regions", []),
            })
    return {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "archive": archive_path.name,
        "pages": list(pages),
        "mode": "local OCR only; no translation or network access",
        "provider": results[0].get("provider") if results else None,
        "results": results,
        "total_elapsed_seconds": round(
            sum(float(item.get("elapsed_seconds") or 0) for item in results), 4
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, default=DEFAULT_ZIP)
    parser.add_argument("--pages", nargs="+", default=list(DEFAULT_PAGES))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.archive.is_file():
        parser.error(f"sample archive not found: {args.archive}")

    report = evaluate(args.archive, tuple(args.pages))
    output = args.output
    if output is None:
        DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output = DEFAULT_OUTPUT_DIR / f"ocr_saved_samples_{stamp}.json"
    elif output.exists():
        parser.error(f"refusing to overwrite existing report: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(output)
    print(json.dumps({
        "report": str(output.resolve()),
        "total_elapsed_seconds": report["total_elapsed_seconds"],
        "pages": [
            {
                "page": item["page"],
                "seconds": item["elapsed_seconds"],
                "lines": item["line_count"],
                "regions": item["region_count"],
                "detector_regions": item["comic_detector_region_count"],
            }
            for item in report["results"]
        ],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
