"""Tool-history compaction, model-visible formatting, output policy, and path safety.

Consolidated from tool_context.py + output_policy.py + path_safety.py + capabilities.py.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, TypeVar

from ToolAgents.data_models.chat_history import ChatHistory
from ToolAgents.data_models.messages import (
    ChatMessage,
    ToolCallResultContent,
)

_T = TypeVar("_T")

PreviewCallback = Callable[..., list[dict[str, Any]]]
_MODEL_WRAPPER_NAMES = {
    "inspect_graph",
    "search_blocks",
    "ask_grc_docs",
    "change_graph",
}


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
    # Surface every per-error hint (not just the first promoted to top-level).
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
            if (
                isinstance(entry_hint, str)
                and entry_hint.strip()
                and entry_hint.strip() != (hint or "").strip()
            ):
                lines.append(f"hint: {entry_hint}")
    # Native GRC validation errors are stored as a sibling payload field
    # (not under ``errors``). Render them as ``error:`` lines so the model
    # sees the real reason instead of a JSON dump.
    native_errors = compact.get("native_validation_errors")
    if isinstance(native_errors, list) and native_errors:
        for native_msg in native_errors:
            if isinstance(native_msg, str) and native_msg.strip():
                lines.append(f"error: gnu_validation — {native_msg}")
    # Stderr tail often names the underlying exception (e.g. a
    # ``LookupError`` that the model can act on). Surface the last line
    # when there is no actionable hint already.
    stderr_text = compact.get("stderr")
    if (
        isinstance(stderr_text, str)
        and stderr_text.strip()
        and not any(line.startswith("error:") for line in lines)
    ):
        last_line = stderr_text.strip().splitlines()
        tail = last_line[-1].strip() if last_line else ""
        if tail and len(tail) < 400:
            lines.append(f"hint: {tail}")
    if tool_name in {"search_blocks", "ask_grc_docs"}:
        return json.dumps(compact, separators=(",", ":"), sort_keys=True)
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


def is_variable_block(block_type: str) -> bool:
    """Whether a block type is a GRC variable/control block.

    Uses the native GRC ``Block.is_variable`` discriminator (``bool(cls.value)``)
    via the platform's block-class registry when available. Falls back to
    GRC's ``not_dsp`` flag from the catalog descriptor when the class
    registry is unavailable but the catalog is.

    The native path catches variable types that don't start with ``variable_``
    (e.g. ``json_config``, ``yaml_config``, ``qtgui_dialgauge``).
    """
    if not isinstance(block_type, str) or not block_type:
        return False
    try:
        from grc_agent.grc_native_adapter import get_platform_or_none

        platform = get_platform_or_none()
    except Exception:
        platform = None
    if platform is not None:
        cls = getattr(platform, "block_classes", {}).get(block_type)
        if cls is not None:
            return bool(getattr(cls, "value", None))
    # Secondary: check catalog flags for ``not_dsp`` (GRC's native signal
    # for control/variable blocks — set by the block author in YAML).
    try:
        from grc_agent.catalog.loaders import describe_block

        details = describe_block(block_type)
        if details.get("ok"):
            flags = details.get("flags") or []
            if "not_dsp" in flags:
                return True
    except Exception:
        pass
    # Last resort: string-prefix heuristic (misses some edge cases).
    return block_type == "variable" or block_type.startswith("variable_")


# -- path safety (was path_safety.py) --


def resolved_path(path_value: str | Path) -> Path:
    return Path(path_value).expanduser().resolve(strict=False)


def unsafe_graph_root_for_path(
    path_value: str | Path,
    *,
    installed_graph_roots: Iterable[Path],
    canonical_fixture_root: Path,
) -> str | None:
    candidate = resolved_path(path_value)
    roots = (*installed_graph_roots, canonical_fixture_root)
    for root in roots:
        resolved_root = root.expanduser().resolve(strict=False)
        try:
            candidate.relative_to(resolved_root)
        except ValueError:
            continue
        return str(resolved_root)
    return None


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
