"""Phase 6 — minimal payload utilities. Legacy Block/Connection/Flowgraph types
replaced with thin stubs while the remaining callers are cut over to the adapter."""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
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
    SAFETY_CEILING = "safety_ceached_reached"
    TOOL_NOT_ALLOWED_FOR_SURFACE = "tool_not_allowed_for_surface"
    LLAMA_SERVER_MISSING = "llama_server_missing"
    GRCC_MISSING = "grcc_missing"
    MODEL_NOT_FOUND = "model_not_found"
    INIT_FAILED = "init_failed"
    BACKEND_UNREACHABLE = "backend_unreachable"


def join_non_empty(*parts: Any, separator: str = " ") -> str:
    return separator.join(str(p) for p in parts if p)


def build_error_payload(*, error_type: str, message: str,
                        details: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": False,
        "error_type": error_type,
        "message": message,
        "errors": [{"code": error_type, "message": message}],
    }
    if details:
        payload["details"] = details
    return payload


# Thin stubs — the remaining consumers that still reference these types
# (block_semantics, old inspect/change) are being cut over to the adapter.
@dataclass
class Block:
    instance_name: str
    block_type: str
    block_uid: str = ""
    params: dict[str, Any] = field(default_factory=dict)

@dataclass
class Connection:
    src_block: str = ""
    src_port: Any = ""
    dst_block: str = ""
    dst_port: Any = ""
    instance_name: str = ""
    block_type: str = ""
    block_uid: str = ""

@dataclass
class Flowgraph:
    blocks: list[Block] = field(default_factory=list)
    connections: list[Connection] = field(default_factory=list)
    ok: bool = False
    errors: list[dict[str, str]] = field(default_factory=list)
