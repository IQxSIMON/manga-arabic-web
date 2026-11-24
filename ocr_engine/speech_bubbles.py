"""CPU speech-balloon instance segmentation using an optional ONNX weight file."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from collections import OrderedDict
from hashlib import sha256
from pathlib import Path
from threading import RLock

import cv2
import numpy as np

from image_pipeline.onnx_runtime import create_inference_session, create_session_options


MODEL_PATH = Path(__file__).resolve().parents[1] / "translator_engine" / "models" / "speech_bubble" / "bubble.onnx"
MODEL_SIZE = 1600
_BUBBLE_CACHE_MAX_BYTES = 48 * 1024 * 1024
_BUBBLE_CACHE_MAX_ENTRIES = 24
_bubble_cache = OrderedDict()
_bubble_cache_bytes = 0
_bubble_cache_lock = RLock()


def _minimum_safe_mask_area(width: int, height: int) -> int:
    """Use page width for the small-balloon floor; page height varies wildly in manhwa."""
    return max(250, min(1800, width * width // 1200))


def _is_rectangular_caption(mask: np.ndarray, bounds: tuple[int, int, int, int], fill_ratio: float) -> bool:
    """Identify straight-edged webtoon narration panels, not speech balloons.

    Manhwa often uses large rectangular caption cards. Their clean, axis-aligned
    borders are measurably different from oval/cloud speech balloons. This is a
    conservative geometry filter: it only rejects a dense mask when most of
    its contour follows horizontal/vertical edges.
    """
    if fill_ratio < 0.76:
        return False
    left, top, right, bottom = bounds
    region = mask[top:bottom, left:right]
    contours, _ = cv2.findContours(region, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return False
    contour = max(contours, key=cv2.contourArea)
    perimeter = cv2.arcLength(contour, True)
    if perimeter <= 0:
        return False
    polygon = cv2.approxPolyDP(contour, 0.018 * perimeter, True).reshape(-1, 2)
    if len(polygon) < 4 or len(polygon) > 10:
        return False
    aligned_length = 0.0
    for start, end in zip(polygon, np.roll(polygon, -1, axis=0)):
        dx, dy = float(end[0] - start[0]), float(end[1] - start[1])
        length = float(np.hypot(dx, dy))
        angle = abs(np.degrees(np.arctan2(dy, dx))) % 90.0
        deviation = min(angle, 90.0 - angle)
        if deviation <= 12.0:
            aligned_length += length
    return aligned_length / perimeter >= 0.68


def _is_rotated_rectangular_caption(mask: np.ndarray, bounds: tuple[int, int, int, int],
                                      fill_ratio: float) -> bool:
    """Reject small filled quadrilateral labels at any rotation, not oval balloons."""
    # A diagonal quadrilateral occupies much less of its axis-aligned bbox than a
    # horizontal card. Keep this modest floor so tilted sale labels are still tested.
    if fill_ratio < 0.28:
        return False
    left, top, right, bottom = bounds
    region = mask[top:bottom, left:right]
    contours, _ = cv2.findContours(region, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return False
    contour = max(contours, key=cv2.contourArea)
    perimeter = cv2.arcLength(contour, True)
    if perimeter <= 0:
        return False
    polygon = cv2.approxPolyDP(contour, 0.018 * perimeter, True).reshape(-1, 2)
    if len(polygon) != 4 or not cv2.isContourConvex(polygon.reshape(-1, 1, 2)):
        return False
    edges = np.roll(polygon, -1, axis=0) - polygon
    lengths = np.linalg.norm(edges.astype(np.float32), axis=1)
    if np.min(lengths) < 4:
        return False
    unit = edges.astype(np.float32) / lengths[:, None]
    adjacent_dots = np.abs(np.sum(unit * np.roll(unit, -1, axis=0), axis=1))
    opposite_dots = np.abs(np.sum(unit * np.roll(unit, -2, axis=0), axis=1))
    opposite_lengths = np.minimum(lengths, np.roll(lengths, -2)) / np.maximum(lengths, np.roll(lengths, -2))
    major_edge = edges[int(np.argmax(lengths))]
    angle = abs(np.degrees(np.arctan2(float(major_edge[1]), float(major_edge[0])))) % 90.0
    rotation_from_axes = min(angle, 90.0 - angle)
    aspect_ratio = float(np.max(lengths) / max(1.0, np.min(lengths)))
    # Require a noticeably tilted, elongated quadrilateral. This excludes
    # axis-aligned rounded/box-like masks and small square UI icons.
    return bool(rotation_from_axes >= 12.0
                and aspect_ratio >= 1.35
                and np.max(adjacent_dots) <= 0.50
                and np.min(opposite_dots) >= 0.92
                and np.min(opposite_lengths) >= 0.62)


def _resize_candidate_score_crop(scores: np.ndarray, model_bounds: tuple[int, int, int, int],
                                 output_size: tuple[int, int]) -> np.ndarray:
    """Resize only a proposal's mask crop instead of allocating a full 1600² map."""
    left, top, right, bottom = model_bounds
    output_width, output_height = output_size
    model_height = model_width = MODEL_SIZE
    source_height, source_width = scores.shape
    model_x = np.arange(left, right, dtype=np.float32)
    model_y = np.arange(top, bottom, dtype=np.float32)
    map_x_line = (model_x + 0.5) * source_width / model_width - 0.5
    map_y_line = (model_y + 0.5) * source_height / model_height - 0.5
    map_x = np.broadcast_to(map_x_line[None, :], (len(model_y), len(model_x)))
    map_y = np.broadcast_to(map_y_line[:, None], (len(model_y), len(model_x)))
    model_crop = cv2.remap(scores, map_x, map_y, cv2.INTER_LINEAR,
                           borderMode=cv2.BORDER_REPLICATE)
    return cv2.resize(model_crop, (output_width, output_height), interpolation=cv2.INTER_LINEAR)


@dataclass(init=False)
class SpeechBubble:
    """Detected bubble bounds and a bbox-local mask (255 means inside)."""

    bbox: tuple[int, int, int, int]
    safe_mask: np.ndarray
    confidence: float

    def __init__(self, bbox, safe_mask_or_legacy_mask, confidence_or_legacy_safe_mask,
                 legacy_confidence=None):
        self.bbox = tuple(int(value) for value in bbox)
        if legacy_confidence is None:
            self.safe_mask = np.asarray(safe_mask_or_legacy_mask, dtype=np.uint8)
            self.confidence = float(confidence_or_legacy_safe_mask)
        else:
            # Older internal callers passed both a full-page mask and a
            # full-page safe mask. Accept that shape during migration, then
            # retain only the relevant crop.
            _legacy_mask = safe_mask_or_legacy_mask
            full_page_safe_mask = np.asarray(confidence_or_legacy_safe_mask, dtype=np.uint8)
            left, top, right, bottom = self.bbox
            self.safe_mask = full_page_safe_mask[top:bottom, left:right].copy()
            self.confidence = float(legacy_confidence)


def _mask_overlap(first: SpeechBubble, second: SpeechBubble) -> int:
    """Count mask overlap using only the intersecting bbox, not page-sized arrays."""
    ax1, ay1, ax2, ay2 = first.bbox
    bx1, by1, bx2, by2 = second.bbox
    left, top, right, bottom = max(ax1, bx1), max(ay1, by1), min(ax2, bx2), min(ay2, by2)
    if right <= left or bottom <= top:
        return 0
    first_crop = first.safe_mask[top - ay1:bottom - ay1, left - ax1:right - ax1]
    second_crop = second.safe_mask[top - by1:bottom - by1, left - bx1:right - bx1]
    return int(np.count_nonzero((first_crop > 0) & (second_crop > 0)))


def deduplicate_bubbles(candidates: list[SpeechBubble], overlap_threshold: float = 0.72) -> list[SpeechBubble]:
    """Remove masks contained by another proposal while retaining the union crop."""
    deduplicated: list[SpeechBubble] = []
    for bubble in sorted(candidates, key=lambda item: item.confidence, reverse=True):
        area = int(np.count_nonzero(bubble.safe_mask))
        duplicate_index = None
        replace_index = None
        for index, existing in enumerate(deduplicated):
            existing_area = int(np.count_nonzero(existing.safe_mask))
            overlap = _mask_overlap(bubble, existing)
            smaller = min(area, existing_area)
            if smaller and overlap / smaller >= overlap_threshold:
                duplicate_index = index
                if area > existing_area * 1.15:
                    replace_index = index
                break
        if replace_index is not None:
            deduplicated[replace_index] = bubble
        elif duplicate_index is None:
            deduplicated.append(bubble)
    return sorted(deduplicated, key=lambda bubble: (bubble.bbox[1], bubble.bbox[0]))


@lru_cache(maxsize=1)
def _session():
    try:
        import onnxruntime as ort
    except ImportError as exc:
        raise RuntimeError("اكتشاف الفقاعات يحتاج onnxruntime داخل .venv.") from exc
    if not MODEL_PATH.is_file():
        raise RuntimeError(
            "نموذج فقاعات الحوار غير موجود. نزّل bubble.onnx حسب دليل النموذج ثم أعد التشغيل."
        )
    options = create_session_options(ort, cpu_threads=4)
    return create_inference_session(
        ort, MODEL_PATH, options, allow_dml=True
    )


def active_bubble_provider() -> str:
    """Return the active provider for the cached bubble model session."""
    try:
        providers = _session().get_providers()
    except Exception:
        return "CPUExecutionProvider"
    return providers[0] if providers else "CPUExecutionProvider"


def _detect_speech_bubbles_single(
    image: np.ndarray,
    confidence_threshold: float,
    secondary_confidence_threshold: float | None = None,
) -> list[SpeechBubble] | tuple[list[SpeechBubble], list[SpeechBubble]]:
    """Run segmentation on one crop in BGR or RGB uint8 format."""
    if not isinstance(image, np.ndarray) or image.ndim != 3 or image.shape[2] != 3:
        raise ValueError("صورة اكتشاف الفقاعات يجب أن تكون مصفوفة ألوان ثلاثية القنوات.")
    height, width = image.shape[:2]
    cache_image = np.ascontiguousarray(image)
    thresholds = [float(confidence_threshold)]
    if (secondary_confidence_threshold is not None
            and float(secondary_confidence_threshold) != float(confidence_threshold)):
        thresholds.append(float(secondary_confidence_threshold))
    cache_digest = sha256(memoryview(cache_image).cast("B")).digest()
    cache_keys = {
        threshold: (cache_image.shape, str(cache_image.dtype), threshold, cache_digest)
        for threshold in thresholds
    }
    with _bubble_cache_lock:
        if all(key in _bubble_cache for key in cache_keys.values()):
            cached = []
            for threshold in thresholds:
                key = cache_keys[threshold]
                _bubble_cache.move_to_end(key)
                cached.append(_bubble_cache[key])
            return cached[0] if secondary_confidence_threshold is None else tuple(cached)
    scale = min(MODEL_SIZE / height, MODEL_SIZE / width)
    scaled_width, scaled_height = round(width * scale), round(height * scale)
    resized = cv2.resize(image, (scaled_width, scaled_height), interpolation=cv2.INTER_LINEAR)
    pad_x, pad_y = (MODEL_SIZE - scaled_width) / 2, (MODEL_SIZE - scaled_height) / 2
    left, top = round(pad_x - 0.1), round(pad_y - 0.1)
    right, bottom = round(pad_x + 0.1), round(pad_y + 0.1)
    canvas = np.full((MODEL_SIZE, MODEL_SIZE, 3), 114, dtype=np.uint8)
    canvas[top:MODEL_SIZE - bottom, left:MODEL_SIZE - right] = resized
    tensor = canvas[:, :, ::-1].transpose(2, 0, 1)[None].astype(np.float32) / 255.0

    session = _session()
    raw, prototypes = session.run(None, {session.get_inputs()[0].name: tensor})
    candidates = raw[0].T
    candidates = candidates[candidates[:, 4] >= min(thresholds)]
    if not len(candidates):
        results = [[] for _ in thresholds]
        for threshold, result in zip(thresholds, results):
            _cache_bubbles(cache_keys[threshold], result)
        return results[0] if secondary_confidence_threshold is None else tuple(results)
    boxes = candidates[:, :4].copy()
    boxes[:, :2] -= boxes[:, 2:] / 2
    selected_by_threshold = {}
    for threshold in thresholds:
        indices = cv2.dnn.NMSBoxes(
            boxes.tolist(), candidates[:, 4].tolist(), threshold, 0.6,
        )
        selected_by_threshold[threshold] = (
            set(np.asarray(indices).reshape(-1).tolist()) if len(indices) else set()
        )
    selected_indices = set().union(*selected_by_threshold.values())
    if not selected_indices:
        results = [[] for _ in thresholds]
        for threshold, result in zip(thresholds, results):
            _cache_bubbles(cache_keys[threshold], result)
        return results[0] if secondary_confidence_threshold is None else tuple(results)
    proto = prototypes[0].reshape(prototypes.shape[1], -1)
    output_by_threshold: dict[float, list[SpeechBubble]] = {
        threshold: [] for threshold in thresholds
    }
    for index in sorted(selected_indices):
        x, y, box_width, box_height = boxes[index]
        x1 = max(0, round((x - left) / scale))
        y1 = max(0, round((y - top) / scale))
        x2 = min(width, round((x + box_width - left) / scale))
        y2 = min(height, round((y + box_height - top) / scale))
        if x2 <= x1 or y2 <= y1:
            continue
        logits = (candidates[index, 5:] @ proto).reshape(prototypes.shape[2:])
        np.clip(logits, -30, 30, out=logits)
        scores = 1.0 / (1.0 + np.exp(-logits))
        # Resample just this proposal's model-space crop; upscaling every mask
        # to a full 1600x1600 plane was the main avoidable postprocess allocation.
        rx1, ry1 = max(0, round(x - left)), max(0, round(y - top))
        rx2 = min(scaled_width, round(x + box_width - left))
        ry2 = min(scaled_height, round(y + box_height - top))
        if rx2 <= rx1 or ry2 <= ry1:
            continue
        local_scores = _resize_candidate_score_crop(
            scores, (left + rx1, top + ry1, left + rx2, top + ry2),
            (x2 - x1, y2 - y1),
        )
        mask = (local_scores > 0.5).astype(np.uint8) * 255
        # Match the former full-page erosion at proposal edges by padding with
        # zero before shrinking the interior mask.
        pad = 8
        padded = cv2.copyMakeBorder(mask, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=0)
        padded = cv2.erode(padded, np.ones((5, 5), dtype=np.uint8))
        safe_padded = cv2.erode(padded, np.ones((9, 9), dtype=np.uint8))
        safe_mask = safe_padded[pad:pad + y2 - y1, pad:pad + x2 - x1]
        box_ratio = (x2 - x1) / max(1, y2 - y1)
        mask_fill = np.count_nonzero(safe_mask) / max(1, (x2 - x1) * (y2 - y1))
        # Reject page UI, panel strips, and narration-like rectangles. The
        # current stage deliberately favors speech balloons over recall.
        if not 0.35 <= box_ratio <= 3.2 or not 0.32 <= mask_fill <= 0.95:
            continue
        local_bounds = (0, 0, x2 - x1, y2 - y1)
        # Rotated quads can be genuine chat bubbles (e.g. phone-message
        # replies), so only apply this filter to the edge-clipped sticker case.
        # Interior tilted bubbles remain available to OCR.
        if (_is_rectangular_caption(safe_mask, local_bounds, mask_fill)
                or (x1 == 0 and _is_rotated_rectangular_caption(
                    safe_mask, local_bounds, mask_fill
                ))):
            continue
        # Keep small but real speech balloons (e.g. a whispered "Ehh...?" next
        # to a larger balloon). The old 0.25%-of-page cutoff discarded them
        # before OCR could decide whether they contained dialogue.
        if np.count_nonzero(safe_mask) < _minimum_safe_mask_area(width, height):
            continue
        proposal = SpeechBubble(
            (x1, y1, x2, y2), safe_mask.copy(), float(candidates[index, 4]),
        )
        for threshold in thresholds:
            if index in selected_by_threshold[threshold]:
                output_by_threshold[threshold].append(proposal)
    # The export's bounding-box NMS can still leave two mask proposals for the
    # same cloud bubble; mask containment is more useful than box IoU here.
    results = []
    for threshold in thresholds:
        result = deduplicate_bubbles(output_by_threshold[threshold])
        _cache_bubbles(cache_keys[threshold], result)
        results.append(result)
    return results[0] if secondary_confidence_threshold is None else tuple(results)


def detect_speech_bubbles(
    image: np.ndarray,
    confidence_threshold: float = 0.25,
    secondary_confidence_threshold: float | None = None,
) -> list[SpeechBubble] | tuple[list[SpeechBubble], list[SpeechBubble]]:
    """Segment balloons, using overlapping horizontal strips on tall webtoons.

    A detector that resizes a very long page to its fixed square input reduces
    balloon details to only a few pixels. Cropping the full-width page into
    overlapping strips preserves those details and returns page-relative masks.
    """
    if not isinstance(image, np.ndarray) or image.ndim != 3 or image.shape[2] != 3:
        raise ValueError("صورة اكتشاف الفقاعات يجب أن تكون مصفوفة ألوان ثلاثية القنوات.")
    paired_thresholds = secondary_confidence_threshold is not None
    height, width = image.shape[:2]
    if height <= max(2400, width * 3.2):
        if paired_thresholds:
            segmented = _detect_speech_bubbles_single(
                image, confidence_threshold, secondary_confidence_threshold
            )
        else:
            segmented = _detect_speech_bubbles_single(image, confidence_threshold)
        dark_ovals = _detect_dark_oval_bubbles(image)
        if paired_thresholds:
            primary, secondary = segmented
            return (
                deduplicate_bubbles([*primary, *dark_ovals]),
                deduplicate_bubbles([*secondary, *dark_ovals]),
            )
        return deduplicate_bubbles([*segmented, *dark_ovals]) if dark_ovals else segmented

    page = np.ascontiguousarray(image)
    page_digest = sha256(memoryview(page).cast("B")).digest()
    page_keys = {
        float(threshold): (page.shape, str(page.dtype), float(threshold), page_digest)
        for threshold in ([confidence_threshold, secondary_confidence_threshold]
                          if paired_thresholds else [confidence_threshold])
    }
    with _bubble_cache_lock:
        if all(key in _bubble_cache for key in page_keys.values()):
            cached = []
            for threshold in page_keys:
                key = page_keys[threshold]
                _bubble_cache.move_to_end(key)
                cached.append(_bubble_cache[key])
            return tuple(cached) if paired_thresholds else cached[0]

    strip_height = min(2400, max(1200, width * 2))
    overlap = min(160, strip_height // 8)
    last_start = max(0, height - strip_height)
    starts = list(range(0, last_start + 1, strip_height - overlap))
    if not starts or starts[-1] != last_start:
        starts.append(last_start)

    candidates = []
    seam_candidates = []
    secondary_candidates = []
    secondary_seam_candidates = []
    seam_margin = 12
    strip_context_height = min(2400, max(strip_height, 1800))
    for top in starts:
        bottom = min(height, top + strip_height)
        strip = page[top:bottom]
        if paired_thresholds:
            detected = _detect_speech_bubbles_single(
                strip, confidence_threshold, secondary_confidence_threshold
            )
        else:
            detected = _detect_speech_bubbles_single(strip, confidence_threshold)
        if paired_thresholds:
            primary_strip_bubbles, secondary_strip_bubbles = detected
        else:
            primary_strip_bubbles, secondary_strip_bubbles = detected, []
        for bubble in primary_strip_bubbles:
            left, bubble_top, right, bubble_bottom = bubble.bbox
            mapped = SpeechBubble(
                (left, bubble_top + top, right, bubble_bottom + top),
                bubble.safe_mask,
                bubble.confidence,
            )
            candidates.append(mapped)
            # A balloon cut by an internal strip edge can still produce a
            # plausible partial mask. Re-segment only uncertain edge-touching
            # proposals in a taller context crop; ordinary balloons add no
            # extra inference and the page-edge cases remain untouched.
            touches_internal_top = top > 0 and bubble_top <= seam_margin
            touches_internal_bottom = bottom < height and bubble_bottom >= (bottom - top) - seam_margin
            if (bubble.confidence < 0.60
                    and (touches_internal_top or touches_internal_bottom)):
                seam_candidates.append(mapped)
        for bubble in secondary_strip_bubbles:
            left, bubble_top, right, bubble_bottom = bubble.bbox
            mapped = SpeechBubble(
                (left, bubble_top + top, right, bubble_bottom + top),
                bubble.safe_mask,
                bubble.confidence,
            )
            secondary_candidates.append(mapped)
            touches_internal_top = top > 0 and bubble_top <= seam_margin
            touches_internal_bottom = bottom < height and bubble_bottom >= (bottom - top) - seam_margin
            if (bubble.confidence < 0.60
                    and (touches_internal_top or touches_internal_bottom)):
                secondary_seam_candidates.append(mapped)

    # Limit duplicate retries when the same partial mask appears on both sides
    # of a seam. Deduplication is mask-aware and retains the larger proposal.
    def refine_seams(partials, threshold, output):
        for partial in deduplicate_bubbles(partials):
            x1, y1, x2, y2 = partial.bbox
            center_y = (y1 + y2) // 2
            context_top = max(0, min(height - strip_context_height,
                                     center_y - strip_context_height // 2))
            context_bottom = min(height, context_top + strip_context_height)
            context = page[context_top:context_bottom]
            for refined in _detect_speech_bubbles_single(context, threshold):
                rx1, ry1, rx2, ry2 = refined.bbox
                mapped_box = (rx1, ry1 + context_top, rx2, ry2 + context_top)
                ix1, iy1 = max(x1, mapped_box[0]), max(y1, mapped_box[1])
                ix2, iy2 = min(x2, mapped_box[2]), min(y2, mapped_box[3])
                intersection = max(0, ix2 - ix1) * max(0, iy2 - iy1)
                partial_area = max(1, (x2 - x1) * (y2 - y1))
                x_overlap = max(0, ix2 - ix1) / max(
                    1, min(x2 - x1, mapped_box[2] - mapped_box[0])
                )
                if intersection / partial_area < 0.35 or x_overlap < 0.50:
                    continue
                output.append(SpeechBubble(
                    mapped_box, refined.safe_mask, refined.confidence
                ))

    refine_seams(seam_candidates, confidence_threshold, candidates)
    if paired_thresholds:
        refine_seams(
            secondary_seam_candidates, secondary_confidence_threshold,
            secondary_candidates,
        )
    dark_ovals = _detect_dark_oval_bubbles(page)
    result = deduplicate_bubbles([*candidates, *dark_ovals])
    _cache_bubbles(page_keys[float(confidence_threshold)], result)
    if not paired_thresholds:
        return result
    secondary_result = deduplicate_bubbles([*secondary_candidates, *dark_ovals])
    _cache_bubbles(page_keys[float(secondary_confidence_threshold)], secondary_result)
    return result, secondary_result


def _cache_bubbles(key, bubbles: list[SpeechBubble]) -> None:
    """Keep a small LRU of compact masks so OCR and rendering share detections."""
    global _bubble_cache_bytes
    entry_bytes = sum(bubble.safe_mask.nbytes for bubble in bubbles)
    if entry_bytes > _BUBBLE_CACHE_MAX_BYTES:
        return
    with _bubble_cache_lock:
        previous = _bubble_cache.pop(key, None)
        if previous is not None:
            _bubble_cache_bytes -= sum(bubble.safe_mask.nbytes for bubble in previous)
        _bubble_cache[key] = bubbles
        _bubble_cache_bytes += entry_bytes
        while (_bubble_cache_bytes > _BUBBLE_CACHE_MAX_BYTES
               or len(_bubble_cache) > _BUBBLE_CACHE_MAX_ENTRIES):
            _old_key, old_bubbles = _bubble_cache.popitem(last=False)
            _bubble_cache_bytes -= sum(bubble.safe_mask.nbytes for bubble in old_bubbles)


def _detect_dark_oval_bubbles(image: np.ndarray) -> list[SpeechBubble]:
    """Recover dark-filled oval balloons missed by the instance model.

    This conservative fallback is geometry-only: a dark connected component
    must closely fill a fitted ellipse in both directions. It intentionally
    ignores rectangular cards and irregular dark artwork, which remain in the
    external-text path.
    """
    height, width = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    binary = (gray < 30).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(binary, 8)
    minimum_area = max(20_000, _minimum_safe_mask_area(width, height) * 8)
    found: list[SpeechBubble] = []
    for label in range(1, count):
        left, top, box_width, box_height, area = map(int, stats[label])
        if (area < minimum_area or box_width < 100 or box_height < 100
                or not 0.55 <= box_width / max(1, box_height) <= 3.2):
            continue
        component = (labels[top:top + box_height, left:left + box_width] == label).astype(np.uint8)
        contours, _ = cv2.findContours(component, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        if not contours:
            continue
        contour = max(contours, key=cv2.contourArea)
        if len(contour) < 5:
            continue
        ellipse = cv2.fitEllipse(contour)
        ellipse_mask = np.zeros_like(component)
        cv2.ellipse(ellipse_mask, ellipse, 1, -1)
        intersection = int(np.count_nonzero(component & ellipse_mask))
        ellipse_pixels = int(np.count_nonzero(ellipse_mask))
        if (intersection / max(1, area) < 0.95
                or intersection / max(1, ellipse_pixels) < 0.75):
            continue
        # The detector's safe masks are inset from the boundary so the inpaint
        # and typesetting stages cannot overwrite the balloon outline.
        padded = cv2.copyMakeBorder(ellipse_mask * 255, 8, 8, 8, 8,
                                    cv2.BORDER_CONSTANT, value=0)
        padded = cv2.erode(padded, np.ones((15, 15), dtype=np.uint8))
        safe_mask = padded[8:8 + box_height, 8:8 + box_width]
        if np.count_nonzero(safe_mask) < _minimum_safe_mask_area(width, height):
            continue
        found.append(SpeechBubble(
            (left, top, left + box_width, top + box_height),
            safe_mask,
            0.40,
        ))
    return found

