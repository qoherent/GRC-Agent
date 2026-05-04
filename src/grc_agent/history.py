"""Local graph checkpoint and edit-journal storage.

The journal is intentionally CLI/runtime-local infrastructure. It does not add
model-facing tools and restore always writes to a caller-provided copy path.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime, timezone
import difflib
import hashlib
import json
import os
from pathlib import Path
from typing import Any
import uuid

from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.session_ops import connection_id as render_connection_id

MAX_ACCEPTED_VERSIONS_PER_LINEAGE = 100
HISTORY_ENV_VAR = "GRC_AGENT_HISTORY_PATH"


def default_history_path() -> Path:
    override = os.environ.get(HISTORY_ENV_VAR)
    if override:
        return Path(override).expanduser()
    return Path(".grc_agent") / "history" / "journal.jsonl"


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
    raw_data = copy.deepcopy(session.flowgraph.raw_data)
    serialized = FlowgraphSession._serialize_raw_data(raw_data)
    graph_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    blocks_by_uid: dict[str, dict[str, Any]] = {}
    for block in session.flowgraph.blocks:
        blocks_by_uid[block.block_uid] = {
            "block_uid": block.block_uid,
            "instance_name": block.instance_name,
            "block_type": block.block_type,
            "params": copy.deepcopy(block.params.get("parameters", {})),
            "state": copy.deepcopy(block.params.get("states", {})),
        }
    connections = sorted(
        render_connection_id(
            connection.src_block,
            connection.src_port,
            connection.dst_block,
            connection.dst_port,
        )
        for connection in session.flowgraph.connections
    )
    return GraphSnapshot(
        raw_data=raw_data,
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
        "raw_data": copy.deepcopy(snapshot.raw_data),
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
    """Append-only JSONL history with retention for accepted checkpoints."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else default_history_path()

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
        self._append(record)
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
        self._append(record)
        return record

    def list_records(self, *, accepted_only: bool = False) -> list[dict[str, Any]]:
        records = self._read_records()
        if accepted_only:
            records = [record for record in records if record.get("accepted") is True]
        return records

    def get_record(self, record_id: str) -> dict[str, Any]:
        for record in self._read_records():
            if record.get("id") == record_id:
                return record
        raise KeyError(record_id)

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
        session.save(target)
        reloaded = FlowgraphSession()
        reloaded.load(target)
        valid = reloaded.validate()
        validation = reloaded.validation_state()
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
            "timestamp": datetime.now(timezone.utc).isoformat(),
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

    def _append(self, record: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")

    def _read_records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        records: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict):
                    records.append(record)
        return records

    def _write_records(self, records: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, sort_keys=True) + "\n")

    def _prune_accepted_versions(self, lineage_key: str) -> None:
        records = self._read_records()
        accepted_for_lineage = [
            index
            for index, record in enumerate(records)
            if record.get("accepted") is True
            and record.get("record_type") == "checkpoint"
            and record.get("lineage_key") == lineage_key
        ]
        excess = len(accepted_for_lineage) - MAX_ACCEPTED_VERSIONS_PER_LINEAGE
        if excess <= 0:
            return
        drop_indexes = set(accepted_for_lineage[:excess])
        self._write_records([
            record for index, record in enumerate(records) if index not in drop_indexes
        ])


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
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"hist_{now}_{uuid.uuid4().hex[:8]}"


def lineage_key_for_session(session: FlowgraphSession) -> str:
    if session.flowgraph is None:
        return "unloaded"
    path = str(session.path) if session.path is not None else "<memory>"
    snapshot = snapshot_session(session)
    digest = hashlib.sha256(f"{path}\n{snapshot.graph_hash}".encode("utf-8")).hexdigest()[:16]
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
