"""One-shot comparison of current per-bubble Index calls and one numbered batch.

Uses four already-saved consecutive speech bubbles from QUALITY_REVIEW_SAMPLES.zip.
It requires a healthy local Index llama.cpp server and never starts or downloads one.
The current path is run with two parallel requests, matching the app's default.
No translation-memory or chapter files are written.
"""

from __future__ import annotations

import concurrent.futures
import argparse
import json
import re
import sys
import threading
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import requests


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import (  # noqa: E402
    _build_translation_context,
    _dialogue_context_line,
    _model_glossary_entries,
    build_model_context,
)
from translator_engine.adapters import (  # noqa: E402
    INDEX_MODEL,
    IndexTranslateAdapter,
    clean_translation_output,
)


SAMPLES_ZIP = ROOT / "QUALITY_REVIEW_SAMPLES.zip"
PAGE_NAME = "page_02"
SAMPLE_COUNT = 4
BASELINE_WORKERS = 2
TIMEOUT_SECONDS = 1800
POLICY = (
    "Translate this English webtoon text into fluent, natural Modern Standard Arabic. "
    "Use strictly Modern Standard Arabic, never colloquial dialect. Preserve meaning, "
    "emotion, tone, and confirmed names. Prefer idiomatic, concise phrasing without "
    "omitting meaning. Use prior dialogue only to resolve references and terminology. "
    "Do not invent facts, repeat earlier dialogue, or guess missing OCR words. For short "
    "reactions, choose a natural conversational Arabic equivalent in context rather than "
    "a dictionary gloss."
)
OUTPUT_LINE = re.compile(r"^\s*[-*]?\s*(\d+)\s*[.):\-]\s*(.*?)\s*$")
INLINE_ID = re.compile(r"(?<!\S)(\d{1,2})\s*[.)]\s*(?=[«\"'“‘\u0600-\u06ff])")


def _load_samples() -> list[dict]:
    with zipfile.ZipFile(SAMPLES_ZIP) as archive:
        page = json.loads(archive.read(f"{PAGE_NAME}/regions.json"))
    regions = page["regions"]
    selected = [region for region in regions if region.get("region_type") == "speech_bubble"][:SAMPLE_COUNT]
    if len(selected) != SAMPLE_COUNT:
        raise RuntimeError(f"Expected {SAMPLE_COUNT} saved dialogue bubbles; found {len(selected)}.")
    orders = [int(region["reading_order"]) for region in selected]
    if orders != list(range(orders[0], orders[0] + SAMPLE_COUNT)):
        raise RuntimeError("The selected speech bubbles are not consecutive in the saved reading order.")

    result = []
    for index, region in enumerate(selected):
        previous = regions[max(0, index - 2):index]
        dialogue_context = "\n".join(
            line for item in previous if (line := _dialogue_context_line({
                "text": item.get("english_text", ""),
                "region_type": item.get("region_type"),
            }))
        )
        context = _build_translation_context("speech_bubble", dialogue_context)
        # Match translate_text()'s normal prompt preparation, including its
        # compact prior-dialogue wrapper and any applicable glossary terms.
        context = build_model_context(
            context, _model_glossary_entries(region["english_text"], "Solo Leveling: Ragnarok")
        )
        result.append({
            "id": index + 1,
            "reading_order": orders[index],
            "source": str(region["english_text"]).strip(),
            "saved_translation": str(region.get("arabic_translation", "")).strip(),
            "context": context,
        })
    return result


def _output_budget(source: str) -> int:
    return min(512, max(64, len(source.split()) * 4 + 24))


def _payload(adapter: IndexTranslateAdapter, sample: dict) -> dict:
    payload = {
        "model": adapter.model_path.stem,
        "messages": adapter.build_messages(sample["source"], sample["context"]),
        "temperature": 0,
        "top_p": 1,
        "max_tokens": _output_budget(sample["source"]),
        "stream": False,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    return payload


def _timing_fields(response: dict) -> dict:
    raw = response.get("timings", {})
    names = (
        "prompt_n", "prompt_ms", "predicted_n", "predicted_ms", "cache_n",
        "prompt_per_second", "predicted_per_second",
    )
    return {key: raw[key] for key in names if isinstance(raw.get(key), (int, float))}


def _send(adapter: IndexTranslateAdapter, payload: dict) -> tuple[str, dict, dict, float]:
    started = time.perf_counter()
    response = adapter._post_chat_completion(payload, timeout=TIMEOUT_SECONDS)
    elapsed = time.perf_counter() - started
    body = response.json()
    if not body.get("choices"):
        raise RuntimeError("Index returned no completion choice.")
    choice = body["choices"][0]
    text = clean_translation_output(choice.get("message", {}).get("content", ""))
    return text, _timing_fields(body), body.get("usage", {}), elapsed


def _batch_payload(adapter: IndexTranslateAdapter, samples: list[dict]) -> dict:
    sections = []
    for sample in samples:
        sections.append(
            f"BUBBLE {sample['id']}\nCURRENT TEXT:\n{sample['source']}\n\n"
            f"CONTEXT (reference only; do not translate):\n{sample['context']}"
        )
    prompt = (
        POLICY + "\n\nTranslate every numbered bubble independently, using only its own "
        "context to resolve references. Preserve the supplied IDs and source order. Return "
        "exactly one line per bubble in the form `1. <Arabic translation>`. Do not omit, "
        "merge, split, or reorder bubbles; do not translate context.\n\n"
        + "\n\n---\n\n".join(sections)
    )
    return {
        "model": adapter.model_path.stem,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "top_p": 1,
        "max_tokens": sum(_output_budget(sample["source"]) for sample in samples),
        "stream": False,
        "chat_template_kwargs": {"enable_thinking": False},
    }


def _parse_batch(text: str, expected_ids: set[int]) -> tuple[dict[int, str], list[str]]:
    parsed: dict[int, str] = {}
    unexpected: list[str] = []
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if lines and all(OUTPUT_LINE.match(line) and len(list(INLINE_ID.finditer(line))) == 1
                     for line in lines):
        matches = []
        for line in lines:
            match = OUTPUT_LINE.match(line)
            matches.append((int(match.group(1)), match.group(2).strip()))
        for number, translation in matches:
            if number not in expected_ids or number in parsed or not translation:
                unexpected.append(f"{number}. {translation}".strip())
            else:
                parsed[number] = translation
    else:
        matches = list(INLINE_ID.finditer(text))
        if matches and text[:matches[0].start()].strip():
            unexpected.append(text[:matches[0].start()].strip())
        for index, match in enumerate(matches):
            number = int(match.group(1))
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            # Keep source punctuation such as ellipses; only trim surrounding whitespace.
            translation = text[match.end():end].strip()
            if number not in expected_ids or number in parsed or not translation:
                unexpected.append(text[match.start():end].strip())
            else:
                parsed[number] = translation
    return parsed, unexpected


def _arabic(text: str) -> bool:
    return bool(re.search(r"[\u0600-\u06ff\u0750-\u077f\u08a0-\u08ff]", text or ""))


def _reparse_saved() -> int:
    output = ROOT / "translator_engine" / "data" / "benchmark_runs" / "index_batch_20261111.json"
    report = json.loads(output.read_text(encoding="utf-8"))
    expected_ids = {int(row["id"]) for row in report["rows"]}
    parsed, unexpected = _parse_batch(report["batch"]["raw_output"], expected_ids)
    ids_in_source_order = list(parsed) == sorted(expected_ids)
    report["batch"]["id_parse_complete"] = set(parsed) == expected_ids and not unexpected
    report["batch"]["ids_in_source_order"] = ids_in_source_order
    report["batch"]["unexpected_lines"] = unexpected
    report["batch"]["all_arabic"] = set(parsed) == expected_ids and all(
        _arabic(value) for value in parsed.values()
    )
    for row in report["rows"]:
        translation = parsed.get(int(row["id"]), "")
        row["batch_translation"] = translation
        row["batch_has_arabic"] = _arabic(translation)
    baseline = {int(row["id"]): row["current_translation"] for row in report["rows"]}
    report["quality_gate"] = {
        "all_ids_present_once": set(parsed) == expected_ids and not unexpected,
        "ids_in_source_order": ids_in_source_order,
        "baseline_and_batch_arabic": report["baseline"]["all_arabic"] and report["batch"]["all_arabic"],
        "baseline_batch_exact_match": all(parsed.get(number) == value for number, value in baseline.items()),
    }
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(output), "raw_batch_output": report["batch"]["raw_output"],
                      "parsed": parsed, "unexpected": unexpected,
                      "quality_gate": report["quality_gate"]}, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reparse-existing", action="store_true",
                        help="repair the saved benchmark's ID parsing without inference")
    args = parser.parse_args()
    if args.reparse_existing:
        return _reparse_saved()
    samples = _load_samples()
    model = Path(INDEX_MODEL)
    if not model.is_file():
        raise RuntimeError(f"Local Index model file is missing: {model}")
    adapter = IndexTranslateAdapter()
    try:
        # Check the already-running server and its authentication without triggering startup.
        check = requests.get(
            f"{adapter.base_url}/v1/models", headers=adapter._headers(), timeout=3,
            proxies={"http": None, "https": None},
        )
        check.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError("Index server is not healthy; no inference was submitted.") from exc

    # Current web-page path submits independent regions to two llama.cpp slots.
    def run_one(sample: dict) -> dict:
        local_adapter = IndexTranslateAdapter()
        text, timings, usage, elapsed = _send(local_adapter, _payload(local_adapter, sample))
        return {
            "id": sample["id"], "translation": text,
            "timings": timings, "usage": usage, "wall_seconds": round(elapsed, 3),
            "arabic": _arabic(text),
        }

    baseline_started = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=BASELINE_WORKERS) as pool:
        baseline_futures = [pool.submit(run_one, sample) for sample in samples]
        baseline_rows = [future.result() for future in baseline_futures]
    baseline_elapsed = time.perf_counter() - baseline_started
    baseline_rows.sort(key=lambda item: item["id"])

    batch_started = time.perf_counter()
    batch_text, batch_timings, batch_usage, batch_elapsed = _send(
        adapter, _batch_payload(adapter, samples)
    )
    batch_wall = time.perf_counter() - batch_started
    expected_ids = {sample["id"] for sample in samples}
    parsed, unexpected = _parse_batch(batch_text, expected_ids)
    batch_rows = [
        {"id": sample["id"], "translation": parsed.get(sample["id"], ""),
         "arabic": _arabic(parsed.get(sample["id"], ""))}
        for sample in samples
    ]

    baseline_by_id = {row["id"]: row["translation"] for row in baseline_rows}
    rows = []
    saved_by_id = {sample["id"]: sample["saved_translation"] for sample in samples}
    for sample in samples:
        number = sample["id"]
        rows.append({
            **{key: sample[key] for key in ("id", "reading_order", "source", "saved_translation")},
            "current_translation": baseline_by_id[number],
            "batch_translation": parsed.get(number, ""),
            "current_has_arabic": _arabic(baseline_by_id[number]),
            "batch_has_arabic": _arabic(parsed.get(number, "")),
        })

    speedup = baseline_elapsed / batch_wall if batch_wall else None
    report = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model": model.name,
        "endpoint": adapter.base_url,
        "source": f"{SAMPLES_ZIP.name}:{PAGE_NAME}/regions.json",
        "sample_count": len(samples),
        "settings": {"temperature": 0, "top_p": 1, "thinking": False,
                      "baseline_parallel_slots": BASELINE_WORKERS,
                      "max_tokens_policy": "same per-bubble budget; batch sums budgets",
                      "context_preparation": "app build_model_context + applicable glossary"},
        "baseline": {
            "method": "current per-region prompts, two simultaneous requests",
            "wall_seconds": round(baseline_elapsed, 3),
            "requests": baseline_rows,
            "prompt_tokens_total": sum(int(row["timings"].get("prompt_n", 0)) for row in baseline_rows),
            "output_tokens_total": sum(int(row["timings"].get("predicted_n", 0)) for row in baseline_rows),
            "all_arabic": all(row["arabic"] for row in baseline_rows),
        },
        "batch": {
            "method": "one numbered request, preserving per-bubble context",
            "wall_seconds": round(batch_wall, 3),
            "request_wall_seconds": round(batch_elapsed, 3),
            "timings": batch_timings,
            "usage": batch_usage,
            "prompt_tokens": batch_timings.get("prompt_n", batch_usage.get("prompt_tokens")),
            "output_tokens": batch_timings.get("predicted_n", batch_usage.get("completion_tokens")),
            "raw_output": batch_text,
            "id_parse_complete": set(parsed) == expected_ids and not unexpected,
            "unexpected_lines": unexpected,
            "all_arabic": set(parsed) == expected_ids and all(_arabic(value) for value in parsed.values()),
        },
        "speedup_vs_current": round(speedup, 3) if speedup is not None else None,
        "wall_reduction_percent": round((1 - batch_wall / baseline_elapsed) * 100, 1)
        if baseline_elapsed else None,
        "rows": rows,
        "saved_output_references": saved_by_id,
        "quality_gate": {
            "all_ids_present_once": set(parsed) == expected_ids and not unexpected,
            "baseline_and_batch_arabic": all(row["arabic"] for row in baseline_rows)
            and set(parsed) == expected_ids and all(_arabic(value) for value in parsed.values()),
            "baseline_batch_exact_match": all(parsed.get(number) == value
                                               for number, value in baseline_by_id.items()),
        },
    }
    output = ROOT / "translator_engine" / "data" / "benchmark_runs" / "index_batch_20261111.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(output), "baseline_seconds": report["baseline"]["wall_seconds"],
                      "batch_seconds": report["batch"]["wall_seconds"],
                      "speedup": report["speedup_vs_current"],
                      "quality_gate": report["quality_gate"],
                      "rows": report["rows"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Benchmark failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
