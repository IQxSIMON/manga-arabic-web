import json
from pathlib import Path

ROOT = Path(r"D:\MangaArabic\manga-arabic-web")
PATH = ROOT / "ocr_test_results/gemini_chapter11_run_20261114/chapter11_translations.json"
OUT = PATH.with_name("quality_pairs.txt")
data = json.loads(PATH.read_text(encoding="utf-8"))
lines = []
for row in data["translations"]:
    flag = "OCR?" if row.get("ocr_review_required") else "OCR ok"
    lines.extend([f'{row["region_id"]} | p{row["page"]:02d} | {flag} | {row["classification"]}',
                  "  EN: " + row["source_text"], "  AR: " + row["translation_ar"], ""])
OUT.write_text("\n".join(lines), encoding="utf-8")
print(f"pairs={len(data['translations'])}; output={OUT}")
