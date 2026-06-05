"""Thin session-loading helper for the read-only inspection package."""

from pathlib import Path
from typing import Any

import yaml

from grc_agent._payload import ErrorCode, build_error_payload
from grc_agent.flowgraph_session import FlowgraphSession


def load_grc(file_path: str | Path) -> FlowgraphSession | dict[str, Any]:
    """Create and load one FlowgraphSession from a `.grc` path.

    Returns a structured error payload on I/O or parse failures instead of
    leaking raw OS or YAML exceptions.
    """
    session = FlowgraphSession()
    try:
        session.load(file_path)
    except (FileNotFoundError, PermissionError, OSError) as exc:
        return build_error_payload(
            error_type=ErrorCode.FILE_LOAD_ERROR,
            message=str(exc),
        )
    except (ValueError, yaml.YAMLError) as exc:
        return build_error_payload(
            error_type=ErrorCode.INVALID_GRC,
            message=str(exc),
        )
    return session
