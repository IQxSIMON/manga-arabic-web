"""Inspect the public page's published image list for a selected chapter."""
import json, re
from urllib.request import Request, urlopen

url = "https://manhuaplus.org/manga/what-a-bountiful-harvest-demon-lord/chapter-93"
headers = {"User-Agent": "Mozilla/5.0 (compatible; MangaArabicOCR-QA/1.0)",
           "Accept": "application/json,*/*"}
with urlopen(Request(url, headers=headers), timeout=45) as response:
    html = response.read().decode("utf-8", errors="replace")
ids = re.findall(r"const CHAPTER_ID = (\d+);", html)
if not ids:
    raise SystemExit("Public chapter ID was not present.")
chapter_id = ids[0]
endpoint = "https://manhuaplus.org/ajax/image/list/chap/" + chapter_id
request = Request(endpoint, data=b"", headers=headers, method="POST")
with urlopen(request, timeout=45) as response:
    payload = json.loads(response.read().decode("utf-8", errors="replace"))
if payload.get("status") is not True or not isinstance(payload.get("html"), str):
    raise SystemExit("Image list endpoint returned an unexpected public response.")
pairs = re.findall(r'data-index="(\d+)"[^>]*>\s*<a href="([^"]+)"', payload["html"])
pairs.sort(key=lambda p: int(p[0]))
print("chapter_id=", chapter_id, "pages=", len(pairs))
print("indexes=", [int(i) for i,_ in pairs])
print("first_urls=", [url for _,url in pairs[:3]])
