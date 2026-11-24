"""Inspect speech-segmenter masks on the saved page-10 long strip."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cv2
import numpy as np

from ocr_engine.speech_bubbles import detect_speech_bubbles

DIR = ROOT / "translator_engine" / "data" / "benchmark_runs" / "page10_manual_extension_20261112"


def main() -> int:
    source = cv2.imread(str(DIR / "original_english.webp"), cv2.IMREAD_COLOR)
    if source is None:
        raise FileNotFoundError("Could not read saved page 10")
    bubbles = detect_speech_bubbles(source)
    overlay = cv2.cvtColor(source, cv2.COLOR_BGR2RGB)
    from PIL import Image, ImageDraw, ImageFont
    canvas = Image.fromarray(overlay)
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.truetype("arial.ttf", 36)
    records = []
    for index, bubble in enumerate(bubbles, 1):
        x1, y1, x2, y2 = bubble.bbox
        mask = bubble.safe_mask
        records.append({
            "id": index, "bbox": list(bubble.bbox), "confidence": round(bubble.confidence, 4),
            "mask_shape": list(mask.shape), "mask_fill": round(float(np.count_nonzero(mask)/mask.size), 4),
            "touches_page_top": y1 <= 1, "touches_page_bottom": y2 >= source.shape[0]-1,
        })
        draw.rectangle((x1,y1,x2,y2),outline=(0,255,50),width=6)
        draw.rectangle((x1,y1,x1+52,y1+48),fill=(0,110,35))
        draw.text((x1+5,y1+3),str(index),fill="white",font=font)
    for index, top in enumerate(range(0, source.shape[0], 3250), 1):
        tile = canvas.crop((0,top,source.shape[1],min(top+3250,source.shape[0])))
        tile.thumbnail((650,2700))
        tile.save(DIR/f"mask_overlay_{index}.jpg",quality=92)
    report={"image_size":[int(source.shape[1]),int(source.shape[0])],"bubble_count":len(records),"bubbles":records}
    (DIR/"bubble_mask_debug.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
