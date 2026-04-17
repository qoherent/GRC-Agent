"""Session management for loading, validating, and editing `.grc` flowgraphs.

The session keeps a typed view of the flowgraph and the original YAML payload in
sync so structural edits can be validated with `grcc` and saved without dropping
unsupported fields.
"""

from collections import defaultdict, deque
import copy
import hashlib
from pathlib import Path
import subprocess
import tempfile
from typing import Any

import yaml

from .models import Block, Connection, Flowgraph
from .session_ops import (
    block_name_is_referenced_elsewhere as shared_block_name_is_referenced_elsewhere,
    connection_entry_to_tuple as shared_connection_entry_to_tuple,
    default_block_states as shared_default_block_states,
    parse_blocks as shared_parse_blocks,
    parse_connections as shared_parse_connections,
    raw_connection_entry as shared_raw_connection_entry,
)

DEFAULT_SUMMARY_BLOCK_LIMIT = 8
DEFAULT_CONTEXT_MAX_NODES = 20
MAX_CONTEXT_HOPS = 4
MAX_CONTEXT_MAX_NODES = 50
MAX_CONTEXT_PARAMETER_SAMPLE = 6


class FlowgraphSession:
    """Own one `.grc` flowgraph and keep parsed and raw state synchronized."""

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
        self.last_validation_ok: bool | None = None
        # Revision and caches support bounded inspection and retrieval reuse.
        self._state_revision = 0
        self._graph_id_revision: int | None = None
        self._graph_id_cache: str | None = None
        self._inspection_cache_revision: int | None = None
        self._block_matches_cache: dict[str, tuple[Block, ...]] = {}
        self._neighbor_names_cache: dict[str, tuple[str, ...]] = {}
        self._incoming_connection_cache: dict[str, tuple[Connection, ...]] = {}
        self._outgoing_connection_cache: dict[str, tuple[Connection, ...]] = {}

    # Session lifecycle

    def load(self, path: str | Path) -> None:
        """Load a `.grc` file into the session."""
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
        # A newly loaded graph should not inherit stale validation diagnostics.
        self.last_validation_stdout = None
        self.last_validation_stderr = None
        self.last_validation_returncode = None
        self.last_validation_ok = None
        self._bump_state_revision()

    def save(self, path: str | Path | None = None) -> None:
        """Write the current in-memory graph to disk."""
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

        path_changed = target_path != self.path

        # Saving makes the new path the active session path and clears the dirty flag.
        self.path = target_path
        self.is_dirty = False
        if path_changed:
            self._bump_state_revision()

    def validate(self) -> bool:
        """Run `grcc` against the current in-memory graph."""
        # Refuse to validate if no flowgraph has been loaded yet.
        if self.flowgraph is None:
            raise ValueError("No flowgraph loaded.")

        # Clear previous diagnostics before running so stale results are never visible.
        self.last_validation_stdout = None
        self.last_validation_stderr = None
        self.last_validation_returncode = None

        # Run validation against the current raw YAML and persist the diagnostics.
        is_valid, stdout, stderr, returncode = self._run_grcc_validation(
            self.flowgraph.raw_data
        )
        self.last_validation_stdout = stdout
        self.last_validation_stderr = stderr
        self.last_validation_returncode = returncode
        self.last_validation_ok = is_valid
        return is_valid

    @property
    def state_revision(self) -> int:
        """Return the current session revision for cache invalidation."""
        return self._state_revision

    def graph_id(self) -> str:
        """Return a stable content-derived identifier for the current graph."""
        flowgraph = self._require_loaded_flowgraph()
        if (
            self._graph_id_revision == self._state_revision
            and self._graph_id_cache is not None
        ):
            return self._graph_id_cache

        serialized = self._serialize_raw_data(flowgraph.raw_data)
        digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]
        self._graph_id_cache = f"grc:{digest}"
        self._graph_id_revision = self._state_revision
        return self._graph_id_cache

    def validation_state(self) -> dict[str, Any]:
        """Return the compact structured validation state for the active graph."""
        status = "unknown"
        if self.last_validation_ok is True:
            status = "valid"
        elif self.last_validation_ok is False:
            status = "invalid"

        payload: dict[str, Any] = {
            "status": status,
            "returncode": self.last_validation_returncode,
        }
        if self.last_validation_stdout is not None:
            payload["stdout"] = self.last_validation_stdout
        if self.last_validation_stderr is not None:
            payload["stderr"] = self.last_validation_stderr
        return payload

    def session_provenance(self) -> dict[str, Any]:
        """Return the stable provenance payload for the current loaded graph."""
        flowgraph = self._require_loaded_flowgraph()
        metadata = flowgraph.metadata.get("metadata")
        top_level_metadata = metadata if isinstance(metadata, dict) else {}
        return {
            "path": str(self.path) if self.path is not None else None,
            "graph_id": self.graph_id(),
            "file_format": top_level_metadata.get("file_format"),
            "grc_version": top_level_metadata.get("grc_version"),
        }

    def summary_payload(
        self, *, max_blocks: int = DEFAULT_SUMMARY_BLOCK_LIMIT
    ) -> dict[str, Any]:
        """Return the structured bounded summary payload for the loaded graph."""
        flowgraph = self._require_loaded_flowgraph()
        if not isinstance(max_blocks, int) or max_blocks < 1:
            raise ValueError("max_blocks must be a positive integer.")

        variable_count = sum(
            1 for block in flowgraph.blocks if block.block_type == "variable"
        )
        preview_blocks = flowgraph.blocks[:max_blocks]
        preview = ", ".join(
            f"{block.instance_name} ({block.block_type})" for block in preview_blocks
        )
        remaining = len(flowgraph.blocks) - len(preview_blocks)
        preview_suffix = f", ... +{remaining} more" if remaining > 0 else ""
        file_name = self.path.name if self.path is not None else "<in-memory-flowgraph>"
        summary = (
            f"{file_name}: {len(flowgraph.blocks)} blocks, {len(flowgraph.connections)} connections, "
            f"{variable_count} variable blocks. Preview: {preview}{preview_suffix}."
            if preview
            else f"{file_name}: empty graph."
        )
        return {
            "ok": True,
            "summary": summary,
            "path": str(self.path) if self.path is not None else None,
            "graph_id": self.graph_id(),
            "block_count": len(flowgraph.blocks),
            "connection_count": len(flowgraph.connections),
            "variable_count": variable_count,
            "dirty": self.is_dirty,
            "validation": self.validation_state(),
        }

    def context_payload(
        self,
        node_id: str,
        *,
        hops: int = 1,
        max_nodes: int = DEFAULT_CONTEXT_MAX_NODES,
    ) -> dict[str, Any]:
        """Return a bounded mini-graph around one loaded block instance."""
        flowgraph = self._require_loaded_flowgraph()
        if not isinstance(node_id, str) or not node_id.strip():
            raise ValueError("node_id must be a non-empty string.")
        if not isinstance(hops, int) or hops < 0 or hops > MAX_CONTEXT_HOPS:
            raise ValueError(
                f"hops must be an integer between 0 and {MAX_CONTEXT_HOPS}."
            )
        if (
            not isinstance(max_nodes, int)
            or max_nodes < 1
            or max_nodes > MAX_CONTEXT_MAX_NODES
        ):
            raise ValueError(
                f"max_nodes must be an integer between 1 and {MAX_CONTEXT_MAX_NODES}."
            )

        normalized_node_id = node_id.strip()
        self._ensure_inspection_cache()
        matches = self._block_matches_cache.get(normalized_node_id, ())
        if not matches:
            raise KeyError(normalized_node_id)
        if len(matches) != 1:
            raise ValueError(f"Block name is not unique: {normalized_node_id}")

        distances: dict[str, int] = {normalized_node_id: 0}
        queue: deque[str] = deque([normalized_node_id])
        ordered_names: list[str] = []
        truncated = False

        while queue and len(ordered_names) < max_nodes:
            current_name = queue.popleft()
            ordered_names.append(current_name)
            current_distance = distances[current_name]
            if current_distance >= hops:
                continue
            for neighbor_name in self._neighbor_names_cache.get(current_name, ()):
                if neighbor_name in distances:
                    continue
                distances[neighbor_name] = current_distance + 1
                if len(ordered_names) + len(queue) >= max_nodes:
                    truncated = True
                    continue
                queue.append(neighbor_name)

        if queue:
            truncated = True
        if any(
            neighbor not in distances
            for current_name, current_distance in list(distances.items())
            if current_distance < hops
            for neighbor in self._neighbor_names_cache.get(current_name, ())
        ):
            truncated = True

        included_names = sorted(ordered_names, key=lambda name: (distances[name], name))
        included_name_set = set(included_names)

        nodes = [
            self._context_node_payload(
                self._block_matches_cache[name][0],
                distance=distances[name],
            )
            for name in included_names
        ]
        edges = [
            {
                "source": connection.src_block,
                "source_port": connection.src_port,
                "target": connection.dst_block,
                "target_port": connection.dst_port,
            }
            for connection in sorted(
                flowgraph.connections,
                key=lambda edge: (
                    edge.src_block,
                    edge.src_port,
                    edge.dst_block,
                    edge.dst_port,
                ),
            )
            if connection.src_block in included_name_set
            and connection.dst_block in included_name_set
        ]

        target = next(node for node in nodes if node["node_id"] == normalized_node_id)
        return {
            "ok": True,
            "node_id": normalized_node_id,
            "hops": hops,
            "max_nodes": max_nodes,
            "target": target,
            "nodes": nodes,
            "edges": edges,
            "provenance": self.session_provenance(),
            "dirty": self.is_dirty,
            "validation": self.validation_state(),
            "truncated": truncated,
        }

    # Connection edits

    def disconnect(
        self, src_block: str, src_port: int, dst_block: str, dst_port: int
    ) -> None:
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
                )
                == target
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
        self._bump_state_revision()

    def connect(
        self, src_block: str, src_port: int, dst_block: str, dst_port: int
    ) -> None:
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
            )
            == target
            for connection in self.flowgraph.connections
        ):
            raise ValueError(f"Connection already exists: {target}")

        # The raw YAML must contain a list of connection entries when present.
        raw_connections = self.flowgraph.raw_data.get("connections")
        if raw_connections is not None and not isinstance(raw_connections, list):
            raise ValueError("Flowgraph raw_data connections section is invalid.")

        # Reject duplicates in the raw YAML before touching any state.
        if isinstance(raw_connections, list) and any(
            self._connection_entry_to_tuple(entry) == target
            for entry in raw_connections
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
        raw_target = self._raw_connection_entry(
            src_block, src_port, dst_block, dst_port
        )
        if raw_connections is None:
            self.flowgraph.raw_data["connections"] = [raw_target]
        else:
            raw_connections.append(raw_target)

        # Any successful mutation means the in-memory session now differs from disk.
        self.is_dirty = True
        self._bump_state_revision()

    # Structural block edits

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
        self._bump_state_revision()

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
        raw_connection = self._raw_connection_entry(
            src_block, src_port, instance_name, 0
        )

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
        self._bump_state_revision()

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
            raise ValueError(
                f"Sink block parameters section is invalid for: {sink_block}"
            )

        # The raw YAML must contain a list of block entries.
        raw_blocks = self.flowgraph.raw_data.get("blocks")
        if not isinstance(raw_blocks, list):
            raise ValueError("Flowgraph raw_data blocks section is invalid.")

        # Block names must stay unique in both representations.
        self._assert_new_block_name_available(instance_name, raw_blocks)

        # The destination sink must also exist exactly once in the raw YAML.
        raw_sink = self._require_unique_raw_block(
            raw_blocks, sink_block, role="Raw sink"
        )
        raw_sink_parameters = raw_sink.get("parameters")
        if not isinstance(raw_sink_parameters, dict):
            raise ValueError(
                f"Raw sink block parameters section is invalid for: {sink_block}"
            )

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
        raw_source_connection = self._raw_connection_entry(
            src_block, src_port, instance_name, 0
        )
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
            raise ValueError(
                f"Candidate sink block parameters section is invalid for: {sink_block}"
            )
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
        self._bump_state_revision()

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
            raise ValueError(
                f"Sink block parameters section is invalid for: {sink_block}"
            )

        # The raw YAML must contain a list of block entries.
        raw_blocks = self.flowgraph.raw_data.get("blocks")
        if not isinstance(raw_blocks, list):
            raise ValueError("Flowgraph raw_data blocks section is invalid.")

        # Both new block names must stay unique in both representations.
        self._assert_new_block_name_available(source_instance_name, raw_blocks)
        self._assert_new_block_name_available(transform_instance_name, raw_blocks)

        # The destination sink must also exist exactly once in the raw YAML.
        raw_sink = self._require_unique_raw_block(
            raw_blocks, sink_block, role="Raw sink"
        )
        raw_sink_parameters = raw_sink.get("parameters")
        if not isinstance(raw_sink_parameters, dict):
            raise ValueError(
                f"Raw sink block parameters section is invalid for: {sink_block}"
            )

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
        raw_source_block, raw_source_parameters, raw_source_states = (
            self._prepare_new_block_payload(
                instance_name=source_instance_name,
                block_type="analog_random_source_x",
                parameters=source_parameters,
                states=source_states,
                existing_block_count=len(raw_blocks),
            )
        )
        raw_transform_block, raw_transform_parameters, raw_transform_states = (
            self._prepare_new_block_payload(
                instance_name=transform_instance_name,
                block_type="blocks_char_to_float",
                parameters=transform_parameters,
                states=transform_states,
                existing_block_count=len(raw_blocks) + 1,
            )
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
            raise ValueError(
                f"Candidate sink block parameters section is invalid for: {sink_block}"
            )
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
        self._bump_state_revision()

    # Block removal and parameter edits

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
            connection.src_block == instance_name
            or connection.dst_block == instance_name
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
        self._bump_state_revision()

    def set_param(self, instance_name: str, parameter_key: str, value: object) -> None:
        """Update one block parameter in both the model and raw YAML."""
        # Refuse to mutate anything if no graph has been loaded yet.
        if self.flowgraph is None:
            raise ValueError("No flowgraph loaded.")

        # Find the parsed block object by its instance name.
        block = next(
            (
                block
                for block in self.flowgraph.blocks
                if block.instance_name == instance_name
            ),
            None,
        )
        if block is None:
            raise ValueError(f"Block not found: {instance_name}")

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

        # Confirm both parameter mappings are valid before mutating either representation.
        parameters = block.params.setdefault("parameters", {})
        if not isinstance(parameters, dict):
            raise ValueError(
                f"Block parameters section is invalid for: {instance_name}"
            )

        # Update the raw parameters mapping in the same way as the parsed model.
        raw_parameters = raw_block.setdefault("parameters", {})
        if not isinstance(raw_parameters, dict):
            raise ValueError(
                f"Raw block parameters section is invalid for: {instance_name}"
            )
        parameters[parameter_key] = value
        raw_parameters[parameter_key] = value

        # Any successful mutation means the in-memory session now differs from disk.
        self.is_dirty = True
        self._bump_state_revision()

    def summarize(self) -> str:
        """Return a compact human-readable summary of the loaded graph."""
        # If nothing has been loaded yet, say so plainly.
        if self.flowgraph is None:
            return "No flowgraph loaded."

        return self.summary_payload()["summary"]

    def _bump_state_revision(self) -> None:
        """Advance the session revision after load, save-path changes, or mutation."""
        self._state_revision += 1

    def _require_loaded_flowgraph(self) -> Flowgraph:
        """Return the active flowgraph or raise when the session is empty."""
        if self.flowgraph is None:
            raise ValueError("No flowgraph loaded.")
        return self.flowgraph

    def _ensure_inspection_cache(self) -> None:
        """Build cached lookup and adjacency indexes for bounded inspection."""
        flowgraph = self._require_loaded_flowgraph()
        if self._inspection_cache_revision == self._state_revision:
            return

        block_matches: dict[str, list[Block]] = defaultdict(list)
        neighbor_names: dict[str, set[str]] = defaultdict(set)
        incoming_connections: dict[str, list[Connection]] = defaultdict(list)
        outgoing_connections: dict[str, list[Connection]] = defaultdict(list)

        for block in flowgraph.blocks:
            block_matches[block.instance_name].append(block)
            neighbor_names.setdefault(block.instance_name, set())

        for connection in flowgraph.connections:
            neighbor_names[connection.src_block].add(connection.dst_block)
            neighbor_names[connection.dst_block].add(connection.src_block)
            incoming_connections[connection.dst_block].append(connection)
            outgoing_connections[connection.src_block].append(connection)

        def edge_sort_key(edge: Connection) -> tuple[str, int, str, int]:
            return (edge.src_block, edge.src_port, edge.dst_block, edge.dst_port)

        self._block_matches_cache = {
            name: tuple(matches) for name, matches in sorted(block_matches.items())
        }
        self._neighbor_names_cache = {
            name: tuple(sorted(names)) for name, names in sorted(neighbor_names.items())
        }
        self._incoming_connection_cache = {
            name: tuple(sorted(edges, key=edge_sort_key))
            for name, edges in sorted(incoming_connections.items())
        }
        self._outgoing_connection_cache = {
            name: tuple(sorted(edges, key=edge_sort_key))
            for name, edges in sorted(outgoing_connections.items())
        }
        self._inspection_cache_revision = self._state_revision

    def _context_node_payload(self, block: Block, *, distance: int) -> dict[str, Any]:
        """Render one block into the bounded session-context node payload."""
        parameters = block.params.get("parameters")
        parameter_map = parameters if isinstance(parameters, dict) else {}
        return {
            "node_id": block.instance_name,
            "label": block.instance_name,
            "block_type": block.block_type,
            "distance": distance,
            "parameter_count": len(parameter_map),
            "parameter_sample": self._parameter_sample(parameter_map),
            "incoming": [
                connection.src_block
                for connection in self._incoming_connection_cache.get(
                    block.instance_name, ()
                )
            ],
            "outgoing": [
                connection.dst_block
                for connection in self._outgoing_connection_cache.get(
                    block.instance_name, ()
                )
            ],
        }

    @staticmethod
    def _parameter_sample(parameter_map: dict[str, Any]) -> list[str]:
        """Return a bounded key=value preview for one block's parameters."""
        rendered = [
            f"{key}={FlowgraphSession._compact_value(value)}"
            for key, value in list(parameter_map.items())[:MAX_CONTEXT_PARAMETER_SAMPLE]
        ]
        remaining = len(parameter_map) - len(rendered)
        if remaining > 0:
            rendered.append(f"... +{remaining} more")
        return rendered

    @staticmethod
    def _compact_value(value: Any) -> str:
        """Collapse arbitrary values into a short single-line representation."""
        if value is None:
            return "null"
        if isinstance(value, str):
            return " ".join(value.split()) or '""'
        if isinstance(value, (list, tuple)):
            return (
                "["
                + ", ".join(FlowgraphSession._compact_value(item) for item in value[:4])
                + (", ..." if len(value) > 4 else "")
                + "]"
            )
        if isinstance(value, dict):
            items = list(value.items())[:4]
            body = ", ".join(
                f"{key}={FlowgraphSession._compact_value(item)}" for key, item in items
            )
            suffix = ", ..." if len(value) > 4 else ""
            return "{" + body + suffix + "}"
        return str(value)

    # Parsing and serialization helpers

    @staticmethod
    def _parse_blocks(blocks_data: Any) -> list[Block]:
        """Parse the raw `blocks` section into typed block objects."""
        return shared_parse_blocks(blocks_data)

    @staticmethod
    def _parse_connections(connections_data: Any) -> list[Connection]:
        """Parse the raw `connections` section into typed connection objects."""
        return shared_parse_connections(connections_data)

    @staticmethod
    def _serialize_raw_data(raw_data: Any) -> str:
        """Serialize raw flowgraph data using the project's YAML settings."""
        # Save and validate both use the same YAML serialization rules.
        if not isinstance(raw_data, dict):
            raise ValueError("Flowgraph raw_data is missing or invalid.")

        # Keep the original key order and make the output readable.
        return yaml.safe_dump(raw_data, sort_keys=False, allow_unicode=True)

    # Validation helpers

    @staticmethod
    def _run_grcc_validation(raw_data: Any) -> tuple[bool, str, str, int]:
        """Run `grcc` against raw flowgraph data inside a temporary workspace."""
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

    def _validate_candidate_raw_data_or_raise(
        self, raw_data: Any, error_prefix: str
    ) -> None:
        """Raise a `ValueError` when a candidate graph fails `grcc` validation."""
        # Candidate validation must be consistent across structural-edit workflows.
        is_valid, stdout, stderr, _returncode = self._run_grcc_validation(raw_data)
        if not is_valid:
            raise ValueError(
                f"{error_prefix}: {self._grcc_failure_message(stdout, stderr)}"
            )

    @staticmethod
    def _raw_connection_entry(
        src_block: str,
        src_port: int,
        dst_block: str,
        dst_port: int,
    ) -> list[str]:
        """Build the on-disk four-item connection entry used by `.grc` files."""
        return shared_raw_connection_entry(src_block, src_port, dst_block, dst_port)

    @staticmethod
    def _default_block_states(existing_block_count: int) -> dict[str, Any]:
        """Return the minimal default `states` payload for generated blocks."""
        return shared_default_block_states(existing_block_count)

    # Structural edit helpers

    def _assert_new_block_name_available(
        self, instance_name: str, raw_blocks: list[Any]
    ) -> None:
        """Reject new block names that already exist in parsed or raw state."""
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
        """Return one parsed block by name and optionally enforce its type."""
        # Structural edits should never act on an ambiguous parsed block lookup.
        if self.flowgraph is None:
            raise ValueError("No flowgraph loaded.")

        matches = [
            block
            for block in self.flowgraph.blocks
            if block.instance_name == instance_name
        ]
        if not matches:
            raise ValueError(f"{role} block not found: {instance_name}")
        if len(matches) != 1:
            raise ValueError(f"{role} block name is not unique: {instance_name}")

        block = matches[0]
        if expected_block_type is not None and block.block_type != expected_block_type:
            raise ValueError(
                f"Unsupported {role.lower()} block type for coordinated add: {instance_name}"
            )
        return block

    @staticmethod
    def _require_unique_raw_block(
        raw_blocks: list[Any],
        instance_name: str,
        role: str,
    ) -> dict[str, Any]:
        """Return one raw block entry by name or raise on ambiguity."""
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
        """Build a raw block payload plus the copied parameter and state mappings."""
        # Keep block payload generation consistent across structural add workflows.
        raw_parameters = copy.deepcopy(parameters)
        if add_default_comment:
            raw_parameters.setdefault("comment", "")

        raw_states = (
            copy.deepcopy(states)
            if states is not None
            else FlowgraphSession._default_block_states(
                existing_block_count=existing_block_count
            )
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
        """Append connection entries into a raw graph mapping in one place."""
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
        """Interpret `grcc` output using GNU Radio's actual failure markers."""
        # grcc sometimes prints flowgraph errors but still exits with status 0.
        if returncode != 0:
            return False

        # Treat the known GNU Radio error markers as validation failures too.
        return not any(
            marker in stdout for marker in (">>> Error:", ">>> Load Error:")
        ) and not any(
            marker in stderr
            for marker in (
                "Traceback (most recent call last):",
                "Compilation error",
            )
        )

    @staticmethod
    def _grcc_failure_message(stdout: str, stderr: str) -> str:
        """Extract the clearest available validation failure message."""
        # Prefer GNU Radio's explicit error lines when turning validation failures into exceptions.
        for stream in (stdout, stderr):
            for line in stream.splitlines():
                stripped = line.strip()
                if stripped.startswith(">>> Error:") or stripped.startswith(
                    ">>> Load Error:"
                ):
                    return stripped

        # Fall back to the first non-empty line if the usual markers are absent.
        for stream in (stdout, stderr):
            for line in stream.splitlines():
                stripped = line.strip()
                if stripped:
                    return stripped

        return "GNU Radio rejected the candidate flowgraph."

    # Raw-data inspection helpers

    @staticmethod
    def _block_name_is_referenced_elsewhere(
        raw_data: Any,
        instance_name: str,
        ignored_raw_block_index: int,
    ) -> bool:
        """Check whether a block name still appears in other raw expressions."""
        return shared_block_name_is_referenced_elsewhere(
            raw_data=raw_data,
            instance_name=instance_name,
            ignored_raw_block_index=ignored_raw_block_index,
        )

    @staticmethod
    def _connection_entry_to_tuple(entry: Any) -> tuple[str, int, str, int] | None:
        """Normalize one raw connection entry to the typed tuple form."""
        return shared_connection_entry_to_tuple(entry)
