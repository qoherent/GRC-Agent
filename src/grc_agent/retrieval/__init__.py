"""Phase 1 retrieval package for bounded GNU Radio search."""

from .index import (
    DEFAULT_GRC_CATALOG_ROOTS,
    RetrievalIndexError,
    build_catalog_index,
    build_session_index,
    clear_catalog_index_cache,
    discover_catalog_root,
    get_catalog_index,
    initialize_retrieval,
)
from .schema import DEFAULT_RESULT_LIMIT, MAX_RESULT_LIMIT, RetrievalIndex
from .search import bind_retrieval_context, search_grc

__all__ = [
    "DEFAULT_GRC_CATALOG_ROOTS",
    "DEFAULT_RESULT_LIMIT",
    "MAX_RESULT_LIMIT",
    "RetrievalIndex",
    "RetrievalIndexError",
    "bind_retrieval_context",
    "build_catalog_index",
    "build_session_index",
    "clear_catalog_index_cache",
    "discover_catalog_root",
    "get_catalog_index",
    "initialize_retrieval",
    "search_grc",
]
