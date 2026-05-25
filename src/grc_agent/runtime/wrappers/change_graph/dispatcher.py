"""Shared dispatcher for model-facing change_graph wrapper orchestration."""

from __future__ import annotations

import copy
import time
from typing import Any

from grc_agent._payload import ErrorCode
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
    "rewire_connections",
    "insert_blocks_on_connections",
    "add_variables",
    "update_variables",
    "remove_variables",
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
    rewire_connections: Any = None,
    insert_blocks_on_connections: Any = None,
    add_variables: Any = None,
    update_variables: Any = None,
    remove_variables: Any = None,
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
        rewire_connections=rewire_connections,
        insert_blocks_on_connections=insert_blocks_on_connections,
        add_variables=add_variables,
        update_variables=update_variables,
        remove_variables=remove_variables,
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
            hint = result.get("hint")
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

    remove_connections = _as_list(batches.get("remove_connections"), "remove_connections", errors)
    rewire_connections = _as_list(batches.get("rewire_connections"), "rewire_connections", errors)
    remove_blocks = _as_list(batches.get("remove_blocks"), "remove_blocks", errors)
    add_blocks = _as_list(batches.get("add_blocks"), "add_blocks", errors)
    add_variables = _as_list(batches.get("add_variables"), "add_variables", errors)
    update_params = _as_list(batches.get("update_params"), "update_params", errors)
    update_variables = _as_list(batches.get("update_variables"), "update_variables", errors)
    update_states = _as_list(batches.get("update_states"), "update_states", errors)
    insert_blocks = _as_list(
        batches.get("insert_blocks_on_connections"),
        "insert_blocks_on_connections",
        errors,
    )
    add_connections = _as_list(batches.get("add_connections"), "add_connections", errors)
    remove_variables = _as_list(batches.get("remove_variables"), "remove_variables", errors)

    if errors:
        return [], errors

    for index, item in enumerate(remove_connections):
        connection_id = _connection_id_from_remove_item(item)
        if connection_id is None:
            errors.append(f"remove_connections[{index}] must be a connection_id string.")
            continue
        _append_remove_connection(operations, removed_connection_ids, connection_id, errors, f"remove_connections[{index}]")

    for index, item in enumerate(rewire_connections):
        if not isinstance(item, dict):
            errors.append(f"rewire_connections[{index}] must be an object.")
            continue
        connection_id = _required_string(item, "connection_id", f"rewire_connections[{index}]", errors)
        parsed = parse_connection_id(connection_id) if connection_id is not None else None
        if parsed is None:
            errors.append(f"rewire_connections[{index}].connection_id is invalid or missing.")
            continue
        src_block, src_port, dst_block, dst_port = parsed
        new_src = item.get("new_src")
        new_dst = item.get("new_dst")
        parsed_new_src = _parse_transaction_endpoint(new_src) if new_src is not None else None
        parsed_new_dst = _parse_transaction_endpoint(new_dst) if new_dst is not None else None
        if (parsed_new_src is None) == (parsed_new_dst is None):
            errors.append(f"rewire_connections[{index}] requires exactly one of new_src or new_dst.")
            continue
        _append_remove_connection(operations, removed_connection_ids, connection_id, errors, f"rewire_connections[{index}]")
        if parsed_new_src is not None:
            src_block, src_port = parsed_new_src
        if parsed_new_dst is not None:
            dst_block, dst_port = parsed_new_dst
        operations.append(
            {
                "op_type": "add_connection",
                "src_block": src_block,
                "src_port": src_port,
                "dst_block": dst_block,
                "dst_port": dst_port,
            }
        )

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

    for index, item in enumerate(remove_variables):
        name = item.strip() if isinstance(item, str) else None
        if not name:
            errors.append(f"remove_variables[{index}] must be a variable name string.")
            continue
        for connection_id in _incident_connection_ids(agent, name):
            _append_remove_connection(
                operations,
                removed_connection_ids,
                connection_id,
                errors,
                f"remove_variables[{index}].auto_detach",
            )
        operations.append(
            {"op_type": "remove_block", "instance_name": name, "block_type": "variable"}
        )

    for index, item in enumerate(add_blocks):
        if not isinstance(item, dict):
            errors.append(f"add_blocks[{index}] must be an object.")
            continue
        block_id = _required_string(item, "block_id", f"add_blocks[{index}]", errors)
        instance_name = _required_string(item, "instance_name", f"add_blocks[{index}]", errors)
        if block_id is None or instance_name is None:
            continue
        params = item.get("params", {})
        states = item.get("states")
        if not isinstance(params, dict):
            errors.append(f"add_blocks[{index}].params must be an object when provided.")
            continue
        if states is not None and not isinstance(states, dict):
            errors.append(f"add_blocks[{index}].states must be an object when provided.")
            continue
        op = {
            "op_type": "add_block",
            "block_type": block_id,
            "instance_name": instance_name,
            "parameters": copy.deepcopy(params),
        }
        if states is not None:
            op["states"] = copy.deepcopy(states)
        operations.append(op)

    for index, item in enumerate(add_variables):
        if not isinstance(item, dict):
            errors.append(f"add_variables[{index}] must be an object.")
            continue
        name = _variable_instance_name(item, f"add_variables[{index}]", errors)
        if name is None or "value" not in item:
            if "value" not in item:
                errors.append(f"add_variables[{index}].value is required.")
            continue
        operations.append(
            {
                "op_type": "add_block",
                "block_type": "variable",
                "instance_name": name,
                "parameters": {"value": copy.deepcopy(item["value"])},
            }
        )

    for index, item in enumerate(update_params):
        op = _update_params_operation(item, index=index, field_name="update_params", errors=errors)
        if op is not None:
            operations.append(op)

    for index, item in enumerate(update_variables):
        if not isinstance(item, dict):
            errors.append(f"update_variables[{index}] must be an object.")
            continue
        name = _variable_instance_name(item, f"update_variables[{index}]", errors)
        if name is None or "value" not in item:
            if "value" not in item:
                errors.append(f"update_variables[{index}].value is required.")
            continue
        op: dict[str, Any] = {
            "op_type": "update_params",
            "instance_name": name,
            "block_type": "variable",
            "params": {"value": copy.deepcopy(item["value"])},
        }
        if "expected_value" in item:
            op["expected_params"] = {"value": copy.deepcopy(item["expected_value"])}
        operations.append(op)

    for index, item in enumerate(update_states):
        op = _update_state_operation(item, index=index, field_name="update_states", errors=errors)
        if op is not None:
            operations.append(op)

    for index, item in enumerate(insert_blocks):
        if not isinstance(item, dict):
            errors.append(f"insert_blocks_on_connections[{index}] must be an object.")
            continue
        connection_id = _required_string(
            item,
            "connection_id",
            f"insert_blocks_on_connections[{index}]",
            errors,
        )
        block_id = _required_string(item, "block_id", f"insert_blocks_on_connections[{index}]", errors)
        instance_name = _required_string(
            item,
            "instance_name",
            f"insert_blocks_on_connections[{index}]",
            errors,
        )
        if connection_id is None or block_id is None or instance_name is None:
            continue
        params = item.get("params", {})
        if not isinstance(params, dict):
            errors.append(f"insert_blocks_on_connections[{index}].params must be an object when provided.")
            continue
        operations.append(
            {
                "op_type": "insert_block_on_connection",
                "connection_id": connection_id,
                "block_type": block_id,
                "instance_name": instance_name,
                "params": copy.deepcopy(params),
            }
        )

    for index, item in enumerate(add_connections):
        if not isinstance(item, dict):
            errors.append(f"add_connections[{index}] must be an object.")
            continue
        src = _parse_transaction_endpoint(item.get("src"))
        dst = _parse_transaction_endpoint(item.get("dst"))
        if src is None or dst is None:
            errors.append(f"add_connections[{index}] requires src and dst endpoints.")
            continue
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
    target_ref = item.get("target_ref")
    instance_name = item.get("instance_name")
    block_id = item.get("block_id")
    op: dict[str, Any] = {"op_type": "remove_block"}
    if isinstance(target_ref, dict) and target_ref:
        op["target_ref"] = copy.deepcopy(target_ref)
        if isinstance(target_ref.get("expected_instance_name"), str):
            op["instance_name"] = target_ref["expected_instance_name"]
    elif isinstance(instance_name, str) and instance_name.strip():
        op["instance_name"] = instance_name.strip()
    else:
        errors.append(f"{field_name}[{index}] requires instance_name or target_ref.")
        return None
    if isinstance(block_id, str) and block_id.strip():
        op["block_type"] = block_id.strip()
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
    target_ref = item.get("target_ref")
    if isinstance(target_ref, dict) and target_ref:
        op["target_ref"] = copy.deepcopy(target_ref)
    else:
        instance_name = _required_string(item, "instance_name", f"{field_name}[{index}]", errors)
        if instance_name is None:
            return None
        op["instance_name"] = instance_name
    block_id = item.get("block_id")
    if isinstance(block_id, str) and block_id.strip():
        op["block_type"] = block_id.strip()
    expected_params = item.get("expected_params")
    if expected_params is not None:
        if not isinstance(expected_params, dict):
            errors.append(f"{field_name}[{index}].expected_params must be an object when provided.")
            return None
        op["expected_params"] = copy.deepcopy(expected_params)
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
    states = item.get("states")
    if not isinstance(states, dict) or not states:
        errors.append(f"{field_name}[{index}].states must be a non-empty object.")
        return None
    state = states.get("state")
    if state is None and "enabled" in states:
        state = "enabled" if bool(states.get("enabled")) else "disabled"
    if state not in {"enabled", "disabled"}:
        errors.append(f"{field_name}[{index}].states must set state to enabled/disabled.")
        return None
    op: dict[str, Any] = {"op_type": "update_states", "state": state}
    target_ref = item.get("target_ref")
    if isinstance(target_ref, dict) and target_ref:
        op["target_ref"] = copy.deepcopy(target_ref)
    else:
        instance_name = _required_string(item, "instance_name", f"{field_name}[{index}]", errors)
        if instance_name is None:
            return None
        op["instance_name"] = instance_name
    block_id = item.get("block_id")
    if isinstance(block_id, str) and block_id.strip():
        op["block_type"] = block_id.strip()
    return op


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
