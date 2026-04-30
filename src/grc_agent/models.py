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
    # Deterministic internal identity derived from the source block entry.
    # This is read-only identity evidence, not a mutation handle.
    block_uid: str = ""


@dataclass
class Connection:
    """Represents one wire between two block ports.

    Stream connections use integer port indices.  Message connections
    use string port names (e.g. ``"strobe"``, ``"pdus"``).
    """

    src_block: str
    src_port: int | str
    dst_block: str
    dst_port: int | str


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
