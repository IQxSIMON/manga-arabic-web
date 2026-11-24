"""Extensible chapter-source adapters."""

from .mangak import MangaKProvider
from .mangadex import MangaDexProvider
from .manhuaplus import ManhuaPlusProvider
from .atsumaru import AtsumaruProvider
from .registry import ProviderRegistry

providers = ProviderRegistry([MangaDexProvider(), ManhuaPlusProvider(), AtsumaruProvider()])

__all__ = ["AtsumaruProvider", "MangaKProvider", "MangaDexProvider", "ManhuaPlusProvider", "ProviderRegistry", "providers"]
