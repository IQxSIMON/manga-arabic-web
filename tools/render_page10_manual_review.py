"""Render a numbered bubble overlay and crops for the page-10 audit."""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
DIR = ROOT / "translator_engine" / "data" / "benchmark_runs" / "page10_manual_extension_20261112"


def main() -> int:
    source = Image.open(DIR / "original_english.webp").convert("RGB")
    output = json.loads((DIR / "speech_ocr_current.json").read_text(encoding="utf-8"))
    overlay = source.copy()
    draw = ImageDraw.Draw(overlay)
    font = ImageFont.truetype("arial.ttf", 36)
    for index, region in enumerate(output.get("regions", []), 1):
        x, y = int(region["left"]), int(region["top"])
        w, h = int(region["width"]), int(region["height"])
        draw.rectangle((x, y, x+w, y+h), outline=(0, 255, 50), width=6)
        draw.rectangle((x, y, x+52, y+48), fill=(0, 110, 35))
        draw.text((x+5, y+3), str(index), fill="white", font=font)
    for index, top in enumerate(range(0, source.height, 3250), 1):
        tile = overlay.crop((0, top, source.width, min(top+3250, source.height)))
        tile.thumbnail((650, 2700))
        tile.save(DIR / f"overlay_{index}.jpg", quality=92)

    tiles = []
    for index, region in enumerate(output.get("regions", []), 1):
        x, y, w, h = map(int, (region["left"], region["top"], region["width"], region["height"]))
        px, py = max(24, w//8), max(24, h//8)
        crop = source.crop((max(0,x-px), max(0,y-py), min(source.width,x+w+px), min(source.height,y+h+py)))
        crop.thumbnail((360,270))
        tile=Image.new("RGB",(380,320),"white")
        tile.paste(crop,((380-crop.width)//2,40+(270-crop.height)//2))
        ImageDraw.Draw(tile).text((5,5),f"{index}. {region['text'][:40]}",fill="black")
        tiles.append(tile)
    sheet=Image.new("RGB",(760,320*((len(tiles)+1)//2)),(235,235,235))
    for index,tile in enumerate(tiles): sheet.paste(tile,((index%2)*380,(index//2)*320))
    sheet.save(DIR/"bubble_crops.jpg",quality=92)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
