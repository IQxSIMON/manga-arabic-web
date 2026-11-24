from __future__ import annotations
import json, os, re, time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

ROOT=Path(r"D:\MangaArabic\manga-arabic-web")
SRC=ROOT/"ocr_test_results/gemini_chapter12_trial_20261114"
OUT=ROOT/"ocr_test_results/gemini_chapter12_gemini31_comparison_20261114"
MODEL="gemini-3.1-flash-lite"
ENDPOINT=f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent?alt=json"

def utc():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")
def save(path,obj):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+".tmp")
    tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding="utf-8")
    os.replace(tmp,path)
def key_from_local():
    k=os.environ.get("GEMINI_API_KEY","").strip()
    if not k:
        for line in (ROOT/".env").read_text(encoding="utf-8-sig").splitlines():
            if line.strip().startswith("GEMINI_API_KEY="):
                k=line.split("=",1)[1].strip().strip('"').strip("'")
                break
    return k
def main():
    metrics_path=OUT/"request_metrics.json"
    if metrics_path.exists() and json.loads(metrics_path.read_text(encoding="utf-8")).get("request_sent"):
        raise RuntimeError("A Gemini 3.1 request is already recorded; refusing to send it again.")
    OUT.mkdir(parents=True,exist_ok=True)
    oldm=json.loads((SRC/"request_metrics.json").read_text(encoding="utf-8"))
    payload=json.loads((SRC/"translation_payload.json").read_text(encoding="utf-8"))
    old=json.loads((SRC/"chapter12_translations.json").read_text(encoding="utf-8"))
    omitted=set(oldm["rendering"]["omitted_region_ids"])
    oldmap={x["region_id"]:x for x in old["translations"]}
    selected=[x for x in payload["regions"] if x["region_id"] not in omitted and x["region_id"] in oldmap]
    if len(selected)!=134:
        raise RuntimeError(f"Expected the exact 134 preview regions, got {len(selected)}.")
    # Keep exactly the same source texts and IDs as the chapter 12 3.5 comparison set.
    if any(x.get("source_text")!=oldmap[x["region_id"]].get("source_text") for x in selected):
        raise RuntimeError("Source text mismatch against baseline.")
    key=key_from_local()
    if not re.fullmatch(r"(?:AIza[A-Za-z0-9_-]{25,55}|AQ\.[A-Za-z0-9._-]{40,80})",key):
        raise RuntimeError("Local API key is absent or invalid; no request sent.")
    prompt=(
      "Translate the following selected story-relevant English text regions from one vertical webtoon chapter into natural, fluent Modern Standard Arabic. "
      "Translate dialogue, narration, and external text only when it carries story content. The supplied set has already excluded sound effects, site branding, URLs, promos, UI labels, and obvious OCR noise. "
      "Preserve meaning, tone, character relationships, names consistently, and the region order. Avoid literal calques and do not invent words where OCR is unclear. "
      "Return every region_id exactly once as JSON translations[{region_id,translation_ar,classification}], with classification dialogue, narration, or external_text. No commentary.\n\n"
      +json.dumps(selected,ensure_ascii=False,separators=(",",":"))
    )
    body={"contents":[{"role":"user","parts":[{"text":prompt}]}],
      "generationConfig":{"temperature":0.2,"maxOutputTokens":16384,"responseMimeType":"application/json",
       "responseJsonSchema":{"type":"object","properties":{"translations":{"type":"array","items":{"type":"object","properties":{
        "region_id":{"type":"string"},"translation_ar":{"type":"string"},
        "classification":{"type":"string","enum":["dialogue","narration","external_text"]}},
        "required":["region_id","translation_ar","classification"],"additionalProperties":False}}},"required":["translations"],"additionalProperties":False}}}
    req=Request(ENDPOINT,data=json.dumps(body,ensure_ascii=False).encode("utf-8"),
      headers={"Content-Type":"application/json","Accept":"application/json","x-goog-api-key":key},method="POST")
    metrics={"chapter":"12.cbz","model":MODEL,"baseline_model":"gemini-3.5-flash-lite",
      "comparison_set":"exact same 134 regions used in chapter 12 reading preview",
      "excluded_regions_count":len(omitted),"sent_regions_count":len(selected),
      "sent_region_ids":[x["region_id"] for x in selected],"free_tier_requested":True,
      "billing_enabled_by_this_run":False,"api_key_value_logged":False,
      "request_sent":True,"request_started_at_utc":utc(),"status":"sent_waiting"}
    save(metrics_path,metrics)
    start=time.perf_counter()
    try:
      with urlopen(req,timeout=180) as resp:
        status=resp.status
        data=json.loads(resp.read().decode("utf-8"))
      metrics.update(http_status=status,server_accepted=True,status="http_success")
    except HTTPError as e:
      raw=e.read().decode("utf-8",errors="replace")
      try: data=json.loads(raw)
      except Exception: data={}
      metrics.update(http_status=e.code,server_accepted=False,status="http_error",
        error=(data.get("error") or {}).get("message",raw[:1000]),
        quota_error=(data.get("error") or {}).get("message") if e.code==429 else None)
      metrics["latency_ms"]=round((time.perf_counter()-start)*1000,2)
      metrics["request_completed_at_utc"]=utc();save(metrics_path,metrics);raise
    except (URLError,TimeoutError,OSError) as e:
      metrics.update(server_accepted=None,status="transport_error",error=f"{type(e).__name__}: {e}")
      metrics["latency_ms"]=round((time.perf_counter()-start)*1000,2)
      metrics["request_completed_at_utc"]=utc();save(metrics_path,metrics);raise
    metrics["latency_ms"]=round((time.perf_counter()-start)*1000,2)
    metrics["request_completed_at_utc"]=utc()
    usage=data.get("usageMetadata",{})
    metrics["input_tokens"]=usage.get("promptTokenCount")
    metrics["output_tokens"]=usage.get("candidatesTokenCount")
    metrics["thinking_tokens"]=usage.get("thoughtsTokenCount")
    metrics["total_tokens"]=usage.get("totalTokenCount")
    metrics["finish_reason"]=(data.get("candidates") or [{}])[0].get("finishReason")
    text="".join(p.get("text","") for p in data["candidates"][0]["content"]["parts"]).strip()
    items=json.loads(text)["translations"]
    expected=[r["region_id"] for r in selected]
    received=[str(x.get("region_id","")) for x in items]
    metrics["missing_ids"]=[i for i in expected if i not in set(received)]
    metrics["unexpected_ids"]=[i for i in received if i not in set(expected)]
    metrics["duplicate_ids"]=sorted({i for i in received if received.count(i)>1})
    if metrics["missing_ids"] or metrics["unexpected_ids"] or metrics["duplicate_ids"]:
      metrics["status"]="response_id_integrity_error"
    else: metrics["status"]="success"
    newmap={str(x["region_id"]):x for x in items if x.get("region_id") in set(expected)}
    result=[]
    for row in selected:
      rid=row["region_id"]; a=oldmap[rid]; b=newmap.get(rid,{})
      result.append({**row,"gemini_3_5_translation":a.get("translation_ar"),
       "gemini_3_1_translation":b.get("translation_ar"),
       "gemini_3_1_classification":b.get("classification"),
       "exact_match":a.get("translation_ar")==b.get("translation_ar")})
    metrics["translated_ids_count"]=len(newmap)
    metrics["exact_match_count"]=sum(r["exact_match"] for r in result)
    metrics["different_translation_count"]=sum(not r["exact_match"] for r in result)
    save(OUT/"gemini31_translations.json",{"chapter":"12.cbz","model":MODEL,"translations":result})
    save(OUT/"request_metrics.json",metrics)
    save(OUT/"comparison.json",{"chapter":"12.cbz","models":["gemini-3.5-flash-lite",MODEL],
      "same_source_region_ids":expected,"count":len(expected),"exact_match_count":metrics["exact_match_count"],
      "different_translation_count":metrics["different_translation_count"],"rows":result})
    print(json.dumps({"status":metrics["status"],"model":MODEL,"sent":len(selected),
     "received":len(newmap),"missing":len(metrics["missing_ids"]),
     "latency_ms":metrics["latency_ms"],"input_tokens":metrics["input_tokens"],
     "output_tokens":metrics["output_tokens"],"thinking_tokens":metrics["thinking_tokens"],
     "total_tokens":metrics["total_tokens"],"exact_matches":metrics["exact_match_count"],
     "different":metrics["different_translation_count"],"output_dir":str(OUT)},ensure_ascii=True))
if __name__=="__main__": main()
