"""add_variable operation helper for change_graph."""

from __future__ import annotations

from typing import Any, Callable

from grc_agent._payload import ErrorCode

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

    normalized_name = variable_name.strip()
    flowgraph = ctx.agent.session.flowgraph
    if flowgraph is not None and any(
        isinstance(block.instance_name, str) and block.instance_name == normalized_name
        for block in flowgraph.blocks
    ):
        result = ctx.agent._payload_result(
            "change_graph",
            {
                "ok": False,
                "dry_run": bool(ctx.dry_run),
                "operation_kind": "add_variable",
                "error_type": ErrorCode.BLOCK_ALREADY_EXISTS,
                "message": (
                    f"Variable `{normalized_name}` already exists. add_variable only creates "
                    "new variables; use set_param on the existing variable to change its value."
                ),
            },
        )
        terminal_result = ctx.agent._attach_wrapper_dispatch_telemetry(
            debug=ctx.debug,
            wrapper_name="change_graph",
            wrapper_action=operation_summary,
            internal_handlers=ctx.handlers or ["none"],
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

    ctx.handlers.append("propose_edit" if ctx.dry_run else "apply_edit")
    tool_result = tx_tool(
        {
            "op_type": "add_block",
            "block_type": "variable",
            "instance_name": normalized_name,
            "parameters": {"value": variable_value},
        }
    )
    return ChangeGraphOperationResult(
        handled=True,
        operation_summary=operation_summary,
        tool_result=tool_result,
    )
