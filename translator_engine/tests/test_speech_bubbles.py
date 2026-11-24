import numpy as np
import cv2
from types import SimpleNamespace

from ocr_engine import LocalRapidOCR
from ocr_engine import local_rapidocr as local_rapidocr_module
from ocr_engine import speech_bubbles
renderer = speech_bubbles
from ocr_engine.speech_bubbles import SpeechBubble


def _proposal(box, confidence):
    left, top, right, bottom = box
    return SpeechBubble(box, np.full((bottom - top, right - left), 255, dtype=np.uint8), confidence)


def test_tall_strip_retries_low_confidence_internal_seam_with_taller_context(monkeypatch):
    page = np.zeros((5000, 800, 3), dtype=np.uint8)
    page[1440, 0, 0] = 1  # identify the strip whose bottom cuts through the balloon
    calls = []

    def fake_segment(crop, _threshold):
        calls.append(crop.shape[:2])
        if crop.shape[0] == 1800:
            return [_proposal((90, 850, 410, 1280), 0.35)]
        if crop[0, 0, 0] == 1:
            return [_proposal((100, 1400, 400, 1590), 0.41)]
        return []

    monkeypatch.setattr(speech_bubbles, "_detect_speech_bubbles_single", fake_segment)

    detected = speech_bubbles.detect_speech_bubbles(page)

    assert (90, 2885, 410, 3315) in [bubble.bbox for bubble in detected]
    assert (1600, 800) in calls  # standard strip inference
    assert (1800, 800) in calls  # one targeted seam-context retry


def test_dual_confidence_levels_share_single_segmentation_inference(monkeypatch):
    class FakeSession:
        def __init__(self):
            self.calls = 0

        def get_inputs(self):
            return [SimpleNamespace(name="images")]

        def run(self, _outputs, _inputs):
            self.calls += 1
            candidates = np.asarray([
                [160, 140, 120, 80, 0.30, 1.0],
                [440, 420, 120, 80, 0.22, 1.0],
            ], dtype=np.float32)
            raw = candidates.T[None, ...]
            prototypes = np.ones((1, 1, 160, 160), dtype=np.float32)
            return raw, prototypes

    session = FakeSession()
    monkeypatch.setattr(speech_bubbles, "_session", lambda: session)
    monkeypatch.setattr(speech_bubbles, "_detect_dark_oval_bubbles", lambda _image: [])
    image = np.full((640, 640, 3), 127, dtype=np.uint8)

    primary, relaxed = speech_bubbles._detect_speech_bubbles_single(
        image, 0.25, secondary_confidence_threshold=0.20
    )

    assert session.calls == 1
    assert np.allclose([bubble.confidence for bubble in primary], [0.30])
    assert np.allclose([bubble.confidence for bubble in relaxed], [0.30, 0.22])


def test_tall_page_requests_both_thresholds_once_per_strip(monkeypatch):
    page = np.zeros((5000, 800, 3), dtype=np.uint8)
    calls = []

    def fake_segment(crop, primary_threshold, secondary_threshold=None):
        calls.append((crop.shape[:2], primary_threshold, secondary_threshold))
        primary = [_proposal((100, 200, 300, 400), 0.90)]
        secondary = [*primary, _proposal((400, 300, 600, 500), 0.21)]
        return primary, secondary

    monkeypatch.setattr(speech_bubbles, "_detect_speech_bubbles_single", fake_segment)
    monkeypatch.setattr(speech_bubbles, "_detect_dark_oval_bubbles", lambda _image: [])

    primary, secondary = speech_bubbles.detect_speech_bubbles(
        page, 0.25, secondary_confidence_threshold=0.20
    )

    assert len(calls) == 4
    assert all(primary_level == 0.25 and secondary_level == 0.20
               for _shape, primary_level, secondary_level in calls)
    assert len(primary) == 4
    assert len(secondary) == 8


def test_seam_retry_is_skipped_for_confident_masks(monkeypatch):
    page = np.zeros((5000, 800, 3), dtype=np.uint8)
    page[1440, 0, 0] = 1
    calls = []

    def fake_segment(crop, _threshold):
        calls.append(crop.shape[:2])
        if crop[0, 0, 0] == 1:
            return [_proposal((100, 1400, 400, 1590), 0.91)]
        return []

    monkeypatch.setattr(speech_bubbles, "_detect_speech_bubbles_single", fake_segment)

    speech_bubbles.detect_speech_bubbles(page)

    assert calls.count((1800, 800)) == 0


def test_dark_filled_oval_fallback_detects_bubble_but_rejects_caption_card():
    image = np.full((500, 900, 3), 255, dtype=np.uint8)
    cv2.ellipse(image, (250, 240), (205, 135), 0, 0, 360, (0, 0, 0), -1)
    cv2.putText(image, "HELLO", (170, 250), cv2.FONT_HERSHEY_SIMPLEX,
                1.2, (255, 255, 255), 3, cv2.LINE_AA)
    cv2.rectangle(image, (500, 120), (850, 360), (0, 0, 0), -1)
    cv2.putText(image, "CAPTION", (550, 250), cv2.FONT_HERSHEY_SIMPLEX,
                1.0, (255, 255, 255), 3, cv2.LINE_AA)

    detected = speech_bubbles._detect_dark_oval_bubbles(image)

    assert len(detected) == 1
    assert detected[0].bbox[0] < 50
    assert detected[0].bbox[2] < 500
    assert detected[0].safe_mask.shape == (
        detected[0].bbox[3] - detected[0].bbox[1],
        detected[0].bbox[2] - detected[0].bbox[0],
    )


def test_extract_reports_bubble_masks_without_dialogue(monkeypatch):
    first = _proposal((10, 10, 80, 80), 0.95)
    second = _proposal((10, 110, 80, 180), 0.88)
    monkeypatch.setattr(local_rapidocr_module, "detect_speech_bubbles", lambda _image: [first, second])
    engine = LocalRapidOCR()

    def fake_ocr(crop):
        if float(crop.mean()) < 20:
            return SimpleNamespace(
                boxes=[np.asarray([[20, 20], [120, 20], [120, 60], [20, 60]], dtype=np.float32)],
                txts=["HELLO THERE"], scores=[0.99],
            )
        return SimpleNamespace(boxes=[], txts=[], scores=[])

    engine._get_engine = lambda: fake_ocr
    engine._rescue_bubble_lines = lambda _crop, lines, _unmasked_crop=None: lines
    engine._rescue_sparse_bubble_lines = lambda _crop, lines, origin, scale: lines
    engine._recover_adjacent_bubble_continuations = lambda _image, _bubbles, lines, regions: (lines, regions, 0.0, 0)
    engine._recover_clipped_bubble_text = lambda _image, _bubbles, lines, regions, _direction: (
        lines, regions, 0.0, 0, 0, 0, 0.0, 0,
    )
    image = np.full((220, 100, 3), 255, dtype=np.uint8)
    image[10:80, 10:80] = 0
    success, encoded = cv2.imencode(".png", image)
    assert success

    result = engine.extract(encoded.tobytes(), include_external=False)

    assert result["speech_bubble_mask_count"] == 2
    assert result["speech_bubble_masks_with_primary_ocr"] == 1
    assert result["speech_bubble_masks_without_primary_ocr"] == 1

def test_candidate_score_crop_matches_full_resize_reference():
    rng = np.random.default_rng(13)
    scores = rng.random((160, 160), dtype=np.float32)
    bounds = (280, 410, 650, 880)
    full = cv2.resize(scores, (1600, 1600), interpolation=cv2.INTER_LINEAR)
    reference = cv2.resize(full[bounds[1]:bounds[3], bounds[0]:bounds[2]],
                           (231, 294), interpolation=cv2.INTER_LINEAR)
    optimized = renderer._resize_candidate_score_crop(scores, bounds, (231, 294))
    assert np.mean(np.abs(reference - optimized)) < 0.004
    assert np.mean((reference > 0.5) == (optimized > 0.5)) > 0.99


def test_rotated_rectangular_label_is_rejected_but_rounded_bubble_survives():
    panel = np.zeros((140, 200), dtype=np.uint8)
    points = cv2.boxPoints(((100, 70), (138, 54), 31)).astype(np.int32)
    cv2.fillConvexPoly(panel, points, 255)
    assert renderer._is_rotated_rectangular_caption(panel, (0, 0, 200, 140), 0.84)

    bubble = np.zeros((140, 200), dtype=np.uint8)
    cv2.ellipse(bubble, (100, 70), (74, 32), 27, 0, 360, 255, -1)
    assert not renderer._is_rotated_rectangular_caption(bubble, (0, 0, 200, 140), 0.84)


def test_rotated_caption_filter_handles_low_axis_aligned_fill():
    label = np.zeros((200, 200), dtype=np.uint8)
    points = cv2.boxPoints(((100, 100), (190, 90), 28)).astype(np.int32)
    cv2.fillConvexPoly(label, points, 255)
    axis_aligned_fill = np.count_nonzero(label) / label.size
    assert axis_aligned_fill < 0.74
    assert renderer._is_rotated_rectangular_caption(
        label, (0, 0, 200, 200), axis_aligned_fill
    )


def test_rotated_caption_filter_rejects_reviewed_sold_out_candidate():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2] / "ocr_test_results" / "bubble_segmentation_optimization_20261116"
    archive = root / "teto_ch09_p01_masks_after.npz"
    if not archive.is_file():
        return
    with np.load(archive) as masks:
        candidate = masks["m4"].copy()
    fill = np.count_nonzero(candidate) / candidate.size
    assert fill < 0.74
    assert renderer._is_rotated_rectangular_caption(
        candidate, (0, 0, candidate.shape[1], candidate.shape[0]), fill
    )
