from __future__ import annotations

import hashlib
import io
import json
import os
import re
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from PIL import Image

ROOT = Path(r"D:\MangaArabic\manga-arabic-web")
sys.path.insert(0, str(ROOT))
CHAPTER = "12.cbz"
ARCHIVE = Path(r"C:\Users\BMW1975\Downloads\12.cbz")
OCR_PATH = ROOT / "ocr_test_results/baseline/12/ocr_results.json"
OUT = ROOT / "ocr_test_results/gemini_chapter12_trial_20261114"
MODEL = "gemini-3.5-flash-lite"
ENDPOINT = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent?alt=json"
MAX_OUTPUT_TOKENS = 16384


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def save_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def get_api_key():
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if key:
        return key
    env_path = ROOT / ".env"
    if not env_path.is_file():
        return ""
    for line in env_path.read_text(encoding="utf-8-sig").splitlines():
        if line.strip().startswith("GEMINI_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def verify_source_archive(ocr):
    with zipfile.ZipFile(ARCHIVE, "r") as archive:
        members = set(archive.namelist())
        for page in ocr["pages"]:
            member = page.get("source_member")
            if not member or member not in members:
                raise RuntimeError(f"Source archive member missing for page {page.get('page')}.")
            digest = hashlib.sha256(archive.read(member)).hexdigest()
            if digest != page.get("source_sha256"):
                raise RuntimeError(f"Source image hash mismatch for page {page.get('page')}.")
    return True


def build_records(ocr):
    records = []
    source_by_page = {}
    for page in ocr["pages"]:
        page_no = int(page["page"])
        source_by_page[page_no] = page
        regions = sorted(page.get("regions", []), key=lambda row: int(row.get("reading_order", 0)))
        for order, region in enumerate(regions, 1):
            text = " ".join(str(region.get("text", "")).split()).strip()
            if not text or not re.search(r"[A-Za-z]", text):
                continue
            source_kind = "dialogue" if region.get("region_type") == "speech_bubble" else "external_text"
            records.append({
                "region_id": f"ch12-p{page_no:03d}-r{order:03d}",
                "page": page_no,
                "reading_order": order,
                "source_kind": source_kind,
                "source_text": text,
                "ocr_review_required": bool(region.get("ocr_review_required")),
                "ocr_review_reason": region.get("ocr_review_reason"),
            })
    return records, source_by_page


INSTRUCTIONS = (
    "Translate every supplied English text region from a vertical webtoon into natural, fluent "
    "Modern Standard Arabic. Preserve meaning, mood, speaker intent, and the source order. Use "
    "literary Arabic for narration and concise, natural dialogue; avoid word-for-word calques. "
    "Use each region's kind and neighboring dialogue to distinguish dialogue, narration, external "
    "text, and sound effects. Keep character names and terms consistent across the chapter; write "
    "names in Arabic script. Translate readable sound effects naturally. Translate promotional "
    "prose, but preserve URLs, site names, and brand identifiers. If OCR is uncertain, do not invent "
    "missing words; translate only what is supported by the text and context. Keep each supplied "
    "region_id exactly once. Return a JSON object with translations, where each item has region_id, "
    "translation_ar, and classification (one of dialogue, narration, external_text, sound_effect, "
    "non_english_or_unclear). Do not add explanations."
)

SCHEMA = {
    "type": "object",
    "properties": {
        "translations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "region_id": {"type": "string"},
                    "translation_ar": {"type": "string"},
                    "classification": {
                        "type": "string",
                        "enum": ["dialogue", "narration", "external_text", "sound_effect", "non_english_or_unclear"]
                    },
                },
                "required": ["region_id", "translation_ar", "classification"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["translations"],
    "additionalProperties": False,
}


def request_translation(key, records, request_no):
    body = {
        "contents": [{"role": "user", "parts": [{
            "text": INSTRUCTIONS + "\n\nREGIONS IN PAGE AND READING ORDER:\n"
                    + json.dumps(records, ensure_ascii=False, separators=(",", ":"))
        }]}],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": MAX_OUTPUT_TOKENS,
            "responseMimeType": "application/json",
            "responseJsonSchema": SCHEMA,
        },
    }
    req = Request(
        ENDPOINT,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json",
                 "x-goog-api-key": key},
        method="POST",
    )
    entry = {
        "request_number": request_no, "model": MODEL, "started_at_utc": now(),
        "sent_region_count": len(records), "sent_region_ids": [r["region_id"] for r in records],
        "status": "sent_waiting", "http_status": None, "server_accepted": None,
        "quota_count_confirmed": None, "latency_ms": None, "input_tokens": None,
        "output_tokens": None, "thinking_tokens": None, "total_tokens": None,
        "received_region_ids": [], "missing_region_ids": [], "unexpected_region_ids": [],
        "duplicate_region_ids": [], "error": None, "quota_error": None,
    }
    started = time.perf_counter()
    data = None
    try:
        with urlopen(req, timeout=180) as response:
            entry["http_status"] = response.status
            data = json.loads(response.read().decode("utf-8"))
        entry.update(status="http_success", server_accepted=True)
    except HTTPError as exc:
        entry["http_status"] = exc.code
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(raw)
        except Exception:
            data = None
        message = (data or {}).get("error", {}).get("message", raw[:1200])
        entry.update(status="http_error", server_accepted=False, error=message)
        if exc.code == 429 or "quota" in str(message).lower() or "rate" in str(message).lower():
            entry["quota_error"] = message
    except (URLError, TimeoutError, OSError) as exc:
        entry.update(status="transport_error", error=f"{type(exc).__name__}: {exc}")
    finally:
        entry["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
        entry["completed_at_utc"] = now()

    items = None
    if data is not None and entry["server_accepted"]:
        usage = data.get("usageMetadata", {})
        entry["input_tokens"] = usage.get("promptTokenCount")
        entry["output_tokens"] = usage.get("candidatesTokenCount")
        entry["thinking_tokens"] = usage.get("thoughtsTokenCount")
        entry["total_tokens"] = usage.get("totalTokenCount")
        entry["finish_reason"] = (data.get("candidates") or [{}])[0].get("finishReason")
        try:
            response_text = "".join(
                part.get("text", "") for part in data["candidates"][0]["content"]["parts"]
            ).strip()
            items = json.loads(response_text)["translations"]
            received = [str(item.get("region_id", "")) for item in items]
            expected = [r["region_id"] for r in records]
            seen = set()
            duplicates = []
            for rid in received:
                if rid in seen and rid not in duplicates:
                    duplicates.append(rid)
                seen.add(rid)
            entry["received_region_ids"] = received
            entry["missing_region_ids"] = [rid for rid in expected if rid not in seen]
            entry["unexpected_region_ids"] = [rid for rid in received if rid not in set(expected)]
            entry["duplicate_region_ids"] = duplicates
            entry["status"] = "success" if not (
                entry["missing_region_ids"] or entry["unexpected_region_ids"] or duplicates
            ) else "success_with_id_mismatch"
        except Exception as exc:
            entry.update(status="invalid_structured_response", error=f"{type(exc).__name__}: {exc}")
    return entry, items


def main():
    metrics_path = OUT / "request_metrics.json"
    if metrics_path.is_file():
        previous = json.loads(metrics_path.read_text(encoding="utf-8"))
        if previous.get("requests"):
            raise RuntimeError("A request is already recorded for chapter 12; refusing to send it again.")
    started = time.perf_counter()
    OUT.mkdir(parents=True, exist_ok=True)
    ocr = json.loads(OCR_PATH.read_text(encoding="utf-8"))
    if Path(ocr.get("chapter", "")).name.casefold() != CHAPTER.casefold():
        raise RuntimeError("Saved OCR does not match chapter 12.")
    if ocr.get("pages_expected") != ocr.get("pages_completed") or len(ocr.get("pages", [])) != ocr.get("pages_expected"):
        raise RuntimeError("Saved OCR page coverage is incomplete; no translation request sent.")
    if not ARCHIVE.is_file():
        raise RuntimeError("Original chapter 12 archive is missing.")
    verify_source_archive(ocr)

    records, source_by_page = build_records(ocr)
    ids = [row["region_id"] for row in records]
    if len(ids) != len(set(ids)) or not records:
        raise RuntimeError("Input region ID integrity check failed.")

    api_key = get_api_key()
    key_format = bool(re.fullmatch(r"(?:AIza[A-Za-z0-9_-]{25,55}|AQ\.[A-Za-z0-9._-]{40,80})", api_key))
    metrics = {
        "chapter": CHAPTER, "model": MODEL, "endpoint": ENDPOINT,
        "source_ocr": str(OCR_PATH), "source_archive": str(ARCHIVE),
        "ocr_pages_expected": ocr.get("pages_expected"),
        "ocr_pages_completed": ocr.get("pages_completed"),
        "ocr_page_status_counts": {"ok": 30, "no_text_detected": 1},
        "ocr_regions_total": sum(len(p.get("regions", [])) for p in ocr["pages"]),
        "eligible_english_regions": len(records),
        "dialogue_source_regions": sum(r["source_kind"] == "dialogue" for r in records),
        "external_source_regions": sum(r["source_kind"] == "external_text" for r in records),
        "ocr_review_flagged_regions": sum(r["ocr_review_required"] for r in records),
        "free_tier": True, "billing_enabled_by_this_run": False,
        "quota_before_estimate": {"rpd": "1/500", "source": "last user-provided AI Studio screenshot after chapter 11"},
        "api_key_present": bool(api_key), "api_key_format_valid": key_format,
        "api_key_value_logged": False, "requests": [], "status": "preflight",
        "started_at_utc": now(),
    }
    save_json(OUT / "request_metrics.json", metrics)
    save_json(OUT / "translation_payload.json", {"chapter": CHAPTER, "regions": records})
    if not key_format:
        metrics.update(status="blocked_missing_or_invalid_local_key",
                       generate_content_requests_sent=0, finished_at_utc=now(),
                       elapsed_seconds=round(time.perf_counter() - started, 2))
        save_json(OUT / "request_metrics.json", metrics)
        print(json.dumps({"status": metrics["status"], "requests_sent": 0}))
        return 2

    pending = list(records)
    merged = {}
    request_no = 1
    while pending and request_no <= 2:
        row, items = request_translation(api_key, pending, request_no)
        metrics["requests"].append(row)
        save_json(OUT / "request_metrics.json", metrics)
        if items is None or row["status"] not in ("success", "success_with_id_mismatch"):
            metrics["status"] = "request_failed"
            break
        allowed = {r["region_id"] for r in pending}
        for item in items:
            rid = str(item.get("region_id", ""))
            translated = str(item.get("translation_ar", "")).strip()
            if rid in allowed and translated:
                merged[rid] = {
                    "translation_ar": translated,
                    "classification": item.get("classification", "external_text"),
                }
        if row["unexpected_region_ids"] or row["duplicate_region_ids"]:
            metrics["status"] = "response_id_integrity_error"
            break
        pending = [r for r in pending if r["region_id"] not in merged]
        if not pending:
            metrics["status"] = "complete"
            break
        request_no += 1

    output_rows = []
    for row in records:
        rid = row["region_id"]
        answer = merged.get(rid)
        output_rows.append({
            **row,
            "translation_ar": answer["translation_ar"] if answer else None,
            "model_classification": answer["classification"] if answer else None,
        })
    missing = [row["region_id"] for row in records if row["region_id"] not in merged]
    save_json(OUT / "chapter12_translations.json", {
        "chapter": CHAPTER, "model": MODEL, "source_ocr": str(OCR_PATH),
        "input_count": len(records), "translated_count": len(merged),
        "missing_count": len(missing), "missing_region_ids": missing,
        "translations": output_rows,
    })
    metrics.update(
        translated_count=len(merged), missing_count=len(missing), missing_region_ids=missing,
        requests_sent=len(metrics["requests"]),
        successful_requests=sum(r.get("server_accepted") is True for r in metrics["requests"]),
        quota_count_confirmed=None,
        quota_confirmation="Quota count requires the post-run AI Studio dashboard; HTTP success is recorded separately.",
        estimated_rpd_after=f"{1 + len(metrics['requests'])}/500",
        finished_at_utc=now(),
    )
    totals = {}
    for field in ("input_tokens", "output_tokens", "thinking_tokens", "total_tokens"):
        vals = [r[field] for r in metrics["requests"] if isinstance(r.get(field), int)]
        totals[field] = sum(vals) if vals else None
    metrics["token_totals"] = totals
    metrics["api_latency_ms"] = round(sum(r.get("latency_ms") or 0 for r in metrics["requests"]), 2)
    metrics["elapsed_translation_pipeline_seconds"] = round(time.perf_counter() - started, 2)
    save_json(OUT / "request_metrics.json", metrics)

    if missing or metrics["status"] != "complete":
        print(json.dumps({"status": metrics["status"], "translated": len(merged),
                          "missing": len(missing), "requests": len(metrics["requests"])}))
        return 1

    from image_pipeline.renderer import render_translated_page

    cbz_path = OUT / "chapter12_arabic_preview.cbz"
    page_dir = OUT / "pages"
    page_dir.mkdir(exist_ok=True)
    by_id = {row["region_id"]: row for row in output_rows}
    render_rows = []
    with zipfile.ZipFile(ARCHIVE, "r") as source_zip, zipfile.ZipFile(
            cbz_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as target_zip:
        names = set(source_zip.namelist())
        for page in ocr["pages"]:
            page_no = int(page["page"])
            member = page["source_member"]
            if member not in names:
                raise RuntimeError(f"Missing source archive member on page {page_no}.")
            source_bytes = source_zip.read(member)
            if hashlib.sha256(source_bytes).hexdigest() != page["source_sha256"]:
                raise RuntimeError(f"Source image hash mismatch on page {page_no}.")
            regions = sorted(page.get("regions", []), key=lambda r: int(r.get("reading_order", 0)))
            selected = []
            for order, source_row in enumerate(regions, 1):
                rid = f"ch12-p{page_no:03d}-r{order:03d}"
                item = by_id.get(rid)
                if not item:
                    continue
                region = dict(source_row)
                region.update(index=order, region_id=rid, translated=item["translation_ar"])
                selected.append(region)
            if selected:
                image_bytes, quality = render_translated_page(source_bytes, selected)
                rendered = quality.get("rendered_regions", 0)
                skipped = quality.get("skipped_regions", 0)
                skipped_indices = quality.get("skipped_region_indices", [])
            else:
                with Image.open(io.BytesIO(source_bytes)) as source_image:
                    buffer = io.BytesIO()
                    source_image.convert("RGB").save(buffer, format="PNG", optimize=True)
                    image_bytes = buffer.getvalue()
                rendered, skipped, skipped_indices = 0, 0, []
            filename = f"page_{page_no:03d}.png"
            (page_dir / filename).write_bytes(image_bytes)
            target_zip.writestr(filename, image_bytes)
            render_rows.append({"page": page_no, "source_member": member,
                                "selected": len(selected), "rendered": rendered,
                                "renderer_skipped": skipped, "skipped_indices": skipped_indices})
    metrics["rendering"] = {
        "status": "complete" if len(render_rows) == ocr["pages_expected"] else "incomplete",
        "pages_completed": len(render_rows), "pages_expected": ocr["pages_expected"],
        "rendered_regions": sum(row["rendered"] for row in render_rows),
        "renderer_skipped_regions": sum(row["renderer_skipped"] for row in render_rows),
        "pages": render_rows, "preview_cbz": str(cbz_path),
    }
    metrics["status"] = "complete" if metrics["rendering"]["status"] == "complete" else "render_incomplete"
    metrics["elapsed_full_trial_seconds"] = round(time.perf_counter() - started, 2)
    metrics["prior_ocr_elapsed_seconds"] = ocr.get("total_seconds")
    metrics["end_to_end_including_saved_ocr_seconds"] = round(
        metrics["elapsed_full_trial_seconds"] + float(ocr.get("total_seconds", 0)), 2
    )
    save_json(OUT / "request_metrics.json", metrics)

    lines = [
        "# Chapter 12 Translation Trial",
        "",
        f"- Status: {metrics['status']}",
        f"- Model: {MODEL} (Free Tier)",
        f"- OCR coverage: {ocr['pages_completed']}/{ocr['pages_expected']} pages; one page had no text detected.",
        f"- English regions sent: {len(records)} ({metrics['dialogue_source_regions']} speech bubbles, {metrics['external_source_regions']} external-text regions).",
        f"- OCR review flags: {metrics['ocr_review_flagged_regions']}.",
        f"- Translated regions: {len(merged)}/{len(records)}; missing: {len(missing)}.",
        f"- API requests sent: {len(metrics['requests'])}; estimated RPD after: {metrics['estimated_rpd_after']}.",
        f"- Token totals: {json.dumps(totals)}.",
        f"- API latency: {metrics['api_latency_ms']} ms.",
        f"- Translation + rendering elapsed: {metrics['elapsed_full_trial_seconds']} seconds.",
        f"- Saved OCR duration: {metrics['prior_ocr_elapsed_seconds']} seconds.",
        f"- End-to-end using saved OCR duration: {metrics['end_to_end_including_saved_ocr_seconds']} seconds.",
        f"- Rendered pages: {metrics['rendering']['pages_completed']}/{metrics['rendering']['pages_expected']}; skipped by renderer: {metrics['rendering']['renderer_skipped_regions']}.",
        f"- Preview: {cbz_path}",
        "",
        "Automated translation preview only; no human gold translation was available, so this report does not claim a semantic accuracy percentage.",
    ]
    (OUT / "CHAPTER12_TRANSLATION_TRIAL_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": metrics["status"], "chapter": CHAPTER, "model": MODEL,
        "eligible": len(records), "translated": len(merged), "missing": len(missing),
        "requests": len(metrics["requests"]), "token_totals": totals,
        "api_latency_ms": metrics["api_latency_ms"],
        "elapsed_full_trial_seconds": metrics["elapsed_full_trial_seconds"],
        "prior_ocr_seconds": metrics["prior_ocr_elapsed_seconds"],
        "end_to_end_seconds": metrics["end_to_end_including_saved_ocr_seconds"],
        "pages_rendered": metrics["rendering"]["pages_completed"],
        "renderer_skipped": metrics["rendering"]["renderer_skipped_regions"],
        "preview": str(cbz_path), "report": str(OUT / "CHAPTER12_TRANSLATION_TRIAL_REPORT.md"),
    }, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
