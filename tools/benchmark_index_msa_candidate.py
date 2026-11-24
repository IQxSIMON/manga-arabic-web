"""Compare Index's compact prompt with an MSA-strict candidate on sample lines."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import requests


ROOT = Path(__file__).resolve().parents[1]
SERVER = "http://127.0.0.1:9941/v1/chat/completions"
MODEL = "Index-Translate-9B.Q4_K_M"
API_KEY = ROOT / "runtime" / "llama.cpp" / "bin" / "index_translate_9b-api-key.txt"

PROMPT = (
    "Translate this English webtoon text into fluent, natural Modern Standard Arabic. "
    "Use strictly Modern Standard Arabic, never colloquial dialect. Preserve meaning, "
    "emotion, tone, and confirmed names. Prefer idiomatic, concise phrasing without "
    "omitting meaning. Use prior dialogue only to resolve references and terminology. "
    "Do not invent facts, repeat earlier dialogue, or guess missing OCR words. For short "
    "reactions, choose a natural conversational Arabic equivalent in context rather than "
    "a dictionary gloss. Return only one Arabic translation.\n\n"
    "CURRENT TEXT: {source}\n"
    "CONTEXT (reference only; do not translate): {context}"
)

SAMPLES = (
    (
        "I CAN'T MISS TODAY'S REWARD...!",
        "I haven't done today's daily quest because I arrived in a rush.",
    ),
    ("OBVIOUSLY.", "You seem exhausted, young monarch."),
)


def main() -> int:
    if not API_KEY.is_file():
        print("The local Index API key is missing.", file=sys.stderr)
        return 2
    headers = {
        "Authorization": f"Bearer {API_KEY.read_text(encoding='ascii').strip()}"
    }
    for source, context in SAMPLES:
        started = time.perf_counter()
        response = requests.post(
            SERVER,
            headers=headers,
            json={
                "model": MODEL,
                "messages":[{"role": "user", "content": PROMPT.format(
                    source=source, context=context,
                )}],
                "temperature": 0,
                "top_p": 1,
                "max_tokens": 160,
                "stream": False,
                "chat_template_kwargs": {"enable_thinking": False},
            },
            timeout=900,
        )
        response.raise_for_status()
        result = response.json()
        print(json.dumps({
            "source": source,
            "translation": result["choices"][0]["message"]["content"],
            "elapsed_seconds": round(time.perf_counter() - started, 2),
            "timings": result.get("timings"),
        }, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
