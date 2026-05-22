"""set_param operation helper for change_graph."""

from __future__ import annotations

from typing import Any, Callable

from .context import ChangeGraphOperationContext, ChangeGraphOperationResult, ToolResult


def handle_set_param(
    *,
    ctx: ChangeGraphOperationContext,
    param_key: str | None,
    param_value: Any,
    expected_old_value: Any,
    target_ref: dict[str, Any] | None,
    instance_name: str | None,
    tx_tool: Callable[[Any], ToolResult],
    kind_mismatch_result: Callable[..., ToolResult | None],
) -> ChangeGraphOperationResult:
    """Attempt to handle a set_param mutation without changing wrapper semantics."""

    if not (isinstance(param_key, str) and param_key.strip() and param_value is not None):
        return ChangeGraphOperationResult(handled=False, operation_summary="clarification_required")

    operation_summary = "update_params"
    mismatch = kind_mismatch_result("set_param")
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
    expected_params = (
        {param_key.strip(): expected_old_value}
        if expected_old_value is not None
        else None
    )
    if isinstance(target_ref, dict):
        operation = {
            "op_type": "update_params",
            "target_ref": target_ref,
            "instance_name": target_ref.get("expected_instance_name"),
            "params": {param_key.strip(): param_value},
        }
        if expected_params is not None:
            operation["expected_params"] = expected_params
        tool_result = tx_tool(operation)
        return ChangeGraphOperationResult(
            handled=True,
            operation_summary=operation_summary,
            tool_result=tool_result,
        )

    if isinstance(instance_name, str) and instance_name.strip():
        operation = {
            "op_type": "update_params",
            "instance_name": instance_name.strip(),
            "params": {param_key.strip(): param_value},
        }
        if expected_params is not None:
            operation["expected_params"] = expected_params
        tool_result = tx_tool(operation)
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
            "message": "Missing target block for parameter update.",
            "clarification_options": [
                "Provide exact instance_name.",
                "Or provide guarded target_ref from a prior clarification.",
            ],
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
