from pathlib import Path

import pytest

from translator_engine.adapters import (
    IndexTranslateAdapter,
    TranslationOutputError,
)


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _adapter():
    return IndexTranslateAdapter(
        model_path=Path("index-test.gguf"),
        server_path=Path("llama-server-test.exe"),
        port=9941,
    )


def _completion(text, finish_reason="stop"):
    return {
        "choices": [{"finish_reason": finish_reason,
                     "message": {"content": text}}],
        "timings": {"prompt_n": 100, "prompt_ms": 50,
                    "predicted_n": 20, "predicted_ms": 100},
    }


def test_index_batch_parses_inline_ids_and_preserves_punctuation(monkeypatch):
    adapter = _adapter()
    monkeypatch.setattr(adapter, "_ensure_server", lambda: None)
    captured = {}

    def post(payload, timeout=1800):
        captured["payload"] = payload
        return _Response(_completion(
            "1. هآآه... 2. تبدو منهكًا يا ملكي الشاب. 3. بالطبع. "
            "4. كان عمي يلقي عليّ محاضرة لا تنتهي..."
        ))

    monkeypatch.setattr(adapter, "_post_chat_completion", post)
    result = adapter.translate_many([
        {"text": "HAAH...", "context": ""},
        {"text": "YOU SEEM EXHAUSTED, YOUNG MONARCH.", "context": "Dialogue: HAAH..."},
        {"text": "OBVIOUSLY.", "context": "Dialogue: YOU SEEM EXHAUSTED, YOUNG MONARCH."},
        {"text": "MY UNCLE WAS LECTURING ME FOR FOREVER...", "context": "Dialogue: OBVIOUSLY."},
    ])

    assert result == [
        "هآآه...", "تبدو منهكًا يا ملكي الشاب.", "بالطبع.",
        "كان عمي يلقي عليّ محاضرة لا تنتهي...",
    ]
    payload = captured["payload"]
    assert payload["temperature"] == 0
    assert payload["top_p"] == 1
    assert payload["max_tokens"] == 256
    assert payload["chat_template_kwargs"] == {"enable_thinking": False}
    assert "Dialogue: HAAH..." in payload["messages"][0]["content"]


@pytest.mark.parametrize("text", [
    "2. الثاني. 1. الأول.",
    "1. الأول. 3. الثالث.",
    "1. first only.",
])
def test_index_batch_rejects_missing_or_reordered_ids(monkeypatch, text):
    adapter = _adapter()
    monkeypatch.setattr(adapter, "_ensure_server", lambda: None)
    monkeypatch.setattr(adapter, "_post_chat_completion",
                        lambda payload, timeout=1800: _Response(_completion(text)))
    with pytest.raises(TranslationOutputError):
        adapter.translate_many([
            {"text": "First source.", "context": ""},
            {"text": "Second source.", "context": "Dialogue: First source."},
        ])


def test_index_batch_falls_back_before_inference_when_prompt_is_too_large(monkeypatch):
    adapter = _adapter()
    calls = []
    monkeypatch.setattr(adapter, "_ensure_server", lambda: calls.append("ensure"))
    with pytest.raises(TranslationOutputError, match="budget"):
        adapter.translate_many([
            {"text": "One.", "context": "x" * 2300},
            {"text": "Two.", "context": "y" * 2300},
        ])
    assert calls == []
