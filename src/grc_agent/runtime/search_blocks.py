"""search_blocks wrapper — vector search over the GNU Radio catalog.

Replaces the FTS5 lexical backend. Uses the same vec1 + embeddinggemma
pipeline as docs (see :mod:`grc_agent.runtime.doc_answer`).
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from grc_agent._payload import ErrorCode
from grc_agent.catalog.loaders import CatalogError, get_catalog_snapshot
from grc_agent.runtime.catalog_vector import (
    CATALOG_DB_PATH,
    VectorCatalogStore,
    embed_query,
    is_catalog_db_usable,
)

if TYPE_CHECKING:
    from grc_agent.agent import GrcAgent, ToolResult


logger = logging.getLogger(__name__)


def search_blocks(
    agent: "GrcAgent",
    query: str,
    k: int | None = None,
    debug: bool = False,
) -> "ToolResult":
    """Vector search over the GNU Radio catalog.

    Returns the same payload shape the model already sees: a list of
    ``{block_id, name, summary, distance, match_type, why}`` rows.
    """

    started = time.monotonic()
    before_revision = agent.session.state_revision
    before_dirty = agent.session.is_dirty
    handlers: list[str] = []
    q = " ".join(str(query).split()) if isinstance(query, str) else ""
    if not q:
        return _tool_error(
            agent,
            started,
            "query must be non-empty.",
            handlers,
            before_revision,
            before_dirty,
        )

    limit_value = (
        agent._retrieval_cfg.search_blocks_default_k
        if k is None
        else int(k)
    )
    limit = max(1, min(limit_value, agent._retrieval_cfg.search_blocks_max_k))

    handlers.append("catalog_vector_search")
    if not is_catalog_db_usable(CATALOG_DB_PATH):
        # Try to (re-)ingest on the fly so the model isn't stuck behind a missing index.
        try:
            snapshot = get_catalog_snapshot(agent.catalog_root)
            blocks_payload = []
            for bid, b in snapshot.blocks.items():
                raw_params = b.payload.get("parameters") or []
                param_values = {
                    str(p.get("id")): "" if p.get("default") is None else str(p.get("default"))
                    for p in raw_params if p.get("id")
                }
                blocks_payload.append({
                    "block_id": bid,
                    "label": _string_value(b.payload.get("label")) or bid,
                    "categories": list(getattr(b, "category_paths", ())),
                    "parameters": [p.get("id") for p in raw_params if p.get("id")],
                    "param_values": param_values,
                    "ports": (
                        [p.get("id") for p in (b.payload.get("inputs") or []) if p.get("id")] +
                        [p.get("id") for p in (b.payload.get("outputs") or []) if p.get("id")]
                    ),
                    "documentation": _string_value(b.payload.get("documentation")) or "",
                })
            store = VectorCatalogStore(CATALOG_DB_PATH, agent._llama_server_url)
            store.ingest_if_needed(
                blocks=blocks_payload, server_url=agent._llama_server_url
            )
        except Exception as exc:
            logger.warning("Catalog vector ingest failed: %s", exc)

    if not is_catalog_db_usable(CATALOG_DB_PATH):
        return _tool_error(
            agent,
            started,
            "Catalog vector index not ready. Build with `make catalog-warmup` or restart the agent.",
            handlers,
            before_revision,
            before_dirty,
            error_type=ErrorCode.RETRIEVAL_NOT_READY,
            degraded=True,
        )

    try:
        query_vec = embed_query(agent._llama_server_url, q)
    except Exception as exc:
        return _tool_error(
            agent,
            started,
            f"Embedding backend unreachable: {exc}",
            handlers,
            before_revision,
            before_dirty,
            error_type=ErrorCode.RETRIEVAL_NOT_READY,
            degraded=True,
        )

    try:
        store = VectorCatalogStore(CATALOG_DB_PATH, agent._llama_server_url)
        neighbours = store.search(query_vec, limit)
    except Exception as exc:
        return _tool_error(
            agent,
            started,
            f"Catalog vector search failed: {exc}",
            handlers,
            before_revision,
            before_dirty,
            error_type=ErrorCode.RETRIEVAL_NOT_READY,
            degraded=True,
        )

    # Map every neighbour back to a real catalog block. The vector store is the
    # authoritative source; some neighbour rows may be stale or no longer exist.
    try:
        snapshot = get_catalog_snapshot(agent.catalog_root)
    except CatalogError as exc:
        return _tool_error(
            agent,
            started,
            f"Catalog unavailable: {exc}",
            handlers,
            before_revision,
            before_dirty,
            error_type=ErrorCode.RETRIEVAL_NOT_READY,
            degraded=True,
        )

    rows: list[dict[str, Any]] = []
    for neighbour in neighbours:
        bid = neighbour.get("block_id", "")
        block = snapshot.blocks.get(bid)
        if block is None:
            continue
        param_ids = [p.get("id") for p in (block.payload.get("parameters") or []) if p.get("id")][:3]
        rows.append({
                "id": bid,
                "params": ", ".join(param_ids) if param_ids else "",
            })

    limited = rows[:limit]
    # The vector store returns limit+1 neighbours. If we got limit+1 back,
    # there are more matches beyond what we showed.
    output_truncated = len(neighbours) > limit

    payload: dict[str, Any] = {
        "ok": True,
        "query": q,
        "results": limited,
        "output_truncated": output_truncated,
    }

    result = agent._payload_result(
        "search_blocks", payload, include_active_session=False
    )
    return agent._attach_wrapper_dispatch_telemetry(
        debug=debug,
        wrapper_name="search_blocks",
        wrapper_action="query",
        internal_handlers=handlers,
        started=started,
        before_revision=before_revision,
        before_dirty=before_dirty,
        result=result,
        validation_run=False,
        output_truncated=output_truncated,
    )


def _tool_error(
    agent: "GrcAgent",
    started: float,
    message: str,
    handlers: list[str],
    before_revision: int,
    before_dirty: bool,
    *,
    error_type: str = ErrorCode.INVALID_REQUEST,
    degraded: bool = False,
) -> "ToolResult":
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
    result = agent._payload_result(
        "search_blocks", payload, include_active_session=False
    )
    return agent._attach_wrapper_dispatch_telemetry(
        debug=False,
        wrapper_name="search_blocks",
        wrapper_action="query",
        internal_handlers=handlers,
        started=started,
        before_revision=before_revision,
        before_dirty=before_dirty,
        result=result,
        validation_run=False,
        output_truncated=False,
    )


def _string_value(value: Any) -> str | None:
    if isinstance(value, str):
        text = " ".join(value.split())
        return text or None
    return None
