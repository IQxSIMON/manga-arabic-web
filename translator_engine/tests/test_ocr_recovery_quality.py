from tools.evaluate_ocr_recovery_quality import evaluate


def test_ocr_recovery_metric_detects_word_and_character_error_reduction():
    gold = {"cases": [{
        "id": "case", "page": "page_05",
        "reference": "LET'S SEE THE QUEST CONDITIONS",
        "baseline_fragment": "LET'S E THE QUEST NDITIONS",
    }]}
    baseline = {"results": [{
        "page": "page_05",
        "regions": [{"text": "LET'S E THE QUEST NDITIONS"}],
    }]}
    current = {"results": [{
        "page": "page_05",
        "regions": [{"text": "LET'S SEE THE QUEST CONDITIONS", "ocr_recovered": True}],
    }]}

    result = evaluate(gold, baseline, current)["results"][0]

    assert result["baseline_wer"] > 0
    assert result["baseline_cer"] > 0
    assert result["current_wer"] == 0
    assert result["current_cer"] == 0


def test_ocr_recovery_metric_ignores_punctuation_for_word_error_rate():
    gold = {"cases": [{
        "id": "case", "page": "page_14",
        "reference": "YOU'VE ONLY COMPLETED THE PUSH-UPS.",
        "baseline_fragment": "VE ONLY ETED THE SH-UPS.",
    }]}
    baseline = {"results": [{
        "page": "page_14",
        "regions": [{"text": "VE ONLY ETED THE SH-UPS."}],
    }]}
    current = {"results": [{
        "page": "page_14",
        "regions": [{"text": "YOU'VE ONLY COMPLETED THE PUSH-UPS", "ocr_recovered": True}],
    }]}

    result = evaluate(gold, baseline, current)["results"][0]

    assert result["current_wer"] == 0
    assert result["current_cer"] == 0
