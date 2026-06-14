"""Shared lightweight payload helpers used across the package."""

from dataclasses import dataclass, field
from typing import Any


class ErrorCode:
    MISSING_SESSION = "missing_session"
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
    UNSUPPORTED_OP = "unsupported_op"
    CATALOG_LOAD_ERROR = "catalog_load_error"
    INTERNAL_ERROR = "internal_error"
    SAFETY_CEILING = "safety_ceiling_reached"
    TOOL_NOT_ALLOWED_FOR_SURFACE = "tool_not_allowed_for_surface"
    LLAMA_SERVER_MISSING = "llama_server_missing"
    GRCC_MISSING = "grcc_missing"
    MODEL_NOT_FOUND = "model_not_found"
    INIT_FAILED = "init_failed"
    BACKEND_UNREACHABLE = "backend_unreachable"


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


@dataclass
class Block:
    """Represents one GNU Radio block instance."""

    instance_name: str
    block_type: str
    params: dict[str, Any] = field(default_factory=dict)
    block_uid: str = ""


@dataclass
class Connection:
    """Represents one wire between two block ports.

    Stream connections use integer port indices.  Message connections
    use string port names (e.g. ``"strobe"``, ``"pdus"``).
    """

    src_block: str
    src_port: int | str
    dst_block: str
    dst_port: int | str


@dataclass
class Flowgraph:
    """In-memory model of a full flowgraph."""

    blocks: list[Block] = field(default_factory=list)
    connections: list[Connection] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    raw_data: dict[str, Any] = field(default_factory=dict)
