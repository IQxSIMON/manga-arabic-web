import io
import tempfile

import cv2
import numpy as np
from PIL import Image, ImageDraw

import image_pipeline.renderer as renderer
from image_pipeline.renderer import render_translated_page
from ocr_engine.speech_bubbles import SpeechBubble


def test_inpaints_and_typesets_arabic_in_detected_region():
    source = Image.new("RGB", (420, 120), "white")
    draw = ImageDraw.Draw(source)
    draw.text((70, 42), "HELLO", fill="black")
    encoded = io.BytesIO()
    source.save(encoded, format="PNG")
    before = encoded.getvalue()

    rendered, report = render_translated_page(before, [{
        "left": 65, "top": 35, "width": 150, "height": 45, "translated": "مرحباً بك",
    }])
    with Image.open(io.BytesIO(rendered)) as result:
        assert result.size == source.size
        assert result.format == "PNG"
    assert rendered != before
    assert report["rendered_regions"] == 1
    assert report["skipped_regions"] == 0
    assert report["review_required"] is True


def test_external_ink_mask_keeps_white_caption_strokes_on_dark_art_without_polygon():
    image = np.zeros((130, 360, 3), dtype=np.uint8)
    for y in range(image.shape[0]):
        image[y, :] = (24 + y // 8, 30 + y // 7, 48 + y // 6)
    cv2.putText(image, "HOW TO FEEL PAIN", (36, 78), cv2.FONT_HERSHEY_DUPLEX,
                1.2, (248, 248, 248), 3, cv2.LINE_AA)

    ink = renderer._build_ink_mask(
        image, {"ocr_boxes": [[28, 38, 300, 58]]}, (28, 38, 328, 96)
    )

    assert 100 < int(np.count_nonzero(ink)) < int(ink.size * 0.70)
    assert ink[0, 0] == 0


def test_external_style_keeps_white_fill_on_dark_tilted_caption_panel():
    image = np.full((180, 380, 3), (220, 220, 220), dtype=np.uint8)
    panel = np.array([[35, 22], [365, 78], [340, 158], [20, 102]], dtype=np.int32)
    cv2.fillConvexPoly(image, panel, (68, 64, 68))
    cv2.putText(image, "FORMIDABLE", (72, 106), cv2.FONT_HERSHEY_DUPLEX,
                1.15, (5, 5, 5), 5, cv2.LINE_AA)
    cv2.putText(image, "FORMIDABLE", (72, 106), cv2.FONT_HERSHEY_DUPLEX,
                1.15, (248, 248, 248), 2, cv2.LINE_AA)
    region = {
        "ocr_boxes": [[64, 62, 270, 52]],
        "ocr_polygons": [[[72, 64], [326, 64], [326, 116], [72, 116]]],
    }

    style = renderer._external_text_style(image, region)

    assert min(style["fill"]) >= 180


def test_bubble_render_uses_one_font_and_preserves_pixels_outside_bubble(monkeypatch):
    source = Image.new("RGB", (360, 180), "#d8e2ef")
    draw = ImageDraw.Draw(source)
    draw.ellipse((65, 18, 295, 160), fill="white", outline="black", width=3)
    draw.text((135, 72), "HELLO", fill="black")
    bgr = cv2.cvtColor(np.asarray(source), cv2.COLOR_RGB2BGR)
    mask = np.zeros((180, 360), dtype=np.uint8)
    cv2.ellipse(mask, (180, 89), (102, 62), 0, 0, 360, 255, -1)
    safe = cv2.erode(mask, np.ones((9, 9), dtype=np.uint8))
    bubble = SpeechBubble((65, 18, 296, 161), mask, safe, 0.98)
    monkeypatch.setattr(renderer, "detect_speech_bubbles", lambda _image: [bubble])
    encoded = io.BytesIO()
    source.save(encoded, format="PNG")

    rendered, report = render_translated_page(encoded.getvalue(), [{
        "bubble_id": 1, "bubble_bbox": [65, 18, 296, 161],
        "ocr_boxes": [[135, 72, 52, 12]], "translated": "مرحباً بك في العمل",
        "left": 65, "top": 18, "width": 231, "height": 143,
    }])
    with Image.open(io.BytesIO(rendered)) as result:
        after = np.asarray(result.convert("RGB"))
    before = np.asarray(source)
    outside = safe == 0
    assert np.array_equal(after[outside], before[outside])
    assert report["rendered_regions"] == 1
    assert report["uniform_font_size_per_bubble"] is True
    assert len(report["bubble_font_sizes"]) == 1


def test_manual_bubble_polygon_clips_all_rendering_to_user_boundary(monkeypatch):
    source = Image.new("RGB", (360, 180), "#d8e2ef")
    draw = ImageDraw.Draw(source)
    draw.ellipse((65, 18, 295, 160), fill="white", outline="black", width=3)
    draw.text((135, 72), "HELLO", fill="black")
    mask = np.zeros((180, 360), dtype=np.uint8)
    cv2.ellipse(mask, (180, 89), (102, 62), 0, 0, 360, 255, -1)
    safe = cv2.erode(mask, np.ones((9, 9), dtype=np.uint8))
    bubble = SpeechBubble((65, 18, 296, 161), mask, safe, 0.98)
    monkeypatch.setattr(renderer, "detect_speech_bubbles", lambda _image: [bubble])
    polygon = [[105, 38], [255, 38], [255, 142], [105, 142]]
    encoded = io.BytesIO()
    source.save(encoded, format="PNG")

    rendered, report = render_translated_page(encoded.getvalue(), [{
        "index": 1, "bubble_id": 1, "bubble_bbox": [65, 18, 296, 161],
        "ocr_boxes": [[135, 72, 52, 12]], "translated": "أهلاً",
        "left": 65, "top": 18, "width": 231, "height": 143,
        "manual_bubble_polygon": polygon,
    }])
    with Image.open(io.BytesIO(rendered)) as result:
        after = np.asarray(result.convert("RGB"))
    before = np.asarray(source)
    polygon_mask = np.zeros((180, 360), dtype=np.uint8)
    cv2.fillPoly(polygon_mask, [np.asarray(polygon, dtype=np.int32)], 255)

    assert report["rendered_regions"] == 1
    assert np.array_equal(after[polygon_mask == 0], before[polygon_mask == 0])


def test_dark_bubble_gets_light_arabic_ink(monkeypatch):
    source = Image.new("RGB", (360, 180), "#d8e2ef")
    draw = ImageDraw.Draw(source)
    draw.ellipse((65, 18, 295, 160), fill="black", outline="white", width=3)
    draw.text((132, 76), "HELLO", fill="white")
    bgr = cv2.cvtColor(np.asarray(source), cv2.COLOR_RGB2BGR)
    mask = np.zeros((180, 360), dtype=np.uint8)
    cv2.ellipse(mask, (180, 89), (102, 62), 0, 0, 360, 255, -1)
    safe = cv2.erode(mask, np.ones((9, 9), dtype=np.uint8))
    bubble = SpeechBubble((65, 18, 296, 161), mask, safe, 0.98)
    monkeypatch.setattr(renderer, "detect_speech_bubbles", lambda _image: [bubble])
    encoded = io.BytesIO()
    source.save(encoded, format="PNG")

    rendered, report = render_translated_page(encoded.getvalue(), [{
        "bubble_id": 1, "bubble_bbox": [65, 18, 296, 161],
        "ocr_boxes": [[132, 76, 55, 18]], "translated": "أهلاً بك هنا",
        "left": 65, "top": 18, "width": 231, "height": 143,
        "region_type": "speech_bubble",
    }])
    with Image.open(io.BytesIO(rendered)) as result:
        after = np.asarray(result.convert("RGB"))

    assert renderer._bubble_text_color(bgr, bubble) == (255, 255, 255)
    assert report["rendered_regions"] == 1
    assert len(report["bubble_font_sizes"]) == 1
    # Arabic glyph strokes must contrast against the black balloon interior.
    interior = (safe > 0)
    assert np.count_nonzero(np.all(after[interior] >= 225, axis=1)) > 15


def test_typesetting_uses_dialogue_block_above_art_inside_the_bubble():
    layout = renderer._text_layout_bounds(
        {"ocr_boxes": [[193, 227, 65, 13], [193, 238, 69, 12], [194, 248, 64, 12], [195, 258, 62, 12]]},
        (194, 198, 261, 353),
    )
    assert layout[1] < 227
    assert layout[3] > 270
    assert layout[3] < 290  # leaves the character drawing lower in the balloon intact


def test_manual_arabic_layout_bounds_are_used_only_inside_bubble():
    region = {"ocr_boxes": [[120, 60, 70, 20]],
              "manual_layout_bbox": [100, 40, 220, 110]}
    assert renderer._text_layout_bounds(region, (60, 20, 300, 160)) == (100, 40, 220, 110)
    region["manual_layout_bbox"] = [0, 0, 220, 110]
    assert renderer._text_layout_bounds(region, (60, 20, 300, 160)) != (0, 0, 220, 110)


def test_saved_bubble_geometry_survives_detector_reordering():
    mask = np.full((30, 40), 255, dtype=np.uint8)
    first = SpeechBubble((10, 10, 50, 40), mask, mask, 0.9)
    second = SpeechBubble((100, 10, 140, 40), mask, mask, 0.9)
    matched = renderer._match_saved_bubble(
        {"bubble_id": 1, "bubble_bbox": [100, 10, 140, 40]}, [first, second]
    )
    assert matched is second
    assert renderer._match_saved_bubble(
        {"bubble_id": 1, "bubble_bbox": [200, 10, 240, 40]}, [first, second]
    ) is None


def test_merged_detector_mask_keeps_two_saved_dialogue_regions(monkeypatch):
    source = Image.new("RGB", (320, 220), "white")
    draw = ImageDraw.Draw(source)
    draw.text((105, 55), "FIRST", fill="black")
    draw.text((105, 145), "SECOND", fill="black")
    mask = np.full((200, 300), 255, dtype=np.uint8)
    bubble = SpeechBubble((10, 10, 310, 210), mask, 0.95)
    monkeypatch.setattr(renderer, "detect_speech_bubbles", lambda _image: [bubble])
    encoded = io.BytesIO()
    source.save(encoded, format="PNG")
    regions = [
        {"index": 1, "bubble_id": 1, "bubble_bbox": [20, 20, 300, 110],
         "left": 20, "top": 20, "width": 280, "height": 90,
         "ocr_boxes": [[105, 55, 60, 14]], "translated": "العبارة الأولى"},
        {"index": 2, "bubble_id": 2, "bubble_bbox": [20, 110, 300, 200],
         "left": 20, "top": 110, "width": 280, "height": 90,
         "ocr_boxes": [[105, 145, 80, 14]], "translated": "العبارة الثانية"},
    ]
    _rendered, report = render_translated_page(encoded.getvalue(), regions)
    assert report["rendered_region_indices"] == [1, 2]
    assert report["skipped_region_indices"] == []


def test_unmatched_saved_bubble_is_reported_as_skipped(monkeypatch):
    source = Image.new("RGB", (320, 120), "white")
    mask = np.full((60, 100), 255, dtype=np.uint8)
    bubble = SpeechBubble((20, 20, 120, 80), mask, 0.9)
    monkeypatch.setattr(renderer, "detect_speech_bubbles", lambda _image: [bubble])
    stream = io.BytesIO()
    source.save(stream, format="PNG")
    regions = [
        {"index": 1, "bubble_id": 1, "bubble_bbox": [20, 20, 120, 80],
         "left": 20, "top": 20, "width": 100, "height": 60,
         "ocr_boxes": [[45, 40, 40, 12]], "translated": "الأول"},
        {"index": 2, "bubble_id": 2, "bubble_bbox": [200, 20, 300, 80],
         "left": 200, "top": 20, "width": 100, "height": 60,
         "ocr_boxes": [[225, 40, 40, 12]], "translated": "الثاني"},
    ]
    _rendered, report = render_translated_page(stream.getvalue(), regions)
    assert report["unmatched_bubble_region_indices"] == [2]
    assert report["skipped_region_indices"] == [2]


def test_unfittable_bubble_keeps_original_ink_and_art(monkeypatch):
    source = Image.new("RGB", (360, 180), "#d8e2ef")
    draw = ImageDraw.Draw(source)
    draw.ellipse((65, 18, 295, 160), fill="white", outline="black", width=3)
    draw.text((135, 72), "HELLO", fill="black")
    draw.rectangle((170, 125, 190, 145), fill="red")
    mask = np.zeros((180, 360), dtype=np.uint8)
    cv2.ellipse(mask, (180, 89), (102, 62), 0, 0, 360, 255, -1)
    safe = cv2.erode(mask, np.ones((9, 9), dtype=np.uint8))
    bubble = SpeechBubble((65, 18, 296, 161), mask, safe, 0.98)
    monkeypatch.setattr(renderer, "detect_speech_bubbles", lambda _image: [bubble])
    monkeypatch.setattr(renderer, "_bubble_glyphs", lambda *_args: None)
    encoded = io.BytesIO()
    source.save(encoded, format="PNG")
    rendered, report = render_translated_page(encoded.getvalue(), [{
        "index": 1, "bubble_id": 1, "bubble_bbox": [65, 18, 296, 161],
        "ocr_boxes": [[135, 72, 52, 12]], "translated": "مرحباً بك",
        "left": 65, "top": 18, "width": 231, "height": 143,
    }])
    with Image.open(io.BytesIO(rendered)) as result:
        assert np.array_equal(np.asarray(result.convert("RGB")), np.asarray(source))
    assert report["rendered_regions"] == 0
    assert report["skipped_region_indices"] == [1]


def test_gradient_bubble_uses_local_overlay_without_covering_outside_art(monkeypatch):
    array = np.full((180, 360, 3), (25, 80, 125), dtype=np.uint8)
    mask = np.zeros((180, 360), dtype=np.uint8)
    cv2.ellipse(mask, (180, 89), (102, 62), 0, 0, 360, 255, -1)
    for x in range(array.shape[1]):
        shade = min(230, 170 + x // 7)
        array[:, x][mask[:, x] > 0] = (shade, shade, shade)
    cv2.ellipse(array, (180, 89), (102, 62), 0, 0, 360, (15, 15, 15), 3)
    cv2.putText(array, "HELLO", (130, 88), cv2.FONT_HERSHEY_SIMPLEX,
                0.8, (0, 0, 0), 2, cv2.LINE_AA)
    safe = cv2.erode(mask, np.ones((9, 9), dtype=np.uint8))
    bubble = SpeechBubble((65, 18, 296, 161), mask, safe, 0.95)
    monkeypatch.setattr(renderer, "detect_speech_bubbles", lambda _image: [bubble])
    source = Image.fromarray(cv2.cvtColor(array, cv2.COLOR_BGR2RGB))
    encoded = io.BytesIO()
    source.save(encoded, format="PNG")

    rendered, report = render_translated_page(encoded.getvalue(), [{
        "index": 1, "bubble_id": 1, "bubble_bbox": [65, 18, 296, 161],
        "ocr_boxes": [[128, 66, 110, 30]],
        "ocr_polygons": [[[128, 66], [238, 66], [238, 96], [128, 96]]],
        "translated": "مرحباً بك", "left": 65, "top": 18,
        "width": 231, "height": 143,
    }])
    after = np.asarray(Image.open(io.BytesIO(rendered)).convert("RGB"))
    before = np.asarray(source)
    assert np.array_equal(after[safe == 0], before[safe == 0])
    assert report["rendered_regions"] == 1
    assert report["bubble_repair_strategies"]["bubble_overlay"] == 1


def test_optional_bubble_layers_recompose_to_the_existing_render(monkeypatch):
    source = Image.new("RGB", (360, 180), "#d8e2ef")
    draw = ImageDraw.Draw(source)
    draw.ellipse((65, 18, 295, 160), fill="white", outline="black", width=3)
    draw.text((135, 72), "HELLO", fill="black")
    mask = np.zeros((180, 360), dtype=np.uint8)
    cv2.ellipse(mask, (180, 89), (102, 62), 0, 0, 360, 255, -1)
    safe = cv2.erode(mask, np.ones((9, 9), dtype=np.uint8))
    bubble = SpeechBubble((65, 18, 296, 161), mask, safe, 0.98)
    monkeypatch.setattr(renderer, "detect_speech_bubbles", lambda _image: [bubble])
    encoded = io.BytesIO()
    source.save(encoded, format="PNG")
    region = {
        "index": 1, "bubble_id": 1, "bubble_bbox": [65, 18, 296, 161],
        "ocr_boxes": [[135, 72, 52, 12]], "translated": "\u0645\u0631\u062d\u0628\u0627",
        "left": 65, "top": 18, "width": 231, "height": 143,
    }
    ordinary, _ordinary_report = render_translated_page(encoded.getvalue(), [region])
    layered, report, layers = render_translated_page(
        encoded.getvalue(), [region], include_layers=True
    )

    underlay = Image.open(io.BytesIO(layers["repaired_underlay.png"])).convert("RGBA")
    overlay = Image.open(io.BytesIO(layers["translated_text_overlay.png"]))
    recomposed = Image.alpha_composite(underlay, overlay).convert("RGB")
    with Image.open(io.BytesIO(ordinary)) as ordinary_image:
        expected = np.asarray(ordinary_image.convert("RGB"))
    with Image.open(io.BytesIO(layered)) as layered_image:
        actual = np.asarray(layered_image.convert("RGB"))

    assert set(layers) == {
        "original.png", "text_removal_mask.png", "repaired_underlay.png",
        "translated_text_overlay.png",
    }
    assert np.array_equal(actual, expected)
    assert np.array_equal(np.asarray(recomposed), expected)
    assert np.any(np.asarray(Image.open(io.BytesIO(layers["text_removal_mask.png"]))) > 0)
    assert np.any(np.asarray(overlay.getchannel("A")) > 0)
    assert report["rendered_regions"] == 1


def test_optional_external_text_overlay_recomposes_to_the_existing_render():
    source = Image.new("RGB", (360, 120), "white")
    ImageDraw.Draw(source).text((80, 42), "OUTSIDE", fill="black")
    encoded = io.BytesIO()
    source.save(encoded, format="PNG")
    region = {
        "index": 1, "region_type": "external_text", "left": 45, "top": 25,
        "width": 260, "height": 65, "ocr_boxes": [[75, 38, 180, 42]],
        "translated": "\u0627\u0644\u0646\u0635 \u062e\u0627\u0631\u062c \u0627\u0644\u0641\u0642\u0627\u0639\u0629",
    }
    ordinary, ordinary_report = render_translated_page(encoded.getvalue(), [region])
    layered, report, layers = render_translated_page(
        encoded.getvalue(), [region], include_layers=True
    )

    underlay = Image.open(io.BytesIO(layers["repaired_underlay.png"])).convert("RGBA")
    overlay = Image.open(io.BytesIO(layers["translated_text_overlay.png"]))
    recomposed = Image.alpha_composite(underlay, overlay).convert("RGB")
    with Image.open(io.BytesIO(ordinary)) as ordinary_image:
        expected = np.asarray(ordinary_image.convert("RGB"))
    with Image.open(io.BytesIO(layered)) as layered_image:
        actual = np.asarray(layered_image.convert("RGB"))

    assert ordinary_report["rendered_regions"] == 1
    assert report["rendered_regions"] == 1
    assert np.array_equal(actual, expected)
    assert np.array_equal(np.asarray(recomposed), expected)
    assert np.any(np.asarray(overlay.getchannel("A")) > 0)


def test_external_polygon_mask_clears_full_confirmed_text_footprint():
    image = np.full((80, 240, 3), 238, dtype=np.uint8)
    cv2.putText(image, "THIN", (42, 48), cv2.FONT_HERSHEY_SIMPLEX,
                1.0, (120, 120, 120), 1, cv2.LINE_AA)
    polygon = [[38, 20], [112, 20], [112, 56], [38, 56]]
    mask = renderer._build_ink_mask(
        image, {"ocr_boxes": [[38, 20, 74, 36]], "ocr_polygons": [polygon]},
        (30, 15, 120, 65),
    )
    # The confirmed OCR footprint is deliberately cleared as a unit so thin
    # antialiased strokes cannot survive. Pixels outside it remain untouched.
    assert np.count_nonzero(mask) > 74 * 36 * 0.8
    assert mask[0, 0] == 0


def test_bubble_detection_disk_cache_roundtrip_skips_second_inference(monkeypatch):
    source = Image.new("RGB", (360, 180), "white")
    draw = ImageDraw.Draw(source)
    draw.ellipse((65, 18, 295, 160), fill="white", outline="black", width=3)
    draw.text((135, 72), "HELLO", fill="black")
    encoded = io.BytesIO()
    source.save(encoded, format="PNG")
    mask = np.zeros((180, 360), dtype=np.uint8)
    cv2.ellipse(mask, (180, 89), (102, 62), 0, 0, 360, 255, -1)
    safe = cv2.erode(mask, np.ones((9, 9), dtype=np.uint8))
    bubble = SpeechBubble((65, 18, 296, 161), mask, safe, 0.98)
    calls = []
    def detect(_image):
        calls.append(1)
        return [bubble]
    monkeypatch.setattr(renderer, "detect_speech_bubbles", detect)
    region = {
        "index": 1, "bubble_id": 1, "bubble_bbox": [65, 18, 296, 161],
        "left": 65, "top": 18, "width": 231, "height": 143,
        "ocr_boxes": [[135, 72, 52, 12]], "translated": "أهلاً بك",
    }

    with tempfile.TemporaryDirectory(
        dir=r"D:\MangaArabic\manga-arabic-web\ocr_test_results"
    ) as cache_dir:
        _cold, cold_report = render_translated_page(
            encoded.getvalue(), [region], bubble_cache_dir=cache_dir,
        )
        _cached, cached_report = render_translated_page(
            encoded.getvalue(), [region], bubble_cache_dir=cache_dir,
        )

    assert cold_report["bubble_cache_hit"] is False
    assert cached_report["bubble_cache_hit"] is True
    assert calls == [1]
    assert cold_report["rendered_regions"] == cached_report["rendered_regions"] == 1


def test_external_expansion_only_uses_visually_quiet_area():
    quiet = np.full((100, 160, 3), 245, dtype=np.uint8)
    assert renderer._expanded_external_bounds(quiet, (50, 45, 110, 55)) is not None
    busy = np.zeros((100, 160, 3), dtype=np.uint8)
    for x in range(0, 160, 2):
        busy[:, x:x + 1] = (255, 255, 255)
    assert renderer._expanded_external_bounds(busy, (50, 45, 110, 55)) is None


def test_wide_horizontal_ocr_footprint_overrides_false_vertical_angle():
    image = np.full((100, 260, 3), 245, dtype=np.uint8)
    region = {
        "left": 20, "top": 40, "width": 210, "height": 24,
        "ocr_boxes": [[20, 40, 210, 24]],
        "translated": "محطم القلوب #09",
        "angle": 90.0,
    }
    canvas = Image.new("RGB", (260, 100), "white")
    assert renderer._draw_external_text(
        canvas, region["translated"], region, (20, 40, 230, 64), image,
    )


def test_flat_external_background_uses_quiet_ring_and_rejects_busy_art():
    flat = np.full((80, 140, 3), (40, 220, 250), dtype=np.uint8)
    cv2.putText(flat, "TEXT", (28, 46), cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                (0, 0, 0), 2, cv2.LINE_AA)
    color = renderer._flat_external_background(flat, (20, 24, 100, 56))
    assert color == (40, 220, 250)

    busy = np.random.default_rng(0).integers(0, 256, flat.shape, dtype=np.uint8)
    assert renderer._flat_external_background(busy, (20, 24, 100, 56)) is None
