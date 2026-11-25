"""Extensible chapter-source adapters."""

from .mangak import MangaKProvider
from .mangadex import MangaDexProvider
from .manhuaplus import ManhuaPlusProvider
from .atsumaru import AtsumaruProvider
from .aquareader import AquaReaderProvider
from .registry import ProviderRegistry

providers = ProviderRegistry([
    MangaDexProvider(),
    ManhuaPlusProvider(),
    AtsumaruProvider(),
    AquaReaderProvider(),
])

__all__ = [
    "AtsumaruProvider",
    "AquaReaderProvider",
    "MangaKProvider",
    "MangaDexProvider",
    "ManhuaPlusProvider",
    "ProviderRegistry",
    "providers",
]
