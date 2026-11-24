"""Local hybrid bubble repair and style-aware Arabic typesetting."""

from __future__ import annotations

import hashlib
import io
import os
import tempfile
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import arabic_reshaper
from bidi.algorithm import get_display
from ocr_engine.speech_bubbles import SpeechBubble, detect_speech_bubbles
from .image_codec import decode_color_image


WINDOWS_FONTS = (
    Path("C:/Windows/Fonts/arial.ttf"),
    Path("C:/Windows/Fonts/tahoma.ttf"),
    Path("C:/Windows/Fonts/segoeui.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
)


def _font_path(bold: bool = False) -> Path:
    configured = os.getenv("MANGA_ARABIC_FONT")
    bold_candidates = (
        Path("C:/Windows/Fonts/tahomabd.ttf"),
        Path("C:/Windows/Fonts/arialbd.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    )
    candidates = ([Path(configured)] if configured else []) + (
        list(bold_candidates) + list(WINDOWS_FONTS) if bold else list(WINDOWS_FONTS)
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise RuntimeError("لم أعثر على خط عربي. اضبط MANGA_ARABIC_FONT إلى ملف خط يدعم العربية.")


def _display_text(text: str) -> str:
    return get_display(arabic_reshaper.reshape(text))


def _wrap(text: str, font, max_width: int) -> list[str]:
    words = text.split()
    lines = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and font.getlength(_display_text(candidate)) > max_width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def _build_ink_mask(image: np.ndarray, region: dict, bounds: tuple[int, int, int, int]) -> np.ndarray:
    """Return a crop-local mask of source glyphs inside the OCR bounds."""
    left, top, right, bottom = bounds
    gray = cv2.cvtColor(image[top:bottom, left:right], cv2.COLOR_BGR2GRAY)
    # Contrast against a local blur catches both dark lettering on white paper
    # and white/stylized lettering on dark art, without erasing a whole OCR box.
    background = cv2.GaussianBlur(gray, (0, 0), sigmaX=5, sigmaY=5)
    local = (cv2.absdiff(gray, background) >= 22).astype(np.uint8) * 255
    polygons = region.get("ocr_polygons") or []
    if polygons:
        support = np.zeros(gray.shape, dtype=np.uint8)
        for polygon in polygons:
            points = np.asarray(polygon, dtype=np.float32)
            points[:, 0] -= left
            points[:, 1] -= top
            cv2.fillPoly(support, [np.rint(points).astype(np.int32)], 255)
        # OCR polygons identify the complete text line, not just high-contrast
        # center strokes. Clearing that confirmed footprint prevents antialiased
        # outlines and thin glyph fragments from surviving beside the Arabic.
        support = cv2.dilate(
            support, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)), iterations=1
        )
        local = support
    else:
        # A high absolute luminance is not enough to call a pixel background:
        # stylized manhwa captions often use white lettering over dark art.
        # Suppress nearly-white pixels only when their local neighborhood is
        # also bright; keep high-contrast white strokes on dark backgrounds.
        locally_bright = gray > 245
        bright_background = background >= 235
        local[locally_bright & bright_background] = 0
    kernel_size = 3 if min(gray.shape) < 80 else 5
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
    local = cv2.dilate(local, kernel, iterations=1)
    return local


def _bubble_safe_mask(bubble, polygon=None) -> np.ndarray:
    """Intersect the detector's safe mask with a user-drawn polygon, if saved."""
    safe = np.asarray(bubble.safe_mask, dtype=np.uint8).copy()
    if not isinstance(polygon, list) or len(polygon) < 3:
        return safe
    bx1, by1, bx2, by2 = bubble.bbox
    try:
        points = np.asarray([[int(point[0]) - bx1, int(point[1]) - by1]
                             for point in polygon], dtype=np.int32)
    except (TypeError, ValueError, IndexError, OverflowError):
        return safe
    if (points.ndim != 2 or points.shape[1] != 2
            or np.any(points[:, 0] < 0) or np.any(points[:, 1] < 0)
            or np.any(points[:, 0] >= safe.shape[1]) or np.any(points[:, 1] >= safe.shape[0])):
        return safe
    manual = np.zeros_like(safe)
    cv2.fillPoly(manual, [points.reshape(-1, 1, 2)], 255)
    return cv2.bitwise_and(safe, manual)


def _bubble_mask_in_bounds(bubble, bounds: tuple[int, int, int, int], polygon=None) -> np.ndarray:
    """Return a compact safe-mask crop aligned to the requested page bounds."""
    left, top, right, bottom = bounds
    bx1, by1, bx2, by2 = bubble.bbox
    safe = _bubble_safe_mask(bubble, polygon)
    result = np.zeros((bottom - top, right - left), dtype=np.uint8)
    x1, y1, x2, y2 = max(left, bx1), max(top, by1), min(right, bx2), min(bottom, by2)
    if x2 > x1 and y2 > y1:
        result[y1 - top:y2 - top, x1 - left:x2 - left] = (
            safe[y1 - by1:y2 - by1, x1 - bx1:x2 - bx1]
        )
    return result


def _bubble_text_color(image: np.ndarray, bubble) -> tuple[int, int, int]:
    """Choose a readable single ink color from the detected balloon interior."""
    left, top, right, bottom = bubble.bbox
    patch = image[top:bottom, left:right]
    safe = np.asarray(bubble.safe_mask) > 0
    if patch.shape[:2] != safe.shape or not np.any(safe):
        return (20, 20, 20)
    luminance = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
    median_luminance = float(np.median(luminance[safe]))
    # Keep the simple, reliable high-contrast treatment used for ordinary
    # white balloons, while making dark or colored balloons readable too.
    return (255, 255, 255) if median_luminance < 118 else (20, 20, 20)


def _external_text_style(image: np.ndarray, region: dict) -> dict:
    """Estimate foreground, optional outline, and weight from the source glyphs."""
    height, width = image.shape[:2]
    pixels, backgrounds, source_heights = [], [], []
    polygons = region.get("ocr_polygons") or []
    for box_index, box in enumerate(region.get("ocr_boxes", [])):
        x, y, box_width, box_height = (int(round(value)) for value in box)
        x1, y1 = max(0, x), max(0, y)
        x2, y2 = min(width, x + box_width), min(height, y + box_height)
        if x2 <= x1 or y2 <= y1:
            continue
        crop = image[y1:y2, x1:x2]
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        support = np.zeros(gray.shape, dtype=np.uint8)
        if box_index < len(polygons):
            try:
                points = np.asarray(polygons[box_index], dtype=np.float32).reshape(-1, 2)
                if len(points) >= 3 and np.isfinite(points).all():
                    points[:, 0] -= x1
                    points[:, 1] -= y1
                    cv2.fillConvexPoly(support, np.rint(points).astype(np.int32), 255)
            except (TypeError, ValueError, cv2.error):
                pass
        else:
            support[:] = 255
        if not np.any(support):
            support[:] = 255
        local_background = cv2.GaussianBlur(gray, (0, 0), sigmaX=2.0, sigmaY=2.0)
        backgrounds.append(float(np.median(local_background[support > 0])))
        contrast = gray.astype(np.int16) - local_background.astype(np.int16)
        candidates = (np.abs(contrast) >= 18) & (support > 0)
        # Preserve both polarities: outlined white glyphs over dark art can have
        # more dark outline pixels than bright fill pixels. The source fill is
        # separated from its outline below using this local background estimate.
        if np.count_nonzero(candidates) < 3:
            candidates = support > 0
        pixels.append((crop[candidates].reshape(-1, 3), gray[candidates].reshape(-1)))
        source_heights.append(box_height)
    if not pixels:
        return {"fill": (25, 25, 25), "outline": None, "stroke_width": 0,
                "bold": False, "source_line_height": 0}

    background_luma = float(np.median(backgrounds))
    colors = np.concatenate([item[0] for item in pixels]).astype(np.float32)
    lumas = np.concatenate([item[1] for item in pixels]).astype(np.float32)
    high_contrast = np.abs(lumas - background_luma) >= 28
    if background_luma >= 135:
        glyphs = colors[high_contrast & (lumas <= np.percentile(lumas, 35))]
        outline_candidates = colors[high_contrast & (lumas > np.percentile(lumas, 35))]
    else:
        glyphs = colors[high_contrast & (lumas >= np.percentile(lumas, 65))]
        outline_candidates = colors[high_contrast & (lumas < np.percentile(lumas, 65))]
    if not len(glyphs):
        glyphs = colors
    fill = tuple(int(value) for value in np.median(glyphs, axis=0)[::-1])
    outline = None
    if len(outline_candidates) >= 6:
        hsv = cv2.cvtColor(outline_candidates.reshape(-1, 1, 3).astype(np.uint8), cv2.COLOR_BGR2HSV).reshape(-1, 3)
        saturated = outline_candidates[hsv[:, 1] >= 70]
        if len(saturated) >= 6:
            outline = tuple(int(value) for value in np.median(saturated, axis=0)[::-1])
        else:
            outline_luma = float(np.median(cv2.cvtColor(
                outline_candidates.reshape(-1, 1, 3).astype(np.uint8), cv2.COLOR_BGR2GRAY,
            )))
            fill_luma = float(np.median(cv2.cvtColor(
                glyphs.reshape(-1, 1, 3).astype(np.uint8), cv2.COLOR_BGR2GRAY,
            )))
            if abs(fill_luma - outline_luma) >= 45:
                outline = tuple(int(value) for value in np.median(outline_candidates, axis=0)[::-1])
    median_height = float(np.median(source_heights))
    stroke_width = max(1, min(4, round(median_height * 0.055))) if outline else 0
    return {
        "fill": fill,
        "outline": outline,
        "stroke_width": stroke_width,
        "bold": median_height >= 12,
        "source_line_height": median_height,
    }


def _expanded_external_bounds(image: np.ndarray, bounds: tuple[int, int, int, int],
                              forbidden_bounds=()) -> tuple[int, int, int, int] | None:
    """Find a larger, visually quiet layout area without crossing other OCR."""
    height, width = image.shape[:2]
    left, top, right, bottom = bounds
    base_width, base_height = right - left, bottom - top
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    for width_scale, height_scale in (
        (1.15, 1.5), (1.35, 2.0), (1.6, 3.0), (2.0, 4.0),
    ):
        new_width = min(width, int(round(base_width * width_scale)))
        new_height = min(height, int(round(base_height * height_scale)))
        cx = (left + right) // 2
        x_positions = [max(0, cx - new_width // 2)]
        # Prefer quiet space above/below the source when a nearby caption limits
        # symmetric growth; keep the original text block anchored in its panel.
        y_positions = [
            max(0, bottom - new_height),
            max(0, top),
            max(0, (top + bottom - new_height) // 2),
        ]
        for x1, y1 in ((x, y) for x in x_positions for y in y_positions):
            x2, y2 = min(width, x1 + new_width), min(height, y1 + new_height)
            x1, y1 = max(0, x2 - new_width), max(0, y2 - new_height)
            candidate = (x1, y1, x2, y2)
            if candidate == bounds:
                continue
            if any(max(x1, fx1) < min(x2, fx2) and max(y1, fy1) < min(y2, fy2)
                   for fx1, fy1, fx2, fy2 in forbidden_bounds):
                continue
            ring = np.ones((y2 - y1, x2 - x1), dtype=bool)
            ring[top - y1:bottom - y1, left - x1:right - x1] = False
            values = gray[y1:y2, x1:x2][ring]
            if len(values) < 12 or np.percentile(values, 90) - np.percentile(values, 10) > 32:
                continue
            return candidate
    return None


def _flat_external_background(image: np.ndarray, bounds: tuple[int, int, int, int],
                             forbidden_bounds=()) -> tuple[int, int, int] | None:
    """Return a sampled flat color only when the surrounding confirmed box is quiet."""
    height, width = image.shape[:2]
    left, top, right, bottom = bounds
    margin = max(4, min(10, int(min(right - left, bottom - top) * 0.15)))
    x1, y1 = max(0, left - margin), max(0, top - margin)
    x2, y2 = min(width, right + margin), min(height, bottom + margin)
    ring = np.ones((y2 - y1, x2 - x1), dtype=bool)
    ring[top - y1:bottom - y1, left - x1:right - x1] = False
    for fx1, fy1, fx2, fy2 in forbidden_bounds:
        ax1, ay1 = max(x1, fx1), max(y1, fy1)
        ax2, ay2 = min(x2, fx2), min(y2, fy2)
        if ax2 > ax1 and ay2 > ay1:
            ring[ay1 - y1:ay2 - y1, ax1 - x1:ax2 - x1] = False
    samples = image[y1:y2, x1:x2][ring]
    if len(samples) < 32:
        return None
    low, high = np.percentile(samples, [10, 90], axis=0)
    gray = cv2.cvtColor(samples.reshape(-1, 1, 3), cv2.COLOR_BGR2GRAY).reshape(-1)
    if float(np.percentile(gray, 90) - np.percentile(gray, 10)) > 12:
        return None
    if float(np.max(high - low)) > 18:
        return None
    return tuple(int(value) for value in np.median(samples, axis=0))


def _safe_manual_external_bounds(image: np.ndarray, source_bounds,
                                 manual_bounds, forbidden_bounds=()):
    """Validate an explicitly reviewed text layout against page and OCR geometry."""
    if not isinstance(manual_bounds, (list, tuple)) or len(manual_bounds) != 4:
        return None
    height, width = image.shape[:2]
    try:
        x1, y1, x2, y2 = (int(round(float(v))) for v in manual_bounds)
    except (TypeError, ValueError, OverflowError):
        return None
    sx1, sy1, sx2, sy2 = source_bounds
    if not (0 <= x1 <= sx1 < sx2 <= x2 <= width
            and 0 <= y1 <= sy1 < sy2 <= y2 <= height):
        return None
    candidate = (x1, y1, x2, y2)
    if any(max(x1, fx1) < min(x2, fx2) and max(y1, fy1) < min(y2, fy2)
           for fx1, fy1, fx2, fy2 in forbidden_bounds):
        return None
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    ring = np.ones((y2 - y1, x2 - x1), dtype=bool)
    ring[sy1 - y1:sy2 - y1, sx1 - x1:sx2 - x1] = False
    values = gray[y1:y2, x1:x2][ring]
    if len(values) < 12 or np.percentile(values, 90) - np.percentile(values, 10) > 32:
        return None
    return candidate


def _draw_external_text(canvas: Image.Image, text: str, region: dict,
                        bounds: tuple[int, int, int, int], image: np.ndarray) -> bool:
    """Replace an external text block with fitted, style-aware Arabic glyphs.

    The transformed overlay is constrained to the detected source bounds. If
    no readable size fits, skip the region instead of covering nearby artwork.
    """
    left, top, right, bottom = bounds
    width, height = right - left, bottom - top
    if width < 12 or height < 10:
        return False
    style = _external_text_style(image, region)
    font_path = _font_path(bold=style["bold"])
    angle = float(region.get("angle", 0.0))
    angle = max(-60.0, min(60.0, angle))
    boxes = region.get("ocr_boxes") or []
    try:
        source_width = max(float(box[2]) for box in boxes)
        source_height = max(float(box[3]) for box in boxes)
    except (TypeError, ValueError, IndexError):
        source_width, source_height = width, height
    # OCR angle classifiers can mark wide horizontal captions as slanted or
    # vertical. Trust measured text geometry for any substantial disagreement;
    # genuinely vertical lettering has a tall, narrow OCR footprint.
    if abs(angle) >= 10.0 and source_width >= source_height * 1.4:
        angle = 0.0
    radians = np.radians(abs(angle))
    margin_x = int(np.ceil((height * np.sin(radians) + width * (1 - np.cos(radians))) / 2)) + 6
    margin_y = int(np.ceil((width * np.sin(radians) + height * (1 - np.cos(radians))) / 2)) + 6
    layer_width = min(5000, width + 2 * margin_x)
    layer_height = min(5000, height + 2 * margin_y)
    minimum_size = max(10, min(14, int(min(width, height) * 0.1)))
    source_height = float(style.get("source_line_height", 0) or 0)
    # The whole caption box can be much taller than the original lettering.
    # Anchor the starting size to OCR line height, then shrink only if Arabic
    # wrapping needs more room.
    preferred_size = min(height, int(round(source_height * 1.1))) if source_height else height
    maximum_size = min(64, max(minimum_size, preferred_size))
    center_x, center_y = layer_width // 2, layer_height // 2
    for size in range(maximum_size, minimum_size - 1, -1):
        font = ImageFont.truetype(str(font_path), size=size)
        lines = _wrap(text, font, max(1, int(width * 0.9)))
        line_height = max(1, int(size * 1.2))
        if len(lines) * line_height > height * 0.88:
            continue
        overlay = Image.new("RGBA", (layer_width, layer_height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        shaped = [_display_text(line) for line in lines]
        # Pillow's ImageDraw.textlength does not accept stroke_width in all
        # supported releases. Center on glyph advance here; the final alpha
        # bounds below still include the actual outline when validating fit.
        widths = [draw.textlength(line, font=font) for line in shaped]
        start_y = center_y - len(lines) * line_height // 2
        for index, line in enumerate(shaped):
            draw.text(
                (center_x - widths[index] / 2, start_y + index * line_height), line,
                font=font, fill=style["fill"],
                stroke_width=style["stroke_width"],
                stroke_fill=style["outline"] or style["fill"],
            )
        rotated = overlay.rotate(-angle, resample=Image.Resampling.BICUBIC, expand=True)
        alpha_bounds = rotated.getchannel("A").getbbox()
        if alpha_bounds is None:
            continue
        glyphs = rotated.crop(alpha_bounds)
        if glyphs.width > width - 4 or glyphs.height > height - 4:
            continue
        destination = (
            left + (width - glyphs.width) // 2,
            top + (height - glyphs.height) // 2,
        )
        if canvas.mode == "RGBA":
            canvas.alpha_composite(glyphs, destination)
        else:
            canvas.paste(glyphs, destination, glyphs.getchannel("A"))
        return True
    return False


def _repair_bubble_interior(repaired: np.ndarray, original: np.ndarray,
                            erase: np.ndarray, safe_mask: np.ndarray,
                            bounds: tuple[int, int, int, int]) -> str:
    """Choose safe flat fill, smooth bubble overlay, or local text inpainting."""
    left, top, right, bottom = bounds
    patch = original[top:bottom, left:right].astype(np.float32)
    safe = safe_mask > 0
    erased = erase > 0
    if int(np.count_nonzero(safe)) < 100 or patch.shape[:2] != safe.shape:
        return "text_inpainting"
    sample_mask = safe & ~cv2.dilate(
        erased.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (11, 11)),
        iterations=1,
    ).astype(bool)
    sy, sx = np.where(sample_mask)
    if len(sx) < 100:
        return "text_inpainting"
    values = patch[sy, sx]
    luminance = cv2.cvtColor(values.reshape(-1, 1, 3).astype(np.uint8), cv2.COLOR_BGR2GRAY).reshape(-1)
    luminance_spread = float(np.percentile(luminance, 90) - np.percentile(luminance, 10))
    x_norm = sx.astype(np.float32) / max(1, patch.shape[1] - 1)
    y_norm = sy.astype(np.float32) / max(1, patch.shape[0] - 1)
    if len(sx) > 30000:
        take = np.linspace(0, len(sx) - 1, 30000).astype(np.int32)
        fit_x, fit_y, fit_values = x_norm[take], y_norm[take], values[take]
    else:
        fit_x, fit_y, fit_values = x_norm, y_norm, values
    fit_design = np.column_stack((np.ones(len(fit_x), dtype=np.float32), fit_x, fit_y))
    coefficients, *_ = np.linalg.lstsq(fit_design, fit_values, rcond=None)
    residual = fit_values - fit_design @ coefficients
    residual_rms = float(np.sqrt(np.mean(np.square(residual))))
    median_fill = np.median(values, axis=0).astype(np.uint8)

    if residual_rms <= 7.0 and luminance_spread <= 18.0:
        target = repaired[top:bottom, left:right]
        target[erased] = median_fill
        return "interior_fill"

    # Estimate a smooth gradient from untouched pixels, then paint only the
    # erased lettering mask. Preserve all original details elsewhere.
    if residual_rms <= 15.0 and luminance_spread <= 70.0:
        fill_y, fill_x = np.where(safe & erased)
        if not len(fill_x):
            return "text_inpainting"
        xg = fill_x.astype(np.float32) / max(1, patch.shape[1] - 1)
        yg = fill_y.astype(np.float32) / max(1, patch.shape[0] - 1)
        colors = np.clip(
            coefficients[0] + xg[:, None] * coefficients[1] + yg[:, None] * coefficients[2],
            0, 255,
        ).astype(np.uint8)
        target = repaired[top:bottom, left:right]
        target[fill_y, fill_x] = colors
        return "bubble_overlay"
    return "text_inpainting"


def _bubble_glyphs(text: str, bubble_mask: np.ndarray, bounds: tuple[int, int, int, int], font_path: Path):
    """Fit one uniform font size to a bubble and return glyph pixels only."""
    left, top, right, bottom = bounds
    width, height = right - left, bottom - top
    safe = bubble_mask > 0
    if safe.shape != (height, width):
        return None
    if not np.any(safe):
        return None
    safe_y, safe_x = np.where(safe)
    center_x, center_y = float(safe_x.mean()), float(safe_y.mean())
    # Avoid rendering illegibly tiny Arabic in large webtoon balloons. If the
    # text cannot fit above this size, report the bubble as skipped for review.
    # Keep the rendered bubble text legible at normal reader zoom. The floor
    # scales with the source page resolution; if it cannot fit, the source
    # lettering is retained and the region is flagged for review.
    # Avoid shrinking long Arabic lines into unreadable lettering. The floor
    # grows with the balloon width because original webtoon pages are often
    # rendered at high resolution; a fixed 10px minimum becomes tiny in the
    # reader. If a long utterance cannot fit above this floor, keep the source
    # lettering and flag the region for review instead of drawing microtext.
    minimum_font_size = max(12, min(26, int(round(bubble_mask.shape[1] * 0.04))))
    for size in range(min(48, max(minimum_font_size, height // 3)), minimum_font_size - 1, -1):
        font = ImageFont.truetype(str(font_path), size=size)
        for width_fraction in (0.9, 0.82, 0.74, 0.66):
            lines = _wrap(text, font, max(1, int(width * width_fraction)))
            line_height = max(1, int(size * 1.25))
            if len(lines) * line_height > height * 0.88:
                continue
            # Test exact rasterized glyph coverage using tight line crops. The
            # former full-bubble mask allocation for each candidate font size
            # made dense dialogue pages spend time rasterizing mostly empty
            # balloon interiors many times.
            probe = ImageDraw.Draw(Image.new("L", (1, 1), 0))
            line_widths = [probe.textlength(_display_text(line), font=font) for line in lines]
            first_y = int(round(center_y - len(lines) * line_height / 2))
            placements = []
            candidate_fits = True
            for index, (line, measured_width) in enumerate(zip(lines, line_widths)):
                shaped = _display_text(line)
                x = int(round(center_x - measured_width / 2))
                y = first_y + index * line_height
                glyph_box = probe.textbbox((0, 0), shaped, font=font)
                glyph_width = glyph_box[2] - glyph_box[0]
                glyph_height = glyph_box[3] - glyph_box[1]
                if glyph_width <= 0 or glyph_height <= 0:
                    candidate_fits = False
                    break
                glyph_left, glyph_top = x + glyph_box[0], y + glyph_box[1]
                glyph_right, glyph_bottom = glyph_left + glyph_width, glyph_top + glyph_height
                if (glyph_left < 0 or glyph_top < 0 or glyph_right > width
                        or glyph_bottom > height):
                    candidate_fits = False
                    break
                glyph_layer = Image.new("L", (glyph_width, glyph_height), 0)
                ImageDraw.Draw(glyph_layer).text(
                    (-glyph_box[0], -glyph_box[1]), shaped, fill=255, font=font
                )
                glyph_pixels = np.asarray(glyph_layer)
                safe_crop = safe[glyph_top:glyph_bottom, glyph_left:glyph_right]
                if np.any((glyph_pixels > 0) & ~safe_crop):
                    candidate_fits = False
                    break
                placements.append((shaped, x, y))
            if candidate_fits and placements:
                layer = Image.new("L", (width, height), 0)
                painter = ImageDraw.Draw(layer)
                for shaped, x, y in placements:
                    painter.text((x, y), shaped, fill=255, font=font)
                pixels = np.asarray(layer)
                if not np.any(pixels) or np.any((pixels > 0) & ~safe):
                    continue
                return pixels, size
    return None


def _text_layout_bounds(region: dict, bubble_bounds: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """Use the original lettering block as a typesetting anchor inside its bubble.

    Some balloons contain character faces or decorations below the dialogue.
    Centering a replacement over the entire mask would cover those drawings.
    """
    manual = region.get("manual_layout_bbox")
    if isinstance(manual, (list, tuple)) and len(manual) == 4:
        try:
            x1, y1, x2, y2 = (int(value) for value in manual)
            bx1, by1, bx2, by2 = bubble_bounds
            if bx1 <= x1 < x2 <= bx2 and by1 <= y1 < y2 <= by2:
                return x1, y1, x2, y2
        except (TypeError, ValueError, OverflowError):
            pass
    boxes = region.get("ocr_boxes") or []
    if not boxes:
        return bubble_bounds
    bubble_left, bubble_top, bubble_right, bubble_bottom = bubble_bounds
    try:
        xs = [float(box[0]) for box in boxes]
        ys = [float(box[1]) for box in boxes]
        rights = [float(box[0]) + float(box[2]) for box in boxes]
        bottoms = [float(box[1]) + float(box[3]) for box in boxes]
    except (TypeError, ValueError, IndexError):
        return bubble_bounds
    bubble_width = bubble_right - bubble_left
    bubble_height = bubble_bottom - bubble_top
    pad_x = max(4, int(bubble_width * 0.12))
    pad_y = max(3, int(bubble_height * 0.07))
    left = max(bubble_left, int(min(xs)) - pad_x)
    top = max(bubble_top, int(min(ys)) - pad_y)
    right = min(bubble_right, int(np.ceil(max(rights))) + pad_x)
    bottom = min(bubble_bottom, int(np.ceil(max(bottoms))) + pad_y)
    if right <= left or bottom <= top:
        return bubble_bounds
    # OCR wraps English according to its original lettering. Arabic may need a
    # wider line, so give the replacement a useful minimum width while keeping
    # its center anchored to the source text block and its vertical position
    # away from any character art below the dialogue.
    minimum_width = min(bubble_width, max(right - left, int(bubble_width * 0.72)))
    block_center_x = (left + right) / 2
    left = max(bubble_left, int(round(block_center_x - minimum_width / 2)))
    right = min(bubble_right, left + minimum_width)
    left = max(bubble_left, right - minimum_width)
    return left, top, right, bottom


def _match_saved_bubble(region: dict, detected_bubbles: list):
    """Match saved OCR geometry before using a detector's transient list index."""
    bubble_id = region.get("bubble_id")
    saved = region.get("bubble_bbox")
    if isinstance(saved, (list, tuple)) and len(saved) == 4:
        try:
            sx1, sy1, sx2, sy2 = (int(value) for value in saved)
            if sx2 > sx1 and sy2 > sy1:
                scored = []
                saved_area = (sx2 - sx1) * (sy2 - sy1)
                for bubble in detected_bubbles:
                    bx1, by1, bx2, by2 = bubble.bbox
                    intersection = max(0, min(sx2, bx2) - max(sx1, bx1)) * max(
                        0, min(sy2, by2) - max(sy1, by1)
                    )
                    union = (sx2 - sx1) * (sy2 - sy1) + (bx2 - bx1) * (by2 - by1) - intersection
                    overlap = intersection / union if union else 0.0
                    coverage = intersection / saved_area
                    scored.append((overlap, coverage, bubble))
                if scored:
                    overlap, coverage, best = max(scored, key=lambda item: (item[0], item[1]))
                    # A newer detector may merge two adjacent balloons into a
                    # single mask. Their saved boxes still lie almost entirely
                    # inside that mask even though each IoU is below 0.5.
                    return best if overlap >= 0.5 or coverage >= 0.85 else None
        except (TypeError, ValueError, OverflowError):
            return None
    if isinstance(bubble_id, int) and 1 <= bubble_id <= len(detected_bubbles):
        return detected_bubbles[bubble_id - 1]
    return None


def _bubble_render_bounds(region: dict, bubble) -> tuple[int, int, int, int]:
    """Keep a saved sub-balloon when a later detector merges adjacent ones."""
    current = bubble.bbox
    saved = region.get("bubble_bbox")
    if not isinstance(saved, (list, tuple)) or len(saved) != 4:
        return current
    try:
        sx1, sy1, sx2, sy2 = (int(value) for value in saved)
    except (TypeError, ValueError, OverflowError):
        return current
    bx1, by1, bx2, by2 = current
    x1, y1, x2, y2 = max(sx1, bx1), max(sy1, by1), min(sx2, bx2), min(sy2, by2)
    saved_area = max(0, sx2 - sx1) * max(0, sy2 - sy1)
    current_area = (bx2 - bx1) * (by2 - by1)
    if (saved_area > 0 and (x2 - x1) * (y2 - y1) >= 0.85 * saved_area
            and current_area >= 1.25 * saved_area):
        return x1, y1, x2, y2
    return current


def _load_or_detect_bubbles(image: np.ndarray, image_bytes: bytes, cache_dir=None):
    """Reuse exact-page segmentation masks across render runs when requested."""
    if cache_dir is None:
        return detect_speech_bubbles(image), False
    directory = Path(cache_dir)
    directory.mkdir(parents=True, exist_ok=True)
    cache_path = directory / (hashlib.sha256(image_bytes).hexdigest() + ".npz")
    if cache_path.is_file():
        try:
            with np.load(cache_path, allow_pickle=False) as saved:
                boxes = saved["boxes"]
                confidences = saved["confidences"]
                bubbles = [
                    SpeechBubble(
                        tuple(int(v) for v in boxes[index]),
                        saved[f"mask_{index}"].copy(),
                        float(confidences[index]),
                    )
                    for index in range(len(boxes))
                ]
            return bubbles, True
        except (OSError, ValueError, KeyError, IndexError):
            # A partial cache is disposable; recompute from the unchanged source.
            try:
                cache_path.unlink()
            except OSError:
                pass
    bubbles = detect_speech_bubbles(image)
    arrays = {f"mask_{index}": bubble.safe_mask for index, bubble in enumerate(bubbles)}
    boxes = np.asarray([bubble.bbox for bubble in bubbles], dtype=np.int32).reshape(-1, 4)
    confidences = np.asarray([bubble.confidence for bubble in bubbles], dtype=np.float32)
    try:
        with tempfile.NamedTemporaryFile(
            mode="w+b", suffix=".npz", prefix="bubble-", dir=directory, delete=False
        ) as temp_file:
            temp_path = Path(temp_file.name)
            np.savez(temp_file, boxes=boxes, confidences=confidences, **arrays)
        os.replace(temp_path, cache_path)
    except OSError:
        try:
            temp_path.unlink(missing_ok=True)
        except (OSError, UnboundLocalError):
            pass
    return bubbles, False


def render_translated_page(image_bytes: bytes, translated_regions: list[dict], *,
                           include_layers: bool = False, bubble_cache_dir=None):
    """Repair bubble or external text regions and typeset Arabic within bounds.

    Flat fills, smooth bubble overlays, and local text inpainting are selected
    per bubble. External text uses estimated color, weight, and rotation. Review
    every page because automatic OCR and style matching can still be imperfect.
    """
    started = time.perf_counter()
    image = decode_color_image(image_bytes)
    if image is None:
        raise ValueError("تعذر فتح صورة الصفحة.")
    height, width = image.shape[:2]
    if width * height > 60_000_000:
        raise ValueError("أبعاد الصورة كبيرة جداً للمعالجة المحلية.")
    decode_done = time.perf_counter()

    if any("bubble_id" in item for item in translated_regions):
        detected_bubbles, bubble_cache_hit = _load_or_detect_bubbles(
            image, image_bytes, bubble_cache_dir
        )
    else:
        detected_bubbles, bubble_cache_hit = [], False
    detection_done = time.perf_counter()
    valid_regions = []
    unmatched_bubble_region_indices = []
    for region in translated_regions:
        text = str(region.get("translated", "")).strip()
        if not text:
            continue
        left = max(0, int(region.get("left", 0)))
        top = max(0, int(region.get("top", 0)))
        right = min(width, int(left + region.get("width", 0)))
        bottom = min(height, int(top + region.get("height", 0)))
        if right <= left or bottom <= top:
            continue
        bubble = _match_saved_bubble(region, detected_bubbles) if "bubble_id" in region else None
        if "bubble_id" in region and bubble is None:
            # Detector IDs can change between crop detection and rendering.
            # Retain the region when it has a text-sized OCR box and use that
            # narrow box as the repair boundary; never expand an unmatched
            # whole-balloon fallback into an erase operation.
            if region.get("detection_fallback") == "whole_bubble_crop":
                unmatched_bubble_region_indices.append(region.get("index"))
                continue
            if not region.get("ocr_boxes") and not region.get("ocr_polygons"):
                unmatched_bubble_region_indices.append(region.get("index"))
                continue
            region = dict(region)
            region.pop("bubble_id", None)
            region.pop("bubble_bbox", None)
            region.pop("bubble_confidence", None)
        if bubble is not None:
            left, top, right, bottom = _bubble_render_bounds(region, bubble)
        valid_regions.append((region, left, top, right, bottom, bubble))

    if not valid_regions:
        encoded_ok, encoded_page = cv2.imencode(".png", image)
        if not encoded_ok:
            raise ValueError("تعذر ترميز الصفحة بعد تعذر مطابقة مناطق الترجمة.")
        return encoded_page.tobytes(), {
            "rendered_regions": 0,
            "skipped_regions": len(translated_regions),
            "skipped_region_indices": unmatched_bubble_region_indices,
            "bubble_repair_strategies": {
                "interior_fill": 0, "bubble_overlay": 0, "text_inpainting": 0,
            },
            "quality_note": "لم تطابق مناطق الترجمة صناديق الفقاعات الحالية؛ أُبقيت الصفحة الأصلية دون تعديل.",
        }

    # Two nearby external OCR blocks can have overlapping axis-aligned boxes
    # (especially when lettering is tilted). Drawing both would stack Arabic
    # glyphs over each other. Leave every strongly conflicting source block
    # untouched and flag it for review instead of choosing a possibly wrong
    # caption automatically.
    external_regions = [item for item in valid_regions if item[5] is None]

    def overlap_ratio(first, second) -> float:
        first_region, ax1, ay1, ax2, ay2, _ = first
        second_region, bx1, by1, bx2, by2, _ = second
        box_intersection = max(0, min(ax2, bx2) - max(ax1, bx1)) * max(
            0, min(ay2, by2) - max(ay1, by1)
        )
        if not box_intersection:
            return 0.0
        first_polygons = first_region.get("ocr_polygons") or []
        second_polygons = second_region.get("ocr_polygons") or []
        try:
            first_contours = [np.asarray(poly, dtype=np.float32).reshape(-1, 1, 2)
                              for poly in first_polygons]
            second_contours = [np.asarray(poly, dtype=np.float32).reshape(-1, 1, 2)
                               for poly in second_polygons]
            first_area = sum(cv2.contourArea(poly) for poly in first_contours)
            second_area = sum(cv2.contourArea(poly) for poly in second_contours)
            if first_area <= 0 or second_area <= 0:
                raise ValueError("empty OCR polygon")
            intersection = 0.0
            for first_poly in first_contours:
                for second_poly in second_contours:
                    area, _ = cv2.intersectConvexConvex(first_poly, second_poly)
                    intersection += max(0.0, float(area))
            return intersection / min(first_area, second_area)
        except (cv2.error, TypeError, ValueError):
            first_area = max(1, (ax2 - ax1) * (ay2 - ay1))
            second_area = max(1, (bx2 - bx1) * (by2 - by1))
            return box_intersection / min(first_area, second_area)

    overlapping_external_ids = set()
    external_by_left = sorted(external_regions, key=lambda item: item[1])
    for first_index, first in enumerate(external_by_left):
        _first_region, ax1, ay1, ax2, ay2, _first_bubble = first
        for second in external_by_left[first_index + 1:]:
            _second_region, bx1, by1, bx2, by2, _second_bubble = second
            if bx1 >= ax2:
                break
            if by1 >= ay2 or by2 <= ay1:
                continue
            if overlap_ratio(first, second) >= 0.20:
                overlapping_external_ids.update((id(first[0]), id(second[0])))

    # Decide whether Arabic fits before removing any source lettering. This
    # also prevents one failed region from restoring an entire shared balloon
    # over Arabic that was already rendered for another region.
    font_path = _font_path()
    fitted_bubbles = {}
    skipped_bubble_ids = set()
    occupied_bubble_pixels = {}
    for region, left, top, right, bottom, bubble in valid_regions:
        if bubble is None:
            continue
        safe_mask = _bubble_mask_in_bounds(
            bubble, (left, top, right, bottom), region.get("manual_bubble_polygon")
        )
        layout_bounds = _text_layout_bounds(region, (left, top, right, bottom))
        lx1, ly1, lx2, ly2 = layout_bounds
        layout_mask = safe_mask[ly1 - top:ly2 - top, lx1 - left:lx2 - left]
        fitted = _bubble_glyphs(str(region["translated"]).strip(), layout_mask, layout_bounds, font_path)
        if fitted is None:
            skipped_bubble_ids.add(id(region))
            continue
        glyphs, _font_size = fitted
        bubble_key = id(bubble)
        occupied = occupied_bubble_pixels.setdefault(bubble_key, np.zeros_like(bubble.safe_mask))
        bx1, by1, _bx2, _by2 = bubble.bbox
        candidate = occupied[ly1 - by1:ly2 - by1, lx1 - bx1:lx2 - bx1]
        if np.any((glyphs > 0) & (candidate > 0)):
            skipped_bubble_ids.add(id(region))
            continue
        candidate[glyphs > 0] = 255
        fitted_bubbles[id(region)] = (layout_bounds, glyphs, _font_size)
    layout_done = time.perf_counter()

    mask = np.zeros((height, width), dtype=np.uint8)
    bubble_erasures = {}
    region_erase_masks = {}
    for region, left, top, right, bottom, bubble in valid_regions:
        if id(region) in overlapping_external_ids or id(region) in skipped_bubble_ids:
            continue
        if bubble is not None and region.get("ocr_boxes"):
            erase = _build_ink_mask(image, region, (left, top, right, bottom))
            safe_mask = _bubble_mask_in_bounds(
                bubble, (left, top, right, bottom), region.get("manual_bubble_polygon")
            )
            erase = cv2.bitwise_and(erase, safe_mask)
            bubble_key = id(bubble)
            if bubble_key in bubble_erasures:
                bubble_entry = bubble_erasures[bubble_key]
            else:
                bubble_entry = [
                    np.zeros_like(bubble.safe_mask),
                    _bubble_safe_mask(bubble, region.get("manual_bubble_polygon")),
                    bubble.bbox,
                ]
                bubble_erasures[bubble_key] = bubble_entry
            bx1, by1, _bx2, _by2 = bubble_entry[2]
            local_left, local_top = left - bx1, top - by1
            local_erase = bubble_entry[0][
                local_top:local_top + erase.shape[0], local_left:local_left + erase.shape[1]
            ]
            local_erase[:] = cv2.bitwise_or(local_erase, erase)
        elif bubble is None:
            erase = _build_ink_mask(image, region, (left, top, right, bottom))
            region_erase_masks[region.get("index")] = (erase.copy(), (left, top, right, bottom))
            mask[top:bottom, left:right] = cv2.bitwise_or(mask[top:bottom, left:right], erase)
    for erase, _safe_mask, (left, top, right, bottom) in bubble_erasures.values():
        mask[top:bottom, left:right] = cv2.bitwise_or(mask[top:bottom, left:right], erase)
    masks_done = time.perf_counter()
    repaired = cv2.inpaint(image, mask, 3, cv2.INPAINT_TELEA) if np.any(mask) else image.copy()
    # For captions on demonstrably flat backgrounds, sample only the quiet ring
    # around the OCR-confirmed box. This removes tiny inpaint fringes without
    # changing pixels outside that exact text box or flattening busy artwork.
    for region, left, top, right, bottom, bubble in valid_regions:
        if bubble is not None:
            continue
        forbidden = [
            (other[1], other[2], other[3], other[4])
            for other in valid_regions if other[0] is not region
        ]
        color = _flat_external_background(image, (left, top, right, bottom), forbidden)
        if color is not None:
            repaired[top:bottom, left:right] = color
    inpaint_done = time.perf_counter()
    # A webtoon speech balloon is usually a flat pale fill. Where the interior is
    # pale, replace the OCR ink with its sampled fill color directly; Telea can
    # otherwise pull black lettering into a gray smear along large OCR boxes.
    bubble_strategies = {"interior_fill": 0, "bubble_overlay": 0, "text_inpainting": 0}
    for erase, safe_mask, bounds in bubble_erasures.values():
        if not np.any(erase):
            continue
        strategy = _repair_bubble_interior(repaired, image, erase, safe_mask, bounds)
        bubble_strategies[strategy] += 1
    repair_done = time.perf_counter()
    canvas = Image.fromarray(cv2.cvtColor(repaired, cv2.COLOR_BGR2RGB))
    original_canvas = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
    text_overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0)) if include_layers else None
    rendered = 0
    skipped = 0
    skipped_region_indices = unmatched_bubble_region_indices + [
        region.get("index") for region, *_rest in valid_regions
        if id(region) in overlapping_external_ids or id(region) in skipped_bubble_ids
    ]
    skipped = len(skipped_region_indices)
    rendered_region_indices = []
    bubble_font_sizes = []

    for region, left, top, right, bottom, bubble in valid_regions:
        if id(region) in overlapping_external_ids or id(region) in skipped_bubble_ids:
            continue
        text = str(region["translated"]).strip()
        if bubble is not None:
            layout_bounds, glyphs, font_size = fitted_bubbles[id(region)]
            layout_left, layout_top, layout_right, layout_bottom = layout_bounds
            bubble_font_sizes.append(font_size)
            glyph_layer = Image.new(
                "RGBA", (layout_right - layout_left, layout_bottom - layout_top),
                (*_bubble_text_color(repaired, bubble), 0),
            )
            if text_overlay is not None:
                glyph_layer.putalpha(Image.fromarray(glyphs))
                text_overlay.alpha_composite(glyph_layer, (layout_left, layout_top))
            else:
                canvas.paste(glyph_layer.convert("RGB"), (layout_left, layout_top), Image.fromarray(glyphs))
            rendered += 1
            rendered_region_indices.append(region.get("index"))
            continue
        text_target = text_overlay if text_overlay is not None else canvas
        source_bounds = (left, top, right, bottom)
        forbidden = [
            (other[1], other[2], other[3], other[4])
            for other in valid_regions if other[0] is not region
        ]
        manual_bounds = _safe_manual_external_bounds(
            image, source_bounds, region.get("manual_external_layout_bbox"), forbidden
        )
        draw_bounds = manual_bounds or source_bounds
        placed = _draw_external_text(text_target, text, region, draw_bounds, image)
        expanded_bounds = None
        if not placed:
            expanded_bounds = _expanded_external_bounds(image, source_bounds, forbidden)
            if expanded_bounds is not None and expanded_bounds != draw_bounds:
                placed = _draw_external_text(text_target, text, region, expanded_bounds, image)
        if not placed:
            skipped += 1
            skipped_region_indices.append(region.get("index"))
            erase, erase_bounds = region_erase_masks.get(region.get("index"), (None, None))
            if erase is not None and np.any(erase):
                # Retain the original lettering if no safe layout area fits.
                erase_left, erase_top, erase_right, erase_bottom = erase_bounds
                original_crop = original_canvas.crop(erase_bounds)
                canvas.paste(original_crop, (erase_left, erase_top), Image.fromarray(erase))
            continue
        rendered += 1
        rendered_region_indices.append(region.get("index"))

    if text_overlay is not None:
        canvas = Image.alpha_composite(canvas.convert("RGBA"), text_overlay).convert("RGB")
    typeset_done = time.perf_counter()
    output = io.BytesIO()
    canvas.save(output, format="PNG", optimize=False, compress_level=1)
    encode_done = time.perf_counter()
    quality = {
        "rendered_regions": rendered,
        "skipped_regions": skipped,
        "rendered_region_indices": rendered_region_indices,
        "skipped_region_indices": skipped_region_indices,
        "uniform_font_size_per_bubble": True,
        "bubble_font_sizes": bubble_font_sizes,
        "bubble_repair_strategies": bubble_strategies,
        "unmatched_bubble_region_indices": unmatched_bubble_region_indices,
        "bubble_cache_hit": bubble_cache_hit,
        "render_timing_seconds": {
            "image_decode": round(decode_done - started, 4),
            "bubble_detection_or_cache_load": round(detection_done - decode_done, 4),
            "image_decode_and_bubble_detection": round(detection_done - started, 4),
            "arabic_layout_preflight": round(layout_done - detection_done, 4),
            "erase_mask_construction": round(masks_done - layout_done, 4),
            "text_inpainting": round(inpaint_done - masks_done, 4),
            "bubble_fill_or_overlay": round(repair_done - inpaint_done, 4),
            "arabic_composition": round(typeset_done - repair_done, 4),
            "png_encoding": round(encode_done - typeset_done, 4),
            "total": round(encode_done - started, 4),
        },
        "overlapping_external_regions_skipped": len(overlapping_external_ids),
        "external_text_style_estimated": "foreground/outline colors, font weight, and OCR angle",
        "method": "speech_bubble_onnx+outside_bubble_ocr+contrast_mask_inpaint+style_aware_arabic_typesetting",
        "review_required": True,
    }
    if not include_layers:
        return output.getvalue(), quality

    def encode_png(layer: Image.Image) -> bytes:
        stream = io.BytesIO()
        layer.save(stream, format="PNG", optimize=False, compress_level=1)
        return stream.getvalue()

    return output.getvalue(), quality, {
        "original.png": encode_png(original_canvas),
        "text_removal_mask.png": encode_png(Image.fromarray(mask, mode="L")),
        "repaired_underlay.png": encode_png(Image.fromarray(cv2.cvtColor(repaired, cv2.COLOR_BGR2RGB))),
        "translated_text_overlay.png": encode_png(text_overlay),
    }
