"""Tool-history compaction, model-visible formatting, output policy, and path safety.

Consolidated from tool_context.py + output_policy.py + path_safety.py + capabilities.py.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

PreviewCallback = Callable[..., list[dict[str, Any]]]
# Tools whose results go through wrapper-result compaction (recursive
# drop-empty via _drop_empty_recursive). A hand-maintained subset of the
# 5-tool MVP_MODEL_TOOL_NAMES (runtime/model_context.py) — deliberately
# narrower, NOT derived from it at runtime: web_search/web_fetch return
# flatter, already-compact payloads and are not included here.
_MODEL_WRAPPER_NAMES: frozenset[str] = frozenset(
    {
        "inspect_graph",
        "query_knowledge",
        "change_graph",
    }
)


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
            "path",
            "graph_id",
            "state_revision",
            "dirty",
            "validation",
            "block_count",
            "connection_count",
            "variable_count",
            "variable_preview",
            "block_preview",
            "connection_preview",
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
