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
    if tool_name == "get_grc_context":
        lines.append(
            "next_step_note: inspection data is routing only."
        )
    if tool_name == "inspect_graph" and compact.get("view") == "overview":
        return _render_inspect_overview_result(lines, compact)
    if tool_name == "change_graph":
        return _render_change_graph_result(lines, compact)
    if tool_name in {"search_blocks", "ask_grc_docs"}:
        return json.dumps(compact, separators=(",", ":"), sort_keys=True)
    lines.append(json.dumps(compact, sort_keys=True))
    return "\n".join(lines)


def _compact_wrapper_result(tool_name: str, content: dict[str, Any]) -> dict[str, Any]:
    if tool_name == "inspect_graph":
        return _compact_inspect_graph(content)
    if tool_name == "search_blocks":
        return _compact_search_blocks(content)
    if tool_name == "ask_grc_docs":
        return _compact_ask_grc_docs(content)
    if tool_name == "change_graph":
        return _compact_change_graph(content)
    return _common_result(content)


def _common_result(content: dict[str, Any]) -> dict[str, Any]:
    result = {
        "ok": content.get("ok"),
        "message": _short_text(content.get("message"), 220),
        "error_type": content.get("error_type"),
    }
    return _drop_empty(result)


def _compact_inspect_graph(content: dict[str, Any]) -> dict[str, Any]:
    result = {
        **_common_result(content),
        "view": content.get("view"),
        "state_revision": content.get("state_revision"),
        "complete": content.get("complete"),
        "validation": _validation_status_brief(content.get("validation_status")),
    }
    errors = content.get("errors")
    if errors:
        result["errors"] = errors
    truncation = _truncation_brief(content.get("truncation"))
    if truncation:
        result["truncation"] = truncation
    if content.get("view") == "overview":
        result["summary"] = _compact_inspect_overview(content.get("summary"))
    else:
        result["targets"] = _compact_inspect_targets(content.get("targets"))
        result["target_matches"] = _compact_target_matches(content.get("target_matches"))
        result["params_filter"] = _compact_params_filter(content.get("params_filter"))
    return _drop_empty(result)


def _compact_inspect_overview(summary: Any) -> dict[str, Any] | None:
    if not isinstance(summary, dict):
        return None
    compact = {
        "graph_name": summary.get("graph_name"),
        "counts": _compact_overview_counts(summary.get("counts")),
        "blocks": _compact_overview_blocks(summary.get("blocks")),
        "connections": _compact_connection_rows(summary.get("connections"), limit=8),
    }
    return _drop_empty(compact)


def _compact_overview_counts(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    parts = []
    for key, label in (("blocks", "b"), ("connections", "c"), ("variables", "v")):
        item = value.get(key)
        if isinstance(item, int):
            parts.append(f"{label}={item}")
    return " ".join(parts) if parts else None


def _compact_overview_blocks(value: Any) -> list[str]:
    grouped: dict[tuple[str, str, str], list[str]] = {}
    for row in _list(value):
        if not isinstance(row, dict):
            continue
        name = str(row.get("instance_name") or row.get("name") or "").strip()
        if not name:
            continue
        block_type = str(row.get("block_type") or row.get("type") or "unknown").strip() or "unknown"
        role = str(row.get("role") or "unknown").strip() or "unknown"
        label = str(row.get("catalog_label") or "").strip()
        grouped.setdefault((role, block_type, label), []).append(name)
    return [
        _format_overview_block_group(role, block_type, label, names)
        for (role, block_type, label), names in grouped.items()
    ]


def _format_overview_block_group(
    role: str,
    block_type: str,
    label: str,
    names: list[str],
) -> str:
    del label
    return (
        f"role={_overview_role_label(role)} block_type={block_type} "
        f"instance_name={', '.join(names)}"
    )


def _overview_role_label(role: str) -> str:
    return {
        "variable_or_control": "control",
        "message_or_event": "message",
        "source": "source",
        "transform": "transform",
        "sink": "sink",
        "metadata": "metadata",
    }.get(role, role)


def _compact_inspect_targets(targets: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in _list(targets):
        if not isinstance(row, dict):
            continue
        rows.append(
            _drop_empty(
                {
                    "request": row.get("request"),
                    "instance_name": row.get("instance_name") or row.get("name"),
                    "block_type": row.get("block_type") or row.get("type"),
                    "label": row.get("catalog_label"),
                    "parameters": [
                        _compact_detail_param(param)
                        for param in _list(row.get("parameters"))
                        if isinstance(param, dict)
                    ],
                    "connections": _compact_connection_context(row.get("connections")),
                    "params_truncated": row.get("params_truncated"),
                    "omitted_param_count": row.get("omitted_param_count"),
                    "params_omitted": row.get("params_omitted"),
                    "more_params_available": row.get("more_params_available"),
                    "available_param_count": row.get("available_param_count"),
                }
            )
        )
    return rows


def _compact_detail_param(param: dict[str, Any]) -> dict[str, Any]:
    return _drop_empty(
        {
            "param_id": param.get("param_id") or param.get("name"),
            "name": param.get("name"),
            "label": param.get("label"),
            "value": param.get("value"),
            "resolved_value": param.get("resolved_value"),
            "dtype": param.get("dtype"),
            "value_label": param.get("value_label"),
        }
    )


def _compact_connection_context(value: Any) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        return {}
    return _drop_empty(
        {
            "incoming": _compact_connection_rows(value.get("incoming"), limit=8),
            "outgoing": _compact_connection_rows(value.get("outgoing"), limit=8),
        }
    )


def _compact_connection_rows(value: Any, *, limit: int) -> list[str]:
    rows: list[str] = []
    for item in _list(value)[:limit]:
        if isinstance(item, str):
            rows.append(item)
        elif isinstance(item, dict) and isinstance(item.get("connection_id"), str):
            rows.append(item["connection_id"])
    return rows


def _compact_target_matches(value: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in _list(value):
        if not isinstance(item, dict):
            continue
        rows.append(
            _drop_empty(
                {
                    "request": item.get("request"),
                    "status": item.get("status"),
                    "matched_by": item.get("matched_by"),
                    "instance_name": item.get("resolved_name") or item.get("instance_name") or item.get("name"),
                    "block_type": item.get("block_type") or item.get("type"),
                    "candidates": item.get("candidates"),
                }
            )
        )
    return rows


def _compact_params_filter(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return _drop_empty(
        {
            "requested": value.get("requested"),
            "matched": value.get("matched"),
            "unmatched": value.get("unmatched"),
        }
    )


def _render_inspect_overview_result(
    prefix_lines: list[str],
    compact: dict[str, Any],
) -> str:
    header_parts = [
        "overview",
        f"complete={compact.get('complete')}",
        f"rev={compact.get('state_revision')}",
    ]
    validation = compact.get("validation")
    if isinstance(validation, dict) and validation.get("status"):
        header_parts.append(f"validation={validation.get('status')}")
    rendered = list(prefix_lines)
    rendered.append(" ".join(str(part) for part in header_parts if part))
    truncation = compact.get("truncation")
    if isinstance(truncation, dict) and truncation.get("truncated"):
        omitted = truncation.get("omitted_counts")
        omitted_text = ""
        if isinstance(omitted, dict):
            omitted_text = ",".join(
                f"{key}+{value}" for key, value in omitted.items() if value
            )
        rendered.append(
            f"truncated: {omitted_text or truncation.get('reason') or True}"
        )

    summary = compact.get("summary")
    if isinstance(summary, dict):
        graph_name = summary.get("graph_name")
        counts = summary.get("counts")
        if graph_name or counts:
            rendered.append(
                " ".join(
                    str(part)
                    for part in (f"graph={graph_name}" if graph_name else "", counts)
                    if part
                )
            )
        blocks = _list(summary.get("blocks"))
        if blocks:
            rendered.append("blocks:")
            rendered.extend(f"- {item}" for item in blocks if item)
        connections = _list(summary.get("connections"))
        if connections:
            rendered.append("connections:")
            rendered.extend(f"- {item}" for item in connections if item)
        hint = summary.get("details_hint")
        if isinstance(hint, str) and hint:
            rendered.append(f"details: {hint}")
    return "\n".join(rendered)


def _render_change_graph_result(
    prefix_lines: list[str],
    compact: dict[str, Any],
) -> str:
    rendered = list(prefix_lines)
    parts = []
    for key, label in (
        ("committed", "committed"),
        ("rev", "rev"),
        ("validation", "validation"),
        ("autosave", "autosave"),
    ):
        value = compact.get(key)
        if is_meaningful(value):
            parts.append(f"{label}={value}")
    if parts:
        rendered.append(" ".join(parts))

    detail = _drop_empty(
        {
            "effect": compact.get("effect"),
            "effects": compact.get("effects"),
            "plan": compact.get("plan"),
            "graph_unchanged": compact.get("graph_unchanged"),
            "rollback": compact.get("rollback"),
            "rejected_phase": compact.get("rejected_phase"),
            "needs": compact.get("needs"),
            "errors": compact.get("errors"),
            "native_errors": compact.get("native_errors"),
            "error_type": compact.get("error_type"),
        }
    )
    if detail:
        rendered.append(json.dumps(detail, separators=(",", ":"), sort_keys=True))
    return "\n".join(rendered)


def _compact_search_blocks(content: dict[str, Any]) -> dict[str, Any]:
    if content.get("ok") is not True:
        return _common_result(content)
    result = {
        "ok": True,
        "results": [
            _drop_empty(
                {
                    "block_id": row.get("block_id"),
                    "catalog_label": row.get("catalog_label") or row.get("name"),
                    "match": row.get("match_type"),
                    "why": _short_text(row.get("why") or row.get("summary"), 100),
                    "catalog": _compact_catalog_facts(row.get("catalog"), index=index),
                }
            )
            for index, row in enumerate(_list(content.get("results")))
            if isinstance(row, dict)
        ],
    }
    if content.get("degraded_retrieval"):
        result["degraded_retrieval"] = True
    if content.get("output_truncated"):
        result["more"] = True
    return _drop_empty(result)


def _compact_ask_grc_docs(content: dict[str, Any]) -> dict[str, Any]:
    result = {
        "ok": content.get("ok"),
        "error_type": content.get("error_type"),
        "answer": _short_text(content.get("answer"), 360),
        "allowed_use": content.get("allowed_use"),
        "mutation_authority": content.get("mutation_authority"),
        "confidence": content.get("confidence"),
        "insufficient_evidence": content.get("insufficient_evidence"),
        "sources": [
            _drop_empty(
                {
                    "title": source.get("title"),
                    "excerpt": _short_text(source.get("excerpt"), 120),
                }
            )
            for source in _list(content.get("sources"))
            if isinstance(source, dict)
        ],
    }
    if content.get("ok") is not True:
        result["message"] = _short_text(content.get("message"), 160)
    for key in ("warnings", "degraded_retrieval"):
        if content.get(key):
            result[key] = content.get(key)
    return _drop_empty(result)


def _compact_change_graph(content: dict[str, Any]) -> dict[str, Any]:
    ok = content.get("ok")
    effect = content.get("effect")
    effects = content.get("effects")
    result = {
        "ok": ok,
        "committed": content.get("committed"),
        "rev": content.get("state_revision"),
        "effect": effect,
        "effects": effects,
        "plan": _operation_plan(content) if not (effect or effects) else None,
        "validation": _validation_status_value(content.get("validation_result")),
        "autosave": _autosave_status_value(content.get("autosave")),
        "error_type": content.get("error_type"),
        "message": _short_text(content.get("message"), 180) if ok is False else None,
        "hint": _short_text(content.get("hint"), 260),
        "needs": _compact_change_needs(content),
        "errors": _compact_error_rows(content.get("errors")),
        "validation_errors": _compact_error_rows(content.get("validation_errors")),
        "native_errors": _compact_native_validation_errors(
            content.get("native_validation_errors")
            if content.get("native_validation_errors")
            else content.get("validation_result")
        ),
        "graph_unchanged": content.get("graph_unchanged"),
        "rollback": content.get("rollback"),
        "rejected_phase": content.get("rejected_phase"),
        "repair": _compact_schema_repair(content.get("schema_repair_instruction")),
    }
    return _drop_empty(result)


def _compact_catalog_facts(value: Any, *, index: int) -> dict[str, Any]:
    """Keep just enough catalog truth for grounded add-block arguments."""
    if not isinstance(value, dict):
        return {}
    params = []
    for raw_param in _list(value.get("params"))[:6]:
        if not isinstance(raw_param, dict):
            continue
        param = _drop_empty(
            {
                "id": raw_param.get("id"),
                "label": raw_param.get("label"),
                "dtype": raw_param.get("dtype"),
                "default": raw_param.get("default"),
                "options": raw_param.get("options")[:6]
                if isinstance(raw_param.get("options"), list)
                else None,
            }
        )
        if param:
            params.append(param)
    ports = {}
    for direction in ("inputs", "outputs"):
        rows = []
        for raw_port in _list(value.get(direction))[:4]:
            if not isinstance(raw_port, dict):
                continue
            port = _drop_empty(
                {
                    "id": raw_port.get("id"),
                    "domain": raw_port.get("domain"),
                    "dtype": raw_port.get("dtype"),
                }
            )
            if port:
                rows.append(port)
        if rows:
            ports[direction] = rows
    return _drop_empty({"params": params, **ports})


def _compact_change_needs(content: dict[str, Any]) -> Any:
    attached = _list(content.get("attached_connection_ids"))
    if attached:
        return _drop_empty(
            {
                "attached": [str(item) for item in attached[:4] if str(item)],
            }
        )
    options = _list(content.get("clarification_options"))
    if not options:
        return None
    return [
        _short_text(str(item), 90) or str(item)
        for item in options[:3]
        if str(item)
    ]


def _compact_schema_repair(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return _drop_empty(
        {
            "missing_fields": value.get("missing_fields"),
            "invalid_fields": value.get("invalid_fields"),
            "hint": _short_text(value.get("change_graph_hint"), 240),
        }
    )


def _compact_native_validation_errors(value: Any) -> list[str]:
    if isinstance(value, list):
        return [_short_text(str(error), 220) for error in value[:3] if str(error)]
    if not isinstance(value, dict):
        return []
    native = value.get("native")
    if not isinstance(native, dict):
        return []
    errors = native.get("errors")
    if not isinstance(errors, list):
        return []
    return [_short_text(str(error), 220) for error in errors[:3] if str(error)]


def _operation_plan(content: dict[str, Any]) -> list[str]:
    effects = content.get("effects")
    if isinstance(effects, list):
        return [str(item) for item in effects if str(item)]
    effect = content.get("effect")
    if isinstance(effect, str) and effect:
        return [effect]
    rows: list[str] = []
    for operation in _list(content.get("planned_operations")):
        if isinstance(operation, dict):
            op_type = operation.get("op_type")
            if isinstance(op_type, str) and op_type:
                rows.append(op_type)
    return rows[:6]


def _validation_status_value(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    status = value.get("status")
    if isinstance(status, str) and status:
        return status
    valid = value.get("valid")
    if isinstance(valid, bool):
        return "valid" if valid else "invalid"
    return None


def _autosave_status_value(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    if value.get("ok") is True:
        return "ok"
    if value.get("skipped") is True:
        return "skipped"
    if value.get("ok") is False:
        return "failed"
    return None


def _compact_error_rows(value: Any) -> list[str]:
    rows: list[str] = []
    for item in _list(value)[:4]:
        if isinstance(item, dict):
            code = item.get("code")
            field = item.get("field")
            message = _short_text(item.get("message"), 100)
            parts = [str(part) for part in (code, field, message) if part]
            if parts:
                rows.append(": ".join(parts))
        elif isinstance(item, str) and item:
            rows.append(_short_text(item, 120) or item)
    return rows




def _validation_status_brief(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return _drop_empty(
        {
            "status": value.get("status"),
            "last_checked_revision": value.get("last_checked_revision"),
        }
    )


def _truncation_brief(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict) or not value.get("truncated"):
        return None
    return _drop_empty(
        {
            "truncated": True,
            "reason": value.get("reason"),
            "omitted_counts": value.get("omitted_counts"),
        }
    )



def _short_text(value: Any, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    return truncate_text(text, limit)


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _drop_empty(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if is_meaningful(item)}


# -- output policy (was output_policy.py) --


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


def is_variable_block(block_type: str) -> bool:
    if not isinstance(block_type, str):
        return False
    return block_type == "variable" or block_type.startswith("variable_")


def truncate_list(items: list[_T], max_items: int) -> tuple[list[_T], list[_T]]:
    if max_items < 0:
        raise ValueError("max_items must be >= 0")
    shown = items[:max_items]
    omitted = items[max_items:]
    return shown, omitted


def truncate_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    overrun = int(max_chars * 0.2)
    window = min(max_chars + overrun, len(text))
    for boundary in (". ", "? ", "! "):
        idx = text.rfind(boundary, max_chars, window)
        if idx != -1:
            return text[: idx + len(boundary)] + "…"
    idx = text.rfind(" ", max_chars, window)
    if idx != -1:
        return text[:idx] + " …"
    return text[:max_chars] + "…"


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
    "... [TRUNCATED by chat-history compactor: was {original} chars, "
    "kept {kept}]"
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
    sentinel = _TRUNCATION_SENTINEL_TEMPLATE.format(
        original=original_length, kept=max_chars - 1
    )
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
            _payload_length(c)
            for c in message.content
            if isinstance(c, ToolCallResultContent)
        )
        return full - payload_total

    shell_total = 0
    candidate_payloads: list[tuple[ChatMessage, ToolCallResultContent, int]] = []
    candidate_total = 0
    for message in messages:
        shell_total += _shell_length(message)
        for content in message.content:
            if (
                isinstance(content, ToolCallResultContent)
                and len(content.tool_call_result) > floor
            ):
                length = _payload_length(content)
                candidate_payloads.append((message, content, length))
                candidate_total += length

    if not candidate_payloads:
        return False

    total = shell_total + candidate_total
    if total <= budget_chars:
        return False

    mutable_budget = max(
        0, budget_chars - shell_total
    )
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


