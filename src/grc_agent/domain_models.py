"""Pydantic V2 domain models — the agent's inner wire-shape contract.

Per AGENTS.md (no in-band control flow): no field name or value here may carry
an ALL-CAPS directive, a "Use this when …" phrase, or a procedural recipe. The
model's system prompt is the only behavioral authority.

All models are outbound (``extra="forbid"``) — the wire shape the model
sees is locked. Inbound JSON-schema validation lives in
:mod:`grc_agent.runtime_tool_validation` (hand-rolled), not in Pydantic.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class BlockRole(StrEnum):
    """Role of a GRC block. Lowercase string values — no ALL-CAPS directives."""

    VARIABLE = "variable"
    SOURCE = "source"
    SINK = "sink"
    TRANSFORM = "transform"
    VIRTUAL_OR_PAD = "virtual_or_pad"
    IMPORT = "import"
    SNIPPET = "snippet"
    OPTIONS = "options"
    OTHER = "other"


class BlockState(StrEnum):
    """Wire-format block state values (mirrors GRC ``Block.STATE_LABELS``)."""

    ENABLED = "enabled"
    DISABLED = "disabled"
    BYPASS = "bypass"


class ValidationStatus(StrEnum):
    """Wire-format ``GrcValidation.status`` values."""

    VALID = "valid"
    INVALID = "invalid"
    UNKNOWN = "unknown"


# --------------------------------------------------------------------------- #
# Outbound models (state to the model). extra="forbid" locks the wire shape.  #
# --------------------------------------------------------------------------- #


class GrcBlock(BaseModel):
    """A single GRC block as seen by the model."""

    model_config = ConfigDict(extra="forbid")
    instance_name: str
    block_id: str
    role: BlockRole
    state: str
    params: dict[str, str] = Field(default_factory=dict)


class GrcValidation(BaseModel):
    """A GRC validation result as seen by the model."""

    model_config = ConfigDict(extra="forbid")
    status: str = ValidationStatus.UNKNOWN
    errors: list[str] = Field(default_factory=list)
    native_ok: bool | None = None


class GrcFlowgraph(BaseModel):
    """A GRC flowgraph snapshot as seen by the model."""

    model_config = ConfigDict(extra="forbid")
    ok: bool
    graph_name: str
    blocks: list[GrcBlock] = Field(default_factory=list)
    connections: list[str] = Field(default_factory=list)
    validation: GrcValidation = Field(default_factory=GrcValidation)
    errors: list[dict[str, str]] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Error infrastructure (moved from the former ``_payload.py``).                #
# Plain constants + builder — not Pydantic models.                            #
# --------------------------------------------------------------------------- #


class ErrorCode:
    """Canonical error-type strings emitted in ``error_type`` fields."""

    MISSING_SESSION = "missing_session"
    FILE_LOAD_ERROR = "file_load_error"
    INVALID_GRC = "invalid_grc"
    GNU_VALIDATION_FAILED = "gnu_validation_failed"
    TOOL_CALL_INVALID = "tool_call_invalid"
    UNKNOWN_TOOL = "unknown_tool"
    INVALID_REQUEST = "invalid_request"
    STALE_REVISION = "stale_revision"
    RETRIEVAL_NOT_READY = "retrieval_not_ready"
    BLOCK_NOT_FOUND = "block_not_found"
    CATALOG_LOAD_ERROR = "catalog_load_error"
    INTERNAL_ERROR = "internal_error"
    SAFETY_CEILING = "safety_ceiling_reached"
    TOOL_NOT_ALLOWED_FOR_SURFACE = "tool_not_allowed_for_surface"
    MODEL_NOT_FOUND = "model_not_found"
    BACKEND_UNREACHABLE = "backend_unreachable"
    EMPTY_MODEL_RESPONSE = "empty_model_response"
    NO_FINAL = "no_final"


class ToolValidationCode:
    """Canonical inner ``validation_errors[].code`` values.

    The outer ``error_type`` field uses :class:`ErrorCode`; the inner
    per-issue ``code`` field uses these constants. One uniform source of
    truth so a rename stays in lockstep across producers and tests.
    """

    UNKNOWN_TOOL = "unknown_tool"
    MISSING_REQUIRED = "missing_required"
    UNEXPECTED_ARGUMENT = "unexpected_argument"
    INVALID_ARGUMENTS = "invalid_arguments"
    INVALID_TYPE = "invalid_type"
    INVALID_ENUM = "invalid_enum"
    TOO_FEW_ITEMS = "too_few_items"
    TOO_MANY_ITEMS = "too_many_items"
    NO_TOOL_RAN = "no_tool_ran"


def build_error_payload(
    *,
    error_type: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": False,
        "error_type": error_type,
        "message": message,
        "errors": [{"code": error_type, "message": message}],
    }
    if details:
        payload["details"] = details
    return payload


def join_non_empty(*parts: Any, separator: str = " ") -> str:
    return separator.join(str(p) for p in parts if p)
