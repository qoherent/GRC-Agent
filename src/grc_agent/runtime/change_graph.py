"""Flat model-facing change_graph batch dispatcher and helpers."""

from __future__ import annotations

import copy
import json
import re
import time
from dataclasses import dataclass
from typing import Any

from grc_agent._payload import ErrorCode
from grc_agent.catalog.loaders import describe_block
from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.runtime.block_semantics import _block_semantics
from grc_agent.runtime.tool_context import is_meaningful, is_variable_block
from grc_agent.session_ops import connection_id as render_connection_id
from grc_agent.session_ops import parse_connection_id
from grc_agent.transaction import propose_edit

ToolResult = dict[str, Any]


# ── Disconnect resolution ──────────────────────────────────────────────


@dataclass(frozen=True)
class DisconnectResolution:
    connection_id: str | None = None
    ambiguous_candidates: list[dict[str, Any]] | None = None
    ok: bool = False
    message: str | None = None
    error_type: str | None = None
    state_revision: int | None = None
    validation_errors: list[dict[str, Any]] | None = None


def resolve_disconnect_connection_id(
    *,
    session: FlowgraphSession,
    connection_id: str | None = None,
    src_block: str | None = None,
    src_port: int | str | None = None,
    dst_block: str | None = None,
    dst_port: int | str | None = None,
) -> DisconnectResolution:
    endpoint_args = {
        "src_block": src_block,
        "src_port": src_port,
        "dst_block": dst_block,
        "dst_port": dst_port,
    }
    has_endpoint_hint = any(value is not None for value in endpoint_args.values())
    if has_endpoint_hint:
        resolved = session.find_connection_candidates(
            src_block=src_block,
            src_port=src_port,
            dst_block=dst_block,
            dst_port=dst_port,
        )
        candidates = resolved["candidates"]
        if not candidates:
            return DisconnectResolution(
                ok=False,
                message="No existing connection matches the provided endpoint fields.",
                error_type="connection_not_found",
                state_revision=session.state_revision,
            )
        if len(candidates) > 1:
            return DisconnectResolution(
                ok=False,
                ambiguous_candidates=candidates,
            )

        resolved_connection_id = candidates[0]["connection_id"]
        if connection_id is not None and connection_id != resolved_connection_id:
            return DisconnectResolution(
                ok=False,
                message=(
                    "connection_id does not match the provided endpoint fields: "
                    f"{connection_id}"
                ),
                error_type="connection_endpoint_mismatch",
                state_revision=session.state_revision,
            )
        connection_id = resolved_connection_id

    if not isinstance(connection_id, str) or not connection_id.strip():
        return DisconnectResolution(
            ok=False,
            message=(
                "remove_connection requires either connection_id or enough "
                "endpoint fields to resolve one existing connection."
            ),
            error_type=ErrorCode.TOOL_CALL_INVALID,
            validation_errors=[
                {
                    "code": "missing_required",
                    "field": "connection_id",
                    "message": "Provide connection_id or endpoint fields.",
                }
            ],
        )

    return DisconnectResolution(ok=True, connection_id=connection_id.strip())


# ── Dispatcher ─────────────────────────────────────────────────────────


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
        agent.session._last_failed_ops_hash = None
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
            payload["committed"] = False
            payload["message"] = (
                "No changes were committed."
                if graph_unchanged
                else "Partial changes applied but final verification failed."
            )
            payload["graph_unchanged"] = graph_unchanged
            payload["rollback"] = "complete" if graph_unchanged else "unknown"
            if result.get("error_type") == ErrorCode.GNU_VALIDATION_FAILED:
                payload["rejected_phase"] = "native_grc_validation"
                payload["message"] = (
                    "Graph edit rejected by validation. Changes not committed."
                )
            native_errors = (
                _native_validation_error_text(validation_result)
                if isinstance(validation_result, dict)
                else []
            )
            if native_errors:
                payload["native_validation_errors"] = native_errors

            # ── Phase 3: State-aware repeat-payload escalator ──────────────
            current_ops_hash = json.dumps(normalized_operations, sort_keys=True)
            if agent.session._last_failed_ops_hash == current_ops_hash:
                escalation_warning = "This payload was already submitted and rejected."
                payload.setdefault("warnings", []).append(escalation_warning)
            agent.session._last_failed_ops_hash = current_ops_hash

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
            errors.append(f"remove_connections[{index}] expected to be a connection_id string.")
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
            errors.append(f"add_blocks[{index}] expected to be an object.")
            continue
        block_id = _required_string(item, "block_id", f"add_blocks[{index}]", errors)
        instance_name = _required_string(item, "instance_name", f"add_blocks[{index}]", errors)
        if block_id is None or instance_name is None:
            continue
        params = item.get("params", {})
        state = item.get("state")
        if not isinstance(params, dict):
            errors.append(f"add_blocks[{index}].params expected to be an object when provided.")
            continue
        if state is not None and not isinstance(state, str):
            errors.append(f"add_blocks[{index}].state expected to be a string when provided.")
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
            errors.append(f"add_connections[{index}] expected to be an object.")
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
                    hints.append(f"Stream dtype mismatch: {src_dt} -> {dst_dt}.")
                else:
                    match = re.search(
                        r"Cannot connect ([a-zA-Z0-9_]+)\(([^)]+)\) \(([^)]+)\) to ([a-zA-Z0-9_]+)\(([^)]+)\) \(([^)]+)\)",
                        msg
                    )
                    if match:
                        src_dt = match.group(3)
                        dst_dt = match.group(6)
                        hints.append(f"Stream dtype mismatch: {src_dt} -> {dst_dt}.")
                    else:
                        match = re.search(
                            r"Existing connection became invalid: ([a-zA-Z0-9_]+)\(([^)]+)\) \(([^)]+)\) -> ([a-zA-Z0-9_]+)\(([^)]+)\) \(([^)]+)\)",
                            msg
                        )
                        if match:
                            src_dt = match.group(3)
                            dst_dt = match.group(6)
                            hints.append(f"Stream dtype mismatch: {src_dt} -> {dst_dt}.")

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
        return "Block disabled — port connections severed. Cannot disable block with active connections."

    return "Terminal/control block cannot be bypassed."


def _ofdm_carrier_hint(
    operations: list[dict[str, Any]],
    errors_payload: Any,
) -> str | None:
    return None


def _port_discovery_hint(
    agent: Any,
    operations: list[dict[str, Any]],
    errors_payload: Any,
) -> str | None:
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
    return "Connection failed — message ports require string identifiers."


def _port_occupancy_hint(errors_payload: Any) -> str | None:
    if not isinstance(errors_payload, list):
        return None
    for row in errors_payload:
        if not isinstance(row, dict):
            continue
        msg = str(row.get("message", "")).lower()
        if "port is already connected" in msg or "already in use" in msg:
            return "Input port already occupied."
    return None


def _undefined_variable_hint(
    operations: list[dict[str, Any]],
    errors_payload: Any,
) -> str | None:
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
        and is_variable_block(str(op.get("block_type", "")))
        for op in operations
    )
    if has_added:
        return "Variable referenced before creation in same batch."
    return None


def _flat_change_graph_hint() -> str:
    return ""


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
    param_pattern = re.compile(r"Param - [^(]+\(([^)]+)\):\s*Expression[\s\S]*?is\s+invalid", re.IGNORECASE)
    for error in native_errors:
        param_match = param_pattern.search(error)
        if param_match:
            param_name = param_match.group(1)
            return f"Invalid or missing value for parameter '{param_name}'."

    dtype_pair = _first_dtype_mismatch(native_errors)
    if dtype_pair is None:
        if _is_port_occupancy_error(native_errors):
            return "Input port already occupied."
        return f"Native GNU validation error: {native_errors[0]}"

    source_dtype, destination_dtype = dtype_pair
    return f"Stream dtype mismatch: {source_dtype} -> {destination_dtype}."


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
    errors.append(f"{field_name} expected to be an array when provided.")
    return []


def _required_string(item: dict[str, Any], key: str, field_name: str, errors: list[str]) -> str | None:
    value = item.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    errors.append(f"{field_name}.{key} expected to be a non-empty string.")
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
        errors.append(f"{field_name}[{index}] expected to be a block name or object.")
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
        errors.append(f"{field_name}[{index}] expected to be an object.")
        return None
    params = item.get("params")
    if not isinstance(params, dict) or not params:
        errors.append(f"{field_name}[{index}].params expected to be a non-empty object.")
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
        errors.append(f"{field_name}[{index}] expected to be an object.")
        return None
    state = item.get("state")
    if isinstance(state, str):
        pass
    else:
        states = item.get("states")
        if not isinstance(states, dict) or not states:
            errors.append(f"{field_name}[{index}].state expected to be an enum or .states expected to be a non-empty object.")
            return None
        state = states.get("state")
        if state is None and "enabled" in states:
            state = "enabled" if bool(states.get("enabled")) else "disabled"
        if state is None and "disabled" in states:
            state = "disabled" if bool(states.get("disabled")) else "enabled"
    if state not in {"enabled", "disabled", "bypass"}:
        errors.append(f"{field_name}[{index}].state expected to be enabled/disabled/bypass.")
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
        block_types = _added_block_types(agent, after_block_names - before_block_names)
        if block_types:
            delta["added_block_types"] = block_types
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


def _added_block_types(agent: Any, added_names: set[str]) -> dict[str, str]:
    """Look up the block type for each newly added block."""
    if not agent.session.flowgraph:
        return {}
    return {
        b.instance_name: b.block_type
        for b in agent.session.flowgraph.blocks
        if b.instance_name in added_names
    }


def _drop_empty_result_fields(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if is_meaningful(value)}


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
        if is_variable_block(str(block_type or "")) and name:
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


# ── Rewire resolution ──────────────────────────────────────────────────


def has_endpoint_value(value: Any) -> bool:
    return value is not None and not (isinstance(value, str) and not value.strip())


def rewire_new_endpoint_is_exact(
    *,
    new_src_block: str | None,
    new_src_port: int | str | None,
    new_dst_block: str | None,
    new_dst_port: int | str | None,
) -> bool:
    return all(
        has_endpoint_value(value)
        for value in (new_src_block, new_src_port, new_dst_block, new_dst_port)
    )


def resolve_rewire_new_endpoint_args(
    agent: Any,
    *,
    old_connection_id: str,
    new_src_block: str | None,
    new_src_port: int | str | None,
    new_dst_block: str | None,
    new_dst_port: int | str | None,
) -> dict[str, Any]:
    if agent._rewire_new_endpoint_is_exact(
        new_src_block=new_src_block,
        new_src_port=new_src_port,
        new_dst_block=new_dst_block,
        new_dst_port=new_dst_port,
    ):
        return {
            "ok": True,
            "new_src_block": str(new_src_block),
            "new_src_port": new_src_port,
            "new_dst_block": str(new_dst_block),
            "new_dst_port": new_dst_port,
        }

    missing_fields = [
        field
        for field, value in (
            ("new_src_block", new_src_block),
            ("new_src_port", new_src_port),
            ("new_dst_block", new_dst_block),
            ("new_dst_port", new_dst_port),
        )
        if not agent._has_endpoint_value(value)
    ]
    has_source_hint = agent._has_endpoint_value(new_src_block) or agent._has_endpoint_value(new_src_port)
    has_destination_hint = agent._has_endpoint_value(new_dst_block) or agent._has_endpoint_value(new_dst_port)
    if not has_source_hint or not has_destination_hint:
        missing_side = "new_source" if not has_source_hint else "new_destination"
        return {
            "ok": False,
            "message": (
                "rewire_connection requires at least one hint for both the "
                "new source and new destination; it will not infer an entire endpoint side."
            ),
            "error_type": ErrorCode.TOOL_CALL_INVALID,
            "state_revision": agent.session.state_revision,
            "validation_errors": [
                {
                    "code": "missing_required",
                    "field": missing_side,
                    "message": (
                        "Provide exact fields or at least one bounded hint for "
                        "this new endpoint side."
                    ),
                }
            ],
        }
    candidates = agent._rewire_new_endpoint_candidates(
        old_connection_id=old_connection_id,
        new_src_block=new_src_block,
        new_src_port=new_src_port,
        new_dst_block=new_dst_block,
        new_dst_port=new_dst_port,
    )
    if not candidates:
        return {
            "ok": False,
            "message": (
                "rewire_connection requires exact new endpoints or endpoint hints "
                "that resolve to existing executable candidates."
            ),
            "error_type": ErrorCode.TOOL_CALL_INVALID,
            "state_revision": agent.session.state_revision,
            "validation_errors": [
                {
                    "code": "missing_required",
                    "field": field,
                    "message": (
                        "Provide an exact new endpoint field or enough endpoint "
                        "hints to resolve executable candidates."
                    ),
                }
                for field in missing_fields
            ],
        }
    if len(candidates) == 1:
        candidate = candidates[0]
        return {"ok": True, **candidate}
    if len(candidates) > 3:
        return {
            "ok": False,
            "message": (
                "Too many executable new endpoint candidates match. "
                "Provide exact new source and destination endpoints."
            ),
            "error_type": "ambiguous_rewire_endpoint",
            "state_revision": agent.session.state_revision,
            "candidate_count": len(candidates),
        }
    return agent._rewire_new_endpoint_clarification_payload(
        old_connection_id=old_connection_id,
        candidates=candidates,
    )


def rewire_new_endpoint_candidates(
    agent: Any,
    *,
    old_connection_id: str,
    new_src_block: str | None,
    new_src_port: int | str | None,
    new_dst_block: str | None,
    new_dst_port: int | str | None,
) -> list[dict[str, Any]]:
    parsed_old = parse_connection_id(old_connection_id)
    if parsed_old is None:
        return []
    source_candidates = agent._connection_endpoint_candidates(
        side="source",
        block=new_src_block,
        port=new_src_port,
    )
    destination_candidates = agent._connection_endpoint_candidates(
        side="destination",
        block=new_dst_block,
        port=new_dst_port,
    )
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, int | str, str, int | str]] = set()
    for source_block, source_port in source_candidates:
        for destination_block, destination_port in destination_candidates:
            connection = (source_block, source_port, destination_block, destination_port)
            if connection == parsed_old or connection in seen:
                continue
            seen.add(connection)
            candidate = {
                "new_src_block": source_block,
                "new_src_port": source_port,
                "new_dst_block": destination_block,
                "new_dst_port": destination_port,
            }
            if agent._rewire_candidate_passes_preflight(old_connection_id, candidate):
                candidates.append(candidate)
    return candidates


def connection_endpoint_candidates(
    agent: Any,
    *,
    side: str,
    block: str | None,
    port: int | str | None,
) -> list[tuple[str, int | str]]:
    if agent._has_endpoint_value(block) and agent._has_endpoint_value(port):
        loaded_block = agent._loaded_block_by_name(str(block))
        if loaded_block is None:
            return []
        if not agent._loaded_block_has_port(
            block_type=loaded_block.block_type,
            port=port,
            side=side,
        ):
            return []
        return [(str(block), port)]
    flowgraph = agent.session.flowgraph
    if flowgraph is None:
        return []
    candidates: set[tuple[str, int | str]] = set()
    if agent._has_endpoint_value(port):
        if agent._has_endpoint_value(block):
            candidates.add((str(block), port))
        else:
            for loaded_block in flowgraph.blocks:
                if agent._loaded_block_has_port(
                    block_type=loaded_block.block_type,
                    port=port,
                    side=side,
                ):
                    candidates.add((loaded_block.instance_name, port))
    for connection in flowgraph.connections:
        if side == "source":
            endpoint_block = connection.src_block
            endpoint_port = connection.src_port
        else:
            endpoint_block = connection.dst_block
            endpoint_port = connection.dst_port
        if agent._has_endpoint_value(block) and endpoint_block != block:
            continue
        if agent._has_endpoint_value(port) and not FlowgraphSession._port_matches(endpoint_port, port):
            continue
        candidates.add((endpoint_block, endpoint_port))
    return sorted(candidates, key=lambda item: (item[0], str(item[1])))


def loaded_block_by_name(agent: Any, instance_name: str) -> Any | None:
    flowgraph = agent.session.flowgraph
    if flowgraph is None:
        return None
    return next(
        (
            loaded_block
            for loaded_block in flowgraph.blocks
            if loaded_block.instance_name == instance_name
        ),
        None,
    )


def loaded_block_has_port(
    *,
    block_type: str,
    port: int | str,
    side: str,
) -> bool:
    description = describe_block(block_type)
    if not description.get("ok"):
        return False
    field_name = "outputs" if side == "source" else "inputs"
    ports = description.get(field_name)
    if not isinstance(ports, list):
        return False
    if not isinstance(port, str):
        return any(
            isinstance(candidate, dict)
            and candidate.get("domain") != "message"
            and not candidate.get("id")
            for candidate in ports
        )
    return any(
        isinstance(candidate, dict) and candidate.get("id") == port
        for candidate in ports
    )


def rewire_candidate_passes_preflight(
    agent: Any,
    old_connection_id: str,
    candidate: dict[str, Any],
) -> bool:
    proposal = propose_edit(
        agent.session,
        [
            {
                "op_type": "remove_connection",
                "connection_id": old_connection_id,
            },
            {
                "op_type": "add_connection",
                "src_block": candidate["new_src_block"],
                "src_port": candidate["new_src_port"],
                "dst_block": candidate["new_dst_block"],
                "dst_port": candidate["new_dst_port"],
            },
        ],
        agent.catalog_root,
    )
    return bool(proposal.get("ok"))


def resolve_old_rewire_connection_id(
    agent: Any,
    *,
    old_connection_id: str | None,
    old_src_block: str | None,
    old_src_port: int | str | None,
    old_dst_block: str | None,
    old_dst_port: int | str | None,
    new_src_block: str | None,
    new_src_port: int | str | None,
    new_dst_block: str | None,
    new_dst_port: int | str | None,
) -> dict[str, Any]:
    old_endpoint_args = {
        "src_block": old_src_block,
        "src_port": old_src_port,
        "dst_block": old_dst_block,
        "dst_port": old_dst_port,
    }
    has_old_hint = any(value is not None for value in old_endpoint_args.values())

    if has_old_hint:
        resolved = agent.session.find_connection_candidates(**old_endpoint_args)
        candidates = resolved["candidates"]
        if not candidates:
            return {
                "ok": False,
                "message": "No existing old connection matches the provided endpoint fields.",
                "error_type": "connection_not_found",
                "state_revision": agent.session.state_revision,
            }
        if len(candidates) > 1:
            if not agent._rewire_new_endpoint_is_exact(
                new_src_block=new_src_block,
                new_src_port=new_src_port,
                new_dst_block=new_dst_block,
                new_dst_port=new_dst_port,
            ):
                return {
                    "ok": False,
                    "message": (
                        "Multiple old connections match. Provide an exact old "
                        "connection before resolving partial new endpoint hints."
                    ),
                    "error_type": "ambiguous_connection",
                    "state_revision": agent.session.state_revision,
                }
            return agent._rewire_clarification_payload(
                candidates,
                new_src_block=str(new_src_block),
                new_src_port=new_src_port,
                new_dst_block=str(new_dst_block),
                new_dst_port=new_dst_port,
            )
        resolved_connection_id = candidates[0]["connection_id"]
        if old_connection_id is not None and old_connection_id != resolved_connection_id:
            return {
                "ok": False,
                "message": (
                    "old_connection_id does not match the provided old endpoint fields: "
                    f"{old_connection_id}"
                ),
                "error_type": "connection_endpoint_mismatch",
                "state_revision": agent.session.state_revision,
            }
        return {"ok": True, "old_connection_id": resolved_connection_id}

    if not isinstance(old_connection_id, str) or not old_connection_id.strip():
        return {
            "ok": False,
            "message": (
                "rewire_connection requires old_connection_id or enough old "
                "endpoint fields to resolve one existing connection."
            ),
            "error_type": ErrorCode.TOOL_CALL_INVALID,
            "state_revision": agent.session.state_revision,
            "validation_errors": [
                {
                    "code": "missing_required",
                    "field": "old_connection_id",
                    "message": "Provide old_connection_id or old endpoint fields.",
                }
            ],
        }

    parsed = parse_connection_id(old_connection_id.strip())
    if parsed is None:
        return {
            "ok": False,
            "message": "old_connection_id must be in form src_block:src_port->dst_block:dst_port.",
            "error_type": ErrorCode.TOOL_CALL_INVALID,
            "state_revision": agent.session.state_revision,
        }
    src_block, src_port, dst_block, dst_port = parsed
    resolved = agent.session.find_connection_candidates(
        src_block=src_block,
        src_port=src_port,
        dst_block=dst_block,
        dst_port=dst_port,
    )
    candidates = resolved["candidates"]
    if not candidates:
        return {
            "ok": False,
            "message": f"Old connection not found: {old_connection_id.strip()}",
            "error_type": "connection_not_found",
            "state_revision": agent.session.state_revision,
        }
    if len(candidates) > 1:
        if not agent._rewire_new_endpoint_is_exact(
            new_src_block=new_src_block,
            new_src_port=new_src_port,
            new_dst_block=new_dst_block,
            new_dst_port=new_dst_port,
        ):
            return {
                "ok": False,
                "message": (
                    "Multiple old connections match. Provide an exact old "
                    "connection before resolving partial new endpoint hints."
                ),
                "error_type": "ambiguous_connection",
                "state_revision": agent.session.state_revision,
            }
        return agent._rewire_clarification_payload(
            candidates,
            new_src_block=str(new_src_block),
            new_src_port=new_src_port,
            new_dst_block=str(new_dst_block),
            new_dst_port=new_dst_port,
        )
    return {"ok": True, "old_connection_id": candidates[0]["connection_id"]}


__all__ = [
    "dispatch_flat_change_graph_batch",
    "has_flat_change_graph_batch",
]
