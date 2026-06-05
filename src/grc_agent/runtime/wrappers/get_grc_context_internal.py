"""Internal read-only get_grc_context wrapper implementation."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from grc_agent._payload import ErrorCode
from grc_agent.flowgraph_session import FlowgraphSession

ContextFn = Callable[..., dict[str, Any]]
SymbolResolver = Callable[[str], str | None]


def get_grc_context_internal(
    node_id: str,
    *,
    hops: int,
    max_nodes: int | None,
    session: FlowgraphSession,
    catalog_root: Path | None,
    default_max_nodes: int,
    symbol_resolver: SymbolResolver,
    context_fn: ContextFn,
) -> dict[str, Any]:
    resolved_node_id = symbol_resolver(node_id) or node_id
    resolved_max_nodes = default_max_nodes if max_nodes is None else max_nodes
    payload = context_fn(
        session,
        resolved_node_id,
        hops=hops,
        max_nodes=resolved_max_nodes,
    )
    if payload.get("ok"):
        payload["hint"] = (
            "This is inspection data only. "
            "If the user also asked for a real change after inspecting, call `apply_edit` next."
        )
    if payload.get("ok") is False and payload.get("error_type") == ErrorCode.BLOCK_NOT_FOUND:
        if session.flowgraph is not None:
            fallback_candidates = [
                b.instance_name for b in session.flowgraph.blocks[: min(5, max_nodes)]
            ]
            if fallback_candidates:
                payload["candidate_nodes"] = fallback_candidates
                payload["hint"] = (
                    "Use an exact loaded session name. "
                    f"Examples: {', '.join(fallback_candidates)}."
                )
    return payload
