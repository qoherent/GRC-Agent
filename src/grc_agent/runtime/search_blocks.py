"""search_blocks wrapper — vector search over the GNU Radio catalog.

sqlite-vec + embeddinggemma KNN. Each hit is rendered through
:meth:`BlockDescription.to_payload`, so the model sees the same filtered
param shape as :func:`describe_block` (Stage A only — hide='all', Advanced,
Config, gui_hint are dropped via the unified :mod:`param_filter` rule).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from grc_agent.catalog.loaders import (
    CatalogError,
    _build_block_description,
    get_catalog_snapshot,
)
from grc_agent.catalog.schema import BlockDescription
from grc_agent.domain_models import ErrorCode
from grc_agent.runtime.catalog_vector import (
    CATALOG_DB_PATH,
    VectorCatalogStore,
    embed_query,
    is_catalog_db_usable,
)

if TYPE_CHECKING:
    from grc_agent.agent import GrcAgent, ToolResult


logger = logging.getLogger(__name__)


def _ensure_catalog_index(agent: GrcAgent) -> bool:
    """Build the vector index on the fly if missing. Returns True when usable."""
    if is_catalog_db_usable(CATALOG_DB_PATH):
        return True
    try:
        snapshot = get_catalog_snapshot(agent.catalog_root)
        blocks_payload: list[dict[str, Any]] = []
        for bid, raw in snapshot.blocks.items():
            payload = raw.payload
            raw_params = payload.get("parameters") or []
            param_values = {
                str(p.get("id")): "" if p.get("default") is None else str(p.get("default"))
                for p in raw_params
                if p.get("id")
            }
            blocks_payload.append(
                {
                    "block_id": bid,
                    "label": str(payload.get("label") or "") or bid,
                    "categories": list(getattr(raw, "category_paths", ())),
                    "parameters": [p.get("id") for p in raw_params if p.get("id")],
                    "param_values": param_values,
                    "ports": (
                        [p.get("id") for p in (payload.get("inputs") or []) if p.get("id")]
                        + [p.get("id") for p in (payload.get("outputs") or []) if p.get("id")]
                    ),
                    "documentation": str(payload.get("documentation") or ""),
                }
            )
        store = VectorCatalogStore(CATALOG_DB_PATH, agent._llama_server_url)
        store.ingest_if_needed(blocks=blocks_payload, server_url=agent._llama_server_url)
    except Exception as exc:
        logger.warning("Catalog vector ingest failed: %s", exc)
    return is_catalog_db_usable(CATALOG_DB_PATH)


def _render_hit(raw_block: Any, distance: float) -> dict[str, Any] | None:
    """One vector-store hit rendered as a model-facing block summary.

    Uses :meth:`BlockDescription.to_payload` (Stage A filter) for the params,
    so the model sees the same per-block shape as :func:`describe_block`.
    The per-block ``ok`` flag is dropped — the outer payload owns that.
    """
    try:
        description: BlockDescription = _build_block_description(raw_block)
    except Exception as exc:
        logger.debug("Skipping unrenderable catalog hit %s: %s", raw_block.block_id, exc)
        return None
    rendered = description.to_payload()
    rendered.pop("ok", None)
    rendered["distance"] = round(float(distance), 6)
    if description.label:
        rendered["label"] = description.label
    category = description.category_path
    if category:
        rendered["category"] = " > ".join(category)
    return rendered


def search_blocks(
    agent: GrcAgent,
    query: str,
    k: int | None = None,
) -> ToolResult:
    """Vector search over the GNU Radio catalog.

    Returns the top-``k`` blocks whose embedded label/category/param text
    is closest to the query. Each result is the Stage-A-filtered
    :class:`BlockDescription` payload (``block_id``, ``label``, ``category``,
    ``params``, ``inputs``, ``outputs``) plus a ``distance`` score.
    """
    q = " ".join(str(query).split()) if isinstance(query, str) else ""
    if not q:
        return _tool_error(agent, "query must be non-empty.")

    limit_value = agent._retrieval_cfg.search_blocks_default_k if k is None else int(k)
    limit = max(1, min(limit_value, agent._retrieval_cfg.search_blocks_max_k))

    if not _ensure_catalog_index(agent):
        return _tool_error(
            agent,
            "Catalog vector index not ready. Build with `make catalog-warmup` or restart the agent.",
            error_type=ErrorCode.RETRIEVAL_NOT_READY,
            degraded=True,
        )

    try:
        query_vec = embed_query(agent._llama_server_url, q)
    except Exception as exc:
        return _tool_error(
            agent,
            f"Embedding backend unreachable: {exc}",
            error_type=ErrorCode.RETRIEVAL_NOT_READY,
            degraded=True,
        )

    try:
        store = VectorCatalogStore(CATALOG_DB_PATH, agent._llama_server_url)
        # Pull one extra neighbour so we can detect truncation.
        neighbours = store.search(query_vec, limit + 1)
    except Exception as exc:
        return _tool_error(
            agent,
            f"Catalog vector search failed: {exc}",
            error_type=ErrorCode.RETRIEVAL_NOT_READY,
            degraded=True,
        )

    try:
        snapshot = get_catalog_snapshot(agent.catalog_root)
    except CatalogError as exc:
        return _tool_error(
            agent,
            f"Catalog unavailable: {exc}",
            error_type=ErrorCode.RETRIEVAL_NOT_READY,
            degraded=True,
        )

    rows: list[dict[str, Any]] = []
    for neighbour in neighbours:
        raw_block = snapshot.blocks.get(neighbour.get("block_id", ""))
        if raw_block is None:
            continue
        hit = _render_hit(raw_block, neighbour.get("distance", 0.0))
        if hit is not None:
            rows.append(hit)
        if len(rows) >= limit:
            break

    return agent._payload_result(
        "search_blocks",
        {
            "ok": True,
            "query": q,
            "results": rows,
            "output_truncated": len(neighbours) > limit,
        },
        include_active_session=False,
    )


def _tool_error(
    agent: GrcAgent,
    message: str,
    *,
    error_type: str = ErrorCode.INVALID_REQUEST,
    degraded: bool = False,
) -> ToolResult:
    payload = {
        "ok": False,
        "query": "",
        "results": [],
        "degraded_retrieval": degraded,
        "retrieval_mode": "vector",
        "output_truncated": False,
        "message": message,
        "error_type": error_type,
    }
    return agent._payload_result("search_blocks", payload, include_active_session=False)
