"""Create before/after crops for representative pages in a mock render run."""
from __future__ import annotations
import argparse
import io
import json
import zipfile
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT=Path(__file__).resolve().parents[1]

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--render-dir",type=Path,default=ROOT/"ocr_test_results/mock_render/chapter11_20261113_final")
    p.add_argument("--output",type=Path,default=ROOT/"ocr_test_results/mock_render/chapter11_20261113_final/representative_review.jpg")
    a=p.parse_args()
    ocr=json.loads((ROOT/"ocr_test_results/post_tiny_lowercase_filter/11/ocr_results.json").read_text(encoding="utf-8"))
    bypage={x["page"]:x for x in ocr["pages"]}
    chosen=[]
    for page_no in (2,10):
        page=bypage[page_no]
        regions=[r for r in page["regions"] if r.get("region_type")=="speech_bubble"]
        region=regions[0]
        chosen.append((page_no,region))
    panels=[]
    with zipfile.ZipFile(r"C:\Users\BMW1975\Downloads\11.cbz") as z:
        for page_no,r in chosen:
            data=z.read(bypage[page_no]["source_member"])
            source=Image.open(io.BytesIO(data)).convert("RGB")
            result=Image.open(a.render_dir/"pages"/f"page_{page_no:03d}.png").convert("RGB")
            x1,y1,x2,y2=(r.get("bubble_bbox") or [r["left"],r["top"],r["left"]+r["width"],r["top"]+r["height"]])
            padx,pady=max(20,(x2-x1)//12),max(20,(y2-y1)//12)
            box=(max(0,x1-padx),max(0,y1-pady),min(source.width,x2+padx),min(source.height,y2+pady))
            before,after=source.crop(box),result.crop(box)
            scale=min(620/max(before.width,after.width),350/max(before.height,after.height),1)
            size=(max(1,round(before.width*scale)),max(1,round(before.height*scale)))
            panels.append((page_no,before.resize(size),after.resize(size)))
    height=0
    for _,b,_ in panels: height+=max(400,b.height+40)
    sheet=Image.new("RGB",(1280,height),"white");draw=ImageDraw.Draw(sheet)
    try:font=ImageFont.truetype("C:/Windows/Fonts/arial.ttf",18)
    except OSError:font=ImageFont.load_default()
    y=0
    for page,b,after in panels:
        draw.text((8,y+2),f"Page {page} — SOURCE",fill="black",font=font)
        draw.text((650,y+2),f"Page {page} — MOCK RENDER",fill="black",font=font)
        sheet.paste(b,(8,y+28));sheet.paste(after,(650,y+28));y+=max(400,b.height+40)
    a.output.parent.mkdir(parents=True,exist_ok=True)
    sheet.save(a.output,quality=90,optimize=True)
    print(json.dumps({"output":str(a.output),"panels":len(panels)}))
if __name__=="__main__":main()
