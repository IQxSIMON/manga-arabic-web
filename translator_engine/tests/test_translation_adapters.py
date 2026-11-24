import requests

from translator_engine.adapters import (
    BalancedAutomaticTranslator,
    IndexTranslateAdapter,
    TranslationOutputError,
    TranslateGemmaAdapter,
    _configured_llama_device_args,
    _configured_llama_thread_counts,
    _has_latin_residue,
    _is_implausibly_expanded_translation,
    _is_usable_arabic_translation,
    _repair_latinized_source_names,
)


def test_optional_llama_device_selection_is_safe_and_off_by_default(monkeypatch):
    monkeypatch.delenv("MANGA_LLAMA_DEVICE", raising=False)
    assert _configured_llama_device_args() == []

    monkeypatch.setenv("MANGA_LLAMA_DEVICE", "Vulkan1")
    assert _configured_llama_device_args() == ["--device", "Vulkan1"]

    monkeypatch.setenv("MANGA_LLAMA_DEVICE", "Vulkan1;malicious")
    assert _configured_llama_device_args() == []


def test_llama_threads_use_available_cores_and_allow_safe_override(monkeypatch):
    monkeypatch.setattr("translator_engine.adapters.os.cpu_count", lambda: 4)
    monkeypatch.delenv("MANGA_LLAMA_THREADS", raising=False)
    monkeypatch.delenv("MANGA_LLAMA_THREADS_BATCH", raising=False)
    assert _configured_llama_thread_counts() == (4, 4)
    monkeypatch.setenv("MANGA_LLAMA_THREADS", "3")
    assert _configured_llama_thread_counts() == (3, 3)


def test_cuda_launcher_defaults_to_six_gpu_layers_and_two_slots(monkeypatch):
    import importlib
    import tools.start_index_cuda_server as launcher

    monkeypatch.delenv("MANGA_LLAMA_GPU_LAYERS", raising=False)
    assert importlib.reload(launcher).GPU_LAYERS == 6
    monkeypatch.setenv("MANGA_LLAMA_GPU_LAYERS", "4")
    assert importlib.reload(launcher).GPU_LAYERS == 4


def test_cuda_launcher_configures_parallel_slots_without_changing_translation_policy(monkeypatch):
    import importlib
    import tools.start_index_cuda_server as launcher

    monkeypatch.delenv("MANGA_LLAMA_PARALLEL", raising=False)
    monkeypatch.delenv("MANGA_LLAMA_CTX_SIZE", raising=False)
    launcher = importlib.reload(launcher)
    assert launcher.PARALLEL_SLOTS == 2
    assert launcher.CONTEXT_SIZE == 3072
    monkeypatch.setenv("MANGA_LLAMA_PARALLEL", "3")
    monkeypatch.setenv("MANGA_LLAMA_CTX_SIZE", "8192")
    launcher = importlib.reload(launcher)
    assert launcher.PARALLEL_SLOTS == 3
    assert launcher.CONTEXT_SIZE == 8192


def test_index_llama_api_key_persists_across_adapter_instances(tmp_path):
    server_path = tmp_path / "bin" / "llama-server.exe"
    first = IndexTranslateAdapter(model_path=tmp_path / "index.gguf", server_path=server_path, port=9937)
    second = IndexTranslateAdapter(model_path=tmp_path / "index.gguf", server_path=server_path, port=9937)

    first_key = first._get_api_key()
    assert len(first_key) >= 32
    assert second._get_api_key() == first_key
    assert (server_path.parent / "index_translate_9b-api-key.txt").read_text(encoding="ascii") == first_key


def test_index_adapter_rejects_existing_server_with_stale_api_key(monkeypatch, tmp_path):
    import requests
    import pytest

    engine = IndexTranslateAdapter(model_path=tmp_path / "index.gguf", server_path=tmp_path / "llama-server.exe", port=9938)
    class Unauthorized:
        status_code = 401
    monkeypatch.setattr(
        requests.Session, "get", lambda _session, *_args, **_kwargs: Unauthorized()
    )

    with pytest.raises(RuntimeError, match="يستخدم مفتاحاً قديماً"):
        engine._server_accepts_api_key()


def test_index_adapter_allows_slow_cpu_inference_without_changing_generation(monkeypatch, tmp_path):
    engine = IndexTranslateAdapter(
        model_path=tmp_path / "index.gguf",
        server_path=tmp_path / "llama-server.exe",
        port=9939,
    )

    class Response:
        status_code = 200
        def raise_for_status(self): pass

    class Session:
        def post(self, url, json, headers, timeout):
            assert url.endswith("/v1/chat/completions")
            assert timeout == 1800
            return Response()

    monkeypatch.setattr(engine, "_http_session", lambda: Session())
    assert engine._post_chat_completion({"messages": []}).status_code == 200


def test_replacement_characters_make_arabic_output_invalid():
    assert _is_usable_arabic_translation("من الأفضل أن نتوقف الآن.")
    assert not _is_usable_arabic_translation("من الأفضل � نتوقف الآن.")


def test_index_retries_corrupted_unicode_and_rejects_it_if_retry_fails(monkeypatch, tmp_path):
    import pytest

    class Response:
        def __init__(self, content):
            self.result = {"choices": [{"finish_reason": "stop", "message": {"content": content}}]}

        def json(self):
            return self.result

    engine = IndexTranslateAdapter(
        model_path=tmp_path / "index.gguf", server_path=tmp_path / "llama-server.exe", port=9940,
    )
    monkeypatch.setattr(engine, "_ensure_server", lambda: None)
    invalid = "\u0647\u0630\u0627 \ufffd \u0639\u0646\u0648\u0627\u0646."
    valid = "\u0647\u0630\u0627 \u0639\u0646\u0648\u0627\u0646 \u0648\u0627\u0636\u062d."
    responses = iter([Response(invalid), Response(valid)])
    monkeypatch.setattr(engine, "_post_chat_completion", lambda _payload: next(responses))
    assert engine.translate("This is a clear title.") == valid

    responses = iter([Response(invalid)] * 3)
    monkeypatch.setattr(engine, "_post_chat_completion", lambda _payload: next(responses))
    with pytest.raises(TranslationOutputError, match="corrupted"):
        engine.translate("This is a clear title.")

def test_index_prompt_uses_translation_instruction_and_previous_dialogue_context():
    messages = IndexTranslateAdapter.build_messages("I won't leave you behind.", "Stay close to me.")
    assert len(messages) == 1
    prompt = messages[0]["content"]
    assert "fluent, natural Modern Standard Arabic" in prompt
    assert "strictly Modern Standard Arabic, never colloquial dialect" in prompt
    assert "idiomatic, concise phrasing without omitting meaning" in prompt
    assert "For short reactions, choose a natural conversational Arabic equivalent in context" in prompt
    assert "Do not invent facts, repeat earlier dialogue, or guess missing OCR words" in prompt
    assert "CURRENT TEXT:" in prompt
    assert "CONTEXT (reference only; do not translate):" in prompt
    assert "Stay close to me." in prompt
    assert "I won't leave you behind." in prompt


def test_index_retries_overexpanded_fragment_without_prior_context(monkeypatch, tmp_path):
    class Response:
        def __init__(self, content, finish_reason="stop"):
            self.result = {"choices": [{"finish_reason": finish_reason,
                                         "message": {"content": content}}]}

        def json(self):
            return self.result

    engine = IndexTranslateAdapter(
        model_path=tmp_path / "index.gguf",
        server_path=tmp_path / "llama-server.exe",
        port=9940,
    )
    monkeypatch.setattr(engine, "_ensure_server", lambda: None)
    calls = []
    responses = iter([
        Response("\u0643\u0644\u0627\u0645 " * 45),
        Response("\u0625\u0630\u0627 \u0643\u0646\u062a"),
    ])

    def post(payload):
        calls.append(payload)
        return next(responses)

    monkeypatch.setattr(engine, "_post_chat_completion", post)
    translated = engine.translate("IF YOU", context="Earlier dialogue that must not be copied.")

    assert translated == "\u0625\u0630\u0627 \u0643\u0646\u062a"
    assert "CONTEXT (reference only" in calls[0]["messages"][0]["content"]
    assert "CONTEXT (reference only" not in calls[1]["messages"][0]["content"]
    assert calls[1]["max_tokens"] <= 128


def test_translation_integrity_rejects_expansion_and_latin_residue():
    assert _is_implausibly_expanded_translation(
        "NEARBY, PAJU FIELD-TYPE DUNGEON",
        "قد يكون هذا مؤلماً، ثم نواصل التدريب ونتحدث عن أمور أخرى كثيرة لا صلة لها بالمصدر.",
    )
    assert _is_implausibly_expanded_translation(
        "TRAINING TO BECOME A FORMIDABLE COMBATANT.",
        "هذا التدريب ألغي! واجهة المتجر جميلة، أيها التمساح الغبي، فلنواصل الحديث عن أمور كثيرة، ثم نذهب إلى المتجر ونتحدث عن التدريب مجدداً وعن كل ما حدث في وقت سابق من القصة.",
    )
    assert not _is_implausibly_expanded_translation(
        "BEFORE FACING LEE MINSUNG...", "قبل مواجهة لي مين سونغ..."
    )
    assert _has_latin_residue("هانتر بايك mAho.")
    assert not _has_latin_residue("هانتر بايك ميهو.")


def test_latinized_name_is_repaired_from_matching_source_spelling():
    assert _repair_latinized_source_names(
        "HUNTER BAEK MIHO.", "هانتر بايك mAho."
    ) == "هانتر بايك ميهو."
    assert _repair_latinized_source_names("HUNTER BAEK MIHO.", "هانتر بايك ho.") == "هانتر بايك ميهو."
    assert _repair_latinized_source_names("HUNTER BAEK MIHO.", "هانتر بايكho.") == "هانتر بايكميهو."
    assert _repair_latinized_source_names(
        "There is no name here.", "هذا blah غير واضح."
    ) is None


def test_index_retries_latin_residue_without_context_and_rejects_bad_retry(monkeypatch, tmp_path):
    import pytest

    class Response:
        def __init__(self, content):
            self.result = {"choices": [{"finish_reason": "stop",
                                         "message": {"content": content}}]}

        def json(self):
            return self.result

    engine = IndexTranslateAdapter(
        model_path=tmp_path / "index.gguf", server_path=tmp_path / "llama-server.exe",
        port=9940,
    )
    monkeypatch.setattr(engine, "_ensure_server", lambda: None)
    calls = []
    responses = iter([Response("هانتر بايك mAho."), Response("هانتر بايك ميهو.")])

    def post(payload):
        calls.append(payload)
        return next(responses)

    monkeypatch.setattr(engine, "_post_chat_completion", post)
    assert engine.translate("HUNTER BAEK MIHO.", context="Prior dialogue.") == "هانتر بايك ميهو."
    assert len(calls) == 1

    unrelated_latin = "\u0647\u0627\u0646\u062a\u0631 xyz."
    responses = iter([Response(unrelated_latin)] * 3)
    monkeypatch.setattr(engine, "_post_chat_completion", lambda _payload: next(responses))
    with pytest.raises(TranslationOutputError, match="incomplete"):
        engine.translate("HUNTER BAEK MIHO.")


def test_index_retries_page_level_context_echo_at_tighter_threshold(monkeypatch, tmp_path):
    class Response:
        def __init__(self, content):
            self.result = {"choices": [{"finish_reason": "stop",
                                         "message": {"content": content}}]}

        def json(self):
            return self.result

    engine = IndexTranslateAdapter(
        model_path=tmp_path / "index.gguf", server_path=tmp_path / "llama-server.exe",
        port=9940,
    )
    monkeypatch.setattr(engine, "_ensure_server", lambda: None)
    calls = []
    responses = iter([
        Response("هذا التدريب ألغي! واجهة المتجر جميلة، أيها التمساح الغبي، فلنواصل الحديث عن أمور كثيرة، ثم نذهب إلى المتجر ونتحدث عن التدريب مجدداً وعن كل ما حدث في وقت سابق من القصة."),
        Response("التدريب لصقل مهاراتي القتالية."),
    ])

    def post(payload):
        calls.append(payload)
        return next(responses)

    monkeypatch.setattr(engine, "_post_chat_completion", post)
    assert engine.translate(
        "TRAINING TO BECOME A FORMIDABLE COMBATANT.", context="Earlier chapter dialogue."
    ) == "التدريب لصقل مهاراتي القتالية."
    assert len(calls) == 2


def test_index_does_not_expand_token_limit_for_a_truncated_short_fragment(monkeypatch, tmp_path):
    class Response:
        def __init__(self, content, finish_reason="stop"):
            self.result = {"choices": [{"finish_reason": finish_reason,
                                         "message": {"content": content}}]}

        def json(self):
            return self.result

    engine = IndexTranslateAdapter(
        model_path=tmp_path / "index.gguf",
        server_path=tmp_path / "llama-server.exe",
        port=9940,
    )
    monkeypatch.setattr(engine, "_ensure_server", lambda: None)
    calls = []
    responses = iter([
        Response("\u0644\u0627", finish_reason="length"),
        Response("\u0625\u0630\u0627 \u0643\u0646\u062a"),
    ])

    def post(payload):
        calls.append(payload)
        return next(responses)

    monkeypatch.setattr(engine, "_post_chat_completion", post)
    assert engine.translate("IF YOU", context="Earlier context.") == "\u0625\u0630\u0627 \u0643\u0646\u062a"
    assert len(calls) == 2
    assert calls[1]["max_tokens"] <= 128


def test_index_bounds_output_limit_recovery_for_noisy_ocr_sentence(monkeypatch, tmp_path):
    class Response:
        def __init__(self, content, finish_reason="stop"):
            self.result = {"choices": [{"finish_reason": finish_reason,
                                         "message": {"content": content}}]}

        def json(self):
            return self.result

    engine = IndexTranslateAdapter(
        model_path=tmp_path / "index.gguf",
        server_path=tmp_path / "llama-server.exe",
        port=9940,
    )
    monkeypatch.setattr(engine, "_ensure_server", lambda: None)
    calls = []
    responses = iter([
        Response("\u0644\u0627", finish_reason="length"),
        Response("\u0647\u0644 \u0645\u0646 \u0627\u0644\u0645\u0641\u062a\u0631\u0636 \u0623\u0646 \u0623\u062a\u0635\u0631\u0641 \u0647\u0643\u0630\u0627\u061f"),
    ])

    def post(payload):
        calls.append(payload)
        return next(responses)

    monkeypatch.setattr(engine, "_post_chat_completion", post)
    source = "I-I'M SUPPOSED TO-HSNO O O PS LIKE THIS?"
    translated = engine.translate(source, context="Earlier dialogue for context.")

    assert translated == "\u0647\u0644 \u0645\u0646 \u0627\u0644\u0645\u0641\u062a\u0631\u0636 \u0623\u0646 \u0623\u062a\u0635\u0631\u0641 \u0647\u0643\u0630\u0627\u061f"
    assert len(calls) == 2
    assert calls[0]["max_tokens"] < 512
    assert calls[1]["max_tokens"] <= 128
    assert "CONTEXT (reference only" not in calls[1]["messages"][0]["content"]


def test_index_adapter_records_llama_inference_timings_without_changing_translation(caplog, tmp_path):
    caplog.set_level("INFO")
    engine = IndexTranslateAdapter(
        model_path=tmp_path / "index.gguf",
        server_path=tmp_path / "llama-server.exe",
        port=9939,
    )

    engine._record_inference_timings({"timings": {
        "prompt_n": 120, "prompt_ms": 4000,
        "predicted_n": 32, "predicted_ms": 8000,
    }})

    assert engine.last_inference_timings == {
        "prompt_n": 120, "prompt_ms": 4000,
        "predicted_n": 32, "predicted_ms": 8000,
    }
    assert "prompt_tokens_per_sec=30.0" in caplog.text
    assert "generated_tokens_per_sec=4.0" in caplog.text


def test_index_adapter_ignores_missing_or_invalid_llama_timings(tmp_path):
    engine = IndexTranslateAdapter(
        model_path=tmp_path / "index.gguf",
        server_path=tmp_path / "llama-server.exe",
        port=9940,
    )

    engine._record_inference_timings({"timings": {"prompt_n": -1, "predicted_n": "unknown"}})

    assert engine.last_inference_timings is None


def test_translategemma_uses_plain_text_for_llama_template_kwargs():
    messages = TranslateGemmaAdapter.build_messages("The floor boss is defeated.", "Ignored context")
    assert messages == [{"role": "user", "content": "The floor boss is defeated."}]


def test_router_tries_translategemma_after_index_failure_without_parallel_models():
    calls = []

    class FakeAdapter:
        def __init__(self, engine_id, output=None, fail=False):
            self.engine_id = engine_id
            self.model_name = engine_id
            self.model_path = f"{engine_id}.gguf"
            self.output = output
            self.fail = fail
            self.running = False

        def available(self):
            return True

        def translate(self, text, context=""):
            calls.append((self.engine_id, "translate"))
            if self.fail:
                raise RuntimeError("simulated load failure")
            self.running = True
            return self.output

        def shutdown(self):
            calls.append((self.engine_id, "shutdown"))
            self.running = False

    index = FakeAdapter("index_translate_9b", fail=True)
    gemma = FakeAdapter("translategemma_4b", output="انتهى زعيم الطابق.")
    router = BalancedAutomaticTranslator(index_adapter=index, translategemma_adapter=gemma)
    assert router.preferred_engine_id() == "index_translate_9b"
    result = router.translate("The floor boss is defeated.")
    assert result == "انتهى زعيم الطابق."
    assert router.last_engine_id == "translategemma_4b"
    assert router.preferred_engine_id() == "translategemma_4b"
    assert "simulated load failure" in router.status()["index_translate_9b"]["session_error"]
    assert calls == [
        ("index_translate_9b", "translate"), ("index_translate_9b", "shutdown"),
        ("translategemma_4b", "translate"),
    ]
    router.shutdown()
    assert calls[-1] == ("translategemma_4b", "shutdown")


def test_router_reports_unavailable_when_both_new_models_are_missing():
    import pytest

    class MissingAdapter:
        def __init__(self, engine_id):
            self.engine_id = engine_id
            self.model_name = engine_id
            self.model_path = f"{engine_id}.gguf"

        def available(self):
            return False

    router = BalancedAutomaticTranslator(
        index_adapter=MissingAdapter("index_translate_9b"),
        translategemma_adapter=MissingAdapter("translategemma_4b"),
    )
    assert router.preferred_engine_id() == "unavailable"
    with pytest.raises(RuntimeError, match="Index أو TranslateGemma"):
        router.translate("Hello.")


def test_manual_engine_router_allows_independent_inference_requests_to_overlap():
    import concurrent.futures
    import threading

    barrier = threading.Barrier(2)

    class ConcurrentAdapter:
        engine_id = "index_translate_9b"
        model_name = "Index test adapter"

        @staticmethod
        def available():
            return True

        def translate(self, text, context=""):
            barrier.wait(timeout=3)
            return "ترجمة " + text

    router = BalancedAutomaticTranslator(index_adapter=ConcurrentAdapter(), allow_fallback=False)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(
            lambda text: router.translate_with("index_translate_9b", text),
            ("one", "two"),
        ))
    assert results == ["ترجمة one", "ترجمة two"]


def test_index_only_router_does_not_register_a_fallback_engine():
    class FakeAdapter:
        engine_id = "index_translate_9b"
        model_name = "Index fixture"
        model_path = "index.gguf"

        def available(self):
            return False

    router = BalancedAutomaticTranslator(
        index_adapter=FakeAdapter(), allow_fallback=False
    )

    assert list(router.status()) == ["index_translate_9b"]
    assert router.preferred_engine_id() == "unavailable"


def test_manual_engine_translation_does_not_fall_back_to_the_other_model():
    import pytest

    calls = []

    class FakeAdapter:
        def __init__(self, engine_id, output=None, fail=False):
            self.engine_id = engine_id
            self.model_name = engine_id
            self.model_path = f"{engine_id}.gguf"
            self.output = output
            self.fail = fail

        def available(self):
            return True

        def translate(self, text, context=""):
            calls.append((self.engine_id, text, context))
            if self.fail:
                raise RuntimeError("model unavailable")
            return self.output

        def shutdown(self):
            calls.append((self.engine_id, "shutdown"))

    index = FakeAdapter("index_translate_9b", output="ترجمة إندكس")
    gemma = FakeAdapter("translategemma_4b", output="ترجمة غيما")
    router = BalancedAutomaticTranslator(index_adapter=index, translategemma_adapter=gemma)

    assert router.translate_with("translategemma_4b", "Hello.", "She is upset.") == "ترجمة غيما"
    assert calls == [("translategemma_4b", "Hello.", "She is upset.")]
    assert router.last_engine_id == "translategemma_4b"

    calls.clear()
    failing_gemma = FakeAdapter("translategemma_4b", fail=True)
    router = BalancedAutomaticTranslator(index_adapter=index, translategemma_adapter=failing_gemma)
    with pytest.raises(RuntimeError, match="model unavailable"):
        router.translate_with("translategemma_4b", "Hello.")
    assert [call[0] for call in calls] == ["translategemma_4b", "translategemma_4b"]


def test_index_server_diagnostic_checks_auth_without_starting_or_exposing_key(monkeypatch, tmp_path):
    engine = IndexTranslateAdapter(
        model_path=tmp_path / "index.gguf",
        server_path=tmp_path / "llama-server.exe",
        port=9939,
    )
    private_key = "k" * 48
    engine._api_key = private_key

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {"data": [{"id": "Index-Translate-9B"}]}

    class Session:
        def __init__(self):
            self.calls = []

        def get(self, url, *, headers, timeout):
            self.calls.append((url, headers, timeout))
            return Response()

    session = Session()
    monkeypatch.setattr(engine, "_http_session", lambda: session)
    report = engine.diagnose_server()

    assert report == {
        "status": "ready",
        "message": "خادم Index المحلي متصل وواجهة النماذج تستجيب.",
        "model_count": 1,
    }
    assert session.calls == [(
        "http://127.0.0.1:9939/v1/models",
        {"Authorization": f"Bearer {private_key}"},
        2,
    )]
    assert private_key not in str(report)
    assert engine._process is None


def test_index_server_diagnostic_reports_stale_server_auth_failure(monkeypatch, tmp_path):
    engine = IndexTranslateAdapter(
        model_path=tmp_path / "index.gguf",
        server_path=tmp_path / "llama-server.exe",
        port=9940,
    )
    engine._api_key = "k" * 48

    class Response:
        status_code = 401

    class Session:
        @staticmethod
        def get(_url, **_kwargs):
            return Response()

    monkeypatch.setattr(engine, "_http_session", lambda: Session())
    report = engine.diagnose_server()
    assert report["status"] == "authentication_error"
    assert "أوقف نسخة llama-server القديمة" in report["message"]
    assert "k" * 48 not in str(report)


def test_index_server_diagnostic_reports_not_running_without_creating_key(monkeypatch, tmp_path):
    engine = IndexTranslateAdapter(
        model_path=tmp_path / "index.gguf",
        server_path=tmp_path / "llama-server.exe",
        port=9942,
    )

    class Session:
        @staticmethod
        def get(_url, **_kwargs):
            raise requests.ConnectionError("connection refused")

    monkeypatch.setattr(engine, "_http_session", lambda: Session())
    report = engine.diagnose_server()
    assert report["status"] == "not_running"
    assert not engine._api_key_path.exists()
    assert engine._process is None

