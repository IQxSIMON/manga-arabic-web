"""Small, independent HTML adapter for vertical-comic source experiments.

This adapter is implemented from the site's public page structure; it does not
import Kotatsu/Tachiyomi parser code.
"""

from __future__ import annotations

import re
from urllib.parse import urlencode, urljoin, urlparse

from bs4 import BeautifulSoup

from .base import is_https_host


class ManhuaPlusProvider:
    provider_id = "manhuaplus"
    display_name = "ManhuaPlus.org (تجريبي)"
    hosts = frozenset({"manhuaplus.org", "www.manhuaplus.org"})
    supports_title_search = True
    supports_chapter_listing = True
    supports_direct_chapter_import = True
    search_note = "مصدر تجريبي للفصول الإنجليزية ذات التمرير العمودي؛ يعتمد الدعم على تخطيط القراءة لا بلد المنشأ."
    base_url = "https://manhuaplus.org"
    image_extensions = (".jpg", ".jpeg", ".png", ".webp", ".avif")
    chapter_pattern = re.compile(r"/chapter-[^/?#]+", re.IGNORECASE)
    chapter_id_pattern = re.compile(r"\bconst\s+CHAPTER_ID\s*=\s*['\"]?(\d+)", re.IGNORECASE)

    def can_handle(self, url: str) -> bool:
        parsed = urlparse(url)
        return is_https_host(url, self.hosts) and parsed.path.startswith("/manga/")

    def _fetch(self, url: str, safe_fetch):
        return safe_fetch(url, self.hosts, 6 * 1024 * 1024)

    @staticmethod
    def _declared_manga_type(html: str) -> str | None:
        """Read the source's explicit Type taxonomy; do not infer it from a title."""
        soup = BeautifulSoup(html, "html.parser")
        for anchor in soup.select('a[href*="/manga-type/"]'):
            path = urlparse(anchor.get("href", "")).path.rstrip("/").casefold()
            declared = path.rsplit("/", 1)[-1]
            label = " ".join(anchor.get_text(" ", strip=True).casefold().split())
            if declared and label == declared:
                return declared
        return None

    @classmethod
    def _require_supported_type(cls, html: str) -> str:
        """Check metadata so the application can record the work's known type."""
        declared_type = cls._declared_manga_type(html)
        if declared_type not in {"manhwa", "manhua", "manga"}:
            if declared_type:
                raise ValueError(f"نوع العمل المعلن في ManhuaPlus غير مدعوم: {declared_type}.")
            raise ValueError("لم يعلن ManhuaPlus نوع العمل بوضوح؛ تعذر التحقق من بياناته.")
        return declared_type

    @staticmethod
    def _title(anchor, fallback="Untitled"):
        title = anchor.get("title") or anchor.get_text(" ", strip=True)
        if not title:
            image = anchor.find("img")
            title = (image.get("alt") or image.get("title")) if image else ""
        return " ".join((title or fallback).split())[:240]

    @staticmethod
    def _chapter_label(anchor, path: str) -> str:
        label = ManhuaPlusProvider._title(anchor, "")
        if label and "read now" not in label.casefold():
            return label
        match = re.search(r"(?:^|/)chapter-([^/]+)", path, re.IGNORECASE)
        return f"Chapter {match.group(1).replace('-', '.')}" if match else (label or "Chapter")

    def search(self, query: str, safe_fetch, limit: int = 20) -> list[dict]:
        query = " ".join(query.split())[:120]
        if len(query) < 2:
            return []
        params = urlencode({"keyword": query})
        response = self._fetch(f"{self.base_url}/search?{params}", safe_fetch)
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
            raise ValueError("معرّف العمل في ManhuaPlus غير صالح.")
        url = f"{self.base_url}/manga/{slug}"
        response = self._fetch(url, safe_fetch)
        self._require_supported_type(response.text)
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
        return chapters

    def parse_chapter(self, html: str, page_url: str, chapter_html: str | None = None) -> dict:
        soup = BeautifulSoup(html, "html.parser")
        if chapter_html is not None:
            reader_soup = BeautifulSoup(chapter_html, "html.parser")
        else:
            reader_soup = soup.select_one("#readerarea, .reading-content, .chapter-content") or soup
        entries = reader_soup.select(".separator[data-index], .page-break[data-index]")
        images, seen = [], set()
        # The site's AJAX response may not preserve DOM order, so honor its page index.
        entries.sort(key=lambda item: int(item.get("data-index", "0")) if str(item.get("data-index", "0")).isdigit() else 0)
        for entry in entries or [reader_soup]:
            for tag in entry.find_all(["img", "source", "a"]):
                values = [tag.get(attr) for attr in ("src", "data-src", "data-lazy-src", "data-original", "data-url", "href")]
                srcset = tag.get("srcset")
                if srcset:
                    values.extend(item.strip().split()[0] for item in srcset.split(",") if item.strip())
                for value in values:
                    if not value:
                        continue
                    absolute = urljoin(page_url, value.strip())
                    parsed = urlparse(absolute)
                    if parsed.scheme != "https" or not parsed.hostname:
                        continue
                    if absolute not in seen and any(parsed.path.lower().split("?")[0].endswith(ext) for ext in self.image_extensions):
                        seen.add(absolute)
                        images.append(absolute)
        if not images:
            raise ValueError("لم يعثر ManhuaPlus على صور الفصل في صفحة القارئ.")
        title = soup.select_one(".entry-title, h1")
        chapter_title = title.get_text(" ", strip=True) if title else (soup.title.get_text(" ", strip=True) if soup.title else "ManhuaPlus chapter")
        series = soup.select_one('a[href*="/manga/"]:not([href*="/chapter-"])')
        series_title = self._title(series, chapter_title) if series else chapter_title
        return {
            "title": chapter_title,
            "series_title": series_title,
            "series_key": f"manhuaplus:{urlparse(page_url).path.split('/')[2] if len(urlparse(page_url).path.split('/')) > 2 else series_title.casefold()}",
            "source": self.provider_id,
            "source_url": page_url,
            "pages": [{"index": index, "image": image} for index, image in enumerate(images, 1)],
        }

    def load_chapter(self, url: str, safe_fetch) -> dict:
        if not self.can_handle(url) or not self.chapter_pattern.search(urlparse(url).path):
            raise ValueError("رابط فصل ManhuaPlus يجب أن يكون من manhuaplus.org ويحتوي مسار الفصل.")
        parsed_chapter = urlparse(url)
        series_path, separator, _chapter_suffix = parsed_chapter.path.partition("/chapter-")
        if not separator or not series_path.startswith("/manga/"):
            raise ValueError("تعذر تحديد صفحة العمل للتحقق من نوعه قبل استيراد الفصل.")
        series_response = self._fetch(f"{self.base_url}{series_path}", safe_fetch)
        declared_type = self._require_supported_type(series_response.text)
        response = self._fetch(url, safe_fetch)
        match = self.chapter_id_pattern.search(response.text)
        if not match:
            raise ValueError("لم يعثر ManhuaPlus على معرّف الفصل اللازم لجلب الصور.")
        chapter_id = match.group(1)
        images_response = safe_fetch(
            f"{self.base_url}/ajax/image/list/chap/{chapter_id}", self.hosts, 4 * 1024 * 1024,
            method="POST", data={"chapter": chapter_id},
            headers={"Referer": response.url, "X-Requested-With": "XMLHttpRequest", "Accept": "application/json"},
        )
        try:
            payload = images_response.json()
        except ValueError:
            payload = {}
        if not isinstance(payload, dict) or not payload.get("status") or not isinstance(payload.get("html"), str):
            raise ValueError("لم يرسل ManhuaPlus صور الفصل في استجابة AJAX.")
        chapter = self.parse_chapter(response.text, response.url, payload["html"])
        chapter["series_type"] = declared_type
        return chapter
