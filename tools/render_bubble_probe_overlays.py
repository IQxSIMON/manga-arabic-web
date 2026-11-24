"""Draw relaxed-threshold proposal boxes over saved OCR review overlays."""
import json
import sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

if len(sys.argv) != 4:
    raise SystemExit("usage: render_bubble_probe_overlays.py probe.json overlays_dir output_dir")
probe_path, overlay_dir, output_dir = map(Path, sys.argv[1:])
data = json.loads(probe_path.read_text(encoding="utf-8"))
output_dir.mkdir(parents=True, exist_ok=True)
font = ImageFont.load_default()
for page in data["pages"]:
    if not page["added"]:
        continue
    source = overlay_dir / f"page_{page['page']:03d}.jpg"
    image = Image.open(source).convert("RGB")
    draw = ImageDraw.Draw(image)
    for index, proposal in enumerate(page["added"], 1):
        x1, y1, x2, y2 = proposal["bbox"]
        draw.rectangle((x1, y1, x2, y2), outline=(255, 0, 220), width=10)
        draw.text((x1 + 4, y1 + 4), f"RELAXED {index} {proposal['confidence']:.2f}",
                  fill=(255, 0, 220), stroke_width=2, stroke_fill=(255,255,255), font=font)
    target = output_dir / source.name
    image.save(target, quality=90)
    print(f"saved={target}")
