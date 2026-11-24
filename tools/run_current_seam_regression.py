"""Re-run four saved long-strip gold pages with the current seam-retry OCR."""

from __future__ import annotations

import hashlib
import json
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ocr_engine import LocalRapidOCR
from tools.score_magic_emperor_retry_full_run import distance, tokens

ARCHIVE = ROOT / "QUALITY_REVIEW_SAMPLES.zip"
DATA = ROOT / "translator_engine/data/benchmark_runs"
OUTPUT_DIR = DATA / "seam_retry_regression_20261112"
SCORE_PATH = OUTPUT_DIR / "seam_retry_regression_score.json"


def main():
    if OUTPUT_DIR.exists():
        raise FileExistsError(f"Refusing to overwrite existing output directory: {OUTPUT_DIR}")
    OUTPUT_DIR.mkdir(parents=True)
    engine = LocalRapidOCR()
    pages = []
    with zipfile.ZipFile(ARCHIVE) as archive:
        for page in (2, 3, 5, 14):
            member = f"page_{page:02d}/original_english.webp"
            image_bytes = archive.read(member)
            gold_path = next(DATA.rglob(f"page{page:02d}_independent_manual_gold_20261112.json"))
            gold = json.loads(gold_path.read_text(encoding="utf-8"))
            references = [row["manual_source_transcript"] for row in gold["regions"] if tokens(row["manual_source_transcript"])]
            started = time.perf_counter()
            result = engine.extract(image_bytes, include_external=False)
            elapsed = time.perf_counter() - started
            result.update({
                "page": page,
                "source_member": member,
                "source_sha256": hashlib.sha256(image_bytes).hexdigest(),
                "pipeline_revision": "selective OCR retries + targeted low-confidence strip-seam recovery",
            })
            page_path = OUTPUT_DIR / f"page_{page:02d}_speech_ocr.json"
            page_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

            regions = result.get("regions", [])
            ref_words = [word for text in references for word in tokens(text)]
            current_words = [word for region in regions for word in tokens(region.get("text", ""))]
            current_errors, current_edits = distance(ref_words, current_words)
            baseline_texts = [
                row.get("ocr_text_after_correction", row.get("ocr_text", ""))
                for row in gold["regions"] if tokens(row["manual_source_transcript"])
            ]
            baseline_words = [word for text in baseline_texts for word in tokens(text)]
            baseline_errors, baseline_edits = distance(ref_words, baseline_words)
            pages.append({
                "page": page,
                "manual_word_bearing_regions": len(references),
                "current_ocr_regions": len(regions),
                "source_sha256": result["source_sha256"],
                "elapsed_seconds": round(elapsed, 3),
                "reference_words": len(ref_words),
                "baseline_word_errors": baseline_errors,
                "baseline_wer": round(baseline_errors / max(1, len(ref_words)), 6),
                "current_word_errors": current_errors,
                "current_wer": round(current_errors / max(1, len(ref_words)), 6),
                "baseline_edits": baseline_edits,
                "current_edits": current_edits,
                "recognized_regions": [region.get("text", "") for region in regions],
            })
            print(json.dumps({
                "page": page, "regions": len(regions), "manual_regions": len(references),
                "elapsed_seconds": round(elapsed, 3), "word_errors": current_errors,
                "saved": page_path.name,
            }, ensure_ascii=False), flush=True)

    total_words = sum(row["reference_words"] for row in pages)
    total_baseline_errors = sum(row["baseline_word_errors"] for row in pages)
    total_current_errors = sum(row["current_word_errors"] for row in pages)
    report = {
        "scope": "four saved vertical visual-gold pages; speech-only, current seam-retry OCR",
        "pages": pages,
        "aggregate": {
            "page_count": len(pages),
            "manual_regions": sum(row["manual_word_bearing_regions"] for row in pages),
            "current_regions": sum(row["current_ocr_regions"] for row in pages),
            "reference_words": total_words,
            "baseline_word_errors": total_baseline_errors,
            "baseline_wer": round(total_baseline_errors / max(1, total_words), 6),
            "current_word_errors": total_current_errors,
            "current_wer": round(total_current_errors / max(1, total_words), 6),
        },
        "limits": [
            "Four pages from the same archived work set; not a population estimate.",
            "Word score is order-preserving over flattened region text; region counts are reported separately.",
            "No external-text OCR, Index, or translation ran.",
        ],
    }
    SCORE_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"aggregate": report["aggregate"], "score": str(SCORE_PATH)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
