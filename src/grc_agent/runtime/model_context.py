"""Render a :class:`ChatHistory` into model-facing messages.

The native :class:`ToolAgents.data_models.chat_history.ChatHistory` does not
need an adapter. The only model-facing concerns that live here are:

1. Prepend a system message (rebuilt every turn so ``chat_session_id`` etc.
   stay current).
2. Optionally append a "runtime reminder" message at the end as a
   ``User``-role message. Using ``user`` (not ``system``) avoids
   mid-stream template-safety rejections in chat templates that
   forbid ``system`` after the first user turn; using ``user`` (not a
   ``Custom`` role tag) avoids emitting a non-standard
   ``role: "runtime_reminder"`` over the OpenAI-compatible wire
   format that small local backends may reject.
3. Format ``ToolCallResultContent`` payloads into compact model-visible
   text (delegated to :mod:`grc_agent.runtime.tool_context`).
"""

from __future__ import annotations

import datetime
import json
import uuid
from collections.abc import Callable
from typing import Any

from ToolAgents.data_models.chat_history import ChatHistory
from ToolAgents.data_models.messages import (
    ChatMessage,
    TextContent,
    ToolCallResultContent,
)

from grc_agent.runtime.tool_context import tool_history_content_as_text

PreviewCallback = Callable[..., list[dict[str, str]]]


def _format_tool_result(
    content: ToolCallResultContent, *, preview: PreviewCallback
) -> str:
    payload = content.tool_call_result
    parsed: Any = payload
    if isinstance(payload, str):
        try:
            parsed = json.loads(payload)
        except (TypeError, ValueError):
            parsed = payload
    if not isinstance(parsed, dict):
        return payload if isinstance(payload, str) else str(payload)
    return tool_history_content_as_text(
        parsed,
        tool_name=content.tool_call_name,
        semantic_search_result_preview=preview,
    )


def _ensure_serializable(message: ChatMessage, *, preview: PreviewCallback) -> ChatMessage:
    new_content = []
    changed = False
    for item in message.content:
        if isinstance(item, ToolCallResultContent):
            text = _format_tool_result(item, preview=preview)
            new_content.append(
                ToolCallResultContent(
                    tool_call_result_id=item.tool_call_result_id or str(uuid.uuid4()),
                    tool_call_id=item.tool_call_id,
                    tool_call_name=item.tool_call_name,
                    tool_call_result=text,
                )
            )
            changed = True
        else:
            new_content.append(item)
    if not changed:
        return message
    return ChatMessage(
        id=message.id,
        role=message.role,
        content=new_content,
        created_at=message.created_at,
        updated_at=message.updated_at,
        additional_fields=message.additional_fields,
        additional_information=message.additional_information,
    )


def render_model_messages(
    chat_history: ChatHistory,
    *,
    system_prompt: str,
    semantic_search_result_preview: PreviewCallback,
    reminder: str | None = None,
) -> list[ChatMessage]:
    """Render ``chat_history`` into the message list handed to the provider."""
    messages: list[ChatMessage] = [ChatMessage.create_system_message(system_prompt)]
    for message in chat_history.get_messages():
        messages.append(_ensure_serializable(message, preview=semantic_search_result_preview))
    if reminder:
        # Wrap in ``<runtime_directive>`` so the model can tell the
        # reminder apart from the human user's own text. The chat
        # template still routes a ``user``-role message to the user
        # turn, which is wire-format-safe for every OpenAI-compatible
        # backend and dodges the "system mid-stream" template
        # rejection that ``role: system`` would trigger.
        wrapped = (
            "<runtime_directive>\n"
            f"{reminder}\n"
            "</runtime_directive>"
        )
        messages.append(ChatMessage.create_user_message(wrapped))
    return messages


__all__ = ["render_model_messages"]
