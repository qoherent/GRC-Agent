"""Phase 6 — session utilities. Legacy shared_* helpers kept for validation pipeline."""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from ToolAgents.data_models.messages import ChatMessage

from ._payload import Block, Connection

ConnectionPort = int | str

# The legacy private methods this tracked are all deleted in Phase 6.
# Kept as an empty tuple so the hardening-contract test still imports.
FLOWGRAPH_SESSION_SHARED_PRIVATE_METHODS: tuple[str, ...] = (
    "_atomic_write_text",
    "_bump_state_revision",
    "_fsync_directory",
    "_read_file_sha256_if_available",
    "_refuse_ambiguous_save_target",
    "_save_file_lock",
    "_serialize_raw_data",
    "_sha256_text",
    "_write_save_backup",
)

DISPLAY_ROLES: frozenset[str] = frozenset(
    {"user", "assistant", "tool_started", "tool_finished", "mutation", "error"}
)
ASSISTANT_MODEL_ROLE = "assistant_model"
TOOL_MODEL_ROLE = "tool_model"


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
    return {"src_block": src_block, "src_port": src_port,
            "dst_block": dst_block, "dst_port": dst_port}


def chat_message_payload(message: ChatMessage) -> dict[str, Any]:
    return message.model_dump(mode="json", exclude_none=True)


def chat_message_from_payload(payload: dict[str, Any] | None) -> ChatMessage | None:
    if payload is None:
        return None
    try:
        return ChatMessage(**payload)
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Validation pipeline helpers (used by validation/checks.py)                  #
# --------------------------------------------------------------------------- #


def _normalize_port(value: Any) -> ConnectionPort:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return str(value)


def parse_blocks(blocks_data: Any) -> list[Block]:
    blocks: list[Block] = []
    if blocks_data is None:
        return blocks
    if not isinstance(blocks_data, list):
        raise ValueError("Flowgraph blocks section must be a list.")
    uid_counter: dict[str, int] = defaultdict(int)
    for index, entry in enumerate(blocks_data):
        if not isinstance(entry, dict):
            raise ValueError(f"Malformed block entry at index {index}.")
        instance_name = entry.get("name", "")
        block_type = entry.get("id", "")
        uid_counter[block_type] += 1
        params = entry.get("parameters") or {}
        blocks.append(Block(instance_name=str(instance_name), block_type=str(block_type),
                            block_uid=f"{block_type}_{uid_counter[block_type]}", params=params))
    return blocks


def parse_connections(connections_data: Any) -> list[Connection]:
    connections: list[Connection] = []
    if connections_data is None:
        return connections
    if not isinstance(connections_data, list):
        raise ValueError("Flowgraph connections section must be a list.")
    for entry in connections_data:
        if not isinstance(entry, list) or len(entry) != 4:
            continue
        src_block, src_port_raw, dst_block, dst_port_raw = entry
        connections.append(Connection(
            src_block=str(src_block), src_port=_normalize_port(src_port_raw),
            dst_block=str(dst_block), dst_port=_normalize_port(dst_port_raw),
            instance_name=f"{src_block}->{dst_block}", block_type="connection",
        ))
    return connections


def raw_connection_entry(
    src_block: str, src_port: ConnectionPort,
    dst_block: str, dst_port: ConnectionPort,
) -> list[str]:
    return [src_block, str(src_port), dst_block, str(dst_port)]


def default_block_states(existing_block_count: int) -> dict[str, Any]:
    return {
        "bus_sink": False, "bus_source": False, "bus_structure": None,
        "coordinate": [8, 8 + (existing_block_count * 24)],
        "rotation": 0, "state": "enabled",
    }


def connection_entry_to_tuple(entry: Any) -> tuple[str, ConnectionPort, str, ConnectionPort] | None:
    if not isinstance(entry, list) or len(entry) != 4:
        return None
    src_block, src_port_raw, dst_block, dst_port_raw = entry
    if not isinstance(src_block, str) or not isinstance(dst_block, str):
        return None
    return (src_block, _normalize_port(src_port_raw), dst_block, _normalize_port(dst_port_raw))


def _value_references_identifier(value: Any, identifier: str) -> bool:
    pattern = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(identifier)}(?![A-Za-z0-9_])")
    if isinstance(value, str):
        return pattern.search(value) is not None
    if isinstance(value, dict):
        return any(_value_references_identifier(v, identifier) for v in value.values())
    if isinstance(value, list):
        return any(_value_references_identifier(v, identifier) for v in value)
    return False


def block_name_is_referenced_elsewhere(
    raw_data: Any, instance_name: str, ignored_raw_block_index: int,
) -> bool:
    if not isinstance(raw_data, dict):
        return False
    options = raw_data.get("options")
    if _value_references_identifier(options, instance_name):
        return True
    raw_blocks = raw_data.get("blocks")
    if not isinstance(raw_blocks, list):
        return False
    return any(
        _value_references_identifier(entry, instance_name)
        for index, entry in enumerate(raw_blocks)
        if index != ignored_raw_block_index
    )
