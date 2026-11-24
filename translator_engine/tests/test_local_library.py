import io
import json
import os
import time
import zipfile

from PIL import Image

import app as web_app


def test_region_ids_follow_geometry_when_ocr_order_changes():
    previous = [
        {"region_id": "reg_top", "region_type": "speech_bubble", "text": "HELLO",
         "left": 10, "top": 10, "width": 90, "height": 50,
         "translated": "العبارة الأولى", "review_status": "human_reviewed"},
        {"region_id": "reg_bottom", "region_type": "speech_bubble", "text": "HELLO",
         "left": 10, "top": 110, "width": 90, "height": 50,
         "translated": "العبارة الثانية", "review_status": "human_reviewed"},
    ]
    detected = [
        {"region_type": "speech_bubble", "text": "HELLO",
         "left": 12, "top": 112, "width": 89, "height": 49, "translated": ""},
        {"region_type": "speech_bubble", "text": "HELLO",
         "left": 12, "top": 12, "width": 89, "height": 49, "translated": ""},
    ]
    assert web_app._assign_stable_region_ids(previous, detected)
    web_app._preserve_exact_human_edits(previous, detected)
    assert [(item["region_id"], item["translated"]) for item in detected] == [
        ("reg_bottom", "العبارة الثانية"), ("reg_top", "العبارة الأولى")]
    assert not web_app._assign_stable_region_ids(previous, detected)


def test_reviewed_ocr_text_survives_refresh_with_same_geometry():
    previous = [{"region_id": "reg_reviewed", "region_type": "speech_bubble",
                 "text": "CORRECTED SOURCE", "translated": "ترجمة مصححة",
                 "review_status": "human_reviewed", "left": 10, "top": 10,
                 "width": 100, "height": 60}]
    detected = [{"region_type": "speech_bubble", "text": "BAD OCR SOURCE",
                 "translated": "", "left": 11, "top": 11,
                 "width": 100, "height": 60}]
    web_app._assign_stable_region_ids(previous, detected)
    web_app._preserve_exact_human_edits(previous, detected)
    assert detected[0]["region_id"] == "reg_reviewed"
    assert detected[0]["text"] == "CORRECTED SOURCE"
    assert detected[0]["translated"] == "ترجمة مصححة"


def test_review_saves_manual_arabic_layout_without_retranslation(monkeypatch, tmp_path):
    monkeypatch.setenv("MANGA_LIBRARY_DIR", str(tmp_path / "library"))
    client = web_app.app.test_client()
    imported = client.post("/api/library/import", data={
        "title": "Layout sample", "reading_layout": "vertical",
        "pages": (io.BytesIO(_png_bytes()), "page1.png"),
    }, content_type="multipart/form-data")
    assert imported.status_code == 201
    chapter_id = imported.json["chapter"]["id"]
    web_app.library_store.save_page_result(chapter_id, 1, {
        "processing_status": "complete", "translated_lines": [{
            "index": 1, "region_id": "reg_test", "bubble_id": 1,
            "bubble_bbox": [20, 10, 220, 100],
            "left": 20, "top": 10, "width": 200, "height": 90,
            "region_type": "speech_bubble", "text": "HELLO", "translated": "مرحباً",
        }],
    })
    edit = {"regions": [{"index": 1, "source": "HELLO", "translated": "مرحباً",
                          "include": True, "layout_bbox": [40, 20, 130, 60]}]}
    response = client.post(f"/api/library/{chapter_id}/pages/1/review", json=edit)
    assert response.status_code == 200
    saved = web_app.library_store.read_page_result(chapter_id, 1)
    assert saved["translated_lines"][0]["manual_layout_bbox"] == [40, 20, 170, 80]
    assert saved["render_stale"] is True
    edit["regions"][0]["layout_bbox"] = [10, 20, 130, 60]
    invalid = client.post(f"/api/library/{chapter_id}/pages/1/review", json=edit)
    assert invalid.status_code == 400


def test_manual_layout_survives_ocr_refresh_with_stable_region_id():
    previous = [{"region_id": "reg_layout", "region_type": "speech_bubble",
                 "text": "OLD OCR", "translated": "ترجمة", "left": 20, "top": 20,
                 "width": 180, "height": 90, "bubble_bbox": [20, 20, 200, 110],
                 "manual_layout_bbox": [40, 30, 170, 90],
                 "manual_bubble_polygon": [[30, 30], [190, 30], [190, 100], [30, 100]]}]
    detected = [{"region_type": "speech_bubble", "text": "CORRECT OCR",
                 "translated": "", "left": 22, "top": 20,
                 "width": 178, "height": 90, "bubble_bbox": [22, 20, 200, 110]}]
    web_app._assign_stable_region_ids(previous, detected)
    web_app._preserve_exact_human_edits(previous, detected)
    assert detected[0]["region_id"] == "reg_layout"
    assert detected[0]["manual_layout_bbox"] == [40, 30, 170, 90]
    assert detected[0]["manual_bubble_polygon"] == previous[0]["manual_bubble_polygon"]
    smaller = [{"region_id": "reg_layout", "region_type": "speech_bubble",
                "text": "CORRECT OCR", "left": 22, "top": 20,
                "width": 128, "height": 90, "bubble_bbox": [22, 20, 150, 110]}]
    web_app._preserve_exact_human_edits(previous, smaller)
    assert "manual_bubble_polygon" not in smaller[0]


def test_review_route_saves_manual_reading_order_and_marks_context_stale(monkeypatch, tmp_path):
    monkeypatch.setenv("MANGA_LIBRARY_DIR", str(tmp_path / "library"))
    client = web_app.app.test_client()
    imported = client.post("/api/library/import", data={
        "title": "Reading order sample", "reading_layout": "vertical",
        "pages": (io.BytesIO(_png_bytes()), "page1.png"),
    }, content_type="multipart/form-data")
    assert imported.status_code == 201
    chapter_id = imported.json["chapter"]["id"]
    web_app.library_store.save_page_result(chapter_id, 1, {
        "processing_status": "complete", "translated_lines": [
            {"index": 1, "region_id": "reg_first", "region_type": "speech_bubble",
             "text": "FIRST", "translated": "الأول", "left": 10, "top": 10,
             "width": 80, "height": 30},
            {"index": 2, "region_id": "reg_second", "region_type": "speech_bubble",
             "text": "SECOND", "translated": "الثاني", "left": 10, "top": 50,
             "width": 80, "height": 30},
        ],
    })
    regions = [
        {"index": 1, "source": "FIRST", "translated": "الأول", "include": True},
        {"index": 2, "source": "SECOND", "translated": "الثاني", "include": True},
    ]
    response = client.post(
        f"/api/library/{chapter_id}/pages/1/review",
        json={"regions": regions, "region_order": ["reg_second", "reg_first"]},
    )
    assert response.status_code == 200
    saved = web_app.library_store.read_page_result(chapter_id, 1)
    assert [item["region_id"] for item in saved["translated_lines"]] == ["reg_second", "reg_first"]
    assert [item["index"] for item in saved["translated_lines"]] == [1, 2]
    assert saved["translation_inputs_stale"] is True
    assert saved["translation_context_signature"] == "reading_order_changed"


def test_review_route_merges_adjacent_regions_from_same_bubble(monkeypatch, tmp_path):
    monkeypatch.setenv("MANGA_LIBRARY_DIR", str(tmp_path / "library"))
    client = web_app.app.test_client()
    imported = client.post("/api/library/import", data={
        "title": "Merge sample", "reading_layout": "vertical",
        "pages": (io.BytesIO(_png_bytes()), "page1.png"),
    }, content_type="multipart/form-data")
    assert imported.status_code == 201
    chapter_id = imported.json["chapter"]["id"]
    regions = [
        {"index": 1, "region_id": "reg_one", "bubble_id": 1,
         "bubble_bbox": [20, 10, 220, 100], "region_type": "speech_bubble",
         "text": "HELLO", "translated": "مرحباً", "left": 40, "top": 30,
         "width": 50, "height": 20, "ocr_boxes": [[40, 30, 50, 20]]},
        {"index": 2, "region_id": "reg_two", "bubble_id": 1,
         "bubble_bbox": [20, 10, 220, 100], "region_type": "speech_bubble",
         "text": "WORLD", "translated": "بالعالم", "left": 40, "top": 52,
         "width": 55, "height": 20, "ocr_boxes": [[40, 52, 55, 20]]},
    ]
    web_app.library_store.save_page_result(chapter_id, 1, {
        "processing_status": "complete", "translated_lines": regions,
    })
    payload = {"regions": [
        {"index": 1, "source": "HELLO", "translated": "مرحباً", "include": True},
        {"index": 2, "source": "WORLD", "translated": "بالعالم", "include": True},
    ], "region_order": ["reg_one", "reg_two"],
        "merge_regions": [["reg_one", "reg_two"]]}
    response = client.post(f"/api/library/{chapter_id}/pages/1/review", json=payload)
    assert response.status_code == 200
    saved = web_app.library_store.read_page_result(chapter_id, 1)
    assert len(saved["translated_lines"]) == 1
    merged = saved["translated_lines"][0]
    assert merged["text"] == "HELLO WORLD"
    assert merged["translated"] == ""
    assert merged["merge_parent_ids"] == ["reg_one", "reg_two"]
    assert saved["original"] == "HELLO WORLD"
    assert saved["translated"] == ""
    assert saved["processing_status"] == "translation_in_progress"
    assert saved["translation_inputs_stale"] is True


def test_review_route_rejects_merge_across_bubbles_without_saving(monkeypatch, tmp_path):
    monkeypatch.setenv("MANGA_LIBRARY_DIR", str(tmp_path / "library"))
    client = web_app.app.test_client()
    imported = client.post("/api/library/import", data={
        "title": "Separate bubbles sample", "reading_layout": "vertical",
        "pages": (io.BytesIO(_png_bytes()), "page1.png"),
    }, content_type="multipart/form-data")
    assert imported.status_code == 201
    chapter_id = imported.json["chapter"]["id"]
    regions = [
        {"index": 1, "region_id": "reg_left", "bubble_id": 1,
         "region_type": "speech_bubble", "text": "LEFT", "translated": "يسار"},
        {"index": 2, "region_id": "reg_right", "bubble_id": 2,
         "region_type": "speech_bubble", "text": "RIGHT", "translated": "يمين"},
    ]
    web_app.library_store.save_page_result(chapter_id, 1, {
        "processing_status": "complete", "original": "LEFT\nRIGHT",
        "translated": "يسار\nيمين", "translated_lines": regions,
    })

    response = client.post(
        f"/api/library/{chapter_id}/pages/1/review",
        json={"regions": [], "merge_regions": [["reg_left", "reg_right"]]},
    )

    assert response.status_code == 400
    saved = web_app.library_store.read_page_result(chapter_id, 1)
    assert [item["text"] for item in saved["translated_lines"]] == ["LEFT", "RIGHT"]
    assert saved["original"] == "LEFT\nRIGHT"


def test_review_route_splits_bubble_region_using_ocr_geometry(monkeypatch, tmp_path):
    monkeypatch.setenv("MANGA_LIBRARY_DIR", str(tmp_path / "library"))
    client = web_app.app.test_client()
    imported = client.post("/api/library/import", data={
        "title": "Split sample", "reading_layout": "vertical",
        "pages": (io.BytesIO(_png_bytes()), "page1.png"),
    }, content_type="multipart/form-data")
    assert imported.status_code == 201
    chapter_id = imported.json["chapter"]["id"]
    web_app.library_store.save_page_result(chapter_id, 1, {
        "processing_status": "complete", "original": "FIRST SECOND",
        "translated": "نص قديم", "translated_lines": [{
            "index": 1, "region_id": "reg_combined", "bubble_id": 1,
            "bubble_bbox": [20, 10, 220, 180], "region_type": "speech_bubble",
            "text": "FIRST SECOND", "translated": "نص قديم",
            "left": 40, "top": 20, "width": 20, "height": 105,
            "ocr_boxes": [[40, 20, 20, 10], [40, 35, 20, 10],
                          [40, 100, 20, 10], [40, 115, 20, 10]],
            "ocr_polygons": [
                [[40, 20], [60, 20], [60, 30], [40, 30]],
                [[40, 35], [60, 35], [60, 45], [40, 45]],
                [[40, 100], [60, 100], [60, 110], [40, 110]],
                [[40, 115], [60, 115], [60, 125], [40, 125]],
            ],
        }],
    })

    response = client.post(
        f"/api/library/{chapter_id}/pages/1/review",
        json={"regions": [], "split_regions": [{
            "region_id": "reg_combined", "first_text": "FIRST",
            "second_text": "SECOND", "axis": "y", "ratio": 50,
        }]},
    )

    assert response.status_code == 200
    saved = web_app.library_store.read_page_result(chapter_id, 1)
    first, second = saved["translated_lines"]
    assert [first["text"], second["text"]] == ["FIRST", "SECOND"]
    assert [first["index"], second["index"]] == [1, 2]
    assert first["region_id"] == "reg_combined"
    assert second["region_id"] != first["region_id"]
    assert second["split_parent_id"] == first["split_parent_id"] == "reg_combined"
    assert len(first["ocr_boxes"]) == len(second["ocr_boxes"]) == 2
    assert first["translated"] == second["translated"] == ""
    assert saved["original"] == "FIRST\nSECOND"
    assert saved["translated"] == ""
    assert saved["translation_inputs_stale"] is True
    assert saved["translation_context_signature"] == "manual_region_split"


def test_review_route_rejects_split_without_separable_ocr_boxes(monkeypatch, tmp_path):
    monkeypatch.setenv("MANGA_LIBRARY_DIR", str(tmp_path / "library"))
    client = web_app.app.test_client()
    imported = client.post("/api/library/import", data={
        "title": "Unsafe split sample", "reading_layout": "vertical",
        "pages": (io.BytesIO(_png_bytes()), "page1.png"),
    }, content_type="multipart/form-data")
    chapter_id = imported.json["chapter"]["id"]
    web_app.library_store.save_page_result(chapter_id, 1, {
        "processing_status": "complete", "translated_lines": [{
            "index": 1, "region_id": "reg_one_box", "bubble_id": 1,
            "region_type": "speech_bubble", "text": "ONE BLOCK",
            "translated": "كتلة واحدة", "ocr_boxes": [[40, 30, 50, 20]],
        }],
    })

    response = client.post(
        f"/api/library/{chapter_id}/pages/1/review",
        json={"regions": [], "split_regions": [{
            "region_id": "reg_one_box", "first_text": "ONE",
            "second_text": "BLOCK", "axis": "y", "ratio": 50,
        }]},
    )

    assert response.status_code == 400
    saved = web_app.library_store.read_page_result(chapter_id, 1)
    assert len(saved["translated_lines"]) == 1
    assert saved["translated_lines"][0]["text"] == "ONE BLOCK"


def test_review_route_splits_external_caption_without_bubble_id(monkeypatch, tmp_path):
    monkeypatch.setenv("MANGA_LIBRARY_DIR", str(tmp_path / "library"))
    client = web_app.app.test_client()
    imported = client.post("/api/library/import", data={
        "title": "Caption split sample", "reading_layout": "vertical",
        "pages": (io.BytesIO(_png_bytes()), "page1.png"),
    }, content_type="multipart/form-data")
    chapter_id = imported.json["chapter"]["id"]
    web_app.library_store.save_page_result(chapter_id, 1, {
        "processing_status": "complete", "translated_lines": [{
            "index": 1, "region_id": "reg_caption", "region_type": "external_text",
            "text": "THE PRICE IS HIGH", "translated": "الثمن مرتفع",
            "left": 20, "top": 20, "width": 100, "height": 50,
            "ocr_boxes": [[20, 20, 100, 12], [20, 58, 100, 12]],
            "ocr_polygons": [
                [[20, 20], [120, 20], [120, 32], [20, 32]],
                [[20, 58], [120, 58], [120, 70], [20, 70]],
            ],
        }],
    })

    response = client.post(
        f"/api/library/{chapter_id}/pages/1/review",
        json={"regions": [], "split_regions": [{
            "region_id": "reg_caption", "first_text": "THE PRICE IS",
            "second_text": "HIGH", "axis": "y", "ratio": 50,
        }]},
    )

    assert response.status_code == 200
    saved = web_app.library_store.read_page_result(chapter_id, 1)
    assert len(saved["translated_lines"]) == 2
    assert all(item["region_type"] == "external_text" for item in saved["translated_lines"])
    assert all("bubble_id" not in item for item in saved["translated_lines"])
    assert [item["text"] for item in saved["translated_lines"]] == ["THE PRICE IS", "HIGH"]


def test_review_route_saves_bubble_mask_for_all_regions_and_rejects_outside_points(monkeypatch, tmp_path):
    monkeypatch.setenv("MANGA_LIBRARY_DIR", str(tmp_path / "library"))
    client = web_app.app.test_client()
    imported = client.post("/api/library/import", data={
        "title": "Manual bubble mask sample", "reading_layout": "vertical",
        "pages": (io.BytesIO(_png_bytes()), "page1.png"),
    }, content_type="multipart/form-data")
    chapter_id = imported.json["chapter"]["id"]
    bubble_box = [20, 10, 220, 150]
    regions = [
        {"index": 1, "region_id": "reg_mask_one", "bubble_id": 1,
         "bubble_bbox": bubble_box, "region_type": "speech_bubble",
         "text": "TOP", "translated": "أعلى"},
        {"index": 2, "region_id": "reg_mask_two", "bubble_id": 1,
         "bubble_bbox": bubble_box, "region_type": "speech_bubble",
         "text": "BOTTOM", "translated": "أسفل"},
    ]
    web_app.library_store.save_page_result(chapter_id, 1, {
        "processing_status": "complete", "translated_lines": regions,
    })
    polygon = [[40, 30], [200, 30], [200, 120], [40, 120]]

    response = client.post(
        f"/api/library/{chapter_id}/pages/1/review",
        json={"regions": [], "bubble_mask_edits": [{
            "region_id": "reg_mask_one", "polygon": polygon,
        }]},
    )
    assert response.status_code == 200
    saved = web_app.library_store.read_page_result(chapter_id, 1)
    assert all(item["manual_bubble_polygon"] == polygon for item in saved["translated_lines"])
    assert saved["render_stale"] is True

    rejected = client.post(
        f"/api/library/{chapter_id}/pages/1/review",
        json={"regions": [], "bubble_mask_edits": [{
            "region_id": "reg_mask_one", "polygon": [[10, 5], [200, 30], [200, 120]],
        }]},
    )
    assert rejected.status_code == 400
    unchanged = web_app.library_store.read_page_result(chapter_id, 1)
    assert all(item["manual_bubble_polygon"] == polygon for item in unchanged["translated_lines"])


def test_page_result_save_retries_temporary_windows_file_lock(monkeypatch, tmp_path):
    monkeypatch.setenv("MANGA_LIBRARY_DIR", str(tmp_path / "library"))
    original_replace = os.replace
    attempts = []

    def fail_twice_then_replace(source, destination):
        attempts.append((source, destination))
        if len(attempts) < 3:
            raise PermissionError("temporary scanner lock")
        return original_replace(source, destination)

    monkeypatch.setattr(web_app.library_store.os, "replace", fail_twice_then_replace)
    web_app.library_store.save_page_result("retry-sample", 1, {"progress": {"stage": "complete"}})

    saved = web_app.library_store.read_page_result("retry-sample", 1)
    assert saved == {"progress": {"stage": "complete"}}
    assert len(attempts) == 3


def _png_bytes():
    image = Image.new("RGB", (320, 120), "white")
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


def _color_png(color):
    image = Image.new("RGB", (40, 40), color)
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


def test_cached_translation_is_reused_only_when_new_integrity_policy_accepts_it():
    valid = {"text": "A short line.", "translated": "\u0646\u0635 \u0642\u0635\u064a\u0631."}
    assert web_app._cached_translation_passes_output_policy(valid)
    assert not web_app._cached_translation_passes_output_policy(
        {"text": "HUNTER BAEK MIHO.", "translated": "\u0647\u0627\u0646\u062a\u0631 \u0628\u0627\u064a\u0643 mAho."}
    )
    assert not web_app._cached_translation_passes_output_policy(
        {"text": "NEARBY, PAJU FIELD-TYPE DUNGEON", "translated": "\u0643\u0644\u0627\u0645 \u063a\u064a\u0631 \u0645\u0631\u062a\u0628\u0637 " * 14}
    )
    assert web_app._cached_translation_passes_output_policy(
        {"text": "scanlation credit", "translated": "", "include": False}
    )


def test_url_only_external_credit_is_skipped_but_prose_with_url_is_translated(monkeypatch, tmp_path):
    monkeypatch.setenv("MANGA_LIBRARY_DIR", str(tmp_path / "library"))
    regions = [
        {"text": "reaperscans.com flamescomics.me", "left": 1, "top": 1,
         "width": 100, "height": 20, "confidence": 0.99, "region_type": "external_text"},
        {"text": "Visit example.com after the battle.", "left": 1, "top": 30,
         "width": 180, "height": 24, "confidence": 0.99, "region_type": "external_text"},
        {"text": "REAPER N FLamEs TRANSLATOR LINGERER", "left": 1, "top": 60,
         "width": 180, "height": 24, "confidence": 0.95, "region_type": "external_text"},
        {"text": "PROOFREADER SHORTPERSON CLEANER/REDRAWER YUN", "left": 1, "top": 90,
         "width": 180, "height": 24, "confidence": 0.95, "region_type": "external_text"},
        {"text": "EE H SEASON 1", "left": 1, "top": 110,
         "width": 180, "height": 24, "confidence": 0.8996, "region_type": "external_text"},
        {"text": "SOLO LEVELING: RAGNAROK by D&C Webtoon Biz, Redice & Daul",
         "left": 1, "top": 130, "width": 220, "height": 24, "confidence": 0.99,
         "region_type": "external_text"},
    ]
    monkeypatch.setattr(web_app, "run_local_ocr", lambda *_args, **_kwargs: {
        "text": "reaperscans.com flamescomics.me\\nVisit example.com after the battle.\\n"
                "REAPER N FLamEs TRANSLATOR LINGERER\\n"
                "PROOFREADER SHORTPERSON CLEANER/REDRAWER YUN\nEE H SEASON 1\n"
                "SOLO LEVELING: RAGNAROK by D&C Webtoon Biz, Redice & Daul",
        "lines": regions, "regions": regions, "provider": "test_ocr", "elapsed_seconds": 0.1,
    })
    translated = []
    monkeypatch.setattr(web_app, "translate_text", lambda text, *_args, **_kwargs: translated.append(text) or "زر المثال بعد المعركة.")
    client = web_app.app.test_client()
    imported = client.post("/api/library/import", data={
        "title": "Vertical sample", "reading_layout": "vertical",
        "pages": (io.BytesIO(_png_bytes()), "page1.png"),
    }, content_type="multipart/form-data")
    assert imported.status_code == 201

    response = client.post(f"/api/library/{imported.json['chapter']['id']}/pages/1/translate")
    assert response.status_code == 200
    lines = response.json["translated_lines"]
    assert translated == ["Visit example.com after the battle."]
    assert lines[0]["include"] is False
    assert lines[0]["auto_exclusion_reason"] == "source_credit"
    assert lines[1]["translated"] == "زر المثال بعد المعركة."
    assert lines[2]["include"] is False
    assert lines[2]["auto_exclusion_reason"] == "scanlation_credit"
    assert lines[3]["include"] is False
    assert lines[3]["auto_exclusion_reason"] == "scanlation_credit"
    assert lines[4]["include"] is False
    assert lines[4]["auto_exclusion_reason"] == "low_ocr_confidence"
    assert lines[5]["include"] is False
    assert lines[5]["auto_exclusion_reason"] == "series_byline_credit"
    assert response.json["processing_status"] == "complete"


def test_standalone_gasp_effect_is_excluded_from_translation(monkeypatch, tmp_path):
    monkeypatch.setenv("MANGA_LIBRARY_DIR", str(tmp_path / "library"))
    regions = [
        {"text": "GASP!", "left": 1, "top": 1, "width": 70, "height": 24,
         "confidence": 0.99, "region_type": "speech_bubble"},
        {"text": "I'M STILL ALIVE.", "left": 1, "top": 40, "width": 150, "height": 24,
         "confidence": 0.99, "region_type": "speech_bubble"},
    ]
    monkeypatch.setattr(web_app, "run_local_ocr", lambda *_args, **_kwargs: {
        "text": "GASP!\\nI'M STILL ALIVE.", "lines": regions, "regions": regions,
        "provider": "test_ocr", "elapsed_seconds": 0.1,
    })
    translated = []
    monkeypatch.setattr(web_app, "translate_text", lambda text, *_args, **_kwargs:
                        translated.append(text) or "ما زلت حياً.")
    client = web_app.app.test_client()
    imported = client.post("/api/library/import", data={
        "title": "Vertical sample", "reading_layout": "vertical",
        "pages": (io.BytesIO(_png_bytes()), "page1.png"),
    }, content_type="multipart/form-data")
    assert imported.status_code == 201

    response = client.post(f"/api/library/{imported.json['chapter']['id']}/pages/1/translate")
    assert response.status_code == 200
    lines = response.json["translated_lines"]
    assert translated == ["I'M STILL ALIVE."]
    assert lines[0]["include"] is False
    assert lines[0]["auto_exclusion_reason"] == "sound_effect"
    assert lines[1]["translated"] == "ما زلت حياً."


def test_obvious_source_promotion_is_skipped_but_story_narration_is_translated(monkeypatch, tmp_path):
    monkeypatch.setenv("MANGA_LIBRARY_DIR", str(tmp_path / "library"))
    regions = [
        {"text": "RESET SCANS IS RECRUITING JAPANESE TRANSLATORS. SUPPORT US AT KO-FI.COM.",
         "left": 1, "top": 1, "width": 220, "height": 50, "confidence": 0.99,
         "region_type": "external_text"},
        {"text": "THE GUILD IS RECRUITING A HEALER AFTER THE ATTACK.",
         "left": 1, "top": 70, "width": 200, "height": 34, "confidence": 0.99,
         "region_type": "external_text"},
    ]
    monkeypatch.setattr(web_app, "run_local_ocr", lambda *_args, **_kwargs: {
        "text": "\\n".join(row["text"] for row in regions), "lines": regions,
        "regions": regions, "provider": "test_ocr", "elapsed_seconds": 0.1,
    })
    translated = []
    monkeypatch.setattr(web_app, "translate_text", lambda text, *_args, **_kwargs:
                        translated.append(text) or "تجند النقابة معالجاً بعد الهجوم.")
    client = web_app.app.test_client()
    imported = client.post("/api/library/import", data={
        "title": "Vertical sample", "reading_layout": "vertical",
        "pages": (io.BytesIO(_png_bytes()), "page1.png"),
    }, content_type="multipart/form-data")
    assert imported.status_code == 201

    response = client.post(f"/api/library/{imported.json['chapter']['id']}/pages/1/translate")
    assert response.status_code == 200
    lines = response.json["translated_lines"]
    assert translated == ["THE GUILD IS RECRUITING A HEALER AFTER THE ATTACK."]
    assert lines[0]["include"] is False
    assert lines[0]["auto_exclusion_reason"] == "source_promotion"
    assert lines[1]["translated"] == "تجند النقابة معالجاً بعد الهجوم."


def test_manhuaplus_faster_release_end_card_is_skipped(monkeypatch, tmp_path):
    monkeypatch.setenv("MANGA_LIBRARY_DIR", str(tmp_path / "library"))
    promotion = "IF YOU ENJOYED THIS SERIES AND WANT A FASTER RELEASE PLEASE READ ON MANHUAPLUS.ORG"
    regions = [{"text": promotion, "left": 1, "top": 1, "width": 220,
                "height": 50, "confidence": 0.99, "region_type": "external_text"}]
    monkeypatch.setattr(web_app, "run_local_ocr", lambda *_args, **_kwargs: {
        "text": promotion, "lines": regions, "regions": regions,
        "provider": "test_ocr", "elapsed_seconds": 0.1,
    })
    translated = []
    monkeypatch.setattr(web_app, "translate_text", lambda text, *_args, **_kwargs:
                        translated.append(text) or "ترجمة إعلان")
    client = web_app.app.test_client()
    imported = client.post("/api/library/import", data={
        "title": "Vertical sample", "reading_layout": "vertical",
        "pages": (io.BytesIO(_png_bytes()), "page1.png"),
    }, content_type="multipart/form-data")
    assert imported.status_code == 201

    response = client.post(f"/api/library/{imported.json['chapter']['id']}/pages/1/translate")
    assert response.status_code == 200
    assert translated == []
    line = response.json["translated_lines"][0]
    assert line["include"] is False
    assert line["auto_exclusion_reason"] == "source_promotion"


def test_discord_cta_inside_bubble_is_skipped_but_story_dialogue_is_translated(monkeypatch, tmp_path):
    monkeypatch.setenv("MANGA_LIBRARY_DIR", str(tmp_path / "library"))
    promotion = ("WANT TO READ A NEW CHAPTER? JOIN OUR DISCORD TO GET UPDATES "
                 "IN REAL TIME https://discord.gg/324gBMj")
    dialogue = "WE HAVE TO GO TO THE MANOR AND FIND HIM."
    regions = [
        {"text": promotion, "left": 1, "top": 1, "width": 220, "height": 50,
         "confidence": 0.99, "region_type": "speech_bubble"},
        {"text": dialogue, "left": 1, "top": 80, "width": 220, "height": 50,
         "confidence": 0.99, "region_type": "speech_bubble"},
    ]
    monkeypatch.setattr(web_app, "run_local_ocr", lambda *_args, **_kwargs: {
        "text": promotion + " " + dialogue, "lines": regions, "regions": regions,
        "provider": "test_ocr", "elapsed_seconds": 0.1,
    })
    translated = []
    monkeypatch.setattr(web_app, "translate_text", lambda text, *_args, **_kwargs:
                        translated.append(text) or "علينا الذهاب إلى القصر.")
    client = web_app.app.test_client()
    imported = client.post("/api/library/import", data={
        "title": "Vertical sample", "reading_layout": "vertical",
        "pages": (io.BytesIO(_png_bytes()), "page1.png"),
    }, content_type="multipart/form-data")
    assert imported.status_code == 201

    response = client.post(f"/api/library/{imported.json['chapter']['id']}/pages/1/translate")
    assert response.status_code == 200
    assert translated == [dialogue]
    lines = response.json["translated_lines"]
    assert lines[0]["include"] is False
    assert lines[0]["auto_exclusion_reason"] == "source_promotion"
    assert lines[1]["translated"] == "علينا الذهاب إلى القصر."


def test_scan_site_ads_are_skipped_but_story_recruiting_is_translated(monkeypatch, tmp_path):

    monkeypatch.setenv("MANGA_LIBRARY_DIR", str(tmp_path / "library"))
    promotions = [
        "WARNING!! READ ONLY AT GENZTOONS.COM SUPPORT US AND HELP US PAY OUR STAFF QUALITY AND QUANTITY FASTER UPDATES",
        "READ ADVANCE PREMIUM CHAPTERS AT GENZUPDATES.COM",
        "WARNING!! READ ONLY AT RAVENSCANS.COM TO SUPPORT US AND HELP US PAY OUR STAFFS",
        "READ AT RAVENSCANS.COM",
        "SUPPORT THE TEAM BY READING THIS SERIES AT RAVENSCANS.COM",
        "by reading this seriesat RavenScans.com",
    ]
    story = "THE GUILD IS RECRUITING A HEALER AFTER THE ATTACK."
    story_with_domain = "I SAW RAVENSCANS.COM AFTER THE BATTLE."
    regions = [{"text": text, "left": 1, "top": index * 60, "width": 220,
                "height": 50, "confidence": 0.99, "region_type": "external_text"}
               for index, text in enumerate([*promotions, story, story_with_domain])]
    regions.extend([
        {"text": "Support the team", "left": 242, "top": 815, "width": 496,
         "height": 99, "confidence": 0.99, "region_type": "external_text"},
        {"text": "byreading this series at RavenScans.com", "left": 142, "top": 861,
         "width": 710, "height": 208, "confidence": 0.99, "region_type": "external_text"},
    ])
    monkeypatch.setattr(web_app, "run_local_ocr", lambda *_args, **_kwargs: {
        "text": "\\n".join(row["text"] for row in regions), "lines": regions,
        "regions": regions, "provider": "test_ocr", "elapsed_seconds": 0.1,
    })
    translated = []
    monkeypatch.setattr(web_app, "translate_text", lambda text, *_args, **_kwargs:
                        translated.append(text) or "تجند النقابة معالجاً بعد الهجوم.")
    client = web_app.app.test_client()
    imported = client.post("/api/library/import", data={
        "title": "Vertical sample", "reading_layout": "vertical",
        "pages": (io.BytesIO(_png_bytes()), "page1.png"),
    }, content_type="multipart/form-data")
    assert imported.status_code == 201

    response = client.post(f"/api/library/{imported.json['chapter']['id']}/pages/1/translate")
    assert response.status_code == 200
    assert translated == [story, story_with_domain]
    lines = response.json["translated_lines"]
    assert [line.get("include", True) for line in lines] == [False] * 6 + [True, True, False, False]
    assert [line.get("auto_exclusion_reason") for line in lines] == [
        "source_promotion", "source_promotion", "source_promotion",
        "source_promotion", "source_promotion", "source_promotion", None, None,
        "source_promotion", "source_promotion",
    ]
    assert lines[6]["translated"] == "تجند النقابة معالجاً بعد الهجوم."
    assert lines[7]["translated"] == "تجند النقابة معالجاً بعد الهجوم."


def test_domain_only_credit_match_does_not_swallow_regular_text():
    assert web_app._is_domain_only_credit("https://reaperscans.com/chapter/1, flamescomics.me")
    assert not web_app._is_domain_only_credit("the flame comics are beautiful")
    assert not web_app._is_domain_only_credit("Visit example.com after the battle.")


def test_scanlation_credit_match_is_conservative_and_external_only():
    assert web_app._is_scanlation_role_credit("REAPER N FLamEs TRANSLATOR LINGERER")
    assert web_app._is_scanlation_role_credit("PROOFREADER SHORTPERSON CLEANER/REDRAWER YUN")
    assert not web_app._is_scanlation_role_credit("The translator helped me escape.")
    assert not web_app._is_scanlation_role_credit("TRANSLATOR, HELP!")


def test_series_byline_credit_is_conservative():
    assert web_app._is_series_byline_credit(
        "SOLO LEVELING: RAGNAROK by D&C Webtoon Biz, Redice & Daul"
    )
    assert not web_app._is_series_byline_credit("THE FLOOR BOSS IS DEFEATED BY EUGENE.")
    assert not web_app._is_series_byline_credit("The story by the river was unforgettable.")


def test_low_confidence_only_excludes_uncertain_external_text():
    assert web_app._is_low_confidence_external_region({
        "region_type": "external_text", "confidence": 0.8996, "text": "EE H SEASON 1",
    })
    assert not web_app._is_low_confidence_external_region({
        "region_type": "external_text", "confidence": 0.90, "text": "THIS IS CLEAR",
    })
    assert not web_app._is_low_confidence_external_region({
        "region_type": "speech_bubble", "confidence": 0.5, "text": "I WILL GO",
    })


def test_ambiguous_short_external_ocr_fragments_are_preserved():
    assert web_app._is_ambiguous_short_external_fragment({
        "region_type": "external_text", "text": "OL", "width": 203, "height": 138,
    })
    assert web_app._is_ambiguous_short_external_fragment({
        "region_type": "external_text", "text": "OLOG", "width": 386, "height": 140,
    })
    assert web_app._is_ambiguous_short_external_fragment({
        "region_type": "external_text", "text": "BiZ", "width": 40, "height": 26,
    })
    assert not web_app._is_ambiguous_short_external_fragment({
        "region_type": "external_text", "text": "HELP", "width": 120, "height": 35,
    })
    assert not web_app._is_ambiguous_short_external_fragment({
        "region_type": "speech_bubble", "text": "OL", "width": 40, "height": 26,
    })


def test_import_read_process_and_export_local_chapter(monkeypatch, tmp_path):
    monkeypatch.setenv("MANGA_LIBRARY_DIR", str(tmp_path / "library"))
    monkeypatch.setattr(
        web_app, "run_local_ocr",
        lambda *_args, **_kwargs: {
            "text": "I will protect you.",
            "lines": [{"text": "I will protect you.", "left": 10, "top": 20, "width": 120, "height": 24, "confidence": 0.98}],
            "provider": "rapidocr_onnxruntime_cpu",
            "elapsed_seconds": 0.25,
        },
    )
    monkeypatch.setattr(web_app, "translate_text", lambda text, *_args, **_kwargs: "سوف أحميك")
    client = web_app.app.test_client()

    imported = client.post("/api/library/import", data={
        "title": "Sample chapter",
        "series_type": "manhwa",
        "reading_direction": "ltr",
        "reading_layout": "vertical",
        "pages": (io.BytesIO(_png_bytes()), "page10.png"),
    }, content_type="multipart/form-data")
    assert imported.status_code == 201
    chapter_id = imported.json["chapter"]["id"]
    assert imported.json["chapter"]["page_count"] == 1

    page_response = client.get(f"/api/library/{chapter_id}/pages/1/image")
    assert page_response.status_code == 200
    assert page_response.mimetype == "image/png"

    processed = client.post(f"/api/library/{chapter_id}/pages/1/translate")
    assert processed.status_code == 200
    assert processed.json["translated"] == "سوف أحميك"
    assert processed.json["ocr_provider"] == "rapidocr_onnxruntime_cpu"
    assert client.get(f"/api/library/{chapter_id}").json["chapter"]["pages"][0]["result"]

    reviewed = client.post(f"/api/library/{chapter_id}/pages/1/review", json={"regions": [{
        "index": 1, "source": "I will guard you.", "translated": "سوف أحرسك", "include": True,
    }]})
    assert reviewed.status_code == 200
    assert reviewed.json["translated_lines"][0]["text"] == "I will guard you."
    assert reviewed.json["translated"] == "سوف أحرسك"

    empty_preview = client.post(f"/api/library/{chapter_id}/pages/1/render", json={"regions": []})
    assert empty_preview.status_code == 400, empty_preview.json

    rendered = client.post(f"/api/library/{chapter_id}/pages/1/render")
    assert rendered.status_code == 200
    assert rendered.json["rendered_regions"] == 1
    assert rendered.json["skipped_regions"] == 0
    rendered_cbz = client.get(f"/api/library/{chapter_id}/export?format=cbz&edition=rendered")
    assert rendered_cbz.status_code == 200
    with zipfile.ZipFile(io.BytesIO(rendered_cbz.data)) as archive:
        assert archive.namelist() == ["0001.png", "ComicInfo.xml"]

    exported = client.get(f"/api/library/{chapter_id}/export")
    assert exported.status_code == 200
    with zipfile.ZipFile(io.BytesIO(exported.data)) as archive:
        assert "images/page-0001.png" in archive.namelist()
        assert "rendered/page-0001.png" in archive.namelist()
        manifest = json.loads(archive.read("translations.json"))
        assert manifest["pages"][0]["translation"]["translated"] == "سوف أحرسك"
        assert "review" in manifest["note"]


def test_page_layers_export_contains_recomposable_assets_and_region_manifest(monkeypatch, tmp_path):
    monkeypatch.setenv("MANGA_LIBRARY_DIR", str(tmp_path / "library"))
    client = web_app.app.test_client()
    imported = client.post("/api/library/import", data={
        "title": "Layer export sample", "reading_layout": "vertical",
        "pages": (io.BytesIO(_png_bytes()), "page1.png"),
    }, content_type="multipart/form-data")
    assert imported.status_code == 201
    chapter_id = imported.json["chapter"]["id"]
    result = {
        "processing_status": "complete",
        "translated_lines": [{
            "index": 1, "region_id": "reg_layer",
            "region_type": "external_text", "text": "HELLO", "translated": "مرحباً",
            "left": 80, "top": 35, "width": 140, "height": 45,
        }],
    }
    web_app.library_store.save_page_result(chapter_id, 1, result)
    monkeypatch.setattr(
        web_app, "refresh_chapter_translation_staleness",
        lambda _chapter_id, _chapter: {1: result},
    )

    response = client.get(f"/api/library/{chapter_id}/pages/1/layers.zip")
    assert response.status_code == 200
    assert response.mimetype == "application/zip"
    with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
        assert set(archive.namelist()) == {
            "final.png", "original.png", "text_removal_mask.png",
            "repaired_underlay.png", "translated_text_overlay.png", "regions.json",
        }
        manifest = json.loads(archive.read("regions.json"))
        assert manifest["regions"][0]["region_id"] == "reg_layer"
        assert manifest["regions"][0]["translated"] == "مرحباً"
        for name in archive.namelist():
            if name.endswith(".png"):
                assert Image.open(io.BytesIO(archive.read(name))).size == (320, 120)


def test_page_layers_export_rejects_incomplete_page(monkeypatch, tmp_path):
    monkeypatch.setenv("MANGA_LIBRARY_DIR", str(tmp_path / "library"))
    client = web_app.app.test_client()
    imported = client.post("/api/library/import", data={
        "title": "Incomplete layer export", "reading_layout": "vertical",
        "pages": (io.BytesIO(_png_bytes()), "page1.png"),
    }, content_type="multipart/form-data")
    assert imported.status_code == 201
    chapter_id = imported.json["chapter"]["id"]
    incomplete = {
        "processing_status": "translation_in_progress",
        "translated_lines": [{"text": "HELLO", "translated": ""}],
    }
    web_app.library_store.save_page_result(chapter_id, 1, incomplete)
    monkeypatch.setattr(
        web_app, "refresh_chapter_translation_staleness",
        lambda _chapter_id, _chapter: {1: incomplete},
    )
    response = client.get(f"/api/library/{chapter_id}/pages/1/layers.zip")
    assert response.status_code == 409


def test_cached_page_from_older_ocr_pipeline_is_reprocessed(monkeypatch, tmp_path):
    monkeypatch.setenv("MANGA_LIBRARY_DIR", str(tmp_path / "library"))
    ocr_calls = []
    monkeypatch.setattr(web_app, "run_local_ocr", lambda *_args, **_kwargs: (
        ocr_calls.append(1) or {
            "text": "The full recovered sentence.",
            "lines": [{"text": "The full recovered sentence.", "left": 8, "top": 8,
                       "width": 180, "height": 24, "confidence": 0.97}],
            "regions": [{"text": "The full recovered sentence.", "left": 8, "top": 8,
                         "width": 180, "height": 24, "confidence": 0.97,
                         "region_type": "external_text"}],
            "provider": "test_ocr_v2", "elapsed_seconds": 0.02,
        }
    ))
    monkeypatch.setattr(web_app, "translate_text", lambda *_args, **_kwargs: "الجملة كاملة بعد استعادتها.")
    client = web_app.app.test_client()
    imported = client.post("/api/library/import", data={
        "title": "OCR migration sample", "reading_layout": "vertical",
        "pages": (io.BytesIO(_png_bytes()), "page1.png"),
    }, content_type="multipart/form-data")
    assert imported.status_code == 201
    chapter_id = imported.json["chapter"]["id"]

    # Mimic a completed page saved before the deskew/refined OCR pipeline.
    web_app.library_store.save_page_result(chapter_id, 1, {
        "pipeline_version": web_app.PAGE_PIPELINE_VERSION - 1,
        "processing_status": "complete",
        "ocr_provider": "rapidocr_onnxruntime_cpu",
        "original": "Recovered sentence.",
        "translated": "جملة ناقصة.",
        "ocr_lines": [],
        "dialogue_regions": [],
        "translated_lines": [{
            "index": 1, "text": "Recovered sentence.", "translated": "جملة ناقصة.",
            "include": True, "region_type": "external_text",
        }],
        "progress": {"stage": "complete", "completed_regions": 1, "total_regions": 1},
    })

    response = client.post(f"/api/library/{chapter_id}/pages/1/translate")
    assert response.status_code == 200
    assert ocr_calls == [1]
    assert response.json["pipeline_version"] == web_app.PAGE_PIPELINE_VERSION
    assert response.json["original"] == "The full recovered sentence."
    assert response.json["translated"] == "الجملة كاملة بعد استعادتها."


def test_ocr_pipeline_refresh_preserves_exact_human_review_and_exclusion(monkeypatch, tmp_path):
    monkeypatch.setenv("MANGA_LIBRARY_DIR", str(tmp_path / "library"))
    monkeypatch.setattr(web_app, "run_local_ocr", lambda *_args, **_kwargs: {
        "text": "Reviewed sentence.\nSkip this line.",
        "lines": [
            {"text": "Reviewed sentence.", "left": 8, "top": 8, "width": 100, "height": 20},
            {"text": "Skip this line.", "left": 8, "top": 40, "width": 90, "height": 20},
        ],
        "regions": [
            {"text": "Reviewed sentence.", "left": 8, "top": 8, "width": 100, "height": 20,
             "region_type": "speech_bubble"},
            {"text": "Skip this line.", "left": 8, "top": 40, "width": 90, "height": 20,
             "region_type": "speech_bubble"},
        ],
        "provider": "test_ocr_v2", "elapsed_seconds": 0.02,
    })
    calls = []
    monkeypatch.setattr(web_app, "translate_text", lambda text, *_args, **_kwargs: calls.append(text) or "ترجمة آلية")
    client = web_app.app.test_client()
    imported = client.post("/api/library/import", data={
        "title": "Reviewed OCR migration sample", "reading_layout": "vertical",
        "pages": (io.BytesIO(_png_bytes()), "page1.png"),
    }, content_type="multipart/form-data")
    chapter_id = imported.json["chapter"]["id"]
    web_app.library_store.save_page_result(chapter_id, 1, {
        "pipeline_version": web_app.PAGE_PIPELINE_VERSION - 1,
        "processing_status": "complete",
        "ocr_provider": "rapidocr_onnxruntime_cpu",
        "translated_lines": [
            {"index": 1, "text": "Reviewed sentence.", "translated": "صياغة بشرية معتمدة.",
             "review_status": "human_reviewed", "include": True, "region_type": "speech_bubble"},
            {"index": 2, "text": "Skip this line.", "translated": "", "include": False,
             "region_type": "speech_bubble"},
        ],
    })

    response = client.post(f"/api/library/{chapter_id}/pages/1/translate")
    assert response.status_code == 200
    assert calls == []
    assert response.json["translated_lines"][0]["translated"] == "صياغة بشرية معتمدة."
    assert response.json["translated_lines"][0]["review_status"] == "human_reviewed"
    assert response.json["translated_lines"][1]["include"] is False


def test_import_rejects_non_image(monkeypatch, tmp_path):
    monkeypatch.setenv("MANGA_LIBRARY_DIR", str(tmp_path / "library"))
    client = web_app.app.test_client()
    response = client.post("/api/library/import", data={
        "title": "Bad upload",
        "reading_layout": "vertical",
        "pages": (io.BytesIO(b"not an image"), "page.png"),
    }, content_type="multipart/form-data")
    assert response.status_code == 400


def test_cbz_import_export_and_round_trip(monkeypatch, tmp_path):
    monkeypatch.setenv("MANGA_LIBRARY_DIR", str(tmp_path / "library"))
    archive_bytes = io.BytesIO()
    with zipfile.ZipFile(archive_bytes, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("pages/page10.png", _color_png("red"))
        archive.writestr("pages/page2.png", _color_png("blue"))
        archive.writestr("zz/../../outside.png", _color_png("green"))
    archive_bytes.seek(0)
    client = web_app.app.test_client()
    imported = client.post("/api/library/import", data={
        "title": "CBZ sample",
        "series_type": "manhwa",
        "reading_direction": "ltr",
        "reading_layout": "vertical",
        "chapter_archive": (archive_bytes, "sample.cbz"),
    }, content_type="multipart/form-data")
    assert imported.status_code == 201
    chapter_id = imported.json["chapter"]["id"]
    assert imported.json["chapter"]["page_count"] == 3
    page = client.get(f"/api/library/{chapter_id}/pages/1/image")
    assert Image.open(io.BytesIO(page.data)).convert("RGB").getpixel((0, 0)) == (0, 0, 255)
    page2 = client.get(f"/api/library/{chapter_id}/pages/2/image")
    assert Image.open(io.BytesIO(page2.data)).convert("RGB").getpixel((0, 0)) == (255, 0, 0)
    assert not (tmp_path / "outside.png").exists()

    exported = client.get(f"/api/library/{chapter_id}/export?format=cbz")
    assert exported.status_code == 200
    with zipfile.ZipFile(io.BytesIO(exported.data)) as archive:
        assert archive.namelist() == ["0001.png", "0002.png", "0003.png", "ComicInfo.xml"]
        metadata = archive.read("ComicInfo.xml").decode("utf-8")
        assert "YesAndRightToLeft" not in metadata

    round_trip = client.post("/api/library/import", data={
        "title": "Round-trip chapter",
        "series_type": "manhwa",
        "reading_direction": "ltr",
        "reading_layout": "vertical",
        "chapter_archive": (io.BytesIO(exported.data), "roundtrip.cbz"),
    }, content_type="multipart/form-data")
    assert round_trip.status_code == 201
    assert round_trip.json["chapter"]["page_count"] == 3


def test_rendered_cbz_requires_every_page(monkeypatch, tmp_path):
    monkeypatch.setenv("MANGA_LIBRARY_DIR", str(tmp_path / "library"))
    client = web_app.app.test_client()
    imported = client.post("/api/library/import", data={
        "title": "Needs render", "reading_layout": "vertical",
        "pages": (io.BytesIO(_png_bytes()), "page1.png"),
    }, content_type="multipart/form-data")
    chapter_id = imported.json["chapter"]["id"]
    response = client.get(f"/api/library/{chapter_id}/export?format=cbz&edition=rendered")
    assert response.status_code == 409


def test_page_translation_resumes_after_region_failure_without_repeating_ocr(monkeypatch, tmp_path):
    monkeypatch.setenv("MANGA_LIBRARY_DIR", str(tmp_path / "library"))
    monkeypatch.setenv("MANGA_TRANSLATION_WORKERS", "1")
    monkeypatch.setenv("MANGA_INDEX_BATCH_SIZE", "1")
    ocr_calls = []
    monkeypatch.setattr(web_app, "run_local_ocr", lambda *_args, **_kwargs: (
        ocr_calls.append(1) or {
            "text": "First line.\nSecond line.",
            "lines": [
                {"text": "First line.", "left": 10, "top": 10, "width": 30, "height": 8},
                {"text": "Second line.", "left": 10, "top": 30, "width": 35, "height": 8},
            ],
            "regions": [
                {"text": "First line.", "left": 10, "top": 10, "width": 30, "height": 8},
                {"text": "Second line.", "left": 10, "top": 30, "width": 35, "height": 8},
            ],
            "provider": "test-ocr", "elapsed_seconds": 0.01,
        }
    ))
    calls = []
    failed_once = {"value": False}
    def translate(text, *_args, **_kwargs):
        calls.append(text)
        if text == "Second line." and not failed_once["value"]:
            failed_once["value"] = True
            raise RuntimeError("simulated interruption")
        return "ترجمة " + text
    monkeypatch.setattr(web_app, "translate_text", translate)
    client = web_app.app.test_client()
    imported = client.post("/api/library/import", data={
        "title": "Resume sample", "reading_layout": "vertical",
        "pages": (io.BytesIO(_png_bytes()), "page1.png"),
    }, content_type="multipart/form-data")
    chapter_id = imported.json["chapter"]["id"]
    untouched_progress = client.get(f"/api/library/{chapter_id}/progress")
    assert untouched_progress.json["status"] == "not_started"

    interrupted = client.post(f"/api/library/{chapter_id}/pages/1/translate")
    assert interrupted.status_code == 500
    partial = web_app.library_store.read_page_result(chapter_id, 1)
    assert partial["processing_status"] == "translation_in_progress"
    assert partial["translated_lines"][0]["translated"] == "ترجمة First line."
    assert partial["translated_lines"][1]["translated"] == ""
    in_progress = client.get(f"/api/library/{chapter_id}/progress").json
    assert in_progress["status"] == "in_progress"
    assert in_progress["current"]["stage"] == "translation"
    assert in_progress["completed_regions"] == 1

    resumed = client.post(f"/api/library/{chapter_id}/pages/1/translate")
    assert resumed.status_code == 200
    assert resumed.json["processing_status"] == "complete"
    assert resumed.json["progress"]["completed_regions"] == 2
    assert client.get(f"/api/library/{chapter_id}/progress").json["status"] == "complete"
    assert ocr_calls == [1]
    assert calls == ["First line.", "Second line.", "Second line."]
    assert partial["translation_engine_signature"] == web_app.active_translation_recipe_signature()


def test_concurrent_page_requests_do_not_duplicate_completed_ocr_or_translation(monkeypatch, tmp_path):
    import concurrent.futures
    import threading
    import time

    monkeypatch.setenv("MANGA_LIBRARY_DIR", str(tmp_path / "library"))
    ocr_calls = []
    translation_calls = []
    counter_lock = threading.Lock()

    def run_ocr(*_args, **_kwargs):
        with counter_lock:
            ocr_calls.append(1)
        return {
            "text": "One sentence.", "lines": [],
            "regions": [{"text": "One sentence.", "left": 10, "top": 10,
                         "width": 45, "height": 12}],
            "provider": "test-ocr", "elapsed_seconds": 0.01,
        }

    def translate(text, *_args, **_kwargs):
        with counter_lock:
            translation_calls.append(text)
        time.sleep(0.05)
        return "ترجمة واحدة"

    monkeypatch.setattr(web_app, "run_local_ocr", run_ocr)
    monkeypatch.setattr(web_app, "translate_text", translate)
    client = web_app.app.test_client()
    imported = client.post("/api/library/import", data={
        "title": "Concurrent duplicate sample", "reading_layout": "vertical",
        "pages": (io.BytesIO(_png_bytes()), "page1.png"),
    }, content_type="multipart/form-data")
    chapter_id = imported.json["chapter"]["id"]
    start_barrier = threading.Barrier(2)

    def request_translation():
        start_barrier.wait(timeout=5)
        with web_app.app.test_client() as concurrent_client:
            return concurrent_client.post(
                f"/api/library/{chapter_id}/pages/1/translate"
            ).status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(lambda _index: request_translation(), range(2)))

    assert statuses == [200, 200]
    assert ocr_calls == [1]
    assert translation_calls == ["One sentence."]


def test_page_translation_runs_independent_regions_concurrently_without_losing_context(monkeypatch, tmp_path):
    import threading

    monkeypatch.setenv("MANGA_LIBRARY_DIR", str(tmp_path / "library"))
    monkeypatch.setenv("MANGA_TRANSLATION_WORKERS", "2")
    monkeypatch.setenv("MANGA_INDEX_BATCH_SIZE", "1")
    monkeypatch.setattr(web_app, "run_local_ocr", lambda *_args, **_kwargs: {
        "text": "First line.\nSecond line.",
        "lines": [],
        "regions": [
            {"text": "First line.", "left": 10, "top": 10, "width": 30, "height": 8},
            {"text": "Second line.", "left": 10, "top": 30, "width": 35, "height": 8},
        ],
        "provider": "test-ocr", "elapsed_seconds": 0.01,
    })
    both_started = threading.Barrier(2)
    contexts = {}

    def translate(text, *_args, context="", **_kwargs):
        contexts[text] = context
        both_started.wait(timeout=5)
        return "ترجمة " + text

    monkeypatch.setattr(web_app, "translate_text", translate)
    client = web_app.app.test_client()
    imported = client.post("/api/library/import", data={
        "title": "Parallel region sample", "reading_layout": "vertical",
        "pages": (io.BytesIO(_png_bytes()), "page1.png"),
    }, content_type="multipart/form-data")
    chapter_id = imported.json["chapter"]["id"]

    response = client.post(f"/api/library/{chapter_id}/pages/1/translate")

    assert response.status_code == 200
    result = response.get_json()
    assert result["processing_status"] == "complete"
    assert [line["translated"] for line in result["translated_lines"]] == [
        "ترجمة First line.", "ترجمة Second line.",
    ]
    assert "Dialogue: First line." in contexts["Second line."]
    assert "Dialogue: Second line." not in contexts["First line."]


def test_progress_reports_live_elapsed_time_for_active_region(monkeypatch, tmp_path):
    monkeypatch.setenv("MANGA_LIBRARY_DIR", str(tmp_path / "library"))
    client = web_app.app.test_client()
    imported = client.post("/api/library/import", data={
        "title": "Live progress sample", "reading_layout": "vertical",
        "pages": (io.BytesIO(_png_bytes()), "page1.png"),
    }, content_type="multipart/form-data")
    assert imported.status_code == 201
    chapter_id = imported.json["chapter"]["id"]
    web_app.library_store.save_page_result(chapter_id, 1, {
        "processing_status": "translation_in_progress",
        "progress": {"stage": "translation", "completed_regions": 0,
                     "total_regions": 1, "current_region": 1},
        "translation_elapsed_seconds": 2.0,
        "translated_lines": [{"index": 1, "text": "Hello.", "translated": ""}],
    })
    web_app.update_local_page_runtime(
        chapter_id, 1, stage="translation", current_region=1,
        started_monotonic=time.monotonic() - 6,
    )
    try:
        progress = client.get(f"/api/library/{chapter_id}/progress").get_json()
    finally:
        web_app.update_local_page_runtime(chapter_id, 1)
    assert progress["current"]["current_region_elapsed_seconds"] >= 5.5
    assert progress["current"]["translation_elapsed_seconds"] >= 7.5
