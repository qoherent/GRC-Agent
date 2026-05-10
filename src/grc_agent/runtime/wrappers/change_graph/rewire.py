"""rewire operation helper for change_graph."""

from __future__ import annotations

from typing import Any

from .context import ChangeGraphOperationContext, ChangeGraphOperationResult


def handle_rewire(
    *,
    ctx: ChangeGraphOperationContext,
    connection_id: str | None,
    src_block: str | None,
    src_port: int | str | None,
    dst_block: str | None,
    dst_port: int | str | None,
    new_src_block: str | None,
    new_src_port: int | str | None,
    new_dst_block: str | None,
    new_dst_port: int | str | None,
    kind_mismatch_result: Any,
) -> ChangeGraphOperationResult:
    """Handle rewire through the existing _rewire_connection path."""

    operation_summary = "rewire_connection"
    mismatch = kind_mismatch_result("rewire")
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

    ctx.handlers.append("rewire_connection")
    tool_result = ctx.agent._rewire_connection(
        old_connection_id=connection_id.strip()
        if isinstance(connection_id, str) and connection_id.strip()
        else None,
        old_src_block=src_block,
        old_src_port=src_port,
        old_dst_block=dst_block,
        old_dst_port=dst_port,
        new_src_block=new_src_block,
        new_src_port=new_src_port,
        new_dst_block=new_dst_block,
        new_dst_port=new_dst_port,
        dry_run=bool(ctx.dry_run),
    )
    return ChangeGraphOperationResult(
        handled=True,
        operation_summary=operation_summary,
        tool_result=tool_result,
    )
