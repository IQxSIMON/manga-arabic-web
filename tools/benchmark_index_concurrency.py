"""Compare serial and two-slot Index inference without changing prompts or decoding."""

from __future__ import annotations

import concurrent.futures
import json
import re
import sys
import time
from pathlib import Path

import requests


ROOT = Path(__file__).resolve().parents[1]
ENDPOINT = "http://127.0.0.1:9941/v1/chat/completions"
API_KEY = ROOT / "runtime" / "llama.cpp" / "bin" / "index_translate_9b-api-key.txt"
MODEL = "Index-Translate-9B.Q4_K_M"
SAMPLES = (
    ("I CAN'T MISS TODAY'S REWARD...!", "I arrived late and missed the daily quest."),
    ("Obviously.", "She asks whether he understands the warning."),
    ("Then come with me.", "She offered to show him the way home."),
    ("She doesn't feel a thing even when her head gets blown off.",
     "They are discussing an undead character who cannot feel pain."),
)


def _payload(source: str, context: str) -> dict:
    prompt = (
        "Translate this English webtoon text into fluent, natural Modern Standard Arabic. "
        "Use strictly Modern Standard Arabic, never colloquial dialect. Preserve meaning, "
        "emotion, tone, and confirmed names. Prefer idiomatic, concise phrasing without "
        "omitting meaning. Use prior dialogue only to resolve references and terminology. "
        "Do not invent facts, repeat earlier dialogue, or guess missing OCR words. For short "
        "reactions, choose a natural conversational Arabic equivalent in context rather than "
        "a dictionary gloss. Return only one Arabic translation.\n\n"
        f"CURRENT TEXT:\n{source}\n\n"
        f"CONTEXT (reference only; do not translate):\n{context}"
    )
    return {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "top_p": 1,
        "max_tokens": min(512, max(64, len(source.split()) * 4 + 24)),
        "stream": False,
        "chat_template_kwargs": {"enable_thinking": False},
    }


def _run_one(index: int, headers: dict[str, str]) -> tuple[int, float, str, dict]:
    started = time.perf_counter()
    response = requests.post(
        ENDPOINT, headers=headers, json=_payload(*SAMPLES[index]), timeout=900,
    )
    response.raise_for_status()
    data = response.json()
    translation = data["choices"][0]["message"]["content"].strip()
    return index, time.perf_counter() - started, translation, data.get("timings", {})


def main() -> int:
    if not API_KEY.is_file():
        print("The saved local Index API key is missing.", file=sys.stderr)
        return 2
    headers = {"Authorization": f"Bearer {API_KEY.read_text(encoding='ascii').strip()}"}
    count = len(SAMPLES)
    serial_started = time.perf_counter()
    serial = [_run_one(index, headers) for index in range(count)]
    serial_elapsed = time.perf_counter() - serial_started

    parallel_started = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        parallel = list(pool.map(lambda index: _run_one(index, headers), range(count)))
    parallel_elapsed = time.perf_counter() - parallel_started

    serial_by_index = {index: translation for index, _elapsed, translation, _timings in serial}
    parallel_by_index = {index: translation for index, _elapsed, translation, _timings in parallel}
    arabic = lambda value: bool(re.search(r"[\u0600-\u06ff]", value))
    report = {
        "model": MODEL,
        "server_slots": 2,
        "samples": count,
        "serial_wall_seconds": round(serial_elapsed, 2),
        "parallel_wall_seconds": round(parallel_elapsed, 2),
        "speedup": round(serial_elapsed / parallel_elapsed, 3) if parallel_elapsed else None,
        "all_outputs_have_arabic": all(arabic(value) for value in parallel_by_index.values()),
        "outputs_identical_serial_vs_parallel": serial_by_index == parallel_by_index,
        "serial": [
            {"source": SAMPLES[index][0], "translation": translation,
             "elapsed_seconds": round(elapsed, 2), "timings": timings}
            for index, elapsed, translation, timings in serial
        ],
        "parallel": [
            {"source": SAMPLES[index][0], "translation": translation,
             "elapsed_seconds": round(elapsed, 2), "timings": timings}
            for index, elapsed, translation, timings in sorted(parallel)
        ],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["all_outputs_have_arabic"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
