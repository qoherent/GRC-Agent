"""Structured issue records and payload builders for preflight validation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


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
