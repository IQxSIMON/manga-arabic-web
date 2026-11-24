"""Focused, model-free regression checks for OCR review cues on vertical dialogue."""
from ocr_engine.local_rapidocr import LocalRapidOCR


def test_flags_structurally_garbled_high_confidence_dialogue_without_rewriting():
    source = "WHAT THE ¿773H"
    region = {"region_type": "speech_bubble", "text": source, "confidence": 0.9188}

    LocalRapidOCR._add_speech_bubble_ocr_review_hint(region)

    assert region["ocr_review_required"] is True
    assert "unexpected_non_english_glyph" in region["ocr_review_reasons"]
    assert "digit_embedded_in_english_token" in region["ocr_review_reasons"]
    assert region["text"] == source


def test_flags_stray_capital_tokens_in_prose():
    source = "DID THEY JUST SAY THEY ARE TAKING OUR M S"
    flags, isolated = LocalRapidOCR._speech_bubble_ocr_structure_flags(source)

    assert "isolated_single_letter_in_prose" in flags
    assert isolated == ["M", "S"]


def test_flags_short_ocr_fragments_inside_dialogue():
    source = "I'M ASKING YOU ABOUT TH INVESTIGATION INTO MY BACKGROUND"
    flags, _ = LocalRapidOCR._speech_bubble_ocr_structure_flags(source)

    assert "uncommon_two_letter_fragment_in_prose" in flags


def test_flags_subject_pronoun_after_preposition():
    source = "DO YOU HAVE INFORMATION ON HE RIVER MARTIAL ALLIANCE?"
    flags, _ = LocalRapidOCR._speech_bubble_ocr_structure_flags(source)

    assert "subject_pronoun_after_preposition" in flags


def test_retries_high_confidence_but_structurally_garbled_lines():
    assert LocalRapidOCR._needs_medium_ocr("WHAT THE ¿773H", 0.9188)
    assert LocalRapidOCR._needs_medium_ocr("THIS IS WHAT ∃M", 0.97)
    assert LocalRapidOCR._needs_medium_ocr(
        "DO YOU HAVE INFORMATION ON HE RIVER MARTIAL ALLIANCE", 0.99
    )


def test_retries_high_confidence_line_with_multiple_unknown_tokens():
    assert LocalRapidOCR._needs_medium_ocr("THNNL OLY HOPE IS THIS", 0.8898)


def test_retries_unknown_token_after_definite_article():
    assert LocalRapidOCR._needs_medium_ocr("WITHIN THE MLRIM", 0.96)


def test_keeps_single_unknown_character_name_out_of_retry():
    assert not LocalRapidOCR._needs_medium_ocr("THEY SAW IJIN AT HOME", 0.98)


def test_does_not_flag_normal_english_structure():
    flags, isolated = LocalRapidOCR._speech_bubble_ocr_structure_flags(
        "I AM SO GLAD WE MADE IT HOME!"
    )

    assert flags == []
    assert isolated == []


def test_non_dialogue_regions_are_not_mutated():
    region = {"region_type": "external_text", "text": "A CAPTION", "confidence": 0.5}

    LocalRapidOCR._add_speech_bubble_ocr_review_hint(region)

    assert "ocr_review_required" not in region
