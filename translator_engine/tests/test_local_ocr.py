from pathlib import Path
import re
from types import SimpleNamespace

import pytest
import numpy as np
import cv2

from ocr_engine import LocalRapidOCR
from ocr_engine import local_rapidocr as local_rapidocr_module
from ocr_engine.comic_text_detector import ComicTextRegion
from ocr_engine.speech_bubbles import (
    MODEL_PATH, SpeechBubble, _minimum_safe_mask_area, deduplicate_bubbles,
    _is_rectangular_caption,
)


SAMPLE_PAGE = (
    Path(__file__).resolve().parents[2]
    / "test-images"
    / "Screenshot_2026-11-02-08-38-57-589_com.android.chrome.jpg"
)


def test_external_ocr_uses_smaller_tail_crop_without_changing_regular_grid():
    intervals = local_rapidocr_module._external_ocr_tile_intervals(15_000, 1_800, 192)

    assert len(intervals) == 10
    assert intervals[:9] == [
        (start, start + 1_800) for start in range(0, 13_200, 1_608)
    ]
    assert intervals[-1] == (14_100, 15_000)
    assert intervals[-2][1] - intervals[-1][0] >= 192
    assert all(left[1] >= right[0] for left, right in zip(intervals, intervals[1:]))


def test_external_truncated_tail_cue_only_flags_implausible_short_final_word():
    assert LocalRapidOCR._has_truncated_external_tail(
        "IF YOU CAME HERE ABOARD A CARRIAGE BEARING THE CREST OF SYLVANIA THEN YOU MI"
    )
    assert not LocalRapidOCR._has_truncated_external_tail(
        "IF YOU CAME HERE ABOARD A CARRIAGE BEARING THE CREST OF SYLVANIA THEN YOU GO"
    )
    assert not LocalRapidOCR._has_truncated_external_tail(
        "IF YOU CAME HERE ABOARD A CARRIAGE BEARING THE CREST OF SYLVANIA THEN YOU MUST."
    )


@pytest.mark.parametrize("length", [800, 1800, 2070])
def test_external_ocr_keeps_near_full_pages_in_one_tile(length):
    assert local_rapidocr_module._external_ocr_tile_intervals(length, 1_800, 192) == [
        (0, length)
    ]


def test_external_ocr_normalizes_icon_and_unique_known_compound(monkeypatch):
    monkeypatch.setattr(
        local_rapidocr_module, "_SPELLCHECKER",
        {"notice", "you", "have", "eliminated", "otherworldly", "inhabitants"},
    )
    region = {
        "region_type": "external_text",
        "text": "ONOTICE YOU HAVE ELIMINATED OTHERWORLDLYINHABITANTS",
        "confidence": 0.98,
    }

    normalized = LocalRapidOCR._normalize_external_ocr_region(region)

    assert normalized["text"] == "NOTICE YOU HAVE ELIMINATED OTHERWORLDLY INHABITANTS"
    assert normalized["ocr_normalization_original_text"] == region["text"]
    assert normalized["ocr_normalization_changes"] == [
        "removed_circled_notice_icon", "split_unique_dictionary_compound",
    ]
    assert region["text"] == "ONOTICE YOU HAVE ELIMINATED OTHERWORLDLYINHABITANTS"


def test_external_ocr_keeps_ambiguous_or_low_confidence_compounds(monkeypatch):
    monkeypatch.setattr(local_rapidocr_module, "_SPELLCHECKER", {
        "notice", "word", "another", "wordagain", "other", "wordagain",
    })
    region = {
        "region_type": "external_text",
        "text": "ONOTICE ANOTHERWORDAGAIN",
        "confidence": 0.69,
    }

    normalized = LocalRapidOCR._normalize_external_ocr_region(region)

    assert normalized["text"] == "NOTICE ANOTHERWORDAGAIN"
    assert normalized["ocr_normalization_changes"] == ["removed_circled_notice_icon"]


def test_external_ocr_normalization_does_not_change_bubbles(monkeypatch):
    monkeypatch.setattr(local_rapidocr_module, "_SPELLCHECKER", {"notice", "quest"})
    region = {"region_type": "speech_bubble", "text": "ONOTICE QUEST", "confidence": 1.0}

    normalized = LocalRapidOCR._normalize_external_ocr_region(region)

    assert normalized == region


def test_english_word_counts_accept_contractions_and_possessives(monkeypatch):
    monkeypatch.setattr(local_rapidocr_module, "_SPELLCHECKER", {
        "haven't", "today", "daily", "quest",
    })

    valid, total = LocalRapidOCR._english_word_counts(
        "I HAVEN'T DONE TODAY'S DAILY QUEST"
    )

    assert (valid, total) == (4, 5)


def test_fragmented_bubble_line_pair_uses_direct_recognition_only_for_strong_repair(monkeypatch):
    monkeypatch.setattr(local_rapidocr_module, "_SPELLCHECKER", {"ur", "unpleasant"})
    engine = SimpleNamespace(recognize_txt=lambda crops: SimpleNamespace(
        txts=("UNPLEASANT.",), scores=(0.999,)
    ))
    monkeypatch.setattr(LocalRapidOCR, "_get_engine", lambda self: engine)
    lines = [
        {"text": "ur", "left": 243.0, "top": 8681.0, "width": 57.0,
         "height": 25.0, "confidence": 0.78, "bubble_id": 5},
        {"text": "DnJHNI.", "left": 326.0, "top": 8681.0, "width": 136.0,
         "height": 25.0, "confidence": 0.81, "bubble_id": 5},
    ]

    result = LocalRapidOCR()._rescue_fragmented_bubble_line_pairs(
        np.zeros((9000, 700, 3), dtype=np.uint8), lines
    )

    assert len(result) == 1
    assert result[0]["text"] == "UNPLEASANT."
    assert result[0]["ocr_recovery_method"] == "direct_recognition_of_fragmented_bubble_line"


def test_fragmented_bubble_line_pair_rejects_low_confidence_direct_recognition(monkeypatch):
    monkeypatch.setattr(local_rapidocr_module, "_SPELLCHECKER", {"ur", "unpleasant"})
    engine = SimpleNamespace(recognize_txt=lambda crops: SimpleNamespace(
        txts=("UNPLEASANT.",), scores=(0.92,)
    ))
    monkeypatch.setattr(LocalRapidOCR, "_get_engine", lambda self: engine)
    lines = [
        {"text": "ur", "left": 243.0, "top": 8681.0, "width": 57.0,
         "height": 25.0, "confidence": 0.78, "bubble_id": 5},
        {"text": "DnJHNI.", "left": 326.0, "top": 8681.0, "width": 136.0,
         "height": 25.0, "confidence": 0.81, "bubble_id": 5},
    ]

    result = LocalRapidOCR()._rescue_fragmented_bubble_line_pairs(
        np.zeros((9000, 700, 3), dtype=np.uint8), lines
    )

    assert result == lines


def test_malformed_external_line_uses_direct_recognition_with_lexical_improvement(monkeypatch):
    monkeypatch.setattr(local_rapidocr_module, "_SPELLCHECKER", {
        "don't", "need", "to", "dwell",
    })
    engine = SimpleNamespace(recognize_txt=lambda crops: SimpleNamespace(
        txts=("DON'T NEED TO DWELL",), scores=(0.993,)
    ))
    monkeypatch.setattr(LocalRapidOCR, "_get_engine", lambda self: engine)
    lines = [{
        "text": "DOED D D NNLL", "left": 10.0, "top": 20.0,
        "width": 220.0, "height": 35.0, "confidence": 0.90,
        "angle": 0.0, "region_type": "external_text",
    }]

    result = LocalRapidOCR()._rescue_external_line_recognition(
        np.zeros((100, 300, 3), dtype=np.uint8), lines
    )

    assert result[0]["text"] == "DON'T NEED TO DWELL"
    assert result[0]["ocr_recovery_method"] == "direct_recognition_of_external_line"


def test_external_accented_prefix_damage_can_be_recovered_without_lexicon_count_gain(monkeypatch):
    monkeypatch.setattr(local_rapidocr_module, "_SPELLCHECKER", {
        "the", "plant", "nd", "send", "everyone", "and",
    })
    engine = SimpleNamespace(recognize_txt=lambda crops: SimpleNamespace(
        txts=("THE PLANT AND SEND EVERYONE",), scores=(0.99567,)
    ))
    monkeypatch.setattr(LocalRapidOCR, "_get_engine", lambda self: engine)
    lines = [{
        "text": "THE PLANT ÁND SEND EVERYONE", "left": 80.0, "top": 40.0,
        "width": 420.0, "height": 48.0, "confidence": 0.9957,
        "angle": 0.0, "region_type": "external_text",
    }]

    result = LocalRapidOCR()._rescue_external_line_recognition(
        np.zeros((120, 600, 3), dtype=np.uint8), lines
    )

    assert result[0]["text"] == "THE PLANT AND SEND EVERYONE"
    assert result[0]["ocr_rescued"] is True


def test_external_question_prefix_damage_rejects_unknown_retry(monkeypatch):
    monkeypatch.setattr(local_rapidocr_module, "_SPELLCHECKER", {
        "the", "plant", "nd", "send", "everyone",
    })
    engine = SimpleNamespace(recognize_txt=lambda crops: SimpleNamespace(
        txts=("THE PLANT XND SEND EVERYONE",), scores=(0.999,)
    ))
    monkeypatch.setattr(LocalRapidOCR, "_get_engine", lambda self: engine)
    lines = [{
        "text": "THE PLANT ?ND SEND EVERYONE", "left": 80.0, "top": 40.0,
        "width": 420.0, "height": 48.0, "confidence": 0.9957,
        "angle": 0.0, "region_type": "external_text",
    }]

    result = LocalRapidOCR()._rescue_external_line_recognition(
        np.zeros((120, 600, 3), dtype=np.uint8), lines
    )

    assert result == lines


def test_external_article_hyphen_artifact_is_normalized_without_changing_compounds():
    lines = [
        {"text": "-a-bad feeling", "left": 20.0, "top": 20.0,
         "width": 151.0, "height": 35.0, "confidence": 0.80,
         "region_type": "external_text"},
        {"text": "well-known", "left": 20.0, "top": 60.0,
         "width": 151.0, "height": 35.0, "confidence": 0.99,
         "region_type": "external_text"},
    ]

    result = LocalRapidOCR()._rescue_external_line_recognition(
        np.zeros((120, 300, 3), dtype=np.uint8), lines
    )

    assert result[0]["text"] == "a bad feeling"
    assert result[0]["ocr_recovery_method"] == "external_article_hyphen_artifact_cleanup"
    assert result[1] == lines[1]


def test_low_confidence_short_external_fragment_can_recover_fused_i_word(monkeypatch):
    monkeypatch.setattr(local_rapidocr_module, "_SPELLCHECKER", {"have"})
    engine = SimpleNamespace(recognize_txt=lambda crops: SimpleNamespace(
        txts=("Ihave",), scores=(0.998,)
    ))
    monkeypatch.setattr(LocalRapidOCR, "_get_engine", lambda self: engine)
    lines = [{
        "text": "hhI", "left": 20.0, "top": 20.0,
        "width": 151.0, "height": 61.0, "confidence": 0.80,
        "angle": 1.5, "region_type": "external_text",
    }]

    result = LocalRapidOCR()._rescue_external_line_recognition(
        np.zeros((120, 300, 3), dtype=np.uint8), lines
    )

    assert result[0]["text"] == "I have"
    assert result[0]["ocr_rescued"] is True


def test_low_confidence_short_external_fragment_rejects_unrelated_word(monkeypatch):
    monkeypatch.setattr(local_rapidocr_module, "_SPELLCHECKER", {"cat"})
    engine = SimpleNamespace(recognize_txt=lambda crops: SimpleNamespace(
        txts=("CAT",), scores=(0.999,)
    ))
    monkeypatch.setattr(LocalRapidOCR, "_get_engine", lambda self: engine)
    lines = [{
        "text": "hhI", "left": 20.0, "top": 20.0,
        "width": 151.0, "height": 61.0, "confidence": 0.80,
        "angle": 1.5, "region_type": "external_text",
    }]

    result = LocalRapidOCR()._rescue_external_line_recognition(
        np.zeros((120, 300, 3), dtype=np.uint8), lines
    )

    assert result == lines


def test_malformed_bubble_line_uses_direct_recognition_and_preserves_known_words(monkeypatch):
    monkeypatch.setattr(local_rapidocr_module, "_SPELLCHECKER", {"as", "expected"})
    engine = SimpleNamespace(recognize_txt=lambda crops: SimpleNamespace(
        txts=("AS EXPECTED",), scores=(0.993,)
    ))
    monkeypatch.setattr(LocalRapidOCR, "_get_engine", lambda self: engine)
    lines = [{
        "text": "AS EXYECTED", "left": 10.0, "top": 20.0,
        "width": 220.0, "height": 35.0, "confidence": 0.92,
        "bubble_id": 2,
    }]

    result = LocalRapidOCR()._rescue_malformed_bubble_lines(
        np.zeros((100, 300, 3), dtype=np.uint8), lines
    )

    assert result[0]["text"] == "AS EXPECTED"
    assert result[0]["ocr_recovery_method"] == "direct_recognition_of_malformed_bubble_line"


def test_malformed_bubble_line_can_replace_only_a_weak_known_fragment(monkeypatch):
    monkeypatch.setattr(local_rapidocr_module, "_SPELLCHECKER",
                        {"si", "be", "silly", "this", "is"})
    engine = SimpleNamespace(recognize_txt=lambda crops: SimpleNamespace(
        txts=("BE SILLY! THIS IS",), scores=(0.99669,)
    ))
    monkeypatch.setattr(LocalRapidOCR, "_get_engine", lambda self: engine)
    lines = [{
        "text": "SI SIHI IXTTIS 3S", "left": 253.0, "top": 20.0,
        "width": 349.0, "height": 46.0, "confidence": 0.9135,
    }]

    result = LocalRapidOCR()._rescue_malformed_bubble_lines(
        np.zeros((100, 500, 3), dtype=np.uint8), lines
    )

    assert result[0]["text"] == "BE SILLY! THIS IS"
    assert result[0]["ocr_rescued"] is True


def test_malformed_bubble_line_keeps_weak_anchor_without_full_high_confidence_recovery(monkeypatch):
    monkeypatch.setattr(local_rapidocr_module, "_SPELLCHECKER",
                        {"si", "be", "silly", "this", "is"})
    engine = SimpleNamespace(recognize_txt=lambda crops: SimpleNamespace(
        txts=("BE SILLY! THIS IS",), scores=(0.9899,)
    ))
    monkeypatch.setattr(LocalRapidOCR, "_get_engine", lambda self: engine)
    lines = [{
        "text": "SI SIHI IXTTIS 3S", "left": 253.0, "top": 20.0,
        "width": 349.0, "height": 46.0, "confidence": 0.9135,
    }]

    result = LocalRapidOCR()._rescue_malformed_bubble_lines(
        np.zeros((100, 500, 3), dtype=np.uint8), lines
    )

    assert result == lines


def test_malformed_bubble_line_can_recover_phrase_with_suspicious_single_letter(monkeypatch):
    monkeypatch.setattr(local_rapidocr_module, "_SPELLCHECKER",
                        {"ni", "you", "nice", "to", "meet"})
    engine = SimpleNamespace(recognize_txt=lambda crops: SimpleNamespace(
        txts=("NICE TO MEET",), scores=(0.9996,)
    ))
    monkeypatch.setattr(LocalRapidOCR, "_get_engine", lambda self: engine)
    lines = [{
        "text": "NI T YOU,", "left": 176.0, "top": 20.0,
        "width": 336.0, "height": 52.0, "confidence": 0.9529,
    }]

    result = LocalRapidOCR()._rescue_malformed_bubble_lines(
        np.zeros((100, 500, 3), dtype=np.uint8), lines
    )

    assert result[0]["text"] == "NICE TO MEET"
    assert result[0]["ocr_rescued"] is True


def test_malformed_short_unknown_bubble_fragment_retries_at_low_confidence(monkeypatch):
    monkeypatch.setattr(local_rapidocr_module, "_SPELLCHECKER", {"you"})
    engine = SimpleNamespace(recognize_txt=lambda crops: SimpleNamespace(
        txts=("YOU, DABIN.",), scores=(0.99913,)
    ))
    monkeypatch.setattr(LocalRapidOCR, "_get_engine", lambda self: engine)
    lines = [{
        "text": "DABIN.", "left": 207.0, "top": 20.0,
        "width": 274.0, "height": 53.0, "confidence": 0.9529,
    }]

    result = LocalRapidOCR()._rescue_malformed_bubble_lines(
        np.zeros((100, 500, 3), dtype=np.uint8), lines
    )

    assert result[0]["text"] == "YOU, DABIN."
    assert result[0]["ocr_rescued"] is True


def test_suspicious_single_letter_retry_keeps_original_below_strong_threshold(monkeypatch):
    monkeypatch.setattr(local_rapidocr_module, "_SPELLCHECKER",
                        {"ni", "you", "nice", "to", "meet"})
    engine = SimpleNamespace(recognize_txt=lambda crops: SimpleNamespace(
        txts=("NICE TO MEET",), scores=(0.9899,)
    ))
    monkeypatch.setattr(LocalRapidOCR, "_get_engine", lambda self: engine)
    lines = [{
        "text": "NI T YOU,", "left": 176.0, "top": 20.0,
        "width": 336.0, "height": 52.0, "confidence": 0.9529,
    }]

    result = LocalRapidOCR()._rescue_malformed_bubble_lines(
        np.zeros((100, 500, 3), dtype=np.uint8), lines
    )

    assert result == lines


def test_high_confidence_duplicate_tail_typo_gets_targeted_retry(monkeypatch):
    monkeypatch.setattr(local_rapidocr_module, "_SPELLCHECKER", {"this"})
    engine = SimpleNamespace(recognize_txt=lambda crops: SimpleNamespace(
        txts=("THIS?",), scores=(0.999,)
    ))
    monkeypatch.setattr(LocalRapidOCR, "_get_engine", lambda self: engine)
    lines = [{
        "text": "THISS", "left": 10.0, "top": 20.0,
        "width": 80.0, "height": 34.0, "confidence": 0.999,
        "bubble_id": 6,
    }]

    result = LocalRapidOCR()._rescue_malformed_bubble_lines(
        np.zeros((100, 300, 3), dtype=np.uint8), lines
    )

    assert result[0]["text"] == "THIS?"
    assert result[0]["ocr_rescued"] is True
    assert LocalRapidOCR._has_trailing_duplicate_ocr_character("THISS")
    assert not LocalRapidOCR._has_trailing_duplicate_ocr_character("THIS")


def test_severely_damaged_large_bubble_lines_use_tight_medium_crops(monkeypatch):
    monkeypatch.setattr(local_rapidocr_module, "_SPELLCHECKER", {
        "the", "will", "fix", "rope", "mi", "pe",
    })
    medium = SimpleNamespace(recognize_txt=lambda crops: SimpleNamespace(
        txts=("I WILL FIX", "THE ROPE!"), scores=(0.9583, 0.9956)
    ))
    monkeypatch.setattr(LocalRapidOCR, "_get_medium_engine", lambda self: medium)
    lines = [
        {"text": "X11MI", "left": 10.0, "top": 10.0,
         "width": 100.0, "height": 100.0, "confidence": 0.70},
        {"text": "THE PE", "left": 12.0, "top": 80.0,
         "width": 100.0, "height": 100.0, "confidence": 0.82},
    ]

    result = LocalRapidOCR()._rescue_large_stylized_bubble_lines(
        np.zeros((250, 200, 3), dtype=np.uint8), lines
    )

    assert [line["text"] for line in result] == ["I WILL FIX", "THE ROPE!"]
    assert all(line["ocr_recovery_method"] == "tight_medium_stylized_bubble_retry"
               for line in result)
    assert all(line["ocr_rescued"] is True for line in result)


def test_terminal_short_fragment_is_retried_only_with_prose_context(monkeypatch):
    class FakeSpellChecker(set):
        def word_usage_frequency(self, word):
            return {"bt": 3.0e-8, "but": 0.0066}.get(word, 0.0)

    monkeypatch.setattr(local_rapidocr_module, "_SPELLCHECKER", FakeSpellChecker({
        "i", "didn't", "bet", "because", "am", "a", "participant", "but",
    }))
    engine = SimpleNamespace(recognize_txt=lambda crops: SimpleNamespace(
        txts=("BUT,",), scores=(0.9896,)
    ))
    monkeypatch.setattr(LocalRapidOCR, "_get_engine", lambda self: engine)
    image = np.zeros((100, 300, 3), dtype=np.uint8)
    lines = [
        {"text": "HUFF... I DIDN'T BET", "left": 10, "top": 10, "width": 180,
         "height": 20, "confidence": 0.99},
        {"text": "BECAUSE I'M A PARTICIPANT,", "left": 10, "top": 30,
         "width": 200, "height": 20, "confidence": 0.99},
        {"text": "BT", "left": 230, "top": 50, "width": 45, "height": 24,
         "confidence": 0.99},
    ]

    result = LocalRapidOCR()._rescue_terminal_short_fragment(image, lines)

    assert result[-1]["text"] == "BUT,"
    assert result[-1]["ocr_recovery_method"] == "terminal_short_fragment_context_retry"
    assert result[0] == lines[0]
    assert LocalRapidOCR()._rescue_terminal_short_fragment(image, lines[-1:]) == lines[-1:]


def test_malformed_bubble_line_rejects_low_confidence_candidate(monkeypatch):
    monkeypatch.setattr(local_rapidocr_module, "_SPELLCHECKER", {"as", "expected"})
    engine = SimpleNamespace(recognize_txt=lambda crops: SimpleNamespace(
        txts=("AS EXPECTED",), scores=(0.90,)
    ))
    monkeypatch.setattr(LocalRapidOCR, "_get_engine", lambda self: engine)
    lines = [{
        "text": "AS EXYECTED", "left": 10.0, "top": 20.0,
        "width": 220.0, "height": 35.0, "confidence": 0.92,
        "bubble_id": 2,
    }]

    result = LocalRapidOCR()._rescue_malformed_bubble_lines(
        np.zeros((100, 300, 3), dtype=np.uint8), lines
    )

    assert result == lines


def test_late_recovered_bubble_region_requires_a_lexically_complete_rescue(monkeypatch):
    monkeypatch.setattr(local_rapidocr_module, "_SPELLCHECKER", {
        "we", "we'll", "die", "if", "don't", "handle", "them",
    })
    engine = SimpleNamespace(recognize_txt=lambda crops: SimpleNamespace(
        txts=("WE'LL", "THEM!!!"), scores=(0.9694, 0.9528)
    ))
    monkeypatch.setattr(LocalRapidOCR, "_get_engine", lambda self: engine)
    texts = ("77,3M", "DIE IF WE", "DON'T HANDLE", "iiiWMHI")
    boxes = [
        [358.0, 20.0, 135.0, 56.0],
        [312.0, 67.0, 230.0, 56.0],
        [260.0, 114.0, 333.0, 55.0],
        [337.0, 159.0, 183.0, 59.0],
    ]
    lines = [
        {"text": text, "left": box[0], "top": box[1], "width": box[2],
         "height": box[3], "confidence": 0.40, "bubble_id": 7}
        for text, box in zip(texts, boxes)
    ]
    region = {
        "text": "77,3M DIE IF WE DON'T HANDLE iiiWMHI",
        "region_type": "speech_bubble", "ocr_boxes": boxes,
    }

    count = LocalRapidOCR()._rescue_malformed_bubble_regions(
        np.zeros((260, 700, 3), dtype=np.uint8), [region], lines
    )

    assert count == 1
    assert region["text"] == "WE'LL DIE IF WE DON'T HANDLE THEM!!!"
    assert [line["text"] for line in lines] == [
        "WE'LL", "DIE IF WE", "DON'T HANDLE", "THEM!!!",
    ]
    assert region["ocr_recovery_method"] == (
        "direct_recognition_of_malformed_recovered_bubble_lines"
    )



def test_contextual_short_bubble_fragments_are_rescued_only_as_complete_phrases(monkeypatch):
    monkeypatch.setattr(local_rapidocr_module, "_SPELLCHECKER", {
        "damn", "it", "what", "is", "that", "run", "away", "quickly",
    })

    def recognize(crops):
        outputs = tuple("THAT?!" if crop.shape[0] > 130 else "QUICKLY!!!" for crop in crops)
        return SimpleNamespace(txts=outputs, scores=(0.9962,) * len(outputs))

    monkeypatch.setattr(
        LocalRapidOCR, "_get_engine", lambda self: SimpleNamespace(recognize_txt=recognize)
    )
    region_specs = [
        (
            "DAMN IT, WHAT IS THTI",
            ("DAMN", "IT, WHAT IS", "THTI"),
            (
                [251.0, 10.0, 213.0, 89.0],
                [162.0, 30.0, 389.0, 102.0],
                [233.0, 60.0, 257.0, 101.0],
            ),
            (0.9981, 0.9869, 0.7357),
        ),
        (
            "RUN AWAY, Iiil",
            ("RUN AWAY,", "Iiil"),
            ([301.0, 170.0, 277.0, 70.0], [296.0, 210.0, 282.0, 67.0]),
            (0.9925, 0.646),
        ),
        (
            "Iiil",
            ("Iiil",),
            ([296.0, 210.0, 282.0, 67.0],),
            (0.646,),
        ),
    ]
    regions = []
    lines = []
    for bubble_id, (text, line_texts, boxes, scores) in enumerate(region_specs, 1):
        regions.append({
            "text": text, "region_type": "speech_bubble", "ocr_boxes": boxes,
        })
        for line_text, box, score in zip(line_texts, boxes, scores):
            lines.append({
                "text": line_text, "left": box[0], "top": box[1],
                "width": box[2], "height": box[3], "confidence": score,
                "bubble_id": bubble_id,
            })

    count = LocalRapidOCR()._rescue_malformed_bubble_regions(
        np.zeros((320, 700, 3), dtype=np.uint8), regions, lines
    )

    assert count == 2
    assert regions[0]["text"] == "DAMN IT, WHAT IS THAT?!"
    assert regions[1]["text"] == "RUN AWAY, QUICKLY!!!"
    assert regions[2]["text"] == "Iiil"


def test_speech_bubble_review_flags_only_a_unique_common_word_candidate(monkeypatch):
    class FakeSpellChecker:
        known = {"a", "hundred", "radiru", "taegyu", "shop"}
        suggestions = {"hundreed": {"hundred"}, "taegyu": {"taegu"}}
        frequencies = {"hundred": 9.1e-5, "taegu": 3.0e-8}

        def __contains__(self, word):
            return word in self.known

        def candidates(self, word):
            return self.suggestions.get(word, set())

        def word_usage_frequency(self, word):
            return self.frequencies.get(word, 0.0)

    monkeypatch.setattr(local_rapidocr_module, "_SPELLCHECKER", FakeSpellChecker())
    region = {
        "text": "A HUNDREED...!", "region_type": "speech_bubble", "confidence": 0.997,
    }

    reviewed = LocalRapidOCR._add_speech_bubble_ocr_review_hint(region)

    assert reviewed["ocr_review_required"] is True
    assert reviewed["ocr_review_reason"] == "possible_ocr_typo_near_common_word"
    assert reviewed["ocr_unrecognized_word_count"] == 1
    assert reviewed["ocr_suspicious_near_words"] == [
        {"observed": "HUNDREED", "candidate": "hundred", "frequency": 9.1e-05},
    ]
    assert reviewed["text"] == "A HUNDREED...!"


def test_speech_bubble_review_does_not_flag_names_rare_words_or_low_confidence(monkeypatch):
    class FakeSpellChecker:
        known = {"radiru", "shop"}

        def __contains__(self, word):
            return word in self.known

        def candidates(self, word):
            return {"taegu"} if word == "taegyu" else set()

        def word_usage_frequency(self, word):
            return 3.0e-8 if word == "taegu" else 0.0

    monkeypatch.setattr(local_rapidocr_module, "_SPELLCHECKER", FakeSpellChecker())
    regions = [
        {"text": "RADIRU CLAN", "region_type": "speech_bubble", "confidence": 0.99},
        {"text": "TAEGYU", "region_type": "speech_bubble", "confidence": 0.99},
        {"text": "A HUNDREED", "region_type": "speech_bubble", "confidence": 0.89},
        {"text": "SH-SHOP", "region_type": "speech_bubble", "confidence": 0.99},
    ]

    for region in regions:
        reviewed = LocalRapidOCR._add_speech_bubble_ocr_review_hint(region)
        assert "ocr_review_required" not in reviewed


def test_near_all_caps_bubble_casing_repairs_only_long_overwhelmingly_uppercase_text():
    noisy = {
        "text": "YOu'VE ONLY COMPLETED THE PUSH-UPS",
        "region_type": "speech_bubble",
    }
    prose = {
        "text": "You've only completed the push-ups.",
        "region_type": "speech_bubble",
    }
    short = {"text": "YOu OK", "region_type": "speech_bubble"}

    assert LocalRapidOCR._normalize_near_all_caps_bubble(noisy)["text"] == (
        "YOU'VE ONLY COMPLETED THE PUSH-UPS"
    )
    assert noisy["ocr_casing_original_text"] == "YOu'VE ONLY COMPLETED THE PUSH-UPS"
    assert "ocr_casing_normalization" not in prose
    assert "ocr_casing_normalization" not in short


def test_near_all_caps_normalization_does_not_touch_external_text():
    region = {
        "text": "YOu'VE ONLY COMPLETED THE PUSH-UPS",
        "region_type": "external_text",
    }

    assert LocalRapidOCR._normalize_near_all_caps_bubble(region)["text"] == region["text"]


def test_common_digit_letter_ocr_confusions_are_repaired_only_when_unique(monkeypatch):
    class FakeSpellChecker:
        known = {"is", "it"}

        def __contains__(self, word):
            return word in self.known

        def word_usage_frequency(self, word):
            return {"is": 0.01, "it": 0.01}.get(word, 0.0)

    monkeypatch.setattr(local_rapidocr_module, "_SPELLCHECKER", FakeSpellChecker())
    region = {
        "text": "I5 THAT 1T, YOUNG MONARCH?",
        "region_type": "speech_bubble",
        "confidence": 0.97,
    }
    low_confidence = {**region, "confidence": 0.89}
    external = {**region, "region_type": "external_text"}

    corrected = LocalRapidOCR._normalize_unique_common_digit_confusions(region)

    assert corrected["text"] == "IS THAT IT, YOUNG MONARCH?"
    assert corrected["ocr_digit_confusion_original_text"] == "I5 THAT 1T, YOUNG MONARCH?"
    assert corrected["ocr_digit_confusion_corrections"] == [
        {"observed": "I5", "candidate": "IS"},
        {"observed": "1T", "candidate": "IT"},
    ]
    assert LocalRapidOCR._normalize_unique_common_digit_confusions(low_confidence)["text"] == low_confidence["text"]
    assert LocalRapidOCR._normalize_unique_common_digit_confusions(external)["text"] == external["text"]


@pytest.mark.skipif(not MODEL_PATH.is_file(), reason="download the documented speech-bubble model")
def test_local_ocr_returns_only_bubble_regions_with_page_coordinates():
    result = LocalRapidOCR().extract(SAMPLE_PAGE.read_bytes())

    assert result["provider"].startswith(("rapidocr_onnxruntime_cpu", "ppocrv6_manga_v03_onnxruntime_cpu"))
    assert result["lines"]
    assert result["regions"]
    assert result["elapsed_seconds"] > 0
    bubble_lines = [line for line in result["lines"] if line.get("bubble_id") is not None]
    assert bubble_lines
    assert all(line.get("region_type") == "external_text" or line.get("bubble_id") is not None
               for line in result["lines"])
    bubble_regions = [region for region in result["regions"] if region.get("region_type") == "speech_bubble"]
    external_regions = [region for region in result["regions"] if region.get("region_type") == "external_text"]
    assert bubble_regions
    assert all(region.get("bubble_bbox") and region.get("ocr_boxes") for region in bubble_regions)
    assert all(region.get("ocr_boxes") and region.get("ocr_polygons") for region in external_regions)
    for line in result["lines"]:
        assert line["left"] >= 0 and line["top"] >= 0
        assert line["width"] > 0 and line["height"] > 0
        assert 0 <= line["confidence"] <= 1


def test_group_lines_merges_overlapping_decorative_text_rows():
    lines = [
        {"text": "KEEP UP THE", "left": 231, "top": 176, "width": 169, "height": 43, "confidence": 0.9},
        {"text": "GOOD WORK AND CONTINUE PROGRESSING!", "left": 219, "top": 211, "width": 194, "height": 139, "confidence": 0.8},
    ]
    regions = LocalRapidOCR.group_lines(lines)
    assert len(regions) == 1
    assert regions[0]["text"] == "KEEP UP THE GOOD WORK AND CONTINUE PROGRESSING!"


def test_group_lines_keeps_heavily_overlapping_rows_in_one_slanted_text_block():
    lines = [
        {"text": "ELG EHEN", "left": 209, "top": 414, "width": 146, "height": 63,
         "confidence": 0.69, "angle": -16.05},
        {"text": "WHEN HER HEAD GETS", "left": 208, "top": 430, "width": 158, "height": 66,
         "confidence": 0.99, "angle": -16.14},
        {"text": "BLOWN OFF.", "left": 247, "top": 458, "width": 91, "height": 46,
         "confidence": 0.99, "angle": -15.77},
    ]

    regions = LocalRapidOCR.group_lines(lines)

    assert len(regions) == 1
    assert regions[0]["line_indexes"] == [0, 1, 2]


def test_group_lines_does_not_merge_overlapping_rows_with_different_slants():
    lines = [
        {"text": "FIRST CAPTION", "left": 100, "top": 100, "width": 120, "height": 60,
         "confidence": 0.9, "angle": -16.0},
        {"text": "SECOND CAPTION", "left": 101, "top": 115, "width": 120, "height": 60,
         "confidence": 0.9, "angle": 4.0},
    ]

    regions = LocalRapidOCR.group_lines(lines)

    assert len(regions) == 2


def test_group_lines_separates_distant_slanted_art_from_horizontal_dialogue():
    lines = [
        {"text": "APPEARANCE OF MONSTER", "left": 0, "top": 5718,
         "width": 699, "height": 426, "confidence": 0.95, "angle": 24.0},
        {"text": "FIGHTING AMONG SOLDIERS", "left": 0, "top": 5989,
         "width": 699, "height": 464, "confidence": 0.95, "angle": 24.0},
        {"text": "THE", "left": 335, "top": 6594,
         "width": 84, "height": 29, "confidence": 0.95, "angle": 0.0},
        {"text": "PROBLEM MAY HAVE BEEN CAUSED BY", "left": 226, "top": 6638,
         "width": 300, "height": 45, "confidence": 0.95, "angle": 0.0},
    ]

    regions = LocalRapidOCR.group_lines(lines)

    assert len(regions) == 2
    assert regions[0]["line_indexes"] == [0, 1]
    assert regions[1]["line_indexes"] == [2, 3]


def test_rotated_external_group_accepts_fuller_confident_deskew_retry(monkeypatch):
    image = np.zeros((600, 700, 3), dtype=np.uint8)
    members = [
        {"text": "DAILY QUEST: TRAIN", "left": 170, "top": 130, "width": 210,
         "height": 48, "confidence": 0.92, "angle": 17.0,
         "ocr_polygon": [[170, 130], [380, 140], [374, 178], [164, 168]]},
        {"text": "DO YOU ACCEPT?", "left": 150, "top": 195, "width": 150,
         "height": 48, "confidence": 0.91, "angle": 17.0,
         "ocr_polygon": [[150, 195], [300, 205], [294, 243], [144, 233]]},
    ]
    retry = SimpleNamespace(
        boxes=[
            np.array([[60, 40], [230, 40], [230, 68], [60, 68]], dtype=np.float32),
            np.array([[50, 82], [250, 82], [250, 112], [50, 112]], dtype=np.float32),
            np.array([[70, 125], [190, 125], [190, 153], [70, 153]], dtype=np.float32),
            np.array([[65, 165], [180, 165], [180, 193], [65, 193]], dtype=np.float32),
        ],
        txts=["DAILY QUEST: TRAIN", "TO BECOME A FORMIDABLE",
              "COMBATANT HAS ARRIVED", "DO YOU ACCEPT?"],
        scores=[0.94, 0.96, 0.95, 0.97],
    )
    engine = LocalRapidOCR()
    monkeypatch.setattr(engine, "_get_engine", lambda: lambda _crop: retry)

    refined = engine._refine_rotated_external_group(image, members, 17.0, [])

    assert len(refined) == 4
    assert "FORMIDABLE" in " ".join(line["text"] for line in refined)
    assert all(len(line["ocr_polygon"]) == 4 for line in refined)
    assert all(0 <= line["left"] < image.shape[1] for line in refined)


def test_adjacent_fragments_inside_same_low_confidence_bubble_are_recovered(monkeypatch):
    image = np.zeros((500, 700, 3), dtype=np.uint8)
    regions = [
        {
            "text": "AND PERHAPS...", "region_type": "speech_bubble",
            "left": 28, "top": 100, "width": 497, "height": 60,
            "bubble_bbox": [28, 100, 525, 160], "bubble_id": 1,
            "bubble_confidence": 0.35,
        },
        {
            "text": "YOU MAY HAVE EVEN PLAN TO TAKE YOUR OW TO KEEP THIS SE BURIED.",
            "region_type": "speech_bubble",
            "left": 62, "top": 160, "width": 463, "height": 124,
            "bubble_bbox": [62, 160, 525, 284], "bubble_id": 1,
            "bubble_confidence": 0.35,
        },
    ]
    recovered_texts = [
        "AND", "PERHAPS...", "YOU MAY", "HAVE EVEN PLANNED",
        "TO TAKE YOUR OWN LIFE", "TO KEEP THIS SECRET", "BURIED.",
    ]
    result = SimpleNamespace(
        boxes=[
            np.array([[50, 70 + i * 30], [360, 70 + i * 30],
                      [360, 92 + i * 30], [50, 92 + i * 30]], dtype=np.float32)
            for i in range(len(recovered_texts))
        ],
        txts=recovered_texts, scores=[0.98] * len(recovered_texts),
    )
    engine = LocalRapidOCR()

    def retry(crop):
        assert crop.shape[1] == image.shape[1]
        return result

    monkeypatch.setattr(engine, "_get_engine", lambda: retry)
    lines, recovered_regions, elapsed, count = engine._recover_adjacent_fragmented_bubble_pair(
        image, [], regions, "ltr",
    )

    assert count == 1
    assert elapsed >= 0
    assert lines and len(lines) == len(recovered_texts)
    assert len(recovered_regions) == 1
    assert recovered_regions[0]["bubble_id"] == 1
    assert recovered_regions[0]["text"] == " ".join(recovered_texts)


def test_low_confidence_wide_bubble_retry_recovers_clipped_words(monkeypatch):
    image = np.zeros((600, 700, 3), dtype=np.uint8)
    old_text = (
        "SHE WAS THE FIRST ACQUAINTANCE OF OLD MAN GLOCKT'S THAT I EVER MET... "
        "AND I BARELY EVEN SOT TO TALK TO HE"
    )
    region = {
        "text": old_text, "region_type": "speech_bubble",
        "left": 100, "top": 180, "width": 550, "height": 280,
        "bubble_bbox": [100, 180, 650, 460], "bubble_id": 3,
        "bubble_confidence": 0.25, "ocr_edge_crop_rescued": True,
        "ocr_boxes": [[60, 200, 350, 40]],
    }
    original_line = {
        "text": old_text, "left": 60, "top": 200, "width": 350,
        "height": 40, "confidence": 0.8, "bubble_id": 3,
    }
    recovered_texts = [
        "SHE WAS THE", "FIRST ACQUAINTANCE OF", "OLD MAN GLOCKT'S THAT",
        "I EVER MET...", "AND", "I BARELY EVEN", "GOT TO TALK", "TO HER.",
    ]
    recovered_boxes = [
        np.array([[60, 50 + i * 32], [350, 50 + i * 32],
                  [350, 75 + i * 32], [60, 75 + i * 32]], dtype=np.float32)
        for i in range(len(recovered_texts))
    ]
    result = SimpleNamespace(
        boxes=recovered_boxes, txts=recovered_texts,
        scores=[0.98] * len(recovered_texts),
    )
    engine = LocalRapidOCR()
    monkeypatch.setattr(engine, "_get_engine", lambda: lambda crop: result)
    monkeypatch.setattr(
        LocalRapidOCR, "_english_word_counts",
        staticmethod(lambda text: (28, 28) if text != old_text else (24, 28)),
    )
    monkeypatch.setattr(LocalRapidOCR, "_looks_like_dialogue", staticmethod(lambda _text: True))

    lines, regions, elapsed, count = engine._recover_low_confidence_wide_bubble_region(
        image, [original_line], [region], "ltr",
    )

    assert count == 1
    assert elapsed >= 0
    assert regions[0]["text"] == " ".join(recovered_texts)
    assert regions[0]["ocr_recovery_method"] == "low_confidence_wide_bubble_crop"
    assert len(lines) == len(recovered_texts)
    assert all(line["ocr_recovered"] for line in lines)


def test_rotated_external_group_accepts_lexically_better_retry(monkeypatch):
    image = np.zeros((600, 700, 3), dtype=np.uint8)
    members = [
        {"text": "ELG EHEN", "left": 170, "top": 130, "width": 150,
         "height": 52, "confidence": 0.90, "angle": -16.0},
        {"text": "WHEN HER HEAD GETS BLOWN OFF.", "left": 160, "top": 165,
         "width": 230, "height": 60, "confidence": 0.91, "angle": -16.0},
    ]
    retry = SimpleNamespace(
        boxes=[np.array([[100, 50], [230, 50], [230, 80], [100, 80]], dtype=np.float32)],
        txts=["SHE DOESN'T"], scores=[0.94],
    )
    engine = LocalRapidOCR()
    monkeypatch.setattr(engine, "_get_engine", lambda: lambda _crop: retry)
    monkeypatch.setattr(
        LocalRapidOCR, "_english_word_counts",
        staticmethod(lambda text: (2, 2) if text == "SHE DOESN'T" else (0, 8)),
    )

    refined = engine._refine_rotated_external_group(image, members, -16.0, [])

    assert [line["text"] for line in refined] == ["SHE DOESN'T"]


def test_rotated_ocr_polygon_avoids_false_bubble_overlap_from_axis_aligned_bounds():
    bubble = SpeechBubble(
        (50, 0, 100, 100), np.full((100, 50), 255, dtype=np.uint8), 0.9,
    )
    rotated_polygon = [[30, 20], [46, 15], [51, 30], [35, 35]]

    # Its enclosing rectangle crosses 6 px into the bubble, while the actual
    # slanted quadrilateral remains outside the safe bubble mask.
    assert LocalRapidOCR._box_overlaps_bubble((30, 15, 52, 36), [bubble], threshold=0.08)
    assert not LocalRapidOCR._polygon_overlaps_bubble(rotated_polygon, [bubble])


def test_rotated_ocr_polygon_still_rejects_real_bubble_overlap():
    bubble = SpeechBubble(
        (50, 0, 100, 100), np.full((100, 50), 255, dtype=np.uint8), 0.9,
    )
    overlapping_polygon = [[58, 20], [88, 20], [88, 40], [58, 40]]

    assert LocalRapidOCR._polygon_overlaps_bubble(overlapping_polygon, [bubble])


def test_rotated_ocr_polygon_fails_closed_when_geometry_is_invalid():
    bubble = SpeechBubble(
        (50, 0, 100, 100), np.full((100, 50), 255, dtype=np.uint8), 0.9,
    )

    assert LocalRapidOCR._polygon_overlaps_bubble([[np.nan, 1], [2, 3], [4, 5]], [bubble])


def test_adjacent_bubble_columns_split_into_separate_utterances():
    lines = [
        {"text": "GEEZ", "left": 130, "top": 500, "width": 45, "height": 20},
        {"text": "REIWA ENERGY", "left": 130, "top": 520, "width": 90, "height": 18},
        {"text": "HAND IN HAND", "left": 130, "top": 560, "width": 90, "height": 18},
        {"text": "THE BOSS'S", "left": 235, "top": 520, "width": 90, "height": 20},
        {"text": "GONE MAD", "left": 240, "top": 540, "width": 80, "height": 20},
        {"text": "AGAIN", "left": 250, "top": 560, "width": 60, "height": 20},
    ]
    split = LocalRapidOCR._split_bubble_columns(lines, 120, 340)
    assert len(split) == 2
    assert [item["text"] for item in split[0]["lines"]] == ["GEEZ", "REIWA ENERGY", "HAND IN HAND"]
    assert [item["text"] for item in split[1]["lines"]] == ["THE BOSS'S", "GONE MAD", "AGAIN"]


def test_stacked_bubbles_split_vertically_inside_a_merged_mask():
    lines = [
        {"text": f"UPPER {index}", "left": 130, "top": 90 + index * 20, "width": 70, "height": 18}
        for index in range(4)
    ] + [
        {"text": f"LOWER {index}", "left": 45, "top": 240 + index * 22, "width": 80, "height": 18}
        for index in range(4)
    ]
    split = LocalRapidOCR._split_bubble_columns(lines, 20, 240, 20, 360)
    assert len(split) == 2
    assert all(item["text"].startswith("UPPER") for item in split[0]["lines"])
    assert all(item["text"].startswith("LOWER") for item in split[1]["lines"])
    assert split[0]["clip"][3] <= split[1]["clip"][1]


def test_detector_boxes_split_a_merged_bubble_mask_and_keep_parent_id():
    mask = np.zeros((300, 200), dtype=np.uint8)
    cv2.ellipse(mask, (100, 75), (85, 65), 0, 0, 360, 255, -1)
    cv2.ellipse(mask, (100, 225), (85, 65), 0, 0, 360, 255, -1)
    cv2.rectangle(mask, (96, 135), (104, 165), 255, -1)
    parent = SpeechBubble((0, 0, 200, 300), mask, 0.9)
    detections = [
        ComicTextRegion("bubble", 0.90, (0, 0, 200, 145)),
        ComicTextRegion("bubble", 0.88, (0, 155, 200, 300)),
        ComicTextRegion("text_bubble", 0.99, (30, 30, 170, 130)),
    ]

    result = LocalRapidOCR._split_bubbles_with_detector(
        [parent], detections, (400, 200, 3)
    )

    assert [bubble_id for bubble_id, _ in result] == [1, 1]
    assert result[0][1].bbox[3] < result[1][1].bbox[1]
    assert all(np.count_nonzero(bubble.safe_mask) > 1000 for _, bubble in result)


def test_detector_does_not_split_duplicate_or_weak_bubble_proposals():
    mask = np.zeros((300, 200), dtype=np.uint8)
    cv2.ellipse(mask, (100, 75), (85, 65), 0, 0, 360, 255, -1)
    cv2.ellipse(mask, (100, 225), (85, 65), 0, 0, 360, 255, -1)
    cv2.rectangle(mask, (96, 135), (104, 165), 255, -1)
    parent = SpeechBubble((0, 0, 200, 300), mask, 0.9)
    duplicate_boxes = [
        ComicTextRegion("bubble", 0.95, (0, 0, 200, 145)),
        ComicTextRegion("bubble", 0.90, (2, 2, 198, 143)),
    ]
    weak_boxes = [
        ComicTextRegion("bubble", 0.69, (0, 0, 200, 145)),
        ComicTextRegion("bubble", 0.68, (0, 155, 200, 300)),
    ]

    for detections in (duplicate_boxes, weak_boxes):
        result = LocalRapidOCR._split_bubbles_with_detector(
            [parent], detections, (400, 200, 3)
        )
        assert len(result) == 1
        assert result[0][0] == 1
        assert result[0][1] is parent


def test_detector_does_not_split_when_parent_mask_covers_only_part_of_box():
    mask = np.zeros((300, 200), dtype=np.uint8)
    cv2.ellipse(mask, (100, 75), (85, 65), 0, 0, 360, 255, -1)
    cv2.ellipse(mask, (100, 225), (85, 65), 0, 0, 360, 255, -1)
    parent = SpeechBubble((0, 0, 200, 300), mask, 0.9)
    detections = [
        ComicTextRegion("bubble", 0.95, (0, 0, 200, 145)),
        # The second detection extends well beyond the parent's left edge.
        # It must not become a cropped OCR/erase region.
        ComicTextRegion("bubble", 0.95, (-180, 155, 200, 300)),
    ]

    result = LocalRapidOCR._split_bubbles_with_detector(
        [parent], detections, (400, 200, 3)
    )

    assert len(result) == 1
    assert result[0][1] is parent


def test_large_concave_bubble_mask_is_selected_for_a_targeted_split_probe():
    mask = np.zeros((650, 500), dtype=np.uint8)
    cv2.ellipse(mask, (250, 170), (210, 160), 0, 0, 360, 255, -1)
    cv2.ellipse(mask, (250, 480), (210, 160), 0, 0, 360, 255, -1)
    merged = SpeechBubble((0, 0, 500, 650), mask, 0.9)

    assert LocalRapidOCR._should_probe_bubble_split(merged, 500)


def test_smooth_single_bubble_does_not_trigger_a_targeted_split_probe():
    mask = np.zeros((650, 500), dtype=np.uint8)
    cv2.ellipse(mask, (250, 325), (210, 300), 0, 0, 360, 255, -1)
    single = SpeechBubble((0, 0, 500, 650), mask, 0.9)

    assert not LocalRapidOCR._should_probe_bubble_split(single, 500)


def test_near_floor_low_confidence_concave_bubble_gets_split_probe_grace():
    mask = np.zeros((650, 500), dtype=np.uint8)
    cv2.ellipse(mask, (250, 170), (210, 160), 0, 0, 360, 255, -1)
    cv2.ellipse(mask, (250, 480), (210, 160), 0, 0, 360, 255, -1)
    merged_low_confidence = SpeechBubble((0, 0, 500, 650), mask, 0.30)
    merged_high_confidence = SpeechBubble((0, 0, 500, 650), mask, 0.90)

    assert LocalRapidOCR._should_probe_bubble_split(merged_low_confidence, 1000)
    assert not LocalRapidOCR._should_probe_bubble_split(merged_high_confidence, 1000)


def test_targeted_probe_splits_only_the_suspicious_parent_and_preserves_its_id(monkeypatch):
    mask = np.zeros((650, 500), dtype=np.uint8)
    cv2.ellipse(mask, (250, 170), (210, 160), 0, 0, 360, 255, -1)
    cv2.ellipse(mask, (250, 480), (210, 160), 0, 0, 360, 255, -1)
    parent = SpeechBubble((0, 0, 500, 650), mask, 0.9)
    smooth_mask = np.zeros((200, 180), dtype=np.uint8)
    cv2.ellipse(smooth_mask, (90, 100), (80, 90), 0, 0, 360, 255, -1)
    ordinary = SpeechBubble((550, 0, 730, 200), smooth_mask, 0.9)

    class FakeDetector:
        available = True
        calls = 0

        def detect(self, _crop):
            self.calls += 1
            return [
                ComicTextRegion("bubble", 0.90, (30, 20, 470, 310)),
                ComicTextRegion("bubble", 0.88, (30, 340, 470, 630)),
            ]

    detector = FakeDetector()
    monkeypatch.setattr(local_rapidocr_module, "_get_comic_text_detector", lambda: detector)
    engine = LocalRapidOCR()

    instances, elapsed, split_count = engine._refine_suspicious_bubble_splits(
        np.zeros((700, 800, 3), dtype=np.uint8), [parent, ordinary]
    )

    assert detector.calls == 1
    assert split_count == 1
    assert [bubble_id for bubble_id, _ in instances] == [1, 1, 2]
    assert instances[0][1].bbox[3] < instances[1][1].bbox[1]
    assert instances[-1][1] is ordinary
    assert elapsed >= 0


def test_medium_ocr_rescue_rejects_lexically_weaker_candidate(monkeypatch):
    class FakeRecognizer:
        def __call__(self, crop):
            return SimpleNamespace(
                boxes=[np.asarray([[0, 0], [100, 0], [100, 20], [0, 20]])],
                txts=["PURCASE"], scores=[0.97],
            )

    ocr = LocalRapidOCR()
    monkeypatch.setattr(ocr, "_get_medium_engine", lambda: FakeRecognizer())
    lines = [{"text": "PUR!ASE", "confidence": 0.88, "left": 20, "top": 20,
              "width": 100, "height": 20}]

    assert ocr._rescue_bubble_lines(
        np.full((60, 160, 3), 255, dtype=np.uint8), lines
    ) is lines


def test_medium_bubble_retry_corrects_question_without_changing_boxes(monkeypatch):
    class FakeRecognizer:
        def __call__(self, crop):
            return SimpleNamespace(
                boxes=[
                    np.asarray([[0, 0], [100, 0], [100, 20], [0, 20]]),
                    np.asarray([[0, 30], [100, 30], [100, 50], [0, 50]]),
                ],
                txts=["IS THAT IT,", "YOUNG MONARCH?"],
                scores=[0.97, 0.99],
            )

    ocr = LocalRapidOCR()
    monkeypatch.setattr(ocr, "_get_medium_engine", lambda: FakeRecognizer())
    lines = [
        {"text": "IS THAT !T,", "confidence": 0.82, "left": 40, "top": 10,
         "width": 100, "height": 20},
        {"text": "YOUNG MONARCH?", "confidence": 0.99, "left": 30, "top": 40,
         "width": 120, "height": 20},
    ]
    result = ocr._rescue_bubble_lines(np.full((60, 160, 3), 255, dtype=np.uint8), lines)

    assert [item["text"] for item in result] == ["IS THAT IT,", "YOUNG MONARCH?"]
    assert result[0]["left"] == lines[0]["left"]
    assert result[0]["ocr_rescued"] is True
    assert "ocr_rescued" not in result[1]


def test_medium_ocr_is_not_loaded_for_normal_high_confidence_dialogue(monkeypatch):
    ocr = LocalRapidOCR()
    monkeypatch.setattr(
        ocr, "_get_medium_engine",
        lambda: pytest.fail("medium OCR should stay unloaded for ordinary text"),
    )
    lines = [{"text": "WE ALWAYS GET HOME", "confidence": 0.96}]

    assert ocr._rescue_bubble_lines(np.full((60, 160, 3), 255, dtype=np.uint8), lines) is lines


def test_medium_ocr_retries_moderately_low_confidence_stylized_bubble(monkeypatch):
    class FakeRecognizer:
        def __call__(self, crop):
            return SimpleNamespace(
                boxes=[np.asarray([[0, 0], [100, 0], [100, 20], [0, 20]])],
                txts=["UP TO THIS!"], scores=[0.8961],
            )

    ocr = LocalRapidOCR()
    monkeypatch.setattr(ocr, "_get_medium_engine", lambda: FakeRecognizer())
    monkeypatch.setattr(
        LocalRapidOCR, "_english_word_counts",
        staticmethod(lambda text: (3, 3) if text == "UP TO THIS!" else (1, 3)),
    )
    lines = [{"text": "UPHO TO ISI", "confidence": 0.7627,
              "left": 10, "top": 10, "width": 100, "height": 20}]

    result = ocr._rescue_bubble_lines(
        np.full((60, 160, 3), 255, dtype=np.uint8), lines
    )

    assert result[0]["text"] == "UP TO THIS!"
    assert result[0]["ocr_rescued"] is True
    assert result[0]["confidence"] == 0.8961
    assert (result[0]["left"], result[0]["top"]) == (10, 10)


def test_medium_ocr_still_skips_confident_correct_stylized_bubble(monkeypatch):
    ocr = LocalRapidOCR()
    monkeypatch.setattr(
        ocr, "_get_medium_engine",
        lambda: pytest.fail("medium OCR should stay unloaded above retry threshold"),
    )
    lines = [{"text": "THE MAP OF THE MANOR!", "confidence": 0.9998}]

    assert ocr._rescue_bubble_lines(
        np.full((60, 160, 3), 255, dtype=np.uint8), lines
    ) is lines


def test_sparse_line_retry_recovers_missed_words_without_changing_geometry(monkeypatch):
    class FakeRecognizer:
        def __call__(self, crop):
            assert crop.shape == (140, 696, 3)
            return SimpleNamespace(
                boxes=[np.asarray([[0, 50], [600, 50], [600, 130], [0, 130]])],
                txts=["BUT WILL IT WORK?"], scores=[0.9978],
            )

    ocr = LocalRapidOCR()
    monkeypatch.setattr(ocr, "_get_engine", lambda: FakeRecognizer())
    monkeypatch.setattr(
        LocalRapidOCR, "_english_word_counts",
        staticmethod(lambda text: (2, 2) if text == "BUT  WORK" else (4, 4)),
    )
    lines = [{"text": "BUT  WORK", "confidence": 0.8093,
              "left": 120, "top": 220, "width": 300, "height": 50}]

    result = ocr._rescue_sparse_bubble_lines(
        np.full((140, 700, 3), 255, dtype=np.uint8), lines,
        origin=(100, 200), scale=2.0,
    )

    assert result[0]["text"] == "BUT WILL IT WORK?"
    assert result[0]["ocr_recovery_method"] == "targeted_sparse_line_crop"
    assert (result[0]["left"], result[0]["top"], result[0]["width"]) == (120, 220, 300)


def test_sparse_line_retry_skips_confident_or_compact_lines(monkeypatch):
    ocr = LocalRapidOCR()
    monkeypatch.setattr(
        ocr, "_get_engine",
        lambda: pytest.fail("targeted OCR should not run for ordinary lines"),
    )
    lines = [
        {"text": "THE MAP OF THE MANOR!", "confidence": 0.9998,
         "left": 300, "top": 200, "width": 450, "height": 40},
        {"text": "NOPE", "confidence": 0.70,
         "left": 300, "top": 200, "width": 80, "height": 40},
    ]

    assert ocr._rescue_sparse_bubble_lines(np.zeros((500, 800, 3)), lines) is lines


def test_ocr_retry_preserves_known_tokens_and_rejects_name_rewrites(monkeypatch):
    monkeypatch.setattr(
        LocalRapidOCR, "_get_spellchecker",
        classmethod(lambda cls: {"but", "butt", "work", "go", "if", "an"}),
    )

    assert LocalRapidOCR._preserves_known_tokens("BUT WORK", "BUT WILL IT WORK?")
    assert LocalRapidOCR._preserves_known_tokens("BUTT KE", "BUT WILL IT WORK?")
    assert not LocalRapidOCR._preserves_known_tokens("GU YIFAN", "GO IF AN")


def test_clipped_bubble_text_uses_detector_crop_only_for_edge_fragments(monkeypatch):
    import ocr_engine.local_rapidocr as local_ocr_module

    image = np.full((600, 500, 3), 255, dtype=np.uint8)
    bubble = SpeechBubble((189, 300, 380, 430), np.ones((130, 191), dtype=np.uint8) * 255, 0.9)
    roi_crops = []
    # The crop is padded around this bubble; return the wider local proposal
    # in crop-relative coordinates to verify page-coordinate remapping.
    detections = [ComicTextRegion("bubble", 0.93, (11, 74, 437, 216))]
    detector = SimpleNamespace(available=True, detect=lambda crop: (roi_crops.append(crop.shape), detections)[1])
    monkeypatch.setattr(local_ocr_module, "_get_comic_text_detector", lambda: detector)
    # Keep the test independent of the optional English dictionary package.
    monkeypatch.setattr(LocalRapidOCR, "_english_word_counts", staticmethod(
        lambda text: (sum(word in {"ONLY", "THE", "PUSH", "UPS", "YOUVE", "COMPLETED"}
                          for word in re.findall(r"[A-Z]+", text.upper())),
                      len(re.findall(r"[A-Z]+", text.upper())))
    ))
    crop_result = SimpleNamespace(
        boxes=[
            np.asarray([[40, 20], [150, 20], [150, 40], [40, 40]], dtype=np.float32),
            np.asarray([[35, 50], [180, 50], [180, 70], [35, 70]], dtype=np.float32),
            np.asarray([[80, 80], [125, 80], [125, 100], [80, 100]], dtype=np.float32),
            np.asarray([[45, 110], [155, 110], [155, 130], [45, 130]], dtype=np.float32),
        ],
        txts=["YOU'VE ONLY", "COMPLETED", "THE", "PUSH-UPS"],
        scores=[0.98, 0.99, 0.99, 0.96],
    )
    engine = LocalRapidOCR()
    monkeypatch.setattr(engine, "_get_engine", lambda: lambda _crop: crop_result)
    monkeypatch.setattr(engine, "_recover_edge_clipped_bubble_lines",
                        lambda _image, lines, regions, _ids: (lines, regions, 0))
    original_lines = [
        {"text": text, "left": 189, "top": top, "width": 110, "height": 20,
         "confidence": 0.72, "bubble_id": 1, "region_type": "speech_bubble"}
        for text, top in [("VE ONLY", 302), ("ETED THE", 337), ("SH-UPS.", 372)]
    ]
    original_region = {
        "text": "VE ONLY ETED THE SH-UPS.", "region_type": "speech_bubble",
        "left": 189, "top": 500, "width": 191, "height": 58, "bubble_id": 1,
        "bubble_bbox": [189, 500, 380, 558],
    }
    # Model three OCR fragments touching the segmentation mask's clipped left edge.
    original_region.update(top=300, height=130, bubble_bbox=[189, 300, 380, 430], left=189)
    bubbles = [bubble]
    (recovered_lines, recovered_regions, elapsed, detector_count, bubble_count,
     ocr_count, edge_seconds, edge_count) = (
        engine._recover_clipped_bubble_text(image, bubbles, original_lines,
                                           [original_region], "ltr")
    )

    assert "YOU'VE ONLY COMPLETED THE PUSH-UPS" == " ".join(line["text"] for line in recovered_lines)
    assert len(recovered_lines) == 4
    assert recovered_regions[0]["region_type"] == "external_text"
    assert recovered_regions[0]["ocr_recovered"] is True
    assert recovered_regions[0]["ocr_polygons"]
    assert elapsed >= 0 and detector_count == 1 and bubble_count == 1 and ocr_count == 4
    assert len(roi_crops) == 1 and roi_crops[0][0] < image.shape[0]
    assert roi_crops[0][1] < image.shape[1]
    assert edge_seconds >= 0 and edge_count == 0


def test_clipped_bubble_detector_uses_separate_rois_and_restores_page_coordinates(monkeypatch):
    import ocr_engine.local_rapidocr as local_ocr_module

    image = np.full((2000, 600, 3), 255, dtype=np.uint8)
    bubbles = [
        SpeechBubble((100, 100, 400, 250), np.ones((150, 300), dtype=np.uint8), 0.9),
        SpeechBubble((100, 1500, 400, 1650), np.ones((150, 300), dtype=np.uint8), 0.9),
    ]
    lines = [
        {"text": "NDITIONS", "left": 99, "top": 160, "width": 150, "height": 30,
         "confidence": 0.9, "bubble_id": 1},
        {"text": "BROKEN", "left": 99, "top": 1560, "width": 150, "height": 30,
         "confidence": 0.9, "bubble_id": 2},
    ]
    regions = [
        {"text": line["text"], "region_type": "speech_bubble", "bubble_id": bid,
         "left": bubble.bbox[0], "top": bubble.bbox[1],
         "width": bubble.bbox[2] - bubble.bbox[0],
         "height": bubble.bbox[3] - bubble.bbox[1], "bubble_bbox": list(bubble.bbox)}
        for bid, (line, bubble) in enumerate(zip(lines, bubbles), 1)
    ]
    monkeypatch.setattr(LocalRapidOCR, "_english_word_counts", staticmethod(
        lambda text: (sum(word in {"THAT", "IS", "CORRECT", "UNDERSTAND", "NOW"}
                          for word in re.findall(r"[A-Z]+", text.upper())),
                      len(re.findall(r"[A-Z]+", text.upper())))
    ))
    monkeypatch.setattr(LocalRapidOCR, "_looks_like_dialogue", staticmethod(lambda _text: True))
    monkeypatch.setattr(LocalRapidOCR, "_is_english_source_text", staticmethod(lambda _text: True))

    roi_shapes = []
    local_boxes = [(100, 100, 400, 250), (100, 180, 400, 330)]
    def detect(roi):
        index = len(roi_shapes)
        roi_shapes.append(roi.shape)
        return [ComicTextRegion("bubble", 0.95, local_boxes[index])]
    detector = SimpleNamespace(available=True, detect=detect)
    monkeypatch.setattr(local_ocr_module, "_get_comic_text_detector", lambda: detector)

    recognized = []
    candidates = ["THAT IS CORRECT", "I UNDERSTAND NOW"]
    def recognize(_crop):
        index = len(recognized)
        recognized.append(candidates[index])
        return SimpleNamespace(
            boxes=[np.asarray([[10, 10], [180, 10], [180, 35], [10, 35]], dtype=np.float32)],
            txts=[candidates[index]], scores=[0.99],
        )
    engine = LocalRapidOCR()
    monkeypatch.setattr(engine, "_get_engine", lambda: recognize)
    monkeypatch.setattr(engine, "_recover_edge_clipped_bubble_lines",
                        lambda _image, output_lines, output_regions, _ids:
                        (output_lines, output_regions, 0))

    recovered_lines, recovered_regions, elapsed, proposal_count, bubble_count, recovered_count, *_ = (
        engine._recover_clipped_bubble_text(image, bubbles, lines, regions, "ltr")
    )

    assert len(roi_shapes) == 2
    assert all(shape[0] < image.shape[0] // 2 for shape in roi_shapes)
    assert proposal_count == 2 and bubble_count == 2 and recovered_count == 2
    assert elapsed > 0
    assert recognized == candidates
    assert {region["text"] for region in recovered_regions if region.get("ocr_recovered")} == set(candidates)
    recovered_tops = sorted(line["top"] for line in recovered_lines if line["text"] in candidates)
    assert len(recovered_tops) == 2
    assert abs(recovered_tops[0] - 110) <= 1.5
    assert abs(recovered_tops[1] - 1510) <= 1.5


def test_clipped_bubble_rescue_skips_healthy_edge_text(monkeypatch):
    import ocr_engine.local_rapidocr as local_ocr_module

    engine = LocalRapidOCR()
    monkeypatch.setattr(LocalRapidOCR, "_english_word_counts", staticmethod(lambda _text: (2, 2)))
    monkeypatch.setattr(local_ocr_module, "_get_comic_text_detector", lambda: pytest.fail(
        "RT-DETR should not run when edge text has no OCR fragments"
    ))
    bubble = SpeechBubble((10, 10, 190, 90), np.ones((80, 180), dtype=np.uint8), 0.9)
    lines = [{"text": "KEEP GOING", "left": 10, "top": 30, "width": 100, "height": 20,
              "confidence": 0.98, "bubble_id": 1}]
    regions = [{"region_type": "speech_bubble", "bubble_id": 1, "left": 10, "top": 10,
                "width": 180, "height": 80, "bubble_bbox": [10, 10, 190, 90]}]

    result = engine._recover_clipped_bubble_text(
        np.zeros((100, 200, 3), dtype=np.uint8), [bubble], lines, regions, "ltr"
    )

    assert result[:2] == (lines, regions)
    assert result[2:] == (0.0, 0, 0, 0, 0.0, 0)


def test_contractions_at_bubble_edge_do_not_trigger_detector_retry(monkeypatch):
    import ocr_engine.local_rapidocr as local_ocr_module

    monkeypatch.setattr(local_ocr_module, "_SPELLCHECKER", {
        "haven't", "done", "that", "today", "reward",
    })
    monkeypatch.setattr(local_ocr_module, "_get_comic_text_detector", lambda: pytest.fail(
        "Valid apostrophe forms at the bubble edge must not request RT-DETR"
    ))
    bubble = SpeechBubble((10, 10, 190, 90), np.ones((80, 180), dtype=np.uint8), 0.9)
    line = {"text": "I HAVEN'T DONE THAT TODAY'S REWARD", "left": 10, "top": 30,
            "width": 170, "height": 20, "confidence": 0.98, "bubble_id": 1}
    region = {"text": line["text"], "region_type": "speech_bubble", "bubble_id": 1,
              "left": 10, "top": 10, "width": 180, "height": 80,
              "bubble_bbox": [10, 10, 190, 90]}

    result = LocalRapidOCR()._recover_clipped_bubble_text(
        np.zeros((100, 200, 3), dtype=np.uint8), [bubble], [line], [region], "ltr"
    )

    assert result[:2] == ([line], [region])
    assert result[2:] == (0.0, 0, 0, 0, 0.0, 0)


def test_expanded_edge_crop_recovers_clipped_words_before_page_detector(monkeypatch):
    import ocr_engine.local_rapidocr as local_ocr_module

    image = np.full((100, 500, 3), 255, dtype=np.uint8)
    bubble = SpeechBubble((195, 5, 400, 80), np.ones((75, 205), dtype=np.uint8), 0.91)
    line = {"text": "VE ONLY", "left": 189, "top": 20, "width": 160,
            "height": 30, "confidence": 0.99, "bubble_id": 1}
    region = {"text": "VE ONLY", "region_type": "speech_bubble", "bubble_id": 1,
              "bubble_bbox": [195, 5, 400, 80], "ocr_boxes": [[189, 20, 160, 30]],
              "ocr_polygons": [[[189, 20], [349, 20], [349, 50], [189, 50]]]}

    def fake_counts(text):
        tokens = re.findall(r"[A-Z]+", text.upper())
        return sum(token in {"ONLY", "YOUVE"} for token in tokens), len(tokens)

    # Treat the existing `VE` as a clipped fragment and ONLY as a trusted word.
    monkeypatch.setattr(LocalRapidOCR, "_english_word_counts", staticmethod(
        lambda text: (1, 2) if text.upper().replace("'", "") in {"VEONLY", "YOUVEONLY"}
        else fake_counts(text)
    ))
    monkeypatch.setattr(local_ocr_module, "_SPELLCHECKER", {"only"})
    engine = LocalRapidOCR()
    # Expanded bubble crop reaches the left page edge and includes both words.
    recovered_ocr = SimpleNamespace(
        boxes=[
            np.asarray([[65, 20], [160, 20], [160, 50], [65, 50]], dtype=np.float32),
            np.asarray([[150, 20], [240, 20], [240, 50], [150, 50]], dtype=np.float32),
            # Nearby panel digit must be rejected by confidence and line overlap.
            np.asarray([[20, 130], [40, 130], [40, 150], [20, 150]], dtype=np.float32),
        ],
        txts=["YOU'VE", "ONLY", "1"], scores=[0.89, 0.98, 0.59],
    )
    monkeypatch.setattr(engine, "_get_engine", lambda: lambda _crop: recovered_ocr)
    monkeypatch.setattr(local_ocr_module, "_get_comic_text_detector", lambda: pytest.fail(
        "A confidently recovered side-clipped line should skip page-level RT-DETR"
    ))

    result = engine._recover_clipped_bubble_text(image, [bubble], [line], [region], "ltr")

    lines_out, regions_out = result[:2]
    assert lines_out[0]["text"] == "YOU'VE ONLY"
    assert lines_out[0]["ocr_edge_crop_rescued"] is True
    assert regions_out[0]["text"] == "YOU'VE ONLY"
    assert regions_out[0]["ocr_recovery_method"] == "expanded_bubble_edge_crop"
    assert result[5] == 0  # RT-DETR was not invoked.
    assert result[7] == 1


def test_edge_crop_does_not_treat_single_letter_ocr_noise_as_a_trusted_word(monkeypatch):
    import ocr_engine.local_rapidocr as local_ocr_module

    image = np.full((600, 800, 3), 255, dtype=np.uint8)
    line = {"text": "E.., THE QUEST", "left": 162, "top": 363, "width": 289,
            "height": 42, "confidence": 0.967, "bubble_id": 4}
    region = {"text": line["text"], "region_type": "speech_bubble", "bubble_id": 4,
              "bubble_bbox": [167, 200, 735, 536],
              "ocr_boxes": [[162, 363, 289, 42]],
              "ocr_polygons": [[[162, 363], [451, 363], [451, 405], [162, 405]]]}
    monkeypatch.setattr(LocalRapidOCR, "_english_word_counts", staticmethod(
        lambda text: (sum(token in {"THE", "QUEST", "SEE"}
                          for token in re.findall(r"[A-Z]+", text.upper())),
                      sum(len(token) >= 2 for token in re.findall(r"[A-Z]+", text.upper())))
    ))
    monkeypatch.setattr(local_ocr_module, "_SPELLCHECKER", {"e", "the", "quest"})
    candidate = SimpleNamespace(
        boxes=[np.asarray([[110, 196], [452, 196], [452, 238], [110, 238]], dtype=np.float32)],
        txts=["SEE.., THE QUEST"], scores=[0.9829],
    )
    engine = LocalRapidOCR()
    monkeypatch.setattr(engine, "_get_engine", lambda: lambda _crop: candidate)

    lines, regions, rescued = engine._recover_edge_clipped_bubble_lines(
        image, [line], [region], {4},
    )

    assert rescued == 1
    assert lines[0]["text"] == "SEE.., THE QUEST"
    assert regions[0]["text"] == "SEE.., THE QUEST"


def test_clipped_single_line_bubble_uses_wider_detector_crop(monkeypatch):
    import ocr_engine.local_rapidocr as local_ocr_module

    image = np.full((120, 200, 3), 255, dtype=np.uint8)
    bubble = SpeechBubble((10, 10, 190, 90), np.ones((80, 180), dtype=np.uint8) * 255, 0.9)
    detector = SimpleNamespace(
        available=True,
        detect=lambda _image: [ComicTextRegion("bubble", 0.95, (0, 20, 170, 110))],
    )
    monkeypatch.setattr(local_ocr_module, "_get_comic_text_detector", lambda: detector)
    known = {"LETS", "SEE", "THE", "QUEST", "CONDITIONS", "ARE"}
    monkeypatch.setattr(LocalRapidOCR, "_english_word_counts", staticmethod(
        lambda text: (sum(word in known for word in re.findall(r"[A-Z]+", text.upper())),
                      len(re.findall(r"[A-Z]+", text.upper())))
    ))
    result = SimpleNamespace(
        boxes=[
            np.asarray([[20, 20], [75, 20], [75, 38], [20, 38]], dtype=np.float32),
            np.asarray([[18, 42], [155, 42], [155, 60], [18, 60]], dtype=np.float32),
            np.asarray([[25, 65], [155, 65], [155, 83], [25, 83]], dtype=np.float32),
        ],
        txts=["LET'S", "SEE... THE QUEST", "CONDITIONS ARE..."],
        scores=[0.99, 0.96, 0.98],
    )
    engine = LocalRapidOCR()
    monkeypatch.setattr(engine, "_get_engine", lambda: lambda _crop: result)
    monkeypatch.setattr(engine, "_recover_edge_clipped_bubble_lines",
                        lambda _image, lines, regions, _ids: (lines, regions, 0))
    lines = [{"text": "NDITIONS ARE...", "left": 10, "top": 40, "width": 120,
              "height": 20, "confidence": 0.98, "bubble_id": 1}]
    regions = [{"text": "NDITIONS ARE...", "region_type": "speech_bubble",
                "left": 10, "top": 10, "width": 180, "height": 80,
                "bubble_id": 1, "bubble_bbox": [10, 10, 190, 90]}]

    recovered_lines, recovered_regions, *_ = engine._recover_clipped_bubble_text(
        image, [bubble], lines, regions, "ltr"
    )

    assert [line["text"] for line in recovered_lines] == [
        "LET'S", "SEE... THE QUEST", "CONDITIONS ARE..."
    ]
    assert recovered_regions[0]["ocr_recovered"] is True
    assert "bubble_id" not in recovered_regions[0]


def test_adjacent_bubble_retry_recovers_missing_middle_lines_without_rtdetr(monkeypatch):
    image = np.full((360, 420, 3), 255, dtype=np.uint8)
    bubble = SpeechBubble((100, 100, 300, 180), np.ones((80, 200), dtype=np.uint8) * 255, 0.92)
    bubble_region = {
        "text": "IF YOU", "region_type": "speech_bubble",
        "left": 100, "top": 100, "width": 200, "height": 80,
        "bubble_id": 1, "bubble_bbox": [100, 100, 300, 180],
    }
    external_region = {
        "text": "WILL BECOME FAR MORE POWERFUL THAN YOU ARE NOW,",
        "region_type": "external_text", "left": 105, "top": 205,
        "width": 190, "height": 65,
    }
    original_lines = [
        {"text": "IF YOU", "left": 155, "top": 150, "width": 90,
         "height": 24, "confidence": 0.94, "bubble_id": 1},
        {"text": "WILL BECOME FAR MORE", "left": 105, "top": 205,
         "width": 190, "height": 20, "confidence": 0.95},
        {"text": "POWERFUL THAN YOU", "left": 112, "top": 225,
         "width": 176, "height": 20, "confidence": 0.95},
        {"text": "ARE NOW,", "left": 145, "top": 250,
         "width": 110, "height": 20, "confidence": 0.95},
    ]
    recognized = [
        "IF YOU", "FOLLOW HIM, YOU", "WILL BECOME FAR MORE",
        "POWERFUL THAN YOU", "ARE NOW,",
    ]

    class FakeOCR:
        def __call__(self, crop):
            assert crop.shape[0] >= 180
            boxes = [
                np.asarray([[10, y], [150, y], [150, y + 15], [10, y + 15]], dtype=np.float32)
                for y in (10, 35, 60, 85, 110)
            ]
            return SimpleNamespace(boxes=boxes, txts=recognized, scores=[0.95] * 5)

    engine = LocalRapidOCR()
    monkeypatch.setattr(engine, "_get_engine", lambda: FakeOCR())

    lines, regions, _elapsed, count = engine._recover_adjacent_bubble_continuations(
        image, [bubble], original_lines, [bubble_region, external_region]
    )

    assert count == 1
    assert len(regions) == 1
    assert regions[0]["ocr_recovery_method"] == "adjacent_bubble_crop"
    assert regions[0]["text"] == "IF YOU FOLLOW HIM, YOU WILL BECOME FAR MORE POWERFUL THAN YOU ARE NOW,"
    assert len(lines) == 5


def test_adjacent_bubble_retry_rejects_candidate_that_drops_existing_words(monkeypatch):
    image = np.full((360, 420, 3), 255, dtype=np.uint8)
    bubble = SpeechBubble((100, 100, 300, 180), np.ones((80, 200), dtype=np.uint8) * 255, 0.9)
    bubble_region = {
        "text": "IF YOU", "region_type": "speech_bubble",
        "left": 100, "top": 100, "width": 200, "height": 80,
        "bubble_id": 1, "bubble_bbox": [100, 100, 300, 180],
    }
    external_region = {
        "text": "WILL BECOME STRONGER", "region_type": "external_text",
        "left": 105, "top": 205, "width": 190, "height": 45,
    }
    lines_in = [
        {"text": "IF YOU", "left": 155, "top": 150, "width": 90,
         "height": 24, "confidence": 0.94, "bubble_id": 1},
        {"text": "WILL BECOME STRONGER", "left": 105, "top": 205,
         "width": 190, "height": 30, "confidence": 0.95},
    ]

    class FakeOCR:
        def __call__(self, crop):
            return SimpleNamespace(
                boxes=[np.asarray([[10, 10], [150, 10], [150, 25], [10, 25]], dtype=np.float32)],
                txts=["YOU BECOME STRONGER"], scores=[0.98],
            )

    engine = LocalRapidOCR()
    monkeypatch.setattr(engine, "_get_engine", lambda: FakeOCR())

    lines_out, regions_out, _elapsed, count = engine._recover_adjacent_bubble_continuations(
        image, [bubble], lines_in, [bubble_region, external_region]
    )

    assert count == 0
    assert lines_out == lines_in
    assert regions_out == [bubble_region, external_region]


def test_external_ocr_normalization_preserves_original_and_translation_for_review(monkeypatch):
    monkeypatch.setattr(
        LocalRapidOCR, "_english_word_counts",
        staticmethod(lambda _text: (15, 17)),
    )
    region = {
        "text": "ONOTICE YOU HAVE ELIMINATED ALL OTHERWORLDLYINHABITANTS",
        "translated": "إشعار: لقد قضيت على السكان من العوالم الأخرى.",
        "region_type": "external_text", "confidence": 0.99,
    }
    original_text = region["text"]
    original_translation = region["translated"]

    result = LocalRapidOCR._add_external_ocr_review_hint(region)

    assert result["ocr_review_required"] is True
    assert result["ocr_review_reason"] == "multiple_unrecognized_english_tokens"
    assert result["ocr_unrecognized_word_count"] == 2
    assert result["text"] == "NOTICE YOU HAVE ELIMINATED ALL OTHERWORLDLY INHABITANTS"
    assert result["ocr_normalization_original_text"] == original_text
    assert result["translated"] == original_translation


def test_ocr_review_hint_does_not_flag_speech_bubble_or_single_unknown_word(monkeypatch):
    monkeypatch.setattr(
        LocalRapidOCR, "_english_word_counts",
        staticmethod(lambda _text: (4, 5)),
    )
    speech = {"text": "Dialogue with a character name", "region_type": "speech_bubble"}
    caption = {"text": "YOU'VE ONLY COMPLETED THE PUSH-UPS", "region_type": "external_text"}

    LocalRapidOCR._add_external_ocr_review_hint(speech)
    LocalRapidOCR._add_external_ocr_review_hint(caption)

    assert "ocr_review_required" not in speech
    assert "ocr_review_required" not in caption


def test_external_ocr_review_flags_unrecognized_hyphenated_token_without_rewriting(monkeypatch):
    known_words = {"shop", "purchase", "window"}
    monkeypatch.setattr(LocalRapidOCR, "_get_spellchecker", staticmethod(lambda: known_words))
    monkeypatch.setattr(
        LocalRapidOCR, "_english_word_counts",
        staticmethod(lambda _text: (0, 2)),
    )
    region = {
        "text": "PURO-ASE", "region_type": "external_text", "confidence": 0.889,
    }

    result = LocalRapidOCR._add_external_ocr_review_hint(region)

    assert result["ocr_review_required"] is True
    assert result["ocr_review_reason"] == "unrecognized_hyphenated_ocr_token"
    assert result["ocr_unrecognized_hyphenated_tokens"] == ["PURO-ASE"]
    assert result["text"] == "PURO-ASE"


def test_external_ocr_review_does_not_flag_known_or_short_stutter_hyphens(monkeypatch):
    known_words = {"shop", "window", "world", "class"}
    monkeypatch.setattr(LocalRapidOCR, "_get_spellchecker", staticmethod(lambda: known_words))
    monkeypatch.setattr(
        LocalRapidOCR, "_english_word_counts",
        staticmethod(lambda _text: (0, 2)),
    )
    known_compound = {
        "text": "WORLD-CLASS", "region_type": "external_text", "confidence": 0.9,
    }
    stutter = {"text": "SH-SHOP", "region_type": "external_text", "confidence": 0.9}

    LocalRapidOCR._add_external_ocr_review_hint(known_compound)
    LocalRapidOCR._add_external_ocr_review_hint(stutter)

    assert "ocr_review_required" not in known_compound
    assert "ocr_review_required" not in stutter


def test_external_ocr_review_flags_unique_fused_words_without_rewriting(monkeypatch):
    class FakeSpellChecker(set):
        def word_usage_frequency(self, word):
            return {"has": 0.001, "ended": 0.0002}.get(word, 0.0)

    known_words = FakeSpellChecker({
        "notice", "you", "have", "eliminated", "all", "the", "shadow", "dungeon",
        "st", "has", "ended",
    })
    monkeypatch.setattr(LocalRapidOCR, "_get_spellchecker", staticmethod(lambda: known_words))
    monkeypatch.setattr(
        LocalRapidOCR, "_english_word_counts",
        staticmethod(lambda _text: (11, 12)),
    )
    region = {
        "text": "NOTICE YOU HAVE ELIMINATED ALL THE SHADOW DUNGEON STHAS ENDED",
        "region_type": "external_text", "confidence": 0.98,
    }

    result = LocalRapidOCR._add_external_ocr_review_hint(region)

    assert result["ocr_review_required"] is True
    assert result["ocr_review_reason"] == "possible_concatenated_english_tokens"
    assert result["ocr_unrecognized_word_count"] == 1
    assert result["ocr_suspicious_fused_tokens"] == ["STHAS"]
    assert result["text"].endswith("STHAS ENDED")


def test_external_ocr_review_does_not_flag_single_unknown_proper_name(monkeypatch):
    monkeypatch.setattr(
        LocalRapidOCR, "_get_spellchecker",
        staticmethod(lambda: {"all", "done", "while", "carrying", "on", "your", "back"}),
    )
    monkeypatch.setattr(
        LocalRapidOCR, "_english_word_counts",
        staticmethod(lambda _text: (8, 9)),
    )
    region = {
        "text": "ALL DONE WHILE CARRYING AMMUT ON YOUR BACK",
        "region_type": "external_text", "confidence": 0.98,
    }

    LocalRapidOCR._add_external_ocr_review_hint(region)

    assert "ocr_review_required" not in region


def test_fused_token_review_requires_a_common_second_word(monkeypatch):
    class FakeSpellChecker(set):
        def word_usage_frequency(self, word):
            return {
                "mut": 1e-8, "has": 1e-3,
                "kat": 1.9e-7, "ya": 5.2e-5,
            }.get(word, 0.0)

    spellchecker = FakeSpellChecker({"am", "mut", "st", "has", "kat", "ya"})
    monkeypatch.setattr(LocalRapidOCR, "_get_spellchecker", staticmethod(lambda: spellchecker))
    monkeypatch.setattr(
        LocalRapidOCR, "_english_word_counts",
        staticmethod(lambda _text: (0, 1)),
    )
    proper_name = {"text": "AMMUT", "region_type": "external_text"}
    fused_words = {"text": "STHAS", "region_type": "external_text"}
    katya_name = {"text": "KATYA", "region_type": "external_text"}

    LocalRapidOCR._add_external_ocr_review_hint(proper_name)
    LocalRapidOCR._add_external_ocr_review_hint(fused_words)
    LocalRapidOCR._add_external_ocr_review_hint(katya_name)

    assert "ocr_review_required" not in proper_name
    assert fused_words["ocr_review_reason"] == "possible_concatenated_english_tokens"
    assert "ocr_review_required" not in katya_name


def test_english_word_counts_remains_generic_for_unit_tokens(monkeypatch):
    monkeypatch.setattr(
        LocalRapidOCR, "_get_spellchecker", staticmethod(lambda: {"run"}),
    )

    assert LocalRapidOCR._english_word_counts("RUN [0/10KM]") == (1, 2)


def test_progress_counter_is_excluded_from_review_hint_word_count(monkeypatch):
    known_words = {"all", "done", "while", "carrying", "on", "your", "back"}
    monkeypatch.setattr(LocalRapidOCR, "_get_spellchecker", staticmethod(lambda: known_words))
    region = {
        "text": "[0/10KM] *ALL DONE WHILE CARRYING AMMUT ON YOUR BACK.",
        "region_type": "external_text", "confidence": 0.98,
    }

    LocalRapidOCR._add_external_ocr_review_hint(region)

    assert "ocr_review_required" not in region
    assert region["text"].startswith("[0/10KM]")


def test_external_ocr_review_flags_code_dense_fragments_without_rewriting(monkeypatch):
    monkeypatch.setattr(LocalRapidOCR, "_get_spellchecker", staticmethod(lambda: {"lou"}))
    monkeypatch.setattr(
        LocalRapidOCR, "_english_word_counts", staticmethod(lambda _text: (4, 4)),
    )
    region = {
        "text": "DD: cu TS: LOU", "region_type": "external_text", "confidence": 0.95,
    }

    LocalRapidOCR._add_external_ocr_review_hint(region)

    assert region["ocr_review_required"] is True
    assert region["ocr_review_reason"] == "ambiguous_short_uppercase_tokens"
    assert region["ocr_suspicious_short_uppercase_tokens"] == ["DD", "LOU", "TS"]
    assert region["text"] == "DD: cu TS: LOU"


def test_external_ocr_review_does_not_flag_common_short_english_words(monkeypatch):
    monkeypatch.setattr(LocalRapidOCR, "_get_spellchecker", staticmethod(lambda: set()))
    monkeypatch.setattr(
        LocalRapidOCR, "_english_word_counts", staticmethod(lambda _text: (7, 7)),
    )
    region = {
        "text": "NO, IT IS UP TO US, OK?", "region_type": "external_text", "confidence": 0.95,
    }

    LocalRapidOCR._add_external_ocr_review_hint(region)

    assert "ocr_review_required" not in region
    assert "ocr_suspicious_short_uppercase_tokens" not in region


def test_external_ocr_review_does_not_flag_connector_or_domain_suffix(monkeypatch):
    monkeypatch.setattr(LocalRapidOCR, "_get_spellchecker", staticmethod(lambda: set()))
    monkeypatch.setattr(
        LocalRapidOCR, "_english_word_counts", staticmethod(lambda _text: (13, 13)),
    )
    region = {
        "text": "IF YOU ENJOYED THIS SERIES AND WANT A FASTER RELEASE PLEASE READ ON MANHUAPLUS.COM",
        "region_type": "external_text", "confidence": 0.97,
    }

    LocalRapidOCR._add_external_ocr_review_hint(region)

    assert "ocr_review_required" not in region
    assert "ocr_suspicious_short_uppercase_tokens" not in region


def test_merged_bubble_splits_three_line_utterance_from_two_line_followup():
    lines = [
        {"text": f"UPPER {index}", "left": 70, "top": 100 + index * 24,
         "width": 100, "height": 18}
        for index in range(3)
    ] + [
        {"text": f"LOWER {index}", "left": 80, "top": 290 + index * 25,
         "width": 90, "height": 18}
        for index in range(2)
    ]

    split = LocalRapidOCR._split_bubble_columns(lines, 20, 240, 80, 360)

    assert len(split) == 2
    assert [line["text"] for line in split[0]["lines"]] == [
        "UPPER 0", "UPPER 1", "UPPER 2"
    ]
    assert [line["text"] for line in split[1]["lines"]] == ["LOWER 0", "LOWER 1"]
    assert split[0]["clip"][3] <= split[1]["clip"][1]


def test_large_vertical_gap_splits_two_dialogue_balloons_in_one_mask():
    lines = [
        {"text": "IS THAT IT,", "left": 277, "top": 5084, "width": 196, "height": 65},
        {"text": "YOUNG MONARCH?", "left": 225, "top": 5130, "width": 307, "height": 68},
        {"text": "VE ONLY", "left": 189, "top": 5302, "width": 160, "height": 70},
        {"text": "ETED THE", "left": 189, "top": 5357, "width": 200, "height": 65},
        {"text": "SH-UPS.", "left": 189, "top": 5405, "width": 143, "height": 71},
    ]

    split = LocalRapidOCR._split_bubble_columns(lines, 189, 560, 5002, 5558)

    assert len(split) == 2
    assert [line["text"] for line in split[0]["lines"]] == [
        "IS THAT IT,", "YOUNG MONARCH?",
    ]
    assert [line["text"] for line in split[1]["lines"]] == [
        "VE ONLY", "ETED THE", "SH-UPS.",
    ]


def test_connected_dark_balloons_with_three_and_four_lines_split_at_large_gap():
    lines = [
        {"text": f"FIRST {index}", "left": 100, "top": 4800 + index * 50,
         "width": 180, "height": 50}
        for index in range(3)
    ] + [
        {"text": f"SECOND {index}", "left": 200, "top": 5050 + index * 50,
         "width": 280, "height": 50}
        for index in range(4)
    ]

    split = LocalRapidOCR._split_bubble_columns(lines, 60, 650, 4700, 5300)

    assert [len(item["lines"]) for item in split] == [3, 4]


def test_short_side_annotation_stays_with_its_speech_balloon():
    # One or two OCR fragments offset beside a paragraph often represent a
    # small annotation in the same balloon, not a second dialogue column.
    lines = [
        {"text": "WE ALWAYS GET", "left": 485, "top": 330, "width": 38, "height": 12},
        {"text": "SWARMED WITH", "left": 480, "top": 345, "width": 48, "height": 12},
        {"text": "PICKUP ARTISTS", "left": 480, "top": 360, "width": 48, "height": 12},
        {"text": "WHEN I GO OUT WITH YOU.", "left": 480, "top": 375, "width": 48, "height": 22},
        {"text": "IT'S SUPER", "left": 458, "top": 415, "width": 22, "height": 14},
        {"text": "ANNOYING", "left": 458, "top": 431, "width": 22, "height": 14},
    ]
    split = LocalRapidOCR._split_bubble_columns(lines, 455, 545, 320, 450)
    assert len(split) == 1
    assert len(split[0]["lines"]) == 6


def test_small_speech_balloons_are_not_dropped_by_page_area_cutoff():
    # Screenshot page is 588x777; an eroded whisper balloon (~950 px) must
    # survive so OCR can reject noise based on its actual text.
    assert _minimum_safe_mask_area(588, 777) < 950


def test_webtoon_caption_filter_rejects_square_panels_but_keeps_oval_balloons():
    bounds = (20, 20, 240, 160)
    rectangular = np.zeros((190, 260), dtype=np.uint8)
    rectangular[20:160, 20:240] = 255
    oval = np.zeros_like(rectangular)
    cv2.ellipse(oval, (130, 90), (110, 70), 0, 0, 360, 255, -1)

    assert _is_rectangular_caption(rectangular, bounds, 1.0)
    assert not _is_rectangular_caption(oval, bounds, 0.78)


def test_contained_bubble_masks_deduplicate_but_keep_the_union_crop():
    large = np.zeros((100, 120), dtype=np.uint8)
    large[5:95, 5:115] = 255
    small = np.zeros_like(large)
    small[20:70, 35:85] = 255
    candidates = [
        SpeechBubble((5, 5, 115, 95), large, large.copy(), 0.6),
        SpeechBubble((35, 20, 85, 70), small, small.copy(), 0.95),
    ]
    unique = deduplicate_bubbles(candidates)
    assert len(unique) == 1
    assert unique[0].bbox == (5, 5, 115, 95)


def test_dialogue_filter_skips_only_repeated_letter_sound_marks():
    assert LocalRapidOCR._looks_like_dialogue("No, no! Leave me alone!")
    assert not LocalRapidOCR._looks_like_dialogue("EEE EEE EK!")
    assert not LocalRapidOCR._looks_like_dialogue("！")


def test_external_ocr_skips_nonfinite_and_out_of_page_polygons():
    engine = LocalRapidOCR()
    result = SimpleNamespace(
        boxes=[
            np.array([[10, 10], [60, 10], [60, 24], [10, 24]], dtype=np.float32),
            np.array([[np.nan, 10], [60, 10], [60, 24], [10, 24]], dtype=np.float32),
            np.array([[110, 10], [140, 10], [140, 24], [110, 24]], dtype=np.float32),
            np.array([[70, 40], [78, 40], [78, 55], [70, 55]], dtype=np.float32),
            np.array([[20, 60], [80, 60], [80, 90], [20, 90]], dtype=np.float32),
        ],
        txts=["Readable caption", "Broken coordinates", "Outside page", "a", "b"],
        scores=[0.95, 0.99, 0.99, 0.99, 0.99],
    )
    engine._get_engine = lambda: (lambda _tile: result)
    image = np.full((100, 100, 3), 255, dtype=np.uint8)

    lines = engine._extract_external_lines(image, [], "ltr")

    assert [line["text"] for line in lines] == ["Readable caption", "b"]
    assert all(0 <= x < 100 and 0 <= y < 100 for x, y in lines[0]["ocr_polygon"])


def test_numeric_external_candidate_crop_retry_recovers_common_word(monkeypatch):
    class FakeSpellChecker(set):
        def word_usage_frequency(self, word):
            return 0.01 if word == "good" else 0.0

    monkeypatch.setattr(LocalRapidOCR, "_get_spellchecker",
                        staticmethod(lambda: FakeSpellChecker({"good"})))
    monkeypatch.setattr(LocalRapidOCR, "_english_word_counts",
                        staticmethod(lambda text: (1, 1) if text == "GOOD." else (0, 1)))
    monkeypatch.setattr(LocalRapidOCR, "_is_english_source_text",
                        staticmethod(lambda text: text == "GOOD."))
    monkeypatch.setattr(LocalRapidOCR, "_is_likely_external_sound_effect",
                        staticmethod(lambda _text: False))
    engine = LocalRapidOCR()
    engine._get_engine = lambda: SimpleNamespace(
        recognize_txt=lambda crops: SimpleNamespace(txts=["GOOD."], scores=[0.963])
    )
    line = {"text": "2009", "left": 10, "top": 10, "width": 90,
            "height": 30, "confidence": 0.869}

    recovered = engine._rescue_numeric_external_candidate(
        np.zeros((60, 120, 3), dtype=np.uint8), line
    )

    assert recovered["text"] == "GOOD."
    assert recovered["ocr_rescued"] is True
    assert recovered["ocr_recovery_method"] == "numeric_external_candidate_crop_retry"


def test_numeric_external_candidate_crop_retry_rejects_nonword(monkeypatch):
    monkeypatch.setattr(LocalRapidOCR, "_english_word_counts",
                        staticmethod(lambda _text: (0, 1)))
    monkeypatch.setattr(LocalRapidOCR, "_is_english_source_text",
                        staticmethod(lambda _text: False))
    engine = LocalRapidOCR()
    engine._get_engine = lambda: SimpleNamespace(
        recognize_txt=lambda crops: SimpleNamespace(txts=["2009"], scores=[0.99])
    )
    line = {"text": "2009", "left": 10, "top": 10, "width": 90,
            "height": 30, "confidence": 0.869}

    assert engine._rescue_numeric_external_candidate(
        np.zeros((60, 120, 3), dtype=np.uint8), line
    ) is None


def test_external_ocr_keeps_aligned_bracketed_counters_out_of_translation_lines():
    engine = LocalRapidOCR()
    result = SimpleNamespace(
        boxes=[
            np.array([[20, 20], [130, 20], [130, 43], [20, 43]], dtype=np.float32),
            np.array([[230, 18], [310, 18], [310, 46], [230, 46]], dtype=np.float32),
            np.array([[20, 65], [65, 65], [65, 86], [20, 86]], dtype=np.float32),
        ],
        txts=["PUSH-UPS", "[0/100]", "12345"],
        scores=[0.99, 0.99, 0.99],
    )
    engine._get_engine = lambda: (lambda _tile: result)
    image = np.full((100, 400, 3), 255, dtype=np.uint8)

    lines, annotations = engine._extract_external_lines(
        image, [], "ltr", include_annotations=True
    )

    assert [line["text"] for line in lines] == ["PUSH-UPS"]
    assert [(item["text"], item["region_type"]) for item in annotations] == [
        ("[0/100]", "nontranslatable_counter")
    ]
    assert annotations[0]["left"] == 230
    assert annotations[0]["top"] == 18


@pytest.mark.parametrize("text", ["[0/100]", "[0/10KM]", " [12 / 999 M] "])
def test_counter_annotation_pattern_is_narrow(text):
    assert LocalRapidOCR._is_counter_annotation(text)


@pytest.mark.parametrize("text", ["12345", "/100", "0/100", "[100/10000]"])
def test_counter_annotation_pattern_rejects_unrelated_numbers(text):
    assert not LocalRapidOCR._is_counter_annotation(text)


@pytest.mark.parametrize("text", ["CH 033", "CHAPTER 117", "ch-09", "Chapter #120"])
def test_standalone_chapter_markers_are_not_dialogue(text):
    assert LocalRapidOCR._is_chapter_marker(text)


@pytest.mark.parametrize("text", ["CH, I can't believe it!", "Chapter 3 was strange.", "CH 033 was printed"])
def test_chapter_marker_filter_does_not_remove_dialogue(text):
    assert not LocalRapidOCR._is_chapter_marker(text)


def test_detector_supplement_skips_only_confident_overlapping_ocr():
    detector_box = (0, 0, 100, 100)
    confident_line = {
        "left": 0, "top": 0, "width": 25, "height": 100, "confidence": 0.95,
    }
    uncertain_line = {
        "left": 0, "top": 0, "width": 60, "height": 100, "confidence": 0.72,
    }

    assert LocalRapidOCR._detector_region_already_ocrd(detector_box, [confident_line])
    assert not LocalRapidOCR._detector_region_already_ocrd(detector_box, [uncertain_line])


def test_low_confidence_speech_bubble_ocr_is_flagged_for_review():
    region = {"region_type": "speech_bubble", "confidence": 0.8242,
              "text": "SO I TO COVER MY EARS?"}

    reviewed = LocalRapidOCR._add_speech_bubble_ocr_review_hint(region)

    assert reviewed["text"] == "SO I TO COVER MY EARS?"
    assert reviewed["ocr_review_required"] is True
    assert reviewed["ocr_review_reason"] == "low_confidence_speech_bubble_ocr"


def test_high_confidence_article_plural_ocr_error_is_flagged_without_rewriting():
    region = {
        "region_type": "speech_bubble",
        "confidence": 0.9968,
        "text": "AND THERE MIGHT BE A DEPLOYMENTS EVEN IN A PLACE WE CAN'T SEE.",
    }

    reviewed = LocalRapidOCR._add_speech_bubble_ocr_review_hint(region)

    assert reviewed["ocr_review_required"] is True
    assert "singular_article_before_plural_ment" in reviewed["ocr_review_reasons"]
    assert reviewed["text"] == "AND THERE MIGHT BE A DEPLOYMENTS EVEN IN A PLACE WE CAN'T SEE."


@pytest.mark.parametrize("text", [
    "AND THERE MIGHT BE A DEPLOYMENT EVEN IN A PLACE WE CAN'T SEE.",
    "THEY'RE DEPLOYMENTS THAT MAY ARRIVE LATER.",
])
def test_plural_ment_review_cue_only_flags_singular_article_pattern(text):
    flags, _isolated = LocalRapidOCR._speech_bubble_ocr_structure_flags(text)

    assert "singular_article_before_plural_ment" not in flags


def test_exact_bubble_duplicate_with_aligned_ocr_boxes_is_removed_at_shallow_mask_overlap():
    first = {
        "text": "SOARING CLOUD LUAN SWORD!", "left": 344, "top": 6591,
        "width": 524, "height": 280, "confidence": 0.996,
        "region_type": "speech_bubble", "bubble_id": 4,
        "ocr_boxes": [[479, 6796, 274, 43], [503, 6852, 230, 23]],
    }
    second = {
        "text": "SOARING CLOUD LUAN SWORD!", "left": 348, "top": 6727,
        "width": 513, "height": 293, "confidence": 0.9997,
        "region_type": "speech_bubble", "bubble_id": 5,
        "ocr_boxes": [[481, 6797, 270, 41], [498, 6850, 236, 43]],
    }

    result = LocalRapidOCR._deduplicate_overlapping_regions([first, second])

    assert len(result) == 1
    assert result[0] is second


def test_fragmented_bubble_duplicate_with_aligned_ocr_boxes_keeps_complete_phrase():
    partial = {
        "text": "WE CAN'T JUST LEAVE HIM. DID YOU FORGET HOW MUCH",
        "left": 208, "top": 6527, "width": 538, "height": 345,
        "confidence": 0.9985, "region_type": "speech_bubble", "bubble_id": 4,
        "ocr_boxes": [[268, 6752, 436, 41], [257, 6807, 463, 40]],
    }
    complete = {
        "text": "WE CAN'T JUST LEAVE HIM. DID YOU FORGET HOW MUCH HIS FATHER HELPED US?",
        "left": 212, "top": 6727, "width": 522, "height": 321,
        "confidence": 0.9905, "region_type": "speech_bubble", "bubble_id": 5,
        "ocr_boxes": [[269, 6752, 434, 40], [256, 6807, 463, 39],
                      [289, 6862, 397, 38]],
    }

    result = LocalRapidOCR._deduplicate_overlapping_regions([partial, complete])

    assert len(result) == 1
    assert result[0] is complete


def test_aligned_glyph_boxes_remove_partial_duplicate_despite_ocr_token_errors():
    complete = {
        "text": "LU CHAOGE! AS LONG AS FLAME GATHERING BIRD SWOOPS DOWN ON YOU, EVEN IF YOU DON'T DIE, YOU'LL BE SEVERELY INJURED! IT'S A POWERFUL OFFENSIVE BEAST AT THE SEVENTH RANK THIRD HEAVENLY LAYER!",
        "left": 176, "top": 23243, "width": 601, "height": 784,
        "confidence": 0.9914, "region_type": "speech_bubble", "bubble_id": 11,
        "ocr_boxes": [[187, 23391, 336, 39], [186, 23434, 338, 39],
                      [187, 23477, 337, 39], [192, 23521, 328, 39],
                      [190, 23563, 332, 40], [203, 23606, 304, 39],
                      [457, 23721, 274, 38], [430, 23762, 330, 41],
                      [445, 23807, 303, 39], [467, 23850, 259, 39],
                      [536, 23891, 116, 43]],
    }
    corrupted_partial = {
        "text": "LU CHAUGE! AS LUNG AS FLAME GATHERING BIRD SWOOPS DOWN ON YOU, EVEN IF YOU DON'T DIE, YOU'LL BE SEVERELY INJURED!",
        "left": 189, "top": 23406, "width": 412, "height": 290,
        "confidence": 0.9902, "region_type": "speech_bubble", "bubble_id": 12,
        "ocr_boxes": [[187, 23402, 335, 28], [184, 23434, 341, 40],
                      [186, 23477, 339, 40], [191, 23519, 330, 42],
                      [187, 23562, 337, 43], [203, 23605, 305, 42]],
    }

    assert LocalRapidOCR._deduplicate_overlapping_regions(
        [complete, corrupted_partial]
    ) == [complete]


def test_glyph_aligned_partial_overlap_keeps_the_longer_chapter_phrase():
    partial = {
        "text": "EVEN A SWORD CULTIVATOR OF MY SAME RANK, THAT ELDER FROM THE SWORD SECT A",
        "left": 354, "top": 7703, "width": 468, "height": 249,
        "confidence": 0.9172, "region_type": "speech_bubble", "bubble_id": 6,
        "ocr_boxes": [[467, 7805, 244, 41], [393, 7849, 393, 40],
                      [388, 7891, 401, 43], [413, 7936, 279, 21],
                      [728, 7936, 43, 21]],
    }
    complete = {
        "text": "EVEN A SWORD CULTIVATOR OF MY SAME RANK, THAT ELDER FROM THE SWORD SECT... AS LONG AS THEY'RE WITHIN MY DOMAIN, THEIR MIND WILL COLLAPSE.",
        "left": 344, "top": 7806, "width": 481, "height": 384,
        "confidence": 0.9929, "region_type": "speech_bubble", "bubble_id": 7,
        "ocr_boxes": [[468, 7805, 243, 40], [394, 7849, 391, 39],
                      [390, 7892, 398, 40], [411, 7935, 362, 40],
                      [392, 7979, 397, 39], [394, 8021, 376, 41],
                      [461, 8064, 253, 40]],
    }

    assert LocalRapidOCR._deduplicate_overlapping_regions(
        [partial, complete]
    ) == [complete]


def test_shallowly_overlapping_repeated_words_with_distinct_ocr_boxes_are_preserved():
    first = {
        "text": "YOU KNOW MY OLD MAN?", "left": 20, "top": 100,
        "width": 300, "height": 180, "confidence": 0.99,
        "region_type": "speech_bubble", "bubble_id": 1,
        "ocr_boxes": [[50, 145, 180, 32]],
    }
    second = {
        "text": "YOU KNOW MY OLD MAN?", "left": 20, "top": 210,
        "width": 300, "height": 180, "confidence": 0.99,
        "region_type": "speech_bubble", "bubble_id": 2,
        "ocr_boxes": [[50, 255, 180, 32]],
    }

    assert LocalRapidOCR._deduplicate_overlapping_regions([first, second]) == [first, second]


def test_fuzzy_overlapping_bubble_duplicates_keep_more_complete_reading():

    regions = [
        {
            "text": "YOU'RE QUITE THE ECCENTRIC YOURSELF, JUST LIKE THAT OLD GEEZER. HEH, HEH...",
            "left": 219, "top": 7689, "width": 454, "height": 333,
            "confidence": 0.9867, "region_type": "speech_bubble", "bubble_id": 4,
        },
        {
            "text": "YOU'RE QUITE ECCENTRIC YOUF JUST LIKE THAT GEEZER. HE HEH...",
            "left": 200, "top": 7723, "width": 334, "height": 250,
            "confidence": 0.9756, "region_type": "speech_bubble", "bubble_id": 3,
        },
    ]

    result = LocalRapidOCR._deduplicate_overlapping_regions(regions)

    assert len(result) == 1
    assert result[0]["text"] == regions[0]["text"]


def test_fuzzy_bubble_dedup_preserves_separate_repeated_dialogue():
    first = {
        "text": "YOU KNOW MY OLD MAN?", "left": 20, "top": 100,
        "width": 300, "height": 180, "confidence": 0.99,
        "region_type": "speech_bubble", "bubble_id": 1,
    }
    second = dict(first, left=20, top=400, bubble_id=2)

    assert LocalRapidOCR._deduplicate_overlapping_regions([first, second]) == [first, second]


def test_adjacent_fragmented_bubble_retry_recovers_overlapping_dialogue(monkeypatch):
    class FakeRecognizer:
        def __call__(self, crop):
            return SimpleNamespace(
                boxes=[
                    np.asarray([[5, y], [110, y], [110, y + 20], [5, y + 20]])
                    for y in (10, 45, 80, 115, 150)
                ],
                txts=[
                    "IT SEEMS", "THAT LORD TELLOS,", "OUR ONE TRUE GOD,",
                    "HAS ANSWERED", "MY PRAYERS.",
                ],
                scores=[0.99, 0.98, 0.99, 0.99, 0.99],
            )

    ocr = LocalRapidOCR()
    monkeypatch.setattr(ocr, "_get_engine", lambda: FakeRecognizer())
    fragment = {
        "text": "IT SEEN THAT LORD T", "region_type": "speech_bubble",
        "left": 38, "top": 349, "width": 454, "height": 137,
        "confidence": 0.9994, "bubble_id": 1,
    }
    neighbor = {
        "text": "OUR ONE TRUE GOD, HAS ANSWERED MY PRAYERS.",
        "region_type": "speech_bubble", "left": 246, "top": 488,
        "width": 390, "height": 123, "confidence": 0.9957,
    }
    lines = [
        {"text": "IT SEEN THAT LORD T", "left": 50, "top": 360,
         "width": 400, "height": 25, "confidence": 0.99, "bubble_id": 1},
        {"text": "OUR ONE TRUE GOD, HAS ANSWERED MY PRAYERS.",
         "left": 260, "top": 500, "width": 350, "height": 35, "confidence": 0.99},
    ]

    new_lines, new_regions, _elapsed, recovered = ocr._recover_adjacent_fragmented_bubble_pair(
        np.zeros((800, 700, 3), dtype=np.uint8), lines, [fragment, neighbor], "ltr"
    )

    assert recovered == 1
    assert len(new_regions) == 1
    assert new_regions[0]["text"] == (
        "IT SEEMS THAT LORD TELLOS, OUR ONE TRUE GOD, HAS ANSWERED MY PRAYERS."
    )
    assert new_regions[0]["ocr_recovery_method"] == "adjacent_fragmented_bubble_crop"
    assert len(new_lines) == 5


def test_adjacent_fragmented_bubble_retry_skips_clean_dialogue(monkeypatch):
    ocr = LocalRapidOCR()
    monkeypatch.setattr(
        ocr, "_get_engine", lambda: pytest.fail("clean dialogue must not be retried")
    )
    region = {
        "text": "THE OLD MAN HAS ANSWERED MY PRAYERS.",
        "region_type": "speech_bubble", "left": 20, "top": 20,
        "width": 300, "height": 100, "confidence": 0.99, "bubble_id": 1,
    }

    new_lines, new_regions, _elapsed, recovered = ocr._recover_adjacent_fragmented_bubble_pair(
        np.zeros((300, 400, 3), dtype=np.uint8), [], [region], "ltr"
    )

    assert recovered == 0
    assert new_lines == []
    assert new_regions == [region]

def test_external_accented_capital_artifact_can_recover_exact_deaccented_text(monkeypatch):
    monkeypatch.setattr(local_rapidocr_module, "_SPELLCHECKER", {"a", "callican", "lv"})
    engine = SimpleNamespace(recognize_txt=lambda crops: SimpleNamespace(
        txts=("A CALLICAN (LV. 27).]",), scores=(0.94444,)
    ))
    monkeypatch.setattr(LocalRapidOCR, "_get_engine", lambda self: engine)
    lines = [{
        "text": "A ČALLICAN (LV. 27).]", "left": 20.0, "top": 20.0,
        "width": 300.0, "height": 38.0, "confidence": 0.986,
        "angle": 0.0, "region_type": "external_text",
    }]

    result = LocalRapidOCR()._rescue_external_line_recognition(
        np.zeros((100, 360, 3), dtype=np.uint8), lines
    )

    assert result[0]["text"] == "A CALLICAN (LV. 27).]"
    assert result[0]["ocr_rescued"] is True


def test_external_accented_capital_artifact_rejects_nonmatching_retry(monkeypatch):
    monkeypatch.setattr(local_rapidocr_module, "_SPELLCHECKER", {"a", "callican", "lv"})
    engine = SimpleNamespace(recognize_txt=lambda crops: SimpleNamespace(
        txts=("A CALLIGAN (LV. 27).]",), scores=(0.999,)
    ))
    monkeypatch.setattr(LocalRapidOCR, "_get_engine", lambda self: engine)
    lines = [{
        "text": "A ČALLICAN (LV. 27).]", "left": 20.0, "top": 20.0,
        "width": 300.0, "height": 38.0, "confidence": 0.986,
        "angle": 0.0, "region_type": "external_text",
    }]

    result = LocalRapidOCR()._rescue_external_line_recognition(
        np.zeros((100, 360, 3), dtype=np.uint8), lines
    )

    assert result == lines

def test_short_accented_external_name_can_recover_when_only_accent_differs(monkeypatch):
    monkeypatch.setattr(local_rapidocr_module, "_SPELLCHECKER", {"callican"})
    engine = SimpleNamespace(recognize_txt=lambda crops: SimpleNamespace(
        txts=("CALLICAN",), scores=(0.94444,)
    ))
    monkeypatch.setattr(LocalRapidOCR, "_get_engine", lambda self: engine)
    lines = [{
        "text": "ČALLICAN", "left": 20.0, "top": 20.0,
        "width": 150.0, "height": 38.0, "confidence": 0.986,
        "angle": 0.0, "region_type": "external_text",
    }]

    result = LocalRapidOCR()._rescue_external_line_recognition(
        np.zeros((100, 200, 3), dtype=np.uint8), lines
    )

    assert result[0]["text"] == "CALLICAN"
    assert result[0]["ocr_rescued"] is True



def test_standalone_kraah_monster_effect_is_excluded_without_dropping_dialogue():
    assert LocalRapidOCR.is_likely_nonverbal_effect("KRAAH!")
    assert not LocalRapidOCR.is_likely_nonverbal_effect("KRAAH! HELP!")
    assert not LocalRapidOCR.is_likely_nonverbal_effect("KRAAHING")


def test_suspicious_bubble_line_boxes_rescue_high_confidence_dictionary_improvement(monkeypatch):
    words = {
        "looks", "like", "this", "is", "the", "end", "of", "line", "for",
        "you", "sword", "princess", "next", "match",
    }
    monkeypatch.setattr(local_rapidocr_module, "_SPELLCHECKER", words)
    recognized = [
        "LOOKS LIKE", "THIS IS THE END", "OF THE LINE FOR YOU,",
        "SWORD PRINCESS.", "NEXT MATCH!",
    ]

    class FakeRecognizer:
        def recognize_txt(self, crops):
            assert len(crops) == len(recognized)
            assert all(crop.shape[0] > 50 for crop in crops)
            return SimpleNamespace(txts=recognized, scores=[0.999] * len(crops))

    reader = LocalRapidOCR()
    monkeypatch.setattr(reader, "_get_engine", lambda: FakeRecognizer())
    image = np.full((500, 900, 3), 255, dtype=np.uint8)
    region = {
        "region_type": "speech_bubble",
        "text": "LOOKS LIKE THHS NOD OF THE LINE FOR YOU, SWORD PRINCESS. NEXT MATCH!",
        "confidence": 0.9475,
        "ocr_review_required": True,
        "ocr_review_reasons": ["uncommon_two_letter_fragment_in_prose"],
        "ocr_boxes": [
            [332, 119, 238, 44], [281, 161, 340, 44], [224, 199, 451, 49],
            [267, 242, 368, 43], [315, 283, 272, 44],
        ],
    }

    rescued = reader._rescue_suspicious_bubble_region_from_line_boxes(image, region)

    assert rescued
    assert region["text"] == (
        "LOOKS LIKE THIS IS THE END OF THE LINE FOR YOU, "
        "SWORD PRINCESS. NEXT MATCH!"
    )
    assert region["ocr_recovery_method"] == "suspicious_bubble_line_boxes"
    assert region["confidence"] == 0.999
    assert "ocr_review_required" not in region


def test_suspicious_bubble_retry_recovers_nonpronoun_single_letter_with_equal_coverage(monkeypatch):
    words = {
        "i", "just", "need", "to", "collect", "thunder", "seeds", "the", "more",
        "get", "stronger", "second", "class", "change", "will", "be",
    }
    monkeypatch.setattr(local_rapidocr_module, "_SPELLCHECKER", words)
    recognized = [
        "I JUST NEED TO", "COLLECT THUNDER", "SEEDS, THE MORE I",
        "GET, THE STRONGER", "THE SECOND CLASS", "CHANGE WILL BE!",
    ]

    class FakeRecognizer:
        def recognize_txt(self, crops):
            assert len(crops) == len(recognized)
            return SimpleNamespace(txts=recognized, scores=[0.999] * len(crops))

    reader = LocalRapidOCR()
    monkeypatch.setattr(reader, "_get_engine", lambda: FakeRecognizer())
    image = np.full((10000, 700, 3), 255, dtype=np.uint8)
    region = {
        "region_type": "speech_bubble",
        "text": "D TO COLLECT THUNDER SEEDS, THE MORE I GET, THE STRONGER THE SECOND CLASS CHANGE WILL BE!",
        "confidence": 0.9195,
        "ocr_review_required": True,
        "ocr_review_reasons": ["isolated_single_letter_in_prose"],
        "ocr_suspicious_single_letter_tokens": ["D"],
        "ocr_boxes": [
            [392, 4555, 215, 26], [377, 4582, 246, 26], [370, 4609, 262, 26],
            [366, 4635, 268, 25], [370, 4661, 260, 26], [382, 4687, 236, 26],
        ],
    }

    rescued = reader._rescue_suspicious_bubble_region_from_line_boxes(image, region)

    assert rescued
    assert region["text"] == (
        "I JUST NEED TO COLLECT THUNDER SEEDS, THE MORE I GET, "
        "THE STRONGER THE SECOND CLASS CHANGE WILL BE!"
    )
    assert region["ocr_recovery_method"] == "suspicious_bubble_line_boxes"
    assert region["confidence"] == 0.999
    assert "ocr_review_required" not in region


def test_suspicious_bubble_retry_keeps_single_letter_when_candidate_does_not_fix_it(monkeypatch):
    words = {
        "d", "i", "just", "need", "to", "collect", "thunder", "seeds", "the", "more",
        "get", "stronger", "second", "class", "change", "will", "be",
    }
    monkeypatch.setattr(local_rapidocr_module, "_SPELLCHECKER", words)
    recognized = [
        "D JUST NEED TO", "COLLECT THUNDER", "SEEDS, THE MORE I",
        "GET, THE STRONGER", "THE SECOND CLASS", "CHANGE WILL BE!",
    ]

    class FakeRecognizer:
        def recognize_txt(self, crops):
            return SimpleNamespace(txts=recognized, scores=[0.999] * len(crops))

    reader = LocalRapidOCR()
    monkeypatch.setattr(reader, "_get_engine", lambda: FakeRecognizer())
    image = np.full((10000, 700, 3), 255, dtype=np.uint8)
    region = {
        "region_type": "speech_bubble",
        "text": "D TO COLLECT THUNDER SEEDS, THE MORE I GET, THE STRONGER THE SECOND CLASS CHANGE WILL BE!",
        "confidence": 0.9195,
        "ocr_review_required": True,
        "ocr_review_reasons": ["isolated_single_letter_in_prose"],
        "ocr_suspicious_single_letter_tokens": ["D"],
        "ocr_boxes": [
            [392, 4555, 215, 26], [377, 4582, 246, 26], [370, 4609, 262, 26],
            [366, 4635, 268, 25], [370, 4661, 260, 26], [382, 4687, 236, 26],
        ],
    }
    original = region["text"]

    rescued = reader._rescue_suspicious_bubble_region_from_line_boxes(image, region)

    assert not rescued
    assert region["text"] == original
    assert "ocr_recovery_method" not in region


def test_single_unknown_leading_word_in_short_bubble_is_recovered(monkeypatch):
    monkeypatch.setattr(local_rapidocr_module, "_SPELLCHECKER", {"how", "refreshing"})
    recognized = ["HOW", "REFRESHING"]

    class FakeRecognizer:
        def recognize_txt(self, crops):
            assert len(crops) == 2
            return SimpleNamespace(txts=recognized, scores=[0.9999, 0.999])

    reader = LocalRapidOCR()
    monkeypatch.setattr(reader, "_get_engine", lambda: FakeRecognizer())
    image = np.full((12300, 700, 3), 255, dtype=np.uint8)
    region = {
        "region_type": "speech_bubble",
        "text": "MOH REFRESHING",
        "confidence": 1.0,
        "ocr_boxes": [[466, 12002, 65, 28], [414, 12027, 165, 27]],
    }
    reader._add_speech_bubble_ocr_review_hint(region)
    assert "unrecognized_short_leading_word_in_bubble" in region["ocr_review_reasons"]

    rescued = reader._rescue_suspicious_bubble_region_from_line_boxes(image, region)

    assert rescued
    assert region["text"] == "HOW REFRESHING"
    assert region["ocr_recovery_method"] == "suspicious_bubble_line_boxes"
    assert region["ocr_review_required"] is not True


def test_single_unknown_leading_word_retry_rejects_weak_or_dissimilar_candidate(monkeypatch):
    monkeypatch.setattr(local_rapidocr_module, "_SPELLCHECKER", {"how", "hot", "refreshing"})
    reader = LocalRapidOCR()
    image = np.full((12300, 700, 3), 255, dtype=np.uint8)
    region = {
        "region_type": "speech_bubble",
        "text": "MOH REFRESHING",
        "confidence": 1.0,
        "ocr_boxes": [[466, 12002, 65, 28], [414, 12027, 165, 27]],
    }
    reader._add_speech_bubble_ocr_review_hint(region)
    original = region["text"]

    class WeakRecognizer:
        def recognize_txt(self, crops):
            return SimpleNamespace(txts=["HOW", "REFRESHING"], scores=[0.989, 0.999])

    monkeypatch.setattr(reader, "_get_engine", lambda: WeakRecognizer())
    assert not reader._rescue_suspicious_bubble_region_from_line_boxes(image, region)
    assert region["text"] == original

    class DissimilarRecognizer:
        def recognize_txt(self, crops):
            return SimpleNamespace(txts=["HOT", "REFRESHING"], scores=[0.9999, 0.999])

    monkeypatch.setattr(reader, "_get_engine", lambda: DissimilarRecognizer())
    assert not reader._rescue_suspicious_bubble_region_from_line_boxes(image, region)
    assert region["text"] == original
    assert "ocr_recovery_method" not in region


def test_suspicious_bubble_line_boxes_reject_low_confidence_candidate(monkeypatch):
    words = {
        "looks", "like", "this", "is", "the", "end", "of", "line", "for",
        "you", "sword", "princess", "next", "match",
    }
    monkeypatch.setattr(local_rapidocr_module, "_SPELLCHECKER", words)

    class FakeRecognizer:
        def recognize_txt(self, crops):
            return SimpleNamespace(
                txts=["LOOKS LIKE", "THIS IS THE END", "OF THE LINE FOR YOU,",
                      "SWORD PRINCESS.", "NEXT MATCH!"],
                scores=[0.94] * len(crops),
            )

    reader = LocalRapidOCR()
    monkeypatch.setattr(reader, "_get_engine", lambda: FakeRecognizer())
    image = np.full((500, 900, 3), 255, dtype=np.uint8)
    region = {
        "region_type": "speech_bubble",
        "text": "LOOKS LIKE THHS NOD OF THE LINE FOR YOU, SWORD PRINCESS. NEXT MATCH!",
        "confidence": 0.9475,
        "ocr_review_required": True,
        "ocr_review_reasons": ["uncommon_two_letter_fragment_in_prose"],
        "ocr_boxes": [
            [332, 119, 238, 44], [281, 161, 340, 44], [224, 199, 451, 49],
            [267, 242, 368, 43], [315, 283, 272, 44],
        ],
    }
    original = region["text"]

    rescued = reader._rescue_suspicious_bubble_region_from_line_boxes(image, region)

    assert not rescued
    assert region["text"] == original
    assert "ocr_recovery_method" not in region



def test_suspicious_bubble_retry_joins_hyphenated_wrapped_word(monkeypatch):
    words = {"let", "s", "finish", "this", "with", "wolf", "gang", "gun"}
    monkeypatch.setattr(local_rapidocr_module, "_SPELLCHECKER", words)
    recognized = ["LET'S FINISH", "THIS WITH WOLF-", "GANG'S GUN."]

    class FakeRecognizer:
        def recognize_txt(self, crops):
            assert len(crops) == 3
            return SimpleNamespace(txts=recognized, scores=[0.999] * 3)

    reader = LocalRapidOCR()
    monkeypatch.setattr(reader, "_get_engine", lambda: FakeRecognizer())
    image = np.full((400, 900, 3), 255, dtype=np.uint8)
    region = {
        "region_type": "speech_bubble",
        "text": "LET'S FINISH TH H SLF. GANG'S GUN.",
        "confidence": 0.94,
        "ocr_review_required": True,
        "ocr_review_reasons": ["multiple_unrecognized_english_tokens"],
        "ocr_boxes": [[284, 120, 335, 58], [227, 180, 449, 57], [282, 240, 335, 59]],
    }

    rescued = reader._rescue_suspicious_bubble_region_from_line_boxes(image, region)

    assert rescued
    assert region["text"] == "LET'S FINISH THIS WITH WOLF-GANG'S GUN."
