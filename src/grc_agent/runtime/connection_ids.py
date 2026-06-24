"""Connection-ID format helpers.

Single source of truth for the ``src_block:src_port->dst_block:dst_port``
wire format used across the agent, GUI, and tests. Extracted from the former
``session_ops.py`` so connection-ID logic lives in the runtime layer it serves.
"""

from __future__ import annotations

from typing import Any

ConnectionPort = int | str


def connection_id(
    src_block: str,
    src_port: ConnectionPort,
    dst_block: str,
    dst_port: ConnectionPort,
) -> str:
    return f"{src_block}:{src_port}->{dst_block}:{dst_port}"


def parse_connection_id(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, str) or "->" not in value:
        return None
    src_text, dst_text = value.split("->", 1)
    if ":" not in src_text or ":" not in dst_text:
        return None
    src_block, src_port_text = src_text.rsplit(":", 1)
    dst_block, dst_port_text = dst_text.rsplit(":", 1)
    if not src_block or not dst_block:
        return None
    src_port: ConnectionPort
    dst_port: ConnectionPort
    try:
        src_port = int(src_port_text)
    except ValueError:
        src_port = src_port_text
    try:
        dst_port = int(dst_port_text)
    except ValueError:
        dst_port = dst_port_text
    return {
        "src_block": src_block,
        "src_port": src_port,
        "dst_block": dst_block,
        "dst_port": dst_port,
    }
