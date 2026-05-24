"""Shared dispatcher for model-facing change_graph wrapper orchestration."""

from __future__ import annotations

import copy
import time
from typing import Any

from grc_agent._payload import ErrorCode
from grc_agent.runtime.capabilities import (
    CAPABILITY_SPECS,
    EXPERIMENTAL_OPERATION_SPECS,
    CONTROL_OUTCOME_KINDS,
    change_graph_operation_kinds,
    get_capability_spec,
)
from grc_agent.runtime.editable_parameters import resolve_set_param_candidate
from grc_agent.session_ops import connection_id as render_connection_id, parse_connection_id

from .context import ChangeGraphOperationContext
from .disconnect import handle_disconnect_by_connection_id, handle_disconnect_by_endpoints
from .insert import handle_insert_block_on_connection
from .param import handle_set_param
from .remove import handle_remove_block
from .rewire import handle_rewire
from .source_to_sum import handle_add_signal_source_to_sum
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
    operation_args: dict[str, Any] | None = None,
    detach_connections: bool | None = None,
    detach_connection_ids: list[str] | None = None,
    param_key: str | None = None,
    param_value: Any = None,
    expected_old_value: Any = None,
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
            message=f"Unsupported change_graph op: {resolved_operation_kind!r}",
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
    if resolved_operation_kind in CAPABILITY_SPECS:
        # Mutable capability lookup is metadata-only and intentionally side-effect free.
        _ = get_capability_spec(resolved_operation_kind)
    elif (
        resolved_operation_kind is not None
        and resolved_operation_kind not in CONTROL_OUTCOME_KINDS
        and resolved_operation_kind in EXPERIMENTAL_OPERATION_SPECS
    ):
        # Internal non-gating operation metadata path.
        _ = EXPERIMENTAL_OPERATION_SPECS[resolved_operation_kind]
    operation_args = operation_args if isinstance(operation_args, dict) else {}
    if resolved_operation_kind == "insert_in_connection":
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
                    candidates_payload = agent.session.find_connection_candidates(
                        src_block=src_block,
                        src_port=src_port_value,
                        dst_block=dst_block_hint,
                        dst_port=None,
                    )
                    candidates = candidates_payload.get("candidates", [])
                    if len(candidates) == 1:
                        candidate = candidates[0]
                        connection_id = render_connection_id(
                            candidate["src_block"],
                            candidate["src_port"],
                            candidate["dst_block"],
                            candidate["dst_port"],
                        )
    target_resolution: dict[str, Any] | None = None
    set_param_resolution = resolve_set_param_candidate(
        user_text=user_goal,
        session=agent.session,
        catalog_root=agent.catalog_root,
        operation_kind=resolved_operation_kind,
        instance_name=instance_name,
        param_key=param_key,
        param_value=param_value,
        target_ref=target_ref,
        expected_old_value=expected_old_value,
    )
    instance_name = set_param_resolution.instance_name
    param_key = set_param_resolution.param_key
    param_value = set_param_resolution.param_value
    target_ref = set_param_resolution.target_ref
    expected_old_value = set_param_resolution.expected_old_value
    target_resolution = set_param_resolution.target_resolution
    if set_param_resolution.clarification is not None:
        clarification_payload = copy.deepcopy(set_param_resolution.clarification)
        clarification_payload["dry_run"] = bool(dry_run)
        clarification_payload["operation_kind"] = resolved_operation_kind
        if target_resolution is not None:
            clarification_payload["target_resolution"] = copy.deepcopy(target_resolution)
        result = agent._payload_result("change_graph", clarification_payload)
        return agent._attach_wrapper_dispatch_telemetry(
            debug=debug,
            wrapper_name="change_graph",
            wrapper_action="target_resolution_clarification",
            internal_handlers=["editable_parameter_resolution"],
            started=started,
            before_revision=before_revision,
            before_dirty=before_dirty,
            result=result,
            validation_run=False,
            output_truncated=False,
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
        operation_args=operation_args,
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
    target_ref_revision = (
        target_ref.get("base_state_revision") if isinstance(target_ref, dict) else None
    )
    if (
        resolved_operation_kind == "add_signal_source_to_sum"
        and not dry_run
        and state_revision is None
        and not isinstance(target_ref_revision, int)
    ):
        stale_result = agent._payload_result(
            "change_graph",
            {
                "ok": False,
                "dry_run": bool(dry_run),
                "operation_kind": resolved_operation_kind,
                "error_type": ErrorCode.INVALID_REQUEST,
                "message": (
                    "add_signal_source_to_sum commits require state_revision or a "
                    "target_ref.base_state_revision to guard against stale structural edits. "
                    "Use the current state_revision from inspect_graph or a preview result."
                ),
                "state_revision": agent.session.state_revision,
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
    if not dry_run:
        file_integrity = agent.session.file_integrity_state()
        if file_integrity.get("externally_modified"):
            stale_result = agent._payload_result(
                "change_graph",
                {
                    "ok": False,
                    "dry_run": bool(dry_run),
                    "operation_kind": resolved_operation_kind,
                    "error_type": ErrorCode.STALE_REVISION,
                    "message": (
                        "The active graph file changed on disk after this session "
                        "loaded or saved it. Reload the graph before committing."
                    ),
                    "file_integrity": _compact_file_integrity(file_integrity),
                    "state_revision": agent.session.state_revision,
                },
            )
            return agent._attach_wrapper_dispatch_telemetry(
                debug=debug,
                wrapper_name="change_graph",
                wrapper_action="external_file_changed",
                internal_handlers=["file_integrity_guard"],
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
                        "Committed mutation requires op. "
                        "Provide one of: set_param, set_state, add_variable, "
                            "disconnect, rewire, insert_in_connection, remove_block."
                    ),
                    "clarification_options": [
                        (
                            "Retry with op set to the intended mutation class "
                            "and preserve all exact fields from this call, including "
                            "connection_id, block_id, instance_name, and insert_params "
                            "when they were supplied."
                        ),
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
                    "Provide exact block_id and placement details for add/insert operations.",
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
                    "change_graph is mutation-only. The CLI /save command handles "
                    "manual saves outside the model tool surface."
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

    rewire_old_hint = any(
        value is not None and (not isinstance(value, str) or value.strip())
        for value in (src_block, src_port, dst_block, dst_port)
    )
    if resolved_operation_kind == "add_signal_source_to_sum":
        signal_source_dispatch = handle_add_signal_source_to_sum(
            ctx=operation_ctx,
            user_goal=user_goal,
            target_ref=target_ref,
            block_id=resolved_insert_block_id,
            instance_name=instance_name,
            dst_block=dst_block,
            dst_port=dst_port,
            insert_params=insert_params,
            operation_args=operation_args,
            tx_tool=tx_tool,
            kind_mismatch_result=_kind_mismatch_result,
        )
        operation_summary = signal_source_dispatch.operation_summary
        if signal_source_dispatch.terminal_result is not None:
            return signal_source_dispatch.terminal_result
        result = signal_source_dispatch.tool_result if signal_source_dispatch.tool_result is not None else {}
    elif resolved_operation_kind == "rewire" and (
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
        resolved_operation_kind == "insert_in_connection"
        and
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
            expected_old_value=expected_old_value,
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
                    "Use add_signal_source_to_sum for another compatible source into an existing summing input.",
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
    ok = bool(result.get("ok")) if isinstance(result, dict) else False
    normalized_operations = (
        result.get("normalized_operations") if isinstance(result, dict) else None
    )
    operation_effects = _operation_effects(normalized_operations)
    payload: dict[str, Any] = _drop_empty_result_fields(
        {
            "ok": ok,
            "dry_run": bool(dry_run),
            "committed": ok and not bool(dry_run),
            "operation_kind": resolved_operation_kind,
            "operation_summary": operation_summary,
            "state_revision": agent.session.state_revision,
            "effect": operation_effects[0] if len(operation_effects) == 1 else None,
            "effects": operation_effects if len(operation_effects) > 1 else None,
            "graph_delta": graph_delta,
            "validation_result": validation_result,
            "autosave": result.get("autosave") if isinstance(result, dict) else None,
            "assumptions": result.get("assumptions") if isinstance(result, dict) else None,
            "preview_token": result.get("preview_token") if isinstance(result, dict) else None,
            "commit_hint": result.get("commit_hint") if isinstance(result, dict) else None,
            "checkpoint_id": result.get("checkpoint_id") if isinstance(result, dict) else None,
            "message": result.get("message") if isinstance(result, dict) else "change_graph failed",
        }
    )
    if target_resolution is not None:
        payload["target_resolution"] = copy.deepcopy(target_resolution)
    if isinstance(result, dict) and result.get("error_type"):
        payload["error_type"] = result.get("error_type")
    if isinstance(result, dict) and result.get("clarification_required"):
        payload["clarification_options"] = result.get("options")
    if isinstance(result, dict):
        if isinstance(normalized_operations, list) and (bool(dry_run) or not ok):
            payload["planned_operations"] = copy.deepcopy(normalized_operations)
        errors = result.get("errors")
        if isinstance(errors, list) and errors:
            payload["errors"] = copy.deepcopy(errors)
        hint = result.get("hint")
        if isinstance(hint, str) and hint and not ok:
            payload["hint"] = hint
    wrapper_result = agent._payload_result("change_graph", payload)
    validation_run = bool(validation_result) or operation_summary in {
        "update_params",
        "update_states",
        "rewire_connection",
        "insert_block_on_connection",
        "add_variable",
        "add_signal_source_to_sum",
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


def _drop_empty_result_fields(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in payload.items()
        if value not in (None, "", [], {})
    }


def _compact_file_integrity(file_integrity: dict[str, Any]) -> dict[str, Any]:
    def _short_hash(value: Any) -> str | None:
        return value[:12] if isinstance(value, str) and value else None

    compact = {
        "status": file_integrity.get("status"),
        "path": file_integrity.get("path"),
        "persisted_sha256": _short_hash(file_integrity.get("persisted_sha256")),
        "current_sha256": _short_hash(file_integrity.get("current_sha256")),
    }
    return _drop_empty_result_fields(compact)


def _operation_effects(operations: Any) -> list[str]:
    if not isinstance(operations, list):
        return []
    effects: list[str] = []
    for operation in operations:
        if not isinstance(operation, dict):
            continue
        effect = _operation_effect(operation)
        if effect:
            effects.append(effect)
    return effects


def _operation_effect(operation: dict[str, Any]) -> str | None:
    op_type = str(operation.get("op_type") or "").strip()
    if op_type == "update_params":
        target = _operation_target_name(operation)
        params = operation.get("params")
        expected = operation.get("expected_params")
        if target and isinstance(params, dict) and params:
            parts = []
            for key, value in params.items():
                if isinstance(expected, dict) and key in expected:
                    parts.append(f"{target}.{key}:{expected[key]}->{value}")
                else:
                    parts.append(f"{target}.{key}={value}")
            return "; ".join(parts)
    if op_type == "update_states":
        target = _operation_target_name(operation)
        state = operation.get("state")
        if target and state is not None:
            return f"{target}.state={state}"
    if op_type == "add_block":
        block_type = operation.get("block_type")
        name = operation.get("instance_name")
        params = operation.get("parameters")
        if block_type == "variable" and name:
            value = params.get("value") if isinstance(params, dict) else None
            return f"add variable {name}={value}" if value is not None else f"add variable {name}"
        if block_type and name:
            return f"add {block_type} as {name}"
    if op_type == "remove_block":
        target = _operation_target_name(operation)
        return f"remove {target}" if target else "remove block"
    if op_type == "remove_connection":
        connection_id = operation.get("connection_id")
        if isinstance(connection_id, str) and connection_id:
            return f"disconnect {connection_id}"
        rendered = _render_operation_connection(operation)
        return f"disconnect {rendered}" if rendered else "disconnect"
    if op_type == "add_connection":
        rendered = _render_operation_connection(operation)
        return f"connect {rendered}" if rendered else "connect"
    if op_type == "insert_block_on_connection":
        block_type = operation.get("block_type")
        name = operation.get("instance_name")
        connection_id = operation.get("connection_id")
        if block_type and connection_id:
            alias = f" as {name}" if name else ""
            return f"insert {block_type}{alias} on {connection_id}"
    return op_type or None


def _operation_target_name(operation: dict[str, Any]) -> str | None:
    instance_name = operation.get("instance_name")
    if isinstance(instance_name, str) and instance_name:
        return instance_name
    target_ref = operation.get("target_ref")
    if isinstance(target_ref, dict):
        expected_name = target_ref.get("expected_instance_name")
        if isinstance(expected_name, str) and expected_name:
            return expected_name
    return None


def _render_operation_connection(operation: dict[str, Any]) -> str | None:
    src_block = operation.get("src_block")
    src_port = operation.get("src_port")
    dst_block = operation.get("dst_block")
    dst_port = operation.get("dst_port")
    if src_block is None or src_port is None or dst_block is None or dst_port is None:
        return None
    return render_connection_id(str(src_block), src_port, str(dst_block), dst_port)
