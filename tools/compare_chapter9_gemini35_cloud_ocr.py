import difflib,json,re
from pathlib import Path

ROOT=Path(r"D:\MangaArabic\manga-arabic-web\ocr_test_results\web_chapter_teto_x_egen_ch09")
local=json.loads((ROOT/"ocr_results.json").read_text(encoding="utf-8"))
cloud=json.loads((ROOT/"gemini35_cloud_ocr_batched_result.json").read_text(encoding="utf-8"))
assert cloud.get("complete") and cloud.get("regions_expected")==145
local_map={}
for page in local["pages"]:
    rows=sorted(page.get("regions",[]),key=lambda r:(float(r.get("top",0)),float(r.get("left",0))))
    for n,row in enumerate(rows,1):
        local_map[f"p{int(page['page']):02d}-r{n:03d}"]=str(row.get("text","")).strip()
def norm(t): return re.sub(r"\s+"," ",str(t or "")).strip().casefold()
comparisons=[]
for row in cloud["regions"]:
    rid=row["region_id"]
    a=local_map.get(rid,"")
    b=str(row.get("cloud_text","")).strip()
    ratio=difflib.SequenceMatcher(None,norm(a),norm(b)).ratio()
    comparisons.append({"id":rid,"local":a,"cloud":b,"similarity":round(ratio,4),
                        "normalized_exact":norm(a)==norm(b),
                        "category":row.get("content_category","uncertain")})
exact=sum(x["normalized_exact"] for x in comparisons)
near=sum(x["similarity"]>=.90 for x in comparisons)
empty=sum(not x["cloud"] for x in comparisons)
mismatches=[x for x in comparisons if not x["normalized_exact"]]
mismatches.sort(key=lambda x:x["similarity"])
result={
 "regions_compared":len(comparisons),"ids_missing_from_local":sum(x["id"] not in local_map for x in comparisons),
 "normalized_exact_matches":exact,"normalized_exact_percent":round(exact/max(1,len(comparisons))*100,2),
 "similarity_at_least_90_count":near,"empty_cloud_text_count":empty,
 "category_counts":{},
 "disagreements":mismatches[:35],
 "caveat":"Agreement with the previous local OCR is not human ground truth and is not an accuracy score."
}
for x in comparisons:
    result["category_counts"][x["category"]]=result["category_counts"].get(x["category"],0)+1
(ROOT/"gemini35_cloud_vs_local_ocr_comparison.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps({k:v for k,v in result.items() if k!="disagreements"},ensure_ascii=False))
