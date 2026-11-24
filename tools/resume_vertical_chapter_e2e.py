"""Resume a saved vertical-chapter E2E run without replacing its history.

Usage (PowerShell):
  $env:MANGA_LIBRARY_DIR = '<test_library from checkpoint>'
  .venv/Scripts/python.exe tools/resume_vertical_chapter_e2e.py `
    --checkpoint "$env:TEMP\full_chapter_v19_checkpoint.json"

The runner keeps the isolated library and original checkpoint. Every checkpoint
update is atomic; pages already marked translated and rendered are not run again.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def save_checkpoint(path: Path, summary: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f"{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(summary, handle, ensure_ascii=True, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def saved_page_progress(path: Path) -> dict | None:
    """Read the latest atomic page-result progress without touching SQLite."""
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
        progress = result.get("progress") or {}
        return {
            "stage": result.get("processing_status") or progress.get("stage"),
            "completed_regions": progress.get("completed_regions"),
            "total_regions": progress.get("total_regions"),
            "current_region": progress.get("current_region"),
            "translation_elapsed_seconds": result.get("translation_elapsed_seconds"),
        }
    except (OSError, json.JSONDecodeError, AttributeError):
        return None


def saved_page_state(path: Path) -> dict | None:
    """Read durable page freshness flags for correct ordered checkpoint resume."""
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
        return {
            "processing_status": result.get("processing_status"),
            "pipeline_version": result.get("pipeline_version"),
            "translation_inputs_stale": bool(result.get("translation_inputs_stale")),
            "render_stale": bool(result.get("render_stale")),
        }
    except (OSError, json.JSONDecodeError, AttributeError):
        return None


def page_matches_current_recipe(page: dict, pipeline: int, prompt: str,
                                context: int, generation_policy: str) -> bool:
    return (
        page.get("processing_status") == "complete"
        and page.get("translation_http") == 200
        and page.get("render_http") == 200
        and page.get("pipeline_version") == pipeline
        and page.get("prompt_version") == prompt
        and page.get("context_version") == context
        and page.get("generation_policy_version") == generation_policy
    )


def failed_page_item(old: dict, page_number: int, status_code: int,
                     elapsed_seconds: float, durable_result: dict | None,
                     error: dict | str, pipeline: int, prompt: str,
                     context: int, generation_policy: str) -> dict:
    """Never carry a previous successful status into a failed page attempt."""
    result = durable_result if isinstance(durable_result, dict) else {}
    progress = result.get("progress") if isinstance(result.get("progress"), dict) else {}
    item = {**old, "page": page_number, "translation_http": status_code,
            "elapsed_seconds": elapsed_seconds}
    for stale_field in ("render_http", "rendered_regions", "skipped_regions", "render_error"):
        item.pop(stale_field, None)
    item.update({
        "processing_status": result.get("processing_status") or "failed",
        "regions": len(result.get("translated_lines", [])),
        "completed_regions": progress.get("completed_regions"),
        "ocr_seconds": result.get("ocr_elapsed_seconds"),
        "translation_seconds": result.get("translation_elapsed_seconds"),
        "pipeline_version": result.get("pipeline_version", pipeline),
        "prompt_version": prompt,
        "context_version": context,
        "generation_policy_version": generation_policy,
        "error": error,
    })
    return item


def apply_completion_metadata(summary: dict, pages: dict, expected_page_count: int,
                              export: dict, pipeline: int, prompt: str,
                              context: int, generation_policy: str) -> None:
    """Record recipe versions even when rendering leaves reviewable warnings."""
    pages_verified = (
        len(pages) == expected_page_count
        and all(p.get("processing_status") == "complete" for p in pages.values())
        and all(p.get("pipeline_version") == pipeline for p in pages.values())
        and all(p.get("prompt_version") == prompt
                and p.get("context_version") == context
                and p.get("generation_policy_version") == generation_policy
                for p in pages.values())
        and all(p.get("render_http") == 200 and p.get("skipped_regions", 0) == 0
                for p in pages.values())
    )
    export_verified = (
        export.get("image_count") == expected_page_count and export.get("comic_info") is True
    )
    summary.update({
        "status": "complete" if pages_verified and export_verified else "complete_with_warnings",
        "pipeline_version": pipeline,
        "prompt_version": prompt,
        "context_version": context,
        "generation_policy_version": generation_policy,
        "pages_verified": pages_verified,
        "export_verified": export_verified,
        "review_pages": [
            {"page": number, "skipped_regions": page.get("skipped_regions", 0)}
            for number, page in sorted(pages.items())
            if page.get("render_http") != 200 or page.get("skipped_regions", 0) != 0
        ],
    })


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", required=True, type=Path)
    parser.add_argument("--library-dir", type=Path,
                        help="explicit isolated library from the checkpoint")
    parser.add_argument("--pages", type=str,
                        help="optional comma-separated page numbers to reprocess")
    args = parser.parse_args()
    checkpoint_path = args.checkpoint.resolve()
    if not checkpoint_path.is_file():
        parser.error(f"checkpoint not found: {checkpoint_path}")
    summary = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    library_path = Path(summary["test_library"]).resolve()
    if not library_path.is_dir() or not (library_path / "library.sqlite3").is_file():
        parser.error(f"isolated test library is missing: {library_path}")
    configured_library = (
        str(args.library_dir.resolve()) if args.library_dir
        else os.environ.get("MANGA_LIBRARY_DIR")
    )
    if configured_library and Path(configured_library).resolve() != library_path:
        parser.error("MANGA_LIBRARY_DIR must point to the test_library from this checkpoint")
    os.environ["MANGA_LIBRARY_DIR"] = str(library_path)
    project_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project_root))

    import library_store
    from app import app, PAGE_PIPELINE_VERSION, PAGE_TRANSLATION_CONTEXT_VERSION
    from translator_engine.adapters import GENERATION_POLICY_VERSION, INDEX_PROMPT_VERSION

    chapter_id = summary["chapter_id"]
    chapter = library_store.get_chapter(chapter_id)
    if not chapter:
        parser.error("chapter is missing from the isolated test library")
    pages = {int(page["page"]): page for page in summary.get("pages", [])}
    selected_pages = None
    if args.pages:
        try:
            selected_pages = {int(value.strip()) for value in args.pages.split(",")
                              if value.strip()}
        except ValueError:
            parser.error("--pages must contain comma-separated page numbers")
        valid_numbers = {int(page["page_number"]) for page in chapter["pages"]}
        if not selected_pages or not selected_pages <= valid_numbers:
            parser.error(f"--pages must be drawn from chapter pages {sorted(valid_numbers)}")
    app.config["TESTING"] = True
    client = app.test_client()

    for stored_page in chapter["pages"]:
        number = int(stored_page["page_number"])
        if selected_pages is not None and number not in selected_pages:
            continue
        old = pages.get(number, {})
        page_result_path = library_path / chapter_id / "results" / f"page-{number:04d}.json"
        durable_state = saved_page_state(page_result_path)
        if page_matches_current_recipe(
            old, PAGE_PIPELINE_VERSION, INDEX_PROMPT_VERSION,
            PAGE_TRANSLATION_CONTEXT_VERSION, GENERATION_POLICY_VERSION,
        ) and durable_state and durable_state["pipeline_version"] == PAGE_PIPELINE_VERSION \
                and durable_state["processing_status"] == "complete" \
                and not durable_state["translation_inputs_stale"] \
                and not durable_state["render_stale"]:
            old.pop("error", None)
            old.pop("render_error", None)
            pages[number] = old
            continue

        started = time.perf_counter()
        last_progress = None
        last_heartbeat = time.monotonic()
        future_done = threading.Event()

        def translate_page():
            try:
                with app.test_client() as page_client:
                    return page_client.post(f"/api/library/{chapter_id}/pages/{number}/translate")
            finally:
                future_done.set()

        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="chapter-e2e") as executor:
            future = executor.submit(translate_page)
            while not future_done.wait(3):
                live = saved_page_progress(page_result_path)
                now = time.monotonic()
                if live and live != last_progress:
                    last_progress = live
                    summary["live_progress"] = {"page": number, **live}
                    save_checkpoint(checkpoint_path, summary)
                    print(json.dumps({"live_progress": summary["live_progress"]},
                                     ensure_ascii=True), flush=True)
                    last_heartbeat = now
                elif now - last_heartbeat >= 60:
                    heartbeat = {
                        "page": number,
                        "wall_elapsed_seconds": round(now - started, 1),
                        **(live or {"stage": "starting"}),
                    }
                    summary["live_progress"] = heartbeat
                    save_checkpoint(checkpoint_path, summary)
                    print(json.dumps({"heartbeat": heartbeat}, ensure_ascii=True), flush=True)
                    last_heartbeat = now
            response = future.result()
        summary.pop("live_progress", None)
        item = {**old, "page": number, "translation_http": response.status_code,
                "elapsed_seconds": round(time.perf_counter() - started, 2),
                "pipeline_version": PAGE_PIPELINE_VERSION}
        if response.status_code != 200:
            try:
                durable_result = json.loads(page_result_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                durable_result = None
            item = failed_page_item(
                old, number, response.status_code,
                round(time.perf_counter() - started, 2), durable_result,
                response.get_json(silent=True) or response.get_data(as_text=True)[:1000],
                PAGE_PIPELINE_VERSION, INDEX_PROMPT_VERSION,
                PAGE_TRANSLATION_CONTEXT_VERSION, GENERATION_POLICY_VERSION,
            )
            pages[number] = item
            summary.update({"status": "failed", "pipeline_version_on_resume": PAGE_PIPELINE_VERSION,
                            "prompt_version_on_resume": INDEX_PROMPT_VERSION,
                            "context_version_on_resume": PAGE_TRANSLATION_CONTEXT_VERSION,
                            "generation_policy_version_on_resume": GENERATION_POLICY_VERSION,
                            "pages": [pages[k] for k in sorted(pages)]})
            save_checkpoint(checkpoint_path, summary)
            print(json.dumps({"page_result": item, "status": "failed"}, ensure_ascii=True), flush=True)
            return 1

        item.pop("error", None)
        result = response.get_json() or {}
        item.update({"processing_status": result.get("processing_status"),
                     "regions": len(result.get("translated_lines", [])),
                     "completed_regions": (result.get("progress") or {}).get("completed_regions"),
                     "ocr_seconds": result.get("ocr_elapsed_seconds"),
                      "ocr_timing_breakdown": result.get("ocr_timing_breakdown"),
                     "translation_seconds": result.get("translation_elapsed_seconds"),
                     "pipeline_version": result.get("pipeline_version"),
                     "prompt_version": INDEX_PROMPT_VERSION,
                     "context_version": PAGE_TRANSLATION_CONTEXT_VERSION,
                     "generation_policy_version": GENERATION_POLICY_VERSION})
        if result.get("processing_status") != "complete":
            item["error"] = result.get("last_translation_error") or result.get("progress")
            pages[number] = item
            summary.update({"status": "incomplete", "pipeline_version_on_resume": PAGE_PIPELINE_VERSION,
                            "prompt_version_on_resume": INDEX_PROMPT_VERSION,
                            "context_version_on_resume": PAGE_TRANSLATION_CONTEXT_VERSION,
                            "generation_policy_version_on_resume": GENERATION_POLICY_VERSION,
                            "pages": [pages[k] for k in sorted(pages)]})
            save_checkpoint(checkpoint_path, summary)
            print(json.dumps({"page_result": item, "status": "incomplete"}, ensure_ascii=True), flush=True)
            return 2

        render = client.post(f"/api/library/{chapter_id}/pages/{number}/render")
        item["render_http"] = render.status_code
        if render.status_code == 200:
            item.pop("render_error", None)
            rendered = render.get_json() or {}
            item.update({"rendered_regions": rendered.get("rendered_regions"),
                         "skipped_regions": rendered.get("skipped_regions")})
        else:
            item["render_error"] = render.get_json(silent=True) or render.get_data(as_text=True)[:1000]
        pages[number] = item
        summary.update({"status": "in_progress", "pipeline_version_on_resume": PAGE_PIPELINE_VERSION,
                        "prompt_version_on_resume": INDEX_PROMPT_VERSION,
                        "context_version_on_resume": PAGE_TRANSLATION_CONTEXT_VERSION,
                        "generation_policy_version_on_resume": GENERATION_POLICY_VERSION,
                        "pages": [pages[k] for k in sorted(pages)]})
        save_checkpoint(checkpoint_path, summary)
        print(json.dumps({"page_result": item, "completed_pages": sum(
            p.get("processing_status") == "complete" for p in pages.values()),
            "page_count": summary.get("page_count")}, ensure_ascii=True), flush=True)

    export = client.get(f"/api/library/{chapter_id}/export?format=cbz&edition=rendered")
    summary["export_http"] = export.status_code
    if export.status_code == 200:
        summary.pop("export_error", None)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        archive_path = checkpoint_path.parent / f"vertical-chapter-resume-{stamp}.cbz"
        archive_path.write_bytes(export.data)
        with zipfile.ZipFile(archive_path) as archive:
            names = archive.namelist()
            summary["export"] = {"path": str(archive_path), "bytes": len(export.data),
                                  "image_count": sum(n.lower().endswith((".png", ".jpg", ".webp")) for n in names),
                                  "comic_info": "ComicInfo.xml" in names}
        apply_completion_metadata(
            summary, pages, len(chapter["pages"]), summary["export"],
            PAGE_PIPELINE_VERSION, INDEX_PROMPT_VERSION,
            PAGE_TRANSLATION_CONTEXT_VERSION, GENERATION_POLICY_VERSION,
        )
    else:
        summary["status"] = "export_failed"
        summary["export_error"] = export.get_json(silent=True) or export.get_data(as_text=True)[:1000]
    save_checkpoint(checkpoint_path, summary)
    print(json.dumps({"final": summary}, ensure_ascii=True), flush=True)
    return 0 if summary["status"] == "complete" else 3


if __name__ == "__main__":
    raise SystemExit(main())
