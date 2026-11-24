import re
import json
from pathlib import Path

import numpy as np
import onnxruntime as ort
import sentencepiece as spm


class TranslatorEngine:

    def __init__(self, model_dir=None, max_new_tokens=120):

        self.base = Path(model_dir) if model_dir else (
            Path(__file__).resolve().parent
            / "models"
            / "opus_en_ar"
        )

        self.model = self.base / "int8"
        self.max_new_tokens = max_new_tokens

        if not self.base.exists():
            raise FileNotFoundError(
                f"Model directory not found: {self.base}"
            )

        with open(
            self.base / "vocab.json",
            encoding="utf-8"
        ) as f:
            self.vocab = json.load(f)

        self.inverse = {
            value: key
            for key, value in self.vocab.items()
        }

        self.unk_id = self.vocab["<unk>"]
        self.eos_id = self.vocab["</s>"]
        self.pad_id = self.vocab["<pad>"]

        if len(self.vocab) != int(json.loads((self.base / "config.json").read_text(encoding="utf-8"))["vocab_size"]):
            raise ValueError("vocab.json size does not match model config vocab_size.")

        self.src = spm.SentencePieceProcessor(
            model_file=str(self.base / "source.spm")
        )

        self.tgt = spm.SentencePieceProcessor(
            model_file=str(self.base / "target.spm")
        )


        self.encoder = ort.InferenceSession(
            str(self.model / "encoder_model.onnx"),
            providers=["CPUExecutionProvider"]
        )

        self.decoder = ort.InferenceSession(
            str(self.model / "decoder_model.onnx"),
            providers=["CPUExecutionProvider"]
        )

        self.decoder_past = ort.InferenceSession(
            str(self.model / "decoder_with_past_model.onnx"),
            providers=["CPUExecutionProvider"]
        )

        self.decoder_past_inputs = {
            item.name
            for item in self.decoder_past.get_inputs()
        }

        print("TranslatorEngine loaded successfully.")

    @staticmethod
    def normalize_text(text):
        if not isinstance(text, str):
            raise TypeError("Input must be a string.")

        text = text.replace("\n", " ")
        text = re.sub(r"\s+", " ", text)

        return text.strip()

    def translate(self, text, max_new_tokens=None):
        text = self.normalize_text(text)

        if not text:
            return ""

        limit = (
            max_new_tokens
            if max_new_tokens is not None
            else self.max_new_tokens
        )

        if not isinstance(limit, int) or limit < 1:
            raise ValueError(
                "max_new_tokens must be a positive integer."
            )

        pieces = self.src.encode(
            text,
            out_type=str
        )

        if len(pieces) > 510:
            segments = re.split(r"(?<=[.!?])\s+", text)
            chunks = []
            current = []
            for segment in segments:
                candidate = " ".join(current + [segment])
                if len(self.src.encode(candidate, out_type=str)) <= 510:
                    current.append(segment)
                    continue
                if current:
                    chunks.append(" ".join(current))
                    current = []
                words = segment.split()
                for word in words:
                    candidate = " ".join(current + [word])
                    if current and len(self.src.encode(candidate, out_type=str)) > 510:
                        chunks.append(" ".join(current))
                        current = [word]
                    else:
                        current.append(word)
            if current:
                chunks.append(" ".join(current))
            if len(chunks) <= 1:
                raise ValueError("Unable to split input within the model's 512-token source limit.")
            return " ".join(self.translate(chunk, max_new_tokens=limit) for chunk in chunks)

        ids = [
            self.vocab.get(piece, self.unk_id)
            for piece in pieces
        ]

        ids.append(self.eos_id)

        input_ids = np.array(
            [ids],
            dtype=np.int64
        )

        mask = np.ones_like(input_ids)

        hidden = self.encoder.run(
            None,
            {
                "input_ids": input_ids,
                "attention_mask": mask
            }
        )[0]

        outputs = self.decoder.run(
            None,
            {
                "input_ids": np.array(
                    [[self.pad_id]],
                    dtype=np.int64
                ),
                "encoder_hidden_states": hidden,
                "encoder_attention_mask": mask
            }
        )

        next_id = int(
            np.argmax(outputs[0][0, -1])
        )

        if next_id == self.eos_id:
            return ""

        output_names = [
            item.name
            for item in self.decoder.get_outputs()
        ]

        output_map = dict(
            zip(output_names, outputs)
        )

        past = {}

        for name, value in output_map.items():
            if name.startswith("present."):
                new_name = name.replace(
                    "present.",
                    "past_key_values."
                )
                past[new_name] = value

        missing = self.decoder_past_inputs - (
            {"input_ids", "encoder_attention_mask"}
            | set(past.keys())
        )

        if missing:
            raise RuntimeError(
                f"Missing KV Cache inputs: {sorted(missing)}"
            )

        generated = []

        for step in range(limit):

            if next_id == self.eos_id:
                break

            generated.append(next_id)

            if step == limit - 1:
                break

            feeds = {
                "input_ids": np.array(
                    [[next_id]],
                    dtype=np.int64
                ),
                "encoder_attention_mask": mask
            }

            feeds.update(past)

            outputs = self.decoder_past.run(
                None,
                feeds
            )

            next_id = int(
                np.argmax(outputs[0][0, -1])
            )

            output_names = [
                item.name
                for item in self.decoder_past.get_outputs()
            ]

            output_map = dict(
                zip(output_names, outputs)
            )

            new_past = {}

            for name, value in output_map.items():

                if name.startswith("present."):
                    new_name = name.replace(
                        "present.",
                        "past_key_values."
                    )

                    new_past[new_name] = value

            past.update(new_past)

        tokens = [
            self.inverse.get(token_id, "<missing>")
            for token_id in generated
        ]

        return self.tgt.decode(tokens)

    def translate_batch(self, texts, max_new_tokens=None):
        if not isinstance(texts, (list, tuple)):
            raise TypeError("texts must be a list or tuple.")

        results = []

        for index, text in enumerate(texts):
            try:
                translation = self.translate(
                    text,
                    max_new_tokens=max_new_tokens
                )

                results.append({
                    "index": index,
                    "source": text,
                    "translation": translation,
                    "error": None
                })

            except Exception as exc:
                results.append({
                    "index": index,
                    "source": text,
                    "translation": None,
                    "error": f"{type(exc).__name__}: {exc}"
                })

        return results

    def __call__(self, text, max_new_tokens=None):
        return self.translate(text, max_new_tokens)


if __name__ == "__main__":

    engine = TranslatorEngine()

    samples = [
        "Hello, how are you?",
        "I will protect you.",
        "What are you doing here?"
    ]

    for sample in samples:
        print("\nEN:", sample)
        print("AR:", engine.translate(sample))
