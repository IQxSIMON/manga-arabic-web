from __future__ import annotations
import base64, io, json, re, time, unicodedata
from pathlib import Path
import requests
from PIL import Image

ROOT = Path(r"D:\MangaArabic\manga-arabic-web")
OUT = ROOT / "ocr_test_results" / "web_chapter_teto_x_egen_ch09"
OCR_PATH = OUT / "ocr_results.json"
SOURCE_DIR = OUT / "source_pages"
KEY_PATH = ROOT / ".env"
OCR_MODEL = "gemini-3.5-flash-lite"
TRANSLATION_MODEL = "gemini-3.1-flash-lite"
METRICS_PATH = OUT / "gemini_cloud_ocr_translation_trial_metrics.json"
OCR_RESULT_PATH = OUT / "gemini35_cloud_ocr_result.json"
TRANSLATION_RESULT_PATH = OUT / "gemini31_cloud_ocr_translation_result.json"

def load_key():
    for line in KEY_PATH.read_text(encoding="utf-8-sig").splitlines():
        if line.startswith("GEMINI_API_KEY="):
            value = line.split("=", 1)[1].strip().strip('"').strip("'")
            if value:
                return value
    raise RuntimeError("Gemini API key is unavailable in the secure local env file.")

def call_once(model, key, contents, response_schema, max_tokens):
    body = {
        "contents": [{"role": "user", "parts": contents}],
        "generationConfig": {
            "temperature": 0,
            "maxOutputTokens": max_tokens,
            "responseMimeType": "application/json",
            "responseSchema": response_schema,
        },
    }
    endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    started = time.perf_counter()
    try:
        response = requests.post(
            endpoint,
            headers={"x-goog-api-key": key, "content-type": "application/json"},
            json=body,
            timeout=(20, 360),
        )
        elapsed = round(time.perf_counter() - started, 3)
        result = {
            "model": model,
            "http_status": response.status_code,
            "success": response.ok,
            "elapsed_seconds": elapsed,
            "requests_sent": 1,
            "attempts": 1,
            "usage": None,
            "response_json": None,
            "error": None,
        }
        if not response.ok:
            result["error"] = response.text[:3000]
            return result
        payload = response.json()
        usage = payload.get("usageMetadata", {})
        result["usage"] = {
            "promptTokenCount": usage.get("promptTokenCount"),
            "candidatesTokenCount": usage.get("candidatesTokenCount"),
            "thoughtsTokenCount": usage.get("thoughtsTokenCount"),
            "totalTokenCount": usage.get("totalTokenCount"),
        }
        result["finish_reason"] = (payload.get("candidates") or [{}])[0].get("finishReason")
        parts = (payload.get("candidates") or [{}])[0].get("content", {}).get("parts", [])
        response_text = "".join(str(p.get("text", "")) for p in parts if "text" in p)
        result["response_text"] = response_text
        try:
            result["response_json"] = json.loads(response_text)
        except (ValueError, TypeError):
            result["parse_error"] = "Response was not valid JSON."
        return result
    except requests.Timeout:
        return {
            "model": model, "http_status": None, "success": False,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "requests_sent": 1, "attempts": 1, "usage": None,
            "error": "Timed out; server-side usage status is unknown.",
        }
    except requests.RequestException as exc:
        # Do not record request headers, API keys, or credential-bearing URLs.
        return {
            "model": model, "http_status": None, "success": False,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "requests_sent": 1, "attempts": 1, "usage": None,
            "error": type(exc).__name__ + ": " + str(exc)[:500],
        }

def main():
    ocr = json.loads(OCR_PATH.read_text(encoding="utf-8"))
    if ocr.get("pages_expected") != 11 or ocr.get("pages_completed") != 11:
        raise RuntimeError("Saved OCR does not contain all 11 chapter pages.")
    page_by_number = {int(p["page"]): p for p in ocr["pages"]}
    if len(page_by_number) != 11:
        raise RuntimeError("Saved OCR page index is incomplete or duplicated.")
    regions = []
    parts = [{
        "text": (
            "Read the English and other visible text from each attached crop. This is OCR only: do not translate. "
            "Each crop is preceded by its immutable region_id, page number, reading order, and local detector type. "
            "Transcribe the crop exactly, preserving punctuation and line breaks when possible. Classify it as dialogue, "
            "narration, caption, sign, phone_text, sound_effect, watermark, ui, decorative, non_english, other, or uncertain. "
            "If no readable text is visible, return empty text and uncertain. Return every supplied region_id exactly once, "
            "never invent ids, and do not merge regions. The crop images are in chapter reading order."
        )
    }]
    image_bytes_total = 0
    for page_number in range(1, 12):
        page = page_by_number[page_number]
        image_path = SOURCE_DIR / f"page_{page_number:02d}.webp"
        page_regions = sorted(
            page.get("regions", []),
            key=lambda r: (float(r.get("top", 0)), float(r.get("left", 0))),
        )
        with Image.open(image_path) as source:
            source = source.convert("RGB")
            image_width, image_height = source.size
            for order, row in enumerate(page_regions, 1):
                left = float(row.get("left", 0))
                top = float(row.get("top", 0))
                width = float(row.get("width", 0))
                height = float(row.get("height", 0))
                if width <= 0 or height <= 0:
                    continue
                px = max(5, int(width * 0.12))
                py = max(5, int(height * 0.20))
                x0 = max(0, int(left) - px)
                y0 = max(0, int(top) - py)
                x1 = min(image_width, int(left + width) + px)
                y1 = min(image_height, int(top + height) + py)
                crop = source.crop((x0, y0, x1, y1))
                buffer = io.BytesIO()
                crop.save(buffer, format="JPEG", quality=92, optimize=True)
                raw = buffer.getvalue()
                image_bytes_total += len(raw)
                region_id = f"p{page_number:02d}-r{order:03d}"
                region = {
                    "region_id": region_id,
                    "page": page_number,
                    "order": order,
                    "region_type": row.get("region_type", "unknown"),
                    "bbox": [round(left, 1), round(top, 1), round(width, 1), round(height, 1)],
                    "local_ocr_text": str(row.get("text", "")).strip(),
                }
                regions.append(region)
                parts.append({"text": f"\n\nREGION_ID={region_id}; PAGE={page_number}; ORDER={order}; DETECTOR_TYPE={region['region_type']}\n"})
                parts.append({"inlineData": {"mimeType": "image/jpeg", "data": base64.b64encode(raw).decode("ascii")}})
    request_body_size_estimate = int(image_bytes_total * 4 / 3) + 18000
    if request_body_size_estimate > 18 * 1024 * 1024:
        raise RuntimeError(
            f"Single inline-image OCR request may exceed safe request size; estimated bytes={request_body_size_estimate}. "
            "No API request was sent."
        )
    ocr_schema = {
        "type": "OBJECT",
        "properties": {
            "chapter_title": {"type": "STRING"},
            "regions": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "region_id": {"type": "STRING"},
                        "text": {"type": "STRING"},
                        "content_category": {
                            "type": "STRING",
                            "enum": ["dialogue", "narration", "caption", "sign", "phone_text", "sound_effect",
                                     "watermark", "ui", "decorative", "non_english", "other", "uncertain"],
                        },
                    },
                    "required": ["region_id", "text", "content_category"],
                },
            },
        },
        "required": ["chapter_title", "regions"],
    }
    key = load_key()
    total_started = time.perf_counter()
    ocr_result = call_once(OCR_MODEL, key, parts, ocr_schema, 16000)
    if ocr_result.get("response_json"):
        returned = ocr_result["response_json"].get("regions", [])
        ids = [str(x.get("region_id", "")) for x in returned]
        expected = [r["region_id"] for r in regions]
        ocr_result["region_validation"] = {
            "expected_regions": len(expected),
            "returned_regions": len(ids),
            "missing_ids": sorted(set(expected) - set(ids)),
            "duplicate_ids": sorted({x for x in ids if ids.count(x) > 1}),
            "unknown_ids": sorted(set(ids) - set(expected)),
        }
        valid = (
            len(ids) == len(expected)
            and not ocr_result["region_validation"]["missing_ids"]
            and not ocr_result["region_validation"]["duplicate_ids"]
            and not ocr_result["region_validation"]["unknown_ids"]
        )
        ocr_result["complete_region_set"] = valid
        if valid:
            by_id = {x["region_id"]: x for x in returned}
            ocr_result["regions_with_geometry"] = [
                {**region, "cloud_text": str(by_id[region["region_id"]].get("text", "")).strip(),
                 "content_category": by_id[region["region_id"]].get("content_category", "uncertain")}
                for region in regions
            ]
    OCR_RESULT_PATH.write_text(json.dumps(ocr_result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "phase": "gemini_3_5_cloud_ocr",
        "success": ocr_result.get("success"),
        "http_status": ocr_result.get("http_status"),
        "elapsed_seconds": ocr_result.get("elapsed_seconds"),
        "usage": ocr_result.get("usage"),
        "regions": len(regions),
        "image_bytes": image_bytes_total,
        "complete_region_set": ocr_result.get("complete_region_set"),
    }, ensure_ascii=False), flush=True)
    translation_result = None
    translation_items = []
    if ocr_result.get("success") and ocr_result.get("complete_region_set"):
        excluded_categories = {
            "sound_effect", "watermark", "ui", "decorative", "non_english", "uncertain"
        }
        for region in ocr_result["regions_with_geometry"]:
            text = region["cloud_text"]
            category = region["content_category"]
            latin = sum(
                unicodedata.category(ch).startswith("L")
                and "LATIN" in unicodedata.name(ch, "")
                for ch in text
            )
            letters = sum(unicodedata.category(ch).startswith("L") for ch in text)
            if (not text or category in excluded_categories
                    or (letters and latin / letters < 0.70)):
                continue
            translation_items.append({
                "id": region["region_id"],
                "page": region["page"],
                "order": region["order"],
                "region_type": region["region_type"],
                "content_category": category,
                "source_text": text,
            })
        translation_schema = {
            "type": "OBJECT",
            "properties": {
                "chapter_title": {"type": "STRING"},
                "items": {
                    "type": "ARRAY",
                    "items": {
                        "type": "OBJECT",
                        "properties": {
                            "id": {"type": "STRING"},
                            "decision": {"type": "STRING", "enum": ["translate", "exclude", "uncertain"]},
                            "translation_ar": {"type": "STRING"},
                            "reason": {"type": "STRING"},
                        },
                        "required": ["id", "decision", "translation_ar", "reason"],
                    },
                },
            },
            "required": ["chapter_title", "items"],
        }
        translation_prompt = (
            "Translate the complete chapter's meaningful English dialogue and text into natural, fluent Arabic. "
            "Use all items as chapter context; preserve names, relationships, tone, meaning, and page reading order. "
            "Translate dialogue, thoughts, narration, captions, phone text, and meaningful signs. Do not translate "
            "sound effects, decorative lettering, watermarks, UI text, non-English text, or OCR gibberish; mark those "
            "excluded. Return JSON only. Every supplied id must appear exactly once, with no merged ids. For uncertain "
            "or fragmentary OCR, avoid inventing context and mark uncertain. Arabic should be idiomatic and readable, "
            "not a word-for-word calque.\n\nCHAPTER: Teto x Egen, Chapter 9\n"
            "READING ORDER: top-to-bottom, then left-to-right within each page.\n"
            "OCR_FROM_GEMINI_3_5_JSON:\n"
            + json.dumps(translation_items, ensure_ascii=False, separators=(",", ":"))
        )
        translation_parts = [{"text": translation_prompt}]
        translation_result = call_once(
            TRANSLATION_MODEL, key, translation_parts, translation_schema, 16000
        )
        if translation_result.get("response_json"):
            returned = translation_result["response_json"].get("items", [])
            sent_ids = [x["id"] for x in translation_items]
            got_ids = [str(x.get("id", "")) for x in returned]
            translation_result["id_validation"] = {
                "sent": len(sent_ids),
                "received": len(got_ids),
                "missing_ids": sorted(set(sent_ids) - set(got_ids)),
                "duplicate_ids": sorted({x for x in got_ids if got_ids.count(x) > 1}),
                "unknown_ids": sorted(set(got_ids) - set(sent_ids)),
            }
            translation_result["complete_translation_set"] = (
                len(sent_ids) == len(got_ids)
                and not translation_result["id_validation"]["missing_ids"]
                and not translation_result["id_validation"]["duplicate_ids"]
                and not translation_result["id_validation"]["unknown_ids"]
            )
            translation_result["translated_count"] = sum(
                1 for item in returned if item.get("decision") == "translate" and item.get("translation_ar")
            )
        TRANSLATION_RESULT_PATH.write_text(
            json.dumps(translation_result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps({
            "phase": "gemini_3_1_translation",
            "success": translation_result.get("success"),
            "http_status": translation_result.get("http_status"),
            "elapsed_seconds": translation_result.get("elapsed_seconds"),
            "usage": translation_result.get("usage"),
            "items_sent": len(translation_items),
            "complete_translation_set": translation_result.get("complete_translation_set"),
            "translated_count": translation_result.get("translated_count"),
        }, ensure_ascii=False), flush=True)
    metrics = {
        "chapter": "Teto x Egen, Chapter 9",
        "pages": 11,
        "source_regions": len(regions),
        "cloud_ocr_model": OCR_MODEL,
        "translation_model": TRANSLATION_MODEL,
        "cloud_ocr_request_count": ocr_result.get("requests_sent", 1),
        "translation_request_count": translation_result.get("requests_sent", 0) if translation_result else 0,
        "request_retries": 0,
        "requests_parallel_wall_seconds": round(time.perf_counter() - total_started, 3),
        "free_tier_only": True,
        "billing_settings_changed": False,
        "cloud_ocr": {k: v for k, v in ocr_result.items()
                      if k not in {"response_text", "response_json", "regions_with_geometry"}},
        "translation": ({k: v for k, v in translation_result.items()
                        if k not in {"response_text", "response_json"}} if translation_result else None),
        "translation_not_sent_reason": (
            None if translation_result else
            "Cloud OCR request failed or did not return every region id; no partial chapter translation was sent."
        ),
    }
    METRICS_PATH.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "phase": "complete",
        "metrics_path": str(METRICS_PATH),
        "total_elapsed_seconds": metrics["requests_parallel_wall_seconds"],
        "requests_sent": metrics["cloud_ocr_request_count"] + metrics["translation_request_count"],
    }, ensure_ascii=False), flush=True)

if __name__ == "__main__":
    main()
