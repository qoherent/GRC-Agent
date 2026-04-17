"""Shared internal session operations used by FlowgraphSession and validation."""

from __future__ import annotations

import re
from typing import Any

from .models import Block, Connection

FLOWGRAPH_SESSION_SHARED_PRIVATE_METHODS: tuple[str, ...] = (
    "_parse_blocks",
    "_parse_connections",
    "_default_block_states",
    "_block_name_is_referenced_elsewhere",
    "_connection_entry_to_tuple",
    "_raw_connection_entry",
)


def parse_blocks(blocks_data: Any) -> list[Block]:
    """Parse the raw `blocks` section into typed block objects."""
    blocks: list[Block] = []
    if blocks_data is None:
        return blocks
    if not isinstance(blocks_data, list):
        raise ValueError("Flowgraph blocks section must be a list.")

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
        blocks.append(
            Block(instance_name=instance_name, block_type=block_type, params=params)
        )

    return blocks


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

        src_block, src_port, dst_block, dst_port = entry
        if not isinstance(src_block, str) or not isinstance(dst_block, str):
            raise ValueError(
                f"Malformed connection entry at index {index}: block names must be strings."
            )

        try:
            src_port_number = int(src_port)
            dst_port_number = int(dst_port)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Malformed connection entry at index {index}: ports must be integers."
            ) from exc

        connections.append(
            Connection(
                src_block=src_block,
                src_port=src_port_number,
                dst_block=dst_block,
                dst_port=dst_port_number,
            )
        )

    return connections


def raw_connection_entry(
    src_block: str,
    src_port: int,
    dst_block: str,
    dst_port: int,
) -> list[str]:
    """Build the on-disk four-item connection entry used by `.grc` files."""
    return [src_block, str(src_port), dst_block, str(dst_port)]


def default_block_states(existing_block_count: int) -> dict[str, Any]:
    """Return the minimal default `states` payload for generated blocks."""
    return {
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


def connection_entry_to_tuple(entry: Any) -> tuple[str, int, str, int] | None:
    """Normalize one raw connection entry to the typed tuple form."""
    if not isinstance(entry, list) or len(entry) != 4:
        return None

    src_block, src_port, dst_block, dst_port = entry
    if not isinstance(src_block, str) or not isinstance(dst_block, str):
        return None

    try:
        return (src_block, int(src_port), dst_block, int(dst_port))
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


__all__ = [
    "FLOWGRAPH_SESSION_SHARED_PRIVATE_METHODS",
    "block_name_is_referenced_elsewhere",
    "connection_entry_to_tuple",
    "default_block_states",
    "parse_blocks",
    "parse_connections",
    "raw_connection_entry",
]
