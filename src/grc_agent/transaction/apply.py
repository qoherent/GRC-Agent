"""Atomic apply flow for preflight-approved transactions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from grc_agent._payload import ErrorCode
from grc_agent.flowgraph_session import FlowgraphSession

from .commit import build_apply_failure_payload, build_apply_success_payload
from .edit import apply_operations
from .planner import propose_edit
from .rollback import clone_session, commit_candidate_session


def apply_edit(
    session: FlowgraphSession,
    transaction: Any,
    catalog_root: str | Path | None = None,
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

        if not candidate.validate():
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
    except Exception as exc:
        return build_apply_failure_payload(
            session=session,
            message=str(exc),
            normalized_operations=normalized_operations,
            warnings=warnings,
            state_revision_before=state_revision_before,
            error_type=ErrorCode.INTERNAL_ERROR,
        )

    commit_candidate_session(session, candidate)
    return build_apply_success_payload(
        session=session,
        normalized_operations=normalized_operations,
        warnings=warnings,
        affected_changes=affected_changes,
        state_revision_before=state_revision_before,
    )
