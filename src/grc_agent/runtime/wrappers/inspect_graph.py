"""inspect_graph wrapper implementation extracted from GrcAgent."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from grc_agent._payload import ErrorCode
from grc_agent.session import summarize_graph

if TYPE_CHECKING:
    from grc_agent.agent import GrcAgent, ToolResult


def inspect_graph(
    agent: "GrcAgent",
    operation: str,
    target: str | None = None,
    max_items: int | None = None,
    debug: bool = False,
) -> "ToolResult":
    started = time.monotonic()
    before_revision = agent.session.state_revision
    before_dirty = agent.session.is_dirty
    op = str(operation).strip().lower()
    handlers: list[str] = []
    output_truncated = False
    if op == "summarize":
        handlers.append("summarize_graph")
        summary_limit = (
            max_items
            if isinstance(max_items, int) and max_items > 0
            else agent._guardrails_cfg.max_graph_summary_blocks
        )
        payload = summarize_graph(agent.session, max_blocks=summary_limit)
        output_truncated = bool(payload.get("blocks_truncated", 0))
        result = agent._payload_result("inspect_graph", agent._compact_inspect_payload(op, payload))
        return agent._attach_wrapper_dispatch_telemetry(
            debug=debug,
            wrapper_name="inspect_graph",
            wrapper_action=op,
            internal_handlers=handlers,
            started=started,
            before_revision=before_revision,
            before_dirty=before_dirty,
            result=result,
            validation_run=False,
            output_truncated=output_truncated,
        )
    if op == "validate":
        handlers.append("validate_graph")
        result = agent._validate_graph()
        wrapper_result = agent._payload_result(
            "inspect_graph",
            {
                "ok": bool(result.get("ok")),
                "operation": op,
                "valid": bool(result.get("valid")),
                "message": result.get("message"),
                "error_type": result.get("error_type"),
                "validation_result": {
                    "valid": bool(result.get("valid")),
                    "returncode": result.get("returncode"),
                    "stderr": result.get("stderr"),
                },
            },
        )
        return agent._attach_wrapper_dispatch_telemetry(
            debug=debug,
            wrapper_name="inspect_graph",
            wrapper_action=op,
            internal_handlers=handlers,
            started=started,
            before_revision=before_revision,
            before_dirty=before_dirty,
            result=wrapper_result,
            validation_run=True,
            output_truncated=False,
        )
    if op == "context":
        if not isinstance(target, str) or not target.strip():
            result = agent._tool_result(
                "inspect_graph",
                ok=False,
                message="context requires target.",
                error_type=ErrorCode.INVALID_REQUEST,
            )
            return agent._attach_wrapper_dispatch_telemetry(
                debug=debug,
                wrapper_name="inspect_graph",
                wrapper_action=op,
                internal_handlers=["none"],
                started=started,
                before_revision=before_revision,
                before_dirty=before_dirty,
                result=result,
                validation_run=False,
                output_truncated=False,
            )
        handlers.append("get_grc_context")
        payload = agent._get_grc_context(
            target.strip(),
            max_nodes=max_items or agent._guardrails_cfg.max_context_nodes,
        )
        output_truncated = bool(payload.get("truncated"))
        result = agent._payload_result("inspect_graph", agent._compact_inspect_payload(op, payload))
        return agent._attach_wrapper_dispatch_telemetry(
            debug=debug,
            wrapper_name="inspect_graph",
            wrapper_action=op,
            internal_handlers=handlers,
            started=started,
            before_revision=before_revision,
            before_dirty=before_dirty,
            result=result,
            validation_run=False,
            output_truncated=output_truncated,
        )

    missing_session = agent._missing_session_result("inspect_graph")
    if missing_session is not None:
        return agent._attach_wrapper_dispatch_telemetry(
            debug=debug,
            wrapper_name="inspect_graph",
            wrapper_action=op,
            internal_handlers=["none"],
            started=started,
            before_revision=before_revision,
            before_dirty=before_dirty,
            result=missing_session,
            validation_run=False,
            output_truncated=False,
        )

    snapshot = agent.active_session_snapshot() or {}
    if op == "list_blocks":
        handlers.append("session_snapshot.list_blocks")
        items = list((snapshot.get("block_preview") or []))
        total_items = len(items)
        if isinstance(max_items, int) and max_items > 0:
            items = items[:max_items]
        output_truncated = len(items) < total_items
        result = agent._payload_result(
            "inspect_graph",
            {"ok": True, "operation": op, "items": items},
        )
        return agent._attach_wrapper_dispatch_telemetry(
            debug=debug,
            wrapper_name="inspect_graph",
            wrapper_action=op,
            internal_handlers=handlers,
            started=started,
            before_revision=before_revision,
            before_dirty=before_dirty,
            result=result,
            validation_run=False,
            output_truncated=output_truncated,
        )
    if op == "list_connections":
        handlers.append("session_snapshot.list_connections")
        items = list((snapshot.get("connection_preview") or []))
        total_items = len(items)
        if isinstance(max_items, int) and max_items > 0:
            items = items[:max_items]
        output_truncated = len(items) < total_items
        result = agent._payload_result(
            "inspect_graph",
            {"ok": True, "operation": op, "items": items},
        )
        return agent._attach_wrapper_dispatch_telemetry(
            debug=debug,
            wrapper_name="inspect_graph",
            wrapper_action=op,
            internal_handlers=handlers,
            started=started,
            before_revision=before_revision,
            before_dirty=before_dirty,
            result=result,
            validation_run=False,
            output_truncated=output_truncated,
        )
    if op == "list_variables":
        handlers.append("session_snapshot.list_variables")
        items = list((snapshot.get("variable_preview") or []))
        total_items = len(items)
        if isinstance(max_items, int) and max_items > 0:
            items = items[:max_items]
        output_truncated = len(items) < total_items
        result = agent._payload_result(
            "inspect_graph",
            {"ok": True, "operation": op, "items": items},
        )
        return agent._attach_wrapper_dispatch_telemetry(
            debug=debug,
            wrapper_name="inspect_graph",
            wrapper_action=op,
            internal_handlers=handlers,
            started=started,
            before_revision=before_revision,
            before_dirty=before_dirty,
            result=result,
            validation_run=False,
            output_truncated=output_truncated,
        )
    if op == "history_summary":
        handlers.append("history_journal.list_records")
        records = agent._history_journal.list_records()
        if isinstance(agent._history_lineage_key, str):
            records = [
                record
                for record in records
                if record.get("lineage_key") == agent._history_lineage_key
            ]
        if isinstance(max_items, int) and max_items > 0:
            records = records[-max_items:]
        else:
            records = records[-10:]
        compact = [
            {
                "id": record.get("id"),
                "kind": record.get("record_type"),
                "tool_name": record.get("tool_name"),
                "operation_type": record.get("operation_type"),
                "state_revision": record.get("state_revision"),
                "timestamp": record.get("timestamp"),
            }
            for record in records
        ]
        result = agent._payload_result(
            "inspect_graph",
            {"ok": True, "operation": op, "items": compact},
        )
        return agent._attach_wrapper_dispatch_telemetry(
            debug=debug,
            wrapper_name="inspect_graph",
            wrapper_action=op,
            internal_handlers=handlers,
            started=started,
            before_revision=before_revision,
            before_dirty=before_dirty,
            result=result,
            validation_run=False,
            output_truncated=False,
        )
    result = agent._tool_result(
        "inspect_graph",
        ok=False,
        message=f"Unsupported inspect_graph operation: {operation!r}",
        error_type=ErrorCode.INVALID_REQUEST,
    )
    return agent._attach_wrapper_dispatch_telemetry(
        debug=debug,
        wrapper_name="inspect_graph",
        wrapper_action=op,
        internal_handlers=["none"],
        started=started,
        before_revision=before_revision,
        before_dirty=before_dirty,
        result=result,
        validation_run=False,
        output_truncated=False,
    )
