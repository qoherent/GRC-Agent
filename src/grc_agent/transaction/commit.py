"""Commit-result payload helpers for transaction apply flows."""

from __future__ import annotations

import copy
from typing import Any

from grc_agent.flowgraph_session import FlowgraphSession

from .edit import AffectedChanges


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
    """Build the public success payload for `apply_edit(...)`."""
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


def _validation_error_summary(validation: dict[str, Any]) -> str:
    message = validation.get("message")
    if isinstance(message, str) and message.strip():
        return message.strip()
    stderr = validation.get("stderr")
    if isinstance(stderr, str) and stderr.strip():
        return " ".join(stderr.strip().split())[:300]
    stdout = validation.get("stdout")
    if isinstance(stdout, str) and stdout.strip():
        return " ".join(stdout.strip().split())[:300]
    status = validation.get("status")
    return str(status or "unknown validation failure")


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
    """Build the public failure payload for `apply_edit(...)`."""
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
