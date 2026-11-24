from __future__ import annotations
import importlib.util, json
from pathlib import Path

ROOT = Path(r"D:\MangaArabic\manga-arabic-web")
OUT = ROOT / "ocr_test_results" / "web_chapter_teto_x_egen_ch09"
MODULE_PATH = OUT / "translate_chapter_once_per_model.py"
OCR_PATH = OUT / "ocr_results.json"
RESULT_PATH = OUT / "gemini31_local_ocr_translation_current_trial.json"
spec = importlib.util.spec_from_file_location("chapter9_saved_translation", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

ocr = json.loads(OCR_PATH.read_text(encoding="utf-8"))
if ocr.get("pages_expected") != 11 or ocr.get("pages_completed") != 11:
    raise RuntimeError("Saved OCR is not a complete 11-page chapter.")
items, preexcluded = module.collect_items(ocr)
if not items:
    raise RuntimeError("No translation candidates were found.")
key = module.load_api_key()
result = module.request_model("gemini-3.1-flash-lite", key, items)
if result.get("response_json"):
    got = result["response_json"].get("items", [])
    sent_ids = [item["id"] for item in items]
    got_ids = [str(item.get("id", "")) for item in got]
    result["id_validation"] = {
        "sent": len(sent_ids),
        "received": len(got_ids),
        "missing_ids": sorted(set(sent_ids) - set(got_ids)),
        "duplicate_ids": sorted({x for x in got_ids if got_ids.count(x) > 1}),
        "unknown_ids": sorted(set(got_ids) - set(sent_ids)),
    }
    result["complete_translation_set"] = (
        len(sent_ids) == len(got_ids)
        and not result["id_validation"]["missing_ids"]
        and not result["id_validation"]["duplicate_ids"]
        and not result["id_validation"]["unknown_ids"]
    )
    result["translated_count"] = sum(
        1 for item in got
        if item.get("decision") == "translate" and item.get("translation_ar")
    )
    result["excluded_by_model_count"] = sum(1 for item in got if item.get("decision") == "exclude")
result["source_pages"] = 11
result["source_ocr_regions"] = sum(len(page.get("regions", [])) for page in ocr["pages"])
result["source_candidate_count"] = len(items)
result["preexcluded_count"] = len(preexcluded)
result["request_retry_count"] = 0
RESULT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({
    "model": result.get("model"),
    "success": result.get("success"),
    "http_status": result.get("http_status"),
    "elapsed_seconds": result.get("elapsed_seconds"),
    "usage": result.get("usage"),
    "pages": result["source_pages"],
    "ocr_regions": result["source_ocr_regions"],
    "candidate_count": result["source_candidate_count"],
    "preexcluded_count": result["preexcluded_count"],
    "complete_translation_set": result.get("complete_translation_set"),
    "translated_count": result.get("translated_count"),
    "result_path": str(RESULT_PATH),
}, ensure_ascii=False), flush=True)
