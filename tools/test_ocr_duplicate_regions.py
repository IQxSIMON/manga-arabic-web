from ocr_engine.local_rapidocr import LocalRapidOCR


def row(text, left=10, top=10, width=100, height=40, kind="speech_bubble", confidence=0.8):
    return {
        "text": text,
        "left": left,
        "top": top,
        "width": width,
        "height": height,
        "confidence": confidence,
        "region_type": kind,
    }


def test_deduplicates_exact_overlapping_regions():
    first = row("WHAT'S ON THE MENU TODAY?")
    duplicate = row("WHAT'S ON THE MENU TODAY?", left=12, top=11, width=98, height=39)

    result = LocalRapidOCR._deduplicate_overlapping_regions([first, duplicate])

    assert result == [first]


def test_keeps_the_complete_reading_when_a_bubble_crop_returns_a_fragment():
    complete = row("YOU TOUCHED THE REALM OF A CLASS MASTER.", confidence=0.78)
    fragment = row("YOU TOUCHED THE REALM OF A", left=11, top=11, width=96, height=38,
                   confidence=0.91)

    result = LocalRapidOCR._deduplicate_overlapping_regions([fragment, complete])

    assert result == [complete]


def test_deduplicates_partially_overlapping_crop_with_one_misread_letter():
    complete = row("WHAT'LL I DO IF NOBODY KNOWS...", left=297, top=3782,
                    width=370, height=274, confidence=0.84)
    crop = row("VHAT'LL I DO IF NOBODY", left=151, top=3812,
               width=374, height=116, confidence=0.92)

    result = LocalRapidOCR._deduplicate_overlapping_regions([crop, complete])

    assert result == [complete]


def test_keeps_same_text_in_neighboring_nonoverlapping_bubbles():
    first = row("YES.", left=10, top=10, width=42, height=26)
    second = row("YES.", left=10, top=42, width=42, height=26)

    result = LocalRapidOCR._deduplicate_overlapping_regions([first, second])

    assert result == [first, second]


def test_does_not_merge_speech_and_external_text_categories():
    speech = row("STEEL HEART", kind="speech_bubble")
    caption = row("STEEL HEART", kind="external_text")

    result = LocalRapidOCR._deduplicate_overlapping_regions([speech, caption])

    assert result == [speech, caption]
