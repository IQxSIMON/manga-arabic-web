from __future__ import annotations

import hashlib
import io
import json
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

ROOT = Path(r"D:\MangaArabic\manga-arabic-web")
sys.path.insert(0, str(ROOT))
ARCHIVE = Path(r"C:\Users\BMW1975\Downloads\12.cbz")
OCR_PATH = ROOT / "ocr_test_results/baseline/12/ocr_results.json"
OUT = ROOT / "ocr_test_results/gemini_chapter12_trial_20261114"
TRANSLATIONS_PATH = OUT / "chapter12_translations.json"
METRICS_PATH = OUT / "request_metrics.json"
CBZ_PATH = OUT / "chapter12_arabic_preview.cbz"

# Omit non-story scan branding, promo cards, isolated interface labels and clear OCR/SFX noise.
SKIP_IDS = {
    "ch12-p001-r001", "ch12-p001-r002", "ch12-p001-r003", "ch12-p001-r006",
    "ch12-p005-r003",
    "ch12-p006-r002", "ch12-p006-r005", "ch12-p006-r006",
    "ch12-p007-r003", "ch12-p008-r004", "ch12-p009-r004",
    "ch12-p010-r002", "ch12-p010-r003", "ch12-p010-r004", "ch12-p010-r005", "ch12-p010-r006",
    "ch12-p011-r001", "ch12-p012-r001", "ch12-p013-r005",
    "ch12-p014-r001", "ch12-p016-r001", "ch12-p016-r002",
    "ch12-p019-r004", "ch12-p022-r006", "ch12-p026-r002",
    "ch12-p027-r004", "ch12-p027-r005", "ch12-p027-r006", "ch12-p027-r007",
    "ch12-p028-r005", "ch12-p028-r010", "ch12-p028-r012",
    "ch12-p031-r003", "ch12-p031-r004",
}


def save_json(path, value):
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def main():
    started = time.perf_counter()
    ocr = json.loads(OCR_PATH.read_text(encoding="utf-8"))
    translations = json.loads(TRANSLATIONS_PATH.read_text(encoding="utf-8"))
    metrics = json.loads(METRICS_PATH.read_text(encoding="utf-8"))
    if metrics.get("status") != "complete" or translations.get("missing_count") != 0:
        raise RuntimeError("Full translation response is not complete; rendering stopped.")
    rows = translations.get("translations", [])
    by_id = {row["region_id"]: row for row in rows}
    if len(by_id) != len(rows) or len(by_id) != translations.get("input_count"):
        raise RuntimeError("Translation IDs are duplicated or incomplete.")
    if Path(ocr.get("chapter", "")).name.casefold() != "12.cbz":
        raise RuntimeError("Saved OCR does not match chapter 12.")
    if ocr.get("pages_expected") != ocr.get("pages_completed") or len(ocr.get("pages", [])) != ocr.get("pages_expected"):
        raise RuntimeError("Saved OCR page coverage is incomplete.")
    if not ARCHIVE.is_file():
        raise RuntimeError("Original chapter archive not found.")

    from image_pipeline.renderer import render_translated_page

    page_dir = OUT / "pages"
    page_dir.mkdir(exist_ok=True)
    render_summary = []
    selected_ids, consumed_ids = set(), set()
    with zipfile.ZipFile(ARCHIVE, "r") as source_zip:
        archive_members = set(source_zip.namelist())
        for page in ocr["pages"]:
            page_no = int(page["page"])
            member = page.get("source_member")
            if member not in archive_members:
                raise RuntimeError(f"Missing original archive member for page {page_no}.")
            source_bytes = source_zip.read(member)
            digest = hashlib.sha256(source_bytes).hexdigest()
            if digest != page.get("source_sha256"):
                raise RuntimeError(f"Original source hash mismatch on page {page_no}.")
            source_regions = sorted(
                page.get("regions", []), key=lambda row: int(row.get("reading_order", 0))
            )
            chosen = []
            for order, source_row in enumerate(source_regions, 1):
                rid = f"ch12-p{page_no:03d}-r{order:03d}"
                translated = by_id.get(rid)
                if not translated:
                    continue
                consumed_ids.add(rid)
                if rid in SKIP_IDS:
                    continue
                text = translated.get("translation_ar")
                if not text:
                    continue
                selected_ids.add(rid)
                region = dict(source_row)
                region.update(index=order, region_id=rid, translated=text)
                chosen.append(region)

            if chosen:
                output_bytes, quality = render_translated_page(source_bytes, chosen)
                rendered = int(quality.get("rendered_regions", 0))
                skipped = int(quality.get("skipped_regions", 0))
                skipped_indices = quality.get("skipped_region_indices", [])
            else:
                with Image.open(io.BytesIO(source_bytes)) as image:
                    buffer = io.BytesIO()
                    image.convert("RGB").save(buffer, format="PNG", optimize=True)
                    output_bytes = buffer.getvalue()
                rendered, skipped, skipped_indices = 0, 0, []

            filename = f"page_{page_no:03d}.png"
            (page_dir / filename).write_bytes(output_bytes)
            render_summary.append({
                "page": page_no, "source_member": member,
                "source_sha256": digest, "selected_regions": len(chosen),
                "rendered_regions": rendered, "renderer_skipped": skipped,
                "skipped_indices": skipped_indices, "output_member": filename,
            })

    if consumed_ids != set(by_id):
        raise RuntimeError("Translation/OCR ID mapping mismatch; preview not packaged.")
    with zipfile.ZipFile(CBZ_PATH, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as target:
        for row in render_summary:
            target.write(page_dir / row["output_member"], row["output_member"])

    excluded = sorted(set(by_id) - selected_ids)
    metrics["rendering"] = {
        "status": "complete" if len(render_summary) == ocr["pages_expected"] else "incomplete",
        "pages_expected": ocr["pages_expected"], "pages_completed": len(render_summary),
        "translations_in_api_response": len(by_id),
        "regions_selected_for_preview": len(selected_ids),
        "regions_omitted_from_preview": len(excluded),
        "omitted_region_ids": excluded, "rendered_regions": sum(r["rendered_regions"] for r in render_summary),
        "renderer_skipped_regions": sum(r["renderer_skipped"] for r in render_summary),
        "pages": render_summary, "preview_cbz": str(CBZ_PATH),
        "source_images_modified": False,
    }
    metrics["status"] = metrics["rendering"]["status"]
    metrics["rendering_elapsed_seconds"] = round(time.perf_counter() - started, 2)
    now_utc = datetime.now(timezone.utc)
    run_start = datetime.fromisoformat(metrics["started_at_utc"])
    metrics["elapsed_wall_including_render_recovery_seconds"] = round(
        (now_utc - run_start).total_seconds(), 2
    )
    metrics["elapsed_full_trial_seconds"] = round(
        metrics.get("elapsed_translation_pipeline_seconds", 0)
        + metrics["rendering_elapsed_seconds"], 2
    )
    metrics["prior_ocr_elapsed_seconds"] = float(ocr.get("total_seconds", 0))
    metrics["end_to_end_with_saved_ocr_seconds"] = round(
        metrics["elapsed_full_trial_seconds"] + metrics["prior_ocr_elapsed_seconds"], 2
    )
    save_json(METRICS_PATH, metrics)

    report = [
        "# Chapter 12 Translation Trial",
        "",
        f"- Status: {metrics['status']}",
        "- Model: Gemini 3.5 Flash-Lite, Free Tier request",
        f"- OCR coverage: {ocr['pages_completed']}/{ocr['pages_expected']} pages ({sum(p.get('status') == 'no_text_detected' for p in ocr['pages'])} page had no text detected).",
        f"- English regions sent: {len(by_id)} ({metrics['dialogue_source_regions']} speech-bubble regions; {metrics['external_source_regions']} external regions).",
        f"- Translated IDs received: {len(by_id)}/{translations['input_count']}; missing: {translations['missing_count']}.",
        f"- OCR review flags: {metrics['ocr_review_flagged_regions']}; these were not visually verified in this run.",
        f"- Regions selected for reading preview: {len(selected_ids)}; omitted from preview: {len(excluded)} (branding, promo, interface labels, and obvious SFX/OCR noise).",
        f"- API requests sent: {metrics['requests_sent']}; successful HTTP responses: {metrics['successful_requests']}.",
        f"- RPD estimate: {metrics['estimated_rpd_after']}; exact quota count was not re-read from AI Studio after the request.",
        f"- Token totals: {json.dumps(metrics['token_totals'])}.",
        f"- API latency: {metrics['api_latency_ms']} ms.",
        f"- Translation plus preview elapsed: {metrics['elapsed_full_trial_seconds']} seconds.",
        f"- Previously saved OCR duration: {metrics['prior_ocr_elapsed_seconds']} seconds.",
        f"- End-to-end elapsed including saved OCR duration: {metrics['end_to_end_with_saved_ocr_seconds']} seconds.",
        f"- Rendered pages: {metrics['rendering']['pages_completed']}/{metrics['rendering']['pages_expected']}; renderer skipped: {metrics['rendering']['renderer_skipped_regions']}.",
        f"- Preview CBZ: {CBZ_PATH}",
        "",
        "This is an automated preview, not a human-reviewed translation. The chapter has no human gold reference, so no semantic accuracy percentage is claimed.",
    ]
    report_path = OUT / "CHAPTER12_TRANSLATION_TRIAL_REPORT.md"
    report_path.write_text("\n".join(report) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": metrics["status"], "translated": len(by_id), "selected_for_preview": len(selected_ids),
        "omitted_from_preview": len(excluded), "pages": len(render_summary),
        "rendered_regions": metrics["rendering"]["rendered_regions"],
        "renderer_skipped": metrics["rendering"]["renderer_skipped_regions"],
        "elapsed_full_trial_seconds": metrics["elapsed_full_trial_seconds"],
        "prior_ocr_seconds": metrics["prior_ocr_elapsed_seconds"],
        "end_to_end_seconds": metrics["end_to_end_with_saved_ocr_seconds"],
        "preview": str(CBZ_PATH), "report": str(report_path),
    }, ensure_ascii=True))


if __name__ == "__main__":
    main()
