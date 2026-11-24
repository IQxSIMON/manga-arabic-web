"""Exercise the app's Index batch-to-memory path on four saved bubbles once.

Requires an already-running local Index server. Uses a temporary isolated manga
library and only reads the saved review sample ZIP; it does not process a chapter.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import time
import zipfile
import argparse
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SAMPLES_ZIP = ROOT / "QUALITY_REVIEW_SAMPLES.zip"
OUTPUT = ROOT / "translator_engine" / "data" / "benchmark_runs" / "index_batch_app_integration_20261111.json"


def verify_saved_run() -> int:
    report = json.loads(OUTPUT.read_text(encoding="utf-8"))
    if (not report.get("all_items_recorded_as_batch")
            or report.get("translation_events") != 4
            or len(report.get("items", [])) != 4):
        raise RuntimeError("The saved integration evidence is incomplete.")
    print(json.dumps({"report": str(OUTPUT), "elapsed_seconds": report["elapsed_seconds"],
                      "translation_events": report["translation_events"],
                      "modes": [item["mode"] for item in report["items"]]}, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify-saved-run", action="store_true",
                        help="verify recorded integration evidence without model inference")
    args = parser.parse_args()
    if args.verify_saved_run:
        return verify_saved_run()
    with tempfile.TemporaryDirectory(prefix="manga-index-batch-smoke-") as library_dir:
        os.environ["MANGA_LIBRARY_DIR"] = library_dir
        sys.path.insert(0, str(ROOT))
        from app import (  # imported after the isolated library is configured
            _build_translation_context,
            _dialogue_context_line,
            _translate_index_region_group,
            get_balanced_translator,
        )

        adapter = get_balanced_translator().adapters[0]
        if not adapter._server_accepts_api_key():
            raise RuntimeError("Start the local Index server before running this integration check.")

        with zipfile.ZipFile(SAMPLES_ZIP) as archive:
            page = json.loads(archive.read("page_02/regions.json"))
        regions = [item for item in page["regions"]
                   if item.get("region_type") == "speech_bubble"][:4]
        if len(regions) != 4:
            raise RuntimeError("Saved sample does not contain four speech bubbles.")

        requests = []
        for offset, region in enumerate(regions):
            context_lines = "\n".join(
                text for previous in regions[max(0, offset - 2):offset]
                if (text := _dialogue_context_line({
                    "text": previous.get("english_text", ""),
                    "region_type": previous.get("region_type"),
                }))
            )
            context = _build_translation_context("speech_bubble", context_lines)
            index = int(region["reading_order"])
            requests.append({
                "text": region["english_text"], "context": context,
                "region_type": "speech_bubble", "chapter_title": page["chapter"],
                "page_index": page["page_number"], "region_index": index,
                "work_scope": page["chapter"], "glossary_scope": page["chapter"],
            })

        started = time.perf_counter()
        translations = _translate_index_region_group(requests)
        elapsed = time.perf_counter() - started
        if len(translations) != 4 or not all(any("\u0600" <= c <= "\u06ff" and c.isalpha()
                                                 for c in value) for value in translations):
            raise RuntimeError("Application batch path returned missing or non-Arabic output.")

        db_path = Path(library_dir) / "library.sqlite3"
        db = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
        try:
            events = db.execute(
                "SELECT target_text, metadata_json FROM translation_events "
                "WHERE event_type = 'engine_output' ORDER BY created_at"
            ).fetchall()
        finally:
            db.close()
        modes = [json.loads(metadata or "{}").get("translation_mode") for _, metadata in events]
        if len(events) != 4 or modes != ["numbered_batch"] * 4:
            raise RuntimeError(f"App did not record all four results as one batch: {modes}")

        report = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "sample": f"{SAMPLES_ZIP.name}:page_02/regions.json",
            "method": "app._translate_index_region_group -> Index numbered batch -> isolated translation memory",
            "model": "Index-Translate-9B.Q4_K_M",
            "elapsed_seconds": round(elapsed, 3),
            "items": [
                {"id": request["region_index"], "source": request["text"],
                 "translation": translation, "mode": mode}
                for request, translation, mode in zip(requests, translations, modes)
            ],
            "translation_events": len(events),
            "all_items_recorded_as_batch": modes == ["numbered_batch"] * 4,
            "isolated_library": True,
        }
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"report": str(OUTPUT), "elapsed_seconds": report["elapsed_seconds"],
                          "items": report["items"], "events": report["translation_events"],
                          "all_items_recorded_as_batch": report["all_items_recorded_as_batch"]},
                         ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
