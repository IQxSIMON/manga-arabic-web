import app as manga_app
from translator_engine.adapters import TranslationOutputError


def _items():
    return [
        {"text": "First.", "context": "", "region_type": "speech_bubble",
         "region_index": 1, "chapter_title": "Test", "work_scope": "Test"},
        {"text": "Second.", "context": "Dialogue: First.", "region_type": "speech_bubble",
         "region_index": 2, "chapter_title": "Test", "work_scope": "Test"},
    ]


def test_app_group_uses_batch_outputs_in_input_order(monkeypatch):
    monkeypatch.setattr(manga_app, "CURRENT_TRANSLATION_ENGINE", "index_translate_9b")
    monkeypatch.setattr(manga_app, "_translate_index_prompt_batch",
                        lambda items: ["الأولى.", "الثانية."])
    calls = []

    def fake_translate(text, **kwargs):
        calls.append((text, kwargs))
        return kwargs.get("_pretranslated", "")

    monkeypatch.setattr(manga_app, "translate_text", fake_translate)
    results = manga_app._translate_index_region_group(_items())

    assert results == ["الأولى.", "الثانية."]
    assert [entry[0] for entry in calls] == ["First.", "Second."]
    assert [entry[1]["_pretranslated"] for entry in calls] == ["الأولى.", "الثانية."]


def test_app_group_falls_back_to_single_translation_on_bad_batch(monkeypatch):
    monkeypatch.setattr(manga_app, "CURRENT_TRANSLATION_ENGINE", "index_translate_9b")

    def invalid_batch(_items):
        raise TranslationOutputError("missing id")

    monkeypatch.setattr(manga_app, "_translate_index_prompt_batch", invalid_batch)
    calls = []

    def fake_translate(text, **kwargs):
        calls.append((text, kwargs))
        return f"ترجمة {text}"

    monkeypatch.setattr(manga_app, "translate_text", fake_translate)
    results = manga_app._translate_index_region_group(_items())

    assert results == ["ترجمة First.", "ترجمة Second."]
    assert len(calls) == 2
    assert all("_pretranslated" not in kwargs for _, kwargs in calls)


def test_app_group_does_not_batch_external_text(monkeypatch):
    monkeypatch.setattr(manga_app, "CURRENT_TRANSLATION_ENGINE", "index_translate_9b")
    batch_calls = []
    monkeypatch.setattr(manga_app, "_translate_index_prompt_batch",
                        lambda items: batch_calls.append(items))
    calls = []
    monkeypatch.setattr(manga_app, "translate_text",
                        lambda text, **kwargs: calls.append(text) or "ترجمة")
    items = _items()
    items[1]["region_type"] = "external_text"

    results = manga_app._translate_index_region_group(items)

    assert results == ["ترجمة", "ترجمة"]
    assert batch_calls == []
    assert calls == ["First.", "Second."]


def test_app_group_batches_only_uncached_regions(monkeypatch):
    monkeypatch.setattr(manga_app, "CURRENT_TRANSLATION_ENGINE", "index_translate_9b")
    monkeypatch.setattr(manga_app, "_model_glossary_entries", lambda *_args: [])
    monkeypatch.setattr(
        manga_app, "_translation_memory_scope",
        lambda text, *_args, **_kwargs: f"scope:{text}",
    )
    monkeypatch.setattr(
        manga_app.library_store, "get_memory",
        lambda _scope, text: "\u062a\u0631\u062c\u0645\u0629 \u0645\u062d\u0641\u0648\u0638\u0629" if text == "First." else None,
    )
    batch_calls = []
    monkeypatch.setattr(
        manga_app, "_translate_index_prompt_batch",
        lambda items: batch_calls.append([item["text"] for item in items])
        or ["\u062a\u0631\u062c\u0645\u0629 \u062c\u062f\u064a\u062f\u0629", "\u062a\u0631\u062c\u0645\u0629 \u062b\u0627\u0644\u062b\u0629"],
    )
    calls = []

    def fake_translate(text, **kwargs):
        calls.append((text, kwargs.get("_pretranslated")))
        return kwargs.get("_pretranslated") or "\u062a\u0631\u062c\u0645\u0629 \u0645\u062d\u0641\u0648\u0638\u0629"

    monkeypatch.setattr(manga_app, "translate_text", fake_translate)
    items = _items() + [{
        "text": "Third.", "context": "Dialogue: Second.",
        "region_type": "speech_bubble", "region_index": 3,
        "chapter_title": "Test", "work_scope": "Test",
    }]
    results = manga_app._translate_index_region_group(items)

    assert results == [
        "\u062a\u0631\u062c\u0645\u0629 \u0645\u062d\u0641\u0648\u0638\u0629",
        "\u062a\u0631\u062c\u0645\u0629 \u062c\u062f\u064a\u062f\u0629",
        "\u062a\u0631\u062c\u0645\u0629 \u062b\u0627\u0644\u062b\u0629",
    ]
    assert batch_calls == [["Second.", "Third."]]
    assert calls == [
        ("First.", None),
        ("Second.", "\u062a\u0631\u062c\u0645\u0629 \u062c\u062f\u064a\u062f\u0629"),
        ("Third.", "\u062a\u0631\u062c\u0645\u0629 \u062b\u0627\u0644\u062b\u0629"),
    ]


def test_app_group_uses_single_request_when_only_one_region_is_uncached(monkeypatch):
    monkeypatch.setattr(manga_app, "CURRENT_TRANSLATION_ENGINE", "index_translate_9b")
    monkeypatch.setattr(manga_app, "_model_glossary_entries", lambda *_args: [])
    monkeypatch.setattr(
        manga_app, "_translation_memory_scope",
        lambda text, *_args, **_kwargs: f"scope:{text}",
    )
    monkeypatch.setattr(
        manga_app.library_store, "get_memory",
        lambda _scope, text: "\u062a\u0631\u062c\u0645\u0629 \u0645\u062d\u0641\u0648\u0638\u0629" if text == "First." else None,
    )
    batch_calls = []
    monkeypatch.setattr(manga_app, "_translate_index_prompt_batch", lambda items: batch_calls.append(items))
    calls = []
    monkeypatch.setattr(
        manga_app, "translate_text",
        lambda text, **kwargs: calls.append((text, kwargs)) or "\u062a\u0631\u062c\u0645\u0629",
    )

    manga_app._translate_index_region_group(_items())

    assert batch_calls == []
    assert [text for text, _kwargs in calls] == ["First.", "Second."]
    assert all("_pretranslated" not in kwargs for _text, kwargs in calls)
