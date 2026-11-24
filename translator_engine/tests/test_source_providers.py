import pytest

from source_providers import MangaKProvider, MangaDexProvider, ManhuaPlusProvider, ProviderRegistry


def test_mangak_provider_parses_pages_and_rejects_http_images():
    provider = MangaKProvider()
    assert provider.can_handle("https://mangak.io/title/chapter-1")
    assert not provider.can_handle("http://mangak.io/title/chapter-1")
    parsed = provider.parse_chapter("""
        <html><head><title>Test Chapter</title></head><body>
        <img src="/pages/2.jpg"><img data-src="https://cdn.example/1.png">
        <img src="http://cdn.example/insecure.jpg">
        </body></html>
    """, "https://mangak.io/title/chapter-1")
    assert parsed["title"] == "Test Chapter"
    assert parsed["source"] == "mangak"
    assert [page["index"] for page in parsed["pages"]] == [1, 2]
    assert all(page["image"].startswith("https://") for page in parsed["pages"])


def test_provider_registry_rejects_duplicate_ids():
    provider = MangaKProvider()
    registry = ProviderRegistry([provider])
    with pytest.raises(ValueError, match="already registered"):
        registry.register(provider)
    assert registry.for_url("https://www.mangak.io/chapter") is provider


def test_manhuaplus_search_and_chapter_listing_parse_only_site_urls():
    provider = ManhuaPlusProvider()
    assert provider.can_handle("https://manhuaplus.org/manga/example/chapter-12")
    assert not provider.can_handle("http://manhuaplus.org/manga/example/chapter-12")
    assert not provider.can_handle("https://manhuaplus.com/manga/example/chapter-12")

    class Response:
        url = "https://manhuaplus.org/?s=example&post_type=wp-manga"
        text = '''<a href="/manga/example"><img alt="Example Manhwa"></a>
                  <a href="https://evil.example/manga/fake">Fake</a>
                  <a href="/manga/example/chapter-1">Chapter 1</a>'''

    calls = []
    def fetch(url, hosts, byte_limit):
        calls.append((url, hosts, byte_limit))
        return Response()
    results = provider.search("Example", fetch)
    assert results == [{"id": "example", "title": "Example Manhwa", "url": "https://manhuaplus.org/manga/example"}]
    assert "/search?keyword=Example" in calls[0][0]

    Response.url = "https://manhuaplus.org/manga/example"
    Response.text = '''<a href="/manga-type/manhwa">Manhwa</a>
      <section class="wp-manga-chapter-list">
      <a href="/manga/example/chapter-12">Chapter 12</a>
      <a href="/manga/example/chapter-11">Chapter 11</a>
      <a href="https://evil.example/manga/example/chapter-10">bad</a>
    </section>'''
    chapters = provider.chapters("example", fetch)
    assert [chapter["chapter"] for chapter in chapters] == ["Chapter 12", "Chapter 11"]
    assert all(chapter["url"].startswith("https://manhuaplus.org/") for chapter in chapters)


def test_manhuaplus_parses_reader_images_only():
    provider = ManhuaPlusProvider()
    chapter = provider.parse_chapter('''<html><h1 class="entry-title">Example - Chapter 3</h1>
      <div id="readerarea"><img data-src="//cdn.example/page-1.jpg?x=1">
      <img src="/not-a-page.html"></div><img src="https://cdn.example/cover.png"></html>''',
      "https://manhuaplus.org/manga/example/chapter-3")
    assert chapter["source"] == "manhuaplus"
    assert chapter["title"] == "Example - Chapter 3"
    assert chapter["series_key"] == "manhuaplus:example"
    assert chapter["pages"] == [{"index": 1, "image": "https://cdn.example/page-1.jpg?x=1"}]


def test_manhuaplus_loads_ajax_images_and_sorts_by_page_index():
    provider = ManhuaPlusProvider()
    calls = []
    class Page:
        url = "https://manhuaplus.org/manga/example/chapter-3"
        text = '''<a href="/manga-type/manhwa">Manhwa</a>
            <h1>Example Chapter 3</h1><script>const CHAPTER_ID = 42;</script>'''
    class Images:
        def json(self):
            return {"status": True, "html": '''
                <div class="separator" data-index="2"><a class="readImg" href="https://cdn.example/03.webp"></a></div>
                <div class="separator" data-index="0"><a class="readImg" href="https://cdn.example/01.webp"></a></div>
                <div class="separator" data-index="1"><a class="readImg" href="https://cdn.example/02.webp"></a></div>
            '''}
    def fetch(url, hosts, limit, **kwargs):
        calls.append((url, hosts, kwargs))
        return Page() if kwargs.get("method") != "POST" else Images()
    chapter = provider.load_chapter(Page.url, fetch)
    assert calls[2][0] == "https://manhuaplus.org/ajax/image/list/chap/42"
    assert calls[2][2]["data"] == {"chapter": "42"}
    assert [page["image"] for page in chapter["pages"]] == [
        "https://cdn.example/01.webp", "https://cdn.example/02.webp", "https://cdn.example/03.webp"
    ]
    assert chapter["series_type"] == "manhwa"


def test_manhuaplus_accepts_chinese_manhua_for_vertical_reading():
    provider = ManhuaPlusProvider()
    assert provider._require_supported_type('<a href="/manga-type/manhua">Manhua</a>') == "manhua"


@pytest.mark.parametrize("series_type", ["manhwa", "manhua", "manga"])
def test_source_import_accepts_vertical_comic_types(monkeypatch, series_type):
    import app as web_app

    provider = web_app.providers.by_id("manhuaplus")
    seen = {}

    def stop_after_scope_check(url, _safe_fetch):
        seen["url"] = url
        raise ValueError("stopped before source network access")

    monkeypatch.setattr(provider, "load_chapter", stop_after_scope_check)
    response = web_app.app.test_client().post("/api/source-import", json={
        "url": "https://manhuaplus.org/manga/example/chapter-1",
        "series_type": series_type,
        "reading_direction": "ltr",
        "reading_layout": "vertical",
    })
    assert seen["url"].endswith("chapter-1")
    assert response.status_code == 422
    assert response.json["error"] == "stopped before source network access"


def test_page_turn_layout_is_rejected_before_source_network_access():
    import app as web_app
    client = web_app.app.test_client()
    chapter = client.post("/api/chapter", json={
        "url": "https://manhuaplus.org/manga/example/chapter-1",
        "reading_layout": "page_by_page",
    })
    assert chapter.status_code == 422
    assert "غير مدعوم حالياً" in chapter.json["error"]

    imported = client.post("/api/source-import", json={
        "url": "https://manhuaplus.org/manga/example/chapter-1",
        "series_type": "manhwa", "reading_direction": "ltr",
        "reading_layout": "page_by_page",
    })
    assert imported.status_code == 422
    assert imported.json["error"] == chapter.json["error"]

    local = client.post("/api/library/import", data={
        "title": "Unsupported layout", "series_type": "manhwa",
        "reading_direction": "ltr", "reading_layout": "page_by_page",
    })
    assert local.status_code == 422
    assert local.json["error"] == chapter.json["error"]


def test_missing_layout_is_not_silently_assumed_vertical():
    import app as web_app
    client = web_app.app.test_client()
    remote = client.post("/api/chapter", json={
        "url": "https://manhuaplus.org/manga/example/chapter-1",
    })
    imported = client.post("/api/source-import", json={
        "url": "https://manhuaplus.org/manga/example/chapter-1",
    })
    local = client.post("/api/library/import", data={"title": "No layout"})
    assert remote.status_code == imported.status_code == local.status_code == 422
    assert remote.json["error"] == imported.json["error"] == local.json["error"]


def test_manhuaplus_is_exposed_through_generic_api(monkeypatch):
    import app as web_app
    provider = web_app.providers.by_id("manhuaplus")
    monkeypatch.setattr(provider, "search", lambda query, *_args, **_kwargs: [
        {"id": "sample", "title": query, "url": "https://manhuaplus.org/manga/sample"}
    ])
    response = web_app.app.test_client().get("/api/sources/manhuaplus/search?q=Sample")
    assert response.status_code == 200
    assert response.json["source"] == "manhuaplus"
    assert response.json["results"][0]["url"] == "https://manhuaplus.org/manga/sample"
    listed = web_app.app.test_client().get("/api/sources")
    assert any(source["id"] == "manhuaplus" for source in listed.json["sources"])
    assert not any(source["id"] == "mangak" for source in listed.json["sources"])


def test_phone_preview_bind_host_is_private_ipv4_only():
    import app as web_app

    assert web_app.resolve_local_bind_host("127.0.0.1") == "127.0.0.1"
    assert web_app.resolve_local_bind_host("192.168.1.24") == "192.168.1.24"
    with pytest.raises(ValueError):
        web_app.resolve_local_bind_host("0.0.0.0")
    with pytest.raises(ValueError):
        web_app.resolve_local_bind_host("8.8.8.8")


def test_mangadex_search_does_not_filter_by_origin_country(monkeypatch):
    from urllib.parse import parse_qs, urlparse

    provider = MangaDexProvider()
    requested = {}

    def fake_fetch(url, _safe_fetch):
        requested.update(parse_qs(urlparse(url).query))
        return {"data": []}

    monkeypatch.setattr(provider, "_fetch_json", fake_fetch)
    assert provider.search("Sample title", lambda *_args: None) == []
    assert "originalLanguage[]" not in requested
    assert requested["title"] == ["Sample title"]


def test_mangadex_chapter_load_validates_api_image_host_and_keeps_credits():
    provider = MangaDexProvider()
    assert provider.can_handle("https://mangadex.org/chapter/12345678-1234-1234-1234-123456789abc")
    assert not provider.can_handle("https://notmangadex.org/chapter/12345678-1234-1234-1234-123456789abc")
    chapter_id = "12345678-1234-1234-1234-123456789abc"
    payloads = iter([
        {"data": {"attributes": {"translatedLanguage": "en", "chapter": "4"}, "relationships": [
            {"type": "manga", "attributes": {
                "title": {"en": "Example"}, "originalLanguage": "zh",
            }},
            {"type": "scanlation_group", "id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "attributes": {"name": "Group"}},
        ]}},
        {"baseUrl": "https://uploads.mangadex.org", "chapter": {"hash": "abc", "data": ["01.jpg"]}},
    ])
    def fetch(url, hosts, limit):
        assert hosts == frozenset({"api.mangadex.org"})
        class Response:
            def json(self): return next(payloads)
        return Response()
    result = provider.load_chapter(f"https://mangadex.org/chapter/{chapter_id}", fetch)
    assert result["title"] == "Example — Chapter 4"
    assert result["series_type"] == "manhua"
    assert result["original_language"] == "zh"
    assert result["scanlation_group_ids"] == ["aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"]
    assert result["attributions"][0]["name"] == "MangaDex"
    assert result["pages"][0]["image"] == "https://uploads.mangadex.org/data/abc/01.jpg"


def test_mangadex_rejects_untrusted_image_host():
    provider = MangaDexProvider()
    chapter_id = "12345678-1234-1234-1234-123456789abc"
    payloads = iter([
        {"data": {"attributes": {"translatedLanguage": "en"}, "relationships": [
            {"type": "manga", "attributes": {"originalLanguage": "ko"}},
        ]}},
        {"baseUrl": "https://attacker.example", "chapter": {"hash": "abc", "data": ["01.jpg"]}},
    ])
    class Response:
        def json(self): return next(payloads)
    with pytest.raises(ValueError, match="شبكة الصور"):
        provider.load_chapter(f"https://mangadex.org/chapter/{chapter_id}", lambda *args: Response())


def test_mangadex_search_connection_failure_has_actionable_message(monkeypatch):
    import requests
    import app as web_app
    provider = web_app.providers.by_id("mangadex")
    monkeypatch.setattr(provider, "search", lambda *args, **kwargs: (_ for _ in ()).throw(requests.ConnectionError("WinError 10013")))
    response = web_app.app.test_client().get("/api/sources/mangadex/search?q=example")
    assert response.status_code == 503
    assert "جدار حماية Windows" in response.json["error"]
    assert "WinError" not in response.json["error"]


def test_ocr_lines_are_grouped_before_online_translation(monkeypatch):
    import io
    from PIL import Image
    import app as web_app
    image = io.BytesIO(); Image.new("RGB", (64, 64), "white").save(image, format="PNG")
    class Response:
        headers = {"Content-Type": "image/png"}
        content = image.getvalue()
        def raise_for_status(self): pass
    monkeypatch.setattr(web_app, "public_https_url", lambda _url: True)
    monkeypatch.setattr(web_app, "safe_get", lambda *args, **kwargs: Response())
    monkeypatch.setattr(web_app, "run_local_ocr", lambda *args, **kwargs: {
        "text": "I will\nprotect you.", "lines": [
            {"text":"I will", "left":10, "top":10, "width":35, "height":10},
            {"text":"protect you.", "left":8, "top":23, "width":48, "height":10},
        ], "provider":"test", "elapsed_seconds":0.01,
    })
    calls = []
    monkeypatch.setattr(web_app, "translate_text", lambda text, *args, **kwargs: calls.append(text) or "سأحميك.")
    response = web_app.app.test_client().post("/api/translate", json={
            "image":"https://images.example/page.png", "referer":"https://manhuaplus.org/manga/test/chapter-1", "chapter_title":"Test", "reading_layout":"vertical"
    })
    assert response.status_code == 200
    assert calls == ["I will protect you."]
    assert len(response.json["translated_lines"]) == 1
    assert response.json["translated_lines"][0]["translated"] == "سأحميك."


def test_remote_translation_preview_returns_image(monkeypatch):
    import io
    from PIL import Image
    import app as web_app
    image = io.BytesIO(); Image.new("RGB", (80, 80), "white").save(image, format="PNG")
    class Response:
        headers = {"Content-Type": "image/png"}
        content = image.getvalue()
    monkeypatch.setattr(web_app, "public_https_url", lambda _url: True)
    monkeypatch.setattr(web_app, "safe_get", lambda *args, **kwargs: Response())
    png = image.getvalue()
    monkeypatch.setattr(web_app, "render_translated_page", lambda *args, **kwargs: (png, {"rendered_regions":1,"skipped_regions":0}))
    client = web_app.app.test_client()
    response = client.post("/api/translate/render", json={
        "image":"https://images.example/page.png", "referer":"https://manhuaplus.org/manga/test/chapter-1",
        "reading_layout":"vertical",
        "regions":[{"left":10,"top":10,"width":20,"height":12,"translated":"مرحبا"}],
    })
    assert response.status_code == 200
    image_response = client.get(response.json["preview_url"])
    assert image_response.status_code == 200
    assert image_response.mimetype == "image/png"


def test_user_reported_ocr_fragments_group_and_get_reviewed_arabic(monkeypatch, tmp_path):
    import io
    from PIL import Image
    import app as web_app
    monkeypatch.setenv("MANGA_LIBRARY_DIR", str(tmp_path / "library"))
    monkeypatch.setattr(web_app, "CURRENT_TRANSLATION_ENGINE", "qwen3")
    monkeypatch.setattr(web_app, "QWEN_TRANSLATOR", None)
    image = io.BytesIO(); Image.new("RGB", (1000, 700), "white").save(image, format="PNG")
    class Response:
        headers = {"Content-Type": "image/png"}
        content = image.getvalue()
        def raise_for_status(self): pass
    monkeypatch.setattr(web_app, "public_https_url", lambda _url: True)
    monkeypatch.setattr(web_app, "safe_get", lambda *args, **kwargs: Response())
    monkeypatch.setattr(web_app, "run_local_ocr", lambda *args, **kwargs: {
        "text":"KEEP UP THE\nCONGRATULATIONS ON DEFEATING THE FLOOR BOSS, EUGENE.\nGOOD WORK AND CONTINUE PROGRESSING! S",
        "lines":[
            {"text":"KEEP UP THE", "left":231, "top":176, "width":169, "height":43, "confidence":0.9},
            {"text":"CONGRATULATIONS ON DEFEATING THE FLOOR BOSS, EUGENE.", "left":663, "top":208, "width":239, "height":113, "confidence":0.9},
            {"text":"GOOD WORK AND CONTINUE PROGRESSING! S", "left":219, "top":211, "width":194, "height":139, "confidence":0.8},
            {"text":"YOU'VE BEEN LATE AFTER BEING LATE AFTER BEING", "left":126, "top":274, "width":119, "height":113, "confidence":0.9},
            {"text":"LATE!", "left":152, "top":382, "width":63, "height":28, "confidence":0.9},
        ], "provider":"test", "elapsed_seconds":0.01,
    })
    response = web_app.app.test_client().post("/api/translate", json={
        "image":"https://images.example/page.png", "referer":"https://manhuaplus.org/manga/test/chapter-1", "chapter_title":"Test", "reading_layout":"vertical"
    })
    assert response.status_code == 200
    assert len(response.json["translated_lines"]) == 3
    assert response.json["translated_lines"][0]["text"] == "KEEP UP THE GOOD WORK AND CONTINUE PROGRESSING! S"
    assert response.json["translated_lines"][0]["translated"] == "أحسنت، واصل التقدم!"
    assert response.json["translated_lines"][1]["translated"] == "تهانينا يا يوجين على هزيمتك لزعيم الطابق!"
    assert response.json["translated_lines"][2]["text"] == "YOU'VE BEEN LATE AFTER BEING LATE AFTER BEING LATE!"
    assert response.json["translated_lines"][2]["translated"] == "تتأخرين مرةً تلو الأخرى!"
    assert web_app.QWEN_TRANSLATOR is None


def test_external_ocr_review_hint_survives_translate_api(monkeypatch, tmp_path):
    import io
    from PIL import Image
    import app as web_app

    monkeypatch.setenv("MANGA_LIBRARY_DIR", str(tmp_path / "library"))
    monkeypatch.setattr(web_app, "CURRENT_TRANSLATION_ENGINE", "index_translate_9b")
    image = io.BytesIO()
    Image.new("RGB", (321, 121), "#d9e3ec").save(image, format="PNG")

    class Response:
        headers = {"Content-Type": "image/png"}
        content = image.getvalue()

        def raise_for_status(self):
            pass

    region = {
        "text": "PURO-ASE", "left": 20, "top": 30, "width": 180, "height": 24,
        "confidence": 0.889, "region_type": "external_text",
        "ocr_review_required": True,
        "ocr_review_reason": "unrecognized_hyphenated_ocr_token",
        "ocr_unrecognized_word_count": 2,
        "ocr_suspicious_near_words": [
            {"observed": "PUROASE", "candidate": "PURPOSE", "frequency": 0.001},
        ],
    }
    monkeypatch.setattr(web_app, "public_https_url", lambda _url: True)
    monkeypatch.setattr(web_app, "safe_get", lambda *args, **kwargs: Response())
    monkeypatch.setattr(web_app, "run_local_ocr", lambda *args, **kwargs: {
        "text": "PURO-ASE", "lines": [{key: region[key] for key in ("text", "left", "top", "width", "height", "confidence")}],
        "regions": [region], "provider": "test", "elapsed_seconds": 0.01,
    })
    monkeypatch.setattr(web_app, "_translate_index_region_group", lambda _unit: ["\u062a\u0631\u062c\u0645\u0629"])

    response = web_app.app.test_client().post("/api/translate", json={
        "image": "https://images.example/ocr-review-hint.png",
        "referer": "https://manhuaplus.org/manga/test/chapter-ocr-review",
        "chapter_title": "OCR review integration", "reading_layout": "vertical",
    })

    assert response.status_code == 200
    translated = response.json["translated_lines"][0]
    assert translated["ocr_review_required"] is True
    assert translated["ocr_review_reason"] == "unrecognized_hyphenated_ocr_token"
    assert translated["ocr_unrecognized_word_count"] == 2
    assert translated["ocr_suspicious_near_words"] == [
        {"observed": "PUROASE", "candidate": "PURPOSE", "frequency": 0.001},
    ]


def test_remote_page_returns_completed_regions_and_progress_after_model_timeout(monkeypatch):
    import io
    from PIL import Image
    import requests
    import app as web_app

    # This test validates checkpoint behavior for individual region failures;
    # keep it independent of a locally running Index server or batch formatting.
    monkeypatch.setattr(web_app, "_index_batch_size", lambda: 1)

    image = io.BytesIO()
    Image.new("RGB", (100, 100), "#d7e0eb").save(image, format="PNG")

    class Response:
        headers = {"Content-Type": "image/png"}
        content = image.getvalue()
        def raise_for_status(self): pass

    regions = [
        {"text": f"Bubble {index}", "left": 5, "top": index * 20, "width": 40, "height": 14,
         "region_type": "speech_bubble"}
        for index in (1, 2, 3)
    ]
    lines = [{key: value for key, value in region.items() if key in {"text", "left", "top", "width", "height"}}
             for region in regions]
    monkeypatch.setattr(web_app, "public_https_url", lambda _url: True)
    monkeypatch.setattr(web_app, "safe_get", lambda *args, **kwargs: Response())
    monkeypatch.setattr(web_app, "run_local_ocr", lambda *args, **kwargs: {
        "text": "Bubble 1\\nBubble 2\\nBubble 3", "lines": lines, "regions": regions,
        "provider": "test", "elapsed_seconds": 0.01,
    })
    calls = []
    def translate(text, *args, **kwargs):
        calls.append(text)
        if len(calls) == 2:
            raise requests.Timeout("model request timed out")
        return "ترجمة عربية"
    monkeypatch.setattr(web_app, "translate_text", translate)

    job_id = "a" * 32
    client = web_app.app.test_client()
    response = client.post("/api/translate", json={
        "image": "https://images.example/page.png",
        "referer": "https://manhuaplus.org/manga/test/chapter-1",
        "job_id": job_id,
        "reading_layout": "vertical",
    })

    assert response.status_code == 200
    assert response.json["processing_incomplete"] is True
    assert response.json["translated_lines"][0]["translated"] == "ترجمة عربية"
    assert "model request timed out" in response.json["translated_lines"][1]["translation_error"]
    assert "لم تبدأ هذه المنطقة" in response.json["translated_lines"][2]["translation_error"]
    assert calls == ["Bubble 1", "Bubble 2"]
    progress = client.get(f"/api/translate/progress/{job_id}")
    assert progress.status_code == 200
    assert progress.json["progress"]["stage"] == "partial"
    assert progress.json["progress"]["completed_regions"] == 1
    assert progress.json["progress"]["current_region_elapsed_seconds"] is None
