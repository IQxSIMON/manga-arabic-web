from __future__ import annotations

from .base import ChapterSourceProvider


class ProviderRegistry:
    def __init__(self, providers=()):
        self._providers: dict[str, ChapterSourceProvider] = {}
        for provider in providers:
            self.register(provider)

    def register(self, provider: ChapterSourceProvider) -> None:
        if provider.provider_id in self._providers:
            raise ValueError(f"Provider already registered: {provider.provider_id}")
        self._providers[provider.provider_id] = provider

    def for_url(self, url: str) -> ChapterSourceProvider | None:
        return next((provider for provider in self._providers.values() if provider.can_handle(url)), None)

    def by_id(self, provider_id: str) -> ChapterSourceProvider | None:
        return self._providers.get(provider_id)

    def provider_ids(self) -> tuple[str, ...]:
        return tuple(self._providers)

    def descriptors(self) -> list[dict]:
        """Describe optional provider capabilities without tying the app to a site."""
        return [
            {
                "id": provider.provider_id,
                "name": getattr(provider, "display_name", provider.provider_id),
                "supports_title_search": bool(getattr(provider, "supports_title_search", False)),
                "supports_chapter_listing": bool(getattr(provider, "supports_chapter_listing", False)),
                "supports_direct_chapter_import": bool(
                    getattr(provider, "supports_direct_chapter_import", True)
                ),
                "search_note": getattr(provider, "search_note", ""),
            }
            for provider in self._providers.values()
        ]

    def search(self, provider_id: str, query: str, safe_fetch, limit: int = 20) -> list[dict]:
        provider = self.by_id(provider_id)
        if provider is None:
            raise ValueError("مصدر البحث غير مسجل.")
        search = getattr(provider, "search", None)
        if not callable(search):
            raise ValueError(
                f"{getattr(provider, 'display_name', provider_id)} لا يوفر بحثاً بالعنوان؛ "
                "يمكنك جلب فصل منه بلصق رابط الفصل المباشر."
            )
        return search(query, safe_fetch, limit=limit)

    def chapters(self, provider_id: str, work_id: str, safe_fetch, limit: int = 100) -> list[dict]:
        provider = self.by_id(provider_id)
        if provider is None:
            raise ValueError("مصدر الفصول غير مسجل.")
        listing = getattr(provider, "chapters", None)
        if not callable(listing):
            raise ValueError(
                f"{getattr(provider, 'display_name', provider_id)} لا يوفر قائمة فصول عبر البحث؛ "
                "يمكنك استيراد رابط الفصل مباشرة."
            )
        return listing(work_id, safe_fetch, limit=limit)
