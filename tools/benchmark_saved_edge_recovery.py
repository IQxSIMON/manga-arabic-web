"""Evaluate local bubble-edge OCR recovery using the five saved review pages.

This tool reuses archived OCR and image bytes. It only reruns narrow OCR crops
for lines touching a bubble-mask boundary; it never invokes translation.
"""

from __future__ import annotations

import copy
import json
import sys
import time
import zipfile
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ocr_engine import LocalRapidOCR


RUNS = ROOT / "translator_engine" / "data" / "benchmark_runs"
SAVED_RUN = RUNS / "ocr_saved_samples_20261111_110052.json"
SAMPLE_ZIP = ROOT / "QUALITY_REVIEW_SAMPLES.zip"
OUTPUT = RUNS / "ocr_saved_edge_recovery_review_20261111.json"


def _box_iou(first, second):
    ax1, ay1, ax2, ay2 = first
    bx1, by1, bx2, by2 = second
    intersection = max(0.0, min(ax2, bx2) - max(ax1, bx1)) * max(
        0.0, min(ay2, by2) - max(ay1, by1)
    )
    union = max(1.0, (ax2 - ax1) * (ay2 - ay1)
                + (bx2 - bx1) * (by2 - by1) - intersection)
    return intersection / union


def _suspicious_bubbles(engine, lines, regions):
    suspicious = set()
    for region in regions:
        if region.get("region_type") != "speech_bubble" or not region.get("bubble_bbox"):
            continue
        bx1, _by1, bx2, _by2 = map(float, region["bubble_bbox"])
        for line in lines:
            if line.get("bubble_id") != region.get("bubble_id"):
                continue
            line_box = (float(line["left"]), float(line["top"]),
                        float(line["left"]) + float(line["width"]),
                        float(line["top"]) + float(line["height"]))
            if not any(_box_iou(
                (float(box[0]), float(box[1]), float(box[0]) + float(box[2]),
                 float(box[1]) + float(box[3])), line_box,
            ) >= 0.60 for box in region.get("ocr_boxes", [])):
                continue
            line_left, _line_top, line_right, _line_bottom = line_box
            edge_margin = max(8.0, float(line["height"]) * 0.12)
            if not (line_left <= bx1 + edge_margin or line_right >= bx2 - edge_margin):
                continue
            valid, total = engine._english_word_counts(line.get("text", ""))
            if total and valid < total:
                suspicious.add(line["bubble_id"])
    return suspicious


def main():
    saved = json.loads(SAVED_RUN.read_text(encoding="utf-8"))
    engine = LocalRapidOCR()
    pages = []
    with zipfile.ZipFile(SAMPLE_ZIP) as archive:
        for saved_page in saved["results"]:
            page = saved_page["page"]
            image = cv2.imdecode(
                np.frombuffer(archive.read(f"{page}/original_english.webp"), np.uint8),
                cv2.IMREAD_COLOR,
            )
            if image is None:
                pages.append({"page": page, "error": "Could not decode saved original"})
                continue
            lines = copy.deepcopy(saved_page["lines"])
            regions = copy.deepcopy(saved_page["regions"])
            suspicious = _suspicious_bubbles(engine, lines, regions)
            before = [region.get("text", "") for region in regions]
            started = time.perf_counter()
            if suspicious:
                lines, regions, rescued = engine._recover_edge_clipped_bubble_lines(
                    image, lines, regions, suspicious,
                )
            else:
                rescued = 0
            elapsed = time.perf_counter() - started
            changes = [
                {"before": old, "after": region.get("text", ""),
                 "bubble_id": region.get("bubble_id")}
                for old, region in zip(before, regions)
                if old != region.get("text", "")
            ]
            rescued_lines = [
                {"text": line["text"],
                 "bbox": [round(float(line[key]), 2)
                          for key in ("left", "top", "width", "height")],
                 "confidence": round(float(line["confidence"]), 4)}
                for line in lines if line.get("ocr_edge_crop_rescued")
            ]
            pages.append({
                "page": page,
                "suspicious_bubble_ids": sorted(suspicious),
                "rescued_line_count": rescued,
                "crop_retry_elapsed_seconds": round(elapsed, 4),
                "changed_regions": changes,
                "rescued_lines": rescued_lines,
                "line_count": len(lines),
                "region_count": len(regions),
            })
    result = {
        "schema_version": 1,
        "scope": "Five archived pages; saved OCR plus targeted edge crops only; no full-page rerun and no translation",
        "source_ocr": SAVED_RUN.name,
        "source_images": f"{SAMPLE_ZIP.name}::* /original_english.webp",
        "pages": pages,
        "total_rescued_lines": sum(page.get("rescued_line_count", 0) for page in pages),
    }
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(OUTPUT), "pages": pages,
                      "total_rescued_lines": result["total_rescued_lines"]},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
