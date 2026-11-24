import json, sys
from pathlib import Path
a, b = [json.loads(Path(x).read_text(encoding="utf-8")) for x in sys.argv[1:3]]
for label, data in (("before",a),("after",b)):
    page=next(p for p in data["pages"] if p["page"]==9)
    print("\\n"+label)
    for i,r in enumerate(page["regions"],1):
        print(i,r.get("region_type"),repr(r.get("text")), "bbox", [r.get("left"),r.get("top"),r.get("width"),r.get("height")], "bubble",r.get("bubble_bbox"),"conf",r.get("confidence"))
