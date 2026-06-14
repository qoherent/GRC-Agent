"""Structured issue records, message helpers, and payload builders for validation.

Consolidated from errors.py + messages.py + preflight.py.
"""

from __future__ import annotations

import copy
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from grc_agent.flowgraph_session import FlowgraphSession


@dataclass(frozen=True)
class ValidationIssue:
    """One stable blocking or non-blocking preflight issue."""

    op_index: int
    op_type: str
    field: str
    code: str
    message: str
    hint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "op_index": self.op_index,
            "op_type": self.op_type,
            "field": self.field,
            "code": self.code,
            "message": self.message,
        }
        if self.hint is not None:
            payload["hint"] = self.hint
        return payload


def make_issue(
    *,
    op_index: int,
    op_type: str,
    field: str,
    code: str,
    message: str,
    hint: str | None = None,
) -> ValidationIssue:
    """Create one stable issue record."""
    return ValidationIssue(
        op_index=op_index,
        op_type=op_type,
        field=field,
        code=code,
        message=message,
        hint=hint,
    )


def build_preflight_payload(
    *,
    errors: list[ValidationIssue],
    warnings: list[ValidationIssue],
    normalized_operations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the public Phase 4 preflight payload."""
    payload: dict[str, Any] = {
        "ok": not errors,
        "errors": [issue.to_dict() for issue in errors],
        "warnings": [issue.to_dict() for issue in warnings],
        "error_count": len(errors),
        "warning_count": len(warnings),
    }
    if normalized_operations is not None:
        payload["normalized_operations"] = normalized_operations
        payload["operation_count"] = len(normalized_operations)
    return payload


# -- messages --

def format_allowed_values(options: Iterable[object]) -> str:
    rendered = ", ".join(str(option) for option in options)
    return f"Valid values: {rendered}." if rendered else ""


def format_endpoint(block_name: str, port: int | str) -> str:
    return f"{block_name}({port})"


def format_port_range(port_count: int) -> str:
    if port_count <= 0:
        return "none"
    if port_count == 1:
        return "0"
    return f"0-{port_count - 1}"


def format_catalog_lookup_message(block_type: str) -> str:
    return f"Could not resolve GNU catalog metadata for block type '{block_type}'."


# -- preflight --

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


def preflight_transaction(
    session: FlowgraphSession,
    operations: Any,
    catalog_root: str | Path | None = None,
) -> dict[str, Any]:
    """Validate one ordered transaction without mutating the live session.

    All operations are validated before returning so the caller receives
    every detectable error rather than only the first.
    """
    from .checks import (
        SessionSnapshot,
        validate_and_apply_operation,
        validate_snapshot_integrity,
    )
    from .rules import normalize_operations

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
