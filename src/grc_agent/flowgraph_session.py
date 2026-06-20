"""Session management for loading, validating, and editing `.grc` flowgraphs.

The session keeps a typed view of the flowgraph and the original YAML payload in
sync so structural edits can be validated with `grcc` and saved without dropping
unsupported fields.
"""

import copy
import fcntl
import hashlib
import logging
import os
import shutil
import subprocess
import tempfile
import time
from collections import defaultdict, deque
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import yaml

from grc_agent.runtime.tool_context import is_variable_block

from ._payload import Block, Connection, Flowgraph
from .session_ops import (
    block_name_is_referenced_elsewhere as shared_block_name_is_referenced_elsewhere,
)
from .session_ops import (
    connection_entry_to_tuple as shared_connection_entry_to_tuple,
)
from .session_ops import (
    connection_id as shared_connection_id,
)
from .session_ops import (
    default_block_states as shared_default_block_states,
)
from .session_ops import (
    parse_blocks as shared_parse_blocks,
)
from .session_ops import (
    parse_connections as shared_parse_connections,
)
from .session_ops import (
    raw_connection_entry as shared_raw_connection_entry,
)

logger = logging.getLogger(__name__)

DEFAULT_SUMMARY_BLOCK_LIMIT = 8
DEFAULT_CONTEXT_MAX_NODES = 20
MAX_CONTEXT_HOPS = 4
MAX_CONTEXT_MAX_NODES = 50
MAX_CONTEXT_PARAMETER_SAMPLE = 6

# Active-session payload preview limits (model-visible every turn).
# These are display caps for the compact session snapshot, not param/block
# filters. See GuardrailsConfig for the config-driven equivalents.
_MAX_CONNECTION_PREVIEW = 8
_MAX_VARIABLE_PREVIEW = 8
_MAX_BLOCK_PREVIEW = 6


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
        self.last_validation_revision: int | None = None
        # Revision and caches support bounded inspection and retrieval reuse.
        self._state_revision = 0
        self._graph_id_revision: int | None = None
        self._graph_id_cache: str | None = None
        self._inspection_cache_revision: int | None = None
        self._block_matches_cache: dict[str, tuple[Block, ...]] = {}
        self._neighbor_names_cache: dict[str, tuple[str, ...]] = {}
        self._incoming_connection_cache: dict[str, tuple[Connection, ...]] = {}
        self._outgoing_connection_cache: dict[str, tuple[Connection, ...]] = {}
        self._persisted_file_sha256: str | None = None
        self._last_failed_ops_hash: str | None = None

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
        self._persisted_file_sha256 = self._sha256_text(raw_text)
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
        self.last_validation_revision = None
        self._bump_state_revision()
        logger.info("load path=%s blocks=%d connections=%d", source_path, len(blocks), len(connections))

    @classmethod
    def from_raw_data(
        cls,
        raw_data: dict[str, Any],
        *,
        path: str | Path | None = None,
        dirty: bool = False,
    ) -> "FlowgraphSession":
        """Build a session from an already parsed `.grc` payload."""
        if not isinstance(raw_data, dict):
            raise ValueError("Top-level .grc data must be a mapping.")

        copied_raw_data = copy.deepcopy(raw_data)
        session = cls(path=path)
        blocks = session._parse_blocks(copied_raw_data.get("blocks"))
        connections = session._parse_connections(copied_raw_data.get("connections"))
        metadata = {
            key: value
            for key, value in copied_raw_data.items()
            if key not in {"blocks", "connections"}
        }
        session.flowgraph = Flowgraph(
            blocks=blocks,
            connections=connections,
            metadata=metadata,
            raw_data=copied_raw_data,
        )
        session.is_dirty = dirty
        session.last_validation_stdout = None
        session.last_validation_stderr = None
        session.last_validation_returncode = None
        session.last_validation_ok = None
        session.last_validation_revision = None
        if path is not None and not dirty:
            session._persisted_file_sha256 = session._read_file_sha256_if_available(Path(path))
        session._bump_state_revision()
        return session

    @classmethod
    def create(cls, *, path: str | Path | None = None, graph_id: str = "new_flowgraph") -> "FlowgraphSession":
        """Create a minimal valid empty GRC session with no DSP blocks."""
        raw_data: dict[str, Any] = {
            "options": {
                "parameters": {
                    "author": "",
                    "catch_exceptions": "True",
                    "category": "[GRC Hier Blocks]",
                    "cmake_opt": "",
                    "comment": "",
                    "copyright": "",
                    "description": "",
                    "gen_cmake": "On",
                    "gen_linking": "dynamic",
                    "generate_options": "qt_gui",
                    "hier_block_src_path": ".:",
                    "id": graph_id,
                    "max_nouts": "0",
                    "output_language": "python",
                    "placement": "(0,0)",
                    "qt_qss_theme": "",
                    "realtime_scheduling": "",
                    "run": "True",
                    "run_command": "{python} -u {filename}",
                    "run_options": "prompt",
                    "sizing_mode": "fixed",
                    "thread_safe_setters": "",
                    "title": graph_id,
                    "window_size": "(1000,1000)",
                },
                "states": {
                    "bus_sink": False,
                    "bus_source": False,
                    "bus_structure": None,
                    "coordinate": [8, 8],
                    "rotation": 0,
                    "state": "enabled",
                },
            },
            "blocks": [],
            "connections": [],
            "metadata": {
                "file_format": 1,
            },
        }

        session = cls(path=path)
        blocks = session._parse_blocks(raw_data.get("blocks"))
        connections = session._parse_connections(raw_data.get("connections"))
        metadata = {
            key: value
            for key, value in raw_data.items()
            if key not in {"blocks", "connections"}
        }
        session.flowgraph = Flowgraph(
            blocks=blocks,
            connections=connections,
            metadata=metadata,
            raw_data=raw_data,
        )
        session.is_dirty = True
        session.last_validation_stdout = None
        session.last_validation_stderr = None
        session.last_validation_returncode = None
        session.last_validation_ok = None
        session.last_validation_revision = None
        session._persisted_file_sha256 = None
        session._bump_state_revision()
        logger.info("create graph_id=%s", graph_id)
        return session

    def save(self, path: str | Path | None = None, *, validate: bool = True) -> None:
        """Write the current in-memory graph to disk."""
        # Refuse to save if no flowgraph has been loaded yet.
        if self.flowgraph is None:
            raise ValueError("No flowgraph loaded.")

        # Use the explicit path when provided, otherwise fall back to the session path.
        target_path = Path(path) if path is not None else self.path
        if target_path is None:
            raise ValueError("No save path provided and no session path is set.")

        # Save only a graph version that has passed grcc validation.
        if (
            validate
            and (
            self.last_validation_ok is not True
            or self.last_validation_revision != self._state_revision
            )
        ):
            if not self.validate():
                raise ValueError(
                    "Refusing to save invalid graph: "
                    + self._grcc_failure_message(
                        self.last_validation_stdout or "",
                        self.last_validation_stderr or "",
                    )
                )

        # Serialize the current YAML once so save and validate stay consistent.
        serialized = self._serialize_raw_data(self.flowgraph.raw_data)

        serialized_hash = self._sha256_text(serialized)
        with self._save_file_lock(target_path):
            self._refuse_ambiguous_save_target(target_path)
            if target_path == self.path and self._persisted_file_sha256 is not None:
                current_hash = self._read_file_sha256_if_available(target_path)
                if current_hash != self._persisted_file_sha256:
                    raise OSError(
                        "Refusing to save flowgraph because the active file changed "
                        "on disk since it was loaded or saved. Reload before saving."
                    )
            self._write_save_backup(target_path)
            self._atomic_write_text(target_path, serialized)
            persisted_hash = self._read_file_sha256_if_available(target_path)
            if persisted_hash != serialized_hash:
                raise OSError(
                    "Failed to verify saved flowgraph contents after atomic replace."
                )
            self._persisted_file_sha256 = persisted_hash

        path_changed = target_path != self.path

        # Saving makes the new path the active session path and clears the dirty flag.
        self.path = target_path
        self.is_dirty = False
        if path_changed:
            self._bump_state_revision()
        logger.info("save path=%s", target_path)

    @property
    def persisted_file_sha256(self) -> str | None:
        """Return the hash of the file version this session last loaded or saved."""
        return self._persisted_file_sha256

    @staticmethod
    def _sha256_text(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    def _read_file_sha256_if_available(path: Path) -> str | None:
        try:
            return hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            logging.getLogger("grc_agent").debug("hash_file_failed path=%s: %s", path, exc)
            return None

    @staticmethod
    @contextmanager
    def _save_file_lock(target_path: Path) -> Any:
        control_dir = target_path.parent / ".grc_agent"
        try:
            control_dir.mkdir(mode=0o700, exist_ok=True)
        except OSError as exc:
            raise OSError(
                f"Could not create save lock directory for {target_path}: {exc}"
            ) from exc
        lock_path = control_dir / f"{target_path.name}.lock"
        try:
            with lock_path.open("a", encoding="utf-8") as lock_file:
                try:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                except OSError as exc:
                    raise OSError(
                        f"Could not lock active graph before saving {target_path}: {exc}"
                    ) from exc
                try:
                    yield
                finally:
                    try:
                        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
                    except OSError:
                        logger.warning("save_lock_release_failed path=%s", lock_path)
        except OSError:
            raise

    @staticmethod
    def _refuse_ambiguous_save_target(target_path: Path) -> None:
        if not target_path.exists():
            return
        if target_path.is_symlink():
            raise OSError(f"Refusing to save through symlink: {target_path}")
        try:
            stat_result = target_path.stat()
        except OSError as exc:
            raise OSError(f"Could not stat save target {target_path}: {exc}") from exc
        if stat_result.st_nlink > 1:
            raise OSError(f"Refusing to save hard-linked graph file: {target_path}")

    @staticmethod
    def _write_save_backup(target_path: Path) -> Path | None:
        if not target_path.exists():
            return None
        old_hash = FlowgraphSession._read_file_sha256_if_available(target_path)
        if old_hash is None:
            raise OSError(f"Could not hash existing save target: {target_path}")
        backup_dir = target_path.parent / ".grc_agent" / "backups"
        try:
            backup_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        except OSError as exc:
            raise OSError(
                f"Could not create save backup directory for {target_path}: {exc}"
            ) from exc
        timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        backup_path = backup_dir / f"{timestamp}-{old_hash[:16]}{target_path.suffix}"
        if backup_path.exists():
            backup_path = backup_dir / (
                f"{timestamp}-{old_hash[:16]}-{time.time_ns()}{target_path.suffix}"
            )
        try:
            shutil.copy2(target_path, backup_path)
        except OSError as exc:
            raise OSError(f"Could not create save backup for {target_path}: {exc}") from exc
        return backup_path

    def file_integrity_state(self) -> dict[str, Any]:
        """Return whether the active file still matches the loaded/saved version."""
        payload: dict[str, Any] = {
            "path": str(self.path) if self.path is not None else None,
            "persisted_sha256": self._persisted_file_sha256,
            "current_sha256": None,
            "status": "untracked",
            "externally_modified": False,
        }
        if self.path is None or self._persisted_file_sha256 is None:
            return payload
        if not self.path.exists():
            payload["status"] = "missing"
            payload["externally_modified"] = True
            return payload
        try:
            current_sha256 = hashlib.sha256(self.path.read_bytes()).hexdigest()
        except OSError as exc:
            payload["status"] = "unreadable"
            payload["externally_modified"] = True
            payload["error"] = str(exc)
            return payload
        payload["current_sha256"] = current_sha256
        if current_sha256 == self._persisted_file_sha256:
            payload["status"] = "clean"
            return payload
        payload["status"] = "modified"
        payload["externally_modified"] = True
        return payload

    @staticmethod
    def _atomic_write_text(target_path: Path, text: str) -> None:
        parent = target_path.parent
        if not parent.exists():
            raise ValueError(f"Save directory does not exist: {parent}")
        if not parent.is_dir():
            raise ValueError(f"Save parent is not a directory: {parent}")

        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=parent,
                prefix=f".{target_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temp_file:
                temp_path = Path(temp_file.name)
                temp_file.write(text)
                temp_file.flush()
                os.fsync(temp_file.fileno())

            os.replace(temp_path, target_path)
            temp_path = None
            FlowgraphSession._fsync_directory(parent)
        except OSError as exc:
            if temp_path is not None:
                try:
                    temp_path.unlink()
                except OSError:
                    logger.warning("atomic_save_temp_cleanup_failed path=%s", temp_path)
            raise OSError(f"Failed to save flowgraph to {target_path}: {exc}") from exc

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        try:
            fd = os.open(directory, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(fd)
        except OSError:
            logger.debug("atomic_save_directory_fsync_failed path=%s", directory)
        finally:
            os.close(fd)

    DEFAULT_GRCC_TIMEOUT_SECONDS = 30.0

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
        self.last_validation_revision = self._state_revision if is_valid else None
        logger.info("validate ok=%s returncode=%s", is_valid, returncode)
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
            "state_revision": self.last_validation_revision,
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

    def active_session_snapshot(self) -> dict[str, Any]:
        """Return the compact active-session payload for runtime history and CLI output."""
        flowgraph = self._require_loaded_flowgraph()
        snapshot = self.session_provenance()
        snapshot["state_revision"] = self.state_revision
        snapshot["dirty"] = self.is_dirty
        snapshot["validation"] = self.validation_state()
        snapshot["block_count"] = len(flowgraph.blocks)
        snapshot["connection_count"] = len(flowgraph.connections)
        snapshot["variable_count"] = sum(
            1 for block in flowgraph.blocks if is_variable_block(block.block_type)
        )
        variable_preview: list[str] = []
        block_preview: list[str] = []
        for block in flowgraph.blocks:
            if is_variable_block(block.block_type):
                value = block.params.get("parameters", {}).get("value", "")
                variable_preview.append(f"{block.instance_name}={value}")
                continue
            block_preview.append(
                f"{block.instance_name} ({block.block_type})"
            )

        total_connections = len(flowgraph.connections)
        connections_sorted = sorted(flowgraph.connections, key=self._connection_sort_key)
        connection_preview = [
            shared_connection_id(
                connection.src_block,
                connection.src_port,
                connection.dst_block,
                connection.dst_port,
            )
            for connection in connections_sorted[:_MAX_CONNECTION_PREVIEW]
        ]
        if total_connections > _MAX_CONNECTION_PREVIEW:
            connection_preview.append(f"... [TRUNCATED connection_preview: was {total_connections} items, kept {_MAX_CONNECTION_PREVIEW}]")

        total_variables = len(variable_preview)
        if total_variables > _MAX_VARIABLE_PREVIEW:
            variable_preview = variable_preview[:_MAX_VARIABLE_PREVIEW]
            variable_preview.append(f"... [TRUNCATED variable_preview: was {total_variables} items, kept {_MAX_VARIABLE_PREVIEW}]")

        total_non_var_blocks = len(block_preview)
        if total_non_var_blocks > _MAX_BLOCK_PREVIEW:
            block_preview = block_preview[:_MAX_BLOCK_PREVIEW]
            block_preview.append(f"... [TRUNCATED block_preview: was {total_non_var_blocks} items, kept {_MAX_BLOCK_PREVIEW}]")

        if variable_preview:
            snapshot["variable_preview"] = variable_preview
        if block_preview:
            snapshot["block_preview"] = block_preview
        if connection_preview:
            snapshot["connection_preview"] = connection_preview
        return snapshot

    def summary_payload(
        self, *, max_blocks: int = DEFAULT_SUMMARY_BLOCK_LIMIT
    ) -> dict[str, Any]:
        """Return the structured bounded summary payload for the loaded graph."""
        flowgraph = self._require_loaded_flowgraph()
        if not isinstance(max_blocks, int) or max_blocks < 1:
            raise ValueError("max_blocks must be a positive integer.")

        variable_count = sum(
            1 for block in flowgraph.blocks if is_variable_block(block.block_type)
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
        # Build the structured blocks list so the model can see truncated counts.
        structured_blocks: list[dict[str, Any]] = [
            {
                "name": block.instance_name,
                "block_uid": block.block_uid,
                "type": block.block_type,
            }
            for block in preview_blocks
        ]
        return {
            "ok": True,
            "summary": summary,
            "path": str(self.path) if self.path is not None else None,
            "graph_id": self.graph_id(),
            "block_count": len(flowgraph.blocks),
            "connection_count": len(flowgraph.connections),
            "blocks_shown": len(structured_blocks),
            "blocks_truncated": remaining,
            "blocks": structured_blocks,
            "connections": [
                self._connection_payload(connection)
                for connection in sorted(
                    flowgraph.connections,
                    key=self._connection_sort_key,
                )
            ],
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
                **self._connection_payload(connection),
                "source": connection.src_block,
                "source_port": connection.src_port,
                "target": connection.dst_block,
                "target_port": connection.dst_port,
            }
            for connection in sorted(
                flowgraph.connections,
                key=self._connection_sort_key,
            )
            if connection.src_block in included_name_set
            and connection.dst_block in included_name_set
        ]

        return {
            "ok": True,
            "node_id": normalized_node_id,
            "nodes": nodes,
            "edges": edges,
            "truncated": truncated,
        }

    # Connection edits

    def disconnect(
        self, src_block: str, src_port: "int | str", dst_block: str, dst_port: "int | str"
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
        self, src_block: str, src_port: "int | str", dst_block: str, dst_port: "int | str"
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
        *,
        _skip_grcc: bool = False,
    ) -> None:
        """Add one block after validating a candidate graph.

        When ``_skip_grcc`` is *True* the per-op grcc check is skipped so that
        multi-op transactions can add disconnected stream blocks and connect
        them later in the same atomic batch.  The caller (typically
        ``apply_edit``) is responsible for running grcc on the final candidate.
        """
        if self.flowgraph is None:
            raise ValueError("No flowgraph loaded.")

        if not isinstance(instance_name, str) or not instance_name:
            raise ValueError("Block instance_name must be a non-empty string.")
        if not isinstance(block_type, str) or not block_type:
            raise ValueError("Block block_type must be a non-empty string.")
        if not isinstance(parameters, dict):
            raise ValueError("Block parameters must be a mapping.")
        if states is not None and not isinstance(states, dict):
            raise ValueError("Block states must be a mapping when provided.")

        raw_blocks = self.flowgraph.raw_data.get("blocks")
        if not isinstance(raw_blocks, list):
            raise ValueError("Flowgraph raw_data blocks section is invalid.")

        self._assert_new_block_name_available(instance_name, raw_blocks)

        add_default_comment = is_variable_block(block_type)
        raw_block, raw_parameters, raw_states = self._prepare_new_block_payload(
            instance_name=instance_name,
            block_type=block_type,
            parameters=parameters,
            states=states,
            existing_block_count=len(raw_blocks),
            add_default_comment=add_default_comment,
        )

        if not _skip_grcc:
            candidate_raw_data = copy.deepcopy(self.flowgraph.raw_data)
            candidate_raw_blocks = candidate_raw_data.get("blocks")
            if not isinstance(candidate_raw_blocks, list):
                raise ValueError(
                    "Flowgraph candidate blocks section is invalid."
                )
            candidate_raw_blocks.append(copy.deepcopy(raw_block))

            self._validate_candidate_raw_data_or_raise(
                candidate_raw_data,
                error_prefix="Added block failed validation",
            )

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

        self.is_dirty = True
        self._bump_state_revision()

    # Block removal and parameter edits

    def remove_block(self, instance_name: str, *, block_type: str | None = None) -> None:
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
            and (block_type is None or block.block_type == block_type)
        ]
        raw_indexes = [
            index
            for index, entry in enumerate(raw_blocks)
            if isinstance(entry, dict)
            and entry.get("name") == instance_name
            and (block_type is None or entry.get("id") == block_type)
        ]

        if not parsed_indexes or not raw_indexes:
            msg = f"Block not found: {instance_name}"
            if block_type:
                msg += f" (type: {block_type})"
            raise ValueError(msg)

        if len(parsed_indexes) != 1 or len(raw_indexes) != 1:
            raise ValueError(
                f"Block name '{instance_name}' is not unique. Provide block_type to disambiguate."
            )

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

    def set_param(
        self,
        instance_name: str,
        parameter_key: str,
        value: object,
        *,
        block_type: str | None = None,
    ) -> None:
        """Update one block parameter in both the model and raw YAML."""
        # Refuse to mutate anything if no graph has been loaded yet.
        if self.flowgraph is None:
            raise ValueError("No flowgraph loaded.")

        # Find the parsed block object by its instance name.
        parsed_indexes = [
            index
            for index, block in enumerate(self.flowgraph.blocks)
            if block.instance_name == instance_name
            and (block_type is None or block.block_type == block_type)
        ]

        if not parsed_indexes:
            msg = f"Block not found: {instance_name}"
            if block_type:
                msg += f" (type: {block_type})"
            raise ValueError(msg)

        if len(parsed_indexes) > 1:
            raise ValueError(
                f"Block name '{instance_name}' is not unique. Provide block_type to disambiguate."
            )

        block = self.flowgraph.blocks[parsed_indexes[0]]

        # The raw YAML must change too so save() and validate() see the mutation.
        raw_blocks = self.flowgraph.raw_data.get("blocks")
        if not isinstance(raw_blocks, list):
            raise ValueError("Flowgraph raw_data blocks section is invalid.")

        # Find the matching raw block entry.
        raw_indexes = [
            index
            for index, entry in enumerate(raw_blocks)
            if isinstance(entry, dict)
            and entry.get("name") == instance_name
            and (block_type is None or entry.get("id") == block_type)
        ]

        if not raw_indexes:
            raise ValueError(f"Raw block not found: {instance_name}")
        if len(raw_indexes) > 1:
            raise ValueError(
                f"Raw block name '{instance_name}' is not unique. Provide block_type to disambiguate."
            )

        raw_block = raw_blocks[raw_indexes[0]]

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

        if parameters.get(parameter_key) == value:
            return

        parameters[parameter_key] = value
        raw_parameters[parameter_key] = value

        # Any successful mutation means the in-memory session now differs from disk.
        self.is_dirty = True
        self._bump_state_revision()

    def set_block_state(
        self, instance_name: str, state: str, *, block_type: str | None = None
    ) -> None:
        """Update one block's enabled/disabled state in both the model and raw YAML."""
        if self.flowgraph is None:
            raise ValueError("No flowgraph loaded.")
        if state not in {"enabled", "disabled", "bypass"}:
            raise ValueError(f"Invalid block state: {state}")

        # If duplicate names exist, use block_type to disambiguate.
        parsed_indexes = [
            index
            for index, block in enumerate(self.flowgraph.blocks)
            if block.instance_name == instance_name
            and (block_type is None or block.block_type == block_type)
        ]

        if not parsed_indexes:
            msg = f"Block not found: {instance_name}"
            if block_type:
                msg += f" (type: {block_type})"
            raise ValueError(msg)

        if len(parsed_indexes) > 1:
            raise ValueError(
                f"Block name '{instance_name}' is not unique. Provide block_type to disambiguate."
            )

        block = self.flowgraph.blocks[parsed_indexes[0]]

        raw_blocks = self.flowgraph.raw_data.get("blocks")
        if not isinstance(raw_blocks, list):
            raise ValueError("Flowgraph raw_data blocks section is invalid.")

        raw_indexes = [
            index
            for index, entry in enumerate(raw_blocks)
            if isinstance(entry, dict)
            and entry.get("name") == instance_name
            and (block_type is None or entry.get("id") == block_type)
        ]

        if not raw_indexes:
            raise ValueError(f"Raw block not found: {instance_name}")
        if len(raw_indexes) > 1:
            raise ValueError(
                f"Raw block name '{instance_name}' is not unique. Provide block_type to disambiguate."
            )

        raw_block = raw_blocks[raw_indexes[0]]
        states = block.params.setdefault("states", {})
        if not isinstance(states, dict):
            raise ValueError(f"Block states section is invalid for: {instance_name}")

        raw_states = raw_block.setdefault("states", {})
        if not isinstance(raw_states, dict):
            raise ValueError(f"Raw block states section is invalid for: {instance_name}")

        if states.get("state") == state:
            return

        states["state"] = state
        raw_states["state"] = state

        self.is_dirty = True
        self._bump_state_revision()

    def _bump_state_revision(self) -> None:
        """Advance the session revision after load, save-path changes, or mutation."""
        self._state_revision += 1
        self.last_validation_stdout = None
        self.last_validation_stderr = None
        self.last_validation_returncode = None
        self.last_validation_ok = None
        self.last_validation_revision = None

    def _require_loaded_flowgraph(self) -> Flowgraph:
        """Return the active flowgraph or raise when the session is empty."""
        if self.flowgraph is None:
            raise ValueError("No flowgraph loaded.")
        return self.flowgraph

    def resolve_block_reference(
        self,
        instance_name: str | None = None,
        *,
        block_uid: str | None = None,
        block_type: str | None = None,
    ) -> dict[str, Any]:
        """Return read-only block identity candidates for clarification flows.

        `block_uid` is identity evidence only. This resolver intentionally does
        not mutate and does not authorize later mutation without a fresh
        revision check.
        """
        flowgraph = self._require_loaded_flowgraph()
        candidates = []
        for block in flowgraph.blocks:
            if instance_name is not None and block.instance_name != instance_name:
                continue
            if block_uid is not None and block.block_uid != block_uid:
                continue
            if block_type is not None and block.block_type != block_type:
                continue
            candidates.append(self._block_identity_payload(block))

        return {
            "state_revision": self.state_revision,
            "unique": len(candidates) == 1,
            "candidates": candidates,
        }

    def find_connection_candidates(
        self,
        *,
        src_block: str | None = None,
        src_port: int | str | None = None,
        dst_block: str | None = None,
        dst_port: int | str | None = None,
    ) -> dict[str, Any]:
        """Return read-only connection candidates matching exact endpoint hints."""
        flowgraph = self._require_loaded_flowgraph()
        candidates = [
            self._connection_payload(connection)
            for connection in sorted(flowgraph.connections, key=self._connection_sort_key)
            if self._connection_matches(
                connection,
                src_block=src_block,
                src_port=src_port,
                dst_block=dst_block,
                dst_port=dst_port,
            )
        ]
        return {
            "state_revision": self.state_revision,
            "unique": len(candidates) == 1,
            "candidates": candidates,
        }

    @staticmethod
    def _block_identity_payload(block: Block) -> dict[str, Any]:
        parameters = block.params.get("parameters")
        states = block.params.get("states")
        return {
            "name": block.instance_name,
            "block_uid": block.block_uid,
            "block_type": block.block_type,
            "state": states.get("state") if isinstance(states, dict) else None,
            "coordinate": states.get("coordinate") if isinstance(states, dict) else None,
            "parameter_keys": sorted(parameters) if isinstance(parameters, dict) else [],
        }

    @staticmethod
    def _connection_matches(
        connection: Connection,
        *,
        src_block: str | None,
        src_port: int | str | None,
        dst_block: str | None,
        dst_port: int | str | None,
    ) -> bool:
        if src_block is not None and connection.src_block != src_block:
            return False
        if dst_block is not None and connection.dst_block != dst_block:
            return False
        if src_port is not None and not FlowgraphSession._port_matches(
            connection.src_port, src_port
        ):
            return False
        if dst_port is not None and not FlowgraphSession._port_matches(
            connection.dst_port, dst_port
        ):
            return False
        return True

    @staticmethod
    def _port_matches(actual: int | str, requested: int | str) -> bool:
        return actual == requested or str(actual) == str(requested)

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

        def edge_sort_key(edge: Connection) -> tuple:
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

    @staticmethod
    def _connection_sort_key(connection: Connection) -> tuple:
        """Return the canonical sort key for stable connection output ordering."""
        return (
            connection.src_block,
            FlowgraphSession._port_sort_key(connection.src_port),
            connection.dst_block,
            FlowgraphSession._port_sort_key(connection.dst_port),
        )

    @staticmethod
    def _port_sort_key(port: int | str) -> tuple[int, str]:
        """Sort stream indices before message ports without comparing unlike types."""
        if isinstance(port, int) and not isinstance(port, bool):
            return (0, str(port))
        return (1, str(port))

    @staticmethod
    def _connection_payload(connection: Connection) -> dict[str, Any]:
        """Render one connection into a stable endpoint-plus-id payload."""
        return {
            "connection_id": shared_connection_id(
                connection.src_block,
                connection.src_port,
                connection.dst_block,
                connection.dst_port,
            ),
            "src_block": connection.src_block,
            "src_port": connection.src_port,
            "dst_block": connection.dst_block,
            "dst_port": connection.dst_port,
        }

    def _context_node_payload(self, block: Block, *, distance: int) -> dict[str, Any]:
        """Render one block into the bounded session-context node payload."""
        parameters = block.params.get("parameters")
        parameter_map = parameters if isinstance(parameters, dict) else {}
        return {
            "node_id": block.instance_name,
            "block_uid": block.block_uid,
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
            n = len(value)
            if n > 4:
                return (
                    "["
                    + ", ".join(FlowgraphSession._compact_value(item) for item in value[:4])
                    + f", ... [TRUNCATED list: was {n} items, kept 4]"
                    + "]"
                )
            return "[" + ", ".join(FlowgraphSession._compact_value(item) for item in value) + "]"
        if isinstance(value, dict):
            n = len(value)
            if n > 4:
                items = list(value.items())[:4]
                body = ", ".join(
                    f"{key}={FlowgraphSession._compact_value(item)}" for key, item in items
                )
                return "{" + body + f", ... [TRUNCATED dict: was {n} keys, kept 4]" + "}"
            body = ", ".join(
                f"{key}={FlowgraphSession._compact_value(item)}" for key, item in value.items()
            )
            return "{" + body + "}"
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
    def _run_grcc_validation(raw_data: Any, *, timeout: float | None = None) -> tuple[bool, str, str, int]:
        """Run `grcc` against raw flowgraph data inside a temporary workspace."""
        # Serialize the candidate YAML once so validation stays consistent everywhere.
        serialized = FlowgraphSession._serialize_raw_data(raw_data)

        wall_timeout = timeout if timeout is not None else FlowgraphSession.DEFAULT_GRCC_TIMEOUT_SECONDS

        # Use a temporary directory so validation does not touch project files.
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a throwaway .grc file for grcc to compile.
            temp_path = Path(tmpdir) / "validate.grc"
            temp_path.write_text(serialized, encoding="utf-8")

            # Run grcc and return the normalized validity plus the raw diagnostics.
            try:
                result = subprocess.run(
                    ["grcc", str(temp_path)],
                    capture_output=True,
                    text=True,
                    cwd=tmpdir,
                    timeout=wall_timeout,
                )
            except subprocess.TimeoutExpired:
                return (
                    False,
                    "",
                    f"grcc validation timed out after {wall_timeout}s",
                    -2,
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
        src_port: "int | str",
        dst_block: str,
        dst_port: "int | str",
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
            raise ValueError(f"Block already exists: {instance_name}.")
        if any(
            isinstance(entry, dict) and entry.get("name") == instance_name
            for entry in raw_blocks
        ):
            raise ValueError(f"Raw block already exists: {instance_name}.")

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
    def _connection_entry_to_tuple(entry: Any) -> tuple | None:
        """Normalize one raw connection entry to the typed tuple form."""
        return shared_connection_entry_to_tuple(entry)
