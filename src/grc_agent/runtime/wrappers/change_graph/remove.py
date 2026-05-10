"""remove_block operation helper for change_graph."""

from __future__ import annotations

from typing import Any, Callable

from grc_agent._payload import ErrorCode
from grc_agent.session_ops import connection_id as render_connection_id

from .context import ChangeGraphOperationContext, ChangeGraphOperationResult, ToolResult


def _build_remove_block_operation(
    *,
    resolved_instance_name: str,
    target_ref: dict[str, Any] | None,
) -> dict[str, Any]:
    operation: dict[str, Any] = {
        "op_type": "remove_block",
        "instance_name": resolved_instance_name,
    }
    if isinstance(target_ref, dict):
        operation["target_ref"] = target_ref
    return operation


def _resolve_remove_block_target_name(
    *,
    ctx: ChangeGraphOperationContext,
    instance_name: str | None,
    target_ref: dict[str, Any] | None,
) -> str | None:
    if isinstance(target_ref, dict):
        resolved = ctx.agent.session.resolve_block_reference(
            instance_name=target_ref.get("expected_instance_name"),
            block_uid=target_ref.get("block_uid"),
            block_type=target_ref.get("expected_block_type"),
        )
        candidates = resolved.get("candidates") if isinstance(resolved, dict) else None
        if isinstance(candidates, list) and len(candidates) == 1:
            candidate = candidates[0]
            if isinstance(candidate, dict):
                name = candidate.get("name")
                if isinstance(name, str) and name.strip():
                    return name.strip()
        return None

    if isinstance(instance_name, str) and instance_name.strip():
        resolved = ctx.agent.session.resolve_block_reference(instance_name=instance_name.strip())
        candidates = resolved.get("candidates") if isinstance(resolved, dict) else None
        if isinstance(candidates, list) and len(candidates) == 1:
            candidate = candidates[0]
            if isinstance(candidate, dict):
                name = candidate.get("name")
                if isinstance(name, str) and name.strip():
                    return name.strip()
    return None


def _attached_connection_ids_for_block(*, ctx: ChangeGraphOperationContext, block_name: str) -> list[str]:
    flowgraph = ctx.agent.session.flowgraph
    if flowgraph is None:
        return []
    attached: list[str] = []
    for connection in flowgraph.connections:
        if connection.src_block != block_name and connection.dst_block != block_name:
            continue
        attached.append(
            render_connection_id(
                connection.src_block,
                connection.src_port,
                connection.dst_block,
                connection.dst_port,
            )
        )
    return sorted(attached)


def _normalize_connection_id_list(values: list[str] | None) -> list[str]:
    if not isinstance(values, list):
        return []
    normalized: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        token = value.strip()
        if token:
            normalized.append(token)
    return sorted(dict.fromkeys(normalized))


def handle_remove_block(
    *,
    ctx: ChangeGraphOperationContext,
    instance_name: str | None,
    target_ref: dict[str, Any] | None,
    detach_connections: bool | None,
    detach_connection_ids: list[str] | None,
    tx_tool: Callable[[Any], ToolResult],
    kind_mismatch_result: Callable[..., ToolResult | None],
) -> ChangeGraphOperationResult:
    """Handle remove_block including attached-connection clarification semantics."""

    operation_summary = "remove_block"
    mismatch = kind_mismatch_result("remove_block")
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

    resolved_target_name = _resolve_remove_block_target_name(
        ctx=ctx,
        instance_name=instance_name,
        target_ref=target_ref,
    )
    fallback_target_name = ""
    if isinstance(instance_name, str) and instance_name.strip():
        fallback_target_name = instance_name.strip()
    elif isinstance(target_ref, dict):
        expected_name = target_ref.get("expected_instance_name")
        if isinstance(expected_name, str) and expected_name.strip():
            fallback_target_name = expected_name.strip()

    explicit_remove_operation = _build_remove_block_operation(
        resolved_instance_name=resolved_target_name or fallback_target_name,
        target_ref=target_ref,
    )

    if resolved_target_name is not None:
        attached_connection_ids = _attached_connection_ids_for_block(
            ctx=ctx,
            block_name=resolved_target_name,
        )
        if attached_connection_ids:
            provided_detach_ids = _normalize_connection_id_list(detach_connection_ids)
            explicit_detach_requested = bool(detach_connections) or (
                bool(provided_detach_ids)
                and provided_detach_ids == attached_connection_ids
            )
            if not explicit_detach_requested:
                clarification_options = [
                    (
                        "Preview exact detach+remove plan by retrying change_graph "
                        "with operation_kind='remove_block', dry_run=true, "
                        "detach_connections=true, and the same target."
                    ),
                    (
                        "Commit exact detach+remove by retrying with "
                        "dry_run=false and detach_connections=true."
                    ),
                ]
                if attached_connection_ids:
                    clarification_options.append(
                        "Attached connections: " + ", ".join(attached_connection_ids)
                    )
                result = ctx.agent._payload_result(
                    "change_graph",
                    {
                        "ok": False,
                        "dry_run": bool(ctx.dry_run),
                        "operation_kind": ctx.resolved_operation_kind,
                        "error_type": "clarification_required",
                        "message": (
                            f"Block '{resolved_target_name}' is connected. "
                            "Explicit detach confirmation is required before remove_block commit."
                        ),
                        "clarification_options": clarification_options,
                        "attached_connection_ids": attached_connection_ids,
                        "planned_operations": [
                            {"op_type": "remove_connection", "connection_id": connection_id}
                            for connection_id in attached_connection_ids
                        ]
                        + [explicit_remove_operation],
                        "state_revision": ctx.agent.session.state_revision,
                    },
                )
                terminal_result = ctx.agent._attach_wrapper_dispatch_telemetry(
                    debug=ctx.debug,
                    wrapper_name="change_graph",
                    wrapper_action=operation_summary,
                    internal_handlers=["clarification"],
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

            if provided_detach_ids and provided_detach_ids != attached_connection_ids:
                result = ctx.agent._payload_result(
                    "change_graph",
                    {
                        "ok": False,
                        "dry_run": bool(ctx.dry_run),
                        "operation_kind": ctx.resolved_operation_kind,
                        "error_type": ErrorCode.INVALID_REQUEST,
                        "message": (
                            "detach_connection_ids do not match the current attached "
                            "connections for this block."
                        ),
                        "attached_connection_ids": attached_connection_ids,
                    },
                )
                terminal_result = ctx.agent._attach_wrapper_dispatch_telemetry(
                    debug=ctx.debug,
                    wrapper_name="change_graph",
                    wrapper_action="invalid_operation_args",
                    internal_handlers=["none"],
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

            ordered_operations = [
                {"op_type": "remove_connection", "connection_id": connection_id}
                for connection_id in attached_connection_ids
            ]
            ordered_operations.append(explicit_remove_operation)
            ctx.handlers.append("propose_edit" if ctx.dry_run else "apply_edit")
            tool_result = tx_tool(ordered_operations)
            return ChangeGraphOperationResult(
                handled=True,
                operation_summary=operation_summary,
                tool_result=tool_result,
            )

        ctx.handlers.append("propose_edit" if ctx.dry_run else "apply_edit")
        tool_result = tx_tool(explicit_remove_operation)
        return ChangeGraphOperationResult(
            handled=True,
            operation_summary=operation_summary,
            tool_result=tool_result,
        )

    ctx.handlers.append("propose_edit" if ctx.dry_run else "apply_edit")
    tool_result = tx_tool(explicit_remove_operation)
    return ChangeGraphOperationResult(
        handled=True,
        operation_summary=operation_summary,
        tool_result=tool_result,
    )
