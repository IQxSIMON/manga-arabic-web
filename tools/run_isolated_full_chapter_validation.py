"""Rerun a saved 15-page vertical chapter in an isolated library copy."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SOURCE_LIBRARY = Path(
    os.getenv(
        "MANGA_VALIDATION_SOURCE_LIBRARY",
        str(Path(tempfile.gettempdir()) / "manga-arabic-fullchapter-v24-c96e5fcf6771"),
    )
)
CHAPTER_ID = os.getenv("MANGA_VALIDATION_CHAPTER_ID", "d6024288c79d4787a368749ab4a0879b")
RESUME_LIBRARY = os.getenv("MANGA_VALIDATION_RESUME_LIBRARY", "").strip()


def main() -> int:
    resuming = bool(RESUME_LIBRARY)
    if resuming:
        run_library = Path(RESUME_LIBRARY)
        if not (run_library / "library.sqlite3").is_file():
            print(f"Resume library is missing: {run_library}", file=sys.stderr)
            return 2
    else:
        if not (SOURCE_LIBRARY / "library.sqlite3").is_file():
            print(f"Source validation library is missing: {SOURCE_LIBRARY}", file=sys.stderr)
            return 2
        run_library = Path(tempfile.mkdtemp(prefix="manga-arabic-fullchapter-v25-"))
        shutil.copytree(SOURCE_LIBRARY, run_library, dirs_exist_ok=True)
        database = run_library / "library.sqlite3"
        with sqlite3.connect(database) as connection:
            tables = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            for table in ("translation_memory", "translation_chunk_cache"):
                if table in tables:
                    connection.execute(f'DELETE FROM "{table}"')
            connection.commit()

    os.environ["MANGA_LIBRARY_DIR"] = str(run_library)
    os.environ["MANGA_TRANSLATION_WORKERS"] = "2"
    import app as web_app  # noqa: PLC0415

    chapter = web_app.library_store.get_chapter(CHAPTER_ID)
    if chapter is None:
        print(f"Chapter {CHAPTER_ID} is missing from the copied library.", file=sys.stderr)
        return 3
    pages = sorted(chapter.get("pages", []), key=lambda page: int(page["page_number"]))
    if not pages:
        print("The copied chapter has no pages.", file=sys.stderr)
        return 3

    if resuming:
        web_app.library_store.set_chapter_paused(CHAPTER_ID, False)
    else:
        for page in pages:
            number = int(page["page_number"])
            result = web_app.library_store.read_page_result(CHAPTER_ID, number)
            if not isinstance(result, dict) or not isinstance(result.get("translated_lines"), list):
                print(f"Page {number} has no saved OCR results to reuse.", file=sys.stderr)
                return 4
            for line in result["translated_lines"]:
                if line.get("review_status") != "human_reviewed":
                    line["translated"] = ""
                    line.pop("translation_elapsed_seconds", None)
                    line.pop("translation_attempt_elapsed_seconds", None)
            result["processing_status"] = "translation_in_progress"
            result["translation_elapsed_seconds"] = 0.0
            result["translated"] = ""
            result["translation_inputs_stale"] = False
            result["progress"] = {
                "stage": "translation", "completed_regions": 0,
                "total_regions": len(result["translated_lines"]),
            }
            web_app.library_store.save_page_result(CHAPTER_ID, number, result)

    os.environ["MANGA_TRANSLATION_WORKERS"] = "2"
    client = web_app.app.test_client()
    engine = web_app.CURRENT_TRANSLATION_ENGINE
    checkpoint = run_library / "full_chapter_v25_checkpoint.json"
    run_started = time.perf_counter()
    if resuming and checkpoint.is_file():
        report = json.loads(checkpoint.read_text(encoding="utf-8"))
        report.pop("failed_page", None)
        report.pop("failed_render_page", None)
        report["resumed_at_utc"] = datetime.now(timezone.utc).isoformat()
    else:
        report = {
            "started_at_utc": datetime.now(timezone.utc).isoformat(),
            "source_library": str(SOURCE_LIBRARY),
            "isolated_library": str(run_library),
            "chapter_id": CHAPTER_ID,
            "chapter_title": chapter["title"],
            "page_count": len(pages),
            "engine": engine,
            "translation_workers": 2,
            "pages": [],
        }
    previous_elapsed = float(report.get("elapsed_seconds", 0.0))
    completed_pages = {int(item["page"]) for item in report.get("pages", [])
                       if item.get("processing_status") == "complete"
                       and item.get("render_http") == 200}

    for page in pages:
        number = int(page["page_number"])
        if number in completed_pages and web_app.library_store.read_rendered_page(CHAPTER_ID, number):
            continue
        page_started = time.perf_counter()
        translated = client.post(f"/api/library/{CHAPTER_ID}/pages/{number}/translate")
        translation_data = translated.get_json(silent=True) or {}
        if translated.status_code != 200 or translation_data.get("processing_status") != "complete":
            report["failed_page"] = {
                "page": number,
                "http_status": translated.status_code,
                "response": translation_data,
            }
            report["elapsed_seconds"] = round(previous_elapsed + time.perf_counter() - run_started, 2)
            checkpoint.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(report["failed_page"], ensure_ascii=True), flush=True)
            return 5

        rendered = client.post(f"/api/library/{CHAPTER_ID}/pages/{number}/render")
        render_data = rendered.get_json(silent=True) or {}
        lines = translation_data.get("translated_lines", [])
        completed = sum(web_app._region_is_complete(line) for line in lines)
        record = {
            "page": number,
            "translation_http": translated.status_code,
            "render_http": rendered.status_code,
            "wall_seconds": round(time.perf_counter() - page_started, 2),
            "translation_seconds": translation_data.get("translation_elapsed_seconds"),
            "ocr_seconds": translation_data.get("ocr_elapsed_seconds"),
            "regions": len(lines),
            "completed_regions": completed,
            "rendered_regions": render_data.get("rendered_regions"),
            "skipped_regions": render_data.get("skipped_regions"),
            "processing_status": translation_data.get("processing_status"),
        }
        report["pages"].append(record)
        report["elapsed_seconds"] = round(previous_elapsed + time.perf_counter() - run_started, 2)
        checkpoint.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(record, ensure_ascii=True), flush=True)
        if rendered.status_code != 200:
            report["failed_render_page"] = number
            checkpoint.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            return 6

    exported = client.get(
        f"/api/library/{CHAPTER_ID}/export?format=cbz&edition=rendered"
    )
    if exported.status_code != 200:
        print(f"CBZ export failed with HTTP {exported.status_code}.", file=sys.stderr)
        return 7
    cbz_path = run_library / "full_chapter_v25_rendered.cbz"
    cbz_path.write_bytes(exported.data)
    with zipfile.ZipFile(cbz_path) as archive:
        bad_member = archive.testzip()
        names = archive.namelist()
    report["export"] = {
        "http_status": exported.status_code,
        "path": str(cbz_path),
        "bytes": cbz_path.stat().st_size,
        "member_count": len(names),
        "bad_member": bad_member,
        "has_comic_info": any(name.lower().endswith("comicinfo.xml") for name in names),
    }
    report["elapsed_seconds"] = round(previous_elapsed + time.perf_counter() - run_started, 2)
    report["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
    checkpoint.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"complete": True, "elapsed_seconds": report["elapsed_seconds"],
                      "export": report["export"], "checkpoint": str(checkpoint)},
                     ensure_ascii=True), flush=True)
    return 0 if bad_member is None else 8


if __name__ == "__main__":
    raise SystemExit(main())
