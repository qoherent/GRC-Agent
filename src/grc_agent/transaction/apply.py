"""Atomic apply flow for preflight-approved transactions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from grc_agent._payload import ErrorCode
from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.session.gnu_loader import validate_raw_flowgraph

from .commit import build_apply_failure_payload, build_apply_success_payload
from .edit import apply_operations
from .planner import propose_edit
from .rollback import clone_session, commit_candidate_session


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
            if candidate.graph_id() == session.graph_id() and candidate.path == session.path:
                candidate.is_dirty = session.is_dirty

        native_validation = (
            validate_raw_flowgraph(candidate.flowgraph.raw_data)
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

        if not candidate.validate() and not force_validation:
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
    )
