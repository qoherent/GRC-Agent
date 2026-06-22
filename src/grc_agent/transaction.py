"""Phase 5 transaction package consolidated into one module."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from grc_agent._payload import ErrorCode
from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.session import validate_raw_flowgraph
from grc_agent.validation import preflight_transaction

__all__ = ["apply_edit", "propose_edit"]


@dataclass(frozen=True)
class AffectedChanges:
    """The blocks and connections touched by one transaction."""

    blocks: tuple[str, ...]
    connections: tuple[tuple, ...]

    def to_payload(self) -> dict[str, Any]:
        return {
            "affected_blocks": list(self.blocks),
            "affected_connections": [
                {
                    "src_block": src_block,
                    "src_port": src_port,
                    "dst_block": dst_block,
                    "dst_port": dst_port,
                }
                for src_block, src_port, dst_block, dst_port in self.connections
            ],
        }


def _resolve_connection_id(session: FlowgraphSession, connection_id: str) -> tuple | None:
    """Resolve a connection_id string (src:sport->dst:dport) to endpoint tuple.

    Also supports legacy shorthand if ports are unambiguous.
    """
    parts = connection_id.split("->")
    if len(parts) != 2:
        return None
    src_part = parts[0]
    dst_part = parts[1]
    try:
        src_block, src_port = src_part.rsplit(":", 1)
        dst_block, dst_port = dst_part.rsplit(":", 1)
        return (src_block, int(src_port), dst_block, int(dst_port))
    except (ValueError, IndexError):
        return None


def apply_operations(
    session: FlowgraphSession,
    operations: list[dict[str, Any]],
) -> AffectedChanges:
    """Apply one normalized ordered transaction to a candidate session.

    This path trusts Phase 4 preflight validation to reject unsupported
    port/domain/dtype/occupancy combinations before apply time. It only applies
    the normalized operation list to the candidate `FlowgraphSession`.
    """
    integrity = session.file_integrity_state()
    if integrity.get("externally_modified"):
        raise OSError(
            f"Refusing to apply: file changed on disk at "
            f"{integrity.get('path')}"
        )
    affected_blocks: set[str] = set()
    affected_connections: set[tuple] = set()

    for operation in operations:
        op_type = operation["op_type"]
        block_type = operation.get("block_type")
        if op_type == "update_params":
            instance_name = operation["instance_name"]
            affected_blocks.add(instance_name)
            for parameter_key, value in operation["params"].items():
                session.set_param(
                    instance_name,
                    parameter_key,
                    copy.deepcopy(value),
                    block_type=block_type,
                )
            continue

        if op_type == "update_states":
            instance_name = operation["instance_name"]
            session.set_block_state(instance_name, operation["state"], block_type=block_type)
            affected_blocks.add(instance_name)
            continue

        if op_type == "add_connection":
            connection = (
                operation["src_block"],
                operation["src_port"],
                operation["dst_block"],
                operation["dst_port"],
            )
            session.connect(*connection)
            affected_blocks.update((connection[0], connection[2]))
            affected_connections.add(connection)
            continue

        if op_type == "remove_connection":
            connection = (
                operation["src_block"],
                operation["src_port"],
                operation["dst_block"],
                operation["dst_port"],
            )
            session.disconnect(*connection)
            affected_blocks.update((connection[0], connection[2]))
            affected_connections.add(connection)
            continue

        if op_type == "remove_block":
            instance_name = operation["instance_name"]
            session.remove_block(instance_name, block_type=block_type)
            affected_blocks.add(instance_name)
            continue

        if op_type == "add_block":
            instance_name = operation["instance_name"]
            session.add_block(
                instance_name,
                operation["block_type"],
                copy.deepcopy(operation["parameters"]),
                copy.deepcopy(operation.get("states")),
                _skip_grcc=True,
            )
            affected_blocks.add(instance_name)
            continue

        if op_type == "insert_block_on_connection":
            conn_id = operation["connection_id"]
            resolved = _resolve_connection_id(session, conn_id)
            if resolved is None:
                raise ValueError(f"insert_block_on_connection: could not resolve connection_id '{conn_id}'")
            src_block, src_port, dst_block, dst_port = resolved
            instance_name = operation["instance_name"]
            session.add_block(
                instance_name,
                operation["block_type"],
                copy.deepcopy(operation["parameters"]),
                copy.deepcopy(operation.get("states")),
                _skip_grcc=True,
            )
            affected_blocks.add(instance_name)
            session.disconnect(src_block, src_port, dst_block, dst_port)
            affected_connections.add((src_block, src_port, dst_block, dst_port))
            session.connect(src_block, src_port, instance_name, 0)
            affected_connections.add((src_block, src_port, instance_name, 0))
            session.connect(instance_name, 0, dst_block, dst_port)
            affected_connections.add((instance_name, 0, dst_block, dst_port))
            affected_blocks.update((src_block, dst_block))
            continue

        raise ValueError(f"Unsupported transaction op_type: {op_type}")

    return AffectedChanges(
        blocks=tuple(sorted(affected_blocks)),
        connections=tuple(sorted(affected_connections)),
    )


# -- rollback --

@dataclass(frozen=True)
class SessionStateSnapshot:
    raw_data: dict[str, Any] | None
    path: Any
    is_dirty: bool
    state_revision: int
    persisted_file_sha256: str | None


def capture_session_state(session: FlowgraphSession) -> SessionStateSnapshot:
    raw_data = session.flowgraph.export_data() if session.flowgraph is not None else None
    return SessionStateSnapshot(
        raw_data=dict(raw_data) if raw_data is not None else None,
        path=session.path,
        is_dirty=session.is_dirty,
        state_revision=session.state_revision,
        persisted_file_sha256=session._persisted_file_sha256,
    )


def restore_session_state(
    session: FlowgraphSession,
    snapshot: SessionStateSnapshot,
) -> FlowgraphSession:
    session.path = snapshot.path
    session.is_dirty = snapshot.is_dirty
    session._state_revision = snapshot.state_revision
    session._persisted_file_sha256 = snapshot.persisted_file_sha256
    if snapshot.raw_data is not None:
        from grc_agent.grc_native_adapter import get_platform
        fg = get_platform().make_flow_graph()
        fg.import_data(snapshot.raw_data)
        fg.rewrite()
        session.flowgraph = fg
    else:
        session.flowgraph = None
    return session


def clone_session(session: FlowgraphSession) -> FlowgraphSession:
    clone = FlowgraphSession()
    return restore_session_state(clone, capture_session_state(session))


def commit_candidate_session(
    session: FlowgraphSession,
    candidate: FlowgraphSession,
) -> FlowgraphSession:
    return restore_session_state(session, capture_session_state(candidate))


# -- commit --

def _validation_error_summary(validation: dict[str, Any]) -> str:
    message = validation.get("message")
    if isinstance(message, str) and message.strip():
        return message.strip()
    _MAX_VALIDATION_OUTPUT_CHARS = 300
    stderr = validation.get("stderr")
    if isinstance(stderr, str) and stderr.strip():
        compact = " ".join(stderr.strip().split())
        if len(compact) > _MAX_VALIDATION_OUTPUT_CHARS:
            from grc_agent.runtime.text_utils import format_truncation_flag
            return (
                compact[:_MAX_VALIDATION_OUTPUT_CHARS]
                + format_truncation_flag("stderr", len(compact), _MAX_VALIDATION_OUTPUT_CHARS)
            )
        return compact
    stdout = validation.get("stdout")
    if isinstance(stdout, str) and stdout.strip():
        compact = " ".join(stdout.strip().split())
        if len(compact) > _MAX_VALIDATION_OUTPUT_CHARS:
            from grc_agent.runtime.text_utils import format_truncation_flag
            return (
                compact[:_MAX_VALIDATION_OUTPUT_CHARS]
                + format_truncation_flag("stdout", len(compact), _MAX_VALIDATION_OUTPUT_CHARS)
            )
        return compact
    status = validation.get("status")
    return str(status or "unknown validation failure")


def build_apply_success_payload(
    *,
    session: FlowgraphSession,
    normalized_operations: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
    affected_changes: AffectedChanges,
    state_revision_before: int,
    forced_validation_failure: bool = False,
    validation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    validation_state = validation if validation is not None else session.validation_state()
    payload: dict[str, Any] = {
        "ok": True,
        "message": (
            "Change committed with validation warnings: "
            + _validation_error_summary(validation_state)
            if forced_validation_failure
            else "Applied transaction and validated the graph successfully."
        ),
        "applied": True,
        "dirty": session.is_dirty,
        "commit_eligible": True,
        "path": str(session.path) if session.path is not None else None,
        "graph_id": session.graph_id(),
        "validation": validation_state,
        "normalized_operations": copy.deepcopy(normalized_operations),
        "warnings": copy.deepcopy(warnings),
        "warning_count": len(warnings),
        "errors": [],
        "error_count": 0,
        "state_revision_before": state_revision_before,
        "state_revision_after": session.state_revision,
        "validation_ok": not forced_validation_failure,
    }
    if forced_validation_failure:
        payload["forced_validation_failure"] = True
    payload.update(affected_changes.to_payload())
    return payload


def build_apply_failure_payload(
    *,
    session: FlowgraphSession,
    message: str,
    normalized_operations: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
    state_revision_before: int,
    error_type: str | None = None,
    errors: list[dict[str, Any]] | None = None,
    validation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": False,
        "message": message,
        "applied": False,
        "dirty": session.is_dirty,
        "commit_eligible": False,
        "path": str(session.path) if session.path is not None else None,
        "normalized_operations": copy.deepcopy(normalized_operations),
        "warnings": copy.deepcopy(warnings),
        "warning_count": len(warnings),
        "errors": copy.deepcopy(errors) if errors is not None else [],
        "error_count": len(errors) if errors is not None else 0,
        "state_revision_before": state_revision_before,
        "state_revision_after": session.state_revision,
    }
    if error_type is not None:
        payload["error_type"] = error_type
    if validation is not None:
        payload["validation"] = validation
    return payload


# -- planner --

def propose_edit(
    session: FlowgraphSession,
    transaction: Any,
    catalog_root: str | Path | None = None,
) -> dict[str, Any]:
    preflight = preflight_transaction(session, transaction, catalog_root)
    normalized_operations = preflight.get("normalized_operations", [])
    return {
        "ok": preflight["ok"],
        "message": (
            "Transaction passed preflight validation."
            if preflight["ok"]
            else "Transaction failed preflight validation."
        ),
        "commit_eligible": False,
        "planned_operations": copy.deepcopy(normalized_operations),
        "normalized_operations": copy.deepcopy(normalized_operations),
        "errors": copy.deepcopy(preflight["errors"]),
        "warnings": copy.deepcopy(preflight["warnings"]),
        "error_count": preflight["error_count"],
        "warning_count": preflight["warning_count"],
        "dirty": session.is_dirty,
        "state_revision": session.state_revision,
    }


# -- apply --

def apply_edit(
    session: FlowgraphSession,
    transaction: Any,
    catalog_root: str | Path | None = None,
    *,
    force_validation: bool = False,
) -> dict[str, Any]:
    """Apply one transaction atomically after Phase 4 preflight passes."""
    state_revision_before = session.state_revision
    proposal = propose_edit(session, transaction, catalog_root)
    normalized_operations = proposal["normalized_operations"]
    warnings = proposal["warnings"]

    if not proposal["ok"]:
        return build_apply_failure_payload(
            session=session,
            message="Transaction failed preflight validation.",
            normalized_operations=normalized_operations,
            warnings=warnings,
            errors=proposal["errors"],
            state_revision_before=state_revision_before,
            error_type=ErrorCode.PREFLIGHT_REJECTED,
        )

    candidate = clone_session(session)
    try:
        affected_changes = apply_operations(candidate, normalized_operations)
        if candidate.flowgraph is not None and session.flowgraph is not None:
            if (candidate.graph_id() == session.graph_id()
                    and candidate.path == session.path
                    and candidate.state_revision == session.state_revision):
                candidate.is_dirty = session.is_dirty

        from grc_agent.grc_native_adapter import validate as adapter_validate
        native_validation = (
            {"ok": True, "valid": adapter_validate(candidate.flowgraph).native_ok, "errors": []}
            if candidate.flowgraph is not None
            else {"ok": False, "available": False, "valid": None, "errors": []}
        )
        if native_validation.get("valid") is False and not force_validation:
            return build_apply_failure_payload(
                session=session,
                message="Candidate graph failed native GNU validation.",
                normalized_operations=normalized_operations,
                warnings=warnings,
                validation={
                    "status": "invalid",
                    "returncode": None,
                    "state_revision": None,
                    "native": native_validation,
                },
                state_revision_before=state_revision_before,
                error_type=ErrorCode.GNU_VALIDATION_FAILED,
            )

        candidate_valid = candidate.validate()
        final_validation = candidate.validation_state()
        if native_validation.get("valid") is False:
            final_validation = {
                **final_validation,
                "native": native_validation,
            }
        if not candidate_valid and not force_validation:
            error_type = (
                ErrorCode.VALIDATION_TIMEOUT
                if candidate.last_validation_returncode == -2
                else ErrorCode.GNU_VALIDATION_FAILED
            )
            return build_apply_failure_payload(
                session=session,
                message="Candidate graph failed GNU validation.",
                normalized_operations=normalized_operations,
                warnings=warnings,
                validation=candidate.validation_state(),
                state_revision_before=state_revision_before,
                error_type=error_type,
            )
        forced_validation_failure = False
        if candidate.last_validation_ok is not True or native_validation.get("valid") is False:
            forced_validation_failure = bool(force_validation)
    except Exception as exc:
        return build_apply_failure_payload(
            session=session,
            message=str(exc),
            normalized_operations=normalized_operations,
            warnings=warnings,
            state_revision_before=state_revision_before,
            error_type=ErrorCode.INTERNAL_ERROR,
        )

    try:
        commit_candidate_session(session, candidate)
    except Exception as exc:
        return build_apply_failure_payload(
            session=session,
            message=str(exc),
            normalized_operations=normalized_operations,
            warnings=warnings,
            state_revision_before=state_revision_before,
            error_type=ErrorCode.INTERNAL_ERROR,
        )
    return build_apply_success_payload(
        session=session,
        normalized_operations=normalized_operations,
        warnings=warnings,
        affected_changes=affected_changes,
        state_revision_before=state_revision_before,
        forced_validation_failure=forced_validation_failure,
        validation=final_validation if forced_validation_failure else None,
    )
