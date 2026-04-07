from dataclasses import dataclass, field
from typing import Any


@dataclass
class Block:
    """Represents one GNU Radio block instance."""

    instance_name: str
    block_type: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class Connection:
    """Represents one wire between two block ports."""

    src_block: str
    src_port: int
    dst_block: str
    dst_port: int


@dataclass
class Flowgraph:
    """In-memory model of a full flowgraph."""

    blocks: list[Block] = field(default_factory=list)
    connections: list[Connection] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    raw_data: dict[str, Any] = field(default_factory=dict)