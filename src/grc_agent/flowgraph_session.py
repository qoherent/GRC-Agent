from pathlib import Path
import subprocess
import tempfile
from typing import Any
import warnings

import yaml

from .models import Block, Connection, Flowgraph


class FlowgraphSession:
    """Owns one `.grc` flowgraph session."""

    def __init__(self, path: str | Path | None = None) -> None:
        # Store the loaded path separately from the parsed flowgraph data.
        self.path = Path(path) if path is not None else None
        # This holds the parsed .grc content after load() succeeds.
        self.flowgraph: Flowgraph | None = None
        # The session starts clean because nothing has been edited yet.
        self.is_dirty = False

    def load(self, path: str | Path) -> None:
        # Convert the incoming path so file operations are easy and consistent.
        source_path = Path(path)
        # Read the file as text because .grc files are YAML documents.
        raw_text = source_path.read_text(encoding="utf-8")
        # Parse the YAML into regular Python objects.
        raw_data = yaml.safe_load(raw_text)

        # The top level must be a mapping so named sections can be accessed.
        if not isinstance(raw_data, dict):
            raise ValueError("Top-level .grc data must be a mapping.")

        # Parse the sections we understand into typed objects.
        blocks = self._parse_blocks(raw_data.get("blocks"))
        connections = self._parse_connections(raw_data.get("connections"))
        # Keep the rest of the top-level content as metadata.
        metadata = {
            key: value
            for key, value in raw_data.items()
            if key not in {"blocks", "connections"}
        }

        # Remember the file that was loaded.
        self.path = source_path
        # Store the parsed data in memory.
        self.flowgraph = Flowgraph(
            blocks=blocks,
            connections=connections,
            metadata=metadata,
            raw_data=raw_data,
        )
        # A fresh load is clean because nothing has been edited yet.
        self.is_dirty = False

    def save(self, path: str | Path | None = None) -> None:
        # Refuse to save if no flowgraph has been loaded yet.
        if self.flowgraph is None:
            raise ValueError("No flowgraph loaded.")

        # Use the explicit path when provided, otherwise fall back to the session path.
        target_path = Path(path) if path is not None else self.path
        if target_path is None:
            raise ValueError("No save path provided and no session path is set.")

        # Serialize the current YAML once so save and validate stay consistent.
        serialized = self._serialize_raw_data(self.flowgraph.raw_data)

        # Make sure the destination directory exists before writing the file.
        target_path.parent.mkdir(parents=True, exist_ok=True)

        # Write the YAML text back to disk.
        target_path.write_text(serialized, encoding="utf-8")

        # Saving makes the new path the active session path and clears the dirty flag.
        self.path = target_path
        self.is_dirty = False

    def validate(self) -> bool:
        # Refuse to validate if no flowgraph has been loaded yet.
        if self.flowgraph is None:
            raise ValueError("No flowgraph loaded.")

        # Serialize the current YAML once so the temporary file matches save().
        serialized = self._serialize_raw_data(self.flowgraph.raw_data)

        # Use a temporary directory so validation does not touch project files.
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a throwaway .grc file for grcc to compile.
            temp_path = Path(tmpdir) / "validate.grc"
            temp_path.write_text(serialized, encoding="utf-8")

            # Run grcc and return True only when it exits successfully.
            result = subprocess.run(
                ["grcc", str(temp_path)],
                capture_output=True,
                text=True,
                cwd=tmpdir,
            )
            # For now, keep validation simple and expose only pass/fail.
            return result.returncode == 0

    def disconnect(self, src_block: str, src_port: int, dst_block: str, dst_port: int) -> None:
        """Remove exactly one connection from both the model and raw YAML."""
        # Refuse to mutate anything if no graph has been loaded yet.
        if self.flowgraph is None:
            raise ValueError("No flowgraph loaded.")

        # Keep the target in one tuple so both representations use the same match key.
        target = (src_block, src_port, dst_block, dst_port)

        # Find the matching parsed connection first.
        match_index = next(
            (
                index
                for index, connection in enumerate(self.flowgraph.connections)
                if (
                    connection.src_block,
                    connection.src_port,
                    connection.dst_block,
                    connection.dst_port,
                ) == target
            ),
            None,
        )
        if match_index is None:
            raise ValueError(f"Connection not found: {target}")

        # The raw YAML must contain a list of connection entries.
        raw_connections = self.flowgraph.raw_data.get("connections")
        if not isinstance(raw_connections, list):
            raise ValueError("Flowgraph raw_data connections section is invalid.")

        # Find the matching raw connection before making any changes.
        raw_match_index = next(
            (
                index
                for index, entry in enumerate(raw_connections)
                if self._connection_entry_to_tuple(entry) == target
            ),
            None,
        )
        if raw_match_index is None:
            raise ValueError(f"Raw connection not found: {target}")

        # Remove the parsed connection and the raw YAML entry.
        del self.flowgraph.connections[match_index]
        del raw_connections[raw_match_index]

        # Any successful mutation means the in-memory session now differs from disk.
        self.is_dirty = True

    def set_param(self, instance_name: str, parameter_key: str, value: object) -> None:
        """Update one block parameter in both the model and raw YAML."""
        # Refuse to mutate anything if no graph has been loaded yet.
        if self.flowgraph is None:
            raise ValueError("No flowgraph loaded.")

        # Find the parsed block object by its instance name.
        block = next(
            (block for block in self.flowgraph.blocks if block.instance_name == instance_name),
            None,
        )
        if block is None:
            raise ValueError(f"Block not found: {instance_name}")

        # The nested parameters section is where block settings live.
        parameters = block.params.setdefault("parameters", {})
        if not isinstance(parameters, dict):
            raise ValueError(f"Block parameters section is invalid for: {instance_name}")
        parameters[parameter_key] = value

        # The raw YAML must change too so save() and validate() see the mutation.
        raw_blocks = self.flowgraph.raw_data.get("blocks")
        if not isinstance(raw_blocks, list):
            raise ValueError("Flowgraph raw_data blocks section is invalid.")

        # Find the matching raw block entry by its original name.
        raw_block = next(
            (
                entry
                for entry in raw_blocks
                if isinstance(entry, dict) and entry.get("name") == instance_name
            ),
            None,
        )
        if raw_block is None:
            raise ValueError(f"Raw block not found: {instance_name}")

        # Update the raw parameters mapping in the same way as the parsed model.
        raw_parameters = raw_block.setdefault("parameters", {})
        if not isinstance(raw_parameters, dict):
            raise ValueError(f"Raw block parameters section is invalid for: {instance_name}")
        raw_parameters[parameter_key] = value

        # Any successful mutation means the in-memory session now differs from disk.
        self.is_dirty = True

    def summarize(self) -> str:
        # If nothing has been loaded yet, say so plainly.
        if self.flowgraph is None:
            return "No flowgraph loaded."

        # Build a short diagnostic summary instead of a full report.
        file_name = self.path.name if self.path is not None else "<unspecified>"
        lines = [
            f"File: {file_name}",
            f"Blocks: {len(self.flowgraph.blocks)}",
            f"Connections: {len(self.flowgraph.connections)}",
            "Block list:",
        ]

        # List each block so the summary is easy to scan.
        if self.flowgraph.blocks:
            lines.extend(
                f"- {block.instance_name} ({block.block_type})" for block in self.flowgraph.blocks
            )
        else:
            lines.append("- none")

        return "\n".join(lines)

    @staticmethod
    def _parse_blocks(blocks_data: Any) -> list[Block]:
        # Start with an empty result and add valid blocks one by one.
        blocks: list[Block] = []

        # A missing blocks section is okay; it just means there are no blocks.
        if blocks_data is None:
            return blocks

        # If the section shape is wrong, skip it and warn instead of failing the load.
        if not isinstance(blocks_data, list):
            warnings.warn("Skipping blocks section because it is not a list.", stacklevel=3)
            return blocks

        # Inspect each block entry in order.
        for index, entry in enumerate(blocks_data):
            # Skip anything that is not a mapping.
            if not isinstance(entry, dict):
                warnings.warn(
                    f"Skipping malformed block entry at index {index}: expected a mapping.",
                    stacklevel=3,
                )
                continue

            # The block name becomes the user-facing instance name.
            instance_name = entry.get("name")
            # The block id becomes the internal GNU Radio block type.
            block_type = entry.get("id")

            # Both fields must exist and both must be strings.
            if not isinstance(instance_name, str) or not isinstance(block_type, str):
                warnings.warn(
                    f"Skipping malformed block entry at index {index}: missing name or id.",
                    stacklevel=3,
                )
                continue

            # Preserve the rest of the block payload for future use.
            params = {key: value for key, value in entry.items() if key not in {"name", "id"}}
            # Build the typed Block object and keep it in file order.
            blocks.append(Block(instance_name=instance_name, block_type=block_type, params=params))

        return blocks

    @staticmethod
    def _parse_connections(connections_data: Any) -> list[Connection]:
        # Start with an empty result and add valid connections one by one.
        connections: list[Connection] = []

        # A missing connections section is okay; it just means there are no wires.
        if connections_data is None:
            return connections

        # If the section shape is wrong, skip it and warn instead of failing the load.
        if not isinstance(connections_data, list):
            warnings.warn("Skipping connections section because it is not a list.", stacklevel=3)
            return connections

        # Inspect each connection entry in order.
        for index, entry in enumerate(connections_data):
            # Each connection must be a 4-item list.
            if not isinstance(entry, list) or len(entry) != 4:
                warnings.warn(
                    f"Skipping malformed connection entry at index {index}: expected four items.",
                    stacklevel=3,
                )
                continue

            # The first and third items are block instance names.
            src_block, src_port, dst_block, dst_port = entry

            # Connection endpoints must refer to block names.
            if not isinstance(src_block, str) or not isinstance(dst_block, str):
                warnings.warn(
                    f"Skipping malformed connection entry at index {index}: block names must be strings.",
                    stacklevel=3,
                )
                continue

            # Ports are stored as strings in the file, so convert them to integers.
            try:
                src_port_number = int(src_port)
                dst_port_number = int(dst_port)
            except (TypeError, ValueError):
                warnings.warn(
                    f"Skipping malformed connection entry at index {index}: ports must be integers.",
                    stacklevel=3,
                )
                continue

            # Build the typed Connection object and keep it in file order.
            connections.append(
                Connection(
                    src_block=src_block,
                    src_port=src_port_number,
                    dst_block=dst_block,
                    dst_port=dst_port_number,
                )
            )

        return connections

    @staticmethod
    def _serialize_raw_data(raw_data: Any) -> str:
        # Save and validate both use the same YAML serialization rules.
        if not isinstance(raw_data, dict):
            raise ValueError("Flowgraph raw_data is missing or invalid.")

        # Keep the original key order and make the output readable.
        return yaml.safe_dump(raw_data, sort_keys=False, allow_unicode=True)

    @staticmethod
    def _connection_entry_to_tuple(entry: Any) -> tuple[str, int, str, int] | None:
        # Normalize a raw connection entry into the same tuple shape as the model.
        if not isinstance(entry, list) or len(entry) != 4:
            return None

        src_block, src_port, dst_block, dst_port = entry
        if not isinstance(src_block, str) or not isinstance(dst_block, str):
            return None

        try:
            return (src_block, int(src_port), dst_block, int(dst_port))
        except (TypeError, ValueError):
            return None