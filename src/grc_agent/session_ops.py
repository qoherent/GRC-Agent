"""Phase 6 — minimal session utilities. Legacy shared_* helpers removed."""
from __future__ import annotations
from typing import Any

from ToolAgents.data_models.messages import ChatMessage

ConnectionPort = int | str

# The legacy private methods this tracked are all deleted in Phase 6.
# Kept as an empty tuple so the hardening-contract test still imports.
FLOWGRAPH_SESSION_SHARED_PRIVATE_METHODS: tuple[str, ...] = ()

DISPLAY_ROLES: frozenset[str] = frozenset(
    {"user", "assistant", "tool_started", "tool_finished", "mutation", "error"}
)
MODEL_ROLES: frozenset[str] = frozenset({"assistant_model", "tool_model"})
ASSISTANT_MODEL_ROLE = "assistant_model"
TOOL_MODEL_ROLE = "tool_model"


def connection_id(
    src_block: str,
    src_port: ConnectionPort,
    dst_block: str,
    dst_port: ConnectionPort,
) -> str:
    return f"{src_block}:{src_port}->{dst_block}:{dst_port}"


def parse_connection_id(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, str) or "->" not in value:
        return None
    src_text, dst_text = value.split("->", 1)
    if ":" not in src_text or ":" not in dst_text:
        return None
    src_block, src_port_text = src_text.rsplit(":", 1)
    dst_block, dst_port_text = dst_text.rsplit(":", 1)
    if not src_block or not dst_block:
        return None
    src_port: ConnectionPort
    dst_port: ConnectionPort
    try:
        src_port = int(src_port_text)
    except ValueError:
        src_port = src_port_text
    try:
        dst_port = int(dst_port_text)
    except ValueError:
        dst_port = dst_port_text
    return {"src_block": src_block, "src_port": src_port,
            "dst_block": dst_block, "dst_port": dst_port}


def chat_message_payload(message: ChatMessage) -> dict[str, Any]:
    return message.model_dump(mode="json", exclude_none=True)


def chat_message_from_payload(payload: dict[str, Any] | None) -> ChatMessage | None:
    if payload is None:
        return None
    return ChatMessage(**payload)
