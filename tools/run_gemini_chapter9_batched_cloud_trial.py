from __future__ import annotations
import base64, importlib.util, io, json, random, re, time, unicodedata
from pathlib import Path
import requests
from PIL import Image

ROOT = Path(r"D:\MangaArabic\manga-arabic-web")
OUT = ROOT / "ocr_test_results" / "web_chapter_teto_x_egen_ch09"
SOURCE_DIR = OUT / "source_pages"
OCR_PATH = OUT / "ocr_results.json"
KEY_PATH = ROOT / ".env"
TRANSLATOR_PATH = OUT / "translate_chapter_once_per_model.py"
OCR_MODEL = "gemini-3.5-flash-lite"
TRANSLATION_MODEL = "gemini-3.1-flash-lite"
BATCH_SIZE = 20
MAX_ATTEMPTS_PER_BATCH = 3
OCR_OUTPUT = OUT / "gemini35_cloud_ocr_batched_result.json"
OCR_PARTIAL = OUT / "gemini35_cloud_ocr_batched_partial.json"
TRANSLATION_OUTPUT = OUT / "gemini31_cloud_ocr_translation_batched_result.json"
METRICS_OUTPUT = OUT / "gemini_cloud_ocr_batched_metrics.json"
LAST_REQUEST_START = 0.0

def load_key():
    for line in KEY_PATH.read_text(encoding="utf-8-sig").splitlines():
        if line.startswith("GEMINI_API_KEY="):
            key = line.split("=", 1)[1].strip().strip('"').strip("'")
            if key:
                return key
    raise RuntimeError("Gemini API key is unavailable in the secure local env file.")

def make_regions():
    ocr = json.loads(OCR_PATH.read_text(encoding="utf-8"))
    if ocr.get("pages_expected") != 11 or ocr.get("pages_completed") != 11:
        raise RuntimeError("Saved OCR is not a complete 11-page chapter.")
    pages = {int(p["page"]): p for p in ocr["pages"]}
    if len(pages) != 11:
        raise RuntimeError("Saved OCR page index is incomplete.")
    regions = []
    for page_no in range(1, 12):
        rows = sorted(pages[page_no].get("regions", []),
                      key=lambda row: (float(row.get("top", 0)), float(row.get("left", 0))))
        with Image.open(SOURCE_DIR / f"page_{page_no:02d}.webp") as opened:
            image = opened.convert("RGB")
            width, height = image.size
            for order, row in enumerate(rows, 1):
                left, top = float(row.get("left", 0)), float(row.get("top", 0))
                box_w, box_h = float(row.get("width", 0)), float(row.get("height", 0))
                if box_w <= 0 or box_h <= 0:
                    continue
                pad_x, pad_y = max(5, int(box_w * .12)), max(5, int(box_h * .20))
                crop = image.crop((max(0, int(left)-pad_x), max(0, int(top)-pad_y),
                                   min(width, int(left+box_w)+pad_x), min(height, int(top+box_h)+pad_y)))
                buffer = io.BytesIO()
                crop.save(buffer, format="JPEG", quality=92, optimize=True)
                regions.append({
                    "region_id": f"p{page_no:02d}-r{order:03d}",
                    "page": page_no, "order": order,
                    "region_type": row.get("region_type", "unknown"),
                    "bbox": [round(left,1), round(top,1), round(box_w,1), round(box_h,1)],
                    "local_ocr_text": str(row.get("text","")).strip(),
                    "image_bytes": buffer.getvalue(),
                })
    return ocr, regions

def wait_for_rpm_spacing():
    global LAST_REQUEST_START
    now = time.perf_counter()
    remaining = 4.1 - (now - LAST_REQUEST_START)
    if LAST_REQUEST_START and remaining > 0:
        time.sleep(remaining)
    LAST_REQUEST_START = time.perf_counter()

def one_ocr_attempt(model, key, batch, batch_index, attempt):
    categories = ["dialogue","narration","caption","sign","phone_text","sound_effect",
                  "watermark","ui","decorative","non_english","other","uncertain"]
    ids = [r["region_id"] for r in batch]
    parts = [{
        "text": (
            f"OCR only for Teto x Egen chapter 9. Batch {batch_index}; input region ids: {', '.join(ids)}. "
            "Read the attached crops, do not translate. Return each id exactly once. Transcribe visible text "
            "verbatim; do not infer unreadable letters. Categorize as dialogue, narration, caption, sign, phone_text, "
            "sound_effect, watermark, ui, decorative, non_english, other, or uncertain. Use empty text only when "
            "the crop truly contains no readable text. Keep every crop separate."
        )
    }]
    for region in batch:
        parts.append({"text": f"\nID={region['region_id']}; page={region['page']}; order={region['order']}; kind={region['region_type']}\n"})
        parts.append({"inlineData": {"mimeType":"image/jpeg",
                                    "data":base64.b64encode(region["image_bytes"]).decode("ascii")}})
    schema = {
        "type":"OBJECT",
        "properties":{
            "regions":{"type":"ARRAY","items":{
                "type":"OBJECT",
                "properties":{
                    "region_id":{"type":"STRING","enum":ids},
                    "text":{"type":"STRING"},
                    "content_category":{"type":"STRING","enum":categories},
                },
                "required":["region_id","text","content_category"]
            }}
        },
        "required":["regions"]
    }
    body = {"contents":[{"role":"user","parts":parts}],
            "generationConfig":{"temperature":0,"maxOutputTokens":3500,
                                "responseMimeType":"application/json","responseSchema":schema}}
    endpoint=f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    wait_for_rpm_spacing()
    started=time.perf_counter()
    record={"batch":batch_index,"attempt":attempt,"request_sent":True,"http_status":None,
            "elapsed_seconds":None,"usage":None,"error":None,"successful":False}
    try:
        response=requests.post(endpoint,headers={"x-goog-api-key":key,"content-type":"application/json"},
                               json=body,timeout=(20,240))
        record["http_status"]=response.status_code
        record["elapsed_seconds"]=round(time.perf_counter()-started,3)
        if not response.ok:
            record["error"]=response.text[:1800]
            return record,None
        payload=response.json()
        usage=payload.get("usageMetadata",{})
        record["usage"]={"promptTokenCount":usage.get("promptTokenCount"),
                         "candidatesTokenCount":usage.get("candidatesTokenCount"),
                         "thoughtsTokenCount":usage.get("thoughtsTokenCount"),
                         "totalTokenCount":usage.get("totalTokenCount")}
        record["finish_reason"]=(payload.get("candidates") or [{}])[0].get("finishReason")
        text="".join(p.get("text","") for p in
                     (payload.get("candidates") or [{}])[0].get("content",{}).get("parts",[])
                     if "text" in p)
        try:
            record["successful"]=True
            return record,json.loads(text)
        except (ValueError,TypeError):
            record["error"]="Successful HTTP response did not contain valid JSON."
            return record,None
    except requests.Timeout:
        record["elapsed_seconds"]=round(time.perf_counter()-started,3)
        record["error"]="Timed out; server-side quota accounting is unknown."
        return record,None
    except requests.RequestException as exc:
        record["elapsed_seconds"]=round(time.perf_counter()-started,3)
        record["error"]=type(exc).__name__+": "+str(exc)[:500]
        return record,None

def english_fraction(text):
    letters=[ch for ch in text if unicodedata.category(ch).startswith("L")]
    latin=sum("LATIN" in unicodedata.name(ch,"") for ch in letters)
    return latin/max(1,len(letters))

def main():
    ocr, regions = make_regions()
    key = load_key()
    batches = [regions[i:i+BATCH_SIZE] for i in range(0,len(regions),BATCH_SIZE)]
    all_results, request_logs, failed_batch = [], [], None
    run_started=time.perf_counter()
    for batch_index,batch in enumerate(batches,1):
        batch_success=None
        for attempt in range(1,MAX_ATTEMPTS_PER_BATCH+1):
            req_log,response_data=one_ocr_attempt(OCR_MODEL,key,batch,batch_index,attempt)
            request_logs.append(req_log)
            if req_log.get("successful") and response_data:
                returned=response_data.get("regions",[])
                ids=[str(x.get("region_id","")) for x in returned]
                expected=[r["region_id"] for r in batch]
                if (len(ids)==len(expected) and set(ids)==set(expected) and len(set(ids))==len(ids)):
                    by_id={str(x["region_id"]):x for x in returned}
                    batch_success=[
                        {**{k:v for k,v in r.items() if k != "image_bytes"},
                         "cloud_text":str(by_id[r["region_id"]].get("text","")).strip(),
                         "content_category":by_id[r["region_id"]].get("content_category","uncertain")}
                        for r in batch
                    ]
                    break
                req_log["error"]="OCR JSON had missing, duplicated, or unknown ids."
                req_log["id_validation"]={"expected":len(expected),"received":len(ids),
                    "missing":sorted(set(expected)-set(ids)),
                    "duplicates":sorted({x for x in ids if ids.count(x)>1}),
                    "unknown":sorted(set(ids)-set(expected))}
                break
            status=req_log.get("http_status")
            if status==429:
                req_log["stop_reason"]="Quota or rate limit; stopped immediately."
                break
            retryable=status in (500,502,503,504)
            if not retryable or attempt>=MAX_ATTEMPTS_PER_BATCH:
                break
            retry_after=None
            if status:
                error=req_log.get("error","")
                match=re.search(r'"retryDelay"\s*:\s*"([0-9.]+)s"',error)
                if match: retry_after=float(match.group(1))
            delay=min(180,max((20*(2**(attempt-1))+random.uniform(0,5)),retry_after or 0))
            req_log["retry_wait_seconds"]=round(delay,1)
            time.sleep(delay)
        if batch_success is None:
            failed_batch=batch_index
            break
        all_results.extend(batch_success)
        partial={"model":OCR_MODEL,"batch_size":BATCH_SIZE,"batches_total":len(batches),
                 "batches_completed":batch_index,"regions_completed":len(all_results),
                 "regions":all_results}
        OCR_PARTIAL.write_text(json.dumps(partial,ensure_ascii=False,indent=2),encoding="utf-8")
        print(json.dumps({"stage":"ocr","batch":batch_index,"batches":len(batches),
                          "regions_completed":len(all_results),"request_status":request_logs[-1]["http_status"],
                          "seconds":request_logs[-1]["elapsed_seconds"]},ensure_ascii=False),flush=True)
    ocr_complete=(failed_batch is None and len(all_results)==len(regions))
    OCR_OUTPUT.write_text(json.dumps({"model":OCR_MODEL,"complete":ocr_complete,
        "pages":11,"regions_expected":len(regions),"regions_returned":len(all_results),
        "failed_batch":failed_batch,"regions":all_results},ensure_ascii=False,indent=2),encoding="utf-8")
    translation_result=None
    translatable=[]
    if ocr_complete:
        excluded={"sound_effect","watermark","ui","decorative","non_english","uncertain"}
        for region in all_results:
            text=region["cloud_text"]
            if text and region["content_category"] not in excluded and english_fraction(text)>=.70:
                translatable.append({"id":region["region_id"],"page":region["page"],
                    "order":region["order"],"region_type":region["region_type"],
                    "content_category":region["content_category"],"text":text})
        translator_spec=importlib.util.spec_from_file_location("saved_ch9_translation",TRANSLATOR_PATH)
        translator=importlib.util.module_from_spec(translator_spec)
        translator_spec.loader.exec_module(translator)
        translation_result=translator.request_model(TRANSLATION_MODEL,key,translatable)
        if translation_result.get("response_json"):
            returned=translation_result["response_json"].get("items",[])
            sent=[x["id"] for x in translatable]
            got=[str(x.get("id","")) for x in returned]
            translation_result["id_validation"]={"sent":len(sent),"received":len(got),
                "missing":sorted(set(sent)-set(got)),
                "duplicates":sorted({x for x in got if got.count(x)>1}),
                "unknown":sorted(set(got)-set(sent))}
            translation_result["complete_translation_set"]=(
                len(sent)==len(got) and set(sent)==set(got) and len(got)==len(set(got)))
            translation_result["translated_count"]=sum(
                1 for x in returned if x.get("decision")=="translate" and x.get("translation_ar"))
        TRANSLATION_OUTPUT.write_text(json.dumps(translation_result,ensure_ascii=False,indent=2),encoding="utf-8")
    metrics={
        "chapter":"Teto x Egen Chapter 9","pages":11,"regions_expected":len(regions),
        "cloud_ocr_model":OCR_MODEL,"translation_model":TRANSLATION_MODEL,
        "batch_size":BATCH_SIZE,"batches_total":len(batches),
        "batches_completed":len(all_results)//BATCH_SIZE+(1 if len(all_results)%BATCH_SIZE else 0),
        "regions_completed":len(all_results),"cloud_ocr_complete":ocr_complete,
        "failed_batch":failed_batch,"requests_sent":len(request_logs),
        "accepted_ocr_requests":sum(1 for x in request_logs if x.get("http_status")==200),
        "failed_ocr_http_requests":sum(1 for x in request_logs if x.get("http_status") not in (None,200)),
        "ocr_request_log":request_logs,
        "translation_request_sent":translation_result is not None,
        "translation_request_count":1 if translation_result is not None else 0,
        "translation":({k:v for k,v in translation_result.items() if k not in {"response_text","response_json"}}
                      if translation_result else None),
        "translation_candidate_count":len(translatable),
        "elapsed_wall_seconds":round(time.perf_counter()-run_started,3),
        "free_tier_only_intended":True,"billing_settings_changed":False,
        "quota_500_share_submitted_percent":round(len(request_logs)/500*100,3),
        "quota_accounting_for_failed_requests":"unknown; verify dashboard after run",
        "note":"Requests are sequential, capped to 3 attempts per batch, exponential backoff for retryable 5xx, and stop on 429."
    }
    METRICS_OUTPUT.write_text(json.dumps(metrics,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"stage":"complete","cloud_ocr_complete":ocr_complete,
        "regions":len(all_results),"requests_sent":len(request_logs),
        "accepted_ocr_requests":metrics["accepted_ocr_requests"],
        "translation_sent":translation_result is not None,
        "translation_success":translation_result.get("success") if translation_result else None,
        "translation_elapsed":translation_result.get("elapsed_seconds") if translation_result else None,
        "translation_usage":translation_result.get("usage") if translation_result else None,
        "wall_seconds":metrics["elapsed_wall_seconds"],"metrics_path":str(METRICS_OUTPUT)},
        ensure_ascii=False),flush=True)

if __name__=="__main__": main()
