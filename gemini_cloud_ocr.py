from __future__ import annotations

import base64
import io
import json
import os
import random
import re
import threading
import time
from typing import Callable

import requests
from PIL import Image

OCR_MODEL = "gemini-3.5-flash-lite"
BATCH_SIZE = 20
MAX_ATTEMPTS = 3
_MIN_REQUEST_INTERVAL = float(os.environ.get("GEMINI_MIN_REQUEST_INTERVAL", "4.0"))
_REQUEST_LOCK = threading.Lock()
_LAST_REQUEST_START = 0.0

CATEGORIES = (
    "dialogue", "narration", "caption", "sign", "phone_text",
    "sound_effect", "watermark", "ui", "decorative",
    "non_english", "other", "uncertain",
)


def _wait_for_rate_limit() -> None:
    global _LAST_REQUEST_START
    with _REQUEST_LOCK:
        now = time.monotonic()
        delay = _MIN_REQUEST_INTERVAL - (now - _LAST_REQUEST_START)
        if delay > 0:
            time.sleep(delay)
        _LAST_REQUEST_START = time.monotonic()


def _crop_region(image_bytes: bytes, region: dict) -> bytes:
    with Image.open(io.BytesIO(image_bytes)) as opened:
        image = opened.convert("RGB")
        width, height = image.size
        left = float(region.get("left", 0))
        top = float(region.get("top", 0))
        box_width = float(region.get("width", 0))
        box_height = float(region.get("height", 0))
        if box_width <= 0 or box_height <= 0:
            raise ValueError("منطقة OCR لا تحتوي إحداثيات صالحة.")
        pad_x = max(5, int(box_width * 0.12))
        pad_y = max(5, int(box_height * 0.20))
        box = (
            max(0, int(left) - pad_x),
            max(0, int(top) - pad_y),
            min(width, int(left + box_width) + pad_x),
            min(height, int(top + box_height) + pad_y),
        )
        if box[2] <= box[0] or box[3] <= box[1]:
            raise ValueError("إحداثيات منطقة OCR خارج حدود الصورة.")
        buffer = io.BytesIO()
        image.crop(box).save(buffer, format="JPEG", quality=92, optimize=True)
        return buffer.getvalue()


def _request_batch(api_key: str, batch: list[dict], batch_number: int) -> tuple[dict, dict | None]:
    ids = [item["id"] for item in batch]
    prompt = (
        f"OCR only. Batch {batch_number}; input IDs: {', '.join(ids)}. "
        "Read each attached comic crop separately and transcribe visible text verbatim. "
        "Do not translate, paraphrase, or infer unreadable letters. Return each ID exactly once. "
        "Classify each crop as dialogue, narration, caption, sign, phone_text, sound_effect, "
        "watermark, ui, decorative, non_english, other, or uncertain. Use empty text only "
        "if no readable text is visible."
    )
    parts = [{"text": prompt}]
    for item in batch:
        parts.append({"text": (
            f"\\nID={item['id']}; page={item.get('page_index')}; "
            f"order={item.get('region_index')}; kind={item.get('region_type', 'unknown')}\\n"
        )})
        parts.append({"inlineData": {
            "mimeType": "image/jpeg",
            "data": base64.b64encode(item["_crop"]).decode("ascii"),
        }})
    schema = {
        "type": "OBJECT",
        "properties": {"regions": {"type": "ARRAY", "items": {
            "type": "OBJECT",
            "properties": {
                "id": {"type": "STRING", "enum": ids},
                "text": {"type": "STRING"},
                "content_category": {"type": "STRING", "enum": list(CATEGORIES)},
            },
            "required": ["id", "text", "content_category"],
        }}},
        "required": ["regions"],
    }
    body = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {
            "temperature": 0,
            "maxOutputTokens": 3500,
            "responseMimeType": "application/json",
            "responseSchema": schema,
        },
    }
    endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{OCR_MODEL}:generateContent"
    log = {"batch": batch_number, "attempts": [], "ids_sent": ids}
    pending = list(batch)
    merged = {}
    for attempt in range(1, MAX_ATTEMPTS + 1):
        if not pending:
            break
        # Keep only missing IDs in a follow-up request after a partial JSON response.
        if len(pending) != len(batch):
            log["attempts"][-1]["missing_ids_retried"] = [x["id"] for x in pending]
            return log, {"regions": list(merged.values())}
        _wait_for_rate_limit()
        started = time.perf_counter()
        record = {"attempt": attempt, "request_sent": True, "status": None}
        try:
            response = requests.post(
                endpoint,
                headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
                json=body,
                timeout=(20, 240),
            )
            record["status"] = response.status_code
            record["elapsed_seconds"] = round(time.perf_counter() - started, 3)
            if response.status_code == 429:
                record["error"] = "rate_limit"
                log["attempts"].append(record)
                break
            if response.status_code in {500, 502, 503, 504}:
                record["error"] = f"http_{response.status_code}"
                log["attempts"].append(record)
                if attempt < MAX_ATTEMPTS:
                    retry_after = response.headers.get("Retry-After", "")
                    try:
                        delay = float(retry_after)
                    except (TypeError, ValueError):
                        delay = min(90, 20 * (2 ** (attempt - 1)) + random.uniform(0, 3))
                    log["attempts"][-1]["retry_wait_seconds"] = round(delay, 1)
                    time.sleep(delay)
                    continue
                break
            if not response.ok:
                record["error"] = f"http_{response.status_code}"
                log["attempts"].append(record)
                break
            payload = response.json()
            usage = payload.get("usageMetadata", {})
            record["usage"] = {
                "promptTokenCount": usage.get("promptTokenCount"),
                "candidatesTokenCount": usage.get("candidatesTokenCount"),
                "thoughtsTokenCount": usage.get("thoughtsTokenCount"),
                "totalTokenCount": usage.get("totalTokenCount"),
            }
            raw = "".join(
                part.get("text", "")
                for part in payload["candidates"][0]["content"]["parts"]
                if isinstance(part, dict) and isinstance(part.get("text"), str)
            )
            decoded = json.loads(raw)
            returned = decoded.get("regions")
            if not isinstance(returned, list):
                raise ValueError("missing_regions")
            ids_returned = [str(item.get("id", "")) for item in returned if isinstance(item, dict)]
            if len(ids_returned) != len(set(ids_returned)):
                raise ValueError("duplicate_ids")
            unknown = set(ids_returned) - set(ids)
            if unknown:
                raise ValueError("unknown_ids")
            merged.update({
                item["id"]: item for item in returned
                if isinstance(item, dict) and item.get("id") in ids
                and isinstance(item.get("text"), str)
                and item.get("content_category") in CATEGORIES
            })
            pending = [item for item in batch if item["id"] not in merged]
            record["returned_ids"] = len(ids_returned)
            record["missing_ids"] = [item["id"] for item in pending]
            log["attempts"].append(record)
            if not pending:
                break
            if attempt >= MAX_ATTEMPTS:
                break
            # Resubmit only missing IDs. An invalid/partial response is separately counted.
            batch = pending
            ids = [item["id"] for item in batch]
            parts = [{"text": (
                "OCR only. Return every provided crop ID exactly once. Transcribe visible text "
                "verbatim without translation. Categorize using the required schema."
            )}]
            for item in batch:
                parts.append({"text": (
                    f"\\nID={item['id']}; page={item.get('page_index')}; "
                    f"order={item.get('region_index')}; kind={item.get('region_type', 'unknown')}\\n"
                )})
                parts.append({"inlineData": {
                    "mimeType": "image/jpeg",
                    "data": base64.b64encode(item["_crop"]).decode("ascii"),
                }})
            schema["properties"]["regions"]["items"]["properties"]["id"]["enum"] = ids
            body["contents"] = [{"role": "user", "parts": parts}]
            log["attempts"][-1]["missing_ids_retried"] = [x["id"] for x in pending]
        except (requests.RequestException, KeyError, IndexError, TypeError, ValueError) as exc:
            record["elapsed_seconds"] = round(time.perf_counter() - started, 3)
            record["error"] = type(exc).__name__
            log["attempts"].append(record)
            # Transport errors and malformed JSON are not blindly replayed.
            break
    log["regions_returned"] = len(merged)
    log["complete"] = len(merged) == len(log["ids_sent"])
    return log, {"regions": list(merged.values()), "usage": [
        item["usage"] for item in log["attempts"] if item.get("usage")
    ]}


def recognize_regions(
    regions: list[dict],
    image_loader: Callable[[str], bytes],
    api_key: str,
) -> tuple[list[dict], dict]:
    """Re-read detector regions through Gemini while preserving IDs and geometry."""
    if not regions:
        return [], {"model": OCR_MODEL, "requests_sent": 0, "complete": True, "batches": []}
    output = []
    logs = []
    usage = []
    batch = []
    for region in regions:
        image_url = str(region.get("page_image", "")).strip()
        image_bytes = image_loader(image_url)
        crop = _crop_region(image_bytes, region)
        row = dict(region)
        row["_crop"] = crop
        batch.append(row)
        if len(batch) < BATCH_SIZE:
            continue
        number = len(logs) + 1
        batch_log, result = _request_batch(api_key, batch, number)
        logs.append(batch_log)
        if result:
            usage.extend(result.get("usage", []))
            by_id = {item["id"]: item for item in result["regions"]}
            for original in batch:
                detected = by_id.get(original["id"])
                output.append({
                    **{key: value for key, value in original.items() if key != "_crop"},
                    "text": str(detected.get("text", "")).strip() if detected else original.get("text", ""),
                    "content_category": detected.get("content_category", "uncertain") if detected else "uncertain",
                    "cloud_ocr_status": "complete" if detected else "local_fallback",
                })
        else:
            for original in batch:
                output.append({
                    **{key: value for key, value in original.items() if key != "_crop"},
                    "content_category": "uncertain",
                    "cloud_ocr_status": "local_fallback",
                })
        batch = []
    if batch:
        batch_log, result = _request_batch(api_key, batch, len(logs) + 1)
        logs.append(batch_log)
        if result:
            usage.extend(result.get("usage", []))
            by_id = {item["id"]: item for item in result["regions"]}
            for original in batch:
                detected = by_id.get(original["id"])
                output.append({
                    **{key: value for key, value in original.items() if key != "_crop"},
                    "text": str(detected.get("text", "")).strip() if detected else original.get("text", ""),
                    "content_category": detected.get("content_category", "uncertain") if detected else "uncertain",
                    "cloud_ocr_status": "complete" if detected else "local_fallback",
                })
        else:
            for original in batch:
                output.append({
                    **{key: value for key, value in original.items() if key != "_crop"},
                    "content_category": "uncertain",
                    "cloud_ocr_status": "local_fallback",
                })
    request_records = [attempt for group in logs for attempt in group["attempts"]]
    metrics = {
        "model": OCR_MODEL,
        "batch_size": BATCH_SIZE,
        "regions_expected": len(regions),
        "regions_cloud_read": sum(item.get("cloud_ocr_status") == "complete" for item in output),
        "regions_fallback": sum(item.get("cloud_ocr_status") != "complete" for item in output),
        "requests_sent": len(request_records),
        "successful_requests": sum(item.get("status") == 200 for item in request_records),
        "failed_requests": sum(item.get("status") not in (200, None) for item in request_records),
        "batches": logs,
        "usage": usage,
    }
    return output, metrics
