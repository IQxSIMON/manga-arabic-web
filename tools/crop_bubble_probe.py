import json, sys
from pathlib import Path
from PIL import Image, ImageDraw
probe, overlay_dir, out_dir = map(Path, sys.argv[1:])
data=json.loads(probe.read_text(encoding="utf-8"))
out_dir.mkdir(parents=True, exist_ok=True)
for page in data["pages"]:
    source=overlay_dir / f"page_{page['page']:03d}.jpg"
    if not page["added"]: continue
    image=Image.open(source).convert("RGB")
    for n,item in enumerate(page["added"],1):
        x1,y1,x2,y2=item["bbox"]
        crop=image.crop((max(0,x1-120),max(0,y1-180),min(image.width,x2+120),min(image.height,y2+180)))
        ImageDraw.Draw(crop).rectangle((min(120,x1),min(180,y1),min(crop.width,x2-x1+120),min(crop.height,y2-y1+180)),outline=(255,0,220),width=6)
        path=out_dir / f"page_{page['page']:03d}_candidate_{n}.jpg"
        crop.save(path,quality=95)
        print(path)
