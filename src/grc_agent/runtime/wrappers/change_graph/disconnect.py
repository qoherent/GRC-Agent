"""disconnect operation helpers for change_graph."""

from __future__ import annotations

from typing import Any, Callable

from .context import ChangeGraphOperationContext, ChangeGraphOperationResult, ToolResult


def handle_disconnect_by_connection_id(
    *,
    ctx: ChangeGraphOperationContext,
    connection_id: str,
    tx_tool: Callable[[Any], ToolResult],
    kind_mismatch_result: Callable[..., ToolResult | None],
) -> ChangeGraphOperationResult:
    """Handle disconnect by exact connection_id."""

    operation_summary = "remove_connection"
    mismatch = kind_mismatch_result("disconnect")
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
        ctx.handlers.append("propose_edit(remove_connection)")
        tool_result = tx_tool(
            {
                "op_type": "remove_connection",
                "connection_id": connection_id,
            }
        )
    else:
        ctx.handlers.append("remove_connection")
        tool_result = ctx.agent._remove_connection(connection_id=connection_id)
    return ChangeGraphOperationResult(
        handled=True,
        operation_summary=operation_summary,
        tool_result=tool_result,
    )


def handle_disconnect_by_endpoints(
    *,
    ctx: ChangeGraphOperationContext,
    src_block: str | None,
    src_port: int | str | None,
    dst_block: str | None,
    dst_port: int | str | None,
    tx_tool: Callable[[Any], ToolResult],
) -> ChangeGraphOperationResult:
    """Handle disconnect by endpoint hints."""

    operation_summary = "remove_connection"
    if ctx.dry_run:
        ctx.handlers.append("propose_edit(remove_connection)")
        tool_result = tx_tool(
            {
                "op_type": "remove_connection",
                "src_block": src_block,
                "src_port": src_port,
                "dst_block": dst_block,
                "dst_port": dst_port,
            }
        )
    else:
        ctx.handlers.append("remove_connection")
        tool_result = ctx.agent._remove_connection(
            src_block=src_block,
            src_port=src_port,
            dst_block=dst_block,
            dst_port=dst_port,
        )
    return ChangeGraphOperationResult(
        handled=True,
        operation_summary=operation_summary,
        tool_result=tool_result,
    )
