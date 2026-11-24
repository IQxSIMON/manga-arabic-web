from __future__ import annotations

import hashlib
import io
import json
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(r"D:\MangaArabic\manga-arabic-web")
sys.path.insert(0, str(ROOT))

from PIL import Image
ARCHIVE = Path(r"C:\Users\BMW1975\Downloads\11.cbz")
OCR_PATH = ROOT / "ocr_test_results/post_tiny_lowercase_filter/11/ocr_results.json"
RAW_PATH = ROOT / "ocr_test_results/gemini_chapter11_run_20261114/chapter11_translations.json"
OUT = ROOT / "ocr_test_results/translated_chapter11_reviewed_20261114"
REVIEWED_PATH = OUT / "chapter11_translations_reviewed.json"
METRICS_PATH = OUT / "render_metrics.json"
CBZ_PATH = OUT / "chapter11_arabic_reviewed.cbz"

# Preserve Gemini's original values; use these human-reviewed alternatives for reading.
CORRECTIONS = {
    "ch11-p001-r004": "أعجوبة سحرية لا تظهر إلا مرة كل قرن، ولن يرى الزمان مثيلاً لها مجدداً.",
    "ch11-p004-r003": "...اجتاحت عالمي بأسره.",
    "ch11-p004-r004": "سيلينا! هل من أحد هناك؟! طبيب! أحضروا طبيباً!!!",
    "ch11-p005-r002": "على الرغم من الرعاية المتواصلة، لم تُظهر حالة انقطاع مسارات الين التسعة أي بوادر للتحسن،",
    "ch11-p007-r001": "الليدي ستارلايت: تلك كلها نباتات ماندريك ناضجة... أليس كذلك؟",
    "ch11-p007-r006": "وايلد غريلز: أتريدين بعضاً منها؟ سأعطيكِ أول نبتة أحصدها مجاناً، وبعدها ستكون بمقابل.",
    "ch11-p008-r001": "الليدي ستارلايت: يمكنك أن تقول ذلك.",
    "ch11-p008-r005": "وايلد غريلز: لن يكون من العدل أن آخذ الإكسير منكِ بلا مقابل، لذا سأرسل لكِ بعض نباتات الماندريك من حين لآخر.",
    "ch11-p008-r007": "الليدي ستارلايت: إذا شُفيتُ، فسأمنحك كل ما أملك من مكانة وثروة.",
    "ch11-p010-r001": "بعضها يعود للاختباء تحت الأرض...",
    "ch11-p014-r001": "لكنني لا أظن أنك قادر على ذلك.",
    "ch11-p014-r002": "...النوبات تتقارب أكثر فأكثر.",
    "ch11-p015-r001": "بهذا المعدل، اليوم أقصى ما أستطيعه...",
    "ch11-p016-r004": "لا بد أنه يرسل أول واحدة اصطادها.",
    "ch11-p017-r002": "الليدي ستارلايت: جمعتها كلها بالفعل؟ كيف يُعقل هذا؟",
    "ch11-p017-r004": "لقد حشوت التراب في أفواهها أيضاً، لذا يُفترض أن تكون بخير.",
    "ch11-p019-r002": "الآن فهمت لماذا يلاحق الشيطان السماوي غريلز بهذه الطريقة...",
    "ch11-p019-r003": "الليدي ستارلايت: شكراً جزيلاً لك... وايلد غريلز.",
    "ch11-p019-r004": "الليدي ستارلايت: سيستغرق تحضير الإكسير بعض الوقت. هل يناسبك ذلك؟",
    "ch11-p020-r006": "بماذا تُراه",
    "ch11-p021-r001": "يفكر الآن؟",
    "ch11-p021-r003": "غريلز، أيمكنني أن أسألك شيئاً؟ ما الأمر؟ لو كنت ستموت غداً، ماذا كنت ستفعل؟",
    "ch11-p022-r002": "...متى بدأ ذلك؟",
    "ch11-p023-r002": "ولأنني ظننت أن الموت قدري على أي حال، استسلمت وذبلت يوماً بعد يوم.",
    "ch11-p026-r001": "أوه... أ-أسرعي، ابدئي الاستخلاص... [سعال]",
    "ch11-p027-r003": "اضربها!",
    "ch11-p027-r004": "م-ماذا؟!",
    "ch11-p027-r005": "اضربيها! إن لم تستطيعي لكمها، فحطّميها على الأرض... [سعال]",
    "ch11-p032-r001": "إنه يحرقني... أشعر وكأن حلقي يشتعل...",
    "ch11-p032-r002": "عليّ أن أُسيّر المانا في جسدي، لكن...",
    "ch11-p033-r002": "هل سأموت هكذا؟!",
    "ch11-p033-r004": "حلماً استطعت فيه أن أمشي على قدميّ.",
    "ch11-p035-r005": "أرجوكِ... ابقي معي!",
}

# The OCR text is too damaged or fragmentary to replace safely. Leave it visible in English.
SKIP_OVERLAY = {
    "ch11-p006-r001", "ch11-p013-r001", "ch11-p015-r002", "ch11-p015-r006",
    "ch11-p017-r005", "ch11-p019-r001", "ch11-p021-r002", "ch11-p028-r001",
    "ch11-p029-r003", "ch11-p029-r006", "ch11-p036-r001",
}
SKIP_REASON = "مصدر OCR غير واضح/ناقص؛ تُرك النص الأصلي ظاهراً بدل تخمين ترجمته."

def save_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)

def main() -> int:
    started = time.perf_counter()
    if not ARCHIVE.is_file():
        raise SystemExit(f"Source chapter not found: {ARCHIVE}")
    ocr = json.loads(OCR_PATH.read_text(encoding="utf-8"))
    raw = json.loads(RAW_PATH.read_text(encoding="utf-8"))
    if ocr.get("pages_expected") != ocr.get("pages_completed") or len(ocr.get("pages", [])) != ocr.get("pages_expected"):
        raise SystemExit("Saved OCR is incomplete; refusing partial rendering.")
    if Path(ocr.get("chapter", "")).name.casefold() != ARCHIVE.name.casefold():
        raise SystemExit("OCR record does not match the source archive.")
    raw_rows = raw.get("translations", [])
    translated = {row["region_id"]: row for row in raw_rows}
    if len(translated) != len(raw_rows) or len(translated) != raw.get("input_count"):
        raise SystemExit("Translation IDs are duplicated or incomplete.")
    if set(CORRECTIONS) - set(translated) or SKIP_OVERLAY - set(translated):
        raise SystemExit("Correction/skip IDs do not match the chapter translation.")

    reviewed_rows = []
    for row in raw_rows:
        item = dict(row)
        rid = item["region_id"]
        if rid in SKIP_OVERLAY:
            item["reviewed_translation_ar"] = None
            item["review_status"] = "source_ocr_unreadable"
            item["review_note"] = SKIP_REASON
        elif rid in CORRECTIONS:
            item["reviewed_translation_ar"] = CORRECTIONS[rid]
            item["review_status"] = "human_post_edit"
            item["review_note"] = "تصحيح معنى/صياغة/إملاء بعد مقارنة النص والترجمة."
        elif item.get("ocr_review_required"):
            item["reviewed_translation_ar"] = item["translation_ar"]
            item["review_status"] = "readable_but_source_ocr_flagged"
            item["review_note"] = "الترجمة مفهومة، لكن يجب مطابقة النص الإنجليزي مع الصورة."
        else:
            item["reviewed_translation_ar"] = item["translation_ar"]
            item["review_status"] = "human_reviewed_acceptable"
            item["review_note"] = None
        reviewed_rows.append(item)

    OUT.mkdir(parents=True, exist_ok=True)
    save_json(REVIEWED_PATH, {"chapter": raw["chapter"], "model": raw["model"],
        "source_ocr": raw["source_ocr"], "input_count": len(reviewed_rows),
        "human_post_edit_count": len(CORRECTIONS), "source_ocr_unreadable_count": len(SKIP_OVERLAY),
        "translations": reviewed_rows})

    from image_pipeline.renderer import render_translated_page

    OUT_PAGES = OUT / "pages"
    OUT_PAGES.mkdir(parents=True, exist_ok=True)
    metrics = {"status": "running", "chapter": raw["chapter"], "source_archive": str(ARCHIVE),
        "source_ocr": str(OCR_PATH), "translation_source": str(RAW_PATH),
        "reviewed_translations": str(REVIEWED_PATH), "source_images_modified": False,
        "pages_expected": ocr["pages_expected"], "pages_completed": 0,
        "regions_available": len(reviewed_rows), "manual_edits": len(CORRECTIONS),
        "ocr_unreadable_left_in_english": len(SKIP_OVERLAY), "pages": []}
    with zipfile.ZipFile(ARCHIVE, "r") as source_zip, zipfile.ZipFile(
            CBZ_PATH, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as target_zip:
        source_names = set(source_zip.namelist())
        expected_ids = set(translated)
        consumed_ids = set()
        for page in ocr["pages"]:
            page_no = int(page["page"])
            member = page["source_member"]
            if member not in source_names:
                raise SystemExit(f"Missing source member on page {page_no}.")
            source_bytes = source_zip.read(member)
            if hashlib.sha256(source_bytes).hexdigest() != page["source_sha256"]:
                raise SystemExit(f"Source hash mismatch on page {page_no}; stopping.")
            rows = sorted(page.get("regions", []), key=lambda r: int(r.get("reading_order", 0)))
            render_regions = []
            skipped_ids = []
            for order, src_row in enumerate(rows, 1):
                rid = f"ch11-p{page_no:03d}-r{order:03d}"
                if rid not in translated:
                    continue
                consumed_ids.add(rid)
                review = next(x for x in reviewed_rows if x["region_id"] == rid)
                text = review.get("reviewed_translation_ar")
                if not text:
                    skipped_ids.append(rid)
                    continue
                region = dict(src_row)
                region["index"] = order
                region["region_id"] = rid
                region["translated"] = text
                render_regions.append(region)

            if render_regions:
                image_bytes, quality = render_translated_page(source_bytes, render_regions)
                rendered_count = quality.get("rendered_regions", 0)
                renderer_skipped = quality.get("skipped_regions", 0)
                renderer_skipped_indices = quality.get("skipped_region_indices", [])
            else:
                with Image.open(io.BytesIO(source_bytes)) as source_image:
                    buffer = io.BytesIO()
                    source_image.convert("RGB").save(buffer, format="PNG", optimize=True)
                    image_bytes = buffer.getvalue()
                rendered_count = 0
                renderer_skipped = 0
                renderer_skipped_indices = []

            filename = f"page_{page_no:03d}.png"
            (OUT_PAGES / filename).write_bytes(image_bytes)
            target_zip.writestr(filename, image_bytes)
            metrics["pages"].append({"page": page_no, "source_member": member,
                "source_sha256": page["source_sha256"], "translated_regions_selected": len(render_regions),
                "rendered_regions": rendered_count, "skipped_by_renderer": renderer_skipped,
                "left_in_english_due_to_source_ocr": skipped_ids,
                "skipped_region_indices": renderer_skipped_indices,
                "output_member": filename})
            metrics["pages_completed"] += 1
            print(json.dumps({"page": page_no, "rendered": rendered_count,
                "renderer_skipped": renderer_skipped, "ocr_left_english": len(skipped_ids)}), flush=True)

    if consumed_ids != expected_ids:
        missing = sorted(expected_ids - consumed_ids)
        raise SystemExit(f"Translation/OCR mapping mismatch; unconsumed IDs: {missing[:10]}")
    metrics["regions_left_english_due_to_source_ocr"] = len(SKIP_OVERLAY)
    metrics["rendered_regions_total"] = sum(p["rendered_regions"] for p in metrics["pages"])
    metrics["renderer_skipped_regions_total"] = sum(p["skipped_by_renderer"] for p in metrics["pages"])
    metrics["elapsed_seconds"] = round(time.perf_counter() - started, 2)
    metrics["status"] = "complete" if metrics["pages_completed"] == metrics["pages_expected"] else "incomplete"
    save_json(METRICS_PATH, metrics)
    print(json.dumps({"status": metrics["status"], "pages": metrics["pages_completed"],
        "regions": len(reviewed_rows), "rendered": metrics["rendered_regions_total"],
        "ocr_left_in_english": len(SKIP_OVERLAY), "renderer_skipped": metrics["renderer_skipped_regions_total"],
        "cbz": str(CBZ_PATH), "reviewed_json": str(REVIEWED_PATH),
        "elapsed_seconds": metrics["elapsed_seconds"]}, ensure_ascii=False))
    return 0 if metrics["status"] == "complete" else 1

if __name__ == "__main__":
    raise SystemExit(main())
