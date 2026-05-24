"""Tool-history compaction and model-visible formatting helpers."""

from __future__ import annotations

import json
from typing import Any, Callable

HistoryEntry = dict[str, Any]
PreviewCallback = Callable[..., list[dict[str, Any]]]
_MODEL_WRAPPER_NAMES = {
    "inspect_graph",
    "search_blocks",
    "ask_grc_docs",
    "change_graph",
}


def compact_tool_entry(
    turn: HistoryEntry,
    *,
    semantic_search_result_preview: PreviewCallback,
) -> HistoryEntry:
    content = turn.get("content")
    if not isinstance(content, dict):
        return turn
    tool_name = turn.get("name")
    if isinstance(tool_name, str) and tool_name in _MODEL_WRAPPER_NAMES:
        return {
            "role": turn.get("role"),
            "tool_call_id": turn.get("tool_call_id"),
            "name": tool_name,
            "content": _compact_wrapper_result(tool_name, content),
        }
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
    if tool_name == "summarize_graph":
        summary = content.get("summary")
        if isinstance(summary, str) and summary:
            compact["summary"] = summary
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
    if tool_name == "get_grc_context":
        lines.append(
            "next_step_note: inspection data is routing only; do not answer later edit or preview requests from it."
        )
    if tool_name == "semantic_search_grc":
        lines.append(
            "next_step_note: semantic search is read-only candidate discovery; it cannot authorize edits, saves, insertions, removals, or repairs."
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
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        block_type = str(row.get("type") or "unknown").strip() or "unknown"
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
    return f"{_overview_role_label(role)} {block_type}: {', '.join(names)}"


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
                    "name": row.get("name"),
                    "type": row.get("type"),
                    "label": row.get("catalog_label"),
                    "target_ref": row.get("target_ref"),
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
                    "name": item.get("resolved_name") or item.get("name"),
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
        ("op", "op"),
        ("dry_run", "dry_run"),
        ("committed", "committed"),
        ("rev", "rev"),
        ("preview_token", "preview_token"),
        ("validation", "validation"),
        ("autosave", "autosave"),
    ):
        value = compact.get(key)
        if value not in (None, "", [], {}):
            parts.append(f"{label}={value}")
    if parts:
        rendered.append(" ".join(parts))

    detail = _drop_empty(
        {
            "effect": compact.get("effect"),
            "effects": compact.get("effects"),
            "plan": compact.get("plan"),
            "changed": compact.get("changed"),
            "needs": compact.get("needs"),
            "errors": compact.get("errors"),
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
                    "name": row.get("name"),
                    "match": row.get("match_type"),
                    "why": _short_text(row.get("summary"), 80),
                }
            )
            for row in _list(content.get("results"))
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
        "op": content.get("operation_kind") or content.get("operation_summary"),
        "dry_run": content.get("dry_run"),
        "committed": content.get("committed"),
        "rev": content.get("state_revision"),
        "preview_token": content.get("preview_token"),
        "effect": effect,
        "effects": effects,
        "plan": _operation_plan(content)
        if content.get("dry_run") is True and not (effect or effects)
        else None,
        "validation": _validation_status_value(content.get("validation_result")),
        "autosave": _autosave_status_value(content.get("autosave")),
        "error_type": content.get("error_type"),
        "message": _short_text(content.get("message"), 180) if ok is False else None,
        "needs": _compact_change_needs(content),
        "errors": _compact_error_rows(content.get("errors")),
    }
    return _drop_empty(result)


def _compact_change_needs(content: dict[str, Any]) -> Any:
    attached = _list(content.get("attached_connection_ids"))
    if attached:
        return _drop_empty(
            {
                "confirm": "retry with args.detach_connections=true",
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


def _operation_plan(content: dict[str, Any]) -> list[str]:
    effects = content.get("effects")
    if isinstance(effects, list):
        return [str(item) for item in effects if str(item)]
    effect = content.get("effect")
    if isinstance(effect, str) and effect:
        return [effect] if content.get("dry_run") is True else []
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


def _autosave_brief(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return _drop_empty(
        {
            "ok": value.get("ok"),
            "skipped": value.get("skipped"),
            "path": value.get("path"),
            "dirty": value.get("dirty"),
            "error_type": value.get("error_type"),
            "message": _short_text(value.get("message"), 160),
        }
    )


def _graph_delta_brief(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    keys = (
        "changed",
        "added_blocks",
        "removed_blocks",
        "changed_blocks",
        "added_connections",
        "removed_connections",
        "validation_status",
        "validation_returncode",
    )
    return _drop_empty({key: value.get(key) for key in keys})


def _validation_result_brief(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return _drop_empty(
        {
            "valid": value.get("valid"),
            "status": value.get("status"),
            "returncode": value.get("returncode"),
        }
    )


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


def _active_session_brief(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    validation = value.get("validation")
    return _drop_empty(
        {
            "path": value.get("path"),
            "state_revision": value.get("state_revision"),
            "dirty": value.get("dirty"),
            "validation": _validation_result_brief(validation),
        }
    )


def _short_text(value: Any, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _drop_empty(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item
        for key, item in value.items()
        if item not in (None, "", [], {})
    }
