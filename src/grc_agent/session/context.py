"""Bounded context helpers for loaded `.grc` sessions."""

from typing import Any

from grc_agent.flowgraph_session import DEFAULT_CONTEXT_MAX_NODES, FlowgraphSession

from grc_agent._payload import build_error_payload

from .inspect import require_loaded_session


def get_grc_context(
    session: FlowgraphSession,
    node_id: str,
    *,
    hops: int = 1,
    max_nodes: int = DEFAULT_CONTEXT_MAX_NODES,
) -> dict[str, Any]:
    """Return a bounded mini-graph around one block instance in the loaded session."""
    try:
        require_loaded_session(session)
        return session.context_payload(node_id, hops=hops, max_nodes=max_nodes)
    except KeyError:
        return build_error_payload(
            error_type="node_not_found",
            message=f"Unknown session node: {node_id}",
            details={"node_id": node_id},
        )
    except ValueError as exc:
        return build_error_payload(
            error_type="invalid_context_request", message=str(exc)
        )
