import sys
from types import SimpleNamespace

from translator_engine.madlad_engine import MadladTranslator


def test_madlad_adapter_uses_arabic_prefix_and_cpu_int8(monkeypatch, tmp_path):
    calls = {}

    class FakeTokenizer:
        def __init__(self, model_file):
            calls["tokenizer_path"] = model_file

        def encode(self, text, out_type=str):
            calls["encoded_text"] = text
            return ["<2ar>", "▁hello"]

        def decode(self, tokens):
            return "<2ar> مرحباً </s>"

    class FakeTranslator:
        def __init__(self, model_path, **kwargs):
            calls["model_path"] = model_path
            calls["runtime"] = kwargs

        def translate_batch(self, batch, **kwargs):
            calls["batch"] = batch
            calls["decode"] = kwargs
            return [SimpleNamespace(hypotheses=[["arabic-token"]])]

    monkeypatch.setitem(sys.modules, "sentencepiece", SimpleNamespace(SentencePieceProcessor=FakeTokenizer))
    monkeypatch.setitem(sys.modules, "ctranslate2", SimpleNamespace(Translator=FakeTranslator))
    monkeypatch.setattr(MadladTranslator, "available", staticmethod(lambda *_args: True))

    engine = MadladTranslator(tmp_path)
    assert engine.translate("  Hello\nworld! ", context="prior dialogue") == "مرحباً"
    assert calls["encoded_text"] == "<2ar> Hello world!"
    assert calls["runtime"]["device"] == "cpu"
    assert calls["runtime"]["compute_type"] == "int8"
    assert calls["batch"] == [["<2ar>", "▁hello"]]
    assert calls["decode"]["beam_size"] == 2
