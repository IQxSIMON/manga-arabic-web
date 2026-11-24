"""Run a reproducible, sequential comparison of locally installed translators.

The report deliberately stores raw outputs and latency rather than pretending
that character overlap is a translation-quality score. Human review remains
the authority for idioms, dialogue tone, and Arabic fluency.
"""

from __future__ import annotations

import argparse
import gc
import json
import math
import platform
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

try:
    from .adapters import (
        GENERATION_POLICY_VERSION, INDEX_PROMPT_VERSION,
        TRANSLATEGEMMA_PROMPT_VERSION, IndexTranslateAdapter, TranslateGemmaAdapter,
    )
except ImportError:  # Support direct execution from the project root.
    from adapters import (
        GENERATION_POLICY_VERSION, INDEX_PROMPT_VERSION,
        TRANSLATEGEMMA_PROMPT_VERSION, IndexTranslateAdapter, TranslateGemmaAdapter,
    )


ROOT = Path(__file__).resolve().parent
CORPUS = ROOT / "data" / "reviewed_manhwa_dialogue.json"
CHALLENGE_CORPUS = ROOT / "data" / "manhwa_dialogue_challenge.json"
REPORT_DIR = ROOT / "data" / "benchmark_runs"


def _normalize(text: str) -> str:
    return " ".join(text.split()).strip().casefold()


def _arabic_letter_ratio(text: str) -> float:
    import unicodedata

    letters = [char for char in text if unicodedata.category(char).startswith("L")]
    if not letters:
        return 0.0
    return round(sum("\u0600" <= char <= "\u06ff" for char in letters) / len(letters), 4)


def _index_factory():
    if not IndexTranslateAdapter.available():
        raise FileNotFoundError("Index weights or llama-server.exe are missing")
    return IndexTranslateAdapter()


def _translategemma_factory():
    if not TranslateGemmaAdapter.available():
        raise FileNotFoundError("TranslateGemma weights or llama-server.exe are missing")
    return TranslateGemmaAdapter()


def _translate(engine, source: str, context: str) -> str:
    return engine.translate(source, context=context)


def _inference_metrics(engine) -> dict:
    """Copy optional llama.cpp counters into the report without inventing data."""
    raw = getattr(engine, "last_inference_timings", None)
    if not isinstance(raw, dict):
        return {}
    metrics = {}
    for key in ("prompt_n", "prompt_ms", "predicted_n", "predicted_ms"):
        value = raw.get(key)
        if (isinstance(value, (int, float)) and not isinstance(value, bool)
                and math.isfinite(value) and value >= 0):
            metrics[key] = value
    if metrics.get("prompt_n") and metrics.get("prompt_ms"):
        metrics["prompt_tokens_per_second"] = round(
            metrics["prompt_n"] * 1000 / metrics["prompt_ms"], 2
        )
    if metrics.get("predicted_n") and metrics.get("predicted_ms"):
        metrics["generated_tokens_per_second"] = round(
            metrics["predicted_n"] * 1000 / metrics["predicted_ms"], 2
        )
    return metrics


def _sample_context(sample: dict) -> str:
    region_type = sample.get("region_type", "speech_bubble")
    if region_type == "external_text":
        instruction = (
            "Current region is visible English narration/caption outside a speech bubble. "
            "Translate it as concise, natural Arabic narration and preserve its complete meaning; "
            "do not treat it as spoken dialogue."
        )
    else:
        instruction = (
            "Current region is dialogue inside a speech bubble. Translate the full utterance "
            "as natural Arabic speech, preserving its tone and complete meaning."
        )
    prior_context = str(sample.get("context", "")).strip()
    return instruction + ("\n" + prior_context if prior_context else "")


def _shutdown(engine) -> None:
    shutdown = getattr(engine, "shutdown", None)
    if shutdown:
        shutdown()
    # Release framework-owned model references before the next model loads.
    for attr in ("_translator", "_tokenizer", "encoder", "decoder", "decoder_past"):
        if hasattr(engine, attr):
            setattr(engine, attr, None)
    del engine
    gc.collect()


def run_engine(engine_id: str, factory, samples: list[dict], on_sample=None) -> dict:
    result = {
        "engine_id": engine_id,
        "status": "pending",
        "model": None,
        "context_mode": (
            "dialogue-and-region-context" if engine_id == "index_translate_9b"
            else "source-only" if engine_id == "translategemma_4b"
            else "adapter-defined"
        ),
        "samples": [],
    }
    engine = None
    try:
        engine = factory()
        result["model"] = str(getattr(
            engine, "model_name", getattr(engine, "model_path", type(engine).__name__)
        ))
        timings = []
        result["status"] = "complete"
        for index, sample in enumerate(samples):
            item = {
                "index": index,
                "source": sample["source"],
                "context": _sample_context(sample),
                "region_type": sample.get("region_type", "speech_bubble"),
                "reference": sample.get("target", ""),
                "reference_status": sample.get("reference_status", "human_reviewed"),
                "category": sample.get("category", "dialogue"),
                "translation": None,
                "seconds": None,
                "inference_timings": None,
                "arabic_letter_ratio": None,
                "exact_reference_match": None,
                "human_review": {
                    "status": "pending",
                    "semantic_accuracy_1_to_5": None,
                    "arabic_fluency_1_to_5": None,
                    "dialogue_tone_1_to_5": None,
                    "bubble_fit_1_to_5": None,
                    "notes": "",
                },
                "error": None,
            }
            started = time.perf_counter()
            try:
                output = _translate(engine, sample["source"], item["context"])
                item["seconds"] = round(time.perf_counter() - started, 3)
                item["inference_timings"] = _inference_metrics(engine) or None
                item["translation"] = output
                item["arabic_letter_ratio"] = _arabic_letter_ratio(output)
                item["exact_reference_match"] = bool(output) and (
                    _normalize(output) == _normalize(sample.get("target", ""))
                )
                timings.append(item["seconds"])
            except Exception as exc:  # Keep a model failure visible in the report.
                item["seconds"] = round(time.perf_counter() - started, 3)
                item["error"] = f"{type(exc).__name__}: {exc}"
                result["status"] = "partial"
            result["samples"].append(item)
            if on_sample is not None:
                try:
                    on_sample(result, index + 1, len(samples), item)
                except Exception as exc:
                    result.setdefault("checkpoint_errors", []).append(
                        f"{type(exc).__name__}: {exc}"
                    )
        valid = [sample for sample in result["samples"] if sample["translation"] is not None]
        prompt_rates = [
            sample["inference_timings"]["prompt_tokens_per_second"] for sample in valid
            if (sample.get("inference_timings") or {}).get("prompt_tokens_per_second")
        ]
        generation_rates = [
            sample["inference_timings"]["generated_tokens_per_second"] for sample in valid
            if (sample.get("inference_timings") or {}).get("generated_tokens_per_second")
        ]
        warm_timings = [
            sample["seconds"] for sample in valid
            if sample.get("index", 0) > 0 and sample.get("seconds") is not None
        ]
        result["summary"] = {
            "sample_count": len(samples),
            "translated_count": len(valid),
            "arabic_output_count": sum(sample["arabic_letter_ratio"] >= 0.5 for sample in valid),
            "exact_reference_matches": sum(sample["exact_reference_match"] for sample in valid),
            "mean_seconds": round(sum(timings) / len(timings), 3) if timings else None,
            "max_seconds": max(timings) if timings else None,
            "first_request_seconds_including_startup": (
                result["samples"][0]["seconds"] if result["samples"] else None
            ),
            "mean_warm_request_seconds": (
                round(sum(warm_timings) / len(warm_timings), 3) if warm_timings else None
            ),
            "mean_prompt_tokens_per_second": round(sum(prompt_rates) / len(prompt_rates), 2) if prompt_rates else None,
            "mean_generated_tokens_per_second": round(sum(generation_rates) / len(generation_rates), 2) if generation_rates else None,
        }
    except Exception as exc:
        result["status"] = "unavailable"
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if engine is not None:
            try:
                _shutdown(engine)
            except Exception as exc:
                result["shutdown_error"] = f"{type(exc).__name__}: {exc}"
    return result


def build_report(samples: list[dict], runners=None, corpus_files=None,
                 on_sample: Callable | None = None) -> dict:
    runners = runners or [("index_translate_9b", _index_factory)]
    engines = []
    for engine_id, factory in runners:
        engines.append(run_engine(
            engine_id, factory, samples,
            on_sample=(
                (lambda result, done, total, item, current_id=engine_id:
                    on_sample(current_id, result, done, total, item))
                if on_sample is not None else None
            ),
        ))
    return {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "corpus": str(CORPUS.relative_to(ROOT)),
        "corpus_files": corpus_files or [str(CORPUS.relative_to(ROOT))],
        "corpus_size": len(samples),
        "prompt_versions": {
            "index_translate_9b": INDEX_PROMPT_VERSION,
            "translategemma_4b": TRANSLATEGEMMA_PROMPT_VERSION,
        },
        "generation_policy_version": GENERATION_POLICY_VERSION,
        "method": "Each engine runs sequentially against identical English vertical-comics samples regardless of country of origin, with supplied dialogue context and region type; raw inputs, outputs, reference status, and latency are preserved. Context-mode metadata distinguishes the contextual Index adapter from TranslateGemma's source-only adapter.",
        "automatic_metrics_note": "Arabic letter ratio and exact match are sanity diagnostics only; they do not measure semantic translation quality. Draft references marked draft_needs_human_review are proposals, not gold translations; matching one is not evidence of correctness.",
        "human_review_rubric": "Review source meaning, natural MSA, character tone, and concise fit for a speech balloon; fill each 1-5 field before choosing a default engine.",
        "engines": engines,
    }


def main() -> Path:
    parser = argparse.ArgumentParser(description="Benchmark local manhwa translation engines.")
    parser.add_argument(
        "--include-challenge", action="store_true",
        help="Include the extra draft dialogue/narration challenge set (targets need human review).",
    )
    parser.add_argument(
        "--include-translategemma", action="store_true",
        help="Also benchmark the inactive TranslateGemma candidate when its weights are installed.",
    )
    args = parser.parse_args()
    samples = json.loads(CORPUS.read_text(encoding="utf-8"))
    corpus_files = [str(CORPUS.relative_to(ROOT))]
    if args.include_challenge:
        samples.extend(json.loads(CHALLENGE_CORPUS.read_text(encoding="utf-8")))
        corpus_files.append(str(CHALLENGE_CORPUS.relative_to(ROOT)))
    runners = [("index_translate_9b", _index_factory)]
    if args.include_translategemma:
        runners.append(("translategemma_4b", _translategemma_factory))
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suite = "extended" if args.include_challenge else "reviewed"
    engine_set = "index_gemma" if args.include_translategemma else "index"
    output = REPORT_DIR / f"engine_matrix_{suite}_{engine_set}_{stamp}.json"

    def save_checkpoint(engine_id, engine_result, completed, total, item):
        checkpoint = {
            "schema_version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "platform": platform.platform(),
            "python": platform.python_version(),
            "corpus": str(CORPUS.relative_to(ROOT)),
            "corpus_files": corpus_files,
            "corpus_size": len(samples),
            "progress": {
                "status": "in_progress", "engine_id": engine_id,
                "completed_samples": completed, "total_samples": total,
                "last_sample_seconds": item.get("seconds"),
            },
            "engines": [engine_result],
        }
        temporary = output.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(checkpoint, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(output)
        print(
            f"{engine_id}: {completed}/{total} samples; latest {item.get('seconds')} s",
            flush=True,
        )

    report = build_report(
        samples, runners=runners, corpus_files=corpus_files, on_sample=save_checkpoint,
    )
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output)
    for engine in report["engines"]:
        print(f"{engine['engine_id']}: {engine['status']} {engine.get('summary', engine.get('error', ''))}")
    return output


if __name__ == "__main__":
    main()
