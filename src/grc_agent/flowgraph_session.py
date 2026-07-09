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

from grc_agent.grc_native_adapter import (
    exclusive_file_lock,
    load_flow_graph,
    refuse_ambiguous_save_target,
    validate,
    write_flow_graph_atomic,
    write_save_backup,
)


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

    def validation_state(self) -> dict[str, Any]:
        if self.flowgraph is None:
            return {"status": "unknown", "errors": []}

        v = validate(self.flowgraph)
        return {"status": v.status, "errors": v.errors, "state_revision": self._state_revision}

    def graph_id(self) -> str | None:
        return self._persisted_file_sha256

    def validate(self) -> bool:
        """Validate the loaded flowgraph. Returns is_valid."""
        if self.flowgraph is None:
            return False

        result = validate(self.flowgraph)
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
            logging.getLogger("grc_agent").debug("hash_file_failed path=%s: %s", path, exc)
            return None
