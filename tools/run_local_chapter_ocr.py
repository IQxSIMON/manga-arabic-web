"""Run the existing OCR pipeline page-by-page on one indexed CBZ chapter."""

from __future__ import annotations

import argparse
import ctypes
import json
import gc
from ctypes import wintypes
import sys
import time
import zipfile
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ocr_engine import LocalRapidOCR  # noqa: E402

STAGE_TIMINGS = (
    "bubble_detection_seconds", "bubble_ocr_seconds", "malformed_bubble_recognition_seconds", "external_ocr_seconds",
    "bubble_split_detector_seconds", "adjacent_bubble_recovery_seconds",
    "adjacent_fragment_recovery_seconds", "wide_bubble_recovery_seconds",
    "edge_crop_recovery_seconds",
    "truncated_external_recovery_seconds", "recovered_bubble_recognition_seconds",
    "comic_detector_seconds",
)
STAGE_COUNTS = (
    "malformed_bubble_recognition_count", "speech_bubble_mask_count", "speech_bubble_masks_with_primary_ocr",
    "speech_bubble_masks_without_primary_ocr", "external_region_count",
    "bubble_split_count", "adjacent_bubble_recovery_count",
    "adjacent_fragment_recovery_count", "wide_bubble_recovery_count",
    "edge_crop_recovery_count",
    "truncated_external_recovery_count", "recovered_bubble_recognition_count",
    "comic_detector_region_count",
    "comic_detector_bubble_count", "comic_detector_ocr_line_count",
)


class _PROCESS_MEMORY_COUNTERS(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]


def peak_rss_mib() -> float:
    counters = _PROCESS_MEMORY_COUNTERS()
    counters.cb = ctypes.sizeof(counters)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE,
                                           ctypes.POINTER(_PROCESS_MEMORY_COUNTERS),
                                           wintypes.DWORD]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    if not psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        return 0.0
    return counters.PeakWorkingSetSize / 1024 / 1024


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    parser.add_argument("--run", default="baseline")
    args = parser.parse_args()
    manifest = json.loads((ROOT / "ocr_test_results" / "chapter_index.json").read_text(encoding="utf-8"))
    chapter = next((item for item in manifest["archives"] if Path(item["chapter"]).name == args.archive.name), None)
    if not chapter or chapter["status"] != "indexed":
        raise SystemExit("Archive is absent from the verified index.")
    target = ROOT / "ocr_test_results" / args.run / args.archive.stem
    overlays = target / "overlays"
    overlays.mkdir(parents=True, exist_ok=True)
    engine = LocalRapidOCR()
    report_path = target / "ocr_results.json"
    pages_out = json.loads(report_path.read_text(encoding="utf-8")).get("pages", []) if report_path.is_file() else []
    completed_pages = {item["page"] for item in pages_out}
    with zipfile.ZipFile(args.archive) as archive:
        for page in chapter["pages"]:
            if page["page"] in completed_pages:
                continue
            started = time.perf_counter()
            data = archive.read(page["member"])
            status = "ok"
            try:
                result = engine.extract(data, reading_direction="ltr", include_external=True)
            except RuntimeError as exc:
                if "لم يعثر OCR المحلي" not in str(exc):
                    raise
                result = {"regions": [], "lines": [], "speech_bubble_mask_count": 0}
                status = "no_text_detected"
            elapsed = time.perf_counter() - started
            image = cv2.imdecode(__import__("numpy").frombuffer(data, dtype="uint8"), cv2.IMREAD_COLOR)
            rows = result.get("regions", [])
            rows = sorted(rows, key=lambda r: (int(r.get("bbox", [0,0,0,0])[1]), int(r.get("bbox", [0,0,0,0])[0])))
            for order, row in enumerate(rows, 1):
                row["reading_order"] = order
            for order, row in enumerate(rows, 1):
                box = row.get("bubble_bbox")
                if not box:
                    left, top = int(row.get("left", 0)), int(row.get("top", 0))
                    box = [left, top, left + int(row.get("width", 0)),
                           top + int(row.get("height", 0))]
                if not box or len(box) != 4:
                    continue
                x1,y1,x2,y2 = map(int, box)
                color = (30,220,30) if row.get("region_type") == "speech_bubble" else (0,150,255)
                cv2.rectangle(image, (x1,y1), (x2,y2), color, max(2, image.shape[1]//500))
                cv2.putText(image, str(order), (x1, max(20,y1-5)), cv2.FONT_HERSHEY_SIMPLEX,
                            max(.6, image.shape[1]/1800), color, 2, cv2.LINE_AA)
            cv2.imwrite(str(overlays / f"page_{page['page']:03}.jpg"), image,
                        [cv2.IMWRITE_JPEG_QUALITY, 88])
            pages_out.append({"page": page["page"], "source_member": page["member"],
                              "source_sha256": page["sha256"], "elapsed_seconds": round(elapsed, 3),
                              "status": status,
                              "regions": rows, "bubble_count": result.get("speech_bubble_mask_count"),
                              "text_lines": len(result.get("lines", [])),
                              **{field: result[field] for field in STAGE_TIMINGS if field in result},
                              **{field: result[field] for field in STAGE_COUNTS if field in result}})
            pages_out.sort(key=lambda item: item["page"])
            partial = {"chapter": args.archive.name, "run": args.run,
                       "pages_expected": chapter["page_count"], "pages_completed": len(pages_out),
                       "total_seconds": round(sum(p["elapsed_seconds"] for p in pages_out), 3),
                       "peak_rss_mib": round(peak_rss_mib(), 1), "pages": pages_out}
            report_path.write_text(json.dumps(partial, ensure_ascii=False, indent=2), encoding="utf-8")
            data = image = result = None
            gc.collect()
            print(json.dumps({"chapter": args.archive.name, "page": page["page"],
                              "total": chapter["page_count"], "seconds": round(elapsed, 2),
                              "regions": len(rows)}, ensure_ascii=False), flush=True)
    report = {"chapter": args.archive.name, "run": args.run, "pages_expected": chapter["page_count"],
              "pages_completed": len(pages_out), "total_seconds": round(sum(p["elapsed_seconds"] for p in pages_out), 3),
              "peak_rss_mib": round(peak_rss_mib(), 1),
              "pages": pages_out}
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("chapter", "pages_expected", "pages_completed", "total_seconds", "peak_rss_mib")}, ensure_ascii=False))
    return 0 if len(pages_out) == chapter["page_count"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
