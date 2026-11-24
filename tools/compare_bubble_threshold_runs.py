"""Compare chapter OCR before/after a bubble-confidence threshold adjustment."""
import argparse
import json
from collections import Counter
from pathlib import Path
import sys
sys.stdout.reconfigure(encoding="utf-8")

p = argparse.ArgumentParser()
p.add_argument("before", type=Path)
p.add_argument("after", type=Path)
p.add_argument("--out", type=Path, required=True)
args = p.parse_args()
before = json.loads(args.before.read_text(encoding="utf-8"))
after = json.loads(args.after.read_text(encoding="utf-8"))
old_pages = {x["page"]: x for x in before["pages"]}
new_pages = {x["page"]: x for x in after["pages"]}
old_total = sum(len(x.get("regions", [])) for x in before["pages"])
new_total = sum(len(x.get("regions", [])) for x in after["pages"])
old_types = Counter(r.get("region_type", "?") for p0 in before["pages"] for r in p0.get("regions", []))
new_types = Counter(r.get("region_type", "?") for p0 in after["pages"] for r in p0.get("regions", []))
lines = [
    "# Bubble confidence threshold comparison — Chapter 19", "",
    f"Baseline: {before['pages_completed']}/{before['pages_expected']} pages, {old_total} regions, "
    f"{before['total_seconds']:.3f}s, peak RSS {before['peak_rss_mib']:.1f} MiB.",
    f"After separate relaxed candidate classification: {after['pages_completed']}/{after['pages_expected']} pages, {new_total} regions, "
    f"{after['total_seconds']:.3f}s, peak RSS {after['peak_rss_mib']:.1f} MiB.",
    f"Delta: {new_total-old_total:+d} regions, "
    f"{after['total_seconds']-before['total_seconds']:+.3f}s, "
    f"{after['peak_rss_mib']-before['peak_rss_mib']:+.1f} MiB.",
    "", f"Types before: {dict(old_types)}", f"Types after: {dict(new_types)}", "",
    "| Page | Bubble masks before → after | Regions before → after | Exact OCR strings changed | Same-string type changes |",
    "|---:|---:|---:|---:|---:|"
]
text_changes = []
for n in sorted(set(old_pages) | set(new_pages)):
    old, new = old_pages.get(n, {}), new_pages.get(n, {})
    a, b = old.get("regions", []), new.get("regions", [])
    ca, cb = Counter(x.get("text", "") for x in a), Counter(x.get("text", "") for x in b)
    missing = list((ca - cb).elements())
    added = list((cb - ca).elements())
    old_by_text = {}
    new_by_text = {}
    for row in a:
        old_by_text.setdefault(row.get("text", ""), []).append(row.get("region_type"))
    for row in b:
        new_by_text.setdefault(row.get("text", ""), []).append(row.get("region_type"))
    retyped = sum(1 for value in set(old_by_text) & set(new_by_text)
                  if sorted(old_by_text[value]) != sorted(new_by_text[value]))
    lines.append(f"| {n} | {old.get('bubble_count', 0)} → {new.get('bubble_count', 0)} | "
                 f"{len(a)} → {len(b)} | {len(missing)+len(added)} | {retyped} |")
    if missing or added:
        text_changes.append(f"- Page {n}: removed strings {missing}; added strings {added}.")
lines += ["", "## Text changes", ""]
lines += text_changes if text_changes else ["No exact OCR strings were added or removed."]
lines += [
    "", "The reference review for this sample contains manually verified examples rather than a complete transcription. "
    "An increase in accepted masks is not itself a precision score. The threshold probe's newly accepted proposals "
    "were manually checked in the saved crops: five were clear dialogue balloons and one covered manuscript writing. "
    "See MANUAL_VISUAL_REVIEW.md and bubble_threshold_probe.json.", ""
]
args.out.parent.mkdir(parents=True, exist_ok=True)
args.out.write_text("\n".join(lines), encoding="utf-8")
print(f"regions {old_total}->{new_total}; types {dict(old_types)}->{dict(new_types)}")
print(f"time {before['total_seconds']:.3f}->{after['total_seconds']:.3f}s; "
      f"peak {before['peak_rss_mib']:.1f}->{after['peak_rss_mib']:.1f} MiB")
print("\n".join(lines[9:]))
