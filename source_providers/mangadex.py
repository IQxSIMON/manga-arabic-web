"""MangaDex v5 API adapter for English chapter discovery and page URLs."""

from __future__ import annotations

import re
from urllib.parse import urlencode, urlparse

from .base import is_https_host


class MangaDexProvider:
    provider_id = "mangadex"
    display_name = "MangaDex"
    hosts = frozenset({"mangadex.org", "www.mangadex.org", "api.mangadex.org"})
    supports_title_search = True
    supports_chapter_listing = True
    supports_direct_chapter_import = True
    search_note = "بحث الأعمال ذات الفصول الإنجليزية؛ تحقّق من نمطها الطولي قبل الاستيراد."
    api = "https://api.mangadex.org"
    chapter_id_pattern = re.compile(r"/chapter/([0-9a-f-]{36})(?:/|$)", re.IGNORECASE)

    def can_handle(self, url: str) -> bool:
        return is_https_host(url, frozenset({"mangadex.org", "www.mangadex.org"}))

    def _fetch_json(self, url, safe_fetch):
        response = safe_fetch(url, frozenset({"api.mangadex.org"}), 4 * 1024 * 1024)
        return response.json()

    @staticmethod
    def _english_title(attributes):
        titles = attributes.get("title") or {}
        return titles.get("en") or next(iter(titles.values()), "Untitled")

    def search(self, query: str, safe_fetch, limit: int = 20) -> list[dict]:
        query = " ".join(query.split())[:120]
        if len(query) < 2:
            return []
        params = [
            ("title", query), ("limit", str(min(max(limit, 1), 20))),
            ("includes[]", "cover_art"), ("contentRating[]", "safe"),
            ("contentRating[]", "suggestive"),
        ]
        payload = self._fetch_json(f"{self.api}/manga?{urlencode(params)}", safe_fetch)
        results = []
        for item in payload.get("data", []):
            attributes = item.get("attributes") or {}
            cover = next((rel for rel in item.get("relationships", []) if rel.get("type") == "cover_art"), None)
            results.append({
                "id": item.get("id"),
                "title": self._english_title(attributes),
                "description": (attributes.get("description") or {}).get("en", ""),
                "status": attributes.get("status", "unknown"),
                "url": f"https://mangadex.org/title/{item.get('id')}",
                "cover_filename": (cover or {}).get("attributes", {}).get("fileName"),
            })
        return results

    def chapters(self, manga_id: str, safe_fetch, limit: int = 100) -> list[dict]:
        if not re.fullmatch(r"[0-9a-f-]{36}", manga_id, re.IGNORECASE):
            raise ValueError("معرّف MangaDex غير صالح.")
        manga = self._fetch_json(f"{self.api}/manga/{manga_id}", safe_fetch).get("data") or {}
        params = [
            ("translatedLanguage[]", "en"), ("limit", str(min(max(limit, 1), 100))),
            ("order[chapter]", "desc"), ("includes[]", "scanlation_group"),
        ]
        payload = self._fetch_json(f"{self.api}/manga/{manga_id}/feed?{urlencode(params)}", safe_fetch)
        chapters = []
        for item in payload.get("data", []):
            attributes = item.get("attributes") or {}
            if attributes.get("translatedLanguage") != "en":
                continue
            groups = [
                {"id": rel.get("id"), "name": (rel.get("attributes") or {}).get("name", "Unknown group")}
                for rel in item.get("relationships", []) if rel.get("type") == "scanlation_group"
            ]
            if any(group["id"] in getattr(self, "blocked_group_ids", set()) for group in groups):
                continue
            chapters.append({
                "id": item.get("id"),
                "chapter": attributes.get("chapter") or "?",
                "title": attributes.get("title") or "",
                "volume": attributes.get("volume") or "",
                "groups": groups,
                "url": f"https://mangadex.org/chapter/{item.get('id')}",
            })
        return chapters

    def load_chapter(self, url: str, safe_fetch) -> dict:
        match = self.chapter_id_pattern.search(url)
        if not match:
            raise ValueError("رابط فصل MangaDex يجب أن يحتوي معرّف الفصل.")
        chapter_id = match.group(1)
        include = urlencode([("includes[]", "manga"), ("includes[]", "scanlation_group")])
        detail = self._fetch_json(f"{self.api}/chapter/{chapter_id}?{include}", safe_fetch).get("data") or {}
        attributes = detail.get("attributes") or {}
        if attributes.get("translatedLanguage") != "en":
            raise ValueError("المصدر الحالي يحمّل فصول MangaDex الإنجليزية فقط.")
        manga = next((r for r in detail.get("relationships", []) if r.get("type") == "manga"), {})
        manga_attributes = manga.get("attributes") or {}
        image_server = self._fetch_json(f"{self.api}/at-home/server/{chapter_id}", safe_fetch)
        base_url = image_server.get("baseUrl", "").rstrip("/")
        chapter_data = image_server.get("chapter") or {}
        parsed_base = urlparse(base_url)
        base_host = (parsed_base.hostname or "").lower()
        if base_host != "uploads.mangadex.org" and not base_host.endswith(".mangadex.network"):
            raise ValueError("أعاد MangaDex خادماً لا ينتمي إلى شبكة الصور المعروفة.")
        if parsed_base.scheme != "https" or parsed_base.username or parsed_base.password:
            raise ValueError("رابط خادم MangaDex غير آمن.")
        filenames = chapter_data.get("data") or []
        content_hash = chapter_data.get("hash")
        if not filenames and attributes.get("externalUrl"):
            raise ValueError(
                "هذا الفصل مستضاف لدى الناشر ولا يستضيفه MangaDex (صفحة ويب خارجية)، "
                "لذا لا يمكن استيراد صوره إلى المكتبة. اختر فصلاً آخر من نفس العمل."
            )
        if not content_hash or not filenames or len(filenames) > 300:
            raise ValueError("الفصل فارغ أو يتجاوز حد 300 صفحة.")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", str(content_hash)) or any(
            not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,200}", name) or name in {".", ".."}
            for name in filenames
        ):
            raise ValueError("بيانات أسماء ملفات صفحات MangaDex غير صالحة.")
        images = [f"{base_url}/data/{chapter_data['hash']}/{name}" for name in filenames]
        manga_title = self._english_title(manga_attributes)
        original_language = str(manga_attributes.get("originalLanguage") or "").lower()
        series_type = {
            "ko": "manhwa", "zh": "manhua", "zh-hk": "manhua", "ja": "manga",
        }.get(original_language, "manhwa")
        manga_id = str(manga.get("id") or "")
        chapter_number = attributes.get("chapter") or "?"
        group_credits = [
            {
                "id": rel.get("id"),
                "name": (rel.get("attributes") or {}).get("name", "Unknown group"),
                **({"url": f"https://mangadex.org/group/{rel['id']}"} if rel.get("id") else {}),
            }
            for rel in detail.get("relationships", []) if rel.get("type") == "scanlation_group"
        ]
        return {
            "title": f"{manga_title} — Chapter {chapter_number}",
            "series_title": manga_title,
            "series_key": f"mangadex:{manga_id}" if manga_id else f"mangadex-title:{manga_title.casefold()}",
            "series_type": series_type,
            "original_language": original_language or None,
            "source": self.provider_id,
            "source_url": f"https://mangadex.org/chapter/{chapter_id}",
            "attributions": [{"name": "MangaDex", "url": "https://mangadex.org"}, *group_credits],
            "scanlation_group_ids": [group["id"] for group in group_credits if group.get("id")],
            "pages": [{"index": index, "image": image} for index, image in enumerate(images, 1)],
        }
