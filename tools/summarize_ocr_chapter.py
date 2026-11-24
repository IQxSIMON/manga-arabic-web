"""Print a compact page-by-page OCR inventory for a saved chapter run."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ocr_engine.local_rapidocr import LocalRapidOCR  # noqa: E402


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("result", type=Path)
    parser.add_argument("--texts", action="store_true", help="include every OCR region")
    parser.add_argument("--dedupe", action="store_true", help="preview conservative region deduplication")
    parser.add_argument("--page", type=int, action="append", help="limit detail to this page (repeatable)")
    args = parser.parse_args()
    report = json.loads(args.result.read_text(encoding="utf-8"))
    all_pages = report.get("pages", [])
    pages = [page for page in all_pages
             if not args.page or int(page.get("page", -1)) in args.page]
    raw_region_count = sum(len(page.get("regions", [])) for page in pages)
    page_regions = []
    for page in pages:
        rows = page.get("regions", [])
        if args.dedupe:
            rows = LocalRapidOCR._deduplicate_overlapping_regions(rows)
        page_regions.append((page, rows))
    regions = [region for _, rows in page_regions for region in rows]
    print(
        f"chapter={report.get('chapter')} pages={report.get('pages_completed')}/"
        f"{report.get('pages_expected')} regions={len(regions)}"
        f"{f' (raw={raw_region_count})' if args.dedupe else ''} "
        f"seconds={report.get('total_seconds')} peak_rss_mib={report.get('peak_rss_mib')}"
    )
    for page, rows in page_regions:
        raw_rows = page.get("regions", [])
        speech = sum(region.get("region_type") == "speech_bubble" for region in rows)
        external = sum(region.get("region_type") == "external_text" for region in rows)
        print(f"page={page.get('page')} status={page.get('status')} regions={len(rows)} speech={speech} external={external}"
              f"{f' raw={len(raw_rows)}' if args.dedupe else ''}")
        if args.dedupe:
            kept_ids = {id(row) for row in rows}
            for row in raw_rows:
                if id(row) not in kept_ids:
                    text = " ".join(str(row.get("text", "")).split())
                    print(f"  removed: {text}")
        if args.texts:
            for index, region in enumerate(rows, 1):
                text = " ".join(str(region.get("text", "")).split())
                flags = " review" if region.get("ocr_review_required") else ""
                print(
                    f"  r{index} [{region.get('region_type')}] "
                    f"bubble={region.get('bubble_id')} "
                    f"box={region.get('left')},{region.get('top')},"
                    f"{region.get('width')},{region.get('height')} "
                    f"{text}{flags}"
                )
    return 0 if report.get("pages_completed") == report.get("pages_expected") else 2


if __name__ == "__main__":
    raise SystemExit(main())
