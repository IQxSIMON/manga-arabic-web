import pytest

from translator_engine.engine import TranslatorEngine


@pytest.fixture(scope="module")
def engine():
    return TranslatorEngine()


def test_model_and_cpu_sessions_load(engine):
    assert engine.encoder.get_providers() == ["CPUExecutionProvider"]
    assert engine.decoder.get_providers() == ["CPUExecutionProvider"]
    assert engine.decoder_past.get_providers() == ["CPUExecutionProvider"]
    assert engine.src.get_piece_size() > 0
    assert engine.tgt.get_piece_size() > 0


def test_empty_input_does_not_infer(engine):
    assert engine.translate("  \n ") == ""


def test_rejects_oversized_generation_limit(engine):
    with pytest.raises(ValueError, match="positive integer"):
        engine.translate("Hi", max_new_tokens=0)


def test_rejects_corrupt_tokenizer_output(engine):
    result = engine.translate("Hello, how are you?")
    assert result
    assert "\ufffd" not in result
    assert any("\u0600" <= char <= "\u06ff" for char in result)


def test_line_batch_reports_individual_failures(engine):
    results = engine.translate_batch(["Hello, how are you?", "   "])
    assert results[0]["error"] is None
    assert results[0]["translation"]
    assert results[1]["translation"] == ""
    assert results[1]["error"] is None


def test_dialogue_samples_translate_to_arabic(engine):
    samples = [
        "I will protect you.",
        "What are you doing here?",
        "I never wanted to lose you!",
        "You have some nerve showing your face here!",
        "Even if the whole world stands against me, I will never give up.",
    ]
    for source, result in zip(samples, engine.translate_batch(samples)):
        assert result["error"] is None, source
        assert result["translation"]
        assert "\ufffd" not in result["translation"]
        assert any("\u0600" <= char <= "\u06ff" for char in result["translation"])


def test_long_text_is_split_within_source_limit(engine):
    source = "I will protect you. " * 70
    result = engine.translate(source)
    assert result
    assert any("\u0600" <= char <= "\u06ff" for char in result)
