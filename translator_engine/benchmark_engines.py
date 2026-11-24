"""Compare local OPUS and optional Qwen output on the reviewed dialogue set."""

from __future__ import annotations

import json
import os
import platform
import time
from pathlib import Path

from engine import TranslatorEngine
from llama_cpp_engine import LlamaCppTranslator


ROOT = Path(__file__).resolve().parent
CORPUS = ROOT / "data" / "reviewed_manhwa_dialogue.json"
OUTPUT = ROOT / "data" / "engine_comparison_latest.json"


def main():
    samples = json.loads(CORPUS.read_text(encoding="utf-8"))
    opus = TranslatorEngine()
    qwen = LlamaCppTranslator()
    results = []
    try:
        for sample in samples:
            result = {"source": sample["source"], "reviewed_target": sample["target"]}
            started = time.perf_counter()
            result["opus"] = opus.translate(sample["source"])
            result["opus_seconds"] = round(time.perf_counter() - started, 3)
            started = time.perf_counter()
            result["qwen3"] = qwen.translate(sample["source"])
            result["qwen3_seconds"] = round(time.perf_counter() - started, 3)
            results.append(result)
    finally:
        qwen.shutdown()

    report = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "platform": platform.platform(),
        "cpu_threads": os.cpu_count(),
        "qwen_model": "Qwen3-1.7B-Q8_0.gguf",
        "qwen_runtime": "llama.cpp b12059 CPU",
        "qwen_license": "Apache-2.0",
        "opus_model": "existing local OPUS-MT English-Arabic ONNX assets; upstream revision not confirmed",
        "samples": results,
        "caution": "Small developer-reviewed sample; not a general translation quality benchmark.",
    }
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(OUTPUT)
    print(f"Compared {len(results)} utterances; results written as UTF-8 JSON.")


if __name__ == "__main__":
    main()
