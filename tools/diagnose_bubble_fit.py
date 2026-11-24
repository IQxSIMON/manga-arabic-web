"""Inspect whether a skipped speech-bubble translation fits alternate safe bounds.

Usage: python tools/diagnose_bubble_fit.py <chapter-directory> <page> <region-index>
The tool reads the stored source page and OCR result and never writes output.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cv2
import numpy as np

from image_pipeline.renderer import (
    _bubble_glyphs,
    _bubble_mask_in_bounds,
    _font_path,
    _text_layout_bounds,
)
from ocr_engine.speech_bubbles import detect_speech_bubbles


def main() -> int:
    if len(sys.argv) not in (4, 5):
        print(__doc__)
        return 2
    chapter = Path(sys.argv[1])
    page_number = int(sys.argv[2])
    region_index = int(sys.argv[3])
    result = json.loads((chapter / "results" / f"page-{page_number:04}.json").read_text("utf-8"))
    image_bytes = (chapter / f"page-{page_number:04}.webp").read_bytes()
    image = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
    regions = result["translated_lines"]
    region = next(item for item in regions if item.get("index") == region_index)
    bubbles = detect_speech_bubbles(image)
    bubble = bubbles[int(region["bubble_id"]) - 1]
    bbox = tuple(int(v) for v in (region.get("bubble_bbox") or bubble.bbox))
    full_mask = _bubble_mask_in_bounds(bubble, bbox)
    anchor = _text_layout_bounds(region, bbox)
    ax1, ay1, ax2, ay2 = anchor
    anchor_mask = full_mask[ay1 - bbox[1]:ay2 - bbox[1], ax1 - bbox[0]:ax2 - bbox[0]]
    text = str(region.get("translated", ""))
    font = _font_path()
    # Conservative centered ellipse is a diagnostic candidate: it models the
    # usable interior of an oval balloon without allowing glyphs near corners.
    yy, xx = np.ogrid[:full_mask.shape[0], :full_mask.shape[1]]
    ellipse = (((xx + 0.5 - full_mask.shape[1] / 2) / (full_mask.shape[1] * 0.43)) ** 2
               + ((yy + 0.5 - full_mask.shape[0] / 2) / (full_mask.shape[0] * 0.40)) ** 2) <= 1
    ellipse_mask = ellipse.astype(np.uint8) * 255
    if len(sys.argv) == 5:
        overlay = image[bbox[1]:bbox[3], bbox[0]:bbox[2]].copy()
        detected = full_mask > 0
        candidate = ellipse
        overlay[detected] = (
            overlay[detected].astype(np.float32) * 0.55
            + np.array([70, 210, 70], dtype=np.float32) * 0.45
        ).astype(np.uint8)
        outside = candidate & ~detected
        overlay[outside] = (
            overlay[outside].astype(np.float32) * 0.45
            + np.array([40, 40, 230], dtype=np.float32) * 0.55
        ).astype(np.uint8)
        if not cv2.imwrite(sys.argv[4], overlay):
            raise OSError(f"could not write diagnostic image: {sys.argv[4]}")
    print(json.dumps({
        "page": page_number,
        "region": region_index,
        "source": region.get("text"),
        "target": text,
        "bubble_bbox": bbox,
        "bubble_confidence": region.get("bubble_confidence"),
        "anchor_bounds": anchor,
        "ocr_box_count": len(region.get("ocr_boxes") or []),
        "source_confidence": region.get("confidence"),
        "target_characters": len(text),
        "full_bubble_safe_fraction": float(np.count_nonzero(full_mask) / max(1, full_mask.size)),
        "anchor_safe_fraction": float(np.count_nonzero(anchor_mask) / max(1, anchor_mask.size)),
        "center_ellipse_inside_detected_fraction": float(
            np.count_nonzero(ellipse & (full_mask > 0)) / max(1, np.count_nonzero(ellipse))
        ),
        "anchor_fit_font": (_bubble_glyphs(text, anchor_mask, anchor, font) or (None, None))[1],
        "full_bubble_fit_font": (_bubble_glyphs(text, full_mask, bbox, font) or (None, None))[1],
        "center_ellipse_fit_font": (_bubble_glyphs(text, ellipse_mask, bbox, font) or (None, None))[1],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
