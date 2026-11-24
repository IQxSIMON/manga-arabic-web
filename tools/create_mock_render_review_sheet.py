"""Build a compact before/after sheet for regions skipped by mock chapter rendering."""
from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ocr-json", type=Path, default=ROOT / "ocr_test_results/post_tiny_lowercase_filter/11/ocr_results.json")
    parser.add_argument("--metrics", type=Path, default=ROOT / "ocr_test_results/mock_render/chapter11_20261113/metrics.json")
    parser.add_argument("--archive", type=Path, default=Path(r"C:\Users\BMW1975\Downloads\11.cbz"))
    parser.add_argument("--render-dir", type=Path, default=ROOT / "ocr_test_results/mock_render/chapter11_20261113")
    parser.add_argument("--output", type=Path, default=ROOT / "ocr_test_results/mock_render/chapter11_20261113/skipped_region_review.jpg")
    args = parser.parse_args()
    ocr = json.loads(args.ocr_json.read_text(encoding="utf-8"))
    metrics = json.loads(args.metrics.read_text(encoding="utf-8"))
    missed = []
    for page_result in metrics["pages"]:
        indices = set(page_result.get("skipped_region_indices", []))
        if not indices:
            continue
        source_page = next(item for item in ocr["pages"] if item["page"] == page_result["page"])
        sorted_regions = sorted(source_page["regions"], key=lambda r: int(r.get("reading_order", 0)))
        for index in sorted(indices):
            if 1 <= index <= len(sorted_regions):
                missed.append((page_result["page"], index, sorted_regions[index - 1]))

    if not missed:
        raise SystemExit("No skipped regions to review.")
    panels = []
    with zipfile.ZipFile(args.archive) as archive:
        for page, index, region in missed:
            source_data = archive.read(next(p["source_member"] for p in ocr["pages"] if p["page"] == page))
            source = Image.open(__import__("io").BytesIO(source_data)).convert("RGB")
            output = Image.open(args.render_dir / "pages" / f"page_{page:03d}.png").convert("RGB")
            x1, y1 = int(region["left"]), int(region["top"])
            x2, y2 = x1 + int(region["width"]), y1 + int(region["height"])
            pad_x, pad_y = max(30, (x2-x1)//2), max(24, (y2-y1)//2)
            box = (max(0,x1-pad_x), max(0,y1-pad_y), min(source.width,x2+pad_x), min(source.height,y2+pad_y))
            before, after = source.crop(box), output.crop(box)
            panel_w = 640
            scale = min(panel_w / max(before.width, after.width), 480 / max(before.height, after.height), 1)
            size = (max(1,round(before.width*scale)), max(1,round(before.height*scale)))
            before, after = before.resize(size), after.resize(size)
            title = f"p{page:02d} r{index:02d} | {region.get('region_type')} | {region.get('text','')[:90]}"
            panels.append((title, before, after))
    row_h = max(max(a.height,b.height) for _,a,b in panels) + 44
    sheet = Image.new("RGB", (1300, row_h*len(panels)), "white")
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 18)
    except OSError:
        font = ImageFont.load_default()
    for i,(title,before,after) in enumerate(panels):
        y=i*row_h
        draw.text((8,y+3),title,fill="black",font=font)
        draw.text((8,y+25),"SOURCE",fill="#444",font=font)
        draw.text((654,y+25),"MOCK RENDER",fill="#444",font=font)
        sheet.paste(before,(8,y+44))
        sheet.paste(after,(654,y+44))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    sheet.save(args.output,quality=88,optimize=True)
    print(json.dumps({"skipped_regions":len(panels),"output":str(args.output)},ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
