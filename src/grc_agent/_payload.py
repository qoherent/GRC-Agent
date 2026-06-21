"""Phase 6 — minimal payload utilities. Legacy Block/Connection/Flowgraph types removed."""
from __future__ import annotations
from enum import Enum
from typing import Any


class ErrorCode:
    BLOCK_NOT_FOUND = "block_not_found"
    INVALID_REQUEST = "invalid_request"
    RETRIEVAL_NOT_READY = "retrieval_not_ready"
    TOOL_CALL_INVALID = "tool_call_invalid"
    CATALOG_LOAD_ERROR = "catalog_load_error"


def join_non_empty(*parts: Any, separator: str = " ") -> str:
    return separator.join(str(p) for p in parts if p)


def build_error_payload(*, error_type: str, message: str,
                        details: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"ok": False, "errors": [{"code": error_type, "message": message}]}
    if details:
        payload["details"] = details
    return payload
