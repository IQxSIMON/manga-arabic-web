import app as web_app
import pytest
from translator_engine.idioms import translate_reviewed_phrase


def test_glossary_exact_override_and_scoped_translation_memory(monkeypatch, tmp_path):
    monkeypatch.setenv("MANGA_LIBRARY_DIR", str(tmp_path / "library"))
    monkeypatch.setattr(web_app, "CURRENT_TRANSLATION_ENGINE", "index_translate_9b")

    class FakeRouter:
        adapters = [type("Adapter", (), {"engine_id": "index_translate_9b", "model_path": "missing.gguf"})()]
        last_engine_id = "index_translate_9b"
        last_model_name = "Index fixture"

        def __init__(self):
            self.calls = []

        def preferred_engine_id(self):
            return "index_translate_9b"

        def translate_with(self, engine, text, context=""):
            self.calls.append((text, context))
            if text.casefold() == "silver fang":
                assert "Silver Fang = الناب الفضي" in context
                return "الناب الفضي"
            return f"ترجمة:{text}"

    fake = FakeRouter()
    monkeypatch.setattr(web_app, "BALANCED_TRANSLATOR", fake)
    client = web_app.app.test_client()

    added = client.post("/api/glossary", json={"source": "Silver Fang", "target": "الناب الفضي"})
    assert added.status_code == 201
    assert web_app.translate_text("silver fang", chapter_title="Series") == "الناب الفضي"
    assert len(fake.calls) == 1

    first = web_app.translate_text(
        "I owe you.", chapter_title="Series chapter 1", context="Wait over there."
    )
    repeated = web_app.translate_text(
        " I   owe you. ", chapter_title="Series chapter 1", context="  WAIT over there.  "
    )
    different_context = web_app.translate_text(
        "I owe you.", chapter_title="Series chapter 1", context="He helped her yesterday."
    )
    other_chapter = web_app.translate_text("I owe you.", chapter_title="Series chapter 2")
    assert first == repeated
    assert different_context == first
    assert other_chapter == first
    assert [call[0] for call in fake.calls] == [
        "silver fang", "I owe you.", "I owe you.", "I owe you."
    ]
    assert client.get("/api/glossary").json["terms"][0]["target_term"] == "الناب الفضي"


def test_reviewed_dialogue_phrases_improve_common_idioms_without_extra_context():
    assert translate_reviewed_phrase("You have some nerve showing your face here!") == "ما أوقحك! كيف تجرؤ على الظهور هنا؟"
    assert translate_reviewed_phrase("I thought I told you to stay away from me.") == "ألم أقل لك أن تبتعد عني؟"
    assert translate_reviewed_phrase("You can do it! I believe in you.") == "أنت قادر على ذلك! أنا أؤمن بك."
    assert translate_reviewed_phrase("CONGRATULATIONS ON DEFEATING THE FLOOR BOSS, EUGENE.") == "تهانينا يا يوجين على هزيمتك لزعيم الطابق!"
    assert translate_reviewed_phrase("KEEP UP THE GOOD WORK AND CONTINUE PROGRESSING!") == "أحسنت، واصل التقدم!"
    assert translate_reviewed_phrase("It's been half a year since you joined! Yet,") == "مرّ نصف عام على انضمامك! ومع ذلك..."
    assert translate_reviewed_phrase("You've been late after being late after being late!") == "تتأخرين مرةً تلو الأخرى!"
    assert translate_reviewed_phrase("Some kid?!") == "أمجرد طفل؟!"
    assert translate_reviewed_phrase("HOW LONG ARE YOU GOING TO KEEP ACTING LIKE SOME KID?!") == "إلى متى ستواصلين التصرف كطفلة؟!"
    assert translate_reviewed_phrase("IF YOU CAN'T EVEN BE MOTIVATED TO WORK,") == "إذا لم تستطيعي تحفيز نفسك على العمل،"
    assert translate_reviewed_phrase("IT'S A GREAT WAY TO PASS THE TIME!") == "طريقة رائعة لتمضية الوقت!"
    assert translate_reviewed_phrase("THAT'S EASY FOR YOU TO SAY.") == "يسهل عليك قول ذلك!"
    assert translate_reviewed_phrase("The boss's gone mad again...") == "لقد جنّ المدير مجدداً..."
    assert translate_reviewed_phrase("Geez, Reiwa energy and power harrassment come hand in hand, huh?") == "يا للعجب، حماسة عصر رييوا والتسلط الوظيفي يسيران جنباً إلى جنب، أليس كذلك؟"
    assert translate_reviewed_phrase("WANT TO GO OUT SOMEWHERE THIS WEEK- END?") == "أترغبين في الخروج إلى مكان ما في عطلة نهاية هذا الأسبوع؟"
    assert translate_reviewed_phrase("NOPE.") == "كلا."
    assert translate_reviewed_phrase("WE ALWAYS GET SWARMED WITH PICKUP ARTISTS WHEN I GO OUT WITH T'S YOU. ax") == "كلما خرجتُ معكِ، حاصرنا المتحرشون. هذا مزعج للغاية!"
    assert translate_reviewed_phrase("WHAT'S WIITH TAL FACE?") == "ما حكاية ذلك الوجه؟"
    assert translate_reviewed_phrase("HUH? IT'S MY FAULT?") == "ماذا؟ أهذا خطئي أنا؟"
    assert translate_reviewed_phrase("THAT'S NOT WHAT I MEANT!") == "لم أقصد ذلك!"
    assert translate_reviewed_phrase("THAT'S NOT WHAT IMENTI") == "لم أقصد ذلك!"
    assert translate_reviewed_phrase("ANY SEXUAL HARASSMENT AND YOU'RE DEAD MEAT.") == "أي تحرّش جنسي، وستكون نهايتك!"
    assert translate_reviewed_phrase("WHAT'S GOING ON WITH THE ETHICS TRAINING OF THE \"ELITE\" DIVISION ONE?") == "ما الذي يجري في تدريب الأخلاقيات للفرقة الأولى من «النخبة»؟"
    assert translate_reviewed_phrase("WHAT'S GO- ING ON WITH THE ETHICS TRAINING OF THE \"ELITE\" DIVISION ONEP") == "ما الذي يجري في تدريب الأخلاقيات للفرقة الأولى من «النخبة»؟"
    assert translate_reviewed_phrase("SHE HAD THESE LONK BANGS, LIKE THIS.") == "كانت لها غرّة طويلة كهذه."
    assert translate_reviewed_phrase("WHOOPS!") == "عفواً!"
    assert translate_reviewed_phrase("THAT NEW GIRL FROM DIVISION TWO'S GOT MOVES!") == "الفتاة الجديدة في القسم الثاني بارعة!"
    assert translate_reviewed_phrase("THEY WERE JUST LIKE THAT!") == "كانوا هكذا فحسب!"
    assert translate_reviewed_phrase("I'M JUST SAYING THAT THE ONES I SAW WERE ON THAT LEVEL! I'M TALKING ABOUT SOMEONE ELSE!") == "أنا فقط أقول إن من رأيتهن بلغن هذا المستوى أيضاً. أنا أتحدث عن شخص آخر!"
    assert translate_reviewed_phrase("The red dragon is in the castle.") is None


def test_full_sentence_glossary_does_not_replace_automatic_translation(monkeypatch, tmp_path):
    monkeypatch.setenv("MANGA_LIBRARY_DIR", str(tmp_path / "library"))
    monkeypatch.setattr(web_app, "CURRENT_TRANSLATION_ENGINE", "index_translate_9b")
    class FakeRouter:
        adapters = [type("Adapter", (), {"engine_id": "index_translate_9b", "model_path": "missing.gguf"})()]
        last_engine_id = "index_translate_9b"
        last_model_name = "Index fixture"

        def translate_with(self, engine, text, context=""):
            assert "How dare you! =" not in context
            return "ما أجرأك!"

    monkeypatch.setattr(web_app, "BALANCED_TRANSLATOR", FakeRouter())
    monkeypatch.setattr(web_app, "translation_recipe_signature", lambda *_args: "test-recipe")
    web_app.library_store.save_glossary_term("How dare you!", "أهذا ما تجرؤ عليه؟")
    assert web_app.translate_text("How dare you!", chapter_title="Test") == "ما أجرأك!"


def test_reviewed_phrase_overrides_bad_model_translation(monkeypatch, tmp_path):
    monkeypatch.setenv("MANGA_LIBRARY_DIR", str(tmp_path / "library"))
    monkeypatch.setattr(web_app, "CURRENT_TRANSLATION_ENGINE", "qwen3")
    monkeypatch.setattr(web_app, "QWEN_TRANSLATOR", None)
    assert web_app.translate_text("CONGRATULATIONS ON DEFEATING THE FLOOR BOSS, EUGENE.") == "تهانينا يا يوجين على هزيمتك لزعيم الطابق!"
    assert web_app.QWEN_TRANSLATOR is None


def test_madlad_is_deferred_from_the_current_index_only_web_scope(monkeypatch, tmp_path):
    monkeypatch.setenv("MANGA_LIBRARY_DIR", str(tmp_path / "library"))
    monkeypatch.setattr(web_app, "CURRENT_TRANSLATION_ENGINE", "opus")
    monkeypatch.setattr(web_app, "TRANSLATOR", None)
    monkeypatch.setattr(web_app, "QWEN_TRANSLATOR", None)
    monkeypatch.setattr(web_app, "MADLAD_TRANSLATOR", None)
    monkeypatch.setattr(web_app.MadladTranslator, "available", staticmethod(lambda: True))

    class FakeMadlad:
        def translate(self, text, context=""):
            return "ترجمة محلية: " + text

    monkeypatch.setattr(web_app, "MADLAD_TRANSLATOR", FakeMadlad())
    client = web_app.app.test_client()
    status = client.get("/api/translation-engine").json
    assert "madlad" not in [item["id"] for item in status["engines"]]
    selected = client.post("/api/translation-engine", json={"engine": "madlad"})
    assert selected.status_code == 409
    assert selected.json["current"] == "index_translate_9b"


def test_translation_history_is_append_only_and_exportable(monkeypatch, tmp_path):
    monkeypatch.setenv("MANGA_LIBRARY_DIR", str(tmp_path / "library"))
    first_id = web_app.library_store.record_translation_event(
        event_type="engine_output", source_text="I am here.", target_text="أنا هنا.",
        engine_id="opus", chapter_id="chapter-1", chapter_title="Series",
        page_index=2, region_index=1, context_text="Wait for me.",
        model_name="OPUS-MT en-ar ONNX INT8", quantization="INT8",
    )
    second_id = web_app.library_store.record_translation_event(
        event_type="human_correction", source_text="I am here.", target_text="ها أنا ذا.",
        engine_id="human", chapter_id="chapter-1", chapter_title="Series",
        page_index=2, region_index=1, review_status="human_reviewed",
        metadata={"previous_translation": "أنا هنا."},
    )
    rows = web_app.library_store.list_translation_events(chapter_id="chapter-1", page_index=2)
    assert {row["event_id"] for row in rows} == {first_id, second_id}
    assert len(rows) == 2
    correction = next(row for row in rows if row["event_id"] == second_id)
    assert correction["review_status"] == "human_reviewed"
    assert correction["metadata"]["previous_translation"] == "أنا هنا."
    assert correction["training_eligible"] is False

    client = web_app.app.test_client()
    jsonl = client.get("/api/translation-history?chapter_id=chapter-1&format=jsonl")
    assert jsonl.status_code == 200
    assert b"human_correction" in jsonl.data
    csv_response = client.get("/api/translation-history?chapter_id=chapter-1&format=csv")
    assert csv_response.status_code == 200
    assert "text/csv" in csv_response.content_type
    assert "ها أنا ذا." in csv_response.get_data(as_text=True)


def test_translation_history_rejects_invalid_filters(monkeypatch, tmp_path):
    monkeypatch.setenv("MANGA_LIBRARY_DIR", str(tmp_path / "library"))
    response = web_app.app.test_client().get("/api/translation-history?page_index=zero")
    assert response.status_code == 400


def test_live_translation_records_engine_and_memory_hits(monkeypatch, tmp_path):
    monkeypatch.setenv("MANGA_LIBRARY_DIR", str(tmp_path / "library"))
    monkeypatch.setattr(web_app, "CURRENT_TRANSLATION_ENGINE", "opus")

    class FakeOpus:
        def translate(self, text):
            return "سأبقى معك."

    monkeypatch.setattr(web_app, "TRANSLATOR", FakeOpus())
    assert web_app.translate_text(
        "I will stay with you.", chapter_title="Series", page_index=4,
        context="I will protect you.", chapter_id="chapter-x", region_index=2,
    ) == "سأبقى معك."
    assert web_app.translate_text(
        "I will stay with you.", chapter_title="Series", page_index=5,
        chapter_id="chapter-x", region_index=2,
    ) == "سأبقى معك."
    assert web_app.translate_text(
        "I will stay with you.", chapter_title="Series", page_index=6,
        context="I will protect you.", chapter_id="chapter-x", region_index=2,
    ) == "سأبقى معك."
    events = web_app.library_store.list_translation_events(chapter_id="chapter-x")
    model_event = next(
        event for event in events
        if event["engine_id"] == "opus" and event["context_text"] == "I will protect you."
    )
    assert model_event["model_name"] == "OPUS-MT en-ar ONNX INT8"
    assert model_event["context_text"] == "I will protect you."
    assert model_event["region_index"] == 2
    assert any(event["engine_id"] == "translation_memory" for event in events)


def test_index_only_uses_index_adapter_with_context(monkeypatch, tmp_path):
    monkeypatch.setenv("MANGA_LIBRARY_DIR", str(tmp_path / "library"))
    monkeypatch.setattr(web_app, "CURRENT_TRANSLATION_ENGINE", "index_translate_9b")
    monkeypatch.setattr(web_app, "BALANCED_TRANSLATOR", None)
    monkeypatch.setattr(web_app, "TRANSLATOR", None)

    class FakeRouter:
        adapters = [type("Adapter", (), {"engine_id": "index_translate_9b", "model_path": "missing.gguf"})()]
        last_engine_id = "index_translate_9b"
        last_model_name = "Index-Translate test fixture"

        def preferred_engine_id(self):
            return "index_translate_9b"

        def translate_with(self, engine, text, context=""):
            assert engine == "index_translate_9b"
            assert "I am waiting." in context
            return "سأبقى قربك."

        def shutdown(self):
            pass

    monkeypatch.setattr(web_app, "BalancedAutomaticTranslator", lambda **_kwargs: FakeRouter())
    result = web_app.translate_text(
        "I will stay by your side.", chapter_title="Auto series", context="I am waiting.",
        chapter_id="auto-chapter", page_index=3, region_index=2,
    )
    assert result == "سأبقى قربك."
    event = web_app.library_store.list_translation_events(chapter_id="auto-chapter")[0]
    assert event["engine_id"] == "index_translate_9b"
    assert event["model_name"] == "Index-Translate test fixture"
    assert event["metadata"]["translation_policy"] == "index_only"
    from translator_engine.adapters import INDEX_PROMPT_VERSION
    assert event["prompt_version"] == INDEX_PROMPT_VERSION
    assert web_app.TRANSLATOR is None


def test_index_localizes_unambiguous_chapter_labels_without_model_call(monkeypatch, tmp_path):
    monkeypatch.setenv("MANGA_LIBRARY_DIR", str(tmp_path / "library"))
    monkeypatch.setattr(web_app, "CURRENT_TRANSLATION_ENGINE", "index_translate_9b")
    monkeypatch.setattr(web_app, "BALANCED_TRANSLATOR", None)
    monkeypatch.setattr(
        web_app, "get_balanced_translator",
        lambda: (_ for _ in ()).throw(AssertionError("chapter labels should not call the model")),
    )

    assert web_app.translate_text("CH 033", chapter_id="chapter") == "\u0627\u0644\u0641\u0635\u0644 33"
    assert web_app.translate_text("Chapter 0007", chapter_id="chapter") == "\u0627\u0644\u0641\u0635\u0644 7"
    event = web_app.library_store.list_translation_events(chapter_id="chapter")[0]
    assert event["metadata"]["translation_method"] == "chapter_label_normalization"


def test_balanced_auto_reports_unavailable_without_new_model_files(monkeypatch):
    import translator_engine.adapters as adapter_module
    monkeypatch.setattr(web_app, "CURRENT_TRANSLATION_ENGINE", "balanced_auto")
    monkeypatch.setattr(web_app, "BALANCED_TRANSLATOR", None)
    monkeypatch.setattr(adapter_module.IndexTranslateAdapter, "available", classmethod(lambda cls: False))
    monkeypatch.setattr(adapter_module.TranslateGemmaAdapter, "available", classmethod(lambda cls: False))
    client = web_app.app.test_client()
    response = client.get("/api/translation-engine")
    assert response.status_code == 200
    assert response.json["mode"] == "index_only"
    assert response.json["current"] == "index_translate_9b"
    assert response.json["preferred_engine"] == "unavailable"
    assert "Index" in response.json["note"]
    assert [engine["id"] for engine in response.json["engines"]] == ["index_translate_9b"]


def test_engine_status_exposes_new_model_failure_reason(monkeypatch):
    from translator_engine.adapters import BalancedAutomaticTranslator

    class FailingAdapter:
        def __init__(self, engine_id):
            self.engine_id = engine_id
            self.model_name = engine_id
            self.model_path = f"{engine_id}.gguf"

        def available(self):
            return True

        def translate(self, text, context=""):
            raise RuntimeError("simulated incompatible GGUF")

        def shutdown(self):
            pass

    router = BalancedAutomaticTranslator(
        index_adapter=FailingAdapter("index_translate_9b"),
        translategemma_adapter=FailingAdapter("translategemma_4b"),
    )
    with pytest.raises(RuntimeError, match="Index أو TranslateGemma"):
        router.translate("Hello.")
    monkeypatch.setattr(web_app, "CURRENT_TRANSLATION_ENGINE", "balanced_auto")
    monkeypatch.setattr(web_app, "BALANCED_TRANSLATOR", router)
    response = web_app.app.test_client().get("/api/translation-engine")
    assert response.json["preferred_engine"] == "unavailable"
    assert "simulated incompatible GGUF" in response.json["note"]


def test_index_server_probe_is_opt_in_and_does_not_start_inference(monkeypatch):
    class Adapter:
        engine_id = "index_translate_9b"

        def __init__(self):
            self.probes = 0

        def diagnose_server(self):
            self.probes += 1
            return {"status": "authentication_error", "message": "stale local server"}

    class Router:
        def __init__(self):
            self.adapters = [Adapter()]

        @staticmethod
        def status():
            return {"index_translate_9b": {
                "available": True, "availability_reason": None, "session_error": None,
            }}

        @staticmethod
        def preferred_engine_id():
            return "index_translate_9b"

    router = Router()
    monkeypatch.setattr(web_app, "BALANCED_TRANSLATOR", router)
    client = web_app.app.test_client()

    normal = client.get("/api/translation-engine")
    assert normal.status_code == 200
    assert "server_diagnostic" not in normal.json
    assert router.adapters[0].probes == 0

    probed = client.get("/api/translation-engine?probe=1")
    assert probed.status_code == 200
    assert probed.json["server_diagnostic"]["status"] == "authentication_error"
    assert router.adapters[0].probes == 1


def test_shutdown_stops_child_translation_servers(monkeypatch):
    stopped = []

    class FakeServer:
        def __init__(self, name):
            self.name = name

        def shutdown(self):
            stopped.append(self.name)

    monkeypatch.setattr(web_app, "BALANCED_TRANSLATOR", FakeServer("balanced"))
    monkeypatch.setattr(web_app, "QWEN_TRANSLATOR", FakeServer("qwen"))
    monkeypatch.setattr(web_app, "QWEN_PROOFREADER", FakeServer("proofreader"))
    web_app.shutdown_local_translation_models()
    assert stopped == ["balanced", "qwen", "proofreader"]


def test_balanced_translation_receives_only_applicable_glossary_entries(monkeypatch, tmp_path):
    monkeypatch.setenv("MANGA_LIBRARY_DIR", str(tmp_path / "library"))
    monkeypatch.setattr(web_app, "CURRENT_TRANSLATION_ENGINE", "balanced_auto")
    monkeypatch.setattr(web_app, "BALANCED_TRANSLATOR", None)
    monkeypatch.setattr(web_app, "TRANSLATOR", None)
    monkeypatch.setattr(web_app.library_store, "get_glossary_exact", lambda _text: None)
    monkeypatch.setattr(web_app.library_store, "list_glossary", lambda: [
        {"source_term": "floor boss", "target_term": "زعيم الطابق"},
        {"source_term": "guild", "target_term": "النقابة"},
    ])

    class FakeRouter:
        adapters = [type("Adapter", (), {"engine_id": "index_translate_9b", "model_path": "missing.gguf"})()]
        last_engine_id = "index_translate_9b"
        last_model_name = "Index fixture"

        def preferred_engine_id(self):
            return "index_translate_9b"

        def translate(self, text, context=""):
            assert "floor boss = زعيم الطابق" in context
            assert "guild = النقابة" not in context
            return "هُزم زعيم الطابق."

    monkeypatch.setattr(web_app, "BalancedAutomaticTranslator", lambda **_kwargs: FakeRouter())
    result = web_app.translate_text("The floor boss is defeated.", chapter_title="Glossary test")
    assert result == "هُزم زعيم الطابق."


def test_glossary_change_invalidates_matching_translation_memory(monkeypatch, tmp_path):
    monkeypatch.setenv("MANGA_LIBRARY_DIR", str(tmp_path / "library"))
    monkeypatch.setattr(web_app, "CURRENT_TRANSLATION_ENGINE", "balanced_auto")
    monkeypatch.setattr(web_app, "BALANCED_TRANSLATOR", None)
    monkeypatch.setattr(web_app, "TRANSLATOR", None)
    monkeypatch.setattr(web_app.library_store, "get_glossary_exact", lambda _text: None)
    glossary = [{"source_term": "moon gate", "target_term": "بوابة القمر"}]
    monkeypatch.setattr(web_app.library_store, "list_glossary", lambda: glossary)

    class FakeRouter:
        adapters = [type("Adapter", (), {"engine_id": "index_translate_9b", "model_path": "missing.gguf"})()]
        last_engine_id = "index_translate_9b"
        last_model_name = "Index fixture"

        def __init__(self):
            self.calls = 0

        def preferred_engine_id(self):
            return "index_translate_9b"

        def translate(self, text, context=""):
            self.calls += 1
            return f"ترجمة {self.calls}"

    router = FakeRouter()
    monkeypatch.setattr(web_app, "BalancedAutomaticTranslator", lambda **_kwargs: router)
    source = "The moon gate opens."
    first = web_app.translate_text(source, chapter_title="Memory glossary")
    glossary[0]["target_term"] = "بوابة الهلال"
    second = web_app.translate_text(source, chapter_title="Memory glossary")
    assert first == "ترجمة 1"
    assert second == "ترجمة 2"
    assert router.calls == 2
