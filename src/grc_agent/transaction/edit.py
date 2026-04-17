"""Ordered transaction application helpers."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from grc_agent.flowgraph_session import FlowgraphSession


@dataclass(frozen=True)
class AffectedChanges:
    """The blocks and connections touched by one transaction."""

    blocks: tuple[str, ...]
    connections: tuple[tuple[str, int, str, int], ...]

    def to_payload(self) -> dict[str, Any]:
        return {
            "affected_blocks": list(self.blocks),
            "affected_connections": [
                {
                    "src_block": src_block,
                    "src_port": src_port,
                    "dst_block": dst_block,
                    "dst_port": dst_port,
                }
                for src_block, src_port, dst_block, dst_port in self.connections
            ],
        }


def apply_operations(
    session: FlowgraphSession,
    operations: list[dict[str, Any]],
) -> AffectedChanges:
    """Apply one normalized ordered transaction to a candidate session.

    This path trusts Phase 4 preflight validation to reject unsupported
    port/domain/dtype/occupancy combinations before apply time. It only applies
    the normalized operation list to the candidate `FlowgraphSession`.
    """
    affected_blocks: set[str] = set()
    affected_connections: set[tuple[str, int, str, int]] = set()

    for operation in operations:
        op_type = operation["op_type"]
        if op_type == "update_params":
            instance_name = operation["instance_name"]
            affected_blocks.add(instance_name)
            for parameter_key, value in operation["params"].items():
                session.set_param(instance_name, parameter_key, copy.deepcopy(value))
            continue

        if op_type == "add_connection":
            connection = (
                operation["src_block"],
                operation["src_port"],
                operation["dst_block"],
                operation["dst_port"],
            )
            session.connect(*connection)
            affected_blocks.update((connection[0], connection[2]))
            affected_connections.add(connection)
            continue

        if op_type == "remove_connection":
            connection = (
                operation["src_block"],
                operation["src_port"],
                operation["dst_block"],
                operation["dst_port"],
            )
            session.disconnect(*connection)
            affected_blocks.update((connection[0], connection[2]))
            affected_connections.add(connection)
            continue

        if op_type == "remove_block":
            instance_name = operation["instance_name"]
            session.remove_block(instance_name)
            affected_blocks.add(instance_name)
            continue

        if op_type == "add_block":
            instance_name = operation["instance_name"]
            session.add_block(
                instance_name,
                operation["block_type"],
                copy.deepcopy(operation["parameters"]),
                copy.deepcopy(operation.get("states")),
            )
            affected_blocks.add(instance_name)
            continue

        raise ValueError(f"Unsupported transaction op_type: {op_type}")

    return AffectedChanges(
        blocks=tuple(sorted(affected_blocks)),
        connections=tuple(sorted(affected_connections)),
    )
