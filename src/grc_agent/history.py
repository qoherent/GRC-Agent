"""Local graph checkpoint and edit-journal storage.

SQLite-backed history with atomic transactions and lineage-scoped retention.
Restore always writes to a caller-provided copy path.
"""

from __future__ import annotations

import copy
import difflib
import hashlib
import json
import logging
import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.runtime.connection_ids import connection_id as render_connection_id

logger = logging.getLogger(__name__)

MAX_ACCEPTED_VERSIONS_PER_LINEAGE = 100
HISTORY_ENV_VAR = "GRC_AGENT_HISTORY_PATH"
_SQLITE_HEADER = b"SQLite format 3\x00"


def default_history_path() -> Path:
    override = os.environ.get(HISTORY_ENV_VAR)
    if override:
        return Path(override).expanduser()
    return Path(".grc_agent") / "history" / "journal.db"


@dataclass(frozen=True)
class GraphSnapshot:
    """Serialized graph state plus compact indexes for delta computation."""

    raw_data: dict[str, Any]
    graph_hash: str
    blocks_by_uid: dict[str, dict[str, Any]]
    connections: list[str]


def snapshot_session(session: FlowgraphSession) -> GraphSnapshot:
    """Capture one loaded session in a JSON-serializable snapshot."""
    if session.flowgraph is None:
        raise ValueError("No flowgraph loaded.")
    fg = session.flowgraph
    raw_data = fg.export_data()
    # Non-recursive identity: persisted file SHA-256 + in-session revision.
    # Replaces the former deep dict→YAML→SHA-256 hash (DoD #7).
    graph_hash = f"{session.graph_id() or 'unknown'}:r{session.state_revision}"
    blocks_by_uid: dict[str, dict[str, Any]] = {}
    for block in fg.blocks:
        uid = str(getattr(block, "name", "") or block.key)
        blocks_by_uid[uid] = {
            "block_uid": uid,
            "instance_name": str(block.name or block.key),
            "block_type": str(block.key),
            "params": {k: str(p.value) for k, p in block.params.items()},
            "state": dict(getattr(block, "states", {}) or {}),
        }
    connections = sorted(
        render_connection_id(
            conn.source_block.name or conn.source_block.key,
            conn.source_port.key,
            conn.sink_block.name or conn.sink_block.key,
            conn.sink_port.key,
        )
        for conn in fg.connections
    )
    return GraphSnapshot(
        raw_data=dict(raw_data),
        graph_hash=graph_hash,
        blocks_by_uid=blocks_by_uid,
        connections=connections,
    )


def graph_delta(before: GraphSnapshot | None, after: GraphSnapshot) -> dict[str, Any]:
    """Return an exact UID/connection delta between two snapshots."""
    if before is None:
        return {
            "changed": True,
            "baseline": True,
            "added_blocks": sorted(after.blocks_by_uid),
            "removed_blocks": [],
            "changed_blocks": [],
            "added_connections": after.connections,
            "removed_connections": [],
        }

    before_uids = set(before.blocks_by_uid)
    after_uids = set(after.blocks_by_uid)
    changed_blocks: list[dict[str, Any]] = []
    for block_uid in sorted(before_uids & after_uids):
        old = before.blocks_by_uid[block_uid]
        new = after.blocks_by_uid[block_uid]
        param_changes = _dict_delta(old.get("params", {}), new.get("params", {}))
        state_changes = _dict_delta(old.get("state", {}), new.get("state", {}))
        identity_changes = {
            key: {"before": old.get(key), "after": new.get(key)}
            for key in ("instance_name", "block_type")
            if old.get(key) != new.get(key)
        }
        if param_changes or state_changes or identity_changes:
            changed_blocks.append(
                {
                    "block_uid": block_uid,
                    "instance_name": new.get("instance_name"),
                    "block_type": new.get("block_type"),
                    "identity_changes": identity_changes,
                    "param_changes": param_changes,
                    "state_changes": state_changes,
                }
            )

    added_connections = sorted(set(after.connections) - set(before.connections))
    removed_connections = sorted(set(before.connections) - set(after.connections))
    return {
        "changed": (
            before.graph_hash != after.graph_hash
            or bool(added_connections)
            or bool(removed_connections)
            or bool(changed_blocks)
            or before_uids != after_uids
        ),
        "baseline": False,
        "added_blocks": sorted(after_uids - before_uids),
        "removed_blocks": sorted(before_uids - after_uids),
        "changed_blocks": changed_blocks,
        "added_connections": added_connections,
        "removed_connections": removed_connections,
    }


def snapshot_to_payload(snapshot: GraphSnapshot) -> dict[str, Any]:
    return {
        "raw_data": snapshot.raw_data,
        "graph_hash": snapshot.graph_hash,
        "block_count": len(snapshot.blocks_by_uid),
        "connection_count": len(snapshot.connections),
        "block_uids": sorted(snapshot.blocks_by_uid),
        "connections": list(snapshot.connections),
    }


def _dict_delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    changes: dict[str, Any] = {}
    for key in sorted(set(before) | set(after)):
        if before.get(key) != after.get(key):
            changes[key] = {
                "before": copy.deepcopy(before.get(key)),
                "after": copy.deepcopy(after.get(key)),
            }
    return changes


class GraphHistoryJournal:
    """SQLite-backed graph checkpoint history with lineage-scoped retention."""

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        accepted_retention: int = MAX_ACCEPTED_VERSIONS_PER_LINEAGE,
    ) -> None:
        self.path = Path(path) if path is not None else default_history_path()
        self.accepted_retention = max(1, int(accepted_retention))
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init_db(self) -> None:
        if self.path.exists():
            with open(self.path, "rb") as handle:
                header = handle.read(16)
            if header[:16] != _SQLITE_HEADER:
                raise ValueError(
                    f"Legacy history format detected at {self.path}. "
                    "History journal storage has migrated to SQLite. "
                    "Remove the old file and re-record checkpoints."
                )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS history_records (
                    id TEXT PRIMARY KEY,
                    lineage_key TEXT NOT NULL,
                    record_type TEXT NOT NULL,
                    accepted INTEGER NOT NULL,
                    timestamp TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_history_lineage
                    ON history_records(lineage_key, accepted, timestamp);
                CREATE INDEX IF NOT EXISTS idx_history_ts
                    ON history_records(timestamp);
                """
            )
            conn.commit()

    def record_checkpoint(
        self,
        *,
        lineage_key: str,
        session: FlowgraphSession,
        before: GraphSnapshot | None,
        request_text: str,
        tool_name: str,
        operation_type: str,
        validation_result: dict[str, Any] | None = None,
        save_path: str | None = None,
    ) -> dict[str, Any]:
        after = snapshot_session(session)
        record = self._base_record(
            record_type="checkpoint",
            accepted=True,
            lineage_key=lineage_key,
            session=session,
            request_text=request_text,
            tool_name=tool_name,
            operation_type=operation_type,
            before_hash=before.graph_hash if before is not None else None,
            after_hash=after.graph_hash,
            validation_result=validation_result or session.validation_state(),
            save_path=save_path,
        )
        record["graph_delta"] = graph_delta(before, after)
        record["graph_snapshot"] = snapshot_to_payload(after)
        self._insert(record)
        self._prune_accepted_versions(lineage_key)
        return record

    def record_failure(
        self,
        *,
        lineage_key: str,
        session: FlowgraphSession,
        before: GraphSnapshot | None,
        request_text: str,
        tool_name: str,
        operation_type: str,
        result: dict[str, Any],
    ) -> dict[str, Any] | None:
        if session.flowgraph is None:
            return None
        current = snapshot_session(session)
        record = self._base_record(
            record_type="failure",
            accepted=False,
            lineage_key=lineage_key,
            session=session,
            request_text=request_text,
            tool_name=tool_name,
            operation_type=operation_type,
            before_hash=before.graph_hash if before is not None else current.graph_hash,
            after_hash=current.graph_hash,
            validation_result=_result_validation(result) or session.validation_state(),
            save_path=None,
        )
        record["error_type"] = result.get("error_type")
        record["message"] = result.get("message", "")
        record["graph_delta"] = graph_delta(before, current) if before is not None else {}
        record["graph_snapshot"] = snapshot_to_payload(current)
        self._insert(record)
        return record

    def list_records(self, *, accepted_only: bool = False) -> list[dict[str, Any]]:
        with self._conn() as conn:
            if accepted_only:
                rows = conn.execute(
                    "SELECT payload FROM history_records WHERE accepted=1 ORDER BY timestamp"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT payload FROM history_records ORDER BY timestamp"
                ).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def get_record(self, record_id: str) -> dict[str, Any]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT payload FROM history_records WHERE id=?", (record_id,)
            ).fetchone()
        if row is None:
            raise KeyError(record_id)
        return json.loads(row["payload"])

    def diff_records(self, first_id: str, second_id: str) -> dict[str, Any]:
        first = self.get_record(first_id)
        second = self.get_record(second_id)
        first_snapshot = _snapshot_from_record(first)
        second_snapshot = _snapshot_from_record(second)
        return {
            "ok": True,
            "from": first_id,
            "to": second_id,
            "before_hash": first_snapshot.graph_hash,
            "after_hash": second_snapshot.graph_hash,
            "graph_delta": graph_delta(first_snapshot, second_snapshot),
            "text_diff": list(
                difflib.unified_diff(
                    FlowgraphSession._serialize_raw_data(first_snapshot.raw_data).splitlines(),
                    FlowgraphSession._serialize_raw_data(second_snapshot.raw_data).splitlines(),
                    fromfile=first_id,
                    tofile=second_id,
                    lineterm="",
                )
            ),
        }

    def restore_record(self, record_id: str, to_path: str | Path) -> dict[str, Any]:
        record = self.get_record(record_id)
        target = Path(to_path)
        if target.exists():
            return {
                "ok": False,
                "error_type": "restore_target_exists",
                "message": f"Refusing to overwrite existing restore target: {target}",
                "path": str(target),
            }
        snapshot = _snapshot_from_record(record)
        session = FlowgraphSession.from_raw_data(snapshot.raw_data, path=target)
        session.save(target, validate=False)
        valid = session.validate()
        validation = session.validation_state()
        return {
            "ok": True,
            "id": record_id,
            "path": str(target),
            "graph_hash": snapshot.graph_hash,
            "validation": validation,
            "valid": valid,
        }

    def _base_record(
        self,
        *,
        record_type: str,
        accepted: bool,
        lineage_key: str,
        session: FlowgraphSession,
        request_text: str,
        tool_name: str,
        operation_type: str,
        before_hash: str | None,
        after_hash: str,
        validation_result: dict[str, Any],
        save_path: str | None,
    ) -> dict[str, Any]:
        return {
            "id": _record_id(),
            "record_type": record_type,
            "accepted": accepted,
            "lineage_key": lineage_key,
            "timestamp": datetime.now(UTC).isoformat(),
            "request_text": request_text,
            "tool_name": tool_name,
            "operation_type": operation_type,
            "before_hash": before_hash,
            "after_hash": after_hash,
            "validation_result": copy.deepcopy(validation_result),
            "state_revision": session.state_revision,
            "graph_path": str(session.path) if session.path is not None else None,
            "save_path": save_path,
        }

    def _insert(self, record: dict[str, Any]) -> None:
        payload = json.dumps(record, sort_keys=True)
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO history_records "
                "(id, lineage_key, record_type, accepted, timestamp, payload) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    record["id"],
                    record["lineage_key"],
                    record["record_type"],
                    int(record["accepted"]),
                    record["timestamp"],
                    payload,
                ),
            )
            conn.commit()

    def _prune_accepted_versions(self, lineage_key: str) -> None:
        with self._conn() as conn:
            excess_ids = [
                row["id"]
                for row in conn.execute(
                    "SELECT id FROM history_records "
                    "WHERE lineage_key=? AND accepted=1 AND record_type='checkpoint' "
                    "ORDER BY timestamp",
                    (lineage_key,),
                ).fetchall()
            ]
            excess = len(excess_ids) - self.accepted_retention
            if excess <= 0:
                return
            drop_ids = excess_ids[:excess]
            placeholders = ",".join("?" * len(drop_ids))
            conn.execute(
                f"DELETE FROM history_records WHERE id IN ({placeholders})",
                drop_ids,
            )
            conn.commit()


def _snapshot_from_record(record: dict[str, Any]) -> GraphSnapshot:
    payload = record.get("graph_snapshot")
    if not isinstance(payload, dict):
        raise ValueError(f"History record {record.get('id')} has no graph snapshot.")
    raw_data = payload.get("raw_data")
    if not isinstance(raw_data, dict):
        raise ValueError(f"History record {record.get('id')} has invalid raw_data.")
    session = FlowgraphSession.from_raw_data(raw_data)
    return snapshot_session(session)


def _record_id() -> str:
    now = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    return f"hist_{now}_{uuid.uuid4().hex[:8]}"


def lineage_key_for_session(session: FlowgraphSession) -> str:
    if session.flowgraph is None:
        return "unloaded"
    path = str(session.path) if session.path is not None else "<memory>"
    # Non-recursive identity: persisted file SHA-256 + in-session revision.
    graph_hash = f"{session.graph_id() or 'unknown'}:r{session.state_revision}"
    digest = hashlib.sha256(f"{path}\n{graph_hash}".encode()).hexdigest()[:16]
    return f"lineage:{digest}"


def operation_type_from_result(tool_name: str, result: dict[str, Any]) -> str:
    if tool_name == "save_graph":
        return "save_graph"
    operations = result.get("normalized_operations")
    if isinstance(operations, list) and operations:
        op_types = [
            str(operation.get("op_type"))
            for operation in operations
            if isinstance(operation, dict) and operation.get("op_type") is not None
        ]
        if op_types:
            return "+".join(op_types)
    return tool_name


def _result_validation(result: dict[str, Any]) -> dict[str, Any] | None:
    validation = result.get("validation")
    return copy.deepcopy(validation) if isinstance(validation, dict) else None
