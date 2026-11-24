"""Probe whether more vertical context recovers clipped page-10 bubbles."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from ocr_engine.speech_bubbles import _detect_speech_bubbles_single

DIR = ROOT / "translator_engine" / "data" / "benchmark_runs" / "page10_manual_extension_20261112"


def main() -> int:
    source = cv2.imread(str(DIR / "original_english.webp"), cv2.IMREAD_COLOR)
    if source is None:
        raise FileNotFoundError("page 10 source")
    results = []
    font = ImageFont.truetype("arial.ttf", 32)
    for name, y0, y1, pad_top in (
        ("top_with_white_context", 0, 1700, 240),
        ("seam_full_context", 2100, 4000, 0),
    ):
        crop = source[y0:y1].copy()
        if pad_top:
            crop = cv2.copyMakeBorder(crop, pad_top, 0, 0, 0, cv2.BORDER_CONSTANT, value=(255,255,255))
        bubbles = _detect_speech_bubbles_single(crop, 0.25)
        records = []
        canvas = Image.fromarray(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB))
        draw = ImageDraw.Draw(canvas)
        for index, bubble in enumerate(bubbles, 1):
            x1, y1b, x2, y2b = bubble.bbox
            records.append({"id":index,"crop_bbox":list(bubble.bbox),"confidence":round(bubble.confidence,4),"safe_mask_shape":list(bubble.safe_mask.shape)})
            draw.rectangle((x1,y1b,x2,y2b),outline=(0,255,50),width=6)
            draw.rectangle((x1,y1b,x1+48,y1b+45),fill=(0,110,35))
            draw.text((x1+4,y1b+2),str(index),fill="white",font=font)
        canvas.thumbnail((650,2600))
        canvas.save(DIR/f"{name}_bubbles.jpg",quality=92)
        results.append({"name":name,"source_y_range":[y0,y1],"top_white_padding":pad_top,"bubble_count":len(records),"bubbles":records})
    output={"mode":"two isolated speech-mask context probes; no OCR or translation","results":results}
    (DIR/"bubble_context_probe.json").write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(output,ensure_ascii=False))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
