"""Chat-role constants and message-payload helpers.

Single source of truth for the model-role labels and chat-message
serialization shared by the runtime, GUI, and tests. Extracted from the former
``session_ops.py``.
"""

from __future__ import annotations

from typing import Any

from ToolAgents.data_models.messages import ChatMessage

DISPLAY_ROLES: frozenset[str] = frozenset(
    {"user", "assistant", "mutation", "error", "info"}
)
ASSISTANT_MODEL_ROLE = "assistant_model"
TOOL_MODEL_ROLE = "tool_model"


def chat_message_payload(message: ChatMessage) -> dict[str, Any]:
    return message.model_dump(mode="json", exclude_none=True)


def chat_message_from_payload(payload: dict[str, Any] | None) -> ChatMessage | None:
    if payload is None:
        return None
    try:
        return ChatMessage(**payload)
    except Exception:
        return None
