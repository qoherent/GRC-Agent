"""Public preflight validation entry point for Phase 4 transactions."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from grc_agent.flowgraph_session import FlowgraphSession

from .checks import (
    SessionSnapshot,
    validate_and_apply_operation,
    validate_snapshot_integrity,
)
from .errors import build_preflight_payload, make_issue
from .rules import normalize_operations


def preflight_transaction(
    session: FlowgraphSession,
    operations: Any,
    catalog_root: str | Path | None = None,
) -> dict[str, Any]:
    """Validate one ordered transaction without mutating the live session.

    All operations are validated before returning so the caller receives
    every detectable error rather than only the first.
    """
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
    warnings: list[Any] = []
    affected_block_names: set[str] = set()
    all_errors: list[Any] = []
    has_errors = False

    for op_index, operation in enumerate(normalized_operations):
        target = copy.deepcopy(snapshot) if has_errors else snapshot
        errors, op_warnings = validate_and_apply_operation(
            target,
            operation,
            op_index=op_index,
            catalog_root=catalog_root,
        )
        warnings.extend(op_warnings)
        if errors:
            all_errors.extend(errors)
            has_errors = True
        else:
            if not has_errors:
                snapshot = target
                affected_block_names.update(
                    _affected_blocks_for_operation(operation)
                )

    if all_errors:
        return build_preflight_payload(
            errors=all_errors,
            warnings=warnings,
            normalized_operations=[op.to_dict() for op in normalized_operations],
        )

    integrity_errors, integrity_warnings = validate_snapshot_integrity(
        snapshot,
        affected_block_names=affected_block_names,
        op_index=max(0, len(normalized_operations) - 1),
        catalog_root=catalog_root,
    )
    warnings.extend(integrity_warnings)
    if integrity_errors:
        return build_preflight_payload(
            errors=integrity_errors,
            warnings=warnings,
            normalized_operations=[op.to_dict() for op in normalized_operations],
        )

    return build_preflight_payload(
        errors=[],
        warnings=warnings,
        normalized_operations=[op.to_dict() for op in normalized_operations],
    )


def _affected_blocks_for_operation(operation: Any) -> set[str]:
    payload = getattr(operation, "payload", None)
    if not isinstance(payload, dict):
        return set()
    if operation.op_type in {"update_params", "update_states", "remove_block", "add_block"}:
        instance_name = payload.get("instance_name")
        return {instance_name} if isinstance(instance_name, str) else set()
    if operation.op_type in {"add_connection", "remove_connection"}:
        names = {
            payload.get("src_block"),
            payload.get("dst_block"),
        }
        return {name for name in names if isinstance(name, str)}
    return set()
