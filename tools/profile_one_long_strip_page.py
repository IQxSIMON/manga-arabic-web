"""Profile the existing OCR stages on one already-indexed long-strip page."""
from __future__ import annotations

import argparse
import json
import sys
import time
import zipfile
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ocr_engine import LocalRapidOCR  # noqa: E402
from tools.run_local_chapter_ocr import peak_rss_mib  # noqa: E402


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("page", type=int)
    parser.add_argument(
        "--skip-relaxed-bubble-classification", action="store_true",
        help="Measure the OCR pass without its second relaxed bubble-detection pass.",
    )
    parser.add_argument(
        "--skip-targeted-line-retries", action="store_true",
        help="Disable only the three targeted speech-line OCR retry helpers.",
    )
    args = parser.parse_args()

    index = json.loads((ROOT / "ocr_test_results" / "chapter_index.json").read_text(encoding="utf-8"))
    chapter = next((item for item in index["archives"]
                    if Path(item["chapter"]).name == args.archive.name
                    and item.get("status") == "indexed"), None)
    if chapter is None:
        raise SystemExit("Archive is absent from the verified chapter index.")
    page = next((item for item in chapter["pages"] if item["page"] == args.page), None)
    if page is None:
        raise SystemExit(f"Page {args.page} is not present in the indexed chapter.")

    init_started = time.perf_counter()
    engine = LocalRapidOCR()
    if args.skip_targeted_line_retries:
        engine._rescue_malformed_bubble_lines = lambda image, lines, **kwargs: lines
        engine._rescue_large_stylized_bubble_lines = lambda image, lines: lines
        engine._rescue_terminal_short_fragment = lambda image, lines: lines
    engine_init_seconds = time.perf_counter() - init_started
    with zipfile.ZipFile(args.archive) as archive:
        image_bytes = archive.read(page["member"])
    image = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise SystemExit("The indexed page image could not be decoded.")
    started = time.perf_counter()
    result = engine.extract(
        image_bytes, reading_direction="ltr", include_external=True,
        recover_external_bubble_classification=not args.skip_relaxed_bubble_classification,
    )
    wall_seconds = time.perf_counter() - started
    timing_fields = (
        "elapsed_seconds", "bubble_detection_and_ocr_seconds", "bubble_detection_seconds",
        "bubble_ocr_seconds", "bubble_split_detector_seconds",
        "external_ocr_seconds", "adjacent_bubble_recovery_seconds", "edge_crop_recovery_seconds",
        "comic_detector_seconds",
    )
    print(json.dumps({
        "chapter": chapter.get("story_series"), "chapter_label": chapter.get("chapter_label"),
        "page": args.page, "dimensions": [image.shape[1], image.shape[0]],
        "engine_init_seconds": round(engine_init_seconds, 4),
        "extract_wall_seconds": round(wall_seconds, 4),
        "peak_rss_mib": round(peak_rss_mib(), 1),
        "regions": len(result.get("regions", [])),
        "region_signature": [
            {
                "text": item.get("text", ""), "region_type": item.get("region_type", ""),
                "box": [round(float(item.get(key, 0)), 1)
                        for key in ("left", "top", "width", "height")],
            }
            for item in result.get("regions", [])
        ],
        "timings": {key: result.get(key) for key in timing_fields if key in result},
        "counts": {key: result.get(key) for key in (
            "speech_bubble_mask_count", "speech_bubble_masks_with_primary_ocr",
            "speech_bubble_masks_without_primary_ocr", "bubble_split_count",
            "adjacent_bubble_recovery_count", "edge_crop_recovery_count",
            "comic_detector_region_count", "comic_detector_bubble_count",
            "comic_detector_ocr_line_count",
        ) if key in result},
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
