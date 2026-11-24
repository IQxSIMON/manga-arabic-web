"""Flag translation output for human review without contacting any model API.

This is a quality-control aid, not a semantic accuracy score. It checks ID
integrity, blank/English-heavy Arabic fields, suspicious script contamination,
known Arabic typos, glossary variants, and upstream OCR review flags.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "ocr_test_results/gemini_chapter11_run_20261114/chapter11_translations.json"
DEFAULT_OUTPUT = ROOT / "ocr_test_results/gemini_chapter11_run_20261114/translation_quality_flags.json"

KNOWN_ISSUES = {
    "توتجد": "likely Arabic spelling corruption",
    "الشيطات السماوي": "proper-name spelling; expected الشيطان السماوي",
    "ستتموت": "Arabic conjugation typo",
    "أعرّ": "likely typo for أشعر",
    "لاببد": "duplicated letter",
    "ابتלعت": "Hebrew character mixed into Arabic",
    "اپرِبحها": "Persian/Urdu letters mixed into Arabic",
}
GLOSSARY_VARIANTS = {
    "mandrake": ("ماندرايك", "ماندراكت", "ماندريك", "الماندرايك"),
}
FOREIGN_SCRIPT_RANGES = (
    (0x0370, 0x03FF, "Greek"),
    (0x0400, 0x052F, "Cyrillic"),
    (0x0590, 0x05FF, "Hebrew"),
    (0x0900, 0x097F, "Devanagari"),
    (0x10A0, 0x10FF, "Georgian"),
    (0x3040, 0x30FF, "Japanese kana"),
    (0x3400, 0x9FFF, "CJK"),
)
PERSIAN_LETTERS = set("پچژگکی")


def script_name(char: str) -> str | None:
    code = ord(char)
    for low, high, name in FOREIGN_SCRIPT_RANGES:
        if low <= code <= high:
            return name
    if char in PERSIAN_LETTERS:
        return "Persian/Urdu letter"
    return None


def audit(input_path: Path) -> dict:
    data = json.loads(input_path.read_text(encoding="utf-8"))
    rows = data.get("translations", [])
    ids = [str(row.get("region_id", "")) for row in rows]
    issues: list[dict] = []

    def add(rid: str, code: str, detail: str) -> None:
        issues.append({"region_id": rid, "code": code, "detail": detail})

    duplicates = [rid for rid, count in Counter(ids).items() if rid and count > 1]
    for rid in duplicates:
        add(rid, "duplicate_region_id", "region_id appears more than once")

    for row in rows:
        rid = str(row.get("region_id", ""))
        text = str(row.get("reviewed_translation_ar") or row.get("translation_ar") or "").strip()
        source = str(row.get("source_text", ""))
        if not text:
            add(rid, "empty_translation", "translation is empty")
        else:
            foreign = sorted({name for char in text if (name := script_name(char))})
            if foreign:
                add(rid, "script_contamination", ", ".join(foreign))
            if not re.search(r"[\u0600-\u06ff\u0750-\u077f\u08a0-\u08ff]", text):
                add(rid, "no_arabic_script", "translation contains no Arabic-script character")
            latin_words = re.findall(r"[A-Za-z]{3,}", text)
            if len(latin_words) >= 3 and len(latin_words) > max(2, len(text.split()) // 2):
                add(rid, "english_heavy_output", "Arabic field contains many Latin words; confirm brands/URLs or untranslated text")
            for phrase, explanation in KNOWN_ISSUES.items():
                if phrase in text:
                    add(rid, "known_issue", f"{phrase}: {explanation}")
            for term, variants in GLOSSARY_VARIANTS.items():
                hits = [variant for variant in variants if variant in text]
                if hits:
                    add(rid, "glossary_variant", f"{term}: {', '.join(hits)}")
        if row.get("ocr_review_required"):
            add(rid, "source_ocr_review", "source text was already marked low-confidence; check the image before judging translation")
        if source and len(re.findall(r"[A-Za-z]", source)) >= 12 and re.search(r"[^A-Za-z0-9\s.,!?;:'\"()\[\]{}%&/@#_+-]", source):
            add(rid, "source_text_mixed_or_noisy", "English OCR includes unusual characters; verify against the image")

    return {
        "chapter": data.get("chapter"),
        "input_count": data.get("input_count", len(rows)),
        "rows_audited": len(rows),
        "unique_region_ids": len(set(ids)),
        "duplicate_region_ids": duplicates,
        "ocr_review_required_count": sum(bool(row.get("ocr_review_required")) for row in rows),
        "quality_flags_count": len(issues),
        "semantic_accuracy_score": None,
        "score_note": "No human gold reference is supplied; automated flags are review cues, not an accuracy score.",
        "issues": issues,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = audit(args.input)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temp_path = args.output.with_suffix(args.output.suffix + ".tmp")
    temp_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(args.output)
    print(json.dumps({key: result[key] for key in (
        "chapter", "rows_audited", "unique_region_ids", "ocr_review_required_count", "quality_flags_count"
    )}, ensure_ascii=False))
    return 0 if not result["duplicate_region_ids"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
