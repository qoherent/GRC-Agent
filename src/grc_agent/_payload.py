"""Shared lightweight payload helpers used across the package."""

from typing import Any


class ErrorCode:
    MISSING_SESSION = "missing_session"
    MISSING_BLOCK_TYPE = "missing_block_type"
    FILE_LOAD_ERROR = "file_load_error"
    INVALID_GRC = "invalid_grc"
    VALIDATION_ERROR = "validation_error"
    VALIDATION_TIMEOUT = "validation_timeout"
    PREFLIGHT_REJECTED = "preflight_rejected"
    GNU_VALIDATION_FAILED = "gnu_validation_failed"
    TOOL_CALL_INVALID = "tool_call_invalid"
    UNKNOWN_TOOL = "unknown_tool"
    INVALID_REQUEST = "invalid_request"
    STALE_REVISION = "stale_revision"
    RETRIEVAL_NOT_READY = "retrieval_not_ready"
    SAVE_REFUSED = "save_refused"
    BLOCK_NOT_FOUND = "block_not_found"
    CONNECTION_NOT_FOUND = "connection_not_found"
    AMBIGUOUS_CONNECTION = "ambiguous_connection"
    CONNECTION_ENDPOINT_MISMATCH = "connection_endpoint_mismatch"
    BLOCK_ALREADY_EXISTS = "block_already_exists"
    CONNECTION_ALREADY_EXISTS = "connection_already_exists"
    UNSUPPORTED_OP = "unsupported_op"
    CATALOG_LOAD_ERROR = "catalog_load_error"
    INTERNAL_ERROR = "internal_error"
    SAFETY_CEILING = "safety_ceiling_reached"
    TOOL_NOT_ALLOWED_FOR_SURFACE = "tool_not_allowed_for_surface"


def build_error_payload(
    *,
    error_type: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a stable structured error payload for public entry points."""
    payload: dict[str, Any] = {
        "ok": False,
        "error_type": error_type,
        "message": message,
    }
    if details:
        payload["details"] = details
    return payload


def join_non_empty(*parts: str) -> str:
    """Join non-empty string parts with single spaces."""
    return " ".join(part for part in parts if part).strip()


def audit_change_graph_result_shape(payload: dict[str, Any]) -> list[str]:
    """Return non-fatal contract findings for change_graph wrapper outputs."""
    findings: list[str] = []
    if not isinstance(payload, dict):
        return ["payload_not_object"]
    if payload.get("tool") != "change_graph":
        findings.append("tool_missing_or_not_change_graph")
    ok_value = payload.get("ok")
    if not isinstance(ok_value, bool):
        findings.append("ok_missing_or_not_bool")
    dry_run_value = payload.get("dry_run")
    if not isinstance(dry_run_value, bool):
        findings.append("dry_run_missing_or_not_bool")
    if "operation_kind" not in payload:
        findings.append("operation_kind_missing")
    committed = payload.get("committed")
    if committed is not None and not isinstance(committed, bool):
        findings.append("committed_not_bool")
    if isinstance(dry_run_value, bool) and dry_run_value and committed is True:
        findings.append("preview_marked_committed")
    if ok_value is False and committed is True:
        findings.append("refusal_marked_committed")

    graph_delta = payload.get("graph_delta")
    success_like_delta = False
    if isinstance(graph_delta, dict):
        success_like_delta = any(
            bool(graph_delta.get(key))
            for key in (
                "added_blocks",
                "removed_blocks",
                "added_connections",
                "removed_connections",
            )
        )
    if ok_value is False and success_like_delta:
        findings.append("failed_or_refused_contains_success_like_graph_delta")
    if ok_value is True and dry_run_value is False and graph_delta is None:
        findings.append("commit_success_missing_graph_delta")
    return findings
