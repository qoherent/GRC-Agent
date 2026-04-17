"""Structured summary helpers for loaded `.grc` sessions."""

from typing import Any

from grc_agent.flowgraph_session import DEFAULT_SUMMARY_BLOCK_LIMIT, FlowgraphSession

from grc_agent._payload import build_error_payload

from .inspect import require_loaded_session


def summarize_graph(
    session: FlowgraphSession,
    *,
    max_blocks: int = DEFAULT_SUMMARY_BLOCK_LIMIT,
) -> dict[str, Any]:
    """Return the bounded structured summary payload for one loaded session."""
    try:
        require_loaded_session(session)
        return session.summary_payload(max_blocks=max_blocks)
    except ValueError as exc:
        return build_error_payload(
            error_type="invalid_summary_request", message=str(exc)
        )
