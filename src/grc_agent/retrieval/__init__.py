"""Retrieval package — pure lexical FTS5 search."""

from .readiness import (
    DEFAULT_GRC_CATALOG_ROOTS,
    RetrievalReadinessError,
    discover_catalog_root,
    initialize_retrieval,
)

__all__ = [
    "DEFAULT_GRC_CATALOG_ROOTS",
    "RetrievalReadinessError",
    "discover_catalog_root",
    "initialize_retrieval",
]
