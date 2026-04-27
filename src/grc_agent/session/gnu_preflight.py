"""Thin GNU Radio GRC validation wrapper for fast preflight checks.

Runs ``FlowGraph.validate()`` in-process before falling back to ``grcc``.
This is an optional layer — ``grcc`` remains the final truth.
"""

from __future__ import annotations

import logging
from typing import Any

from grc_agent.models import Block, Connection

logger = logging.getLogger(__name__)


class GnuPreflightResult:
    """Result container for GNU Radio preflight validation."""

    def __init__(self, ok: bool, errors: list[str], warnings: list[str]) -> None:
        self.ok = ok
        self.errors = errors
        self.warnings = warnings


def validate_graph(
    blocks: list[Block], connections: list[Connection]
) -> GnuPreflightResult | None:
    """Run GNU Radio structural validation on a graph.

    Returns ``None`` when the GNU API is unavailable (fallback to grcc).
    """
    from grc_agent.session.gnu_loader import _ensure_platform

    platform = _ensure_platform()
    if platform is None:
        logger.debug("GNU Platform unavailable; skipping preflight")
        return None

    try:
        fg = platform.make_flow_graph()
        raw = _to_raw_data(blocks, connections)
        fg.import_data(raw)
        fg.validate()

        errors: list[str] = []
        warnings: list[str] = []
        for msg in fg.iter_error_messages():
            level = getattr(msg, "level", "error")
            text = str(msg)
            if level in ("error", "critical"):
                errors.append(text)
            else:
                warnings.append(text)

        return GnuPreflightResult(ok=len(errors) == 0, errors=errors, warnings=warnings)
    except Exception as exc:
        logger.debug("GNU preflight validation failed: %s", exc)
        return None


def _to_raw_data(blocks: list[Block], connections: list[Connection]) -> dict[str, Any]:
    """Rebuild a minimal GRC raw dict from our typed models."""
    raw_blocks: list[dict[str, Any]] = []
    for block in blocks:
        raw: dict[str, Any] = {
            "name": block.instance_name,
            "id": block.block_type,
        }
        params = block.params.get("parameters") if isinstance(block.params, dict) else None
        if isinstance(params, dict):
            raw["parameters"] = dict(params)
        states = block.params.get("states") if isinstance(block.params, dict) else None
        if isinstance(states, dict):
            raw["states"] = dict(states)
        raw_blocks.append(raw)

    raw_connections: list[list[Any]] = []
    for conn in connections:
        raw_connections.append(
            [conn.src_block, str(conn.src_port), conn.dst_block, str(conn.dst_port)]
        )

    return {
        "options": {
            "parameters": {"id": "top_block", "generate_options": "qt_gui", "output_language": "python"},
            "states": {"state": "enabled", "coordinate": [0, 0], "rotation": 0},
        },
        "blocks": raw_blocks,
        "connections": raw_connections,
        "metadata": {"file_format": 1},
    }
