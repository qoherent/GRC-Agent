"""Shared helpers for the read-only session inspection package."""

from grc_agent.flowgraph_session import FlowgraphSession


def require_loaded_session(session: FlowgraphSession) -> FlowgraphSession:
    """Ensure the provided session has an active loaded flowgraph."""
    if session.flowgraph is None:
        raise ValueError("No flowgraph loaded.")
    return session
