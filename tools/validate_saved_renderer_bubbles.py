"""Render saved review samples without OCR or translation inference."""

from __future__ import annotations

import io
import json
import sys
import time
import zipfile
from pathlib import Path

from PIL import Image, ImageChops

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from image_pipeline.renderer import render_translated_page


ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "QUALITY_REVIEW_SAMPLES.zip"
OUTPUT = ROOT / "translator_engine" / "data" / "benchmark_runs" / "renderer_saved_review_20261112"


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    summary = []
    with zipfile.ZipFile(ARCHIVE) as archive:
        warm = "--warm" in sys.argv[1:]
        merged_fix = "--merged-fix" in sys.argv[1:]
        pages = tuple(arg for arg in sys.argv[1:] if arg not in {"--warm", "--merged-fix"}) or ("02", "03", "05", "10", "14")
        for page in pages:
            prefix = f"page_{page}/"
            original = archive.read(prefix + "original_english.webp")
            stored = json.loads(archive.read(prefix + "regions.json"))
            regions = []
            for saved in stored["regions"]:
                box = saved["region_bbox_xywh"]
                region = {
                    "index": saved["saved_index"],
                    "left": box["left"], "top": box["top"],
                    "width": box["width"], "height": box["height"],
                    "text": saved["english_text"],
                    "translated": saved["arabic_translation"],
                    "ocr_boxes": saved.get("ocr_boxes_xywh") or [],
                    "ocr_polygons": saved.get("ocr_polygons") or [],
                    "region_type": saved["region_type"],
                }
                if saved.get("bubble_bbox_xyxy"):
                    region["bubble_id"] = saved["saved_index"]
                    region["bubble_bbox"] = saved["bubble_bbox_xyxy"]
                regions.append(region)
            started = time.perf_counter()
            rendered, report = render_translated_page(original, regions)
            elapsed = round(time.perf_counter() - started, 3)
            image_name = (f"page_{page}_merged_fix_rerender.png" if merged_fix else
                          f"page_{page}_rerender.png" if len(pages) == 5 else
                          f"page_{page}_timed_rerender.png")
            (OUTPUT / image_name).write_bytes(rendered)
            with Image.open(io.BytesIO(original)) as source:
                with Image.open(io.BytesIO(rendered)) as final:
                    changed = ImageChops.difference(source.convert("RGB"), final.convert("RGB"))
                    changed_bounds = changed.getbbox()
            summary.append({"page": page, "elapsed_seconds": elapsed,
                            "regions_in_saved_review": len(regions),
                            "changed_bounds": changed_bounds, **report})
            if warm:
                warm_started = time.perf_counter()
                _warm_image, warm_report = render_translated_page(original, regions)
                summary[-1]["warm_repeat_seconds"] = round(time.perf_counter() - warm_started, 3)
                summary[-1]["warm_repeat_timing_seconds"] = warm_report["render_timing_seconds"]
            print(f"page {page}: {elapsed}s, rendered {report['rendered_regions']}, "
                  f"skipped {report['skipped_regions']}", flush=True)
    filename = ("summary_merged_fix.json" if merged_fix else
                "summary.json" if len(pages) == 5 and not warm else
                "summary_" + "_".join(pages) + ("_warm" if warm else "") + ".json")
    (OUTPUT / filename).write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
