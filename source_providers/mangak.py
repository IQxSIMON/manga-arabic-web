from __future__ import annotations

from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .base import is_https_host


class MangaKProvider:
    provider_id = "mangak"
    display_name = "MangaK"
    hosts = frozenset({"mangak.io", "www.mangak.io"})
    supports_title_search = False
    supports_chapter_listing = False
    supports_direct_chapter_import = True
    search_note = "لا يوفر بحثاً داخل التطبيق حالياً؛ يمكن جلب فصل بلصق رابطه المباشر."
    image_extensions = (".jpg", ".jpeg", ".png", ".webp", ".avif")

    def can_handle(self, url: str) -> bool:
        return is_https_host(url, self.hosts)

    def image_candidates(self, soup: BeautifulSoup, page_url: str) -> list[str]:
        found = []
        seen = set()
        for tag in soup.find_all(["img", "source"]):
            values = []
            for attr in ("src", "data-src", "data-lazy-src", "data-original", "data-url"):
                value = tag.get(attr)
                if value:
                    values.append(value)
            srcset = tag.get("srcset")
            if srcset:
                values.extend(part.strip().split()[0] for part in srcset.split(",") if part.strip())
            for value in values:
                absolute = urljoin(page_url, value.strip())
                parsed = urlparse(absolute)
                if parsed.scheme != "https" or not parsed.netloc:
                    continue
                if absolute not in seen and (
                    any(parsed.path.lower().endswith(ext) for ext in self.image_extensions)
                    or "image" in absolute.lower()
                ):
                    seen.add(absolute)
                    found.append(absolute)
        return found

    def parse_chapter(self, html: str, page_url: str) -> dict:
        soup = BeautifulSoup(html, "html.parser")
        images = self.image_candidates(soup, page_url)
        if not images:
            raise ValueError("لم يعثر MangaK على صور للفصل.")
        title = soup.title.get_text(" ", strip=True) if soup.title else "Manga chapter"
        return {
            "title": title,
            "source": self.provider_id,
            "url": page_url,
            "pages": [{"index": index, "image": image} for index, image in enumerate(images, 1)],
        }

    def load_chapter(self, url: str, safe_fetch) -> dict:
        response = safe_fetch(url, self.hosts, 5 * 1024 * 1024)
        return self.parse_chapter(response.text, response.url)
