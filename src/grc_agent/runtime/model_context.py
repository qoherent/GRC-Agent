"""Render runtime history into model-facing chat messages."""

from __future__ import annotations

import json
from typing import Any, Callable

from grc_agent.runtime.tool_context import tool_history_content_as_text


HistoryEntry = dict[str, Any]
PromptProvider = Callable[[], str]
PreviewCallback = Callable[..., list[dict[str, str]]]


def render_model_messages(
    history: list[HistoryEntry],
    *,
    system_prompt_provider: PromptProvider,
    semantic_search_result_preview: PreviewCallback,
    reminder: str | None = None,
) -> list[HistoryEntry]:
    """Render runtime history into chat-completions messages."""
    messages: list[HistoryEntry] = [
        {
            "role": "system",
            "content": system_prompt_provider(),
        }
    ]

    for index, turn in enumerate(history):
        role = turn.get("role")

        if role == "session":
            continue

        if role == "tool":
            tool_name = turn.get("name")
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": str(turn.get("tool_call_id") or f"tool_call_{index}"),
                    "name": tool_name,
                    "content": history_content_as_text(
                        turn.get("content"),
                        tool_name=tool_name,
                        semantic_search_result_preview=semantic_search_result_preview,
                    ),
                }
            )
            continue

        if role not in {"user", "assistant"}:
            continue

        message: HistoryEntry = {
            "role": role,
            "content": turn.get("content"),
        }
        if role == "assistant" and "tool_calls" in turn:
            message["tool_calls"] = turn["tool_calls"]
        messages.append(message)

    if reminder:
        messages.append(
            {
                "role": "user",
                "content": f"Runtime reminder: {reminder}",
            }
        )

    return messages


def history_content_as_text(
    content: Any,
    *,
    tool_name: str | None = None,
    semantic_search_result_preview: PreviewCallback,
) -> str:
    """Normalize stored history content into the string form chat APIs expect."""
    if (
        tool_name == "summarize_graph"
        and isinstance(content, dict)
        and isinstance(content.get("summary"), str)
    ):
        return content["summary"]
    if isinstance(content, str):
        return content
    if content is None:
        return ""
    if isinstance(content, dict) and tool_name is not None:
        return tool_history_content_as_text(
            content,
            tool_name=tool_name,
            semantic_search_result_preview=semantic_search_result_preview,
        )
    if isinstance(content, (dict, list)):
        return json.dumps(content, sort_keys=True)
    return str(content)
