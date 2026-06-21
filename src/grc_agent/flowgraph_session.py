"""Session management — thin shim over the native GRC adapter.

Phase 6 cutover: the legacy YAML-parsing, ``grcc`` subprocess, and dict-crawl
inspection paths are gone. This module retains only atomic save, file integrity,
and state revision; everything else delegates to :mod:`grc_agent.grc_native_adapter`.

Rollback point: ``git tag phase-6-baseline`` (the commit before this cutover).
"""

from __future__ import annotations

import fcntl
import hashlib
import logging
import os
import shutil
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from grc_agent.grc_native_adapter import (
    bump_revision,
    load_flow_graph,
    write_flow_graph_atomic,
)

logger = logging.getLogger(__name__)


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
        # Validation tracking (kept for agent.py save-gating compatibility).
        self.last_validation_stdout: str | None = None
        self.last_validation_stderr: str | None = None
        self.last_validation_returncode: int | None = None
        self.last_validation_ok: bool | None = None
        self.last_validation_revision: int | None = None
        self._state_revision = 0
        self._persisted_file_sha256: str | None = None
        self._last_failed_ops_hash: str | None = None

    # -- identity / revision --------------------------------------------------

    @property
    def state_revision(self) -> int:
        return self._state_revision

    def _bump_state_revision(self) -> None:
        self._state_revision += 1

    @property
    def persisted_file_sha256(self) -> str | None:
        return self._persisted_file_sha256

    # -- load / save ----------------------------------------------------------

    @classmethod
    def create(cls, path: str | Path | None = None) -> FlowgraphSession:
        """Create a session with a blank native flowgraph."""
        from grc_agent.grc_native_adapter import get_platform
        session = cls(path)
        fg = get_platform().make_flow_graph()
        fg.rewrite()
        session.flowgraph = fg
        return session

    def load(self, path: str | Path) -> None:
        source_path = Path(path)
        self.flowgraph = load_flow_graph(source_path)
        self.path = source_path
        self.is_dirty = False
        self._state_revision = 0
        self._persisted_file_sha256 = FlowgraphSession._read_file_sha256_if_available(
            source_path
        )

    def save(self, path: str | Path | None = None) -> None:
        target = Path(path) if path is not None else self.path
        if target is None:
            raise ValueError("No save path: pass path= or load a file first.")
        if self.flowgraph is None:
            raise RuntimeError("No flowgraph loaded.")
        FlowgraphSession._refuse_ambiguous_save_target(target)
        FlowgraphSession._write_save_backup(target)
        with FlowgraphSession._save_file_lock(target):
            write_flow_graph_atomic(self.flowgraph, target)
        self.path = target
        self._persisted_file_sha256 = FlowgraphSession._read_file_sha256_if_available(
            target
        )
        self.is_dirty = False

    # -- snapshot / validation / identity (adapter-backed) --------------------

    def active_session_snapshot(self) -> dict[str, Any] | None:
        if self.flowgraph is None:
            return None
        from grc_agent.grc_native_adapter import render_flow_graph
        return render_flow_graph(self.flowgraph).model_dump(exclude_none=True)

    def summary_payload(self, *, block_limit: int = 8) -> dict[str, Any]:
        if self.flowgraph is None:
            return {"ok": False, "errors": [{"code": "no_flowgraph", "message": "No flowgraph loaded."}]}
        from grc_agent.grc_native_adapter import render_flow_graph
        snapshot = render_flow_graph(self.flowgraph)
        return {
            "ok": snapshot.ok,
            "graph_name": snapshot.graph_name,
            "blocks": [{"instance_name": b.instance_name, "block_type": b.block_type,
                         "role": b.role.value if hasattr(b.role, "value") else str(b.role)}
                        for b in snapshot.blocks[:block_limit]],
            "validation": snapshot.validation.model_dump(exclude_none=True),
        }

    def validation_state(self) -> dict[str, Any]:
        if self.flowgraph is None:
            return {"status": "unknown", "errors": []}
        from grc_agent.grc_native_adapter import validate
        v = validate(self.flowgraph)
        self.last_validation_ok = v.native_ok
        self.last_validation_revision = self._state_revision
        return {"status": v.status, "errors": v.errors, "state_revision": self._state_revision}

    def graph_id(self) -> str | None:
        return self._persisted_file_sha256

    # -- legacy compat (used by history.py + transaction.py) ------------------

    @classmethod
    def from_raw_data(cls, raw_data: dict[str, Any], path: str | Path | None = None) -> FlowgraphSession:
        """Create a session from a raw data dict (GRC import_data format)."""
        from grc_agent.grc_native_adapter import get_platform
        session = cls(path)
        fg = get_platform().make_flow_graph()
        fg.import_data(raw_data)
        fg.rewrite()
        session.flowgraph = fg
        return session

    @staticmethod
    def _serialize_raw_data(raw_data: Any) -> str:
        """Serialize a raw data dict to GRC-native YAML."""
        from grc_agent.grc_native_adapter import get_platform
        get_platform()  # warm the platform (io.yaml circular-import guard)
        from gnuradio.grc.core.io import yaml as _grc_yaml
        return _grc_yaml.dump(raw_data)

    def validate(self) -> bool:
        """Validate the loaded flowgraph. Returns is_valid."""
        if self.flowgraph is None:
            return False
        from grc_agent.grc_native_adapter import validate as _validate
        result = _validate(self.flowgraph)
        self.last_validation_ok = result.native_ok
        self.last_validation_revision = self._state_revision
        return bool(result.native_ok)

    # -- mutation helpers (adapter-backed; used by transaction.apply_operations) --

    def set_param(self, instance_name: str, key: str, value: Any, *, block_type: str | None = None) -> None:
        from grc_agent.grc_native_adapter import _find_block, set_param as _set_param
        _set_param(_find_block(self.flowgraph, instance_name), key, str(value))

    def set_block_state(self, instance_name: str, state: str, *, block_type: str | None = None) -> None:
        from grc_agent.grc_native_adapter import _find_block, set_block_state as _set_state
        _set_block_state(_find_block(self.flowgraph, instance_name), state)

    def connect(self, src_block: str, src_port: Any, dst_block: str, dst_port: Any) -> None:
        from grc_agent.grc_native_adapter import connect as _connect
        _connect(self.flowgraph, src_block, str(src_port), dst_block, str(dst_port))

    def disconnect(self, src_block: str, src_port: Any, dst_block: str, dst_port: Any) -> None:
        from grc_agent.grc_native_adapter import disconnect as _disconnect
        _disconnect(self.flowgraph, src_block, str(src_port), dst_block, str(dst_port))

    def remove_block(self, instance_name: str, *, block_type: str | None = None) -> None:
        from grc_agent.grc_native_adapter import remove_block as _remove
        _remove(self.flowgraph, instance_name)

    def add_block(self, instance_name: str, block_type: str,
                  parameters: dict[str, Any] | None = None,
                  states: dict[str, Any] | None = None,
                  *, _skip_grcc: bool = False) -> None:
        from grc_agent.grc_native_adapter import add_block as _add
        _add(self.flowgraph, block_type, instance_name, parameters or {})

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

    # -- atomic-save plumbing (preserved verbatim from Phase 0–5) --------------

    @staticmethod
    def _sha256_text(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    def _read_file_sha256_if_available(path: Path) -> str | None:
        try:
            return hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            logging.getLogger("grc_agent").debug(
                "hash_file_failed path=%s: %s", path, exc
            )
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
            raise OSError(
                f"Refusing to save hard-linked graph file: {target_path}"
            )

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
        backup_path = (
            backup_dir / f"{timestamp}-{old_hash[:16]}{target_path.suffix}"
        )
        if backup_path.exists():
            backup_path = backup_dir / (
                f"{timestamp}-{old_hash[:16]}-{time.time_ns()}{target_path.suffix}"
            )
        try:
            shutil.copy2(target_path, backup_path)
        except OSError as exc:
            raise OSError(
                f"Could not create save backup for {target_path}: {exc}"
            ) from exc
        return backup_path

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
                    logger.warning(
                        "atomic_save_temp_cleanup_failed path=%s", temp_path
                    )
            raise OSError(
                f"Failed to save flowgraph to {target_path}: {exc}"
            ) from exc

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
