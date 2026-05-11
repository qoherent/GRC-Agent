"""search_blocks wrapper implementation extracted from GrcAgent."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from grc_agent._payload import ErrorCode
from grc_agent.catalog import describe_block

if TYPE_CHECKING:
    from grc_agent.agent import GrcAgent, ToolResult

logger = logging.getLogger(__name__)


def search_blocks(
    agent: "GrcAgent",
    query: str,
    k: int | None = None,
    debug: bool = False,
    enrich: bool = False,
) -> "ToolResult":
    import time

    import grc_agent.agent as agent_module

    started = time.monotonic()
    before_revision = agent.session.state_revision
    before_dirty = agent.session.is_dirty
    handlers: list[str] = []
    q = " ".join(str(query).split()) if isinstance(query, str) else ""
    if not q:
        result = agent._tool_result(
            "search_blocks",
            ok=False,
            message="query must be non-empty.",
            error_type=ErrorCode.INVALID_REQUEST,
        )
        return agent._attach_wrapper_dispatch_telemetry(
            debug=debug,
            wrapper_name="search_blocks",
            wrapper_action="query",
            internal_handlers=["none"],
            started=started,
            before_revision=before_revision,
            before_dirty=before_dirty,
            result=result,
            validation_run=False,
            output_truncated=False,
        )
    session_ctx = agent.session if agent.session.flowgraph is not None else None
    limit_value = (
        agent._retrieval_cfg.search_blocks_default_k
        if k is None
        else int(k)
    )
    limit = max(1, min(limit_value, agent._retrieval_cfg.search_blocks_max_k))
    cacheable = not debug and not enrich
    lexical: dict[str, Any] = {"ok": True, "results": []}
    retrieval_mode = "hybrid"
    semantic: dict[str, Any] = {"ok": True, "results": []}

    query_raw = " ".join(q.split()).strip().lower()
    query_alias = agent_module._normalize_alias_key(q)
    exact_block_id: str | None = None
    exact_alias_hit = False
    if agent._retrieval_cfg.exact_match_fast_path:
        try:
            alias_map = agent_module._catalog_alias_to_block_map(agent.catalog_root)
            if query_raw and query_raw in alias_map:
                exact_block_id = alias_map[query_raw]
                exact_alias_hit = True
            elif query_alias and query_alias in alias_map:
                exact_block_id = alias_map[query_alias]
                exact_alias_hit = True
        except Exception:
            logger.exception("search_blocks_alias_map_failed")

    cache_key: tuple[str, int, str] | None = None
    if exact_block_id is None and cacheable:
        cache_key = agent._search_blocks_cache_key(query=q, k=limit)
        cached_payload = agent._search_blocks_cache_get(cache_key)
        if cached_payload is not None:
            handlers.append("search_blocks_cache(hit)")
            result = agent._payload_result(
                "search_blocks",
                {
                    "ok": True,
                    "query": q,
                    "results": cached_payload["results"],
                    "degraded_retrieval": bool(cached_payload["degraded_retrieval"]),
                    "retrieval_mode": str(cached_payload["retrieval_mode"]),
                    "message": "Block candidates returned.",
                },
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
                output_truncated=bool(cached_payload.get("output_truncated", False)),
            )

    handlers.append("search_grc(lexical,catalog)")
    lexical = agent_module._search_grc_with_context(
        q,
        scope="catalog",
        k=limit,
        session=session_ctx,
        catalog_root=agent.catalog_root,
    )
    lexical_rows = lexical.get("results", []) if lexical.get("ok") else []

    if exact_block_id is None:
        handlers.append("search_blocks_cache(miss)")
        handlers.append("semantic_search_grc(catalog)")
        semantic = agent_module.semantic_search_grc(q, scope="catalog", k=limit)
    else:
        retrieval_mode = "exact"

    merged: dict[str, dict[str, Any]] = {}
    degraded_retrieval = False
    if semantic.get("ok"):
        for row in semantic.get("results", []):
            if not isinstance(row, dict):
                continue
            block_id = row.get("canonical_block_id")
            if not isinstance(block_id, str) or not block_id:
                continue
            name = row.get("title")
            summary = row.get("excerpt")
            merged[block_id] = {
                "block_id": block_id,
                "name": name if isinstance(name, str) and name else block_id,
                "summary": agent_module._compact_block_summary(summary),
            }
            if debug:
                merged[block_id]["debug"] = {
                    "source": "semantic",
                    "record_id": row.get("record_id"),
                    "score": row.get("vector_score_raw"),
                }
    else:
        if semantic:
            degraded_retrieval = semantic.get("error_type") in {
                "missing_index",
                ErrorCode.RETRIEVAL_NOT_READY,
            }
            if degraded_retrieval:
                retrieval_mode = "lexical_fallback_missing_vector"

    if lexical.get("ok"):
        for row in lexical_rows:
            if not isinstance(row, dict):
                continue
            block_id = row.get("block_id")
            if not isinstance(block_id, str) or not block_id:
                continue
            current = merged.get(block_id)
            summary = row.get("summary")
            label = row.get("label")
            if current is None:
                merged[block_id] = {
                    "block_id": block_id,
                    "name": label if isinstance(label, str) and label else block_id,
                    "summary": agent_module._compact_block_summary(summary),
                }
                if debug:
                    merged[block_id]["debug"] = {
                        "source": "lexical",
                        "record_id": row.get("node_id"),
                    }
            else:
                if not current.get("summary") and isinstance(summary, str):
                    current["summary"] = agent_module._compact_block_summary(summary)
                if current.get("name") == block_id and isinstance(label, str):
                    current["name"] = label
                if debug and "debug" not in current:
                    current["debug"] = {
                        "source": "semantic+lexical",
                        "record_id": row.get("node_id"),
                    }

    if enrich:
        handlers.append("describe_block(enrichment)")
        for item in merged.values():
            if item.get("summary"):
                continue
            details = describe_block(str(item.get("block_id", "")))
            if details.get("ok"):
                summary = details.get("summary")
                if isinstance(summary, str) and summary:
                    item["summary"] = agent_module._compact_block_summary(summary)

    ordered = list(merged.values())
    query_l = q.lower()
    ordered.sort(
        key=lambda item: (
            0
            if query_l in {item["block_id"].lower(), item["name"].lower()}
            else 1,
            item["block_id"],
        )
    )
    if retrieval_mode == "exact" and exact_block_id:
        limited = [item for item in ordered if item.get("block_id") == exact_block_id][:1]
        if not limited and lexical_rows:
            for row in lexical_rows:
                if row.get("block_id") == exact_block_id:
                    label = row.get("label")
                    summary = row.get("summary")
                    fallback_row: dict[str, Any] = {
                        "block_id": exact_block_id,
                        "name": label if isinstance(label, str) and label else exact_block_id,
                        "summary": agent_module._compact_block_summary(summary),
                    }
                    if debug:
                        fallback_row["debug"] = {
                            "source": "lexical_exact_fallback",
                            "record_id": row.get("node_id"),
                            "exact_alias": exact_alias_hit,
                        }
                    limited = [fallback_row]
                    break
    else:
        limited = ordered[:limit]

    output_truncated = len(ordered) > len(limited)
    if not debug:
        limited = [
            {
                "block_id": str(item.get("block_id", "")),
                "name": str(item.get("name", "")),
                "summary": str(item.get("summary", "")),
            }
            for item in limited
        ]
    if cache_key is not None and cacheable:
        agent._search_blocks_cache_put(
            cache_key,
            {
                "results": limited,
                "degraded_retrieval": degraded_retrieval,
                "retrieval_mode": retrieval_mode,
                "output_truncated": output_truncated,
            },
        )
    result = agent._payload_result(
        "search_blocks",
        {
            "ok": True,
            "query": q,
            "results": limited,
            "degraded_retrieval": degraded_retrieval,
            "retrieval_mode": retrieval_mode,
            "message": "Block candidates returned.",
        },
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
