"""Pydantic V2 domain models — the agent's inner wire-shape contract.

Per AGENTS.md (no in-band control flow): no field name or value here may carry
an ALL-CAPS directive, a "Use this when …" phrase, or a procedural recipe. The
model's system prompt is the only behavioral authority.

Two directions, two configs:

- **Outbound** (state serialized to the model): ``extra="forbid"``. The wire
  shape is locked so the model's input is predictable across turns and models.
- **Inbound** (LLM tool-call arguments to the agent): ``extra="ignore"``. Extra
  hallucinated fields are silently dropped; the agent never hard-crashes on a
  harmless extra parameter. Type errors are still ``ValidationError``.

No native GRC imports here. These are pure data schemas; the native adapter
(``grc_native_adapter.py``, Phase 5) fills the outbound models.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class BlockRole(str, Enum):
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


# --------------------------------------------------------------------------- #
# Outbound models (state to the model). extra="forbid" locks the wire shape.  #
# --------------------------------------------------------------------------- #


class GrcParameter(BaseModel):
    """A single GRC block parameter as seen by the model."""

    model_config = ConfigDict(extra="forbid")
    name: str
    dtype: str
    value: Any
    evaluated_value: Any | None = None
    category: str = "General"
    hide: str = "none"


class GrcConnection(BaseModel):
    """A single GRC connection as seen by the model."""

    model_config = ConfigDict(extra="forbid")
    connection_id: str
    src_block: str
    src_port: str
    dst_block: str
    dst_port: str
    dtype: str | None = None


class GrcBlock(BaseModel):
    """A single GRC block as seen by the model."""

    model_config = ConfigDict(extra="forbid")
    instance_name: str
    block_type: str
    block_uid: str
    role: BlockRole
    state: str
    parameters: list[GrcParameter] = Field(default_factory=list)
    coordinate: tuple[float, float] | None = None


class GrcValidation(BaseModel):
    """A GRC validation result as seen by the model."""

    model_config = ConfigDict(extra="forbid")
    status: str = "unknown"
    errors: list[str] = Field(default_factory=list)
    native_ok: bool | None = None


class GrcFlowgraph(BaseModel):
    """A GRC flowgraph snapshot as seen by the model."""

    model_config = ConfigDict(extra="forbid")
    ok: bool
    graph_name: str
    file_format: int | None = None
    grc_version: str | None = None
    blocks: list[GrcBlock] = Field(default_factory=list)
    connections: list[GrcConnection] = Field(default_factory=list)
    validation: GrcValidation = Field(default_factory=GrcValidation)
    errors: list[dict[str, str]] = Field(default_factory=list)
    state_revision: int = 0


# --------------------------------------------------------------------------- #
# Inbound models (LLM tool-call arguments). extra="ignore" drops unknowns.    #
# --------------------------------------------------------------------------- #


class InspectGraphArgs(BaseModel):
    """Arguments to the inspect_graph tool. Extra fields are ignored."""

    model_config = ConfigDict(extra="ignore")
    view: str = "overview"
    targets: list[str] = Field(default_factory=list)
    params: list[str] = Field(default_factory=list)
    debug: bool = False


class ChangeGraphAddBlock(BaseModel):
    model_config = ConfigDict(extra="ignore")
    block_id: str
    instance_name: str
    params: dict[str, Any] = Field(default_factory=dict)
    state: str | None = None


class ChangeGraphRemoveBlock(BaseModel):
    model_config = ConfigDict(extra="ignore")
    instance_name: str
    block_type: str | None = None


class ChangeGraphUpdateParams(BaseModel):
    model_config = ConfigDict(extra="ignore")
    instance_name: str
    params: dict[str, Any]
    block_type: str | None = None


class ChangeGraphUpdateStates(BaseModel):
    model_config = ConfigDict(extra="ignore")
    instance_name: str
    state: str
    block_type: str | None = None


class ChangeGraphAddConnection(BaseModel):
    model_config = ConfigDict(extra="ignore")
    src: dict[str, str]
    dst: dict[str, str]


class ChangeGraphRemoveConnection(BaseModel):
    model_config = ConfigDict(extra="ignore")
    connection_id: str


class ChangeGraphArgs(BaseModel):
    """Arguments to the change_graph tool. Extra fields are ignored."""

    model_config = ConfigDict(extra="ignore")
    add_blocks: list[ChangeGraphAddBlock] = Field(default_factory=list)
    remove_blocks: list[ChangeGraphRemoveBlock] = Field(default_factory=list)
    update_params: list[ChangeGraphUpdateParams] = Field(default_factory=list)
    update_states: list[ChangeGraphUpdateStates] = Field(default_factory=list)
    add_connections: list[ChangeGraphAddConnection] = Field(default_factory=list)
    remove_connections: list[ChangeGraphRemoveConnection] = Field(default_factory=list)
    force: bool = False
    debug: bool = False


class QueryKnowledgeArgs(BaseModel):
    """Arguments to the query_knowledge tool. Extra fields are ignored."""

    model_config = ConfigDict(extra="ignore")
    query: str
    domain: str = "catalog"
    debug: bool = False


# --------------------------------------------------------------------------- #
# Error infrastructure (moved from the former ``_payload.py``).                #
# Plain constants + builder — not Pydantic models.                            #
# --------------------------------------------------------------------------- #


class ErrorCode:
    """Canonical error-type strings emitted in ``error_type`` fields."""

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
