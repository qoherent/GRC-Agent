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
    search_result_preview: PreviewCallback,
    semantic_search_result_preview: PreviewCallback,
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
            messages.append(
                {
                    "role": "system",
                    "content": session_history_content_as_text(
                        turn.get("content"),
                        reason=turn.get("reason"),
                    ),
                }
            )
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
                        search_result_preview=search_result_preview,
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

    return messages


def history_content_as_text(
    content: Any,
    *,
    tool_name: str | None = None,
    search_result_preview: PreviewCallback,
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
            search_result_preview=search_result_preview,
            semantic_search_result_preview=semantic_search_result_preview,
        )
    if isinstance(content, (dict, list)):
        return json.dumps(content, sort_keys=True)
    return str(content)


def session_history_content_as_text(content: Any, *, reason: Any = None) -> str:
    """Render bound active-session state into a deterministic model-visible message."""
    if not isinstance(content, dict):
        return "No active session context is available."
    action = "Switched active session" if reason == "load_grc" else "Active session"
    validation = content.get("validation")
    validation_status = (
        validation.get("status")
        if isinstance(validation, dict) and isinstance(validation.get("status"), str)
        else "unknown"
    )
    variables_hint = ""
    blocks_hint = ""
    connections_hint = ""
    count_parts = []
    if isinstance(content.get("block_count"), int):
        count_parts.append(f"blocks={content.get('block_count')}")
    if isinstance(content.get("connection_count"), int):
        count_parts.append(f"connections={content.get('connection_count')}")
    if isinstance(content.get("variable_count"), int):
        count_parts.append(f"variables={content.get('variable_count')}")
    counts_hint = f" {', '.join(count_parts)};" if count_parts else ""
    if reason != "turn_refresh":
        variable_preview = content.get("variable_preview")
        if isinstance(variable_preview, list) and variable_preview:
            variables_hint = f" variables=[{', '.join(str(item) for item in variable_preview)}];"
        block_preview = content.get("block_preview")
        if isinstance(block_preview, list) and block_preview:
            blocks_hint = f" blocks=[{', '.join(str(item) for item in block_preview[:6])}];"
        connection_preview = content.get("connection_preview")
        if isinstance(connection_preview, list) and connection_preview:
            connections_hint = (
                " connections_preview=["
                f"{', '.join(str(item) for item in connection_preview[:8])}];"
            )
    return (
        f"{action}: path={content.get('path')}, "
        f"graph_id={content.get('graph_id')}, "
        f"state_revision={content.get('state_revision')}, "
        f"dirty={content.get('dirty')}, "
        f"validation={validation_status};"
        f"{counts_hint}{variables_hint}{blocks_hint}{connections_hint}"
    )
