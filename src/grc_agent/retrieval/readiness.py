"""Catalog readiness checks for vector-backed retrieval."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from grc_agent._payload import ErrorCode, build_error_payload
from grc_agent.catalog.errors import CatalogLoadError
from grc_agent.catalog.loaders import (
    DEFAULT_GRC_CATALOG_ROOTS,
    collect_catalog_files,
    discover_catalog_root,
    validate_catalog_files,
)


class RetrievalReadinessError(RuntimeError):
    """Raised when catalog metadata required for vector indexing is unavailable."""


def initialize_retrieval(
    *,
    catalog_root: str | Path | None = None,
    warm_catalog: bool = False,
) -> dict[str, Any]:
    """Verify GNU Radio catalog metadata required by vector search.

    ``warm_catalog`` is accepted for CLI compatibility. There is no separate
    catalog index to warm.
    """
    _ = warm_catalog
    try:
        root = discover_catalog_root(catalog_root)
        files = collect_catalog_files(root)
        validate_catalog_files(root, files)
    except CatalogLoadError as exc:
        return build_error_payload(error_type=ErrorCode.RETRIEVAL_NOT_READY, message=str(exc))

    return {
        "ok": True,
        "message": "Retrieval ready.",
        "catalog_root": str(root),
        "catalog_files": {
            "block": len(files.block),
            "tree": len(files.tree),
            "domain": len(files.domain),
        },
        "catalog_index_warmed": False,
        "retrieval_backend": "vector",
    }


__all__ = [
    "DEFAULT_GRC_CATALOG_ROOTS",
    "RetrievalReadinessError",
    "discover_catalog_root",
    "initialize_retrieval",
]
