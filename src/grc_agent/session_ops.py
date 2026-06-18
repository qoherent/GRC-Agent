"""Shared internal session operations used by FlowgraphSession and validation."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import defaultdict
from typing import Any

from ToolAgents.data_models.messages import ChatMessage

from ._payload import Block, Connection

logger = logging.getLogger(__name__)

FLOWGRAPH_SESSION_SHARED_PRIVATE_METHODS: tuple[str, ...] = (
    "_parse_blocks",
    "_parse_connections",
    "_default_block_states",
    "_block_name_is_referenced_elsewhere",
    "_connection_entry_to_tuple",
    "_raw_connection_entry",
)

DISPLAY_ROLES: frozenset[str] = frozenset(
    {"user", "assistant", "tool_started", "tool_finished", "mutation", "error"}
)
MODEL_ROLES: frozenset[str] = frozenset({"assistant_model", "tool_model"})

ASSISTANT_MODEL_ROLE = "assistant_model"
TOOL_MODEL_ROLE = "tool_model"

ConnectionPort = int | str
"""Type alias for a port that is either an integer index (stream) or a string name (message)."""


def _normalize_port(value: Any) -> ConnectionPort:
    """Return *value* as ``int`` when it looks numeric, otherwise ``str``."""
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


def connection_id(
    src_block: str,
    src_port: ConnectionPort,
    dst_block: str,
    dst_port: ConnectionPort,
) -> str:
    """Return the stable public connection identifier for one edge."""
    return f"{src_block}:{src_port}->{dst_block}:{dst_port}"


def parse_connection_id(value: Any) -> tuple[str, ConnectionPort, str, ConnectionPort] | None:
    """Parse one stable connection identifier back into endpoint fields."""
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

    if isinstance(src_port, int) and src_port < 0:
        return None
    if isinstance(dst_port, int) and dst_port < 0:
        return None
    return (src_block, src_port, dst_block, dst_port)


def parse_blocks(blocks_data: Any) -> list[Block]:
    """Parse the raw `blocks` section into typed block objects."""
    blocks: list[Block] = []
    if blocks_data is None:
        return blocks
    if not isinstance(blocks_data, list):
        raise ValueError("Flowgraph blocks section must be a list.")

    uid_occurrences: dict[str, int] = defaultdict(int)
    for index, entry in enumerate(blocks_data):
        if not isinstance(entry, dict):
            raise ValueError(
                f"Malformed block entry at index {index}: expected a mapping."
            )

        instance_name = entry.get("name")
        block_type = entry.get("id")
        if not isinstance(instance_name, str) or not isinstance(block_type, str):
            raise ValueError(
                f"Malformed block entry at index {index}: missing name or id."
            )

        params = {key: value for key, value in entry.items() if key not in {"name", "id"}}
        uid_key = _block_uid_key(entry)
        occurrence = uid_occurrences[uid_key]
        uid_occurrences[uid_key] += 1
        blocks.append(
            Block(
                instance_name=instance_name,
                block_type=block_type,
                params=params,
                block_uid=stable_block_uid(entry, occurrence=occurrence),
            )
        )

    return blocks


def stable_block_uid(raw_block: dict[str, Any], *, occurrence: int = 0) -> str:
    """Return a deterministic internal ID for one source block entry.

    The UID is intentionally read-only. It helps inspection/evals distinguish
    duplicate display names without authorizing mutation by hidden handles.
    """
    payload = {
        "name": raw_block.get("name"),
        "id": raw_block.get("id"),
        "coordinate": _raw_block_coordinate(raw_block),
        "rotation": _raw_block_rotation(raw_block),
        "occurrence": occurrence,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    # No 'block:' prefix — that prefix was leaking into model-visible
    # fields, and the consumer had to strip it. The bare 16-char hex
    # hash is enough to be stable + collision-resistant for the size
    # of a flowgraph's block set.
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def _block_uid_key(raw_block: dict[str, Any]) -> str:
    payload = {
        "name": raw_block.get("name"),
        "id": raw_block.get("id"),
        "coordinate": _raw_block_coordinate(raw_block),
        "rotation": _raw_block_rotation(raw_block),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _raw_block_coordinate(raw_block: dict[str, Any]) -> Any:
    states = raw_block.get("states")
    return states.get("coordinate") if isinstance(states, dict) else None


def _raw_block_rotation(raw_block: dict[str, Any]) -> Any:
    states = raw_block.get("states")
    return states.get("rotation") if isinstance(states, dict) else None


def parse_connections(connections_data: Any) -> list[Connection]:
    """Parse the raw `connections` section into typed connection objects."""
    connections: list[Connection] = []
    if connections_data is None:
        return connections
    if not isinstance(connections_data, list):
        raise ValueError("Flowgraph connections section must be a list.")

    for index, entry in enumerate(connections_data):
        if not isinstance(entry, list) or len(entry) != 4:
            raise ValueError(
                f"Malformed connection entry at index {index}: expected four items."
            )

        src_block, src_port_raw, dst_block, dst_port_raw = entry
        if not isinstance(src_block, str) or not isinstance(dst_block, str):
            raise ValueError(
                f"Malformed connection entry at index {index}: block names must be strings."
            )

        src_port = _normalize_port(src_port_raw)
        dst_port = _normalize_port(dst_port_raw)

        connections.append(
            Connection(
                src_block=src_block,
                src_port=src_port,
                dst_block=dst_block,
                dst_port=dst_port,
            )
        )

    return connections


def raw_connection_entry(
    src_block: str,
    src_port: ConnectionPort,
    dst_block: str,
    dst_port: ConnectionPort,
) -> list[str]:
    """Build the on-disk four-item connection entry used by `.grc` files."""
    return [src_block, str(src_port), dst_block, str(dst_port)]


def default_block_states(existing_block_count: int) -> dict[str, Any]:
    """Return the minimal default `states` payload for generated blocks."""
    return {
        "bus_sink": False,
        "bus_source": False,
        "bus_structure": None,
        "coordinate": [8, 8 + (existing_block_count * 24)],
        "rotation": 0,
        "state": "enabled",
    }


def block_name_is_referenced_elsewhere(
    raw_data: Any,
    instance_name: str,
    ignored_raw_block_index: int,
) -> bool:
    """Check whether a block name still appears in other raw expressions."""
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


def connection_entry_to_tuple(entry: Any) -> tuple[str, ConnectionPort, str, ConnectionPort] | None:
    """Normalize one raw connection entry to the typed tuple form."""
    if not isinstance(entry, list) or len(entry) != 4:
        return None

    src_block, src_port_raw, dst_block, dst_port_raw = entry
    if not isinstance(src_block, str) or not isinstance(dst_block, str):
        return None

    try:
        return (src_block, _normalize_port(src_port_raw), dst_block, _normalize_port(dst_port_raw))
    except (TypeError, ValueError):
        return None


def _value_references_identifier(value: Any, identifier: str) -> bool:
    pattern = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(identifier)}(?![A-Za-z0-9_])")

    if isinstance(value, str):
        return pattern.search(value) is not None
    if isinstance(value, dict):
        return any(
            _value_references_identifier(nested_value, identifier)
            for nested_value in value.values()
        )
    if isinstance(value, list):
        return any(
            _value_references_identifier(nested_value, identifier)
            for nested_value in value
        )
    return False


# ---------------------------------------------------------------------------
# Session-store model-side helpers (from session_roles.py)
# ---------------------------------------------------------------------------


def chat_message_payload(message: ChatMessage) -> dict[str, Any]:
    """Serialize a ``ChatMessage`` for the ``payload`` column."""
    return message.model_dump(mode="json")


def chat_message_from_payload(payload: dict[str, Any] | None) -> ChatMessage | None:
    """Deserialize a ``ChatMessage`` from a ``payload`` column value."""
    if not isinstance(payload, dict):
        return None
    try:
        return ChatMessage.from_dict(payload)
    except Exception as exc:
        logger.warning("Failed to decode ChatMessage payload: %s", exc)
        return None


__all__ = [
    "ASSISTANT_MODEL_ROLE",
    "ConnectionPort",
    "connection_id",
    "DISPLAY_ROLES",
    "FLOWGRAPH_SESSION_SHARED_PRIVATE_METHODS",
    "MODEL_ROLES",
    "TOOL_MODEL_ROLE",
    "block_name_is_referenced_elsewhere",
    "chat_message_from_payload",
    "chat_message_payload",
    "connection_entry_to_tuple",
    "default_block_states",
    "parse_connection_id",
    "parse_blocks",
    "parse_connections",
    "raw_connection_entry",
    "stable_block_uid",
]
