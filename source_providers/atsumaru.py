"""Public chapter JSON adapter for Atsumaru (atsu.moe)."""

from __future__ import annotations

import re
from urllib.parse import urlencode, urlparse

from .base import is_https_host


class AtsumaruProvider:
    provider_id = "atsumaru"
    display_name = "Atsumaru (Atsu.moe)"
    hosts = frozenset({"atsu.moe", "www.atsu.moe", "cdn.atsu.moe"})
    page_hosts = frozenset({"atsu.moe", "www.atsu.moe"})
    cdn_host = "cdn.atsu.moe"
    supports_title_search = False
    supports_chapter_listing = False
    supports_direct_chapter_import = True
    search_note = "الصق رابط /read/ المباشر لفصل عام متاح على Atsumaru."
    chapter_path = re.compile(r"^/read/([A-Za-z0-9_-]{1,80})/([A-Za-z0-9_-]{1,80})/?$")

    def can_handle(self, url: str) -> bool:
        parsed = urlparse(url)
        return (
            is_https_host(url, self.page_hosts)
            and self.chapter_path.fullmatch(parsed.path) is not None
        )

    def load_chapter(self, url: str, safe_fetch) -> dict:
        parsed = urlparse(url)
        match = self.chapter_path.fullmatch(parsed.path)
        if not match or parsed.query:
            raise ValueError("استخدم رابط قراءة فصل Atsumaru بصيغة /read/{work}/{chapter}.")
        work_id, chapter_id = match.groups()
        query = urlencode({"mangaId": work_id, "chapterId": chapter_id})
        api_url = f"https://atsu.moe/api/read/chapter?{query}"
        response = safe_fetch(api_url, self.page_hosts, 6 * 1024 * 1024)
        payload = response.json()
        chapter = payload.get("readChapter") or {}
        pages_data = chapter.get("pages") or []
        if not isinstance(pages_data, list) or not pages_data or len(pages_data) > 300:
            raise ValueError("لم يعثر Atsumaru على صفحات فصل عامة صالحة (الحد 300 صفحة).")
        pages = []
        for page in pages_data:
            if not isinstance(page, dict):
                continue
            path = page.get("image")
            if not isinstance(path, str) or not path.startswith("/static/pages/") or ".." in path.split("/"):
                raise ValueError("أعاد Atsumaru مسار صورة غير صالح.")
            page_entry = {
                "index": len(pages) + 1,
                "image": f"https://{self.cdn_host}{path}",
            }
            ratio = page.get("aspectRatio")
            try:
                ratio = float(ratio)
            except (TypeError, ValueError):
                ratio = None
            if ratio and 0 < ratio < 10:
                page_entry["aspect_ratio"] = ratio
            pages.append(page_entry)
        if not pages:
            raise ValueError("لم يعثر Atsumaru على صفحات قابلة للقراءة.")

        title = str(chapter.get("title") or f"Chapter {chapter.get('number', '')}").strip()
        return {
            "title": f"{title} — Atsumaru",
            "series_title": f"Atsumaru {work_id}",
            "series_key": f"atsumaru:{work_id}",
            "source": self.provider_id,
            "source_url": url,
            "pages": pages,
        }
