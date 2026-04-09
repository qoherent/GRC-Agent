import copy
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any

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
        # Diagnostic output from the most recent validate() call.
        self.last_validation_stdout: str | None = None
        self.last_validation_stderr: str | None = None
        self.last_validation_returncode: int | None = None

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

        # Clear previous diagnostics before running so stale results are never visible.
        self.last_validation_stdout = None
        self.last_validation_stderr = None
        self.last_validation_returncode = None

        # Run validation against the current raw YAML and persist the diagnostics.
        is_valid, stdout, stderr, returncode = self._run_grcc_validation(self.flowgraph.raw_data)
        self.last_validation_stdout = stdout
        self.last_validation_stderr = stderr
        self.last_validation_returncode = returncode
        return is_valid

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

    def connect(self, src_block: str, src_port: int, dst_block: str, dst_port: int) -> None:
        """Add exactly one connection to both the model and raw YAML."""
        # Refuse to mutate anything if no graph has been loaded yet.
        if self.flowgraph is None:
            raise ValueError("No flowgraph loaded.")

        # Keep the target in one tuple so both representations use the same match key.
        target = (src_block, src_port, dst_block, dst_port)

        # Require both endpoints to exist before adding a new wire.
        if not any(block.instance_name == src_block for block in self.flowgraph.blocks):
            raise ValueError(f"Source block not found: {src_block}")
        if not any(block.instance_name == dst_block for block in self.flowgraph.blocks):
            raise ValueError(f"Destination block not found: {dst_block}")

        # Reject duplicates in the parsed model before touching any state.
        if any(
            (
                connection.src_block,
                connection.src_port,
                connection.dst_block,
                connection.dst_port,
            ) == target
            for connection in self.flowgraph.connections
        ):
            raise ValueError(f"Connection already exists: {target}")

        # The raw YAML must contain a list of connection entries when present.
        raw_connections = self.flowgraph.raw_data.get("connections")
        if raw_connections is not None and not isinstance(raw_connections, list):
            raise ValueError("Flowgraph raw_data connections section is invalid.")

        # Reject duplicates in the raw YAML before touching any state.
        if isinstance(raw_connections, list) and any(
            self._connection_entry_to_tuple(entry) == target for entry in raw_connections
        ):
            raise ValueError(f"Raw connection already exists: {target}")

        # Update the parsed model only after all validation passes.
        self.flowgraph.connections.append(
            Connection(
                src_block=src_block,
                src_port=src_port,
                dst_block=dst_block,
                dst_port=dst_port,
            )
        )

        # Mirror the new wire into the raw YAML so save() and validate() see it.
        raw_target = self._raw_connection_entry(src_block, src_port, dst_block, dst_port)
        if raw_connections is None:
            self.flowgraph.raw_data["connections"] = [raw_target]
        else:
            raw_connections.append(raw_target)

        # Any successful mutation means the in-memory session now differs from disk.
        self.is_dirty = True

    def add_block(
        self,
        instance_name: str,
        block_type: str,
        parameters: dict[str, Any],
        states: dict[str, Any] | None = None,
    ) -> None:
        """Add one detached variable block after validating a candidate graph."""
        # Refuse to mutate anything if no graph has been loaded yet.
        if self.flowgraph is None:
            raise ValueError("No flowgraph loaded.")

        # Keep the first implementation narrow and predictable.
        if not isinstance(instance_name, str) or not instance_name:
            raise ValueError("Block instance_name must be a non-empty string.")
        if block_type != "variable":
            raise ValueError(f"Unsupported block type for add_block: {block_type}")
        if not isinstance(parameters, dict):
            raise ValueError("Block parameters must be a mapping.")
        if "value" not in parameters:
            raise ValueError("Variable blocks require parameters['value'].")
        if states is not None and not isinstance(states, dict):
            raise ValueError("Block states must be a mapping when provided.")

        # The raw YAML must contain a list of block entries.
        raw_blocks = self.flowgraph.raw_data.get("blocks")
        if not isinstance(raw_blocks, list):
            raise ValueError("Flowgraph raw_data blocks section is invalid.")

        # Block names must stay unique in both representations.
        self._assert_new_block_name_available(instance_name, raw_blocks)

        # Build the raw block payload using the narrow variable-block defaults.
        raw_block, raw_parameters, raw_states = self._prepare_new_block_payload(
            instance_name=instance_name,
            block_type=block_type,
            parameters=parameters,
            states=states,
            existing_block_count=len(raw_blocks),
            add_default_comment=True,
        )

        # Validate a copied graph first so failures never partially mutate the session.
        candidate_raw_data = copy.deepcopy(self.flowgraph.raw_data)
        candidate_raw_blocks = candidate_raw_data.get("blocks")
        if not isinstance(candidate_raw_blocks, list):
            raise ValueError("Flowgraph candidate blocks section is invalid.")
        candidate_raw_blocks.append(copy.deepcopy(raw_block))

        self._validate_candidate_raw_data_or_raise(
            candidate_raw_data,
            error_prefix="Added block failed validation",
        )

        # Update the parsed model and raw YAML only after the candidate is accepted.
        self.flowgraph.blocks.append(
            Block(
                instance_name=instance_name,
                block_type=block_type,
                params={
                    "parameters": copy.deepcopy(raw_parameters),
                    "states": copy.deepcopy(raw_states),
                },
            )
        )
        raw_blocks.append(raw_block)

        # Any successful mutation means the in-memory session now differs from disk.
        self.is_dirty = True

    def add_and_connect_qtgui_time_sink(
        self,
        instance_name: str,
        parameters: dict[str, Any],
        src_block: str,
        src_port: int,
        states: dict[str, Any] | None = None,
    ) -> None:
        """Add one qtgui_time_sink_x block and connect its single input before commit."""
        # Refuse to mutate anything if no graph has been loaded yet.
        if self.flowgraph is None:
            raise ValueError("No flowgraph loaded.")

        # Keep the first stream workflow narrow and predictable.
        if not isinstance(instance_name, str) or not instance_name:
            raise ValueError("Block instance_name must be a non-empty string.")
        if not isinstance(parameters, dict):
            raise ValueError("Block parameters must be a mapping.")
        if not isinstance(src_block, str) or not src_block:
            raise ValueError("Source block name must be a non-empty string.")
        if not isinstance(src_port, int):
            raise ValueError("Source port must be an integer.")
        if states is not None and not isinstance(states, dict):
            raise ValueError("Block states must be a mapping when provided.")

        # The source endpoint must exist before we build the candidate graph.
        self._require_unique_parsed_block(src_block, role="Source")

        # The raw YAML must contain a list of block entries.
        raw_blocks = self.flowgraph.raw_data.get("blocks")
        if not isinstance(raw_blocks, list):
            raise ValueError("Flowgraph raw_data blocks section is invalid.")

        # Block names must stay unique in both representations.
        self._assert_new_block_name_available(instance_name, raw_blocks)

        # The connections section must be either absent or a list.
        raw_connections = self.flowgraph.raw_data.get("connections")
        if raw_connections is not None and not isinstance(raw_connections, list):
            raise ValueError("Flowgraph raw_data connections section is invalid.")

        # Build the raw block payload using caller-provided parameters and narrow defaults.
        raw_block, raw_parameters, raw_states = self._prepare_new_block_payload(
            instance_name=instance_name,
            block_type="qtgui_time_sink_x",
            parameters=parameters,
            states=states,
            existing_block_count=len(raw_blocks),
        )
        raw_connection = self._raw_connection_entry(src_block, src_port, instance_name, 0)

        # Validate a copied graph first so failures never partially mutate the session.
        candidate_raw_data = copy.deepcopy(self.flowgraph.raw_data)
        candidate_raw_blocks = candidate_raw_data.get("blocks")
        if not isinstance(candidate_raw_blocks, list):
            raise ValueError("Flowgraph candidate blocks section is invalid.")
        candidate_raw_blocks.append(copy.deepcopy(raw_block))

        self._append_raw_connections(
            candidate_raw_data,
            [raw_connection],
            error_context="Flowgraph candidate",
        )

        self._validate_candidate_raw_data_or_raise(
            candidate_raw_data,
            error_prefix="Added sink block failed validation",
        )

        # Update the parsed model and raw YAML only after the candidate is accepted.
        self.flowgraph.blocks.append(
            Block(
                instance_name=instance_name,
                block_type="qtgui_time_sink_x",
                params={
                    "parameters": copy.deepcopy(raw_parameters),
                    "states": copy.deepcopy(raw_states),
                },
            )
        )
        self.flowgraph.connections.append(
            Connection(
                src_block=src_block,
                src_port=src_port,
                dst_block=instance_name,
                dst_port=0,
            )
        )
        raw_blocks.append(raw_block)
        self._append_raw_connections(
            self.flowgraph.raw_data,
            [raw_connection],
            error_context="Flowgraph raw_data",
        )

        # Any successful mutation means the in-memory session now differs from disk.
        self.is_dirty = True

    def add_and_connect_char_to_float_to_qtgui_time_sink(
        self,
        instance_name: str,
        parameters: dict[str, Any],
        src_block: str,
        src_port: int,
        sink_block: str,
        states: dict[str, Any] | None = None,
    ) -> None:
        """Add one blocks_char_to_float tap into an existing qtgui_time_sink_x block."""
        # Refuse to mutate anything if no graph has been loaded yet.
        if self.flowgraph is None:
            raise ValueError("No flowgraph loaded.")

        # Keep the coordinated transform workflow narrow and predictable.
        if not isinstance(instance_name, str) or not instance_name:
            raise ValueError("Block instance_name must be a non-empty string.")
        if not isinstance(parameters, dict):
            raise ValueError("Block parameters must be a mapping.")
        if not isinstance(src_block, str) or not src_block:
            raise ValueError("Source block name must be a non-empty string.")
        if not isinstance(src_port, int):
            raise ValueError("Source port must be an integer.")
        if not isinstance(sink_block, str) or not sink_block:
            raise ValueError("Sink block name must be a non-empty string.")
        if states is not None and not isinstance(states, dict):
            raise ValueError("Block states must be a mapping when provided.")

        # The source endpoint must exist before we build the candidate graph.
        self._require_unique_parsed_block(src_block, role="Source")

        # Find the destination sink in the parsed model and keep the contract sink-specific.
        parsed_sink = self._require_unique_parsed_block(
            sink_block,
            role="Sink",
            expected_block_type="qtgui_time_sink_x",
        )

        parsed_sink_parameters = parsed_sink.params.get("parameters")
        if not isinstance(parsed_sink_parameters, dict):
            raise ValueError(f"Sink block parameters section is invalid for: {sink_block}")

        # The raw YAML must contain a list of block entries.
        raw_blocks = self.flowgraph.raw_data.get("blocks")
        if not isinstance(raw_blocks, list):
            raise ValueError("Flowgraph raw_data blocks section is invalid.")

        # Block names must stay unique in both representations.
        self._assert_new_block_name_available(instance_name, raw_blocks)

        # The destination sink must also exist exactly once in the raw YAML.
        raw_sink = self._require_unique_raw_block(raw_blocks, sink_block, role="Raw sink")
        raw_sink_parameters = raw_sink.get("parameters")
        if not isinstance(raw_sink_parameters, dict):
            raise ValueError(f"Raw sink block parameters section is invalid for: {sink_block}")

        # The connections section must be either absent or a list.
        raw_connections = self.flowgraph.raw_data.get("connections")
        if raw_connections is not None and not isinstance(raw_connections, list):
            raise ValueError("Flowgraph raw_data connections section is invalid.")

        # Expand the existing sink by one input and use that new port for the added tap.
        try:
            current_sink_input_count = int(raw_sink_parameters["nconnections"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(
                f"Sink block nconnections parameter is invalid for: {sink_block}"
            ) from error

        new_sink_input_count = str(current_sink_input_count + 1)
        new_sink_port = current_sink_input_count

        # Build the raw block payload using caller-provided parameters and narrow defaults.
        raw_block, raw_parameters, raw_states = self._prepare_new_block_payload(
            instance_name=instance_name,
            block_type="blocks_char_to_float",
            parameters=parameters,
            states=states,
            existing_block_count=len(raw_blocks),
        )
        raw_source_connection = self._raw_connection_entry(src_block, src_port, instance_name, 0)
        raw_sink_connection = self._raw_connection_entry(
            instance_name,
            0,
            sink_block,
            new_sink_port,
        )

        # Validate a copied graph first so failures never partially mutate the session.
        candidate_raw_data = copy.deepcopy(self.flowgraph.raw_data)
        candidate_raw_blocks = candidate_raw_data.get("blocks")
        if not isinstance(candidate_raw_blocks, list):
            raise ValueError("Flowgraph candidate blocks section is invalid.")
        candidate_raw_blocks.append(copy.deepcopy(raw_block))

        candidate_raw_sink = self._require_unique_raw_block(
            candidate_raw_blocks,
            sink_block,
            role="Candidate sink",
        )

        candidate_raw_sink_parameters = candidate_raw_sink.get("parameters")
        if not isinstance(candidate_raw_sink_parameters, dict):
            raise ValueError(f"Candidate sink block parameters section is invalid for: {sink_block}")
        candidate_raw_sink_parameters["nconnections"] = new_sink_input_count

        self._append_raw_connections(
            candidate_raw_data,
            [raw_source_connection, raw_sink_connection],
            error_context="Flowgraph candidate",
        )

        self._validate_candidate_raw_data_or_raise(
            candidate_raw_data,
            error_prefix="Added char_to_float block failed validation",
        )

        # Update the parsed model and raw YAML only after the candidate is accepted.
        self.flowgraph.blocks.append(
            Block(
                instance_name=instance_name,
                block_type="blocks_char_to_float",
                params={
                    "parameters": copy.deepcopy(raw_parameters),
                    "states": copy.deepcopy(raw_states),
                },
            )
        )
        self.flowgraph.connections.append(
            Connection(
                src_block=src_block,
                src_port=src_port,
                dst_block=instance_name,
                dst_port=0,
            )
        )
        self.flowgraph.connections.append(
            Connection(
                src_block=instance_name,
                src_port=0,
                dst_block=sink_block,
                dst_port=new_sink_port,
            )
        )
        raw_blocks.append(raw_block)
        parsed_sink_parameters["nconnections"] = new_sink_input_count
        raw_sink_parameters["nconnections"] = new_sink_input_count
        self._append_raw_connections(
            self.flowgraph.raw_data,
            [raw_source_connection, raw_sink_connection],
            error_context="Flowgraph raw_data",
        )

        # Any successful mutation means the in-memory session now differs from disk.
        self.is_dirty = True

    def add_and_connect_analog_random_source_to_qtgui_time_sink(
        self,
        source_instance_name: str,
        source_parameters: dict[str, Any],
        transform_instance_name: str,
        transform_parameters: dict[str, Any],
        sink_block: str,
        source_states: dict[str, Any] | None = None,
        transform_states: dict[str, Any] | None = None,
    ) -> None:
        """Add one analog_random_source_x -> blocks_char_to_float pipeline into a qtgui sink."""
        # Refuse to mutate anything if no graph has been loaded yet.
        if self.flowgraph is None:
            raise ValueError("No flowgraph loaded.")

        # Keep the first source workflow exact and bespoke.
        if not isinstance(source_instance_name, str) or not source_instance_name:
            raise ValueError("Source instance_name must be a non-empty string.")
        if not isinstance(source_parameters, dict):
            raise ValueError("Source parameters must be a mapping.")
        if not isinstance(transform_instance_name, str) or not transform_instance_name:
            raise ValueError("Transform instance_name must be a non-empty string.")
        if not isinstance(transform_parameters, dict):
            raise ValueError("Transform parameters must be a mapping.")
        if not isinstance(sink_block, str) or not sink_block:
            raise ValueError("Sink block name must be a non-empty string.")
        if source_states is not None and not isinstance(source_states, dict):
            raise ValueError("Source states must be a mapping when provided.")
        if transform_states is not None and not isinstance(transform_states, dict):
            raise ValueError("Transform states must be a mapping when provided.")
        if source_instance_name == transform_instance_name:
            raise ValueError("Source and transform instance names must be distinct.")

        # Keep the existing sink lookup exact and sink-specific.
        parsed_sink = self._require_unique_parsed_block(
            sink_block,
            role="Sink",
            expected_block_type="qtgui_time_sink_x",
        )
        parsed_sink_parameters = parsed_sink.params.get("parameters")
        if not isinstance(parsed_sink_parameters, dict):
            raise ValueError(f"Sink block parameters section is invalid for: {sink_block}")

        # The raw YAML must contain a list of block entries.
        raw_blocks = self.flowgraph.raw_data.get("blocks")
        if not isinstance(raw_blocks, list):
            raise ValueError("Flowgraph raw_data blocks section is invalid.")

        # Both new block names must stay unique in both representations.
        self._assert_new_block_name_available(source_instance_name, raw_blocks)
        self._assert_new_block_name_available(transform_instance_name, raw_blocks)

        # The destination sink must also exist exactly once in the raw YAML.
        raw_sink = self._require_unique_raw_block(raw_blocks, sink_block, role="Raw sink")
        raw_sink_parameters = raw_sink.get("parameters")
        if not isinstance(raw_sink_parameters, dict):
            raise ValueError(f"Raw sink block parameters section is invalid for: {sink_block}")

        # The connections section must be either absent or a list.
        raw_connections = self.flowgraph.raw_data.get("connections")
        if raw_connections is not None and not isinstance(raw_connections, list):
            raise ValueError("Flowgraph raw_data connections section is invalid.")

        # Expand the existing sink by one input and use that new port for the added pipeline.
        try:
            current_sink_input_count = int(raw_sink_parameters["nconnections"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(
                f"Sink block nconnections parameter is invalid for: {sink_block}"
            ) from error

        new_sink_input_count = str(current_sink_input_count + 1)
        new_sink_port = current_sink_input_count

        # Build the raw block payloads using caller-provided parameters and narrow defaults.
        raw_source_block, raw_source_parameters, raw_source_states = self._prepare_new_block_payload(
            instance_name=source_instance_name,
            block_type="analog_random_source_x",
            parameters=source_parameters,
            states=source_states,
            existing_block_count=len(raw_blocks),
        )
        raw_transform_block, raw_transform_parameters, raw_transform_states = self._prepare_new_block_payload(
            instance_name=transform_instance_name,
            block_type="blocks_char_to_float",
            parameters=transform_parameters,
            states=transform_states,
            existing_block_count=len(raw_blocks) + 1,
        )
        raw_source_connection = self._raw_connection_entry(
            source_instance_name,
            0,
            transform_instance_name,
            0,
        )
        raw_sink_connection = self._raw_connection_entry(
            transform_instance_name,
            0,
            sink_block,
            new_sink_port,
        )

        # Validate a copied graph first so failures never partially mutate the session.
        candidate_raw_data = copy.deepcopy(self.flowgraph.raw_data)
        candidate_raw_blocks = candidate_raw_data.get("blocks")
        if not isinstance(candidate_raw_blocks, list):
            raise ValueError("Flowgraph candidate blocks section is invalid.")
        candidate_raw_blocks.append(copy.deepcopy(raw_source_block))
        candidate_raw_blocks.append(copy.deepcopy(raw_transform_block))

        candidate_raw_sink = self._require_unique_raw_block(
            candidate_raw_blocks,
            sink_block,
            role="Candidate sink",
        )
        candidate_raw_sink_parameters = candidate_raw_sink.get("parameters")
        if not isinstance(candidate_raw_sink_parameters, dict):
            raise ValueError(f"Candidate sink block parameters section is invalid for: {sink_block}")
        candidate_raw_sink_parameters["nconnections"] = new_sink_input_count
        self._append_raw_connections(
            candidate_raw_data,
            [raw_source_connection, raw_sink_connection],
            error_context="Flowgraph candidate",
        )

        self._validate_candidate_raw_data_or_raise(
            candidate_raw_data,
            error_prefix="Added source pipeline failed validation",
        )

        # Update the parsed model and raw YAML only after the candidate is accepted.
        self.flowgraph.blocks.append(
            Block(
                instance_name=source_instance_name,
                block_type="analog_random_source_x",
                params={
                    "parameters": copy.deepcopy(raw_source_parameters),
                    "states": copy.deepcopy(raw_source_states),
                },
            )
        )
        self.flowgraph.blocks.append(
            Block(
                instance_name=transform_instance_name,
                block_type="blocks_char_to_float",
                params={
                    "parameters": copy.deepcopy(raw_transform_parameters),
                    "states": copy.deepcopy(raw_transform_states),
                },
            )
        )
        self.flowgraph.connections.append(
            Connection(
                src_block=source_instance_name,
                src_port=0,
                dst_block=transform_instance_name,
                dst_port=0,
            )
        )
        self.flowgraph.connections.append(
            Connection(
                src_block=transform_instance_name,
                src_port=0,
                dst_block=sink_block,
                dst_port=new_sink_port,
            )
        )
        raw_blocks.append(raw_source_block)
        raw_blocks.append(raw_transform_block)
        parsed_sink_parameters["nconnections"] = new_sink_input_count
        raw_sink_parameters["nconnections"] = new_sink_input_count
        self._append_raw_connections(
            self.flowgraph.raw_data,
            [raw_source_connection, raw_sink_connection],
            error_context="Flowgraph raw_data",
        )

        # Any successful mutation means the in-memory session now differs from disk.
        self.is_dirty = True

    def remove_block(self, instance_name: str) -> None:
        """Remove one detached, unreferenced block from both the model and raw YAML."""
        # Refuse to mutate anything if no graph has been loaded yet.
        if self.flowgraph is None:
            raise ValueError("No flowgraph loaded.")

        # The raw YAML must contain a list of block entries.
        raw_blocks = self.flowgraph.raw_data.get("blocks")
        if not isinstance(raw_blocks, list):
            raise ValueError("Flowgraph raw_data blocks section is invalid.")

        # Find the block exactly once in both representations before mutating either side.
        parsed_indexes = [
            index
            for index, block in enumerate(self.flowgraph.blocks)
            if block.instance_name == instance_name
        ]
        raw_indexes = [
            index
            for index, entry in enumerate(raw_blocks)
            if isinstance(entry, dict) and entry.get("name") == instance_name
        ]

        if not parsed_indexes or not raw_indexes:
            raise ValueError(f"Block not found: {instance_name}")
        if len(parsed_indexes) != 1 or len(raw_indexes) != 1:
            raise ValueError(f"Block name is not unique: {instance_name}")

        # The first implementation is conservative: connected blocks must be detached first.
        if any(
            connection.src_block == instance_name or connection.dst_block == instance_name
            for connection in self.flowgraph.connections
        ):
            raise ValueError(
                f"Cannot remove connected block: {instance_name}. Disconnect all attached wires first."
            )

        # Reject removals that would leave parameter references unresolved elsewhere.
        raw_index = raw_indexes[0]
        if self._block_name_is_referenced_elsewhere(
            raw_data=self.flowgraph.raw_data,
            instance_name=instance_name,
            ignored_raw_block_index=raw_index,
        ):
            raise ValueError(f"Block is still referenced elsewhere: {instance_name}")

        # Remove the parsed block and the raw YAML entry.
        del self.flowgraph.blocks[parsed_indexes[0]]
        del raw_blocks[raw_index]

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

        # Reject malformed top-level structure instead of silently dropping data.
        if not isinstance(blocks_data, list):
            raise ValueError("Flowgraph blocks section must be a list.")

        # Inspect each block entry in order.
        for index, entry in enumerate(blocks_data):
            # Reject malformed entries so callers never load partial graphs silently.
            if not isinstance(entry, dict):
                raise ValueError(
                    f"Malformed block entry at index {index}: expected a mapping."
                )

            # The block name becomes the user-facing instance name.
            instance_name = entry.get("name")
            # The block id becomes the internal GNU Radio block type.
            block_type = entry.get("id")

            # Both fields must exist and both must be strings.
            if not isinstance(instance_name, str) or not isinstance(block_type, str):
                raise ValueError(
                    f"Malformed block entry at index {index}: missing name or id."
                )

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

        # Reject malformed top-level structure instead of silently dropping data.
        if not isinstance(connections_data, list):
            raise ValueError("Flowgraph connections section must be a list.")

        # Inspect each connection entry in order.
        for index, entry in enumerate(connections_data):
            # Each connection must be a 4-item list.
            if not isinstance(entry, list) or len(entry) != 4:
                raise ValueError(
                    f"Malformed connection entry at index {index}: expected four items."
                )

            # The first and third items are block instance names.
            src_block, src_port, dst_block, dst_port = entry

            # Connection endpoints must refer to block names.
            if not isinstance(src_block, str) or not isinstance(dst_block, str):
                raise ValueError(
                    f"Malformed connection entry at index {index}: block names must be strings."
                )

            # Ports are stored as strings in the file, so convert them to integers.
            try:
                src_port_number = int(src_port)
                dst_port_number = int(dst_port)
            except (TypeError, ValueError):
                raise ValueError(
                    f"Malformed connection entry at index {index}: ports must be integers."
                )

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
    def _run_grcc_validation(raw_data: Any) -> tuple[bool, str, str, int]:
        # Serialize the candidate YAML once so validation stays consistent everywhere.
        serialized = FlowgraphSession._serialize_raw_data(raw_data)

        # Use a temporary directory so validation does not touch project files.
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a throwaway .grc file for grcc to compile.
            temp_path = Path(tmpdir) / "validate.grc"
            temp_path.write_text(serialized, encoding="utf-8")

            # Run grcc and return the normalized validity plus the raw diagnostics.
            result = subprocess.run(
                ["grcc", str(temp_path)],
                capture_output=True,
                text=True,
                cwd=tmpdir,
            )
            return (
                FlowgraphSession._grcc_result_is_valid(
                    returncode=result.returncode,
                    stdout=result.stdout,
                    stderr=result.stderr,
                ),
                result.stdout,
                result.stderr,
                result.returncode,
            )

    def _validate_candidate_raw_data_or_raise(self, raw_data: Any, error_prefix: str) -> None:
        # Candidate validation must be consistent across structural-edit workflows.
        is_valid, stdout, stderr, _returncode = self._run_grcc_validation(raw_data)
        if not is_valid:
            raise ValueError(f"{error_prefix}: {self._grcc_failure_message(stdout, stderr)}")

    @staticmethod
    def _raw_connection_entry(
        src_block: str,
        src_port: int,
        dst_block: str,
        dst_port: int,
    ) -> list[str]:
        # Preserve the same on-disk connection shape as the original .grc files.
        return [src_block, str(src_port), dst_block, str(dst_port)]

    @staticmethod
    def _default_block_states(existing_block_count: int) -> dict[str, Any]:
        # Keep the first generated state payload minimal because GNU Radio accepts it.
        return {
            "coordinate": [8, 8 + (existing_block_count * 24)],
            "rotation": 0,
            "state": "enabled",
        }

    def _assert_new_block_name_available(self, instance_name: str, raw_blocks: list[Any]) -> None:
        # New structural blocks must stay unique in both parsed and raw representations.
        if self.flowgraph is None:
            raise ValueError("No flowgraph loaded.")

        if any(block.instance_name == instance_name for block in self.flowgraph.blocks):
            raise ValueError(f"Block already exists: {instance_name}")
        if any(
            isinstance(entry, dict) and entry.get("name") == instance_name
            for entry in raw_blocks
        ):
            raise ValueError(f"Raw block already exists: {instance_name}")

    def _require_unique_parsed_block(
        self,
        instance_name: str,
        role: str,
        expected_block_type: str | None = None,
    ) -> Block:
        # Structural edits should never act on an ambiguous parsed block lookup.
        if self.flowgraph is None:
            raise ValueError("No flowgraph loaded.")

        matches = [
            block for block in self.flowgraph.blocks if block.instance_name == instance_name
        ]
        if not matches:
            raise ValueError(f"{role} block not found: {instance_name}")
        if len(matches) != 1:
            raise ValueError(f"{role} block name is not unique: {instance_name}")

        block = matches[0]
        if expected_block_type is not None and block.block_type != expected_block_type:
            raise ValueError(f"Unsupported {role.lower()} block type for coordinated add: {instance_name}")
        return block

    @staticmethod
    def _require_unique_raw_block(
        raw_blocks: list[Any],
        instance_name: str,
        role: str,
    ) -> dict[str, Any]:
        # Structural edits should never act on an ambiguous raw block lookup.
        matches = [
            entry
            for entry in raw_blocks
            if isinstance(entry, dict) and entry.get("name") == instance_name
        ]
        if not matches:
            raise ValueError(f"{role} block not found: {instance_name}")
        if len(matches) != 1:
            raise ValueError(f"{role} block name is not unique: {instance_name}")
        return matches[0]

    @staticmethod
    def _prepare_new_block_payload(
        instance_name: str,
        block_type: str,
        parameters: dict[str, Any],
        states: dict[str, Any] | None,
        existing_block_count: int,
        add_default_comment: bool = False,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        # Keep block payload generation consistent across structural add workflows.
        raw_parameters = copy.deepcopy(parameters)
        if add_default_comment:
            raw_parameters.setdefault("comment", "")

        raw_states = (
            copy.deepcopy(states)
            if states is not None
            else FlowgraphSession._default_block_states(existing_block_count=existing_block_count)
        )
        raw_block = {
            "name": instance_name,
            "id": block_type,
            "parameters": raw_parameters,
            "states": raw_states,
        }
        return raw_block, raw_parameters, raw_states

    @staticmethod
    def _append_raw_connections(
        raw_data: Any,
        connections: list[list[str]],
        error_context: str,
    ) -> None:
        # Keep raw connection insertion consistent across candidate and committed graphs.
        if not isinstance(raw_data, dict):
            raise ValueError(f"{error_context} is missing or invalid.")

        raw_connections = raw_data.get("connections")
        copied_connections = [copy.deepcopy(entry) for entry in connections]
        if raw_connections is None:
            raw_data["connections"] = copied_connections
        elif isinstance(raw_connections, list):
            raw_connections.extend(copied_connections)
        else:
            raise ValueError(f"{error_context} connections section is invalid.")

    @staticmethod
    def _grcc_result_is_valid(returncode: int, stdout: str, stderr: str) -> bool:
        # grcc sometimes prints flowgraph errors but still exits with status 0.
        if returncode != 0:
            return False

        # Treat the known GNU Radio error markers as validation failures too.
        return not any(
            marker in stdout
            for marker in (">>> Error:", ">>> Load Error:")
        ) and not any(
            marker in stderr
            for marker in (
                "Traceback (most recent call last):",
                "Compilation error",
            )
        )

    @staticmethod
    def _grcc_failure_message(stdout: str, stderr: str) -> str:
        # Prefer GNU Radio's explicit error lines when turning validation failures into exceptions.
        for stream in (stdout, stderr):
            for line in stream.splitlines():
                stripped = line.strip()
                if stripped.startswith(">>> Error:") or stripped.startswith(">>> Load Error:"):
                    return stripped

        # Fall back to the first non-empty line if the usual markers are absent.
        for stream in (stdout, stderr):
            for line in stream.splitlines():
                stripped = line.strip()
                if stripped:
                    return stripped

        return "GNU Radio rejected the candidate flowgraph."

    @staticmethod
    def _block_name_is_referenced_elsewhere(
        raw_data: Any,
        instance_name: str,
        ignored_raw_block_index: int,
    ) -> bool:
        # Only inspect the sections that commonly hold block-name expressions.
        if not isinstance(raw_data, dict):
            return False

        options = raw_data.get("options")
        if FlowgraphSession._value_references_identifier(options, instance_name):
            return True

        raw_blocks = raw_data.get("blocks")
        if not isinstance(raw_blocks, list):
            return False

        return any(
            FlowgraphSession._value_references_identifier(entry, instance_name)
            for index, entry in enumerate(raw_blocks)
            if index != ignored_raw_block_index
        )

    @staticmethod
    def _value_references_identifier(value: Any, identifier: str) -> bool:
        # GNU Radio expressions are plain strings, so use an identifier-boundary match.
        pattern = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(identifier)}(?![A-Za-z0-9_])")

        if isinstance(value, str):
            return pattern.search(value) is not None
        if isinstance(value, dict):
            return any(
                FlowgraphSession._value_references_identifier(nested, identifier)
                for nested in value.values()
            )
        if isinstance(value, list):
            return any(
                FlowgraphSession._value_references_identifier(nested, identifier)
                for nested in value
            )
        return False

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