"""Pure staged checks and snapshot simulation for preflight validation."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from grc_agent._payload import ErrorCode
from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.models import Block, Connection
from grc_agent.session_ops import (
    block_name_is_referenced_elsewhere,
    connection_entry_to_tuple,
    default_block_states,
    parse_blocks,
    parse_connections,
    raw_connection_entry,
)

from .errors import ValidationIssue, make_issue
from .messages import (
    format_allowed_values,
    format_endpoint,
    format_port_range,
)
from .rules import (
    ResolvedPort,
    ValidationOperation,
    get_block_rules,
    resolve_port_slots,
)


@dataclass
class SessionSnapshot:
    """A mutable staged copy of one loaded session."""

    raw_data: dict[str, Any]
    blocks: list[Block]
    connections: list[Connection]

    @classmethod
    def from_session(cls, session: FlowgraphSession) -> "SessionSnapshot":
        if session.flowgraph is None:
            raise ValueError("No flowgraph loaded.")
        raw_data = copy.deepcopy(session.flowgraph.raw_data)
        return cls.from_raw_data(raw_data)

    @classmethod
    def from_raw_data(cls, raw_data: dict[str, Any]) -> "SessionSnapshot":
        blocks = parse_blocks(raw_data.get("blocks"))
        connections = parse_connections(raw_data.get("connections"))
        return cls(raw_data=raw_data, blocks=blocks, connections=connections)

    def refresh(self) -> None:
        self.blocks = parse_blocks(self.raw_data.get("blocks"))
        self.connections = parse_connections(self.raw_data.get("connections"))

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
    raise ValueError(f"Unsupported validation op_type: {operation.op_type}")


def _apply_update_params(
    snapshot: SessionSnapshot,
    operation: ValidationOperation,
    *,
    op_index: int,
    catalog_root: str | Path | None,
) -> tuple[list[ValidationIssue], list[ValidationIssue]]:
    op_type = operation.op_type
    instance_name = operation.payload["instance_name"]
    params = operation.payload["params"]

    block, raw_block, _raw_index, issues = _require_unique_block(
        snapshot,
        instance_name,
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


def _apply_add_block(
    snapshot: SessionSnapshot,
    operation: ValidationOperation,
    *,
    op_index: int,
    catalog_root: str | Path | None,
) -> tuple[list[ValidationIssue], list[ValidationIssue]]:
    op_type = operation.op_type
    instance_name = operation.payload["instance_name"]
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

    if block_type != "variable":
        return [
            make_issue(
                op_index=op_index,
                op_type=op_type,
                field="block_type",
                code="unsupported_block_type",
                message=f"Unsupported block type for add_block: {block_type}",
                hint="Phase 4 only supports detached variable blocks.",
            )
        ], []

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

    parameter_issues = _validate_parameter_updates(
        block_type=block_type,
        params=parameters,
        parameter_rules=lookup.parameters,
        allowed_parameter_ids={"comment"} if block_type == "variable" else set(),
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


def _apply_remove_block(
    snapshot: SessionSnapshot,
    operation: ValidationOperation,
    *,
    op_index: int,
) -> tuple[list[ValidationIssue], list[ValidationIssue]]:
    op_type = operation.op_type
    instance_name = operation.payload["instance_name"]

    block, _raw_block, raw_index, issues = _require_unique_block(
        snapshot,
        instance_name,
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
                hint="Disconnect all attached wires first.",
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
    target = (
        operation.payload["src_block"],
        operation.payload["src_port"],
        operation.payload["dst_block"],
        operation.payload["dst_port"],
    )

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

    src_parameters = src_block.params.get("parameters")
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
    ) and destination_port.domain != "message":
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
        source_port.domain == "stream"
        and destination_port.domain == "stream"
        and source_port.dtype is not None
        and destination_port.dtype is not None
        and source_port.dtype != destination_port.dtype
    ):
        return [
            make_issue(
                op_index=op_index,
                op_type=op_type,
                field="connection",
                code="incompatible_dtype",
                message=(
                    f"Cannot connect {format_endpoint(src_block_name, src_port)} "
                    f"({source_port.dtype}) to {format_endpoint(dst_block_name, dst_port)} "
                    f"({destination_port.dtype})."
                ),
            )
        ], warnings

    snapshot.raw_connections().append(
        raw_connection_entry(src_block_name, src_port, dst_block_name, dst_port)
    )
    snapshot.refresh()
    return [], warnings


def _require_unique_block(
    snapshot: SessionSnapshot,
    instance_name: str,
    *,
    op_index: int,
    op_type: str,
    field: str,
) -> tuple[Block | None, dict[str, Any] | None, int | None, list[ValidationIssue]]:
    parsed_matches = [
        block for block in snapshot.blocks if block.instance_name == instance_name
    ]
    raw_matches = [
        (index, entry)
        for index, entry in enumerate(snapshot.raw_blocks())
        if isinstance(entry, dict) and entry.get("name") == instance_name
    ]

    if not parsed_matches or not raw_matches:
        return None, None, None, [
            make_issue(
                op_index=op_index,
                op_type=op_type,
                field=field,
                code="block_not_found",
                message=f"Block not found: {instance_name}",
            )
        ]

    if len(parsed_matches) != 1 or len(raw_matches) != 1:
        return None, None, None, [
            make_issue(
                op_index=op_index,
                op_type=op_type,
                field=field,
                code="block_name_not_unique",
                message=f"Block name is not unique: {instance_name}",
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
    return None, make_issue(
        op_index=op_index,
        op_type=op_type,
        field=field,
        code=code,
        message=lookup.message or f"Could not resolve block type: {block_type}",
    )


def _validate_parameter_updates(
    *,
    block_type: str,
    params: dict[str, Any],
    parameter_rules: dict[str, Any],
    allowed_parameter_ids: set[str],
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
                message=f"Block already exists: {instance_name}",
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
                message=f"Raw block already exists: {instance_name}",
            )
        ]
    return []


def _validate_port_index(
    *,
    ports: list[ResolvedPort],
    port_index: int,
    block_name: str,
    op_index: int,
    op_type: str,
    field: str,
    direction: str,
) -> ValidationIssue | None:
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
