"""Phase 6 — minimal session utilities. Legacy shared_* helpers removed."""
from __future__ import annotations
from typing import Any


def connection_id(connection: Any) -> str:
    """Render a connection to its wire-format string."""
    src_name = connection.source_block.name or connection.source_block.key
    dst_name = connection.sink_block.name or connection.sink_block.key
    sp = connection.source_port.key
    dp = connection.sink_port.key
    return f"{src_name}:{sp}->{dst_name}:{dp}"


def parse_connection_id(connection_id: str) -> dict[str, Any] | None:
    """Parse ``blk:port->blk:port`` into {src_block, src_port, dst_block, dst_port}."""
    if not isinstance(connection_id, str):
        return None
    parts = connection_id.strip().split("->")
    if len(parts) != 2:
        return None
    src, dst = parts[0].strip(), parts[1].strip()
    src_parts = src.rsplit(":", 1)
    dst_parts = dst.rsplit(":", 1)
    if len(src_parts) != 2 or len(dst_parts) != 2:
        return None
    return {"src_block": src_parts[0], "src_port": src_parts[1],
            "dst_block": dst_parts[0], "dst_port": dst_parts[1]}
