from pathlib import Path
from typing import Any
import warnings

import yaml

from .models import Block, Connection, Flowgraph


class FlowgraphSession:
    """Owns one `.grc` flowgraph session."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else None
        self.flowgraph: Flowgraph | None = None
        self.is_dirty = False

    def load(self, path: str | Path) -> None:
        source_path = Path(path)
        raw_text = source_path.read_text(encoding="utf-8")
        raw_data = yaml.safe_load(raw_text)

        if not isinstance(raw_data, dict):
            raise ValueError("Top-level .grc data must be a mapping.")

        blocks = self._parse_blocks(raw_data.get("blocks"))
        connections = self._parse_connections(raw_data.get("connections"))
        metadata = {key: value for key, value in raw_data.items() if key not in {"blocks", "connections"}}

        self.path = source_path
        self.flowgraph = Flowgraph(
            blocks=blocks,
            connections=connections,
            metadata=metadata,
            raw_data=raw_data,
        )
        self.is_dirty = False

    def save(self, path: str | Path | None = None) -> None:
        raise NotImplementedError

    def validate(self) -> bool:
        raise NotImplementedError

    def summarize(self) -> str:
        if self.flowgraph is None:
            return "No flowgraph loaded."

        file_name = self.path.name if self.path is not None else "<unspecified>"
        lines = [
            f"File: {file_name}",
            f"Blocks: {len(self.flowgraph.blocks)}",
            f"Connections: {len(self.flowgraph.connections)}",
            "Block list:",
        ]

        if self.flowgraph.blocks:
            lines.extend(
                f"- {block.instance_name} ({block.block_type})" for block in self.flowgraph.blocks
            )
        else:
            lines.append("- none")

        return "\n".join(lines)

    @staticmethod
    def _parse_blocks(blocks_data: Any) -> list[Block]:
        blocks: list[Block] = []

        if blocks_data is None:
            return blocks

        if not isinstance(blocks_data, list):
            warnings.warn("Skipping blocks section because it is not a list.", stacklevel=3)
            return blocks

        for index, entry in enumerate(blocks_data):
            if not isinstance(entry, dict):
                warnings.warn(
                    f"Skipping malformed block entry at index {index}: expected a mapping.",
                    stacklevel=3,
                )
                continue

            instance_name = entry.get("name")
            block_type = entry.get("id")

            if not isinstance(instance_name, str) or not isinstance(block_type, str):
                warnings.warn(
                    f"Skipping malformed block entry at index {index}: missing name or id.",
                    stacklevel=3,
                )
                continue

            params = {key: value for key, value in entry.items() if key not in {"name", "id"}}
            blocks.append(Block(instance_name=instance_name, block_type=block_type, params=params))

        return blocks

    @staticmethod
    def _parse_connections(connections_data: Any) -> list[Connection]:
        connections: list[Connection] = []

        if connections_data is None:
            return connections

        if not isinstance(connections_data, list):
            warnings.warn("Skipping connections section because it is not a list.", stacklevel=3)
            return connections

        for index, entry in enumerate(connections_data):
            if not isinstance(entry, list) or len(entry) != 4:
                warnings.warn(
                    f"Skipping malformed connection entry at index {index}: expected four items.",
                    stacklevel=3,
                )
                continue

            src_block, src_port, dst_block, dst_port = entry

            if not isinstance(src_block, str) or not isinstance(dst_block, str):
                warnings.warn(
                    f"Skipping malformed connection entry at index {index}: block names must be strings.",
                    stacklevel=3,
                )
                continue

            try:
                src_port_number = int(src_port)
                dst_port_number = int(dst_port)
            except (TypeError, ValueError):
                warnings.warn(
                    f"Skipping malformed connection entry at index {index}: ports must be integers.",
                    stacklevel=3,
                )
                continue

            connections.append(
                Connection(
                    src_block=src_block,
                    src_port=src_port_number,
                    dst_block=dst_block,
                    dst_port=dst_port_number,
                )
            )

        return connections