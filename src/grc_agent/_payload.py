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
    LLAMA_SERVER_MISSING = "llama_server_missing"
    GRCC_MISSING = "grcc_missing"
    MODEL_NOT_FOUND = "model_not_found"
    INIT_FAILED = "init_failed"


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
