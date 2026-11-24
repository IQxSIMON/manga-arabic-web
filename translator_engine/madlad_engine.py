"""Local MADLAD-400 translation adapter using the CTranslate2 CPU runtime."""

from __future__ import annotations

import importlib.util
import os
import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL = PROJECT_ROOT / "translator_engine" / "models" / "alternatives" / "madlad400_ct2"


class MadladTranslator:
    """Lazy INT8 English-to-Arabic model; no network calls occur at inference."""

    def __init__(self, model_path=None):
        self.model_path = Path(model_path or os.getenv("MANGA_MADLAD_MODEL", DEFAULT_MODEL))
        self._translator = None
        self._tokenizer = None

    @staticmethod
    def available(model_path=None) -> bool:
        model = Path(model_path or os.getenv("MANGA_MADLAD_MODEL", DEFAULT_MODEL))
        return (
            (model / "model.bin").is_file()
            and (model / "spiece.model").is_file()
            and importlib.util.find_spec("ctranslate2") is not None
        )

    def _load(self):
        if self._translator is not None:
            return
        if not self.available(self.model_path):
            raise RuntimeError(
                "محرك MADLAD غير جاهز. ثبّت requirements-madlad.txt ونزّل ملفات النموذج المحلية."
            )
        import ctranslate2
        import sentencepiece as spm

        cpu_threads = max(1, min(4, (os.cpu_count() or 4) - 1))
        self._tokenizer = spm.SentencePieceProcessor(
            model_file=str(self.model_path / "spiece.model")
        )
        self._translator = ctranslate2.Translator(
            str(self.model_path),
            device="cpu",
            compute_type="int8",
            inter_threads=1,
            intra_threads=cpu_threads,
        )

    @staticmethod
    def _clean_output(text: str) -> str:
        text = re.sub(r"<\/?2[a-zA-Z_]+>", "", text)
        text = text.replace("</s>", "").strip()
        return " ".join(text.split())

    def _translate_chunk(self, text: str, max_new_tokens: int) -> str:
        self._load()
        tokens = self._tokenizer.encode(f"<2ar> {text}", out_type=str)
        if not tokens:
            return ""
        result = self._translator.translate_batch(
            [tokens],
            beam_size=2,
            max_decoding_length=max_new_tokens,
            no_repeat_ngram_size=1,
            repetition_penalty=2.0,
        )
        return self._clean_output(self._tokenizer.decode(result[0].hypotheses[0]))

    def translate(self, text: str, context: str = "", max_new_tokens: int = 160) -> str:
        """Translate a single utterance; context is currently not consumed by this MT model."""
        if not isinstance(text, str):
            raise TypeError("Input must be a string.")
        text = " ".join(text.split()).strip()
        if not text:
            return ""
        if not isinstance(max_new_tokens, int) or max_new_tokens < 1:
            raise ValueError("max_new_tokens must be a positive integer.")

        self._load()
        pieces = self._tokenizer.encode(f"<2ar> {text}", out_type=str)
        if len(pieces) <= 480:
            return self._translate_chunk(text, max_new_tokens)

        chunks: list[str] = []
        current: list[str] = []
        for sentence in re.split(r"(?<=[.!?])\s+", text):
            candidate = " ".join(current + [sentence])
            if current and len(self._tokenizer.encode(f"<2ar> {candidate}", out_type=str)) > 480:
                chunks.append(" ".join(current))
                current = [sentence]
            else:
                current.append(sentence)
        if current:
            chunks.append(" ".join(current))
        if len(chunks) == 1:
            raise ValueError("Unable to split input within MADLAD's 480-token source limit.")
        return " ".join(self._translate_chunk(chunk, max_new_tokens) for chunk in chunks)

