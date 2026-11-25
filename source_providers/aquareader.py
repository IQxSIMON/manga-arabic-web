"""Small, independent HTML adapter for the Aqua Reader / Aqua Manga site.

This adapter is implemented from the site's public page structure (a Madara
theme child with a custom reader); it does not import Kotatsu/Tachiyomi parser
code.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .base import is_https_host


class AquaReaderProvider:
    provider_id = "aquareader"
    display_name = "Aqua Reader (تجريبي)"
    hosts = frozenset({"aquareader.org", "aquamanga.io", "www.aquareader.org", "www.aquamanga.io"})
    base_url = "https://aquamanga.io"
    supports_title_search = True
    supports_chapter_listing = True
    supports_direct_chapter_import = True
    chapter_pattern = re.compile(r"/chapter-\d+", re.I)
    image_extensions = (".jpg", ".jpeg", ".png", ".webp", ".avif")

    def can_handle(self, url: str) -> bool:
        return is_https_host(url, self.hosts) and urlparse(url).path.startswith("/manga/")

    def _fetch(self, url: str, safe_fetch):
        return safe_fetch(url, self.hosts, 6 * 1024 * 1024)

    @staticmethod
    def _title(anchor, fallback="Untitled"):
        if anchor is None:
            return fallback
        heading = anchor.select_one("h3, h4, h5, .h5, .manga-title")
        if heading is not None:
            return heading.get_text(" ", strip=True)
        return anchor.get_text(" ", strip=True) or anchor.get("title", "") or fallback

    @staticmethod
    def _chapter_label(anchor, path: str) -> str:
        if anchor is not None:
            text = anchor.get_text(" ", strip=True)
            if text:
                return text
        return path.rstrip("/").rsplit("/", 1)[-1].replace("-", " ").title()

    def search(self, query: str, safe_fetch, limit: int = 20) -> list[dict]:
        query = " ".join(query.split())[:120]
        if len(query) < 2:
            return []
        response = self._fetch(f"{self.base_url}/?s={query.replace(' ', '+')}&post_type=wp-manga", safe_fetch)
        soup = BeautifulSoup(response.text, "html.parser")
        results, seen = [], set()
        for anchor in soup.select('a[href*="/manga/"]'):
            href = urljoin(response.url, anchor.get("href", ""))
            parsed = urlparse(href)
            if parsed.scheme != "https" or (parsed.hostname or "").lower() not in self.hosts:
                continue
            path = parsed.path.rstrip("/")
            if not path.startswith("/manga/") or self.chapter_pattern.search(path) or path in seen:
                continue
            seen.add(path)
            results.append({
                "id": path.removeprefix("/manga/"),
                "title": self._title(anchor),
                "url": f"{self.base_url}{path}",
            })
            if len(results) >= min(max(limit, 1), 30):
                break
        return results

    def chapters(self, work_id: str, safe_fetch, limit: int = 100) -> list[dict]:
        slug = str(work_id).strip().strip("/")
        if not re.fullmatch(r"[A-Za-z0-9_%.-]{1,180}", slug):
            raise ValueError("معرّف العمل في Aqua Reader غير صالح.")
        url = f"{self.base_url}/manga/{slug}"
        response = self._fetch(url, safe_fetch)
        soup = BeautifulSoup(response.text, "html.parser")
        chapters, seen = [], set()
        for anchor in soup.select('a[href*="/chapter-"]'):
            href = urljoin(response.url, anchor.get("href", ""))
            parsed = urlparse(href)
            if parsed.scheme != "https" or (parsed.hostname or "").lower() not in self.hosts:
                continue
            if not self.chapter_pattern.search(parsed.path):
                continue
            normalized = f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/')}"
            if normalized in seen:
                continue
            seen.add(normalized)
            label = self._chapter_label(anchor, parsed.path)
            chapters.append({"id": normalized.rsplit("/", 1)[-1], "chapter": label, "title": "", "url": normalized})
            if len(chapters) >= min(max(limit, 1), 300):
                break
        if not chapters:
            raise ValueError("لم يعثر Aqua Reader على فصول في صفحة العمل.")
        return chapters

    def parse_chapter(self, html: str, page_url: str) -> dict:
        soup = BeautifulSoup(html, "html.parser")
        reader = soup.select_one(".reading-content, .reading-content-wrap, #readerarea") or soup
        tags = reader.select("img.wp-manga-page-image") or reader.select(".reading-content img, #readerarea img")
        images, seen = [], set()
        for tag in tags:
            for attr in ("src", "data-src", "data-lazy-src", "data-original"):
                value = tag.get(attr)
                if not value:
                    continue
                absolute = urljoin(page_url, value.strip())
                parsed = urlparse(absolute)
                if parsed.scheme != "https" or not parsed.hostname:
                    continue
                if absolute not in seen and any(
                    parsed.path.lower().split("?")[0].endswith(ext) for ext in self.image_extensions
                ):
                    seen.add(absolute)
                    images.append(absolute)
        if not images:
            raise ValueError("لم يعثر Aqua Reader على صور الفصل في صفحة القارئ.")
        heading = soup.select_one("#chapter-heading, h1")
        chapter_title = heading.get_text(" ", strip=True) if heading else (
            soup.title.get_text(" ", strip=True) if soup.title else "Aqua Reader chapter"
        )
        series_anchor = soup.select_one('.c-breadcrumb a[href*="/manga/"], a[href*="/manga/"]:not([href*="/chapter-"])')
        series_title = self._title(series_anchor, chapter_title) if series_anchor else chapter_title
        return {
            "title": chapter_title,
            "series_title": series_title,
            "series_key": f"aquareader:{urlparse(page_url).path.split('/')[2] if len(urlparse(page_url).path.split('/')) > 2 else series_title.casefold()}",
            "source": self.provider_id,
            "source_url": page_url,
            "pages": [{"index": index, "image": image} for index, image in enumerate(images, 1)],
        }

    def load_chapter(self, url: str, safe_fetch) -> dict:
        if not self.can_handle(url) or not self.chapter_pattern.search(urlparse(url).path):
            raise ValueError("رابط فصل Aqua Reader يجب أن يكون من aquareader.org أو aquamanga.io ويحتوي مسار الفصل.")
        response = self._fetch(url, safe_fetch)
        return self.parse_chapter(response.text, response.url)
