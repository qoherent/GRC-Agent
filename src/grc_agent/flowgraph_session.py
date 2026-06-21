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
