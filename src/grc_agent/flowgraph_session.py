"""Session management — thin shim over the native GRC adapter.

Phase 6 cutover: the legacy YAML-parsing, ``grcc`` subprocess, and dict-crawl
inspection paths are gone. This module retains only atomic save, file integrity,
and state revision; everything else delegates to :mod:`grc_agent.grc_native_adapter`.

Rollback point: ``git tag phase-6-baseline`` (the commit before this cutover).
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

from grc_agent.domain_models import BlockRole, ErrorCode, build_error_payload
from grc_agent.grc_native_adapter import (
    exclusive_file_lock,
    get_platform,
    load_flow_graph,
    refuse_ambiguous_save_target,
    render_flow_graph,
    serialize_raw_data,
    validate,
    write_flow_graph_atomic,
    write_save_backup,
)

SUMMARY_PREVIEW_LIMIT = 3


class FlowgraphSession:
    """Own one ``.grc`` flowgraph, persisting through the native adapter.

    The ``flowgraph`` attribute is a native ``gnuradio.grc.core.FlowGraph``
    (not the legacy parsed model). All reads and writes go through the
    adapter; this class owns only path, integrity, atomic save, and revision.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else None
        self.flowgraph: Any | None = None
        self.is_dirty = False
        # Validation snapshot (consumed by ``summary_payload`` to report
        # status without re-running ``validate()`` on every call).
        self.last_validation_ok: bool | None = None
        self.last_validation_revision: int | None = None
        self._state_revision = 0
        self._persisted_file_sha256: str | None = None

    # -- identity / revision --------------------------------------------------

    @property
    def state_revision(self) -> int:
        return self._state_revision

    def bump_revision(self) -> None:
        """Increment the state revision (public API for the change_graph engine)."""
        self._state_revision += 1

    def set_state_revision(self, value: int) -> None:
        """Restore the state revision (used by ``transaction.restore_session``)."""
        self._state_revision = int(value)

    def set_persisted_sha256(self, value: str | None) -> None:
        """Restore the persisted-file SHA-256 (used by ``transaction.restore_session``)."""
        self._persisted_file_sha256 = value

    @property
    def persisted_file_sha256(self) -> str | None:
        return self._persisted_file_sha256

    # -- load / save ----------------------------------------------------------

    def load(self, path: str | Path) -> None:
        source_path = Path(path)
        self.flowgraph = load_flow_graph(source_path)
        self.path = source_path
        self.is_dirty = False
        self._state_revision = 1
        self._persisted_file_sha256 = FlowgraphSession._read_file_sha256_if_available(source_path)

    def save(self, path: str | Path | None = None) -> None:
        target = Path(path) if path is not None else self.path
        if target is None:
            raise ValueError("No save path: pass path= or load a file first.")
        if self.flowgraph is None:
            raise RuntimeError("No flowgraph loaded.")
        integrity = self.file_integrity_state()
        if integrity.get("externally_modified"):
            raise OSError(f"Refusing to save: file changed on disk at {target}")

        refuse_ambiguous_save_target(target)
        write_save_backup(target)
        with exclusive_file_lock(target.parent / ".grc_agent" / f"{target.name}.lock"):
            write_flow_graph_atomic(self.flowgraph, target)
        self.path = target
        self._persisted_file_sha256 = FlowgraphSession._read_file_sha256_if_available(target)
        self.is_dirty = False

    # -- snapshot / validation / identity (adapter-backed) --------------------

    def summary_payload(
        self, *, block_limit: int = 8, max_blocks: int | None = None
    ) -> dict[str, Any]:
        if max_blocks is not None:
            block_limit = max_blocks
        if self.flowgraph is None:
            return build_error_payload(
                error_type=ErrorCode.INVALID_REQUEST,
                message="No flowgraph loaded.",
            )

        snapshot = render_flow_graph(self.flowgraph)
        if (
            self.last_validation_revision is not None
            and self.last_validation_revision == self._state_revision
        ):
            validation_payload = {
                "status": "valid" if self.last_validation_ok else "invalid",
                "returncode": 0 if self.last_validation_ok else 1,
                "errors": [],
            }
        else:
            validation_payload = {"status": "unknown", "errors": []}

        user_blocks = [
            b
            for b in snapshot.blocks
            if b.role != BlockRole.OPTIONS
        ]
        all_blocks = [
            {
                "instance_name": b.instance_name,
                "block_id": b.block_id,
                "role": b.role.value,
            }
            for b in user_blocks
        ]
        variable_count = sum(1 for b in user_blocks if b.role == BlockRole.VARIABLE)
        all_conns = sorted(snapshot.connections)
        block_summaries = [f"{b['instance_name']} ({b['block_id']})" for b in all_blocks[:SUMMARY_PREVIEW_LIMIT]]
        if len(all_blocks) > SUMMARY_PREVIEW_LIMIT:
            block_summaries.append(f"... +{len(all_blocks) - SUMMARY_PREVIEW_LIMIT} more")
        summary_text = (
            f"{Path(self.path).name if self.path else 'graph'}: "
            f"{len(all_blocks)} blocks, {len(all_conns)} connections. " + ", ".join(block_summaries)
        )
        gid = self._persisted_file_sha256 or ""
        return {
            "ok": snapshot.ok,
            "path": str(self.path) if self.path else None,
            "graph_id": f"grc:{gid}" if gid else None,
            "graph_name": snapshot.graph_name,
            "block_count": len(all_blocks),
            "connection_count": len(all_conns),
            "variable_count": variable_count,
            "dirty": self.is_dirty,
            "blocks": all_blocks[:block_limit],
            "connections": all_conns,
            "validation": validation_payload,
            "summary": summary_text,
        }

    def validation_state(self) -> dict[str, Any]:
        if self.flowgraph is None:
            return {"status": "unknown", "errors": []}

        v = validate(self.flowgraph)
        self.last_validation_ok = v.native_ok
        self.last_validation_revision = self._state_revision
        return {"status": v.status, "errors": v.errors, "state_revision": self._state_revision}

    def graph_id(self) -> str | None:
        return self._persisted_file_sha256

    # -- raw-data construction (used by history.py) ---------------------------

    @classmethod
    def from_raw_data(
        cls, raw_data: dict[str, Any], path: str | Path | None = None
    ) -> FlowgraphSession:
        """Create a session from a raw data dict (GRC import_data format)."""
        session = cls(path)
        fg = get_platform().make_flow_graph()
        fg.import_data(raw_data)
        fg.rewrite()
        session.flowgraph = fg
        return session

    @staticmethod
    def _serialize_raw_data(raw_data: Any) -> str:
        """Serialize a raw data dict to GRC-native YAML."""
        return serialize_raw_data(raw_data)

    def validate(self) -> bool:
        """Validate the loaded flowgraph. Returns is_valid."""
        if self.flowgraph is None:
            return False

        result = validate(self.flowgraph)
        self.last_validation_ok = result.native_ok
        self.last_validation_revision = self._state_revision
        return bool(result.native_ok)

    # -- integrity ------------------------------------------------------------

    def file_integrity_state(self) -> dict[str, Any]:
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

    # -- file integrity --------------------------------------------------------

    @staticmethod
    def _read_file_sha256_if_available(path: Path) -> str | None:
        try:
            return hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            logging.getLogger("grc_agent").debug(
                "hash_file_failed path=%s: %s", path, exc
            )
            return None

    def get_top_block_class_name(self) -> str:
        """Return the top-block class name (defaults to ``"top_block"``).

        Single source of truth for the top-block identity; replaces
        callers that reached into ``session.flowgraph.metadata["options"]
        ["parameters"]["id"]`` directly.
        """
        if self.flowgraph is None:
            return "top_block"
        options = getattr(self.flowgraph, "options_block", None)
        if options is None:
            return "top_block"
        return str(getattr(options, "name", "top_block") or "top_block")
