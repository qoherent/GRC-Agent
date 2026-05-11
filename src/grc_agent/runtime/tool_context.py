"""Tool-history compaction and model-visible formatting helpers."""

from __future__ import annotations

import json
from typing import Any, Callable

HistoryEntry = dict[str, Any]
PreviewCallback = Callable[..., list[dict[str, Any]]]


def compact_tool_entry(
    turn: HistoryEntry,
    *,
    search_result_preview: PreviewCallback,
    semantic_search_result_preview: PreviewCallback,
) -> HistoryEntry:
    content = turn.get("content")
    if not isinstance(content, dict):
        return turn
    compact: dict[str, Any] = {}
    for key in (
        "ok",
        "message",
        "error_type",
        "active_session",
        "tool",
        "valid",
        "hint",
        "suggested_next_tools",
    ):
        if key in content:
            compact[key] = content[key]
    tool_name = turn.get("name")
    if tool_name == "summarize_graph":
        summary = content.get("summary")
        if isinstance(summary, str) and summary:
            compact["summary"] = summary
    if tool_name == "search_grc":
        for key in ("query", "scope"):
            value = content.get(key)
            if isinstance(value, str) and value:
                compact[key] = value
        results_preview = search_result_preview(content.get("results"))
        if results_preview:
            compact["results_preview"] = results_preview
        fallback_preview = search_result_preview(
            content.get("catalog_fallback_preview")
        )
        if fallback_preview:
            compact["catalog_fallback_preview"] = fallback_preview
    if tool_name == "semantic_search_grc":
        for key in ("query", "scope"):
            value = content.get(key)
            if isinstance(value, str) and value:
                compact[key] = value
        results_preview = semantic_search_result_preview(content.get("results"))
        if results_preview:
            compact["results_preview"] = results_preview
    if not compact:
        compact["ok"] = content.get("ok", False)
        compact["message"] = "result truncated"
    return {
        "role": turn.get("role"),
        "tool_call_id": turn.get("tool_call_id"),
        "name": turn.get("name"),
        "content": compact,
    }


def tool_history_content_as_text(
    content: dict[str, Any],
    *,
    tool_name: str,
    search_result_preview: PreviewCallback,
    semantic_search_result_preview: PreviewCallback,
) -> str:
    """Render one tool result with the next-step hint made prominent for the model."""
    compact = dict(content)
    validation = compact.get("validation")
    if isinstance(validation, dict):
        compact["validation"] = {
            "status": validation.get("status"),
            "returncode": validation.get("returncode"),
        }

    active_session = compact.get("active_session")
    if isinstance(active_session, dict):
        active_validation = active_session.get("validation")
        compact["active_session"] = {
            "path": active_session.get("path"),
            "graph_id": active_session.get("graph_id"),
            "state_revision": active_session.get("state_revision"),
            "dirty": active_session.get("dirty"),
            "validation": {
                "status": active_validation.get("status"),
                "returncode": active_validation.get("returncode"),
            }
            if isinstance(active_validation, dict)
            else active_validation,
            "block_count": active_session.get("block_count"),
            "connection_count": active_session.get("connection_count"),
            "variable_count": active_session.get("variable_count"),
            "variable_preview": active_session.get("variable_preview"),
            "block_preview": active_session.get("block_preview"),
            "connection_preview": active_session.get("connection_preview"),
        }

    if tool_name == "search_grc":
        compact.pop("results", None)
        history_preview = search_result_preview(
            content.get("results"),
            include_summary=False,
        )
        if history_preview:
            compact["results_preview"] = history_preview
        fallback_preview = search_result_preview(
            content.get("catalog_fallback_preview"),
            include_summary=False,
        )
        if fallback_preview:
            compact["catalog_fallback_preview"] = fallback_preview
    if tool_name == "semantic_search_grc":
        compact.pop("results", None)
        history_preview = semantic_search_result_preview(content.get("results"))
        if history_preview:
            compact["results_preview"] = history_preview

    if tool_name == "get_grc_context":
        compact.pop("nodes", None)
        target = compact.get("target")
        if isinstance(target, dict):
            compact["target"] = {
                key: target.get(key)
                for key in ("node_id", "label", "block_type", "incoming", "outgoing")
                if key in target
            }

    if tool_name == "apply_edit" and compact.get("ok") is True:
        compact["message"] = "Edit applied. Internal compile check passed."

    lines = [f"{tool_name} result"]
    ok = compact.get("ok")
    if isinstance(ok, bool):
        lines[0] = f"{lines[0]}: ok={ok}"
    message = compact.get("message")
    if isinstance(message, str) and message:
        lines.append(f"message: {message}")
    hint = compact.get("hint")
    if isinstance(hint, str) and hint:
        lines.append(f"hint: {hint}")
    if tool_name == "search_grc" and (
        compact.get("results_preview") or compact.get("catalog_fallback_preview")
    ):
        lines.append(
            "next_step_note: search previews are routing only; for later follow-ups like `what does that block look like?`, call describe_block with the stored block_id, not get_grc_context."
        )
    if tool_name == "get_grc_context":
        lines.append(
            "next_step_note: inspection data is routing only; do not answer later edit or preview requests from it."
        )
    if tool_name == "semantic_search_grc":
        lines.append(
            "next_step_note: semantic search is read-only candidate discovery; it cannot authorize apply_edit, save_graph, insertions, removals, or repairs."
        )
    lines.append(json.dumps(compact, sort_keys=True))
    return "\n".join(lines)
