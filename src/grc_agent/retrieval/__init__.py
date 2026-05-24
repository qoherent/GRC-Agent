"""Vector-backed retrieval package."""

from .readiness import (
    DEFAULT_GRC_CATALOG_ROOTS,
    RetrievalReadinessError,
    discover_catalog_root,
    initialize_retrieval,
)
from .vector import semantic_search_grc

__all__ = [
    "DEFAULT_GRC_CATALOG_ROOTS",
    "RetrievalReadinessError",
    "discover_catalog_root",
    "initialize_retrieval",
    "semantic_search_grc",
]
