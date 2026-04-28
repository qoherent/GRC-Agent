"""Read-only GNU Radio manual search over the bundled wiki corpus."""

from .clean import clean_manual_page
from .search import search_manual

__all__ = ["clean_manual_page", "search_manual"]
