from __future__ import annotations
import hashlib, json, re, zipfile
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urlparse
from PIL import Image
from io import BytesIO

ROOT=Path(r"D:\MangaArabic\manga-arabic-web")
PAGE_URL="https://manhuaplus.org/manga/mercenary-enrollment/chapter-239"
LIST_URL="https://manhuaplus.org/ajax/image/list/chap/73257"
CDN_HOST="cdn.manhuaplus.cc"
OUT_DIR=ROOT/"ocr_test_results/site_web_samples_20261114/mercenary_enrollment"
ARCHIVE=OUT_DIR/"mercenary_enrollment_chapter239.cbz"
MANIFEST=ROOT/"ocr_test_results/chapter_index.json"
UA="Mozilla/5.0 (compatible; MangaArabicOCR-QA/1.0)"

def get(url, data=None):
    req=Request(url,data=data,headers={"User-Agent":UA,"Accept":"application/json, image/webp, */*"},
                method="POST" if data is not None else "GET")
    with urlopen(req,timeout=45) as response:
        if response.status!=200: raise RuntimeError(f"HTTP {response.status} for allowed public page resource")
        return response.read(),response.headers.get("Content-Type","")

def main():
    OUT_DIR.mkdir(parents=True,exist_ok=True)
    raw,_=get(LIST_URL,data=b"")
    response=json.loads(raw.decode("utf-8"))
    if response.get("status") is not True or not isinstance(response.get("html"),str):
        raise RuntimeError("Public chapter image list returned an unexpected response.")
    pairs=re.findall(r'data-index="(\d+)"[^>]*>\s*<a href="([^"]+)"',response["html"])
    # The page emits each separator's data-index before its image link.
    pairs.sort(key=lambda p:int(p[0]))
    indexes=[int(i) for i,_ in pairs]
    if not pairs or indexes!=list(range(len(pairs))):
        raise RuntimeError(f"Chapter page index sequence is incomplete or duplicated: {indexes}")
    for _,url in pairs:
        parsed=urlparse(url)
        if parsed.scheme!="https" or parsed.hostname!=CDN_HOST or not parsed.path.lower().endswith(".webp"):
            raise RuntimeError("Image URL outside the site's published CDN/WebP format; stopped.")
    pages=[]
    with zipfile.ZipFile(ARCHIVE,"w",compression=zipfile.ZIP_STORED) as zf:
        for idx,url in pairs:
            data,ctype=get(url)
            with Image.open(BytesIO(data)) as im:
                fmt=im.format; width,height=im.size
                im.verify()
            if fmt!="WEBP" or not width or not height:
                raise RuntimeError(f"Unexpected image payload at chapter index {idx}.")
            member=f"page_{int(idx)+1:03d}.webp"
            zf.writestr(member,data)
            pages.append({"page":int(idx)+1,"member":member,"bytes":len(data),
                          "sha256":hashlib.sha256(data).hexdigest(),
                          "width":width,"height":height,"format":"webp",
                          "source_url":url,"content_type":ctype})
    with zipfile.ZipFile(ARCHIVE,"r") as zf:
        bad=zf.testzip()
        if bad: raise RuntimeError(f"CBZ integrity check failed at {bad}")
    manifest={"schema":1,"source_unchanged":True,"supported_raster_suffixes":
        [".jpg",".jpeg",".png",".webp",".bmp",".tif",".tiff"],"archives":[]}
    if MANIFEST.is_file():
        manifest=json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest.setdefault("archives",[])
    manifest["archives"]=[x for x in manifest["archives"] if x.get("chapter")!=ARCHIVE.name]
    manifest["archives"].append({"chapter":ARCHIVE.name,"status":"indexed",
      "archive_bytes":ARCHIVE.stat().st_size,"archive_sha256":hashlib.sha256(ARCHIVE.read_bytes()).hexdigest(),
      "page_count":len(pages),"formats":{" .webp".strip():len(pages)},
      "source_page":PAGE_URL,"source_origin":"ManhuaPlus public chapter page and its page-listed CDN",
      "story_series":"Mercenary Enrollment","chapter_label":"Chapter 239",
      "vertical_layout_basis":"listed in Long Strip category and tagged Webtoon",
      "pages":pages})
    MANIFEST.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"status":"complete","chapter":ARCHIVE.name,"pages":len(pages),
      "archive_bytes":ARCHIVE.stat().st_size,
      "all_vertical_strip_images":all(p["height"]>=p["width"] for p in pages),
      "dimensions":[[p["width"],p["height"]] for p in pages],
      "archive":str(ARCHIVE),"manifest":str(MANIFEST),
      "content_downloaded_only_from_page_listed_host":CDN_HOST},ensure_ascii=False))
if __name__=="__main__": main()
