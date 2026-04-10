"""Typed in-memory models for parsed GNU Radio flowgraphs."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Block:
    """Represents one GNU Radio block instance."""

    # Human-readable instance label from the .grc file.
    instance_name: str
    # GNU Radio block class id from the .grc file.
    block_type: str
    # The full nested block payload is preserved here for future use.
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class Connection:
    """Represents one wire between two block ports."""

    # Name of the block sending data.
    src_block: str
    # Output port number on the source block.
    src_port: int
    # Name of the block receiving data.
    dst_block: str
    # Input port number on the destination block.
    dst_port: int


@dataclass
class Flowgraph:
    """In-memory model of a full flowgraph."""

    # Parsed block objects in file order.
    blocks: list[Block] = field(default_factory=list)
    # Parsed connection objects in file order.
    connections: list[Connection] = field(default_factory=list)
    # Top-level sections other than blocks and connections.
    metadata: dict[str, Any] = field(default_factory=dict)
    # Full raw parsed YAML for round-trip safety later.
    raw_data: dict[str, Any] = field(default_factory=dict)