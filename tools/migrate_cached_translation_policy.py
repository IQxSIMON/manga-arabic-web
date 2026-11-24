"""Safely advance validated cached page results to the active output policy.

This migration changes recipe metadata only. It refuses to touch pages whose
saved text fails the active Index output integrity checks.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", required=True, type=Path)
    parser.add_argument("--pages", required=True,
                        help="comma-separated page numbers to validate and migrate")
    args = parser.parse_args()
    checkpoint = args.checkpoint.resolve()
    if not checkpoint.is_file():
        parser.error(f"checkpoint not found: {checkpoint}")
    try:
        numbers = sorted({int(value.strip()) for value in args.pages.split(",")
                          if value.strip()})
    except ValueError:
        parser.error("--pages must contain comma-separated integers")
    if not numbers:
        parser.error("at least one page is required")

    summary = json.loads(checkpoint.read_text(encoding="utf-8"))
    library = Path(summary["test_library"]).resolve()
    os.environ["MANGA_LIBRARY_DIR"] = str(library)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import library_store
    from app import PAGE_PIPELINE_VERSION, PAGE_TRANSLATION_CONTEXT_VERSION, active_translation_recipe_signature
    from translator_engine.adapters import (
        GENERATION_POLICY_VERSION,
        INDEX_PROMPT_VERSION,
        _has_latin_residue,
        _is_implausibly_expanded_translation,
    )

    chapter_id = summary["chapter_id"]
    chapter = library_store.get_chapter(chapter_id)
    valid_numbers = {int(page["page_number"]) for page in chapter["pages"]}
    if not set(numbers) <= valid_numbers:
        parser.error(f"pages must be drawn from {sorted(valid_numbers)}")
    signature = active_translation_recipe_signature()
    results = {}
    for number in numbers:
        result = library_store.read_page_result(chapter_id, number)
        if not result or result.get("processing_status") != "complete":
            parser.error(f"page {number} is not complete")
        lines = result.get("translated_lines", [])
        if not result.get("no_text_detected") and not lines:
            parser.error(f"page {number} has no saved translations")
        if any(
            line.get("include", True) is not False and (
                not str(line.get("text", "")).strip()
                or not str(line.get("translated", "")).strip()
                or _is_implausibly_expanded_translation(line.get("text", ""), line.get("translated", ""))
                or _has_latin_residue(line.get("translated", ""))
            )
            for line in lines
        ):
            parser.error(f"page {number} failed output integrity validation")
        results[number] = result

    for number, result in results.items():
        result["translation_engine_signature"] = signature
        library_store.save_page_result(chapter_id, number, result)
    migrated = set(numbers)
    for page in summary.get("pages", []):
        if int(page.get("page", -1)) in migrated:
            page.update({
                "pipeline_version": PAGE_PIPELINE_VERSION,
                "prompt_version": INDEX_PROMPT_VERSION,
                "context_version": PAGE_TRANSLATION_CONTEXT_VERSION,
                "generation_policy_version": GENERATION_POLICY_VERSION,
            })
    summary.update({
        "pipeline_version_on_resume": PAGE_PIPELINE_VERSION,
        "prompt_version_on_resume": INDEX_PROMPT_VERSION,
        "context_version_on_resume": PAGE_TRANSLATION_CONTEXT_VERSION,
        "generation_policy_version_on_resume": GENERATION_POLICY_VERSION,
    })
    temp = checkpoint.with_suffix(checkpoint.suffix + ".tmp")
    temp.write_text(json.dumps(summary, ensure_ascii=True, indent=2), encoding="utf-8")
    temp.replace(checkpoint)
    print(json.dumps({"migrated_validated_pages": numbers}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
