"""Catalog readiness checks for retrieval.

Consolidated from __init__.py + readiness.py.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from grc_agent._payload import ErrorCode, build_error_payload
from grc_agent.catalog.loaders import CatalogLoadError
from grc_agent.catalog.loaders import (
    DEFAULT_GRC_CATALOG_ROOTS,
    collect_catalog_files,
    discover_catalog_root,
    validate_catalog_files,
)
from grc_agent.runtime.catalog_vector import (
    CATALOG_DB_PATH,
    VectorCatalogStore,
    is_catalog_db_usable,
)


class RetrievalReadinessError(RuntimeError):
    """Raised when catalog metadata required for retrieval is unavailable."""


def warmup_catalog_vector_index(
    *,
    catalog_root: str | Path | None = None,
    server_url: str,
) -> dict[str, Any]:
    """Synchronously build the catalog vector index if it isn't already.

    Safe to call repeatedly — the store is a no-op once the DB is populated.
    """
    if is_catalog_db_usable(CATALOG_DB_PATH):
        return {"ok": True, "already_populated": True, "db_path": str(CATALOG_DB_PATH)}

    from grc_agent.catalog.loaders import get_catalog_snapshot

    snapshot = get_catalog_snapshot(catalog_root)
    blocks_payload = []
    for bid, b in snapshot.blocks.items():
        raw_params = b.payload.get("parameters") or []
        param_ids = [p.get("id") for p in raw_params if p.get("id")]
        # Defaults let GRC evaluate conditional ``hide`` expressions
        # (e.g. "${ ('none' if len(name) > 0 else 'part') }"). Without
        # values, those expressions fall back to their unevaluated state
        # and ``hide='all'`` GUI-styling params leak into the embed.
        param_values = {
            str(p.get("id")): "" if p.get("default") is None else str(p.get("default"))
            for p in raw_params if p.get("id")
        }
        blocks_payload.append({
            "block_id": bid,
            "label": (b.payload.get("label") or bid),
            "categories": list(getattr(b, "category_paths", ())),
            "parameters": param_ids,
            "param_values": param_values,
            "ports": (
                [p.get("id") for p in (b.payload.get("inputs") or []) if p.get("id")] +
                [p.get("id") for p in (b.payload.get("outputs") or []) if p.get("id")]
            ),
            "documentation": b.payload.get("documentation") or "",
        })
    store = VectorCatalogStore(CATALOG_DB_PATH, server_url)
    store.ingest_if_needed(blocks=blocks_payload, server_url=server_url)
    return {
        "ok": True,
        "already_populated": False,
        "db_path": str(CATALOG_DB_PATH),
        "block_count": len(blocks_payload),
    }


def initialize_retrieval(
    *,
    catalog_root: str | Path | None = None,
    warm_catalog: bool = False,
    server_url: str | None = None,
) -> dict[str, Any]:
    _ = warm_catalog
    try:
        root = discover_catalog_root(catalog_root)
        files = collect_catalog_files(root)
        validate_catalog_files(root, files)
    except CatalogLoadError as exc:
        return build_error_payload(error_type=ErrorCode.RETRIEVAL_NOT_READY, message=str(exc))

    payload = {
        "ok": True,
        "message": "Retrieval ready.",
        "catalog_root": str(root),
        "catalog_files": {
            "block": len(files.block),
            "tree": len(files.tree),
            "domain": len(files.domain),
        },
        "catalog_index_warmed": is_catalog_db_usable(CATALOG_DB_PATH),
        "retrieval_backend": "vector",
    }
    if warm_catalog and server_url and not payload["catalog_index_warmed"]:
        try:
            warm = warmup_catalog_vector_index(catalog_root=root, server_url=server_url)
            payload["catalog_index_warmed"] = bool(warm.get("ok"))
            payload["catalog_warmup"] = warm
        except Exception as exc:
            payload["catalog_warmup_error"] = str(exc)
    return payload


__all__ = [
    "DEFAULT_GRC_CATALOG_ROOTS",
    "RetrievalReadinessError",
    "discover_catalog_root",
    "initialize_retrieval",
    "warmup_catalog_vector_index",
]
