"""Tool-history compaction, model-visible formatting, output policy, and path safety.

Consolidated from tool_context.py + output_policy.py + path_safety.py + capabilities.py.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from typing import Any

from ToolAgents.data_models.chat_history import ChatHistory
from ToolAgents.data_models.messages import (
    ChatMessage,
    ToolCallResultContent,
)

PreviewCallback = Callable[..., list[dict[str, Any]]]
# Model-facing tool names (the 3 MVP tools). Kept in sync with
# MVP_MODEL_TOOL_NAMES in runtime/model_context.py; derived at runtime
# in P2 to break the import cycle between tool_context <-> model_context.
_MODEL_WRAPPER_NAMES: frozenset[str] = frozenset({
    "inspect_graph",
    "query_knowledge",
    "change_graph",
})


def tool_history_content_as_text(
    content: dict[str, Any],
    *,
    tool_name: str,
    semantic_search_result_preview: PreviewCallback,
) -> str:
    """Render one tool result with the next-step hint made prominent for the model."""
    compact = dict(content)
    if tool_name in _MODEL_WRAPPER_NAMES:
        compact = _compact_wrapper_result(tool_name, compact)

    validation = compact.get("validation")
    if isinstance(validation, dict):
        omitted = [k for k in validation if k not in {"status", "returncode"}]
        compact["validation"] = {
            "status": validation.get("status"),
            "returncode": validation.get("returncode"),
        }
        if omitted:
            compact["validation"]["omitted"] = omitted

    active_session = compact.get("active_session")
    if isinstance(active_session, dict):
        active_validation = active_session.get("validation")
        keep_session_keys = {
            "path", "graph_id", "state_revision", "dirty", "validation",
            "block_count", "connection_count", "variable_count",
            "variable_preview", "block_preview", "connection_preview"
        }
        omitted = [k for k in active_session if k not in keep_session_keys]

        session_val_dict = None
        if isinstance(active_validation, dict):
            val_omitted = [k for k in active_validation if k not in {"status", "returncode"}]
            session_val_dict = {
                "status": active_validation.get("status"),
                "returncode": active_validation.get("returncode"),
            }
            if val_omitted:
                session_val_dict["omitted"] = val_omitted
        else:
            session_val_dict = active_validation

        compact["active_session"] = {
            "path": active_session.get("path"),
            "graph_id": active_session.get("graph_id"),
            "state_revision": active_session.get("state_revision"),
            "dirty": active_session.get("dirty"),
            "validation": session_val_dict,
            "block_count": active_session.get("block_count"),
            "connection_count": active_session.get("connection_count"),
            "variable_count": active_session.get("variable_count"),
            "variable_preview": active_session.get("variable_preview"),
            "block_preview": active_session.get("block_preview"),
            "connection_preview": active_session.get("connection_preview"),
        }
        if omitted:
            compact["active_session"]["omitted"] = omitted

    lines = [f"{tool_name} result"]
    ok = compact.get("ok")
    if isinstance(ok, bool):
        lines[0] = f"{lines[0]}: ok={ok}"
    message = compact.get("message")
    if isinstance(message, str) and message:
        lines.append(f"message: {message}")
    # Surface every per-error hint.
    errors_list = compact.get("errors")
    if isinstance(errors_list, list) and errors_list:
        for error_entry in errors_list:
            if not isinstance(error_entry, dict):
                continue
            code = error_entry.get("code", "error")
            message = error_entry.get("message", "")
            if isinstance(message, str) and message:
                lines.append(f"error: {code} — {message}")
            entry_hint = error_entry.get("hint")
            if isinstance(entry_hint, str) and entry_hint.strip():
                lines.append(f"hint: {entry_hint}")
    lines.append(json.dumps(compact, separators=(",", ":"), sort_keys=True))
    return "\n".join(lines)


def _compact_wrapper_result(tool_name: str, content: dict[str, Any]) -> dict[str, Any]:
    return _drop_empty_recursive(content)


def is_meaningful(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str) and value == "":
        return False
    if isinstance(value, list) and len(value) == 0:
        return False
    if isinstance(value, dict) and len(value) == 0:
        return False
    return True


def _drop_empty_recursive(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            dropped = _drop_empty_recursive(item)
            if is_meaningful(dropped):
                result[key] = dropped
        return result
    if isinstance(value, list):
        return [
            dropped
            for dropped in (_drop_empty_recursive(item) for item in value)
            if is_meaningful(dropped)
        ]
    return value


# --- merged from chat_history.py ---

_TRUNCATION_SENTINEL_TEMPLATE = (
    "... [TRUNCATED by chat-history compactor: was {original} chars, kept {kept}]"
)


def _shorten_tool_result(
    content: ToolCallResultContent,
    *,
    max_chars: int,
    original_length: int | None = None,
) -> ToolCallResultContent:
    original = content.tool_call_result
    if len(original) <= max_chars:
        return content
    if original_length is None:
        original_length = len(original)
    sentinel = _TRUNCATION_SENTINEL_TEMPLATE.format(original=original_length, kept=max_chars - 1)
    budget = max(0, max_chars - len(sentinel) - 1)
    kept = original[:budget].rstrip()
    return ToolCallResultContent(
        tool_call_result_id=content.tool_call_result_id or str(uuid.uuid4()),
        tool_call_id=content.tool_call_id,
        tool_call_name=content.tool_call_name,
        tool_call_result=f"{kept} {sentinel}",
    )


def _replace_tool_result(
    message: ChatMessage,
    original: ToolCallResultContent,
    replacement: ToolCallResultContent,
) -> ChatMessage:
    new_content = []
    for item in message.content:
        if item is original:
            new_content.append(replacement)
        else:
            new_content.append(item)
    if new_content == list(message.content):
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


def compact_chat_history(
    chat_history: ChatHistory,
    *,
    budget_chars: int,
    max_tool_result_chars: int = 4000,
) -> bool:
    """Shorten tool results in ``chat_history`` until the total is under budget.

    Returns ``True`` if any message was rewritten, ``False`` if the history
    already fit. The function is idempotent: calling it twice with the same
    arguments is a no-op on the second call.

    ``max_tool_result_chars`` caps any individual ``ToolCallResultContent``
    payload. The previous hard-coded cap of 800 was starving the model of
    ``query_knowledge`` results: a single GNU Radio catalog block
    definition (name, IO signature, full param list) can easily exceed
    800 chars, so the compactor was deleting the exact block ID the
    model had just retrieved. 4000 chars is roughly 1,000 tokens — large
    enough to fit a full catalog JSON object, small enough that even
    ten such payloads still fit comfortably inside the 100K-char
    ``history_compact_budget`` and the 256K-token context window of
    even the smallest local model.

    Algorithm: one pass. We split the history into an immutable
    "shell" (system message, user messages, assistant text, tool-call
    arguments) and a mutable sum of tool-result payload lengths. We
    compute the exact new payload size for each candidate by
    proportional allocation against the remaining budget, then slice
    each payload once. No iteration, no per-cycle length re-computation.
    """
    if budget_chars <= 0:
        return False
    messages = chat_history.get_messages()
    if not messages:
        return False

    floor = 64

    def _payload_length(content: ToolCallResultContent) -> int:
        return len(content.tool_call_result)

    def _shell_length(message: ChatMessage) -> int:
        full = len(message.get_as_text())
        payload_total = sum(
            _payload_length(c) for c in message.content if isinstance(c, ToolCallResultContent)
        )
        return full - payload_total

    shell_total = 0
    candidate_payloads: list[tuple[ChatMessage, ToolCallResultContent, int]] = []
    candidate_total = 0
    for message in messages:
        shell_total += _shell_length(message)
        for content in message.content:
            if isinstance(content, ToolCallResultContent) and len(content.tool_call_result) > floor:
                length = _payload_length(content)
                candidate_payloads.append((message, content, length))
                candidate_total += length

    if not candidate_payloads:
        return False

    total = shell_total + candidate_total
    if total <= budget_chars:
        return False

    mutable_budget = max(0, budget_chars - shell_total)
    floor_total = floor * len(candidate_payloads)
    if mutable_budget < floor_total:
        mutable_budget = floor_total

    extra = mutable_budget - floor_total
    new_sizes: list[int] = []
    if candidate_total == 0:
        new_sizes = [floor] * len(candidate_payloads)
    else:
        for _, _, current_length in candidate_payloads:
            share = int(round(extra * (current_length / candidate_total)))
            new_sizes.append(floor + share)

    changed = False
    for (message, content, current_length), new_length in zip(
        candidate_payloads, new_sizes, strict=False
    ):
        target = min(max_tool_result_chars, new_length)
        if target >= current_length:
            continue
        if target < floor:
            target = floor
        new_content = _shorten_tool_result(
            content,
            max_chars=target,
            original_length=current_length,
        )
        if new_content is content:
            continue
        idx_in_history = chat_history.messages.index(message)
        old_message = chat_history.messages[idx_in_history]
        new_message = _replace_tool_result(old_message, content, new_content)
        if new_message is not old_message:
            chat_history.messages[idx_in_history] = new_message
            changed = True
    return changed
