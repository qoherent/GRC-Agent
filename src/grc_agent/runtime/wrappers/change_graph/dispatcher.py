"""Shared dispatcher for model-facing change_graph wrapper orchestration."""

from __future__ import annotations

import copy
import re
import time
from typing import Any

from grc_agent._payload import ErrorCode
from grc_agent.catalog import describe_block
from grc_agent.runtime.block_semantics import _block_semantics
from grc_agent.session_ops import connection_id as render_connection_id, parse_connection_id

ToolResult = dict[str, Any]


def _parse_transaction_endpoint(value: Any) -> tuple[str, int | str] | None:
    if isinstance(value, str):
        text = value.strip()
        if ":" not in text:
            return None
        block, port = text.rsplit(":", 1)
        block = block.strip()
        port = port.strip()
        if not block or not port:
            return None
        return block, int(port) if port.isdigit() else port
    if isinstance(value, dict):
        block = value.get("block")
        port = value.get("port")
        if not isinstance(block, str) or not block.strip():
            return None
        if isinstance(port, int) and not isinstance(port, bool) and port >= 0:
            return block.strip(), port
        if isinstance(port, str) and port.strip():
            port_text = port.strip()
            return block.strip(), int(port_text) if port_text.isdigit() else port_text
    return None


_FLAT_BATCH_FIELDS = {
    "add_blocks",
    "remove_blocks",
    "update_params",
    "update_states",
    "add_connections",
    "remove_connections",
}


def has_flat_change_graph_batch(kwargs: dict[str, Any]) -> bool:
    """Return true when args use the new flat model-facing batch surface."""
    return any(key in kwargs for key in _FLAT_BATCH_FIELDS)


def dispatch_flat_change_graph_batch(
    agent: Any,
    *,
    add_blocks: Any = None,
    remove_blocks: Any = None,
    update_params: Any = None,
    update_states: Any = None,
    add_connections: Any = None,
    remove_connections: Any = None,
    force: bool = False,
    debug: bool = False,
) -> ToolResult:
    """Execute the flat model-facing batch edit surface."""
    started = time.monotonic()
    before_revision = agent.session.state_revision
    before_dirty = agent.session.is_dirty
    before_block_names = _flat_block_names_snapshot(agent)
    before_connection_ids = _flat_connection_ids_snapshot(agent)

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

    before_graph_id = agent.session.graph_id()
    file_integrity = agent.session.file_integrity_state()
    if file_integrity.get("externally_modified"):
        stale_result = agent._payload_result(
            "change_graph",
            {
                "ok": False,
                "committed": False,
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

    normalized_operations, errors = _normalize_flat_change_graph_batch(
        agent,
        add_blocks=add_blocks,
        remove_blocks=remove_blocks,
        update_params=update_params,
        update_states=update_states,
        add_connections=add_connections,
        remove_connections=remove_connections,
    )
    if errors:
        result = agent._payload_result(
            "change_graph",
            {
                "ok": False,
                "committed": False,
                "error_type": ErrorCode.INVALID_REQUEST,
                "message": errors[0],
                "errors": [
                    {"message": message, "code": "invalid_flat_batch"}
                    for message in errors
                ],
                "state_revision": agent.session.state_revision,
            },
        )
        return agent._attach_wrapper_dispatch_telemetry(
            debug=debug,
            wrapper_name="change_graph",
            wrapper_action="invalid_flat_batch",
            internal_handlers=["flat_batch_normalizer"],
            started=started,
            before_revision=before_revision,
            before_dirty=before_dirty,
            result=result,
            validation_run=False,
            output_truncated=False,
        )

    result = agent._apply_edit(normalized_operations, force_validation=bool(force))
    validation_result = result.get("validation") if isinstance(result, dict) else None
    graph_delta = result.get("graph_delta") if isinstance(result, dict) else None
    ok = bool(result.get("ok")) if isinstance(result, dict) else False
    if graph_delta is None and ok:
        graph_delta = _synthesized_flat_delta(
            agent,
            before_block_names=before_block_names,
            before_connection_ids=before_connection_ids,
            validation_result=validation_result,
        )
    operation_effects = _operation_effects(normalized_operations)
    payload: dict[str, Any] = _drop_empty_result_fields(
        {
            "ok": ok,
            "committed": ok,
            "operation_summary": "batch",
            "state_revision": agent.session.state_revision,
            "effect": operation_effects[0] if len(operation_effects) == 1 else None,
            "effects": operation_effects if len(operation_effects) > 1 else None,
            "graph_delta": graph_delta,
            "validation_result": validation_result,
            "validation_ok": result.get("validation_ok") if isinstance(result, dict) else None,
            "autosave": result.get("autosave") if isinstance(result, dict) else None,
            "checkpoint_id": result.get("checkpoint_id") if isinstance(result, dict) else None,
            "message": result.get("message") if isinstance(result, dict) else "change_graph failed",
        }
    )
    if ok:
        payload["system_directive"] = (
            "SUCCESS. The graph has been updated and validated. "
            "STOP calling tools. Output a final text response explaining what was done."
        )
    if isinstance(result, dict):
        if result.get("forced_validation_failure"):
            payload["forced_validation_failure"] = result.get("forced_validation_failure")
        if result.get("error_type"):
            payload["error_type"] = result.get("error_type")
        errors_payload = result.get("errors")
        if isinstance(errors_payload, list) and errors_payload:
            payload["errors"] = copy.deepcopy(errors_payload)
        warnings = result.get("warnings")
        if isinstance(warnings, list) and warnings:
            payload["warnings"] = copy.deepcopy(warnings)
        if not ok:
            payload["planned_operations"] = copy.deepcopy(normalized_operations)
            after_graph_id = agent.session.graph_id()
            graph_unchanged = (
                after_graph_id == before_graph_id
                and agent.session.state_revision == before_revision
                and agent.session.is_dirty == before_dirty
            )
            payload["graph_unchanged"] = graph_unchanged
            payload["rollback"] = "complete" if graph_unchanged else "unknown"
            if result.get("error_type") == ErrorCode.GNU_VALIDATION_FAILED:
                payload["rejected_phase"] = "native_grc_validation"
                payload["message"] = (
                    "Candidate graph rejected by native GRC validation; "
                    "no changes committed. "
                    "Note: change_graph validates the entire graph atomically. "
                    "If multiple independent errors exist, you must fix all "
                    "of them in a single batch payload, or use force=true "
                    "for intermediate steps."
                )
            native_errors = (
                _native_validation_error_text(validation_result)
                if isinstance(validation_result, dict)
                else []
            )
            if native_errors:
                payload["native_validation_errors"] = native_errors
            hint = _aggregate_hints(
                agent=agent,
                operations=normalized_operations,
                validation_result=validation_result,
                errors_payload=errors_payload,
            )
            if isinstance(hint, str) and hint:
                payload["hint"] = hint
    wrapper_result = agent._payload_result("change_graph", payload)
    return agent._attach_wrapper_dispatch_telemetry(
        debug=debug,
        wrapper_name="change_graph",
        wrapper_action="batch",
        internal_handlers=["flat_batch_normalizer", "apply_edit"],
        started=started,
        before_revision=before_revision,
        before_dirty=before_dirty,
        result=wrapper_result,
        validation_run=True,
        output_truncated=False,
    )


def _normalize_flat_change_graph_batch(agent: Any, **batches: Any) -> tuple[list[dict[str, Any]], list[str]]:
    errors: list[str] = []
    operations: list[dict[str, Any]] = []
    removed_connection_ids: set[str] = set()
    added_block_aliases: dict[str, str | None] = {}

    remove_connections = _as_list(batches.get("remove_connections"), "remove_connections", errors)
    remove_blocks = _as_list(batches.get("remove_blocks"), "remove_blocks", errors)
    add_blocks = _as_list(batches.get("add_blocks"), "add_blocks", errors)
    update_params = _as_list(batches.get("update_params"), "update_params", errors)
    update_states = _as_list(batches.get("update_states"), "update_states", errors)
    add_connections = _as_list(batches.get("add_connections"), "add_connections", errors)

    if errors:
        return [], errors

    for index, item in enumerate(remove_connections):
        connection_id = _connection_id_from_remove_item(item)
        if connection_id is None:
            errors.append(f"remove_connections[{index}] must be a connection_id string.")
            continue
        _append_remove_connection(operations, removed_connection_ids, connection_id, errors, f"remove_connections[{index}]")

    for index, item in enumerate(remove_blocks):
        op = _remove_block_operation(item, index=index, field_name="remove_blocks", errors=errors)
        if op is None:
            continue
        block_name = _operation_target_name(op)
        if block_name:
            for connection_id in _incident_connection_ids(agent, block_name):
                _append_remove_connection(
                    operations,
                    removed_connection_ids,
                    connection_id,
                    errors,
                    f"remove_blocks[{index}].auto_detach",
                )
        operations.append(op)

    for index, item in enumerate(add_blocks):
        if not isinstance(item, dict):
            errors.append(f"add_blocks[{index}] must be an object.")
            continue
        block_id = _required_string(item, "block_id", f"add_blocks[{index}]", errors)
        instance_name = _required_string(item, "instance_name", f"add_blocks[{index}]", errors)
        if block_id is None or instance_name is None:
            continue
        params = item.get("params", {})
        state = item.get("state")
        if not isinstance(params, dict):
            errors.append(f"add_blocks[{index}].params must be an object when provided.")
            continue
        if state is not None and not isinstance(state, str):
            errors.append(f"add_blocks[{index}].state must be a string when provided.")
            continue
        op = {
            "op_type": "add_block",
            "block_type": block_id,
            "instance_name": instance_name,
            "parameters": copy.deepcopy(params),
        }
        if isinstance(state, str) and state.strip():
            op["states"] = {"state": state.strip()}
        operations.append(op)
        if block_id in added_block_aliases:
            added_block_aliases[block_id] = None
        else:
            added_block_aliases[block_id] = instance_name

    for index, item in enumerate(update_params):
        op = _update_params_operation(item, index=index, field_name="update_params", errors=errors)
        if op is not None:
            operations.append(op)

    for index, item in enumerate(update_states):
        op = _update_state_operation(item, index=index, field_name="update_states", errors=errors)
        if op is not None:
            operations.append(op)

    for index, item in enumerate(add_connections):
        if not isinstance(item, dict):
            errors.append(f"add_connections[{index}] must be an object.")
            continue
        src = _parse_transaction_endpoint(item.get("src"))
        dst = _parse_transaction_endpoint(item.get("dst"))
        if src is None or dst is None:
            errors.append(f"add_connections[{index}] requires src and dst endpoints.")
            continue
        src = _resolve_added_block_endpoint_alias(src, added_block_aliases)
        dst = _resolve_added_block_endpoint_alias(dst, added_block_aliases)
        operations.append(
            {
                "op_type": "add_connection",
                "src_block": src[0],
                "src_port": src[1],
                "dst_block": dst[0],
                "dst_port": dst[1],
            }
        )

    if not operations and not errors:
        errors.append("change_graph requires at least one non-empty edit list.")
    return operations, errors


def _first_error_hint(errors_payload: Any) -> str | None:
    if not isinstance(errors_payload, list):
        return None
    for row in errors_payload:
        if not isinstance(row, dict):
            continue
        hint = row.get("hint")
        if isinstance(hint, str) and hint.strip():
            return hint.strip()
    return None


def _aggregate_hints(
    *,
    agent: Any,
    operations: list[dict[str, Any]],
    validation_result: Any,
    errors_payload: Any,
) -> str | None:
    """Collect all applicable hints instead of short-circuiting on the first match."""
    hints: list[str] = []

    bypass_hint = _bypass_hint(
        agent=agent,
        operations=operations,
        validation_result=validation_result,
        errors_payload=errors_payload,
    )
    if bypass_hint:
        hints.append(bypass_hint)

    port_hint = _port_discovery_hint(agent, operations, errors_payload)
    if port_hint:
        hints.append(port_hint)

    occupancy_hint = _port_occupancy_hint(errors_payload)
    if occupancy_hint:
        hints.append(occupancy_hint)

    ofdm_hint = _ofdm_carrier_hint(operations, errors_payload)
    if ofdm_hint:
        hints.append(ofdm_hint)

    repair_hint = _repair_hint_for_validation_failure(operations, validation_result)
    if repair_hint:
        hints.append(repair_hint)

    # Check for preflight incompatible_dtype error to produce the same precise config hint
    if isinstance(errors_payload, list):
        for err in errors_payload:
            if not isinstance(err, dict):
                continue
            if err.get("code") == "incompatible_dtype" and err.get("message"):
                msg = err["message"]
                match = re.search(
                    r"Source IO type \"([^\"]+)\" does not match sink IO type \"([^\"]+)\" connecting ([a-zA-Z0-9_]+)\(([^)]+)\) to ([a-zA-Z0-9_]+)\(([^)]+)\)",
                    msg
                )
                if match:
                    src_dt = match.group(1)
                    dst_dt = match.group(2)
                    src_name = match.group(3)
                    src_port_val = match.group(4)
                    dst_name = match.group(5)
                    dst_port_val = match.group(6)
                else:
                    match = re.search(
                        r"Cannot connect ([a-zA-Z0-9_]+)\(([^)]+)\) \(([^)]+)\) to ([a-zA-Z0-9_]+)\(([^)]+)\) \(([^)]+)\)",
                        msg
                    )
                    if match:
                        src_name = match.group(1)
                        src_port_val = match.group(2)
                        src_dt = match.group(3)
                        dst_name = match.group(4)
                        dst_port_val = match.group(5)
                        dst_dt = match.group(6)
                    else:
                        # Also try matching existing connection invalid pattern
                        match = re.search(
                            r"Existing connection became invalid: ([a-zA-Z0-9_]+)\(([^)]+)\) \(([^)]+)\) -> ([a-zA-Z0-9_]+)\(([^)]+)\) \(([^)]+)\)",
                            msg
                        )
                        if match:
                            src_name = match.group(1)
                            src_port_val = match.group(2)
                            src_dt = match.group(3)
                            dst_name = match.group(4)
                            dst_port_val = match.group(5)
                            dst_dt = match.group(6)
                if match:
                    try:
                        src_idx = int(src_port_val)
                    except ValueError:
                        src_idx = src_port_val
                        
                    try:
                        dst_idx = int(dst_port_val)
                    except ValueError:
                        dst_idx = dst_port_val

                    added_blocks = {
                        str(op.get("instance_name")): op
                        for op in operations
                        if isinstance(op, dict)
                        and op.get("op_type") in {"add_block", "insert_block_on_connection"}
                        and isinstance(op.get("instance_name"), str)
                    }

                    preflight_hint = None
                    if dst_name in added_blocks:
                        preflight_hint = _dtype_param_hint_for_added_block(
                            added_blocks[dst_name],
                            port_direction="inputs",
                            port_id=dst_idx,
                            desired_dtype=src_dt,
                            mismatch=f"{src_dt} -> {dst_dt}",
                        )
                    if not preflight_hint and src_name in added_blocks:
                        preflight_hint = _dtype_param_hint_for_added_block(
                            added_blocks[src_name],
                            port_direction="outputs",
                            port_id=src_idx,
                            desired_dtype=dst_dt,
                            mismatch=f"{src_dt} -> {dst_dt}",
                        )
                    if preflight_hint:
                        hints.append(preflight_hint)

    var_hint = _undefined_variable_hint(operations, errors_payload)
    if var_hint:
        hints.append(var_hint)

    first = _first_error_hint(errors_payload)
    if first:
        hints.append(first)

    if not hints:
        return _flat_change_graph_hint()

    return " ".join(hints)


def _bypass_hint(
    *,
    agent: Any,
    operations: list[dict[str, Any]],
    validation_result: Any,
    errors_payload: Any,
) -> str | None:
    """Check if model disabled a block — fire on preflight or native errors.

    Only suggests bypass for stream-transform (inline DSP) blocks.
    For sinks, sources, variables, and blocks flagged with disable_bypass,
    emits a topology-aware terminal hint instead.
    """
    disabled_ops = [
        op for op in operations
        if isinstance(op, dict)
        and op.get("op_type") == "update_states"
        and op.get("state") == "disabled"
        and isinstance(op.get("instance_name"), str)
    ]
    if not disabled_ops:
        return None

    native_errors = _native_validation_error_text(validation_result)
    port_disconnected = any(
        "port is not connected" in err.lower()
        for err in native_errors
    )

    if isinstance(errors_payload, list):
        for row in errors_payload:
            if isinstance(row, dict):
                msg = str(row.get("message", "")).lower()
                if "port" in msg or "source" in msg:
                    port_disconnected = True
                    break

    if not port_disconnected:
        return None

    all_are_stream_transforms = True
    session = getattr(agent, "session", None)
    catalog_root = getattr(agent, "catalog_root", None)

    for op in disabled_ops:
        instance_name = op["instance_name"]
        block_type = None
        if session is not None and session.flowgraph is not None:
            for block in session.flowgraph.blocks:
                if block.instance_name == instance_name:
                    block_type = block.block_type
                    break

        if block_type is not None:
            semantics = _block_semantics(block_type, catalog_root)
            role = semantics.get("role", "")
            semantic_flags = semantics.get("evidence", {}).get("semantic_flags", [])
            if role in ("source", "sink", "variable_or_control", "metadata") or "disable_bypass" in semantic_flags:
                all_are_stream_transforms = False
        else:
            all_are_stream_transforms = False

    if all_are_stream_transforms:
        return (
            "Disabling this block broke its port connections. "
            "Use update_states with state='bypass' instead of 'disabled' "
            "to deactivate the block while keeping the graph connected."
        )

    return (
        "This is a terminal/control block that cannot be bypassed. "
        "To deactivate: remove its connections via remove_connections first, "
        "then disable; or use force=true for an invalid intermediate state."
    )


def _ofdm_carrier_hint(
    operations: list[dict[str, Any]],
    errors_payload: Any,
) -> str | None:
    """Return a tuple-of-lists hint for OFDM carrier parameter updates."""
    if not isinstance(errors_payload, list):
        return None
    has_carrier_update = any(
        isinstance(op, dict)
        and op.get("op_type") == "update_params"
        and any(
            key in ("occupied_carriers", "pilot_carriers")
            for key in (op.get("params") or {}).keys()
        )
        for op in operations
    )
    if has_carrier_update:
        return (
            "GNU Radio OFDM carrier parameters strictly require a tuple of lists. "
            "Ensure your Python expression is wrapped in parentheses with a trailing comma. "
            'Example: (list(range(-24, 0)) + list(range(1, 25)),)'
        )
    return None


def _port_discovery_hint(
    agent: Any,
    operations: list[dict[str, Any]],
    errors_payload: Any,
) -> str | None:
    """Return a hint when a connection fails due to port or catalog block issues."""
    if not isinstance(errors_payload, list):
        return None
    has_port_error = any(
        isinstance(row, dict)
        and (
            row.get("code") in ("port_out_of_range", "catalog_block_unavailable", "invalid_port")
            or "port" in str(row.get("field", "")).lower()
        )
        for row in errors_payload
    )
    if not has_port_error:
        return None
    return (
        "Connection failed. Message ports use string identifiers (e.g. port='pdus'), "
        "not integers. Use query_knowledge(catalog) to find the exact port names "
        "for each block before connecting."
    )


def _port_occupancy_hint(errors_payload: Any) -> str | None:
    """Check for port occupancy errors in preflight."""
    if not isinstance(errors_payload, list):
        return None
    for row in errors_payload:
        if not isinstance(row, dict):
            continue
        msg = str(row.get("message", "")).lower()
        if "port is already connected" in msg or "already in use" in msg:
            return (
                "You attempted to connect to an input port that is already in use. "
                "GRC input ports accept only one connection. "
                "Include the exact connection_id in remove_connections to free "
                "the port before connecting your new block."
            )
    return None


def _undefined_variable_hint(
    operations: list[dict[str, Any]],
    errors_payload: Any,
) -> str | None:
    """Return a hint when a variable is created and referenced in the same batch."""
    if not isinstance(errors_payload, list):
        return None
    has_undefined = any(
        isinstance(row, dict)
        and row.get("code") in ("block_not_found", "parameter_not_found")
        for row in errors_payload
    )
    if not has_undefined:
        return None
    has_added = any(
        isinstance(op, dict)
        and op.get("op_type") == "add_block"
        and op.get("block_type") == "variable"
        for op in operations
    )
    if has_added:
        return (
            "You created a variable and referenced it in the same batch. "
            "Create the variable in one change_graph call, then reference it "
            "in a second call."
        )
    return None


def _flat_change_graph_hint() -> str:
    return (
        "Use the flat change_graph fields: add_blocks[].block_id, "
        "add_blocks[].instance_name, add_blocks[].params, update_params[].params, "
        "add_connections[].src/dst, remove_connections[]."
    )


def _repair_hint_for_validation_failure(
    operations: list[dict[str, Any]],
    validation_result: Any,
) -> str | None:
    if not isinstance(validation_result, dict):
        return None
    if str(validation_result.get("status") or "").lower() not in {"invalid", "failed"}:
        return None
    native_errors = _native_validation_error_text(validation_result)
    if not native_errors:
        return None
    # Check for invalid parameter expressions in native errors
    param_pattern = re.compile(r"Param - [^(]+\(([^)]+)\):\s*Expression[\s\S]*?is\s+invalid", re.IGNORECASE)
    for error in native_errors:
        param_match = param_pattern.search(error)
        if param_match:
            param_name = param_match.group(1)
            return (
                f"No change committed; graph unchanged. Native GNU validation error: "
                f"The parameter '{param_name}' has an invalid or missing value. "
                f"Please specify a valid value for '{param_name}' in your parameter dictionary."
            )

    dtype_pair = _first_dtype_mismatch(native_errors)
    if dtype_pair is None:
        if _is_port_occupancy_error(native_errors):
            return (
                "No change committed; graph unchanged. "
                "You attempted to connect to an input port that is already in use. "
                "GRC input ports accept only one connection. "
                "Include the exact connection_id in remove_connections to free "
                "the port before connecting your new block."
            )
        return (
            "No change committed; graph unchanged. Native GNU validation error: "
            f"{native_errors[0]} Ask the user for the intended valid edit or explicit force."
        )
    source_dtype, destination_dtype = dtype_pair
    param_hint = _configurable_dtype_param_hint(
        operations,
        native_errors=native_errors,
        source_dtype=source_dtype,
        destination_dtype=destination_dtype,
    )
    if param_hint:
        return param_hint
    return (
        "Native GNU validation found a stream dtype mismatch "
        f"({source_dtype} -> {destination_dtype}). Retry with compatible block "
        "parameters or add a converter block."
    )


def _native_validation_error_text(validation_result: dict[str, Any]) -> list[str]:
    native = validation_result.get("native")
    if not isinstance(native, dict):
        return []
    errors = native.get("errors")
    if not isinstance(errors, list):
        return []
    return [" ".join(str(error).split()) for error in errors if str(error).strip()]


def _first_dtype_mismatch(errors: list[str]) -> tuple[str, str] | None:
    pattern = re.compile(
        r'Source IO type "([^"]+)" does not match sink IO type "([^"]+)"'
    )
    for error in errors:
        match = pattern.search(error)
        if match:
            return match.group(1), match.group(2)
    return None


def _is_port_occupancy_error(native_errors: list[str]) -> bool:
    """Return whether the model hit a port occupancy / multi-connection error."""
    return any(
        "port is already connected" in error.lower()
        for error in native_errors
    )


def _configurable_dtype_param_hint(
    operations: list[dict[str, Any]],
    *,
    native_errors: list[str],
    source_dtype: str,
    destination_dtype: str,
) -> str | None:
    added_blocks = {
        str(op.get("instance_name")): op
        for op in operations
        if isinstance(op, dict)
        and op.get("op_type") in {"add_block", "insert_block_on_connection"}
        and isinstance(op.get("instance_name"), str)
    }
    if not added_blocks:
        return None

    block_pat = re.compile(
        r"Block - ([a-zA-Z0-9_]+) - [^(]+\([^)]+\)\s+(Source|Sink) - ([a-zA-Z0-9_]+)\((\d+)\)"
    )
    type_pat = re.compile(
        r"Source IO type \"([^\"]+)\" does not match sink IO type \"([^\"]+)\""
    )

    for error in native_errors:
        block_matches = block_pat.findall(error)
        type_match = type_pat.search(error)
        if len(block_matches) == 2 and type_match:
            src_name, src_dir, src_port, src_idx_str = block_matches[0]
            dst_name, dst_dir, dst_port, dst_idx_str = block_matches[1]
            src_idx = int(src_idx_str)
            dst_idx = int(dst_idx_str)
            src_dt = type_match.group(1)
            dst_dt = type_match.group(2)

            if dst_name in added_blocks:
                hint = _dtype_param_hint_for_added_block(
                    added_blocks[dst_name],
                    port_direction="inputs",
                    port_id=dst_idx,
                    desired_dtype=src_dt,
                    mismatch=f"{src_dt} -> {dst_dt}",
                )
                if hint:
                    return hint

            if src_name in added_blocks:
                hint = _dtype_param_hint_for_added_block(
                    added_blocks[src_name],
                    port_direction="outputs",
                    port_id=src_idx,
                    desired_dtype=dst_dt,
                    mismatch=f"{src_dt} -> {dst_dt}",
                )
                if hint:
                    return hint

    # Fallback to the original logic
    for operation in operations:
        if not isinstance(operation, dict) or operation.get("op_type") != "add_connection":
            continue
        dst_block = operation.get("dst_block")
        dst_port = operation.get("dst_port")
        if isinstance(dst_block, str) and dst_block in added_blocks:
            hint = _dtype_param_hint_for_added_block(
                added_blocks[dst_block],
                port_direction="inputs",
                port_id=dst_port,
                desired_dtype=source_dtype,
                mismatch=f"{source_dtype} -> {destination_dtype}",
            )
            if hint:
                return hint
        src_block = operation.get("src_block")
        src_port = operation.get("src_port")
        if isinstance(src_block, str) and src_block in added_blocks:
            hint = _dtype_param_hint_for_added_block(
                added_blocks[src_block],
                port_direction="outputs",
                port_id=src_port,
                desired_dtype=destination_dtype,
                mismatch=f"{source_dtype} -> {destination_dtype}",
            )
            if hint:
                return hint
    return None


def _dtype_param_hint_for_added_block(
    operation: dict[str, Any],
    *,
    port_direction: str,
    port_id: Any,
    desired_dtype: str,
    mismatch: str,
) -> str | None:
    block_type = operation.get("block_type")
    instance_name = operation.get("instance_name")
    if not isinstance(block_type, str) or not isinstance(instance_name, str):
        return None
    catalog = describe_block(block_type)
    if catalog.get("ok") is False:
        return None
    port = _catalog_port(catalog.get(port_direction), port_id)
    dtype = port.get("dtype") if isinstance(port, dict) else None
    param_id = _template_param_id(dtype)
    if param_id is None:
        return None
    param = _catalog_param(catalog.get("parameters"), param_id)
    options = param.get("options") if isinstance(param, dict) else None

    suggested_val = None
    if isinstance(options, list):
        if desired_dtype in {str(option) for option in options}:
            suggested_val = desired_dtype
        else:
            attr = _template_param_attr(dtype)
            option_attributes = param.get("option_attributes") if isinstance(param, dict) else None
            if attr and isinstance(option_attributes, dict):
                attrs = option_attributes.get(attr)
                if isinstance(attrs, list):
                    matching = [options[i] for i, val in enumerate(attrs) if str(val) == desired_dtype and i < len(options)]
                    if matching:
                        suggested_val = matching[0]

    if suggested_val is None:
        return None

    existing_params = operation.get("parameters") or operation.get("params")
    if isinstance(existing_params, dict) and str(existing_params.get(param_id)) == suggested_val:
        return None
    op_type = operation.get("op_type")
    if op_type == "insert_block_on_connection":
        return (
            f"Native GNU validation found a stream dtype mismatch ({mismatch}). "
            f"The newly inserted `{instance_name}` uses catalog param `{param_id}` "
            f"for its {port_direction[:-1]} dtype; retry with "
            f"add_blocks[].params.{param_id}=\"{suggested_val}\"."
        )
    return (
        f"Native GNU validation found a stream dtype mismatch ({mismatch}). "
        f"The newly added `{instance_name}` uses catalog param `{param_id}` "
        f"for its {port_direction[:-1]} dtype; retry with "
        f"add_blocks[].params.{param_id}=\"{suggested_val}\"."
    )


def _catalog_port(ports: Any, port_id: Any) -> dict[str, Any]:
    rows = [row for row in ports if isinstance(row, dict)] if isinstance(ports, list) else []
    if isinstance(port_id, int) and 0 <= port_id < len(rows):
        return rows[port_id]
    text = str(port_id)
    for row in rows:
        if str(row.get("id")) == text:
            return row
    return {}


def _catalog_param(params: Any, param_id: str) -> dict[str, Any]:
    rows = [row for row in params if isinstance(row, dict)] if isinstance(params, list) else []
    for row in rows:
        if row.get("id") == param_id:
            return row
    return {}


def _template_param_id(dtype: Any) -> str | None:
    if not isinstance(dtype, str):
        return None
    match = re.search(r"\$\{\s*([A-Za-z_][A-Za-z0-9_]*)", dtype.strip())
    return match.group(1) if match else None


def _template_param_attr(dtype: Any) -> str | None:
    if not isinstance(dtype, str):
        return None
    match = re.search(r"\$\{\s*[A-Za-z_][A-Za-z0-9_]*\.([A-Za-z_][A-Za-z0-9_]*)", dtype.strip())
    return match.group(1) if match else None


def _resolve_added_block_endpoint_alias(
    endpoint: tuple[str, int | str],
    aliases: dict[str, str | None],
) -> tuple[str, int | str]:
    block, port = endpoint
    alias = aliases.get(block)
    if alias:
        return alias, port
    return endpoint


def _as_list(value: Any, field_name: str, errors: list[str]) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    errors.append(f"{field_name} must be an array when provided.")
    return []


def _required_string(item: dict[str, Any], key: str, field_name: str, errors: list[str]) -> str | None:
    value = item.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    errors.append(f"{field_name}.{key} must be a non-empty string.")
    return None


def _variable_instance_name(item: dict[str, Any], field_name: str, errors: list[str]) -> str | None:
    value = item.get("instance_name")
    if isinstance(value, str) and value.strip():
        return value.strip()
    legacy = item.get("name")
    if isinstance(legacy, str) and legacy.strip():
        return legacy.strip()
    errors.append(f"{field_name}.instance_name must be a non-empty string.")
    return None


def _connection_id_from_remove_item(item: Any) -> str | None:
    if isinstance(item, str) and item.strip():
        return item.strip()
    if isinstance(item, dict):
        value = item.get("connection_id")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _append_remove_connection(
    operations: list[dict[str, Any]],
    seen: set[str],
    connection_id: str,
    errors: list[str],
    field_name: str,
) -> None:
    parsed = parse_connection_id(connection_id)
    if parsed is None:
        errors.append(f"{field_name} is not a valid connection_id.")
        return
    if connection_id in seen:
        return
    seen.add(connection_id)
    operations.append({"op_type": "remove_connection", "connection_id": connection_id})


def _remove_block_operation(
    item: Any,
    *,
    index: int,
    field_name: str,
    errors: list[str],
) -> dict[str, Any] | None:
    if isinstance(item, str):
        item = {"instance_name": item}
    if not isinstance(item, dict):
        errors.append(f"{field_name}[{index}] must be a block name or object.")
        return None
    instance_name = item.get("instance_name")
    block_id = item.get("block_id")
    op: dict[str, Any] = {"op_type": "remove_block"}
    if isinstance(instance_name, str) and instance_name.strip():
        op["instance_name"] = instance_name.strip()
    else:
        errors.append(f"{field_name}[{index}] requires instance_name.")
        return None
    block_id = _optional_catalog_block_id(block_id)
    if block_id is not None:
        op["block_type"] = block_id
    return op


def _update_params_operation(
    item: Any,
    *,
    index: int,
    field_name: str,
    errors: list[str],
) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        errors.append(f"{field_name}[{index}] must be an object.")
        return None
    params = item.get("params")
    if not isinstance(params, dict) or not params:
        errors.append(f"{field_name}[{index}].params must be a non-empty object.")
        return None
    op: dict[str, Any] = {
        "op_type": "update_params",
        "params": copy.deepcopy(params),
    }
    instance_name = _required_string(item, "instance_name", f"{field_name}[{index}]", errors)
    if instance_name is None:
        return None
    op["instance_name"] = instance_name
    block_id = _optional_catalog_block_id(item.get("block_id"))
    if block_id is not None:
        op["block_type"] = block_id
    return op


def _update_state_operation(
    item: Any,
    *,
    index: int,
    field_name: str,
    errors: list[str],
) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        errors.append(f"{field_name}[{index}] must be an object.")
        return None
    state = item.get("state")
    if isinstance(state, str):
        pass
    else:
        states = item.get("states")
        if not isinstance(states, dict) or not states:
            errors.append(f"{field_name}[{index}].state must be an enum or .states must be a non-empty object.")
            return None
        state = states.get("state")
        if state is None and "enabled" in states:
            state = "enabled" if bool(states.get("enabled")) else "disabled"
        if state is None and "disabled" in states:
            state = "disabled" if bool(states.get("disabled")) else "enabled"
    if state not in {"enabled", "disabled", "bypass"}:
        errors.append(f"{field_name}[{index}].state must be enabled/disabled/bypass.")
        return None
    op: dict[str, Any] = {"op_type": "update_states", "state": state}
    instance_name = _required_string(item, "instance_name", f"{field_name}[{index}]", errors)
    if instance_name is None:
        return None
    op["instance_name"] = instance_name
    block_id = _optional_catalog_block_id(item.get("block_id"))
    if block_id is not None:
        op["block_type"] = block_id
    return op
    if block_id is not None:
        op["block_type"] = block_id
    return op


def _optional_catalog_block_id(value: Any) -> str | None:
    """Return a catalog block id, ignoring inspected block UID values."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or text.startswith("block:"):
        return None
    return text


def _incident_connection_ids(agent: Any, block_name: str) -> list[str]:
    flowgraph = agent.session.flowgraph
    if flowgraph is None:
        return []
    ids: list[str] = []
    for conn in flowgraph.connections:
        if conn.src_block == block_name or conn.dst_block == block_name:
            ids.append(
                render_connection_id(
                    conn.src_block,
                    conn.src_port,
                    conn.dst_block,
                    conn.dst_port,
                )
            )
    return ids


def _flat_block_names_snapshot(agent: Any) -> set[str]:
    flowgraph = agent.session.flowgraph
    if flowgraph is None:
        return set()
    return {
        block.instance_name
        for block in flowgraph.blocks
        if isinstance(block.instance_name, str)
    }


def _flat_connection_ids_snapshot(agent: Any) -> set[str]:
    flowgraph = agent.session.flowgraph
    if flowgraph is None:
        return set()
    return {
        render_connection_id(conn.src_block, conn.src_port, conn.dst_block, conn.dst_port)
        for conn in flowgraph.connections
    }


def _synthesized_flat_delta(
    agent: Any,
    *,
    before_block_names: set[str],
    before_connection_ids: set[str],
    validation_result: Any,
) -> dict[str, Any]:
    after_block_names = _flat_block_names_snapshot(agent)
    after_connection_ids = _flat_connection_ids_snapshot(agent)
    delta: dict[str, Any] = {}
    added_blocks = sorted(after_block_names - before_block_names)
    removed_blocks = sorted(before_block_names - after_block_names)
    added_connections = sorted(after_connection_ids - before_connection_ids)
    removed_connections = sorted(before_connection_ids - after_connection_ids)
    if added_blocks:
        delta["added_blocks"] = added_blocks
    if removed_blocks:
        delta["removed_blocks"] = removed_blocks
    if added_connections:
        delta["added_connections"] = added_connections
    if removed_connections:
        delta["removed_connections"] = removed_connections
    delta["dirty"] = bool(agent.session.is_dirty)
    if isinstance(validation_result, dict):
        status = validation_result.get("status")
        returncode = validation_result.get("returncode")
        if status is not None:
            delta["validation_status"] = status
        if returncode is not None:
            delta["validation_returncode"] = returncode
    return delta


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
        if target and isinstance(params, dict) and params:
            parts = [f"{target}.{key}={value}" for key, value in params.items()]
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
    return None


def _render_operation_connection(operation: dict[str, Any]) -> str | None:
    src_block = operation.get("src_block")
    src_port = operation.get("src_port")
    dst_block = operation.get("dst_block")
    dst_port = operation.get("dst_port")
    if src_block is None or src_port is None or dst_block is None or dst_port is None:
        return None
    return render_connection_id(str(src_block), src_port, str(dst_block), dst_port)
