"""add_variable operation helper for change_graph."""

from __future__ import annotations

from typing import Any, Callable

from .context import ChangeGraphOperationContext, ChangeGraphOperationResult, ToolResult


def handle_add_variable(
    *,
    ctx: ChangeGraphOperationContext,
    variable_name: str | None,
    variable_value: Any,
    tx_tool: Callable[[Any], ToolResult],
    kind_mismatch_result: Callable[..., ToolResult | None],
) -> ChangeGraphOperationResult:
    """Handle add_variable through transaction apply/propose path."""

    if not (isinstance(variable_name, str) and variable_name.strip() and variable_value is not None):
        return ChangeGraphOperationResult(handled=False, operation_summary="clarification_required")

    operation_summary = "add_variable"
    mismatch = kind_mismatch_result("add_variable")
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
    tool_result = tx_tool(
        {
            "op_type": "add_block",
            "block_type": "variable",
            "instance_name": variable_name.strip(),
            "parameters": {"value": variable_value},
        }
    )
    return ChangeGraphOperationResult(
        handled=True,
        operation_summary=operation_summary,
        tool_result=tool_result,
    )
