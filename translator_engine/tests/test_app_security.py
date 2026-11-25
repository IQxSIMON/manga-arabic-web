import requests

import app as web_app


def test_public_https_url_rejects_local_and_credential_urls():
    assert not web_app.public_https_url("https://127.0.0.1/private")
    assert not web_app.public_https_url("https://user:pass@example.com/image.jpg")
    assert not web_app.public_https_url("https://example.com:8443/image.jpg")


def test_safe_get_rechecks_redirect_destination(monkeypatch):
    monkeypatch.setattr(
        web_app.socket, "getaddrinfo",
        lambda *args, **kwargs: [(None, None, None, None, ("1.1.1.1", 443))],
    )
    response = requests.Response()
    response.status_code = 302
    response._content = b""
    response._content_consumed = True
    response.headers["Location"] = "http://127.0.0.1/admin"
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        return response

    monkeypatch.setattr(web_app.HTTP, "get", fake_get)
    try:
        web_app.safe_get("https://public.example/image.jpg")
    except ValueError as exc:
        assert "غير آمن" in str(exc)
    else:
        raise AssertionError("Private redirect destination was accepted")
    assert calls == ["https://public.example/image.jpg"]


def test_image_proxy_rejects_loopback_without_fetching(monkeypatch):
    monkeypatch.setattr(
        web_app.HTTP, "get",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network request attempted")),
    )
    client = web_app.app.test_client()
    response = client.get(
        "/api/image?url=https%3A%2F%2F127.0.0.1%2Fsecret&referer=https%3A%2F%2Fmangak.io%2Fchapter"
    )
    assert response.status_code == 400


def test_safe_get_bounds_download_size(monkeypatch):
    monkeypatch.setattr(
        web_app.socket, "getaddrinfo",
        lambda *args, **kwargs: [(None, None, None, None, ("1.1.1.1", 443))],
    )
    response = requests.Response()
    response.status_code = 200
    response._content = b""
    response._content_consumed = True
    response.iter_content = lambda chunk_size: iter([b"12345"])
    monkeypatch.setattr(web_app.HTTP, "get", lambda *args, **kwargs: response)
    try:
        web_app.safe_get("https://public.example/image.jpg", max_bytes=4)
    except ValueError as exc:
        assert "الحد المسموح" in str(exc)
    else:
        raise AssertionError("Oversized response was accepted")


def test_source_block_api_persists_mangadex_group(monkeypatch, tmp_path):
    monkeypatch.setenv("MANGA_LIBRARY_DIR", str(tmp_path / "library"))
    client = web_app.app.test_client()
    group_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    response = client.post("/api/source-blocks", json={"source_id": group_id, "source_name": "Group"})
    assert response.status_code == 201
    assert response.json["blocks"][0]["source_id"] == group_id
    assert web_app.library_store.is_source_blocked("mangadex", group_id)


def test_source_block_api_rejects_invalid_id():
    response = web_app.app.test_client().post("/api/source-blocks", json={"source_id": "../private", "source_name": "x"})
    assert response.status_code == 400


def test_local_server_rejects_unrecognized_host_header():
    response = web_app.app.test_client().get("/", headers={"Host": "attacker.example"})

    assert response.status_code == 400


def test_local_server_allows_each_configured_preview_host(monkeypatch):
    monkeypatch.setenv(
        "PREVIEW_ALLOWED_HOST", "sb-session.vercel.run, preview-check.vercel.run"
    )
    client = web_app.app.test_client()

    assert client.get("/", headers={"Host": "sb-session.vercel.run"}).status_code == 200
    assert client.get("/", headers={"Host": "preview-check.vercel.run"}).status_code == 200
    assert client.get("/", headers={"Host": "other.vercel.run"}).status_code == 400


def test_local_write_routes_reject_cross_origin_requests(monkeypatch, tmp_path):
    monkeypatch.setenv("MANGA_LIBRARY_DIR", str(tmp_path / "library"))
    client = web_app.app.test_client()

    response = client.post(
        "/api/source-blocks",
        json={"source_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "source_name": "Group"},
        headers={"Origin": "http://attacker.example"},
    )

    assert response.status_code == 403
    assert not web_app.library_store.is_source_blocked(
        "mangadex", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    )


def test_local_write_routes_allow_same_origin_over_configured_private_ip(monkeypatch, tmp_path):
    monkeypatch.setenv("MANGA_LIBRARY_DIR", str(tmp_path / "library"))
    monkeypatch.setenv("MANGA_BIND_HOST", "192.168.50.2")
    client = web_app.app.test_client()

    response = client.post(
        "/api/source-blocks",
        base_url="http://192.168.50.2:5000",
        json={"source_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", "source_name": "Group"},
        headers={"Origin": "http://192.168.50.2:5000"},
    )

    assert response.status_code == 201


def test_source_import_stores_validated_pages_locally(monkeypatch, tmp_path):
    import io
    from PIL import Image
    monkeypatch.setenv("MANGA_LIBRARY_DIR", str(tmp_path / "library"))
    image = io.BytesIO(); Image.new("RGB", (32, 32), "white").save(image, format="PNG")
    class Provider:
        provider_id = "test"
        def load_chapter(self, url, fetch):
            return {"title": "Test — Chapter 1", "source_url": url, "pages": [{"index": 1, "image": "https://example.org/page.png"}]}
    class Response:
        headers = {"Content-Type": "image/png"}
        content = image.getvalue()
    monkeypatch.setattr(web_app.providers, "for_url", lambda _url: Provider())
    monkeypatch.setattr(web_app, "safe_get", lambda *args, **kwargs: Response())
    response = web_app.app.test_client().post("/api/source-import", json={
        "url": "https://example.org/chapter", "reading_layout": "vertical",
    })
    assert response.status_code == 201
    chapter = response.json["chapter"]
    assert chapter["page_count"] == 1
    assert web_app.library_store.read_page(chapter["id"], 1) is not None
