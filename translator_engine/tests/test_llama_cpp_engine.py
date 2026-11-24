from translator_engine.llama_cpp_engine import LlamaCppTranslator


def test_qwen_prompt_preserves_dialogue_context_and_restricts_output():
    messages = LlamaCppTranslator.build_messages("You have some nerve.", "I warned you before.")
    assert "natural Modern Standard Arabic" in messages[0]["content"]
    assert "I warned you before." in messages[1]["content"]
    assert "You have some nerve." in messages[1]["content"]
    assert messages[1]["content"].endswith("/no_think")


def test_qwen_proofreader_prompt_compares_source_and_draft_with_dialogue_examples():
    messages = LlamaCppTranslator.build_proofread_messages(
        "You have some nerve!", "لديك بعض الأعصاب!", "You threatened him."
    )
    assert "English source is authoritative" in messages[0]["content"]
    assert "You're dead meat. -> ستكون نهايتك!" in messages[0]["content"]
    assert "You have some nerve!" in messages[1]["content"]
    assert "لديك بعض الأعصاب!" in messages[1]["content"]
    assert "You threatened him." in messages[1]["content"]
    assert messages[1]["content"].endswith("/no_think")


def test_qwen_adapter_calls_only_loopback_server(monkeypatch, tmp_path):
    import requests
    engine = LlamaCppTranslator(model_path="model.gguf", server_path=tmp_path / "llama-server.exe", port=9931)
    monkeypatch.setattr(engine, "_ensure_server", lambda: None)
    class Response:
        def raise_for_status(self): pass
        def json(self): return {"choices":[{"message":{"content":"  سأحميك.  "}}]}
    def fake_post(url, json, headers, timeout):
        assert url == "http://127.0.0.1:9931/v1/chat/completions"
        assert headers["Authorization"].startswith("Bearer ")
        assert json["messages"][1]["content"].endswith("/no_think")
        return Response()
    monkeypatch.setattr(requests, "post", fake_post)
    assert engine.translate("I will protect you.", "I will stay by your side.") == "سأحميك."


def test_qwen_proofreader_calls_only_loopback_server(monkeypatch, tmp_path):
    import requests
    engine = LlamaCppTranslator(model_path="model.gguf", server_path=tmp_path / "llama-server.exe", port=9932)
    monkeypatch.setattr(engine, "_ensure_server", lambda: None)
    class Response:
        def raise_for_status(self): pass
        def json(self): return {"choices":[{"message":{"content":"يا لك من جريء!"}}]}
    def fake_post(url, json, headers, timeout):
        assert url == "http://127.0.0.1:9932/v1/chat/completions"
        assert json["model"] == "model"
        return Response()
    monkeypatch.setattr(requests, "post", fake_post)
    assert engine.proofread("You have some nerve!", "لديك بعض الأعصاب!") == "يا لك من جريء!"


def test_engine_selection_api_rejects_deferred_engines_without_starting_them(monkeypatch):
    import app as web_app
    monkeypatch.setattr(web_app, "CURRENT_TRANSLATION_ENGINE", "index_translate_9b")
    monkeypatch.setattr(web_app, "QWEN_TRANSLATOR", None)
    client = web_app.app.test_client()
    changed = client.post("/api/translation-engine", json={"engine":"qwen3"})
    assert changed.status_code == 409
    assert changed.json["current"] == "index_translate_9b"
    assert web_app.QWEN_TRANSLATOR is None
    unavailable = client.post("/api/translation-engine", json={"engine":"translategemma_4b"})
    assert unavailable.status_code == 409


def test_proofreader_selection_requires_local_4b_model_and_is_off_by_default(monkeypatch, tmp_path):
    import app as web_app
    monkeypatch.setattr(web_app, "ENABLE_LOCAL_PROOFREADER", False)
    model = tmp_path / "qwen4b.gguf"
    model.touch()
    monkeypatch.setattr(web_app, "DEFAULT_PROOFREADER_MODEL", model)
    monkeypatch.setattr(web_app.LlamaCppTranslator, "available", lambda: True)
    client = web_app.app.test_client()
    state = client.get("/api/translation-engine").json["proofreader"]
    assert state["enabled"] is False
    assert state["available"] is True
    enabled = client.post("/api/translation-proofreader", json={"enabled": True})
    assert enabled.status_code == 200
    assert enabled.json["enabled"] is True
    disabled = client.post("/api/translation-proofreader", json={"enabled": False})
    assert disabled.json["enabled"] is False
