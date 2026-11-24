"""Render one complete chapter from saved OCR using deterministic Arabic test strings.

This validates image repair and Arabic layout only. The generated strings are mock
fixtures, not translations and must never be used as reader-facing translations.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import io
import json
import sys
import time
import zipfile
from pathlib import Path

import cv2
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def peak_rss_mib() -> float | None:
    if __import__("os").name != "nt":
        return None
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    if not psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        return None
    return round(counters.PeakWorkingSetSize / 1024 / 1024, 1)


def mock_arabic(word_count: int) -> str:
    # Repeat a natural Arabic fixture phrase to approximate source-text length.
    phrase = "هذا نص تجريبي"
    # Keep Arabic fixture length near or below the source length so that
    # renderer skips measure layout limits, not an intentionally inflated mock.
    repeats = max(1, min(12, (max(1, word_count) + 3) // 4))
    return " ".join([phrase] * repeats)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    parser.add_argument("--ocr-json", type=Path, default=ROOT / "ocr_test_results/post_tiny_lowercase_filter/11/ocr_results.json")
    parser.add_argument("--out", type=Path, default=ROOT / "ocr_test_results/mock_render/chapter11_20261113")
    args = parser.parse_args()

    ocr = json.loads(args.ocr_json.read_text(encoding="utf-8"))
    if ocr.get("pages_expected") != ocr.get("pages_completed") or len(ocr.get("pages", [])) != ocr.get("pages_expected"):
        raise SystemExit("Saved OCR is incomplete; refusing partial-chapter rendering.")
    if Path(ocr.get("chapter", "")).name.casefold() != args.archive.name.casefold():
        raise SystemExit("OCR archive name does not match the selected source archive.")

    args.out.mkdir(parents=True, exist_ok=True)
    page_dir, layer_dir = args.out / "pages", args.out / "layers"
    page_dir.mkdir(exist_ok=True)
    layer_dir.mkdir(exist_ok=True)
    from image_pipeline.renderer import render_translated_page

    page_metrics = []
    translation_rows = []
    started_all = time.perf_counter()
    with zipfile.ZipFile(args.archive) as archive:
        for page in ocr["pages"]:
            page_no = int(page["page"])
            member = page["source_member"]
            source = archive.read(member)
            source_hash = hashlib.sha256(source).hexdigest()
            if source_hash != page["source_sha256"]:
                raise SystemExit(f"Source page hash mismatch at page {page_no}; no output written for this page.")
            regions = []
            for order, row in enumerate(sorted(page.get("regions", []), key=lambda r: int(r.get("reading_order", 0))), 1):
                region = dict(row)
                region_id = f"ch11-p{page_no:03d}-r{order:03d}"
                region["index"] = order
                region["region_id"] = region_id
                region["translated"] = mock_arabic(len(str(row.get("text", "")).split()))
                regions.append(region)
                translation_rows.append({
                    "region_id": region_id, "page": page_no, "reading_order": row.get("reading_order"),
                    "source_text": row.get("text", ""), "mock_arabic_fixture": region["translated"],
                    "source_region_type": row.get("region_type"),
                })
            if not regions:
                # Preserve pages with zero OCR results in the complete-chapter
                # export; leave every source pixel unchanged and create empty layers.
                source_image = Image.open(io.BytesIO(source)).convert("RGB")
                output_path = page_dir / f"page_{page_no:03d}.png"
                source_image.save(output_path, format="PNG", optimize=True)
                page_layers = layer_dir / f"page_{page_no:03d}"
                page_layers.mkdir(exist_ok=True)
                empty_mask = Image.new("L", source_image.size, 0)
                transparent_text = Image.new("RGBA", source_image.size, (0, 0, 0, 0))
                source_image.save(page_layers / "original.png", format="PNG", optimize=True)
                empty_mask.save(page_layers / "text_removal_mask.png", format="PNG", optimize=True)
                source_image.save(page_layers / "repaired_underlay.png", format="PNG", optimize=True)
                transparent_text.save(page_layers / "translated_text_overlay.png", format="PNG", optimize=True)
                row_metrics = {"page": page_no, "source_member": member, "source_sha256": source_hash,
                               "source_regions": 0, "rendered_regions": 0, "skipped_regions": 0,
                               "output_file": str(output_path.relative_to(ROOT)),
                               "status": "source_preserved_no_ocr"}
                page_metrics.append(row_metrics)
                print(json.dumps({"page": page_no, "status": row_metrics["status"]}, ensure_ascii=False), flush=True)
                source_image.close()
                continue

            rendered, quality, layers = render_translated_page(source, regions, include_layers=True)
            output_path = page_dir / f"page_{page_no:03d}.png"
            output_path.write_bytes(rendered)
            page_layers = layer_dir / f"page_{page_no:03d}"
            page_layers.mkdir(exist_ok=True)
            for name, data in layers.items():
                (page_layers / name).write_bytes(data)

            row_metrics = {
                "page": page_no, "source_member": member, "source_sha256": source_hash,
                "source_regions": len(regions), "rendered_regions": quality["rendered_regions"],
                "skipped_regions": quality["skipped_regions"],
                "skipped_region_indices": quality.get("skipped_region_indices", []),
                "overlapping_external_regions_skipped": quality.get("overlapping_external_regions_skipped", 0),
                "bubble_repair_strategies": quality.get("bubble_repair_strategies", {}),
                "render_timing_seconds": quality.get("render_timing_seconds", {}),
                "output_file": str(output_path.relative_to(ROOT)),
                "status": "ok" if quality["skipped_regions"] == 0 else "review_required",
            }
            page_metrics.append(row_metrics)
            summary = {"page": page_no, "regions": len(regions), "rendered": quality["rendered_regions"],
                       "skipped": quality["skipped_regions"], "seconds": quality["render_timing_seconds"]["total"]}
            print(json.dumps(summary, ensure_ascii=False), flush=True)
            # Persist a checkpoint after every complete page.
            payload = {
                "experiment": "mock Arabic layout validation; NOT a translation",
                "chapter": ocr["chapter"], "ocr_source": str(args.ocr_json),
                "archive": str(args.archive), "pages_expected": ocr["pages_expected"],
                "pages_completed": len(page_metrics), "total_regions": len(translation_rows),
                "rendered_regions": sum(p.get("rendered_regions", 0) for p in page_metrics),
                "skipped_regions": sum(p.get("skipped_regions", 0) for p in page_metrics),
                "elapsed_seconds": round(time.perf_counter() - started_all, 3),
                "peak_rss_mib": peak_rss_mib(), "pages": page_metrics,
            }
            (args.out / "metrics.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            (args.out / "mock_translation_fixtures.json").write_text(
                json.dumps({"warning": "Mock Arabic strings, not translations.", "regions": translation_rows},
                           ensure_ascii=False, indent=2), encoding="utf-8")

    final = {
        "experiment": "mock Arabic layout validation; NOT a translation",
        "chapter": ocr["chapter"], "ocr_source": str(args.ocr_json), "archive": str(args.archive),
        "pages_expected": ocr["pages_expected"], "pages_completed": len(page_metrics),
        "total_regions": len(translation_rows),
        "rendered_regions": sum(p.get("rendered_regions", 0) for p in page_metrics),
        "skipped_regions": sum(p.get("skipped_regions", 0) for p in page_metrics),
        "elapsed_seconds": round(time.perf_counter() - started_all, 3), "peak_rss_mib": peak_rss_mib(),
        "pages": page_metrics,
    }
    (args.out / "metrics.json").write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.out / "mock_translation_fixtures.json").write_text(
        json.dumps({"warning": "Mock Arabic strings, not translations.", "regions": translation_rows},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: final[k] for k in ("pages_expected", "pages_completed", "total_regions",
                                            "rendered_regions", "skipped_regions", "elapsed_seconds",
                                            "peak_rss_mib")}, ensure_ascii=False))
    return 0 if final["pages_expected"] == final["pages_completed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
