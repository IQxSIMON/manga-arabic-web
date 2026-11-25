"""Contract tests for the one-request full-chapter translation endpoint."""

import json

import app as web_app


def _chapter_payload():
    return {
        "chapter_title": "Magic Emperor - Chapter 939",
        "cloud_ocr": False,
        "regions": [
            {"id": "r1", "text": "You dare challenge me?", "region_type": "dialogue",
             "page_image": "https://aquamanga.io/wp-content/uploads/WP-manga/data/abc/01.webp"},
            {"id": "r2", "text": "Meanwhile, far away...", "region_type": "external_text",
             "page_image": "https://aquamanga.io/wp-content/uploads/WP-manga/data/abc/02.webp"},
        ],
    }


class FakeResponse:
    status_code = 200
    ok = True

    def __init__(self, translations):
        text = json.dumps({"translations": translations}, ensure_ascii=False)
        part = {"text": text}
        candidate = {"content": {"parts": [part]}}
        self._payload = {"candidates": [candidate], "usageMetadata": {"totalTokenCount": 321}}

    def json(self):
        return self._payload


def test_translate_chapter_sends_exactly_one_gemini_request_with_all_regions(monkeypatch):
    web_app.app.config["TESTING"] = True
    client = web_app.app.test_client()
    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append({"url": url, "json": json, "key": headers.get("x-goog-api-key")})
        return FakeResponse([
            {"id": "r1", "translation": "أتجرؤ على تحديّي؟"},
            {"id": "r2", "translation": "وفي مكان بعيد..."},
        ])

    monkeypatch.setattr(web_app, "_gemini_api_key_from_local_settings", lambda: "test-key")
    monkeypatch.setattr("app.requests.post", fake_post)
    response = client.post("/api/translate/chapter", json=_chapter_payload())
    assert response.status_code == 200
    body = response.get_json()
    assert len(calls) == 1
    assert body["translation_requests_sent"] == 1
    prompt = calls[0]["json"]["contents"][0]["parts"][0]["text"]
    sent_regions = json.loads(prompt.split("REGIONS:\n", 1)[1])
    assert [item["id"] for item in sent_regions] == ["r1", "r2"]
    assert "Arabic" in prompt
    assert calls[0]["key"] == "test-key"
    translations = {item["id"]: item["translated"] for item in body["translations"]}
    assert translations == {"r1": "أتجرؤ على تحديّي؟", "r2": "وفي مكان بعيد..."}
    assert body["missing_ids"] == []
    assert body["unknown_ids"] == []


def test_translate_chapter_requires_a_key_and_reports_missing_ids(monkeypatch):
    web_app.app.config["TESTING"] = True
    client = web_app.app.test_client()
    monkeypatch.setattr(web_app, "_gemini_api_key_from_local_settings", lambda: "")
    response = client.post("/api/translate/chapter", json=_chapter_payload())
    assert response.status_code == 503

    monkeypatch.setattr(web_app, "_gemini_api_key_from_local_settings", lambda: "test-key")
    monkeypatch.setattr(
        "app.requests.post",
        lambda *args, **kwargs: FakeResponse([{"id": "r1", "translation": "تم"}]),
    )
    body = client.post("/api/translate/chapter", json=_chapter_payload()).get_json()
    assert body["missing_ids"] == ["r2"]
    assert {item["id"] for item in body["translations"]} == {"r1", "r2"}


def test_translate_chapter_caps_region_count_and_text_length(monkeypatch):
    web_app.app.config["TESTING"] = True
    client = web_app.app.test_client()
    monkeypatch.setattr(web_app, "_gemini_api_key_from_local_settings", lambda: "test-key")
    # Region count is capped before any network call.
    too_many = {"chapter_title": "x", "regions": [{"id": f"r{i}", "text": "hi"} for i in range(1201)]}
    assert client.post("/api/translate/chapter", json=too_many).status_code == 400
    # Long text is truncated to 4000 characters per region, not rejected.
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["prompt"] = json["contents"][0]["parts"][0]["text"]
        return FakeResponse([{"id": "r1", "translation": "تم"}])

    monkeypatch.setattr("app.requests.post", fake_post)
    long_text = {"chapter_title": "x", "cloud_ocr": False, "regions": [{"id": "r1", "text": "a" * 20000}]}
    response = client.post("/api/translate/chapter", json=long_text)
    assert response.status_code == 200
    sent = json.loads(captured["prompt"].split("REGIONS:\n", 1)[1])
    assert sent[0]["text"] == "a" * 4000
