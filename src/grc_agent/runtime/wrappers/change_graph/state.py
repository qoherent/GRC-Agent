"""set_state operation helper for change_graph."""

from __future__ import annotations

from typing import Any, Callable

from .context import ChangeGraphOperationContext, ChangeGraphOperationResult, ToolResult


def handle_set_state(
    *,
    ctx: ChangeGraphOperationContext,
    state: str | None,
    target_ref: dict[str, Any] | None,
    instance_name: str | None,
    tx_tool: Callable[[Any], ToolResult],
    kind_mismatch_result: Callable[..., ToolResult | None],
) -> ChangeGraphOperationResult:
    """Attempt to handle a set_state mutation without changing wrapper semantics."""

    if not (isinstance(state, str) and state in {"enabled", "disabled"}):
        return ChangeGraphOperationResult(handled=False, operation_summary="clarification_required")

    operation_summary = "update_states"
    mismatch = kind_mismatch_result("set_state")
    if mismatch is not None:
        terminal_result = ctx.agent._attach_wrapper_dispatch_telemetry(
            debug=ctx.debug,
            wrapper_name="change_graph",
            wrapper_action="operation_kind_mismatch",
            internal_handlers=["none"],
            started=ctx.started,
            before_revision=ctx.before_revision,
            before_dirty=ctx.before_dirty,
            result=mismatch,
            validation_run=False,
            output_truncated=False,
        )
        return ChangeGraphOperationResult(
            handled=True,
            operation_summary=operation_summary,
            terminal_result=terminal_result,
        )

    ctx.handlers.append("propose_edit" if ctx.dry_run else "apply_edit")
    if isinstance(target_ref, dict):
        tool_result = tx_tool({"op_type": "update_states", "target_ref": target_ref, "state": state})
        return ChangeGraphOperationResult(
            handled=True,
            operation_summary=operation_summary,
            tool_result=tool_result,
        )

    if isinstance(instance_name, str) and instance_name.strip():
        tool_result = tx_tool(
            {
                "op_type": "update_states",
                "instance_name": instance_name.strip(),
                "state": state,
            }
        )
        return ChangeGraphOperationResult(
            handled=True,
            operation_summary=operation_summary,
            tool_result=tool_result,
        )

    result = ctx.agent._payload_result(
        "change_graph",
        {
            "ok": False,
            "dry_run": bool(ctx.dry_run),
            "error_type": "clarification_required",
            "message": "Missing target block for state update.",
            "clarification_options": ["Provide exact instance_name."],
        },
    )
    terminal_result = ctx.agent._attach_wrapper_dispatch_telemetry(
        debug=ctx.debug,
        wrapper_name="change_graph",
        wrapper_action=operation_summary,
        internal_handlers=ctx.handlers,
        started=ctx.started,
        before_revision=ctx.before_revision,
        before_dirty=ctx.before_dirty,
        result=result,
        validation_run=False,
        output_truncated=False,
    )
    return ChangeGraphOperationResult(
        handled=True,
        operation_summary=operation_summary,
        terminal_result=terminal_result,
    )
