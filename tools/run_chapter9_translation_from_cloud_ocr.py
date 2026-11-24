from __future__ import annotations
import importlib.util, json, time, unicodedata
from pathlib import Path

ROOT=Path(r"D:\MangaArabic\manga-arabic-web")
OUT=ROOT/"ocr_test_results"/"web_chapter_teto_x_egen_ch09"
OCR_PATH=OUT/"gemini35_cloud_ocr_batched_result.json"
TRANSLATOR_PATH=OUT/"translate_chapter_once_per_model.py"
FINAL_PATH=OUT/"gemini31_cloud_translation_from_gemini35_ocr.json"
MAX_ATTEMPTS=3

spec=importlib.util.spec_from_file_location("saved_chapter9_translator",TRANSLATOR_PATH)
translator=importlib.util.module_from_spec(spec)
spec.loader.exec_module(translator)

def main():
    ocr=json.loads(OCR_PATH.read_text(encoding="utf-8"))
    if not ocr.get("complete") or ocr.get("regions_expected")!=ocr.get("regions_returned"):
        raise RuntimeError("Cloud OCR is incomplete; translation was not sent.")
    excluded={"sound_effect","watermark","ui","decorative","non_english","uncertain"}
    items=[]
    for region in ocr["regions"]:
        text=str(region.get("cloud_text","")).strip()
        letters=[c for c in text if unicodedata.category(c).startswith("L")]
        latin=sum("LATIN" in unicodedata.name(c,"") for c in letters)
        if not text or region.get("content_category") in excluded or latin/max(1,len(letters))<.70:
            continue
        items.append({
            "id":region["region_id"],"page":int(region["page"]),"order":int(region["order"]),
            "region_type":region.get("region_type","unknown"),
            "text":text,"confidence":None,"bubble_id":None
        })
    if len(items)!=110:
        raise RuntimeError(f"Candidate list changed; expected 110 based on preflight, found {len(items)}.")
    key=translator.load_api_key()
    attempts=[]
    for attempt in range(1,MAX_ATTEMPTS+1):
        result=translator.request_model("gemini-3.1-flash-lite",key,items)
        result["attempt_number"]=attempt
        result["request_retry_count"]=attempt-1
        attempts.append(result)
        attempt_path=OUT/f"gemini31_cloud_translation_from_gemini35_ocr_attempt{attempt}.json"
        attempt_path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
        if result.get("success") and result.get("response_json"):
            returned=result["response_json"].get("items",[])
            sent=[x["id"] for x in items]
            got=[str(x.get("id","")) for x in returned]
            result["id_validation"]={
                "sent":len(sent),"received":len(got),
                "missing":sorted(set(sent)-set(got)),
                "duplicates":sorted({x for x in got if got.count(x)>1}),
                "unknown":sorted(set(got)-set(sent))
            }
            result["complete_translation_set"]=(
                len(sent)==len(got) and set(sent)==set(got) and len(got)==len(set(got))
            )
            result["translated_count"]=sum(
                1 for x in returned if x.get("decision")=="translate" and x.get("translation_ar")
            )
            result["source_pages"]=11
            result["source_ocr_model"]="gemini-3.5-flash-lite"
            result["source_ocr_regions"]=145
            result["source_candidate_count"]=len(items)
            result["all_attempts"]=[{
                "attempt":r.get("attempt_number"),"http_status":r.get("http_status"),
                "success":r.get("success"),"elapsed_seconds":r.get("elapsed_seconds"),
                "usage":r.get("usage"),"error":r.get("error")
            } for r in attempts]
            FINAL_PATH.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
            print(json.dumps({
                "model":result.get("model"),"success":result.get("success"),
                "http_status":result.get("http_status"),"elapsed_seconds":result.get("elapsed_seconds"),
                "usage":result.get("usage"),"pages":11,"ocr_regions":145,
                "items_sent":len(items),"ids_complete":result["complete_translation_set"],
                "translated_count":result["translated_count"],
                "attempts":len(attempts),"result_path":str(FINAL_PATH)
            },ensure_ascii=False),flush=True)
            return
        if result.get("http_status")==429:
            break
        if result.get("http_status") not in (500,502,503,504) or attempt>=MAX_ATTEMPTS:
            break
        time.sleep(min(120,30*(2**(attempt-1))))
    summary={
        "model":"gemini-3.1-flash-lite","success":False,"source_pages":11,
        "source_ocr_model":"gemini-3.5-flash-lite","source_ocr_regions":145,
        "source_candidate_count":len(items),"requests_sent":len(attempts),
        "attempts":[{k:r.get(k) for k in ("http_status","success","elapsed_seconds","usage","error")}
                    for r in attempts]
    }
    FINAL_PATH.write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"success":False,"attempts":len(attempts),"statuses":[r.get("http_status") for r in attempts],
                      "result_path":str(FINAL_PATH)},ensure_ascii=False),flush=True)

if __name__=="__main__": main()
