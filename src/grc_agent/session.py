"""Read-oriented session inspection: load.

The flowgraph edit / insertion / context machinery lives elsewhere:
- ``grc_agent.runtime.inspect_graph`` is the read path the MVP ``inspect_graph``
  tool delegates to (Stage A + B filtered ``GrcFlowgraph``).
- ``grc_agent.runtime.change_graph`` is the write path the MVP ``change_graph``
  tool delegates to (flat batch mutations via the native GRC adapter).

This module keeps only ``load_grc`` (called by the GUI / startup).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from grc_agent.domain_models import ErrorCode, build_error_payload
from grc_agent.flowgraph_session import FlowgraphSession

__all__ = ["load_grc"]


def load_grc(file_path: str | Path) -> FlowgraphSession | dict[str, Any]:
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
