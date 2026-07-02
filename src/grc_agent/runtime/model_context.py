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
4. Episodic memory pruning: strip tool_call and tool_result payloads from
   completed prior turns so the model only sees tool activity from the
   current active episode. An episode boundary is a terminal assistant
   message (one that contains TextContent but no ToolCallContent).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from grc_agent.runtime.tool_context import tool_history_content_as_text
from ToolAgents.data_models.chat_history import ChatHistory
from ToolAgents.data_models.messages import (
    ChatMessage,
    ChatMessageRole,
    TextContent,
    ToolCallResultContent,
)

PreviewCallback = Callable[..., list[dict[str, str]]]


def _format_tool_result(content: ToolCallResultContent, *, preview: PreviewCallback) -> str:
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


def _is_human_user_message(message: ChatMessage) -> bool:
    if message.role != ChatMessageRole.User:
        return False
    text = message.get_as_text() if hasattr(message, "get_as_text") else ""
    if not text:
        parts = [c.content for c in message.content if isinstance(c, TextContent)]
        text = "".join(parts)
    return "<runtime_directive>" not in text


def _strip_tool_content(message: ChatMessage) -> ChatMessage | None:
    if message.role == ChatMessageRole.User:
        text = message.get_as_text() if hasattr(message, "get_as_text") else ""
        if not text:
            parts = [c.content for c in message.content if isinstance(c, TextContent)]
            text = "".join(parts)
        if "<runtime_directive>" in text:
            return None
    return message


def _prune_completed_episodes(messages: list[ChatMessage]) -> list[ChatMessage]:
    human_boundary = -1
    for i in range(len(messages) - 1, -1, -1):
        if _is_human_user_message(messages[i]):
            human_boundary = i
            break
    if human_boundary < 0:
        return messages

    dead_history = messages[:human_boundary]
    active_history = messages[human_boundary:]

    pruned_dead: list[ChatMessage] = []
    for msg in dead_history:
        stripped = _strip_tool_content(msg)
        if stripped is not None:
            pruned_dead.append(stripped)

    combined = pruned_dead + active_history

    consolidated: list[ChatMessage] = []
    for msg in combined:
        if not consolidated:
            consolidated.append(msg)
            continue
        prev = consolidated[-1]
        if msg.role == prev.role and msg.role in (ChatMessageRole.User, ChatMessageRole.Assistant):
            merged_content = []
            for item in prev.content:
                merged_content.append(item)
            for item in msg.content:
                if (
                    isinstance(item, TextContent)
                    and merged_content
                    and isinstance(merged_content[-1], TextContent)
                ):
                    merged_content[-1] = TextContent(
                        content=merged_content[-1].content + "\n\n" + item.content
                    )
                else:
                    merged_content.append(item)
            consolidated[-1] = ChatMessage(
                id=prev.id,
                role=prev.role,
                content=merged_content,
                created_at=prev.created_at,
                updated_at=prev.updated_at,
                additional_fields=prev.additional_fields,
                additional_information=prev.additional_information,
            )
        else:
            consolidated.append(msg)
    return consolidated


def render_model_messages(
    chat_history: ChatHistory,
    *,
    system_prompt: str,
    semantic_search_result_preview: PreviewCallback,
    reminder: str | None = None,
    system_salt: str | None = None,
) -> list[ChatMessage]:
    """Render ``chat_history`` into the message list handed to the provider."""
    raw_messages = chat_history.get_messages()
    pruned = _prune_completed_episodes(raw_messages)

    sys_msg = system_prompt
    if system_salt:
        sys_msg += f"\n# {system_salt}"

    messages: list[ChatMessage] = [ChatMessage.create_system_message(sys_msg)]
    for message in pruned:
        messages.append(_ensure_serializable(message, preview=semantic_search_result_preview))
    if reminder:
        wrapped = f"<runtime_directive>\n{reminder}\n</runtime_directive>"
        messages.append(ChatMessage.create_user_message(wrapped))
    return messages


# -- system prompt (was prompt.py) --

__version__ = "2026-07-02-concise-no-latex"


def build_system_prompt(session_id: str | None = None) -> str:
    """Return the system prompt shipped to the model."""
    prefix = f"Session ID: {session_id}\n" if session_id else ""
    return prefix + (
        "Role: GNU Radio graph editing assistant.\n"
        "inspect_graph: read topology, blocks, connections, field values, and validation status. "
        "Pass a targets list of block instance names to scope it to those blocks instead of the whole graph.\n"
        "query_knowledge: search catalog blocks or GNU Radio documentation.\n"
        "change_graph: add/remove blocks, edit field values, add/remove connections.\n"
        "Parameter values are string expressions; a variable reference is simply the variable's name (e.g. use 'base_freq * 1.5', NOT 'vars.base_freq * 1.5').\n"
        "Connections use numeric port keys (e.g. '0', '1', '2'), not names like 'out', 'in(0)', or 'in0'. GRC error messages like 'in(0)' refer to port index '0'.\n"
        "New blocks whose id contains _xx / _ff / _cc / _ii default to type=complex; "
        "set type explicitly (e.g. type=float) when the connection requires it.\n"
        "Do not attempt to rename blocks by changing the 'id' parameter in update_params; "
        "changing a block's ID is not supported and will be ignored. To rename a block, you must remove it and add a new one.\n"
        'Variables are blocks; use block_id "variable" (not "parameter") to add one.\n'
        "Every GNU Radio fact must be grounded in query_knowledge, not memory.\n"
        "Ensure the final state of the flowgraph is valid: run inspect_graph before finishing "
        "and verify that validation.status is 'valid'.\n"
        "A change_graph call that returns ok=false applied nothing — the batch was rolled back. "
        "Read the errors, adjust the call, and retry; do not resubmit identical arguments.\n"
        "The force=True flag in change_graph commits edits but does not resolve errors; "
        "you must still fix any unconnected ports or blocks to make the graph valid.\n"
        "Disabling a block that is part of a connection fails native validation ('Port is not connected'); "
        "use state=bypass to take a connected block out of service without breaking the graph, "
        "or force=true to commit the disabled state anyway.\n"
        "When removing blocks, also remove or disable any source blocks that become unconnected.\n"
        "Never use hallucinated block IDs; if query_knowledge does not return a block ID, it does not exist.\n"
        "When the user asks a question, answer concisely: lead with the direct answer, then add only the context needed to act on it.\n"
        "Do not use LaTeX or TeX math notation in chat replies; write math inline in plain text (e.g. `350 microHz`, `f^2`, `x_i`).\n"
    )


# -- tool surface (was tool_surface.py) --

MVP_MODEL_TOOL_NAMES: tuple[str, ...] = (
    "inspect_graph",
    "query_knowledge",
    "web_search",
    "web_fetch",
    "change_graph",
)

# The one tool in the MVP surface that mutates the flowgraph. Single source
# of truth so the agent's checkpoint/journal logic and the GUI's
# mutation-rendering logic agree on the same name.
GRAPH_MUTATING_TOOL_NAME: str = "change_graph"


@dataclass(frozen=True)
class ToolSurface:
    """Runtime policy for one model-facing tool profile."""

    name: str
    model_tool_names: tuple[str, ...]
    default_max_tool_rounds: int


MVP_TOOL_SURFACE = ToolSurface(
    name="mvp",
    model_tool_names=MVP_MODEL_TOOL_NAMES,
    default_max_tool_rounds=8,
)

__all__ = ["render_model_messages"]


