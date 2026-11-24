"""Compare two saved chapter OCR runs without reprocessing source images."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

STAGE_FIELDS = (
    "bubble_detection_seconds",
    "bubble_ocr_seconds",
    "malformed_bubble_recognition_seconds",
    "recovered_bubble_recognition_seconds",
    "external_ocr_seconds",
    "bubble_split_detector_seconds",
    "adjacent_bubble_recovery_seconds",
    "adjacent_fragment_recovery_seconds",
    "wide_bubble_recovery_seconds",
    "edge_crop_recovery_seconds",
    "comic_detector_seconds",
)


def _region_signature(region: dict) -> tuple:
    return (
        str(region.get("text", "")),
        str(region.get("region_type", "")),
        tuple(round(float(region.get(key, 0) or 0), 1)
              for key in ("left", "top", "width", "height")),
    )


def _load(path: Path) -> tuple[dict, dict[int, list[tuple]]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    signatures = {
        int(page["page"]): [_region_signature(region) for region in page.get("regions", [])]
        for page in data.get("pages", [])
    }
    return data, signatures


def _stage_totals(document: dict) -> dict[str, float | None]:
    """Sum stages only when they were recorded; missing telemetry is not zero."""
    pages = document.get("pages", [])
    totals = {}
    for field in STAGE_FIELDS:
        values = [float(page[field]) for page in pages if page.get(field) is not None]
        totals[field] = round(sum(values), 4) if values else None
    return totals


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("before", type=Path)
    parser.add_argument("after", type=Path)
    args = parser.parse_args()
    before_doc, before = _load(args.before)
    after_doc, after = _load(args.after)
    before_pages = {int(page["page"]): page for page in before_doc.get("pages", [])}
    after_pages = {int(page["page"]): page for page in after_doc.get("pages", [])}
    pages = sorted(set(before) | set(after))
    identical_ordered = 0
    identical_unordered = 0
    text_type_equal = 0
    total_regions_before = sum(map(len, before.values()))
    total_regions_after = sum(map(len, after.values()))
    for page in pages:
        left, right = before.get(page, []), after.get(page, [])
        before_hash = before_pages.get(page, {}).get("source_sha256")
        after_hash = after_pages.get(page, {}).get("source_sha256")
        source_hash_equal = (
            before_hash == after_hash
            if before_hash is not None and after_hash is not None else None
        )
        ordered_equal = left == right
        unordered_equal = Counter(left) == Counter(right)
        text_type_equal_for_page = Counter((row[0], row[1]) for row in left) == Counter(
            (row[0], row[1]) for row in right
        )
        identical_ordered += ordered_equal
        identical_unordered += unordered_equal
        text_type_equal += text_type_equal_for_page
        print(json.dumps({
            "page": page,
            "source_hash_equal": source_hash_equal,
            "before_regions": len(left),
            "after_regions": len(right),
            "before_seconds": before_pages.get(page, {}).get("elapsed_seconds"),
            "after_seconds": after_pages.get(page, {}).get("elapsed_seconds"),
            "ordered_signature_equal": ordered_equal,
            "unordered_signature_equal": unordered_equal,
            "text_and_type_multiset_equal": text_type_equal_for_page,
            "before_only": list((Counter(left) - Counter(right)).elements()),
            "after_only": list((Counter(right) - Counter(left)).elements()),
        }, ensure_ascii=False))
    print(json.dumps({
        "pages_compared": len(pages),
        "source_hash_equal_pages": sum(
            before_pages.get(page, {}).get("source_sha256") is not None
            and before_pages.get(page, {}).get("source_sha256")
            == after_pages.get(page, {}).get("source_sha256")
            for page in pages
        ),
        "pages_missing_a_source_hash": sum(
            before_pages.get(page, {}).get("source_sha256") is None
            or after_pages.get(page, {}).get("source_sha256") is None
            for page in pages
        ),
        "source_hash_mismatch_pages": [
            page for page in pages
            if before_pages.get(page, {}).get("source_sha256") is not None
            and after_pages.get(page, {}).get("source_sha256") is not None
            and before_pages.get(page, {}).get("source_sha256")
            != after_pages.get(page, {}).get("source_sha256")
        ],
        "ordered_signature_equal_pages": identical_ordered,
        "unordered_signature_equal_pages": identical_unordered,
        "text_and_type_equal_pages": text_type_equal,
        "regions_before": total_regions_before,
        "regions_after": total_regions_after,
        "before_total_seconds": before_doc.get("total_seconds"),
        "after_total_seconds": after_doc.get("total_seconds"),
        "before_peak_rss_mib": before_doc.get("peak_rss_mib"),
        "after_peak_rss_mib": after_doc.get("peak_rss_mib"),
        "before_stage_seconds": _stage_totals(before_doc),
        "after_stage_seconds": _stage_totals(after_doc),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
