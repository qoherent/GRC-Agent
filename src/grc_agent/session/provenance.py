"""Session provenance helpers."""

from typing import Any

from grc_agent._payload import ErrorCode, build_error_payload
from grc_agent.flowgraph_session import FlowgraphSession

from .inspect import require_loaded_session


def session_provenance(session: FlowgraphSession) -> dict[str, Any]:
    """Return the stable provenance payload for one loaded session."""
    try:
        require_loaded_session(session)
    except ValueError as exc:
        return build_error_payload(error_type=ErrorCode.MISSING_SESSION, message=str(exc))
    return session.session_provenance()
