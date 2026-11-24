from __future__ import annotations

from typing import Protocol
from urllib.parse import urlparse


class ChapterSourceProvider(Protocol):
    provider_id: str
    display_name: str
    hosts: frozenset[str]
    supports_title_search: bool
    supports_chapter_listing: bool
    supports_direct_chapter_import: bool

    def can_handle(self, url: str) -> bool: ...

    def parse_chapter(self, html: str, page_url: str) -> dict: ...

    def load_chapter(self, url: str, safe_fetch) -> dict: ...


def is_https_host(url: str, hosts: frozenset[str]) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and (parsed.hostname or "").lower() in hosts
