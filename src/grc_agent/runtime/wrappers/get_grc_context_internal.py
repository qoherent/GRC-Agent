"""Internal read-only get_grc_context wrapper implementation."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from grc_agent._payload import ErrorCode
from grc_agent.flowgraph_session import FlowgraphSession


ContextFn = Callable[..., dict[str, Any]]
SearchFn = Callable[..., dict[str, Any]]
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
    search_fn: SearchFn,
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
        candidate_nodes: list[str] = []
        candidate_result = search_fn(
            node_id,
            scope="session",
            k=3,
            session=session if session.flowgraph is not None else None,
            catalog_root=catalog_root,
        )
        if candidate_result.get("ok") and candidate_result.get("results"):
            candidate_nodes = [
                str(result.get("node_id")).removeprefix("session:block:")
                for result in candidate_result["results"]
                if isinstance(result, dict)
                and isinstance(result.get("node_id"), str)
                and str(result.get("node_id")).startswith("session:block:")
            ]
        if candidate_nodes:
            payload["candidate_nodes"] = candidate_nodes
            payload["hint"] = f"Closest session matches: {', '.join(candidate_nodes)}."
        elif session.flowgraph is not None:
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
