"""Shared dispatcher for model-facing change_graph wrapper orchestration."""

from __future__ import annotations

import copy
import time
from typing import Any

from grc_agent._payload import ErrorCode
from grc_agent.runtime.capabilities import (
    change_graph_operation_kinds,
    get_capability_spec,
)
from grc_agent.session_ops import connection_id as render_connection_id, parse_connection_id

from .context import ChangeGraphOperationContext
from .disconnect import handle_disconnect_by_connection_id, handle_disconnect_by_endpoints
from .insert import handle_insert_block_on_connection
from .param import handle_set_param
from .remove import handle_remove_block
from .rewire import handle_rewire
from .state import handle_set_state
from .variable import handle_add_variable

ToolResult = dict[str, Any]


def dispatch_change_graph(
    agent: Any,
    *,
    dry_run: bool,
    user_goal: str,
    operation_kind: str | None = None,
    target_ref: dict[str, Any] | None = None,
    block_id: str | None = None,
    candidate_id: str | None = None,
    insert_block: str | None = None,
    instance_name: str | None = None,
    connection_id: str | None = None,
    src_block: str | None = None,
    src_port: int | str | None = None,
    dst_block: str | None = None,
    dst_port: int | str | None = None,
    state_revision: int | None = None,
    new_src_block: str | None = None,
    new_src_port: int | str | None = None,
    new_dst_block: str | None = None,
    new_dst_port: int | str | None = None,
    insert_params: dict[str, Any] | None = None,
    detach_connections: bool | None = None,
    detach_connection_ids: list[str] | None = None,
    param_key: str | None = None,
    param_value: Any = None,
    state: str | None = None,
    variable_name: str | None = None,
    variable_value: Any = None,
    debug: bool = False,
) -> ToolResult:
    """Execute the existing change_graph wrapper behavior via extracted dispatcher."""

    started = time.monotonic()
    before_revision = agent.session.state_revision
    before_dirty = agent.session.is_dirty

    def _block_names_snapshot() -> set[str]:
        flowgraph = agent.session.flowgraph
        if flowgraph is None:
            return set()
        return {
            block.instance_name
            for block in flowgraph.blocks
            if isinstance(block.instance_name, str)
        }

    def _connection_ids_snapshot() -> set[str]:
        flowgraph = agent.session.flowgraph
        if flowgraph is None:
            return set()
        return {
            render_connection_id(
                conn.src_block,
                conn.src_port,
                conn.dst_block,
                conn.dst_port,
            )
            for conn in flowgraph.connections
        }

    before_block_names = _block_names_snapshot()
    before_connection_ids = _connection_ids_snapshot()
    handlers: list[str] = []
    missing_session = agent._missing_session_result("change_graph")
    if missing_session is not None:
        return agent._attach_wrapper_dispatch_telemetry(
            debug=debug,
            wrapper_name="change_graph",
            wrapper_action="missing_session",
            internal_handlers=["none"],
            started=started,
            before_revision=before_revision,
            before_dirty=before_dirty,
            result=missing_session,
            validation_run=False,
            output_truncated=False,
        )
    if not isinstance(user_goal, str) or not user_goal.strip():
        result = agent._tool_result(
            "change_graph",
            ok=False,
            message="user_goal must be non-empty.",
            error_type=ErrorCode.INVALID_REQUEST,
        )
        return agent._attach_wrapper_dispatch_telemetry(
            debug=debug,
            wrapper_name="change_graph",
            wrapper_action="invalid_request",
            internal_handlers=["none"],
            started=started,
            before_revision=before_revision,
            before_dirty=before_dirty,
            result=result,
            validation_run=False,
            output_truncated=False,
        )
    allowed_operation_kinds = set(change_graph_operation_kinds())
    resolved_operation_kind = (
        operation_kind.strip() if isinstance(operation_kind, str) else None
    )
    if resolved_operation_kind == "":
        resolved_operation_kind = None
    if (
        resolved_operation_kind is not None
        and resolved_operation_kind not in allowed_operation_kinds
    ):
        result = agent._tool_result(
            "change_graph",
            ok=False,
            message=f"Unsupported change_graph operation_kind: {resolved_operation_kind!r}",
            error_type=ErrorCode.INVALID_REQUEST,
        )
        return agent._attach_wrapper_dispatch_telemetry(
            debug=debug,
            wrapper_name="change_graph",
            wrapper_action="invalid_operation_kind",
            internal_handlers=["none"],
            started=started,
            before_revision=before_revision,
            before_dirty=before_dirty,
            result=result,
            validation_run=False,
            output_truncated=False,
        )
    if resolved_operation_kind is not None:
        # Capability lookup is metadata-only and intentionally side-effect free.
        _ = get_capability_spec(resolved_operation_kind)
    if resolved_operation_kind == "insert_block":
        if isinstance(insert_params, dict):
            normalized_insert_params: dict[str, Any] = {}
            for key, value in insert_params.items():
                key_text = str(key).strip()
                if not key_text:
                    continue
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    normalized_insert_params[key_text] = str(value)
                else:
                    normalized_insert_params[key_text] = value
            insert_params = normalized_insert_params
        if (
            not (isinstance(block_id, str) and block_id.strip())
            and not (isinstance(candidate_id, str) and candidate_id.strip())
            and isinstance(insert_block, str)
            and insert_block.strip()
        ):
            candidate_id = insert_block.strip()
        if (
            not (isinstance(block_id, str) and block_id.strip())
            and not (isinstance(candidate_id, str) and candidate_id.strip())
            and isinstance(new_dst_block, str)
            and new_dst_block.strip()
        ):
            candidate_id = new_dst_block.strip()
            new_dst_block = None

        if isinstance(connection_id, str) and connection_id.strip():
            normalized_connection_id = connection_id.strip()
            if parse_connection_id(normalized_connection_id) is None and "->" in normalized_connection_id:
                left, right = normalized_connection_id.split("->", 1)
                src_block = ""
                src_port_value: int | str | None = None
                if ":" in left:
                    src_block, src_port_token = left.split(":", 1)
                    src_block = src_block.strip()
                    src_port_token = src_port_token.strip()
                    if src_port_token:
                        if src_port_token.isdigit():
                            src_port_value = int(src_port_token)
                        else:
                            src_port_value = src_port_token
                dst_block_hint = right.strip()
                if src_block and src_port_value is not None and dst_block_hint and ":" not in dst_block_hint:
                    candidates = agent.session.find_connection_candidates(
                        src_block=src_block,
                        src_port=src_port_value,
                        dst_block=dst_block_hint,
                        dst_port=None,
                    )
                    if len(candidates) == 1:
                        candidate = candidates[0]
                        connection_id = render_connection_id(
                            candidate.src_block,
                            candidate.src_port,
                            candidate.dst_block,
                            candidate.dst_port,
                        )
    canonical_target_ref, target_ref_error = agent._canonicalize_change_graph_target_ref(
        dry_run=bool(dry_run),
        operation_kind=resolved_operation_kind,
        target_ref=target_ref,
    )
    if target_ref_error is not None:
        return agent._attach_wrapper_dispatch_telemetry(
            debug=debug,
            wrapper_name="change_graph",
            wrapper_action="invalid_operation_args",
            internal_handlers=["none"],
            started=started,
            before_revision=before_revision,
            before_dirty=before_dirty,
            result=target_ref_error,
            validation_run=False,
            output_truncated=False,
        )
    target_ref = canonical_target_ref
    operation_args_error = agent._validate_change_graph_operation_args(
        dry_run=bool(dry_run),
        operation_kind=resolved_operation_kind,
        target_ref=target_ref,
        block_id=block_id,
        candidate_id=candidate_id,
        instance_name=instance_name,
        connection_id=connection_id,
        src_block=src_block,
        src_port=src_port,
        dst_block=dst_block,
        dst_port=dst_port,
        new_src_block=new_src_block,
        new_src_port=new_src_port,
        new_dst_block=new_dst_block,
        new_dst_port=new_dst_port,
        insert_params=insert_params,
        detach_connections=detach_connections,
        detach_connection_ids=detach_connection_ids,
        param_key=param_key,
        param_value=param_value,
        state=state,
        variable_name=variable_name,
        variable_value=variable_value,
    )
    if operation_args_error is not None:
        return agent._attach_wrapper_dispatch_telemetry(
            debug=debug,
            wrapper_name="change_graph",
            wrapper_action="invalid_operation_args",
            internal_handlers=["none"],
            started=started,
            before_revision=before_revision,
            before_dirty=before_dirty,
            result=operation_args_error,
            validation_run=False,
            output_truncated=False,
        )
    if state_revision is not None:
        if not isinstance(state_revision, int) or isinstance(state_revision, bool):
            stale_result = agent._payload_result(
                "change_graph",
                {
                    "ok": False,
                    "dry_run": bool(dry_run),
                    "operation_kind": resolved_operation_kind,
                    "error_type": ErrorCode.INVALID_REQUEST,
                    "message": "state_revision must be an integer when provided.",
                },
            )
            return agent._attach_wrapper_dispatch_telemetry(
                debug=debug,
                wrapper_name="change_graph",
                wrapper_action="invalid_operation_args",
                internal_handlers=["none"],
                started=started,
                before_revision=before_revision,
                before_dirty=before_dirty,
                result=stale_result,
                validation_run=False,
                output_truncated=False,
            )
        if state_revision != agent.session.state_revision:
            stale_result = agent._payload_result(
                "change_graph",
                {
                    "ok": False,
                    "dry_run": bool(dry_run),
                    "operation_kind": resolved_operation_kind,
                    "error_type": ErrorCode.STALE_REVISION,
                    "message": (
                        "state_revision is stale for the current graph. "
                        f"Provided {state_revision}, current is {agent.session.state_revision}."
                    ),
                    "state_revision": agent.session.state_revision,
                },
            )
            return agent._attach_wrapper_dispatch_telemetry(
                debug=debug,
                wrapper_name="change_graph",
                wrapper_action="stale_revision",
                internal_handlers=["none"],
                started=started,
                before_revision=before_revision,
                before_dirty=before_dirty,
                result=stale_result,
                validation_run=False,
                output_truncated=False,
            )
    if isinstance(target_ref, dict):
        target_ref_revision = target_ref.get("base_state_revision")
        if isinstance(target_ref_revision, int) and target_ref_revision != agent.session.state_revision:
            stale_result = agent._payload_result(
                "change_graph",
                {
                    "ok": False,
                    "dry_run": bool(dry_run),
                    "operation_kind": resolved_operation_kind,
                    "error_type": ErrorCode.STALE_REVISION,
                    "message": (
                        "target_ref.base_state_revision is stale for the current graph. "
                        f"Provided {target_ref_revision}, current is {agent.session.state_revision}."
                    ),
                    "state_revision": agent.session.state_revision,
                },
            )
            return agent._attach_wrapper_dispatch_telemetry(
                debug=debug,
                wrapper_name="change_graph",
                wrapper_action="stale_revision",
                internal_handlers=["none"],
                started=started,
                before_revision=before_revision,
                before_dirty=before_dirty,
                result=stale_result,
                validation_run=False,
                output_truncated=False,
            )
    if resolved_operation_kind == "rewire" and state_revision is None:
        stale_result = agent._payload_result(
            "change_graph",
            {
                "ok": False,
                "dry_run": bool(dry_run),
                "operation_kind": resolved_operation_kind,
                "error_type": ErrorCode.INVALID_REQUEST,
                "message": (
                    "rewire requires state_revision to guard against stale-edge execution. "
                    "Provide the current active state_revision from inspect_graph."
                ),
            },
        )
        return agent._attach_wrapper_dispatch_telemetry(
            debug=debug,
            wrapper_name="change_graph",
            wrapper_action="invalid_operation_args",
            internal_handlers=["none"],
            started=started,
            before_revision=before_revision,
            before_dirty=before_dirty,
            result=stale_result,
            validation_run=False,
            output_truncated=False,
        )
    lower_goal = user_goal.lower()
    if not dry_run and resolved_operation_kind is None:
        has_structured_mutation_args = any(
            (
                isinstance(target_ref, dict),
                isinstance(block_id, str) and bool(block_id.strip()),
                isinstance(instance_name, str) and bool(instance_name.strip()),
                isinstance(connection_id, str) and bool(connection_id.strip()),
                isinstance(src_block, str) and bool(src_block.strip()),
                src_port is not None,
                isinstance(dst_block, str) and bool(dst_block.strip()),
                dst_port is not None,
                isinstance(new_src_block, str) and bool(new_src_block.strip()),
                new_src_port is not None,
                isinstance(new_dst_block, str) and bool(new_dst_block.strip()),
                new_dst_port is not None,
                isinstance(param_key, str) and bool(param_key.strip()),
                param_value is not None,
                isinstance(state, str) and bool(state.strip()),
                isinstance(variable_name, str) and bool(variable_name.strip()),
                variable_value is not None,
            )
        )
        if has_structured_mutation_args:
            result = agent._payload_result(
                "change_graph",
                {
                    "ok": False,
                    "dry_run": bool(dry_run),
                    "error_type": "clarification_required",
                    "message": (
                        "Committed mutation requires operation_kind. "
                        "Provide one of: set_param, set_state, add_variable, "
                        "disconnect, rewire, insert_block, remove_block, auto_insert."
                    ),
                    "clarification_options": [
                        "Retry with operation_kind set to the intended mutation class.",
                        "Or set dry_run=true for a preview-only request.",
                    ],
                },
            )
            return agent._attach_wrapper_dispatch_telemetry(
                debug=debug,
                wrapper_name="change_graph",
                wrapper_action="missing_operation_kind",
                internal_handlers=["none"],
                started=started,
                before_revision=before_revision,
                before_dirty=before_dirty,
                result=result,
                validation_run=False,
                output_truncated=False,
            )
    if any(token in lower_goal for token in ("yaml", "undo", "redo", "export python", "source text")):
        resolved_operation_kind = resolved_operation_kind or "unsupported"
    if resolved_operation_kind == "unsupported":
        result = agent._payload_result(
            "change_graph",
            {
                "ok": False,
                "dry_run": bool(dry_run),
                "error_type": ErrorCode.UNSUPPORTED_OP,
                "message": "Unsupported workflow for change_graph.",
            },
        )
        return agent._attach_wrapper_dispatch_telemetry(
            debug=debug,
            wrapper_name="change_graph",
            wrapper_action="unsupported",
            internal_handlers=["none"],
            started=started,
            before_revision=before_revision,
            before_dirty=before_dirty,
            result=result,
            validation_run=False,
            output_truncated=False,
        )
    if resolved_operation_kind == "clarify":
        result = agent._payload_result(
            "change_graph",
            {
                "ok": False,
                "dry_run": bool(dry_run),
                "operation_kind": resolved_operation_kind,
                "error_type": "clarification_required",
                "message": "Clarification required before changing the graph.",
                "clarification_options": [
                    "Provide exact instance_name + param_key + param_value for param edits.",
                    "Provide exact connection_id (preferred) or endpoint hints for disconnect.",
                    "Provide exact block_id and placement details for inserts.",
                ],
            },
        )
        return agent._attach_wrapper_dispatch_telemetry(
            debug=debug,
            wrapper_name="change_graph",
            wrapper_action="clarify",
            internal_handlers=["clarification"],
            started=started,
            before_revision=before_revision,
            before_dirty=before_dirty,
            result=result,
            validation_run=False,
            output_truncated=False,
        )
    if "save" in lower_goal or "write out" in lower_goal:
        result = agent._payload_result(
            "change_graph",
            {
                "ok": False,
                "dry_run": bool(dry_run),
                "error_type": ErrorCode.UNSUPPORTED_OP,
                "message": (
                    "change_graph is mutation-only. Use save_graph_explicit for "
                    "explicit lifecycle save requests."
                ),
            },
        )
        return agent._attach_wrapper_dispatch_telemetry(
            debug=debug,
            wrapper_name="change_graph",
            wrapper_action="unsupported",
            internal_handlers=["none"],
            started=started,
            before_revision=before_revision,
            before_dirty=before_dirty,
            result=result,
            validation_run=False,
            output_truncated=False,
        )

    tx_tool = agent._propose_edit if dry_run else agent._apply_edit
    operation_summary = "clarification_required"
    result: dict[str, Any]
    operation_ctx = ChangeGraphOperationContext(
        agent=agent,
        debug=debug,
        started=started,
        before_revision=before_revision,
        before_dirty=before_dirty,
        dry_run=bool(dry_run),
        resolved_operation_kind=resolved_operation_kind,
        handlers=handlers,
    )

    def _kind_allows(*allowed: str) -> bool:
        return resolved_operation_kind is None or resolved_operation_kind in allowed

    def _kind_mismatch_result(*allowed: str) -> ToolResult | None:
        if _kind_allows(*allowed):
            return None
        return agent._payload_result(
            "change_graph",
            {
                "ok": False,
                "dry_run": bool(dry_run),
                "operation_kind": resolved_operation_kind,
                "error_type": ErrorCode.INVALID_REQUEST,
                "message": (
                    f"operation_kind={resolved_operation_kind!r} does not match "
                    f"the supplied arguments; expected one of {sorted(allowed)!r}."
                ),
            },
        )

    resolved_insert_block_id: str | None = None
    if isinstance(block_id, str) and block_id.strip():
        resolved_insert_block_id = block_id.strip()
    elif isinstance(candidate_id, str) and candidate_id.strip():
        resolved_insert_block_id = candidate_id.strip()

    rewire_old_hint = any(
        value is not None and (not isinstance(value, str) or value.strip())
        for value in (src_block, src_port, dst_block, dst_port)
    )
    if resolved_operation_kind == "rewire" and (
        (isinstance(connection_id, str) and connection_id.strip()) or rewire_old_hint
    ):
        rewire_dispatch = handle_rewire(
            ctx=operation_ctx,
            connection_id=connection_id,
            src_block=src_block,
            src_port=src_port,
            dst_block=dst_block,
            dst_port=dst_port,
            new_src_block=new_src_block,
            new_src_port=new_src_port,
            new_dst_block=new_dst_block,
            new_dst_port=new_dst_port,
            kind_mismatch_result=_kind_mismatch_result,
        )
        operation_summary = rewire_dispatch.operation_summary
        if rewire_dispatch.terminal_result is not None:
            return rewire_dispatch.terminal_result
        result = rewire_dispatch.tool_result if rewire_dispatch.tool_result is not None else {}
    elif (
        resolved_insert_block_id is not None
        and isinstance(connection_id, str)
        and connection_id.strip()
    ):
        insert_dispatch = handle_insert_block_on_connection(
            ctx=operation_ctx,
            resolved_insert_block_id=resolved_insert_block_id,
            connection_id=connection_id.strip(),
            instance_name=instance_name,
            insert_params=insert_params,
            tx_tool=tx_tool,
            kind_mismatch_result=_kind_mismatch_result,
        )
        operation_summary = insert_dispatch.operation_summary
        if insert_dispatch.terminal_result is not None:
            return insert_dispatch.terminal_result
        result = insert_dispatch.tool_result if insert_dispatch.tool_result is not None else {}
    elif isinstance(connection_id, str) and connection_id.strip():
        insertion_words = ("insert", "add", "compatible")
        if resolved_operation_kind == "auto_insert" or (
            resolved_operation_kind is None
            and any(word in lower_goal for word in insertion_words)
        ):
            operation_summary = "auto_insert_block"
            if dry_run:
                handlers.append("suggest_compatible_insertions")
                result = agent._suggest_compatible_insertions(connection_id=connection_id.strip())
            else:
                handlers.append("auto_insert_block")
                result = agent._auto_insert_block(
                    goal=user_goal,
                    preferred_block_type=block_id,
                    target_hint=connection_id.strip(),
                )
        else:
            disconnect_dispatch = handle_disconnect_by_connection_id(
                ctx=operation_ctx,
                connection_id=connection_id.strip(),
                tx_tool=tx_tool,
                kind_mismatch_result=_kind_mismatch_result,
            )
            operation_summary = disconnect_dispatch.operation_summary
            if disconnect_dispatch.terminal_result is not None:
                return disconnect_dispatch.terminal_result
            result = (
                disconnect_dispatch.tool_result
                if disconnect_dispatch.tool_result is not None
                else {}
            )
    elif (
        resolved_operation_kind == "disconnect"
        and any(
            value is not None and (not isinstance(value, str) or value.strip())
            for value in (src_block, src_port, dst_block, dst_port)
        )
    ):
        disconnect_dispatch = handle_disconnect_by_endpoints(
            ctx=operation_ctx,
            src_block=src_block,
            src_port=src_port,
            dst_block=dst_block,
            dst_port=dst_port,
            tx_tool=tx_tool,
        )
        operation_summary = disconnect_dispatch.operation_summary
        result = (
            disconnect_dispatch.tool_result
            if disconnect_dispatch.tool_result is not None
            else {}
        )
    elif isinstance(variable_name, str) and variable_name.strip() and variable_value is not None:
        add_variable_dispatch = handle_add_variable(
            ctx=operation_ctx,
            variable_name=variable_name,
            variable_value=variable_value,
            tx_tool=tx_tool,
            kind_mismatch_result=_kind_mismatch_result,
        )
        operation_summary = add_variable_dispatch.operation_summary
        if add_variable_dispatch.terminal_result is not None:
            return add_variable_dispatch.terminal_result
        result = (
            add_variable_dispatch.tool_result
            if add_variable_dispatch.tool_result is not None
            else {}
        )
    elif isinstance(param_key, str) and param_key.strip() and param_value is not None:
        set_param_dispatch = handle_set_param(
            ctx=operation_ctx,
            param_key=param_key,
            param_value=param_value,
            target_ref=target_ref,
            instance_name=instance_name,
            tx_tool=tx_tool,
            kind_mismatch_result=_kind_mismatch_result,
        )
        if set_param_dispatch.handled:
            operation_summary = set_param_dispatch.operation_summary
            if set_param_dispatch.terminal_result is not None:
                return set_param_dispatch.terminal_result
            result = (
                set_param_dispatch.tool_result
                if set_param_dispatch.tool_result is not None
                else {}
            )
    elif isinstance(state, str) and state in {"enabled", "disabled"}:
        set_state_dispatch = handle_set_state(
            ctx=operation_ctx,
            state=state,
            target_ref=target_ref,
            instance_name=instance_name,
            tx_tool=tx_tool,
            kind_mismatch_result=_kind_mismatch_result,
        )
        if set_state_dispatch.handled:
            operation_summary = set_state_dispatch.operation_summary
            if set_state_dispatch.terminal_result is not None:
                return set_state_dispatch.terminal_result
            result = (
                set_state_dispatch.tool_result
                if set_state_dispatch.tool_result is not None
                else {}
            )
    elif (
        resolved_operation_kind == "remove_block"
        and (
            isinstance(instance_name, str) and instance_name.strip()
            or isinstance(target_ref, dict)
        )
    ):
        remove_dispatch = handle_remove_block(
            ctx=operation_ctx,
            instance_name=instance_name,
            target_ref=target_ref,
            detach_connections=detach_connections,
            detach_connection_ids=detach_connection_ids,
            tx_tool=tx_tool,
            kind_mismatch_result=_kind_mismatch_result,
        )
        operation_summary = remove_dispatch.operation_summary
        if remove_dispatch.terminal_result is not None:
            return remove_dispatch.terminal_result
        result = remove_dispatch.tool_result if remove_dispatch.tool_result is not None else {}
    else:
        wrapper_result = agent._payload_result(
            "change_graph",
            {
                "ok": False,
                "dry_run": bool(dry_run),
                "error_type": "clarification_required",
                "message": "Not enough exact change details to execute safely.",
                "clarification_options": [
                    "Provide exact instance_name + param_key + param_value for param edits.",
                    "Provide exact connection_id (preferred) or endpoint hints for disconnect.",
                    "Provide exact rewire endpoints for rewiring.",
                ],
            },
        )
        return agent._attach_wrapper_dispatch_telemetry(
            debug=debug,
            wrapper_name="change_graph",
            wrapper_action=operation_summary,
            internal_handlers=handlers or ["clarification"],
            started=started,
            before_revision=before_revision,
            before_dirty=before_dirty,
            result=wrapper_result,
            validation_run=False,
            output_truncated=False,
        )

    validation_result = None
    if isinstance(result, dict):
        validation_result = result.get("validation")
    graph_delta = result.get("graph_delta") if isinstance(result, dict) else None
    if (
        graph_delta is None
        and isinstance(result, dict)
        and bool(result.get("ok"))
        and not bool(dry_run)
    ):
        after_block_names = _block_names_snapshot()
        after_connection_ids = _connection_ids_snapshot()
        synthesized_delta: dict[str, Any] = {}
        added_blocks = sorted(after_block_names - before_block_names)
        removed_blocks = sorted(before_block_names - after_block_names)
        added_connections = sorted(after_connection_ids - before_connection_ids)
        removed_connections = sorted(before_connection_ids - after_connection_ids)
        if added_blocks:
            synthesized_delta["added_blocks"] = added_blocks
        if removed_blocks:
            synthesized_delta["removed_blocks"] = removed_blocks
        if added_connections:
            synthesized_delta["added_connections"] = added_connections
        if removed_connections:
            synthesized_delta["removed_connections"] = removed_connections
        synthesized_delta["dirty"] = bool(agent.session.is_dirty)
        if isinstance(validation_result, dict):
            status = validation_result.get("status")
            returncode = validation_result.get("returncode")
            if status is not None:
                synthesized_delta["validation_status"] = status
            if returncode is not None:
                synthesized_delta["validation_returncode"] = returncode
        graph_delta = synthesized_delta
    payload: dict[str, Any] = {
        "ok": bool(result.get("ok")) if isinstance(result, dict) else False,
        "dry_run": bool(dry_run),
        "operation_kind": resolved_operation_kind,
        "operation_summary": operation_summary,
        "graph_delta": graph_delta,
        "validation_result": validation_result,
        "checkpoint_id": result.get("checkpoint_id") if isinstance(result, dict) else None,
        "message": result.get("message") if isinstance(result, dict) else "change_graph failed",
    }
    if isinstance(result, dict) and result.get("error_type"):
        payload["error_type"] = result.get("error_type")
    if isinstance(result, dict) and result.get("clarification_required"):
        payload["clarification_options"] = result.get("options")
    if isinstance(result, dict):
        normalized_operations = result.get("normalized_operations")
        if isinstance(normalized_operations, list):
            payload["planned_operations"] = copy.deepcopy(normalized_operations)
        errors = result.get("errors")
        if isinstance(errors, list):
            payload["errors"] = copy.deepcopy(errors)
        hint = result.get("hint")
        if isinstance(hint, str) and hint:
            payload["hint"] = hint
    wrapper_result = agent._payload_result("change_graph", payload)
    validation_run = bool(validation_result) or operation_summary in {
        "update_params",
        "update_states",
        "rewire_connection",
        "insert_block_on_connection",
        "add_variable",
    }
    return agent._attach_wrapper_dispatch_telemetry(
        debug=debug,
        wrapper_name="change_graph",
        wrapper_action=operation_summary,
        internal_handlers=handlers,
        started=started,
        before_revision=before_revision,
        before_dirty=before_dirty,
        result=wrapper_result,
        validation_run=validation_run,
        output_truncated=False,
    )
