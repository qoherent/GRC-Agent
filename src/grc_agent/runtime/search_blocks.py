"""search_blocks wrapper — vector search over the GNU Radio catalog.

Replaces the FTS5 lexical backend. Uses the same vec1 + embeddinggemma
pipeline as docs (see :mod:`grc_agent.runtime.doc_answer`).
"""

from __future__ import annotations

import logging
import time
from collections import OrderedDict
from typing import TYPE_CHECKING, Any

from grc_agent._payload import ErrorCode
from grc_agent.catalog.loaders import CatalogError, describe_block, get_catalog_snapshot
from grc_agent.runtime.tool_context import is_meaningful
from grc_agent.runtime.catalog_vector import (
    CATALOG_DB_PATH,
    VectorCatalogStore,
    embed_query,
    is_catalog_db_usable,
)

if TYPE_CHECKING:
    from grc_agent.agent import GrcAgent, ToolResult


logger = logging.getLogger(__name__)


_CATALOG_DETAIL_LIMIT = 3
_VECTOR_CACHE_MAX = 4
_VECTOR_CACHE: "OrderedDict[tuple[str, int, str], dict[str, Any]]" = OrderedDict()


def search_blocks(
    agent: "GrcAgent",
    query: str,
    k: int | None = None,
    debug: bool = False,
    enrich: bool = False,
) -> "ToolResult":
    """Vector search over the GNU Radio catalog.

    Returns the same payload shape the model already sees: a list of
    ``{block_id, name, summary, distance, match_type, why}`` rows.
    """
    import grc_agent.agent as agent_module

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
    cacheable = not debug and not enrich

    cache_key: tuple[str, int, str] | None = None
    if cacheable:
        cache_key = (q, limit, agent._search_blocks_version_token())
        cached = _VECTOR_CACHE.get(cache_key)
        if cached is not None:
            _VECTOR_CACHE.move_to_end(cache_key)
            payload = {
                "ok": True,
                "query": q,
                "results": cached["results"],
                "degraded_retrieval": bool(cached.get("degraded_retrieval", False)),
                "retrieval_mode": "vector",
                "output_truncated": bool(cached.get("output_truncated", False)),
                "message": "Block candidates returned.",
                "cache": "hit",
            }
            result = agent._payload_result(
                "search_blocks", payload, include_active_session=False
            )
            return agent._attach_wrapper_dispatch_telemetry(
                debug=debug,
                wrapper_name="search_blocks",
                wrapper_action="query",
                internal_handlers=["search_blocks_cache(hit)"],
                started=started,
                before_revision=before_revision,
                before_dirty=before_dirty,
                result=result,
                validation_run=False,
                output_truncated=bool(cached.get("output_truncated", False)),
            )

    handlers.append("catalog_vector_search")
    if not is_catalog_db_usable(CATALOG_DB_PATH):
        # Try to (re-)ingest on the fly so the model isn't stuck behind a missing index.
        try:
            snapshot = get_catalog_snapshot(agent.catalog_root)
            blocks_payload = [
                {
                    "block_id": bid,
                    "label": _string_value(b.payload.get("label")) or bid,
                    "categories": list(getattr(b, "category_paths", ())),
                    "parameters": [
                        p.get("id")
                        for p in (b.payload.get("parameters") or [])
                        if p.get("id")
                    ],
                    "ports": [
                        p.get("id")
                        for p in (b.payload.get("inputs") or [])
                        if p.get("id")
                    ] + [
                        p.get("id")
                        for p in (b.payload.get("outputs") or [])
                        if p.get("id")
                    ],
                    "documentation": _string_value(b.payload.get("documentation")) or "",
                }
                for bid, b in snapshot.blocks.items()
            ]
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
        label = _string_value(block.payload.get("label")) or bid
        params = [
            p.get("id")
            for p in (block.payload.get("parameters") or [])
            if p.get("id")
        ]
        categories = [
            " ".join(part for part in path if part)
            for path in getattr(block, "category_paths", ())
        ]
        summary = agent_module._compact_block_summary(
            _catalog_summary(
                documentation=_string_value(block.payload.get("documentation")) or "",
                params=params,
                inputs=[
                    p.get("id")
                    for p in (block.payload.get("inputs") or [])
                    if p.get("id")
                ],
                outputs=[
                    p.get("id")
                    for p in (block.payload.get("outputs") or [])
                    if p.get("id")
                ],
                categories=categories,
            )
        )
        rows.append(
            {
                "block_id": bid,
                "name": label,
                "summary": summary,
                "distance": float(neighbour.get("distance", 1.0)),
                "match_type": "vector",
                "why": _vector_why(neighbour, label),
            }
        )

    limited = rows[:limit]
    output_truncated = len(rows) > len(limited)

    if not debug:
        for idx, item in enumerate(limited):
            if idx < _CATALOG_DETAIL_LIMIT:
                details = _compact_catalog_details(str(item["block_id"]))
                if details:
                    item["catalog"] = details
        limited = [
            {
                "block_id": str(item["block_id"]),
                "name": str(item["name"]),
                "summary": str(item["summary"]),
                "match_type": str(item["match_type"]),
                "why": str(item["why"]),
                "distance": float(item.get("distance", 0.0)),
                **(
                    {"catalog": item["catalog"]}
                    if isinstance(item.get("catalog"), dict)
                    else {}
                ),
            }
            for item in limited
        ]

    text_lines: list[str] = []
    if not debug:
        for idx, item in enumerate(limited, 1):
            bid = str(item.get("block_id", ""))
            name = str(item.get("name", ""))
            text_lines.append(f"{idx}. ID: {bid} | Name: {name}")

    payload = {
        "ok": True,
        "query": q,
        "results": limited,
        **({"results_text": "\n".join(text_lines)} if text_lines else {}),
        "degraded_retrieval": False,
        "retrieval_mode": "vector",
        "output_truncated": output_truncated,
        "message": "Block candidates returned.",
    }

    if cache_key is not None and cacheable:
        _VECTOR_CACHE[cache_key] = {
            "results": limited,
            "degraded_retrieval": False,
            "retrieval_mode": "vector",
            "output_truncated": output_truncated,
        }
        _VECTOR_CACHE.move_to_end(cache_key)
        while len(_VECTOR_CACHE) > _VECTOR_CACHE_MAX:
            _VECTOR_CACHE.popitem(last=False)

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


def _vector_why(neighbour: dict[str, Any], label: str) -> str:
    distance = float(neighbour.get("distance", 1.0))
    # Cosine distance in our calibrated range: 0.29..0.65. Same bands as docs.
    if distance < 0.35:
        band = "strong semantic match"
    elif distance < 0.50:
        band = "moderate semantic match"
    else:
        band = "weak semantic match"
    return f"{band} for {label} (cosine distance {distance:.3f})"


def _catalog_summary(
    *,
    documentation: str,
    params: list[str],
    inputs: list[str],
    outputs: list[str],
    categories: list[str],
    templates_make: str | None = None,
) -> str:
    if documentation:
        return " ".join(documentation.split())
    parts: list[str] = []
    if inputs:
        inp_str = ", ".join(inputs[:4])
        if len(inputs) > 4:
            inp_str += f" ... [TRUNCATED inputs: was {len(inputs)}, kept 4]"
        parts.append("inputs: " + inp_str)
    if outputs:
        out_str = ", ".join(outputs[:4])
        if len(outputs) > 4:
            out_str += f" ... [TRUNCATED outputs: was {len(outputs)}, kept 4]"
        parts.append("outputs: " + out_str)
    if params:
        par_str = "; ".join(params[:4])
        if len(params) > 4:
            par_str += f" ... [TRUNCATED params: was {len(params)}, kept 4]"
        parts.append("params: " + par_str)
    if categories:
        parts.append("category: " + categories[0])
    if templates_make:
        usage = _string_value(templates_make)
        if usage:
            parts.append("Usage: " + usage)
    return "; ".join(parts)


def _compact_catalog_details(block_id: str) -> dict[str, Any]:
    details = describe_block(block_id)
    if details.get("ok") is not True:
        return {}
    params = []
    raw_params = details.get("parameters", [])
    for raw_param in raw_params[:10]:
        if not isinstance(raw_param, dict):
            continue
        param = {
            key: raw_param.get(key)
            for key in ("id", "label", "dtype", "default")
            if is_meaningful(raw_param.get(key))
        }
        options = raw_param.get("options")
        if isinstance(options, list) and options:
            param["options"] = options[:8]
            if len(options) > 8:
                param["options"].append(
                    f"... [TRUNCATED options: was {len(options)}, kept 8]"
                )
        labels = raw_param.get("option_labels")
        if isinstance(labels, list) and labels:
            param["option_labels"] = labels[:8]
            if len(labels) > 8:
                param["option_labels"].append(
                    f"... [TRUNCATED option_labels: was {len(labels)}, kept 8]"
                )
        params.append(param)
    if len(raw_params) > 10:
        params.append({"_truncated": f"was {len(raw_params)}, kept 10"})
    ports: dict[str, list[dict[str, Any]]] = {}
    for direction in ("inputs", "outputs"):
        compact_ports: list[dict[str, Any]] = []
        raw_ports = details.get(direction, [])
        for raw_port in raw_ports[:8]:
            if not isinstance(raw_port, dict):
                continue
            compact_ports.append(
                {
                    key: raw_port.get(key)
                    for key in ("id", "domain", "dtype", "optional")
                    if is_meaningful(raw_port.get(key))
                }
            )
        if len(raw_ports) > 8:
            compact_ports.append({"_truncated": f"was {len(raw_ports)}, kept 8"})
        if compact_ports:
            ports[direction] = compact_ports
    return {
        key: value
        for key, value in {"params": params, **ports}.items()
        if is_meaningful(value)
    }
