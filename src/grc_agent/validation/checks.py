"""Pure staged checks and snapshot simulation for preflight validation."""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from grc_agent.runtime.block_semantics import PortDomain
from grc_agent.runtime.tool_context import is_variable_block

logger = logging.getLogger(__name__)

from grc_agent._payload import ErrorCode
from grc_agent.catalog.loaders import get_catalog_snapshot
from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent._payload import Block, Connection
from grc_agent.session_ops import (
    block_name_is_referenced_elsewhere,
    connection_entry_to_tuple,
    connection_id,
    default_block_states,
    parse_blocks,
    parse_connections,
    raw_connection_entry,
)

from .errors import ValidationIssue, format_allowed_values, format_endpoint, format_port_range, make_issue
from .rules import (
    ResolvedPort,
    ValidationOperation,
    get_block_rules,
    resolve_port_slots,
    validate_block_asserts,
)


@dataclass
class SessionSnapshot:
    """A mutable staged copy of one loaded session."""

    raw_data: dict[str, Any]
    blocks: list[Block]
    connections: list[Connection]
    state_revision: int | None = None
    _eval_revision: int = field(default=0, init=False, repr=False, compare=False)
    _eval_params_cache: dict[str, dict[str, Any]] | None = field(
        default=None, init=False, repr=False, compare=False
    )
    _eval_errors_cache: list[tuple[str, str, str]] | None = field(
        default=None, init=False, repr=False, compare=False
    )

    @classmethod
    def from_session(cls, session: FlowgraphSession) -> SessionSnapshot:
        if session.flowgraph is None:
            raise ValueError("No flowgraph loaded.")
        raw_data = copy.deepcopy(session.flowgraph.raw_data)
        snapshot = cls.from_raw_data(raw_data)
        snapshot.state_revision = session.state_revision
        return snapshot

    @classmethod
    def from_raw_data(cls, raw_data: dict[str, Any]) -> SessionSnapshot:
        blocks = parse_blocks(raw_data.get("blocks"))
        connections = parse_connections(raw_data.get("connections"))
        return cls(raw_data=raw_data, blocks=blocks, connections=connections)

    def refresh(self) -> None:
        self.blocks = parse_blocks(self.raw_data.get("blocks"))
        self.connections = parse_connections(self.raw_data.get("connections"))
        self._eval_revision += 1
        self._eval_params_cache = None
        self._eval_errors_cache = None

    def raw_blocks(self) -> list[Any]:
        raw_blocks = self.raw_data.get("blocks")
        if not isinstance(raw_blocks, list):
            raise ValueError("Flowgraph raw_data blocks section is invalid.")
        return raw_blocks

    def raw_connections(self) -> list[Any]:
        raw_connections = self.raw_data.get("connections")
        if raw_connections is None:
            self.raw_data["connections"] = []
            return self.raw_data["connections"]
        if not isinstance(raw_connections, list):
            raise ValueError("Flowgraph raw_data connections section is invalid.")
        return raw_connections


def validate_and_apply_operation(
    snapshot: SessionSnapshot,
    operation: ValidationOperation,
    *,
    op_index: int,
    catalog_root: str | Path | None = None,
) -> tuple[list[ValidationIssue], list[ValidationIssue]]:
    """Validate one op against the staged snapshot and apply it if it passes."""
    if operation.op_type == "update_params":
        return _apply_update_params(
            snapshot,
            operation,
            op_index=op_index,
            catalog_root=catalog_root,
        )
    if operation.op_type == "update_states":
        return _apply_update_states(
            snapshot,
            operation,
            op_index=op_index,
        )
    if operation.op_type == "add_connection":
        return _apply_add_connection(
            snapshot,
            operation,
            op_index=op_index,
            catalog_root=catalog_root,
        )
    if operation.op_type == "remove_connection":
        return _apply_remove_connection(
            snapshot,
            operation,
            op_index=op_index,
        )
    if operation.op_type == "remove_block":
        return _apply_remove_block(
            snapshot,
            operation,
            op_index=op_index,
        )
    if operation.op_type == "add_block":
        return _apply_add_block(
            snapshot,
            operation,
            op_index=op_index,
            catalog_root=catalog_root,
        )
    if operation.op_type == "insert_block_on_connection":
        return _apply_insert_block_on_connection(
            snapshot,
            operation,
            op_index=op_index,
            catalog_root=catalog_root,
        )
    raise ValueError(f"Unsupported validation op_type: {operation.op_type}")


def _apply_update_params(
    snapshot: SessionSnapshot,
    operation: ValidationOperation,
    *,
    op_index: int,
    catalog_root: str | Path | None,
) -> tuple[list[ValidationIssue], list[ValidationIssue]]:
    op_type = operation.op_type
    instance_name = operation.payload.get("instance_name", "")
    block_type = operation.payload.get("block_type")
    params = operation.payload["params"]

    block, raw_block, _raw_index, issues = _require_unique_block(
        snapshot,
        instance_name,
        block_type=block_type,
        op_index=op_index,
        op_type=op_type,
        field="instance_name",
    )
    if issues:
        return issues, []
    assert block is not None
    assert raw_block is not None

    lookup, lookup_issue = _require_block_rules(
        block.block_type,
        op_index=op_index,
        op_type=op_type,
        field="params",
        catalog_root=catalog_root,
    )
    if lookup_issue is not None:
        return [lookup_issue], []
    assert lookup is not None

    parameter_issues = _validate_parameter_updates(
        block_type=block.block_type,
        params=params,
        parameter_rules=lookup.parameters,
        allowed_parameter_ids=set(raw_block.get("parameters", {}))
        if isinstance(raw_block.get("parameters"), dict)
        else set(),
        op_index=op_index,
        op_type=op_type,
        field_prefix="params",
    )
    if parameter_issues:
        return parameter_issues, []

    raw_parameters = raw_block.get("parameters")
    if not isinstance(raw_parameters, dict):
        return [
            make_issue(
                op_index=op_index,
                op_type=op_type,
                field="params",
                code="invalid_parameter_section",
                message=f"Block parameters section is invalid for: {instance_name}",
            )
        ], []

    for parameter_id, value in params.items():
        raw_parameters[parameter_id] = copy.deepcopy(value)
    snapshot.refresh()
    return [], []


def _apply_update_states(
    snapshot: SessionSnapshot,
    operation: ValidationOperation,
    *,
    op_index: int,
) -> tuple[list[ValidationIssue], list[ValidationIssue]]:
    op_type = operation.op_type
    instance_name = operation.payload.get("instance_name", "")
    block_type = operation.payload.get("block_type")
    state = operation.payload["state"]

    _block, raw_block, _raw_index, issues = _require_unique_block(
        snapshot,
        instance_name,
        block_type=block_type,
        op_index=op_index,
        op_type=op_type,
        field="instance_name",
    )
    if issues:
        return issues, []
    assert raw_block is not None

    raw_states = raw_block.get("states")
    if raw_states is None:
        raw_states = {}
        raw_block["states"] = raw_states
    if not isinstance(raw_states, dict):
        return [
            make_issue(
                op_index=op_index,
                op_type=op_type,
                field="state",
                code="invalid_states_section",
                message=f"Block states section is invalid for: {instance_name}",
            )
        ], []

    raw_states["state"] = state
    snapshot.refresh()
    return [], []


def _fill_parameter_defaults(
    caller_params: dict[str, Any],
    *,
    parameter_rules: dict[str, Any],
) -> dict[str, Any]:
    merged = copy.deepcopy(caller_params)
    for param_id, rule in parameter_rules.items():
        if param_id in merged:
            continue
        default = rule.default if hasattr(rule, "default") else rule.get("default")
        if default is None:
            continue
        options = rule.options if hasattr(rule, "options") else rule.get("options")
        if options and str(default) not in [str(o) for o in options]:
            continue
        merged[param_id] = str(default)
    return merged


def _apply_add_block(
    snapshot: SessionSnapshot,
    operation: ValidationOperation,
    *,
    op_index: int,
    catalog_root: str | Path | None,
) -> tuple[list[ValidationIssue], list[ValidationIssue]]:
    op_type = operation.op_type
    instance_name = operation.payload.get("instance_name", "")
    block_type = operation.payload["block_type"]
    parameters = operation.payload["parameters"]
    states = operation.payload.get("states")

    lookup, lookup_issue = _require_block_rules(
        block_type,
        op_index=op_index,
        op_type=op_type,
        field="block_type",
        catalog_root=catalog_root,
        block_not_found_code="unknown_block_id",
    )
    if lookup_issue is not None:
        return [lookup_issue], []
    assert lookup is not None

    raw_blocks = snapshot.raw_blocks()
    name_issues = _assert_new_block_name_available(
        snapshot,
        instance_name,
        raw_blocks,
        op_index=op_index,
        op_type=op_type,
    )
    if name_issues:
        return name_issues, []

    if is_variable_block(block_type):
        # Variable blocks have an implicit ``comment`` parameter added by
        # GRC's _build.py that the YAML-only catalog loader never sees.
        # Keep the allowlist until the catalog loader is taught to merge
        # implicit params from the GRC Block class.
        parameter_issues = _validate_parameter_updates(
            block_type=block_type,
            params=parameters,
            parameter_rules=lookup.parameters,
            allowed_parameter_ids={"comment"},
            op_index=op_index,
            op_type=op_type,
            field_prefix="parameters",
        )
        if parameter_issues:
            return parameter_issues, []
        if "value" not in parameters:
            return [
                make_issue(
                    op_index=op_index,
                    op_type=op_type,
                    field="parameters.value",
                    code="missing_required_param",
                    message="Detached variable blocks require parameters.value.",
                )
            ], []
        raw_parameters = copy.deepcopy(parameters)
        raw_parameters.setdefault("comment", "")
    else:
        raw_parameters = _fill_parameter_defaults(
            parameters,
            parameter_rules=lookup.parameters,
        )
        parameter_issues = _validate_parameter_updates(
            block_type=block_type,
            params=raw_parameters,
            parameter_rules=lookup.parameters,
            op_index=op_index,
            op_type=op_type,
            field_prefix="parameters",
        )
        if parameter_issues:
            return parameter_issues, []
        operation.payload["parameters"] = copy.deepcopy(raw_parameters)

    raw_states = (
        copy.deepcopy(states)
        if states is not None
        else default_block_states(existing_block_count=len(raw_blocks))
    )
    raw_blocks.append(
        {
            "name": instance_name,
            "id": block_type,
            "parameters": raw_parameters,
            "states": raw_states,
        }
    )
    snapshot.refresh()
    return [], []


def _apply_insert_block_on_connection(
    snapshot: SessionSnapshot,
    operation: ValidationOperation,
    *,
    op_index: int,
    catalog_root: str | Path | None,
) -> tuple[list[ValidationIssue], list[ValidationIssue]]:
    op_type = operation.op_type
    payload = operation.payload
    connection_id_str = payload["connection_id"]
    block_type = payload["block_type"]
    instance_name = payload["instance_name"]
    params = payload.get("params", {})
    states = payload.get("states")

    # 1. Resolve connection_id
    target_conn: Connection | None = None
    for conn in snapshot.connections:
        if (
            connection_id(conn.src_block, conn.src_port, conn.dst_block, conn.dst_port)
            == connection_id_str
        ):
            target_conn = conn
            break

    if target_conn is None:
        return [
            make_issue(
                op_index=op_index,
                op_type=op_type,
                field="connection_id",
                code="connection_not_found",
                message=f"Connection not found: {connection_id_str}",
            )
        ], []

    # 2. Reject if the connection uses string ports (message-domain)
    if isinstance(target_conn.src_port, str) or isinstance(target_conn.dst_port, str):
        return [
            make_issue(
                op_index=op_index,
                op_type=op_type,
                field="connection_id",
                code="message_connection_not_supported",
                message=f"Message connections are not supported for insert_block_on_connection: {connection_id_str}",
            )
        ], []

    # 3. Reject if either endpoint block is disabled/stated off
    def _block_state(name: str) -> str:
        for entry in snapshot.raw_blocks():
            if isinstance(entry, dict) and entry.get("name") == name:
                raw_states = entry.get("states")
                if isinstance(raw_states, dict):
                    return raw_states.get("state", "enabled")
                return "enabled"
        return "enabled"

    if _block_state(target_conn.src_block) != "enabled" or _block_state(target_conn.dst_block) != "enabled":
        return [
            make_issue(
                op_index=op_index,
                op_type=op_type,
                field="connection_id",
                code="disabled_connection_not_supported",
                message=f"Cannot insert on a connection with disabled blocks: {connection_id_str}",
            )
        ], []

    # 4. Reject if instance_name already exists
    name_issues = _assert_new_block_name_available(
        snapshot,
        instance_name,
        snapshot.raw_blocks(),
        op_index=op_index,
        op_type=op_type,
    )
    if name_issues:
        return name_issues, []

    # 5-9. Reuse add_block validation and simulate adding the block
    add_block_payload: dict[str, Any] = {
        "instance_name": instance_name,
        "block_type": block_type,
        "parameters": copy.deepcopy(params),
    }
    if states is not None:
        add_block_payload["states"] = copy.deepcopy(states)

    add_block_op = ValidationOperation(op_type="add_block", payload=add_block_payload)
    add_errors, add_warnings = _apply_add_block(
        snapshot,
        add_block_op,
        op_index=op_index,
        catalog_root=catalog_root,
    )
    if add_errors:
        return add_errors, add_warnings
    warnings = list(add_warnings)

    # 10. Resolve inserted-block ports deterministically
    resolved_input_port, resolved_output_port, port_issues = _resolve_single_stream_ports(
        snapshot=snapshot,
        instance_name=instance_name,
        block_type=block_type,
        parameters=params,
        catalog_root=catalog_root,
        op_index=op_index,
        op_type=op_type,
    )
    if port_issues:
        return port_issues, []

    # 11. Remove the original connection
    remove_conn_payload: dict[str, Any] = {
        "src_block": target_conn.src_block,
        "src_port": target_conn.src_port,
        "dst_block": target_conn.dst_block,
        "dst_port": target_conn.dst_port,
    }
    remove_conn_op = ValidationOperation(op_type="remove_connection", payload=remove_conn_payload)
    rem_errors, rem_warnings = _apply_remove_connection(
        snapshot,
        remove_conn_op,
        op_index=op_index,
    )
    if rem_errors:
        return rem_errors, rem_warnings
    warnings.extend(rem_warnings)

    # 12. Add src->new connection
    conn1_payload: dict[str, Any] = {
        "src_block": target_conn.src_block,
        "src_port": target_conn.src_port,
        "dst_block": instance_name,
        "dst_port": resolved_input_port,
    }
    conn1_op = ValidationOperation(op_type="add_connection", payload=conn1_payload)
    c1_errors, c1_warnings = _apply_add_connection(
        snapshot,
        conn1_op,
        op_index=op_index,
        catalog_root=catalog_root,
    )
    if c1_errors:
        return c1_errors, c1_warnings
    warnings.extend(c1_warnings)

    # 13. Add new->dst connection
    conn2_payload: dict[str, Any] = {
        "src_block": instance_name,
        "src_port": resolved_output_port,
        "dst_block": target_conn.dst_block,
        "dst_port": target_conn.dst_port,
    }
    conn2_op = ValidationOperation(op_type="add_connection", payload=conn2_payload)
    c2_errors, c2_warnings = _apply_add_connection(
        snapshot,
        conn2_op,
        op_index=op_index,
        catalog_root=catalog_root,
    )
    if c2_errors:
        return c2_errors, c2_warnings
    warnings.extend(c2_warnings)

    return [], warnings


def _resolve_single_stream_ports(
    snapshot: SessionSnapshot,
    instance_name: str,
    *,
    block_type: str,
    parameters: dict[str, Any],
    catalog_root: str | Path | None,
    op_index: int,
    op_type: str,
) -> tuple[int, int, list[ValidationIssue]]:
    """Resolve exactly one stream input and one stream output port for an inserted block.

    Returns (input_port_index, output_port_index, issues).
    If resolution fails, returns (0, 0, [issue, ...]).
    """
    lookup = get_block_rules(block_type, catalog_root=catalog_root)
    if not lookup.ok:
        return 0, 0, [
            make_issue(
                op_index=op_index,
                op_type=op_type,
                field="block_type",
                code="catalog_block_unavailable",
                message=f"Cannot insert unknown block type: {block_type}",
            )
        ]

    evaluated_parameters_by_block, _ = _evaluate_snapshot_params(snapshot)
    eval_params = evaluated_parameters_by_block.get(instance_name)
    if eval_params is None:
        eval_params = parameters

    ins, _warn1 = resolve_port_slots(
        block_rules=lookup.rules,
        parameters=eval_params,
        direction="inputs",
    )
    outs, _warn2 = resolve_port_slots(
        block_rules=lookup.rules,
        parameters=eval_params,
        direction="outputs",
    )

    stream_inputs = [(i, p) for i, p in enumerate(ins) if p.domain == PortDomain.STREAM]
    stream_outputs = [(i, p) for i, p in enumerate(outs) if p.domain == PortDomain.STREAM]

    if not stream_inputs or not stream_outputs:
        return 0, 0, [
            make_issue(
                op_index=op_index,
                op_type=op_type,
                field="block_type",
                code="insert_incompatible_ports",
                message=f"Block {block_type} has no compatible stream input/output ports.",
            )
        ]

    if len(stream_inputs) != 1 or len(stream_outputs) != 1:
        return 0, 0, [
            make_issue(
                op_index=op_index,
                op_type=op_type,
                field="block_type",
                code="insert_port_resolution_failed",
                message=f"Block {block_type} has ambiguous stream ports (in={len(stream_inputs)}, out={len(stream_outputs)}).",
            )
        ]

    return stream_inputs[0][0], stream_outputs[0][0], []


def _apply_remove_block(
    snapshot: SessionSnapshot,
    operation: ValidationOperation,
    *,
    op_index: int,
) -> tuple[list[ValidationIssue], list[ValidationIssue]]:
    op_type = operation.op_type
    instance_name = operation.payload["instance_name"]
    block_type = operation.payload.get("block_type")

    block, _raw_block, raw_index, issues = _require_unique_block(
        snapshot,
        instance_name,
        block_type=block_type,
        op_index=op_index,
        op_type=op_type,
        field="instance_name",
    )
    if issues:
        return issues, []
    assert block is not None
    assert raw_index is not None

    if any(
        connection.src_block == instance_name or connection.dst_block == instance_name
        for connection in snapshot.connections
    ):
        return [
            make_issue(
                op_index=op_index,
                op_type=op_type,
                field="instance_name",
                code="connected_block",
                message=f"Cannot remove connected block: {instance_name}",
            )
        ], []

    if block_name_is_referenced_elsewhere(
        raw_data=snapshot.raw_data,
        instance_name=instance_name,
        ignored_raw_block_index=raw_index,
    ):
        return [
            make_issue(
                op_index=op_index,
                op_type=op_type,
                field="instance_name",
                code="block_still_referenced",
                message=f"Block is still referenced elsewhere: {instance_name}",
            )
        ], []

    del snapshot.raw_blocks()[raw_index]
    snapshot.refresh()
    return [], []


def _apply_remove_connection(
    snapshot: SessionSnapshot,
    operation: ValidationOperation,
    *,
    op_index: int,
) -> tuple[list[ValidationIssue], list[ValidationIssue]]:
    op_type = operation.op_type
    payload = operation.payload
    connection_ref = payload.get("connection_id")

    target: tuple | None = None
    if all(
        field_name in payload
        for field_name in ("src_block", "src_port", "dst_block", "dst_port")
    ):
        target = (
            payload["src_block"],
            payload["src_port"],
            payload["dst_block"],
            payload["dst_port"],
        )

    if isinstance(connection_ref, str):
        matching_targets = [
            (
                connection.src_block,
                connection.src_port,
                connection.dst_block,
                connection.dst_port,
            )
            for connection in snapshot.connections
            if connection_id(
                connection.src_block,
                connection.src_port,
                connection.dst_block,
                connection.dst_port,
            )
            == connection_ref
        ]
        if not matching_targets:
            return [
                make_issue(
                    op_index=op_index,
                    op_type=op_type,
                    field="connection_id",
                    code="connection_not_found",
                    message=f"Connection not found: {connection_ref}",
                )
            ], []
        if len(matching_targets) > 1:
            return [
                make_issue(
                    op_index=op_index,
                    op_type=op_type,
                    field="connection_id",
                    code="ambiguous_connection",
                    message=f"Connection id resolves to multiple edges: {connection_ref}",
                )
            ], []

        resolved_target = matching_targets[0]
        if target is not None and target != resolved_target:
            return [
                make_issue(
                    op_index=op_index,
                    op_type=op_type,
                    field="connection_id",
                    code="connection_endpoint_mismatch",
                    message=(
                        "connection_id does not match the provided endpoints: "
                        f"{connection_ref}"
                    ),
                )
            ], []
        target = resolved_target
        payload["src_block"] = resolved_target[0]
        payload["src_port"] = resolved_target[1]
        payload["dst_block"] = resolved_target[2]
        payload["dst_port"] = resolved_target[3]
        payload.pop("connection_id", None)

    assert target is not None

    raw_connections = snapshot.raw_connections()
    raw_index = next(
        (
            index
            for index, entry in enumerate(raw_connections)
            if connection_entry_to_tuple(entry) == target
        ),
        None,
    )
    if raw_index is None:
        return [
            make_issue(
                op_index=op_index,
                op_type=op_type,
                field="connection",
                code="connection_not_found",
                message=f"Connection not found: {target}",
            )
        ], []

    del raw_connections[raw_index]
    snapshot.refresh()
    return [], []


def _apply_add_connection(
    snapshot: SessionSnapshot,
    operation: ValidationOperation,
    *,
    op_index: int,
    catalog_root: str | Path | None,
) -> tuple[list[ValidationIssue], list[ValidationIssue]]:
    op_type = operation.op_type
    src_block_name = operation.payload["src_block"]
    src_port = operation.payload["src_port"]
    dst_block_name = operation.payload["dst_block"]
    dst_port = operation.payload["dst_port"]

    src_block, _src_raw, _src_index, src_issues = _require_unique_block(
        snapshot,
        src_block_name,
        op_index=op_index,
        op_type=op_type,
        field="src_block",
    )
    dst_block, _dst_raw, _dst_index, dst_issues = _require_unique_block(
        snapshot,
        dst_block_name,
        op_index=op_index,
        op_type=op_type,
        field="dst_block",
    )
    if src_issues or dst_issues:
        return src_issues + dst_issues, []
    assert src_block is not None
    assert dst_block is not None

    target = (src_block_name, src_port, dst_block_name, dst_port)
    if any(
        (
            connection.src_block,
            connection.src_port,
            connection.dst_block,
            connection.dst_port,
        )
        == target
        for connection in snapshot.connections
    ):
        return [
            make_issue(
                op_index=op_index,
                op_type=op_type,
                field="connection",
                code="duplicate_connection",
                message=f"Connection already exists: {target}",
            )
        ], []

    src_rules, src_issue = _require_block_rules(
        src_block.block_type,
        op_index=op_index,
        op_type=op_type,
        field="src_block",
        catalog_root=catalog_root,
    )
    dst_rules, dst_issue = _require_block_rules(
        dst_block.block_type,
        op_index=op_index,
        op_type=op_type,
        field="dst_block",
        catalog_root=catalog_root,
    )
    if src_issue is not None or dst_issue is not None:
        return [issue for issue in (src_issue, dst_issue) if issue is not None], []
    assert src_rules is not None
    assert dst_rules is not None

    # Evaluate snapshot parameters to get accurately resolved ports
    evaluated_parameters_by_block, _ = _evaluate_snapshot_params(snapshot)

    src_parameters = evaluated_parameters_by_block.get(src_block_name)
    if src_parameters is None:
        src_parameters = src_block.params.get("parameters")

    dst_parameters = evaluated_parameters_by_block.get(dst_block_name)
    if dst_parameters is None:
        dst_parameters = dst_block.params.get("parameters")

    if not isinstance(src_parameters, dict) or not isinstance(dst_parameters, dict):
        return [
            make_issue(
                op_index=op_index,
                op_type=op_type,
                field="connection",
                code="invalid_parameter_section",
                message="Block parameters are invalid for connection validation.",
            )
        ], []

    src_ports, src_warnings = resolve_port_slots(
        block_rules=src_rules,
        parameters=src_parameters,
        direction="outputs",
    )
    dst_ports, dst_warnings = resolve_port_slots(
        block_rules=dst_rules,
        parameters=dst_parameters,
        direction="inputs",
    )

    warnings = _warnings_from_port_resolution(
        src_warnings,
        op_index=op_index,
        op_type=op_type,
        field="src_port",
    ) + _warnings_from_port_resolution(
        dst_warnings,
        op_index=op_index,
        op_type=op_type,
        field="dst_port",
    )

    src_port_issue = _validate_port_index(
        ports=src_ports,
        port_index=src_port,
        block_name=src_block_name,
        op_index=op_index,
        op_type=op_type,
        field="src_port",
        direction="output",
    )
    dst_port_issue = _validate_port_index(
        ports=dst_ports,
        port_index=dst_port,
        block_name=dst_block_name,
        op_index=op_index,
        op_type=op_type,
        field="dst_port",
        direction="input",
    )
    if src_port_issue is not None or dst_port_issue is not None:
        return [issue for issue in (src_port_issue, dst_port_issue) if issue is not None], warnings

    if isinstance(src_port, str) or isinstance(dst_port, str):
        has_duplicate_dst = any(
            connection.dst_block == dst_block_name and connection.dst_port == dst_port
            for connection in snapshot.connections
        )
        if has_duplicate_dst:
            return [
                make_issue(
                    op_index=op_index,
                    op_type=op_type,
                    field="dst_port",
                    code="occupied_input_port",
                    message=f"Input port is already connected: {format_endpoint(dst_block_name, dst_port)}",
                )
            ], warnings
        return [], warnings

    source_port = src_ports[src_port]
    destination_port = dst_ports[dst_port]

    if (
        source_port.domain is not None
        and destination_port.domain is not None
        and source_port.domain != destination_port.domain
    ):
        return [
            make_issue(
                op_index=op_index,
                op_type=op_type,
                field="connection",
                code="incompatible_domain",
                message=(
                    f"Cannot connect {format_endpoint(src_block_name, src_port)} "
                    f"({source_port.domain}) to {format_endpoint(dst_block_name, dst_port)} "
                    f"({destination_port.domain})."
                ),
            )
        ], warnings

    if any(
        connection.dst_block == dst_block_name and connection.dst_port == dst_port
        for connection in snapshot.connections
    ) and destination_port.domain != PortDomain.MESSAGE:
        return [
            make_issue(
                op_index=op_index,
                op_type=op_type,
                field="dst_port",
                code="occupied_input_port",
                message=f"Input port is already connected: {format_endpoint(dst_block_name, dst_port)}",
            )
        ], warnings

    if (
        source_port.domain == PortDomain.STREAM
        and destination_port.domain == PortDomain.STREAM
        and source_port.dtype is not None
        and destination_port.dtype is not None
        and source_port.dtype != destination_port.dtype
    ):
        specific_hint = _preflight_dtype_param_hint(
            dst_block,
            port_direction="inputs",
            port_id=dst_port,
            desired_dtype=source_port.dtype,
            catalog_root=catalog_root,
        )
        if not specific_hint:
            specific_hint = _preflight_dtype_param_hint(
                src_block,
                port_direction="outputs",
                port_id=src_port,
                desired_dtype=destination_port.dtype,
                catalog_root=catalog_root,
            )
        hint_text = specific_hint
        return [
            make_issue(
                op_index=op_index,
                op_type=op_type,
                field="connection",
                code="incompatible_dtype",
                message=(
                    f"Source IO type \"{source_port.dtype}\" does not match sink IO type \"{destination_port.dtype}\" "
                    f"connecting {format_endpoint(src_block_name, src_port)} to {format_endpoint(dst_block_name, dst_port)}."
                ),
                hint=hint_text,
            )
        ], warnings

    if (
        source_port.domain == PortDomain.STREAM
        and destination_port.domain == PortDomain.STREAM
        and source_port.vlen is not None
        and destination_port.vlen is not None
        and source_port.vlen != destination_port.vlen
    ):
        vlen_hint = _preflight_vlen_param_hint(
            src_block,
            src_port,
            source_port.vlen,
            destination_port.vlen,
            catalog_root=catalog_root,
        ) or _preflight_vlen_param_hint(
            dst_block,
            dst_port,
            destination_port.vlen,
            source_port.vlen,
            catalog_root=catalog_root,
        )
        return [
            make_issue(
                op_index=op_index,
                op_type=op_type,
                field="connection",
                code="incompatible_vlen",
                message=(
                    f"Cannot connect {format_endpoint(src_block_name, src_port)} "
                    f"(vlen={source_port.vlen}) to {format_endpoint(dst_block_name, dst_port)} "
                    f"(vlen={destination_port.vlen})."
                ),
                hint=vlen_hint,
            )
        ], warnings

    snapshot.raw_connections().append(
        raw_connection_entry(src_block_name, src_port, dst_block_name, dst_port)
    )
    snapshot.refresh()
    return [], warnings


def _is_name_error(e: Exception) -> bool:
    if isinstance(e, NameError):
        return True
    if isinstance(getattr(e, "__context__", None), NameError):
        return True
    if isinstance(getattr(e, "__cause__", None), NameError):
        return True
    return False


_cached_platform: Any = None


def _evaluate_snapshot_params(
    snapshot: SessionSnapshot,
    *,
    affected_block_names: set[str] | None = None,
    op_index: int = 0,
) -> tuple[dict[str, dict[str, Any]], list[ValidationIssue]]:
    """Evaluate block parameters natively using GRC Platform.

    Results are memoized on the snapshot keyed on its eval revision,
    so repeated calls within one preflight (e.g. the insert path) skip
    the expensive Platform rebuild + import + evaluate cycle.
    """
    if snapshot._eval_params_cache is not None:
        evaluated_parameters_by_block = snapshot._eval_params_cache
        errors: list[ValidationIssue] = []
        if affected_block_names is not None and snapshot._eval_errors_cache is not None:
            for block_name, param_key, err_msg in snapshot._eval_errors_cache:
                if block_name in affected_block_names:
                    errors.append(
                        make_issue(
                            op_index=op_index,
                            op_type="transaction",
                            field=f"params.{block_name}.{param_key}",
                            code="parameter_evaluation_failed",
                            message=f"Block {block_name} parameter '{param_key}' failed evaluation: {err_msg}",
                        )
                    )
        return evaluated_parameters_by_block, errors

    evaluated_parameters_by_block = {}
    raw_errors: list[tuple[str, str, str]] = []

    global _cached_platform
    if _cached_platform is None:
        try:
            from gnuradio import gr
            from gnuradio.grc.core.platform import Platform

            _cached_platform = Platform(
                version=gr.version(),
                version_parts=(gr.major_version(), gr.api_version(), gr.minor_version()),
                prefs=gr.prefs(),
            )
            _cached_platform.build_library()
        except Exception as e:
            logger.debug("Failed to initialize GRC Platform for preflight parameter validation: %s", e)
            _cached_platform = None

    if _cached_platform is not None:
        try:
            fg = _cached_platform.make_flow_graph()

            orig_eval = fg.evaluate
            def custom_eval(expr, namespace=None, local_namespace=None):
                try:
                    return orig_eval(expr, namespace, local_namespace)
                except NameError as e:
                    import importlib
                    import re
                    match = re.search(r"name '([A-Za-z0-9_]+)' is not defined", str(e))
                    if match:
                        name = match.group(1)
                        try:
                            try:
                                mod = importlib.import_module(f"gnuradio.{name}")
                            except ImportError:
                                mod = importlib.import_module(name)
                            fg.namespace[name] = mod
                            if name == "filter":
                                fg.namespace["firdes"] = mod.firdes
                            return orig_eval(expr, namespace, local_namespace)
                        except ImportError:
                            pass
                    raise
            fg.evaluate = custom_eval

            fg.import_data(snapshot.raw_data)

            for grc_block in fg.blocks:
                if grc_block.is_import:
                    for param in grc_block.params.values():
                        try:
                            param.evaluate()
                        except Exception:
                            pass

            for grc_block in fg.blocks:
                block_eval_params = {}
                for param in grc_block.params.values():
                    try:
                        val = param.evaluate()
                        block_eval_params[param.key] = val
                    except Exception as e:
                        if not _is_name_error(e):
                            raw_errors.append((grc_block.name, param.key, str(e)))
                        block_eval_params[param.key] = param.get_value()
                evaluated_parameters_by_block[grc_block.name] = block_eval_params
        except Exception as e:
            logger.debug("Snapshot native evaluation failed: %s", e)

    snapshot._eval_params_cache = evaluated_parameters_by_block
    snapshot._eval_errors_cache = raw_errors

    errors = []
    if affected_block_names is not None:
        for block_name, param_key, err_msg in raw_errors:
            if block_name in affected_block_names:
                errors.append(
                    make_issue(
                        op_index=op_index,
                        op_type="transaction",
                        field=f"params.{block_name}.{param_key}",
                        code="parameter_evaluation_failed",
                        message=f"Block {block_name} parameter '{param_key}' failed evaluation: {err_msg}",
                    )
                )

    return evaluated_parameters_by_block, errors


def validate_snapshot_integrity(
    snapshot: SessionSnapshot,
    *,
    affected_block_names: set[str],
    op_index: int,
    catalog_root: str | Path | None = None,
) -> tuple[list[ValidationIssue], list[ValidationIssue]]:
    """Revalidate affected blocks and adjacent connections after staged edits."""
    if not affected_block_names:
        return [], []

    warnings: list[ValidationIssue] = []
    errors: list[ValidationIssue] = []

    duplicate_issue = _validate_enabled_symbol_uniqueness(snapshot, op_index=op_index)
    if duplicate_issue is not None:
        return [duplicate_issue], []

    # Native GRC parameter evaluation and validation
    evaluated_parameters_by_block, eval_errors = _evaluate_snapshot_params(
        snapshot,
        affected_block_names=affected_block_names,
        op_index=op_index,
    )
    errors.extend(eval_errors)

    # If we already have evaluation errors, return them immediately
    if errors:
        return errors, warnings

    connected_neighbors = {
        connection.src_block
        for connection in snapshot.connections
        if connection.src_block in affected_block_names or connection.dst_block in affected_block_names
    } | {
        connection.dst_block
        for connection in snapshot.connections
        if connection.src_block in affected_block_names or connection.dst_block in affected_block_names
    }
    block_names_to_check = affected_block_names | connected_neighbors

    resolved_blocks: dict[str, tuple[list[ResolvedPort], list[ResolvedPort], str | None]] = {}
    for block_name in sorted(block_names_to_check):
        raw_block = next(
            (
                entry
                for entry in snapshot.raw_blocks()
                if isinstance(entry, dict) and entry.get("name") == block_name
            ),
            None,
        )
        block = next((item for item in snapshot.blocks if item.instance_name == block_name), None)
        if raw_block is None or block is None:
            continue

        parameters = evaluated_parameters_by_block.get(block_name)
        if parameters is None:
            parameters = raw_block.get("parameters")

        if not isinstance(parameters, dict):
            errors.append(
                make_issue(
                    op_index=op_index,
                    op_type="transaction",
                    field="operations",
                    code="invalid_parameter_section",
                    message=f"Block parameters section is invalid for: {block_name}",
                )
            )
            continue

        rules, rule_issue = _require_block_rules(
            block.block_type,
            op_index=op_index,
            op_type="transaction",
            field="operations",
            catalog_root=catalog_root,
        )
        if rule_issue is not None:
            errors.append(rule_issue)
            continue
        assert rules is not None

        assert_failures, assert_warnings = validate_block_asserts(
            block_rules=rules,
            parameters=parameters,
        )
        warnings.extend(
            make_issue(
                op_index=op_index,
                op_type="transaction",
                field="operations",
                code="block_assert_unresolved",
                message=warning,
            )
            for warning in assert_warnings
        )
        if assert_failures:
            errors.append(
                make_issue(
                    op_index=op_index,
                    op_type="transaction",
                    field="operations",
                    code="block_assert_failed",
                    message=f"Block assertion failed for {block_name}: {assert_failures[0]}",
                )
            )
            continue

        input_ports, input_warnings = resolve_port_slots(
            block_rules=rules,
            parameters=parameters,
            direction="inputs",
        )
        output_ports, output_warnings = resolve_port_slots(
            block_rules=rules,
            parameters=parameters,
            direction="outputs",
        )
        warnings.extend(
            _warnings_from_port_resolution(
                input_warnings,
                op_index=op_index,
                op_type="transaction",
                field="operations",
            )
        )
        warnings.extend(
            _warnings_from_port_resolution(
                output_warnings,
                op_index=op_index,
                op_type="transaction",
                field="operations",
            )
        )

        states = raw_block.get("states")
        state_text = states.get("state") if isinstance(states, dict) else None
        resolved_blocks[block_name] = (input_ports, output_ports, state_text)

    if errors:
        return errors, warnings

    input_counts: dict[tuple, int] = {}
    for connection in snapshot.connections:
        if connection.dst_block in resolved_blocks:
            key = (connection.dst_block, connection.dst_port)
            input_counts[key] = input_counts.get(key, 0) + 1

    for connection in snapshot.connections:
        if (
            connection.src_block not in affected_block_names
            and connection.dst_block not in affected_block_names
        ):
            continue
        src_tuple = resolved_blocks.get(connection.src_block, ([], [], None))
        dst_tuple = resolved_blocks.get(connection.dst_block, ([], [], None))
        src_outputs = src_tuple[1]
        dst_inputs = dst_tuple[0]

        src_issue = _validate_port_index(
            ports=src_outputs,
            port_index=connection.src_port,
            block_name=connection.src_block,
            op_index=op_index,
            op_type="transaction",
            field="operations",
            direction="output",
        )
        dst_issue = _validate_port_index(
            ports=dst_inputs,
            port_index=connection.dst_port,
            block_name=connection.dst_block,
            op_index=op_index,
            op_type="transaction",
            field="operations",
            direction="input",
        )
        if src_issue is not None or dst_issue is not None:
            return [issue for issue in (src_issue, dst_issue) if issue is not None], warnings

        if isinstance(connection.src_port, str) or isinstance(connection.dst_port, str):
            continue

        source_port = src_outputs[connection.src_port]
        destination_port = dst_inputs[connection.dst_port]

        if (
            source_port.domain is not None
            and destination_port.domain is not None
            and source_port.domain != destination_port.domain
        ):
            return [
                make_issue(
                    op_index=op_index,
                    op_type="transaction",
                    field="operations",
                    code="incompatible_domain",
                    message=(
                        f"Existing connection became invalid: "
                        f"{format_endpoint(connection.src_block, connection.src_port)} "
                        f"({source_port.domain}) -> {format_endpoint(connection.dst_block, connection.dst_port)} "
                        f"({destination_port.domain})."
                    ),
                )
            ], warnings

        if (
            destination_port.domain != PortDomain.MESSAGE
            and input_counts.get((connection.dst_block, connection.dst_port), 0) > 1
        ):
            return [
                make_issue(
                    op_index=op_index,
                    op_type="transaction",
                    field="operations",
                    code="occupied_input_port",
                    message=(
                        "Existing connections became invalid because one input port now has "
                        f"multiple stream connections: {format_endpoint(connection.dst_block, connection.dst_port)}"
                    ),
                )
            ], warnings

        if (
            source_port.domain == PortDomain.STREAM
            and destination_port.domain == PortDomain.STREAM
            and source_port.dtype is not None
            and destination_port.dtype is not None
            and source_port.dtype != destination_port.dtype
        ):
            return [
                make_issue(
                    op_index=op_index,
                    op_type="transaction",
                    field="operations",
                    code="incompatible_dtype",
                    message=(
                        f"Existing connection became invalid: "
                        f"{format_endpoint(connection.src_block, connection.src_port)} "
                        f"({source_port.dtype}) -> {format_endpoint(connection.dst_block, connection.dst_port)} "
                        f"({destination_port.dtype})."
                    ),
                )
            ], warnings

        if (
            source_port.domain == PortDomain.STREAM
            and destination_port.domain == PortDomain.STREAM
            and source_port.vlen is not None
            and destination_port.vlen is not None
            and source_port.vlen != destination_port.vlen
        ):
            return [
                make_issue(
                    op_index=op_index,
                    op_type="transaction",
                    field="operations",
                    code="incompatible_vlen",
                    message=(
                        f"Existing connection became invalid: "
                        f"{format_endpoint(connection.src_block, connection.src_port)} "
                        f"(vlen={source_port.vlen}) -> {format_endpoint(connection.dst_block, connection.dst_port)} "
                        f"(vlen={destination_port.vlen})."
                    ),
                )
            ], warnings

    return [], warnings


def _require_unique_block(
    snapshot: SessionSnapshot,
    instance_name: str,
    *,
    block_type: str | None = None,
    op_index: int,
    op_type: str,
    field: str,
) -> tuple[Block | None, dict[str, Any] | None, int | None, list[ValidationIssue]]:
    parsed_matches = [
        block
        for block in snapshot.blocks
        if block.instance_name == instance_name
        and (block_type is None or block.block_type == block_type)
    ]
    raw_matches = [
        (index, entry)
        for index, entry in enumerate(snapshot.raw_blocks())
        if isinstance(entry, dict)
        and entry.get("name") == instance_name
        and (block_type is None or entry.get("id") == block_type)
    ]

    if not parsed_matches or not raw_matches:
        message = f"Block not found: {instance_name}"
        if block_type:
            message += f" (type: {block_type})"
        return None, None, None, [
            make_issue(
                op_index=op_index,
                op_type=op_type,
                field=field,
                code="block_not_found",
                message=message,
            )
        ]

    if len(parsed_matches) != 1 or len(raw_matches) != 1:
        message = f"Block name is not unique: {instance_name}"
        if block_type:
            message += f" (type: {block_type})"
        return None, None, None, [
            make_issue(
                op_index=op_index,
                op_type=op_type,
                field=field,
                code="block_name_not_unique",
                message=message,
            )
        ]

    raw_index, raw_entry = raw_matches[0]
    return parsed_matches[0], raw_entry, raw_index, []


def _require_block_rules(
    block_type: str,
    *,
    op_index: int,
    op_type: str,
    field: str,
    catalog_root: str | Path | None,
    block_not_found_code: str = "catalog_block_unavailable",
) -> tuple[Any | None, ValidationIssue | None]:
    lookup = get_block_rules(block_type, catalog_root=catalog_root)
    if lookup.ok:
        return lookup.rules, None

    code = block_not_found_code if lookup.error_type == ErrorCode.BLOCK_NOT_FOUND else "catalog_block_unavailable"
    suggestions = (
        tuple(_block_id_suggestions(block_type, catalog_root))
        if code == "unknown_block_id"
        else None
    )
    return None, make_issue(
        op_index=op_index,
        op_type=op_type,
        field=field,
        code=code,
        message=lookup.message or f"Could not resolve block type: {block_type}",
        suggestions=suggestions,
    )


def _native_core_block_ids(catalog_root: str | Path | None) -> list[str]:
    """Return control/core block IDs using GRC's native role discriminators.

    Two native signals:
      1. Platform ``cls.value`` discriminator + catalog ``Core/Variables``
         category → identifies variable/control blocks the model can add.
      2. Platform-internal blocks (those NOT in the catalog YAML —
         ``import``, ``snippet``, ``epy_block``, ``epy_module``, ``options``)
         detected by checking which platform keys are absent from the catalog.

    Replaces the stale static ``_CORE_BLOCK_IDS`` tuple. Dynamically
    adapts when GRC adds new control block types.
    """
    core_ids: list[str] = []

    # Signal 1: variable blocks via cls.value + category filter
    try:
        from grc_agent.session import _ensure_platform

        platform = _ensure_platform()
        snap = get_catalog_snapshot(catalog_root)
        catalog_keys = set(snap.blocks.keys()) if snap else set()

        if platform is not None:
            for key, cls in getattr(platform, "block_classes", {}).items():
                key_str = str(key)
                # cls.value is the native discriminator for variable blocks
                if getattr(cls, "value", None) is None:
                    continue
                # Filter to Core/Variables category (excludes specialized
                # variable_band_pass_filter_taps etc. in Core/Filters)
                block = snap.blocks.get(key_str) if snap else None
                if block:
                    cats = block.category_paths or ()
                    cat_flat = "/".join(
                        part for path in cats for part in path
                    ).lower()
                    if "variables" in cat_flat:
                        core_ids.append(key_str)
                # Also include variable blocks not in catalog (internal)
                elif key_str not in catalog_keys and not key_str.startswith("_"):
                    core_ids.append(key_str)
    except Exception:
        pass

    # Signal 2: platform-internal blocks not in catalog (options, import, snippet, etc.)
    try:
        from grc_agent.session import _ensure_platform

        platform = _ensure_platform()
        snap = get_catalog_snapshot(catalog_root)
        catalog_keys = set(snap.blocks.keys()) if snap else set()

        if platform is not None:
            for key in getattr(platform, "block_classes", {}):
                key_str = str(key)
                if key_str.startswith("_"):
                    continue
                if key_str not in catalog_keys and key_str not in core_ids:
                    # Not in catalog = platform-internal special block
                    core_ids.append(key_str)
    except Exception:
        pass

    return sorted(set(core_ids))


def _block_id_suggestions(requested: str, catalog_root: str | Path | None) -> list[str]:
    """Return catalog block_ids close to the requested name + core blocks."""
    try:
        snap = get_catalog_snapshot(catalog_root)
        all_ids = sorted(snap.blocks.keys())
    except Exception:
        return _native_core_block_ids(catalog_root)
    # Fuzzy: block_ids containing the requested string as a substring.
    needle = requested.lower()
    fuzzy = [bid for bid in all_ids if needle in bid.lower()][:5]
    # Include core/control blocks via GRC native role discriminators
    # (is_variable, is_import, is_snippet, is_param) — not a stale allowlist.
    native_core = set(_native_core_block_ids(catalog_root))
    core_present = sorted(bid for bid in native_core if bid in all_ids)
    # Dedupe, preserve order.
    seen: set[str] = set()
    result: list[str] = []
    for bid in fuzzy + core_present:
        if bid not in seen:
            seen.add(bid)
            result.append(bid)
    return result[:10]


def _validate_parameter_updates(
    *,
    block_type: str,
    params: dict[str, Any],
    parameter_rules: dict[str, Any],
    allowed_parameter_ids: set[str] = set(),
    op_index: int,
    op_type: str,
    field_prefix: str,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for parameter_id in sorted(params, key=str):
        rule = parameter_rules.get(parameter_id)
        if rule is None and parameter_id not in allowed_parameter_ids:
            issues.append(
                make_issue(
                    op_index=op_index,
                    op_type=op_type,
                    field=f"{field_prefix}.{parameter_id}",
                    code="parameter_not_found",
                    message=f"Unknown parameter for block type {block_type}: {parameter_id}",
                    hint=(
                        f"Unknown param_id. Available parameters: {', '.join(sorted(allowed_parameter_ids)[:12])}"
                        + (f"... [TRUNCATED by chat-history compactor: was {len(allowed_parameter_ids)} items, kept 12]" if len(allowed_parameter_ids) > 12 else "")
                        + "."
                    ),
                )
            )
            continue
        if rule is None:
            continue

        if rule.dtype == "enum" and rule.options:
            value_text = str(params[parameter_id])
            if value_text not in rule.options:
                issues.append(
                    make_issue(
                        op_index=op_index,
                        op_type=op_type,
                        field=f"{field_prefix}.{parameter_id}",
                        code="invalid_enum_value",
                        message=(
                            f"Invalid enum value for {block_type}.{parameter_id}: {value_text}"
                        ),
                        hint=format_allowed_values(rule.options),
                    )
                )
    return issues


def _assert_new_block_name_available(
    snapshot: SessionSnapshot,
    instance_name: str,
    raw_blocks: list[Any],
    *,
    op_index: int,
    op_type: str,
) -> list[ValidationIssue]:
    if any(block.instance_name == instance_name for block in snapshot.blocks):
        return [
            make_issue(
                op_index=op_index,
                op_type=op_type,
                field="instance_name",
                code="duplicate_block_name",
                message=f"Block already exists: {instance_name}. Block already exists in the graph.",
            )
        ]

    if any(
        isinstance(entry, dict) and entry.get("name") == instance_name
        for entry in raw_blocks
    ):
        return [
            make_issue(
                op_index=op_index,
                op_type=op_type,
                field="instance_name",
                code="duplicate_block_name",
                message=f"Raw block already exists: {instance_name}. Block already exists in the graph.",
            )
        ]
    return []


def _validate_port_index(
    *,
    ports: list[ResolvedPort],
    port_index: int | str,
    block_name: str,
    op_index: int,
    op_type: str,
    field: str,
    direction: str,
) -> ValidationIssue | None:
    if isinstance(port_index, str):
        return None
    if 0 <= port_index < len(ports):
        return None

    return make_issue(
        op_index=op_index,
        op_type=op_type,
        field=field,
        code="port_out_of_range",
        message=(
            f"{direction.capitalize()} port {port_index} is out of range for {block_name} "
            f"(available: {format_port_range(len(ports))})."
        ),
    )


def _warnings_from_port_resolution(
    warnings: list[str],
    *,
    op_index: int,
    op_type: str,
    field: str,
) -> list[ValidationIssue]:
    return [
        make_issue(
            op_index=op_index,
            op_type=op_type,
            field=field,
            code="port_metadata_unresolved",
            message=warning,
        )
        for warning in warnings
    ]


def _validate_enabled_symbol_uniqueness(
    snapshot: SessionSnapshot,
    *,
    op_index: int,
) -> ValidationIssue | None:
    enabled_names: dict[str, int] = {}
    for entry in snapshot.raw_blocks():
        if not isinstance(entry, dict):
            continue
        block_name = entry.get("name")
        if not isinstance(block_name, str) or not block_name:
            continue
        states = entry.get("states")
        state = states.get("state") if isinstance(states, dict) else "enabled"
        if state == "disabled":
            continue
        enabled_names[block_name] = enabled_names.get(block_name, 0) + 1

    duplicates = sorted(name for name, count in enabled_names.items() if count > 1)
    if not duplicates:
        return None

    return make_issue(
        op_index=op_index,
        op_type="transaction",
        field="operations",
        code="duplicate_enabled_symbol_id",
        message=f"Enabled block name is not unique: {duplicates[0]}",
    )


def _preflight_dtype_param_hint(
    block: Block,
    *,
    port_direction: str,
    port_id: Any,
    desired_dtype: str,
    catalog_root: str | Path | None = None,
) -> str | None:
    import re

    from .rules import _resolve_port_multiplicity, build_parameter_context

    block_type = block.block_type
    instance_name = block.instance_name
    rules_lookup = get_block_rules(block_type, catalog_root=catalog_root)
    if not rules_lookup.ok or rules_lookup.rules is None:
        return None
    rules = rules_lookup.rules

    ports_list = rules.inputs if port_direction == "inputs" else rules.outputs
    if not isinstance(ports_list, (list, tuple)):
        return None

    try:
        port_idx = int(port_id)
    except (ValueError, TypeError):
        return None

    context = build_parameter_context(block.params, block_rules=rules)
    current_idx = 0
    port_rule = None
    for rule in ports_list:
        multiplicity = _resolve_port_multiplicity(rule.multiplicity, context)
        if multiplicity is None:
            multiplicity = 1
        if multiplicity < 0:
            multiplicity = 0
        if current_idx <= port_idx < current_idx + multiplicity:
            port_rule = rule
            break
        current_idx += multiplicity

    if port_rule is None and len(ports_list) == 1:
        port_rule = ports_list[0]

    if port_rule is None:
        return None

    dtype = port_rule.dtype
    if not isinstance(dtype, str):
        return None

    match_id = re.search(r"\$\{\s*([A-Za-z_][A-Za-z0-9_]*)", dtype.strip())
    param_id = match_id.group(1) if match_id else None
    if param_id is None:
        return None

    param_rule = rules.parameters.get(param_id)
    if param_rule is None:
        return None

    options = param_rule.options
    suggested_val = None
    if isinstance(options, (list, tuple)):
        if desired_dtype in {str(option) for option in options}:
            suggested_val = desired_dtype
        else:
            match_attr = re.search(r"\$\{\s*[A-Za-z_][A-Za-z0-9_]*\.([A-Za-z_][A-Za-z0-9_]*)", dtype.strip())
            attr = match_attr.group(1) if match_attr else None
            option_attributes = param_rule.option_attributes
            if attr and isinstance(option_attributes, dict):
                attrs = option_attributes.get(attr)
                if isinstance(attrs, (list, tuple)):
                    matching = [options[i] for i, val in enumerate(attrs) if str(val) == desired_dtype and i < len(options)]
                    if matching:
                        suggested_val = matching[0]

    if suggested_val is None:
        return None

    return f"Parameter '{param_id}' of '{instance_name}' has matching type option '{suggested_val}'."


def _preflight_vlen_param_hint(
    block: Block,
    port_id: Any,
    block_vlen: Any,
    target_vlen: Any,
    *,
    catalog_root: str | Path | None = None,
) -> str | None:
    """Build a hint when two connected stream ports have different ``vlen``.

    The model gets a recovery path: it should set the block's ``vlen``
    parameter (if it has one) to the value the OTHER side of the connection
    expects, or to a variable that holds that value. Returns ``None`` if
    the block has no configurable ``vlen`` parameter — in that case the
    vlen is hard-coded by the block type and the only fix is to pick a
    different block on the other side of the connection.
    """
    from .rules import get_block_rules

    rules_lookup = get_block_rules(block.block_type, catalog_root=catalog_root)
    if not rules_lookup.ok or rules_lookup.rules is None:
        return None
    rules = rules_lookup.rules

    vlen_rule = rules.parameters.get("vlen")
    if vlen_rule is None:
        return None

    if isinstance(target_vlen, int):
        suggested = str(target_vlen)
    else:
        suggested = str(target_vlen)

    return (
        f"Parameter 'vlen' of '{block.instance_name}' is "
        f"{block_vlen}; set to {suggested} to match the other side."
    )
