"""insert_block operation helper for change_graph."""

from __future__ import annotations

from typing import Any, Callable

from .context import ChangeGraphOperationContext, ChangeGraphOperationResult, ToolResult


def handle_insert_block_on_connection(
    *,
    ctx: ChangeGraphOperationContext,
    resolved_insert_block_id: str,
    connection_id: str,
    instance_name: str | None,
    insert_params: dict[str, Any] | None,
    tx_tool: Callable[[Any], ToolResult],
    kind_mismatch_result: Callable[..., ToolResult | None],
) -> ChangeGraphOperationResult:
    """Handle insert_block on an existing connection."""

    operation_summary = "insert_block_on_connection"
    mismatch = kind_mismatch_result("insert_block")
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

    if ctx.dry_run:
        ctx.handlers.append("propose_edit(insert_block_on_connection)")
        tool_result = tx_tool(
            {
                "op_type": "insert_block_on_connection",
                "connection_id": connection_id,
                "block_type": resolved_insert_block_id,
                "instance_name": instance_name or f"{resolved_insert_block_id}_0",
                "params": insert_params or {},
            }
        )
    else:
        ctx.handlers.append("insert_block_on_connection")
        tool_result = ctx.agent._insert_block_on_connection(
            connection_id=connection_id,
            block_type=resolved_insert_block_id,
            instance_name=instance_name or f"{resolved_insert_block_id}_0",
            params=insert_params or {},
        )

    return ChangeGraphOperationResult(
        handled=True,
        operation_summary=operation_summary,
        tool_result=tool_result,
    )
