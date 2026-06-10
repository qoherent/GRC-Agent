"""Session-store helpers for the model-side message history rows.

The ``sessions.db`` stores two kinds of rows:

* display rows — existing flat text rows for the chat widget
  (role ∈ ``user``, ``assistant``, ``tool_started``, ``tool_finished``,
  ``mutation``, ``error``)
* model rows — typed ``ChatMessage`` payloads used to rebuild the
  agent's ``ChatHistory`` on resume (role ∈ ``assistant_model``,
  ``tool_model``). The ``payload`` column carries the JSON of the
  ``ChatMessage.model_dump()``.
"""

from __future__ import annotations

import logging
from typing import Any

from ToolAgents.data_models.messages import ChatMessage

logger = logging.getLogger(__name__)

DISPLAY_ROLES: frozenset[str] = frozenset(
    {"user", "assistant", "tool_started", "tool_finished", "mutation", "error"}
)
MODEL_ROLES: frozenset[str] = frozenset({"assistant_model", "tool_model"})

ASSISTANT_MODEL_ROLE = "assistant_model"
TOOL_MODEL_ROLE = "tool_model"


def chat_message_payload(message: ChatMessage) -> dict[str, Any]:
    """Serialize a ``ChatMessage`` for the ``payload`` column."""
    return message.model_dump(mode="json")


def chat_message_from_payload(payload: dict[str, Any] | None) -> ChatMessage | None:
    """Deserialize a ``ChatMessage`` from a ``payload`` column value.

    Returns ``None`` if the payload is missing, malformed, or fails
    validation. Failures are logged at warning level — a corrupt row
    must not crash the resume path.
    """
    if not isinstance(payload, dict):
        return None
    try:
        return ChatMessage.from_dict(payload)
    except Exception as exc:
        logger.warning("Failed to decode ChatMessage payload: %s", exc)
        return None


__all__ = [
    "ASSISTANT_MODEL_ROLE",
    "DISPLAY_ROLES",
    "MODEL_ROLES",
    "TOOL_MODEL_ROLE",
    "chat_message_from_payload",
    "chat_message_payload",
]
