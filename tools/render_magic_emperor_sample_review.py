"""Render compact numbered OCR overlays and bubble-crop sheets for sample review."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = ROOT / "translator_engine" / "data" / "benchmark_runs" / "magic_emperor_920_sample"


def main() -> int:
    for page in (1, 2):
        image = Image.open(SAMPLE_DIR / f"chapter920_page{page}.webp").convert("RGB")
        original = image.copy()
        data = json.loads((SAMPLE_DIR / f"chapter920_page{page}_ocr.json").read_text(encoding="utf-8"))
        draw = ImageDraw.Draw(image)
        font = ImageFont.truetype("arial.ttf", 38)
        for index, region in enumerate(data.get("regions", []), 1):
            x, y, w, h = map(int, (region["left"], region["top"], region["width"], region["height"]))
            draw.rectangle((x, y, x + w, y + h), outline=(0, 255, 60), width=8)
            draw.rectangle((x, y, x + 58, y + 52), fill=(0, 120, 40))
            draw.text((x + 7, y + 3), str(index), font=font, fill=(255, 255, 255))

        for part, top in enumerate(range(0, image.height, 3600), 1):
            bottom = min(top + 3600, image.height)
            overlay = image.crop((0, top, image.width, bottom))
            overlay.thumbnail((620, 2600))
            overlay.save(SAMPLE_DIR / f"chapter920_page{page}_overlay_{part}.jpg", quality=90)

        crops = []
        for index, region in enumerate(data.get("regions", []), 1):
            x, y, w, h = map(int, (region["left"], region["top"], region["width"], region["height"]))
            pad_x, pad_y = max(28, w // 8), max(24, h // 7)
            crop = original.crop((max(0, x-pad_x), max(0, y-pad_y), min(image.width, x+w+pad_x), min(image.height, y+h+pad_y)))
            crop.thumbnail((330, 260))
            tile = Image.new("RGB", (350, 300), "white")
            tile.paste(crop, ((350-crop.width)//2, 32+(260-crop.height)//2))
            ImageDraw.Draw(tile).text((8, 5), f"{index}. {region.get('text','')[:42]}", fill="black")
            crops.append(tile)
        sheet = Image.new("RGB", (350*2, 300*((len(crops)+1)//2)), (235,235,235))
        for index, tile in enumerate(crops):
            sheet.paste(tile, ((index%2)*350, (index//2)*300))
        sheet.save(SAMPLE_DIR / f"chapter920_page{page}_bubble_crops.jpg", quality=92)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
