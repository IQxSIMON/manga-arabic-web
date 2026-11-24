"""Compare two saved per-chapter OCR runs and produce transparent summary metrics."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("baseline", type=Path)
    parser.add_argument("after", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    before = json.loads(args.baseline.read_text(encoding="utf-8"))
    after = json.loads(args.after.read_text(encoding="utf-8"))
    before_pages = {page["page"]: page for page in before["pages"]}
    after_pages = {page["page"]: page for page in after["pages"]}
    before_regions = sum(len(page["regions"]) for page in before["pages"])
    after_regions = sum(len(page["regions"]) for page in after["pages"])
    before_reviews = sum(bool(row.get("ocr_review_required"))
                         for page in before["pages"] for row in page["regions"])
    after_reviews = sum(bool(row.get("ocr_review_required"))
                        for page in after["pages"] for row in page["regions"])
    changed = []
    page_rows = []
    for number in sorted(set(before_pages) | set(after_pages)):
        old = before_pages.get(number, {}).get("regions", [])
        new = after_pages.get(number, {}).get("regions", [])
        count = sum(a.get("text") != b.get("text")
                    for a, b in zip(old, new))
        changed.append(count)
        page_rows.append(
            f"| {number} | {len(old)} | {len(new)} | {count} |"
        )
        for idx, (a, b) in enumerate(zip(old, new), 1):
            if a.get("text") != b.get("text"):
                page_rows.append(
                    f"| ↳ {idx} | {a.get('text', '').replace('|', '/')} | "
                    f"{b.get('text', '').replace('|', '/')} | text changed |"
                )
    lines = [
        "# Chapter 239 OCR: baseline vs targeted retry",
        "",
        f"Baseline: {before['pages_completed']}/{before['pages_expected']} pages, "
        f"{before['total_seconds']:.3f}s, peak RSS {before['peak_rss_mib']:.1f} MiB, "
        f"{before_regions} regions, {before_reviews} review-flagged regions.",
        f"After: {after['pages_completed']}/{after['pages_expected']} pages, "
        f"{after['total_seconds']:.3f}s, peak RSS {after['peak_rss_mib']:.1f} MiB, "
        f"{after_regions} regions, {after_reviews} review-flagged regions.",
        f"Delta: {after['total_seconds'] - before['total_seconds']:+.3f}s; "
        f"{after['peak_rss_mib'] - before['peak_rss_mib']:+.1f} MiB peak RSS.",
        "",
        "All pages were processed sequentially. This run used only the already-installed OCR models; "
        "no translation API or model download was used.",
        "",
        "## Per-page comparison",
        "",
        "| Page | Baseline regions | After regions | Text changes at same sorted region position |",
        "|---:|---:|---:|---:|",
        *page_rows,
        "",
        "Same-position comparison is a diagnostic, not a geometric region matching metric. "
        "Human-reviewed examples are documented in MANUAL_VISUAL_REVIEW.md. The reference set is "
        "selected, not complete, so no chapter-wide accuracy/recall percentage is claimed.",
        "",
        "## Interpretation",
        "",
        "The targeted second-read path increased review metadata coverage but did not reliably correct "
        "the visually confirmed difficult readings in this chapter. Treat flagged text as needing "
        "human confirmation; confidence alone is not a correctness score. Bubble detection covered the "
        "dialogue boxes in the reviewed examples, while stylized character recognition remains the main "
        "open failure mode. The chapter is fully processed and audited, but the detector/OCR is not yet "
        "ready for unattended production translation.",
        "",
    ]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {args.out}")
    print(f"regions {before_regions}->{after_regions}; review flags "
          f"{before_reviews}->{after_reviews}; text changes {sum(changed)}")
    print(f"time {before['total_seconds']:.3f}->{after['total_seconds']:.3f}s; "
          f"peak RSS {before['peak_rss_mib']:.1f}->{after['peak_rss_mib']:.1f} MiB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
