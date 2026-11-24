import numpy as np

from ocr_engine.local_rapidocr import (
    _bubble_mask_overlap_fraction,
    _bubble_region_overlap_fraction,
    _relaxed_bubble_candidates,
)
from ocr_engine.speech_bubbles import SpeechBubble


def bubble(bbox, confidence=0.4):
    left, top, right, bottom = bbox
    mask = np.full((bottom-top, right-left), 255, dtype=np.uint8)
    return SpeechBubble(bbox, mask, confidence)


def test_relaxed_candidate_does_not_replace_overlapping_primary_mask():
    primary = bubble((0, 0, 100, 100), 0.8)
    relaxed_duplicate = bubble((10, 10, 120, 120), 0.23)

    assert _bubble_mask_overlap_fraction(relaxed_duplicate, primary) > 0.5
    assert _relaxed_bubble_candidates([primary], [relaxed_duplicate]) == []


def test_relaxed_candidate_is_kept_when_it_adds_a_separate_bubble():
    primary = bubble((0, 0, 100, 100), 0.8)
    relaxed_new = bubble((200, 0, 300, 100), 0.23)

    assert _relaxed_bubble_candidates([primary], [relaxed_new]) == [relaxed_new]


def test_region_overlap_uses_ocr_polygon_not_just_nearby_bbox():
    candidate = bubble((0, 0, 100, 100))
    inside = {
        "left": 20, "top": 20, "width": 20, "height": 10,
        "ocr_polygons": [[[20, 20], [40, 20], [40, 30], [20, 30]]],
    }
    outside = {
        "left": 110, "top": 20, "width": 20, "height": 10,
        "ocr_polygons": [[[110, 20], [130, 20], [130, 30], [110, 30]]],
    }

    assert _bubble_region_overlap_fraction(inside, candidate) == 1.0
    assert _bubble_region_overlap_fraction(outside, candidate) == 0.0
