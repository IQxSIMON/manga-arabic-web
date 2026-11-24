import app as web_app


def test_ocr_timing_breakdown_exposes_detector_and_residual_costs():
    timing = web_app._ocr_timing_breakdown({
        "elapsed_seconds": 31.0,
        "bubble_detection_and_ocr_seconds": 12.0,
        "speech_bubble_mask_count": 9,
        "speech_bubble_masks_with_primary_ocr": 8,
        "speech_bubble_masks_without_primary_ocr": 1,
        "external_ocr_seconds": 8.0,
        "adjacent_bubble_recovery_seconds": 1.0,
        "adjacent_bubble_recovery_count": 2,
        "edge_crop_recovery_seconds": 2.0,
        "edge_crop_recovery_count": 3,
        "comic_detector_seconds": 7.0,
        "external_region_count": 3,
        "comic_detector_region_count": 6,
        "comic_detector_ocr_line_count": 4,
    })

    assert timing == {
        "bubble_detection_and_ocr_seconds": 12.0,
        "speech_bubble_mask_count": 9,
        "speech_bubble_masks_with_primary_ocr": 8,
        "speech_bubble_masks_without_primary_ocr": 1,
        "external_ocr_seconds": 8.0,
        "adjacent_bubble_recovery_seconds": 1.0,
        "adjacent_bubble_recovery_count": 2,
        "edge_crop_recovery_seconds": 2.0,
        "edge_crop_recovery_count": 3,
        "comic_detector_seconds": 7.0,
        "other_ocr_processing_seconds": 1.0,
        "external_region_count": 3,
        "comic_detector_region_count": 6,
        "comic_detector_ocr_line_count": 4,
    }


def test_ocr_timing_breakdown_does_not_guess_when_stage_data_is_missing():
    timing = web_app._ocr_timing_breakdown({
        "elapsed_seconds": 9.0,
        "bubble_detection_and_ocr_seconds": 5.0,
        "external_ocr_seconds": 2.0,
    })

    assert timing["comic_detector_seconds"] is None
    assert timing["other_ocr_processing_seconds"] is None
    assert timing["speech_bubble_mask_count"] is None
