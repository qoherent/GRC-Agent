"""Public preflight validation entry point for Phase 4 transactions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from grc_agent.flowgraph_session import FlowgraphSession

from .checks import SessionSnapshot, validate_and_apply_operation
from .errors import build_preflight_payload, make_issue
from .rules import normalize_operations


def preflight_transaction(
    session: FlowgraphSession,
    operations: Any,
    catalog_root: str | Path | None = None,
) -> dict[str, Any]:
    """Validate one ordered transaction without mutating the live session."""
    if session.flowgraph is None:
        return build_preflight_payload(
            errors=[
                make_issue(
                    op_index=0,
                    op_type="transaction",
                    field="session",
                    code="no_flowgraph_loaded",
                    message="No flowgraph loaded.",
                )
            ],
            warnings=[],
        )

    normalized_operations, normalization_errors = normalize_operations(operations)
    if normalization_errors:
        return build_preflight_payload(
            errors=normalization_errors,
            warnings=[],
        )

    snapshot = SessionSnapshot.from_session(session)
    warnings = []
    for op_index, operation in enumerate(normalized_operations):
        errors, op_warnings = validate_and_apply_operation(
            snapshot,
            operation,
            op_index=op_index,
            catalog_root=catalog_root,
        )
        warnings.extend(op_warnings)
        if errors:
            return build_preflight_payload(
                errors=errors,
                warnings=warnings,
                normalized_operations=[operation.to_dict() for operation in normalized_operations],
            )

    return build_preflight_payload(
        errors=[],
        warnings=warnings,
        normalized_operations=[operation.to_dict() for operation in normalized_operations],
    )
