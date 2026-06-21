"""Phase 6 — minimal payload utilities. Legacy Block/Connection/Flowgraph types
replaced with thin stubs while the remaining callers are cut over to the adapter."""
from __future__ import annotations
from dataclasses import dataclass, field
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
    instance_name: str = ""
    block_type: str = ""
    block_uid: str = ""

@dataclass
class Flowgraph:
    blocks: list[Block] = field(default_factory=list)
    connections: list[Connection] = field(default_factory=list)
    ok: bool = False
    errors: list[dict[str, str]] = field(default_factory=list)
