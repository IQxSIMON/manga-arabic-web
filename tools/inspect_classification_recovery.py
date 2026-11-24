import json, sys
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")
before, after = [json.loads(Path(p).read_text(encoding="utf-8")) for p in sys.argv[1:3]]
for name, data in (("before",before),("after",after)):
 print("\\n"+name)
 for page in data["pages"]:
  for i,row in enumerate(page["regions"],1):
   if row.get("bubble_classification_recovered") or page["page"] in (8,11):
    print(page["page"],i,row.get("region_type"),repr(row.get("text")),row.get("bubble_bbox"),row.get("bubble_confidence"),row.get("bubble_classification_recovered"))
