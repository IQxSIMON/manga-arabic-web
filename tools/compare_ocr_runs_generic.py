"""Compare two saved OCR chapter runs without asserting ground-truth accuracy."""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
parser = argparse.ArgumentParser()
parser.add_argument("before", type=Path)
parser.add_argument("after", type=Path)
parser.add_argument("--out", type=Path, required=True)
args = parser.parse_args()
before = json.loads(args.before.read_text(encoding="utf-8"))
after = json.loads(args.after.read_text(encoding="utf-8"))
before_pages = {page["page"]: page for page in before["pages"]}
after_pages = {page["page"]: page for page in after["pages"]}
old_rows = [row for page in before["pages"] for row in page.get("regions", [])]
new_rows = [row for page in after["pages"] for row in page.get("regions", [])]
old_types = Counter(row.get("region_type", "unknown") for row in old_rows)
new_types = Counter(row.get("region_type", "unknown") for row in new_rows)
same_text_retyped = []
changed_texts = []
lines = [
    f"# OCR comparison — {before.get('chapter', 'chapter')}",
    "",
    f"Baseline: {before.get('pages_completed')}/{before.get('pages_expected')} pages, "
    f"{len(old_rows)} regions, {before.get('total_seconds', 0):.3f}s, "
    f"peak RSS {before.get('peak_rss_mib', 0):.1f} MiB.",
    f"After: {after.get('pages_completed')}/{after.get('pages_expected')} pages, "
    f"{len(new_rows)} regions, {after.get('total_seconds', 0):.3f}s, "
    f"peak RSS {after.get('peak_rss_mib', 0):.1f} MiB.",
    f"Delta: {len(new_rows)-len(old_rows):+d} regions, "
    f"{after.get('total_seconds', 0)-before.get('total_seconds', 0):+.3f}s, "
    f"{after.get('peak_rss_mib', 0)-before.get('peak_rss_mib', 0):+.1f} MiB peak RSS.",
    "",
    f"Types before: {dict(old_types)}",
    f"Types after: {dict(new_types)}",
    "",
    "| Page | Regions before → after | Exact OCR strings added | Exact OCR strings removed |",
    "|---:|---:|---:|---:|",
]
for number in sorted(set(before_pages) | set(after_pages)):
    old = before_pages.get(number, {}).get("regions", [])
    new = after_pages.get(number, {}).get("regions", [])
    old_text = Counter(row.get("text", "") for row in old)
    new_text = Counter(row.get("text", "") for row in new)
    added = list((new_text - old_text).elements())
    removed = list((old_text - new_text).elements())
    lines.append(f"| {number} | {len(old)} → {len(new)} | {len(added)} | {len(removed)} |")
    if added or removed:
        changed_texts.append(f"- Page {number}: added={added!r}; removed={removed!r}")
for old in old_rows:
    matches = [new for new in new_rows if new.get("text") == old.get("text")]
    if matches and not any(new.get("region_type") == old.get("region_type") for new in matches):
        same_text_retyped.append((old.get("text"), old.get("region_type"),
                                  matches[0].get("region_type")))
lines += ["", "## OCR text differences", ""]
lines += changed_texts or ["No exact OCR strings were added or removed."]
lines += ["", "## Type changes for unchanged text", ""]
lines += [
    f"- {text!r}: {old_type} → {new_type}"
    for text, old_type, new_type in same_text_retyped
] or ["No exact-text type changes detected."]
lines += [
    "",
    "This is a deterministic output comparison, not a human accuracy score. See the chapter's "
    "manual visual review for selected human-confirmed reference examples. Exact string differences "
    "can include region grouping changes; each changed page should be visually checked.",
    "",
]
args.out.parent.mkdir(parents=True, exist_ok=True)
args.out.write_text("\n".join(lines), encoding="utf-8")
print(f"regions {len(old_rows)}->{len(new_rows)}; types {dict(old_types)}->{dict(new_types)}")
print(f"time {before.get('total_seconds')}->{after.get('total_seconds')}s; "
      f"peak {before.get('peak_rss_mib')}->{after.get('peak_rss_mib')} MiB")
print(f"report={args.out}")
