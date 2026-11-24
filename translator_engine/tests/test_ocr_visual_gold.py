import pytest

from tools.evaluate_ocr_visual_gold import score


def test_visual_gold_aligns_regions_by_geometry_when_region_indexes_shift():
    gold = {"cases": [{
        "page": "page_14", "region_index": 1,
        "reference": "IS THAT IT YOUNG MONARCH", "class": "bubble",
    }]}
    baseline = {"results": [{
        "page": "page_14",
        "regions": [
            {"text": "UNRELATED", "left": 400, "top": 100, "width": 50, "height": 30},
            {"text": "IS THAT !T YOUNG MONARCH VE ONLY ETED", "left": 10, "top": 10, "width": 120, "height": 60},
        ],
    }]}
    current = {"results": [{
        "page": "page_14",
        "regions": [{
            "text": "IS THAT IT YOUNG MONARCH", "left": 10, "top": 10,
            "width": 60, "height": 25,
        }],
    }]}

    result = score(gold, baseline, current)

    assert result["rows"][0]["baseline_region_index"] == 2
    assert result["baseline_word_errors"] > 0
    assert result["current_word_errors"] == 0


def test_visual_gold_reports_exact_results_for_curated_regions():
    gold = {"cases": [{
        "page": "page_02", "region_index": 1,
        "reference": "YOU SEEM EXHAUSTED YOUNG MONARCH", "class": "bubble",
    }]}
    report = {"results": [{"page": "page_02", "regions": [{
        "text": "YOU SEEM EXHAUSTED, YOUNG MONARCH.",
        "left": 0, "top": 0, "width": 100, "height": 50,
    }]}]}

    result = score(gold, report, report)

    assert result["case_count"] == 1
    assert result["baseline_wer"] == 0
    assert result["current_wer"] == 0


def test_visual_gold_rejects_unmatched_baseline_region_geometry():
    gold = {"cases": [{
        "page": "page_02", "region_index": 1,
        "reference": "HELLO", "class": "bubble",
    }]}
    baseline = {"results": [{"page": "page_02", "regions": [{
        "text": "HELLO", "left": 400, "top": 400, "width": 20, "height": 20,
    }]}]}
    current = {"results": [{"page": "page_02", "regions": [{
        "text": "HELLO", "left": 0, "top": 0, "width": 20, "height": 20,
    }]}]}

    with pytest.raises(ValueError, match="spatially corresponding"):
        score(gold, baseline, current)


def test_visual_gold_aligns_current_regions_by_geometry_after_region_merge():
    gold = {"cases": [{
        "page": "page_10", "region_index": 1,
        "reference": "HELLO THERE", "class": "bubble",
    }]}
    anchor = {"results": [{"page": "page_10", "regions": [{
        "text": "HELLO THERE", "left": 100, "top": 100, "width": 100, "height": 50,
    }]}]}
    baseline = anchor
    current = {"results": [{"page": "page_10", "regions": [
        {"text": "UNRELATED", "left": 500, "top": 500, "width": 40, "height": 20},
        {"text": "HELLO THERE", "left": 100, "top": 100, "width": 100, "height": 50},
    ]}]}

    result = score(gold, baseline, current, anchor)

    assert result["rows"][0]["current_region_index"] == 2
    assert result["current_wer"] == 0


def test_visual_gold_applies_saved_crop_override_only_to_annotated_region():
    gold = {"cases": [
        {"page": "page_05", "region_index": 1, "reference": "HELLO AGAIN", "class": "bubble"},
        {"page": "page_05", "region_index": 2, "reference": "OTHER TEXT", "class": "bubble"},
    ]}
    report = {"results": [{"page": "page_05", "regions": [
        {"text": "HELLO", "left": 0, "top": 0, "width": 60, "height": 20, "bubble_id": 4},
        {"text": "OTHER TEXT", "left": 0, "top": 40, "width": 80, "height": 20, "bubble_id": 4},
    ]}]}
    recovery = {"overrides": [{
        "page": "page_05", "region_index": 1, "text": "HELLO AGAIN",
        "method": "expanded_bubble_edge_crop",
    }]}

    result = score(gold, report, report, recovery=recovery)

    assert result["rows"][0]["current"] == "HELLO AGAIN"
    assert result["rows"][0]["current_source"] == "expanded_bubble_edge_crop"
    assert result["rows"][1]["current"] == "OTHER TEXT"
    assert result["rows"][1]["current_source"] == "saved_full_page_ocr"
    assert result["crop_recovery_overrides"] == 1
