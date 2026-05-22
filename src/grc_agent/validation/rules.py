"""Operation normalization and GNU block-rule helpers for preflight validation."""

from __future__ import annotations

import ast
import copy
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from grc_agent.catalog.describe import _describe_block_with_root

from .errors import ValidationIssue, make_issue
from .messages import format_catalog_lookup_message

OperationType = Literal[
    "update_params",
    "update_states",
    "add_connection",
    "remove_connection",
    "remove_block",
    "add_block",
    "insert_block_on_connection",
]

VALID_OPERATION_TYPES = frozenset(
    {
        "update_params",
        "update_states",
        "add_connection",
        "remove_connection",
        "remove_block",
        "add_block",
        "insert_block_on_connection",
    }
)

_UNRESOLVED = object()
_EXPRESSION_PATTERN = "${"


@dataclass(frozen=True)
class ValidationOperation:
    """One normalized ordered transaction operation."""

    op_type: OperationType
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        if self.op_type == "update_params":
            res = {
                "op_type": self.op_type,
                "params": copy.deepcopy(self.payload["params"]),
            }
            if "instance_name" in self.payload:
                res["instance_name"] = self.payload["instance_name"]
            if "block_type" in self.payload:
                res["block_type"] = self.payload["block_type"]
            if "target_ref" in self.payload:
                res["target_ref"] = copy.deepcopy(self.payload["target_ref"])
            if "expected_params" in self.payload:
                res["expected_params"] = copy.deepcopy(self.payload["expected_params"])
            return res
        if self.op_type == "update_states":
            res = {
                "op_type": self.op_type,
                "state": self.payload["state"],
            }
            if "instance_name" in self.payload:
                res["instance_name"] = self.payload["instance_name"]
            if "block_type" in self.payload:
                res["block_type"] = self.payload["block_type"]
            if "target_ref" in self.payload:
                res["target_ref"] = copy.deepcopy(self.payload["target_ref"])
            return res
        if self.op_type in {"add_connection", "remove_connection"}:
            res = {
                "op_type": self.op_type,
            }
            if "connection_id" in self.payload:
                res["connection_id"] = self.payload["connection_id"]
            if all(
                field_name in self.payload
                for field_name in ("src_block", "src_port", "dst_block", "dst_port")
            ):
                res.update(
                    {
                        "src_block": self.payload["src_block"],
                        "src_port": self.payload["src_port"],
                        "dst_block": self.payload["dst_block"],
                        "dst_port": self.payload["dst_port"],
                    }
                )
            return res
        if self.op_type == "remove_block":
            res = {
                "op_type": self.op_type,
            }
            if "instance_name" in self.payload:
                res["instance_name"] = self.payload["instance_name"]
            if "block_type" in self.payload:
                res["block_type"] = self.payload["block_type"]
            if "target_ref" in self.payload:
                res["target_ref"] = copy.deepcopy(self.payload["target_ref"])
            return res

        rendered = {
            "op_type": self.op_type,
            "instance_name": self.payload["instance_name"],
            "block_type": self.payload["block_type"],
        }
        if self.op_type == "insert_block_on_connection":
            rendered["connection_id"] = self.payload["connection_id"]
            # "params" maps to "parameters" for add_block compatibility
            params = self.payload.get("params", {})
            rendered["parameters"] = copy.deepcopy(params)
        else:
            rendered["parameters"] = copy.deepcopy(self.payload["parameters"])
        states = self.payload.get("states")
        if states is not None:
            rendered["states"] = copy.deepcopy(states)
        return rendered


@dataclass(frozen=True)
class ParameterRule:
    """One normalized parameter rule used by preflight checks."""

    parameter_id: str
    dtype: str | None
    default: Any
    options: tuple[str, ...]
    option_attributes: dict[str, tuple[Any, ...]]


@dataclass(frozen=True)
class PortRule:
    """One normalized input/output rule used by preflight checks."""

    domain: str | None
    dtype: str | None
    vlen: int | str | None
    multiplicity: int | str | None
    optional: bool | int | str | None


@dataclass(frozen=True)
class BlockRules:
    """The normalized GNU rules needed for one block type."""

    block_id: str
    parameters: dict[str, ParameterRule]
    inputs: tuple[PortRule, ...]
    outputs: tuple[PortRule, ...]
    asserts: tuple[str, ...]


@dataclass(frozen=True)
class BlockRulesLookup:
    """One catalog lookup result for a block type."""

    rules: BlockRules | None
    error_type: str | None = None
    message: str | None = None

    @property
    def ok(self) -> bool:
        return self.rules is not None


@dataclass(frozen=True)
class ResolvedPort:
    """One resolved concrete port on a block instance."""

    domain: str | None
    dtype: str | None
    vlen: int | None
    optional: bool | None


class EnumChoice(str):
    """Enum parameter value with GNU option attributes attached as properties."""

    def __new__(cls, value: str, *, attributes: dict[str, Any] | None = None) -> "EnumChoice":
        instance = str.__new__(cls, value)
        instance._attributes = attributes or {}
        return instance

    def __getattr__(self, name: str) -> Any:
        if name in self._attributes:
            return self._attributes[name]
        raise AttributeError(name)


def normalize_operations(operations: Any) -> tuple[list[ValidationOperation], list[ValidationIssue]]:
    """Normalize one transaction list or single operation into the Phase 4 shape."""
    if isinstance(operations, dict):
        operation_items = [operations]
    elif isinstance(operations, list):
        operation_items = operations
    else:
        return [], [
            make_issue(
                op_index=0,
                op_type="transaction",
                field="operations",
                code="invalid_operations",
                message="operations must be a mapping or a list of mappings.",
            )
        ]

    if not operation_items:
        return [], [
            make_issue(
                op_index=0,
                op_type="transaction",
                field="operations",
                code="empty_operations",
                message="operations must contain at least one operation.",
            )
        ]

    normalized: list[ValidationOperation] = []
    issues: list[ValidationIssue] = []
    for op_index, candidate in enumerate(operation_items):
        if not isinstance(candidate, dict):
            issues.append(
                make_issue(
                    op_index=op_index,
                    op_type="transaction",
                    field="operation",
                    code="invalid_operation",
                    message="Each operation must be a mapping.",
                )
            )
            continue

        raw_op_type = candidate.get("op_type")
        op_type = " ".join(raw_op_type.split()) if isinstance(raw_op_type, str) else None
        if op_type not in VALID_OPERATION_TYPES:
            issues.append(
                make_issue(
                    op_index=op_index,
                    op_type=str(raw_op_type) if raw_op_type is not None else "unknown",
                    field="op_type",
                    code="unsupported_op_type",
                    message=(
                        "op_type must be one of: update_params, add_connection, "
                        "remove_connection, remove_block, add_block."
                    ),
                )
            )
            continue

        op_issues, operation = _normalize_operation(
            op_index=op_index,
            op_type=op_type,
            candidate=candidate,
        )
        if op_issues:
            issues.extend(op_issues)
            continue
        if operation is not None:
            normalized.append(operation)

    return normalized, issues


def get_block_rules(
    block_type: str,
    *,
    catalog_root: str | Path | None = None,
) -> BlockRulesLookup:
    """Return cached GNU rules for one installed block type."""
    catalog_root_text = None if catalog_root is None else str(Path(catalog_root).resolve())
    return _get_cached_block_rules(block_type, catalog_root_text)


def build_parameter_context(
    parameters: dict[str, Any],
    *,
    block_rules: BlockRules,
) -> dict[str, Any]:
    """Build the safe expression-evaluation context for one block instance."""
    context: dict[str, Any] = {}
    for parameter_id, rule in block_rules.parameters.items():
        raw_value = parameters.get(parameter_id, rule.default)
        context[parameter_id] = _coerce_parameter_value(raw_value, rule)
    return context


def resolve_port_slots(
    *,
    block_rules: BlockRules,
    parameters: dict[str, Any],
    direction: Literal["inputs", "outputs"],
) -> tuple[list[ResolvedPort], list[str]]:
    """Resolve one block's concrete ports for the current parameter values."""
    context = build_parameter_context(parameters, block_rules=block_rules)
    port_rules = block_rules.inputs if direction == "inputs" else block_rules.outputs
    resolved_ports: list[ResolvedPort] = []
    warnings: list[str] = []

    for template_index, port_rule in enumerate(port_rules):
        multiplicity = _resolve_port_multiplicity(port_rule.multiplicity, context)
        if multiplicity is None:
            warnings.append(
                f"Could not resolve {direction} port multiplicity for template {template_index}."
            )
            multiplicity = 1
        if multiplicity < 0:
            warnings.append(
                f"Resolved {direction} port multiplicity was negative for template {template_index}."
            )
            multiplicity = 0

        domain = _resolve_text_expression(port_rule.domain, context)
        dtype = _resolve_text_expression(port_rule.dtype, context)
        vlen = _resolve_port_vlen(port_rule.vlen, context)
        if vlen is None:
            warnings.append(
                f"Could not resolve {direction} port vector length for template {template_index}."
            )
        elif vlen < 0:
            warnings.append(
                f"Resolved {direction} port vector length was negative for template {template_index}."
            )
            vlen = 0
        optional = _resolve_optional_value(port_rule.optional, context)
        for _unused in range(multiplicity):
            resolved_ports.append(
                ResolvedPort(
                    domain=domain,
                    dtype=dtype,
                    vlen=vlen,
                    optional=optional,
                )
            )

    return resolved_ports, warnings


def validate_block_asserts(
    *,
    block_rules: BlockRules,
    parameters: dict[str, Any],
) -> tuple[list[str], list[str]]:
    """Evaluate one block's catalog assertions against the current parameters."""
    context = build_parameter_context(parameters, block_rules=block_rules)
    failures: list[str] = []
    warnings: list[str] = []
    for assertion in block_rules.asserts:
        expression = _unwrap_expression(assertion)
        if expression is None:
            warnings.append(f"Could not parse block assertion: {assertion}")
            continue
        resolved = evaluate_expression(expression, context)
        if resolved is _UNRESOLVED:
            warnings.append(f"Could not resolve block assertion: {assertion}")
            continue
        if not bool(resolved):
            failures.append(assertion)
    return failures, warnings


def evaluate_expression(expression: str, context: dict[str, Any]) -> Any | object:
    """Evaluate one GNU `${ ... }` expression with a small safe subset."""
    source = expression.strip()
    if not source:
        return _UNRESOLVED

    try:
        tree = ast.parse(source, mode="eval")
    except SyntaxError:
        return _UNRESOLVED

    evaluator = _SafeExpressionEvaluator(context)
    try:
        return evaluator.visit(tree.body)
    except (AttributeError, KeyError, TypeError, ValueError):
        return _UNRESOLVED


def _normalize_operation(
    *,
    op_index: int,
    op_type: OperationType,
    candidate: dict[str, Any],
) -> tuple[list[ValidationIssue], ValidationOperation | None]:
    allowed_fields: dict[OperationType, tuple[str, ...]] = {
        "update_params": (
            "op_type",
            "instance_name",
            "params",
            "expected_params",
            "block_type",
            "target_ref",
        ),
        "update_states": ("op_type", "instance_name", "state", "block_type", "target_ref"),
        "add_connection": ("op_type", "src_block", "src_port", "dst_block", "dst_port"),
        "remove_connection": (
            "op_type",
            "connection_id",
            "src_block",
            "src_port",
            "dst_block",
            "dst_port",
        ),
        "remove_block": ("op_type", "instance_name", "block_type", "target_ref"),
        "add_block": ("op_type", "instance_name", "block_type", "parameters", "states"),
        "insert_block_on_connection": ("op_type", "connection_id", "block_type", "instance_name", "params"),
    }
    required_fields: dict[OperationType, tuple[str, ...]] = {
        "update_params": ("params",),
        "update_states": ("state",),
        "add_connection": ("src_block", "src_port", "dst_block", "dst_port"),
        "remove_connection": (),
        "remove_block": (),
        "add_block": ("instance_name", "block_type", "parameters"),
        "insert_block_on_connection": ("connection_id", "block_type", "instance_name"),
    }

    issues: list[ValidationIssue] = []
    unexpected_fields = sorted(set(candidate) - set(allowed_fields[op_type]), key=str)
    for field_name in unexpected_fields:
        issues.append(
            make_issue(
                op_index=op_index,
                op_type=op_type,
                field=field_name,
                code="unexpected_field",
                message=f"Unexpected field for {op_type}: {field_name}",
            )
        )

    for field_name in required_fields[op_type]:
        if field_name not in candidate:
            issues.append(
                make_issue(
                    op_index=op_index,
                    op_type=op_type,
                    field=field_name,
                    code="missing_field",
                    message=f"Missing required field for {op_type}: {field_name}",
                )
            )

    if issues:
        return issues, None

    payload: dict[str, Any] = {}

    if op_type in {"update_params", "update_states", "remove_block", "add_block"}:
        target_ref = candidate.get("target_ref")
        if target_ref is not None:
            if op_type == "add_block":
                issues.append(
                    make_issue(
                        op_index=op_index,
                        op_type=op_type,
                        field="target_ref",
                        code="unexpected_field",
                        message="target_ref is not supported for add_block.",
                    )
                )
            elif not isinstance(target_ref, dict):
                issues.append(
                    make_issue(
                        op_index=op_index,
                        op_type=op_type,
                        field="target_ref",
                        code="invalid_field_type",
                        message="target_ref must be a mapping.",
                    )
                )
            else:
                payload["target_ref"] = copy.deepcopy(target_ref)
                expected_name = target_ref.get("expected_instance_name")
                expected_type = target_ref.get("expected_block_type")
                if isinstance(expected_name, str) and expected_name.strip():
                    payload.setdefault("instance_name", expected_name.strip())
                if isinstance(expected_type, str) and expected_type.strip():
                    payload.setdefault("block_type", expected_type.strip())

        instance_name = candidate.get("instance_name")
        if target_ref is None and (not isinstance(instance_name, str) or not instance_name.strip()):
            issues.append(
                make_issue(
                    op_index=op_index,
                    op_type=op_type,
                    field="instance_name",
                    code="invalid_field_type",
                    message="instance_name must be a non-empty string.",
                )
            )
        else:
            if isinstance(instance_name, str) and instance_name.strip():
                payload["instance_name"] = instance_name.strip()

        # Optional block_type discriminator
        block_type_val = candidate.get("block_type")
        if block_type_val is not None:
            if not isinstance(block_type_val, str) or not block_type_val.strip():
                issues.append(
                    make_issue(
                        op_index=op_index,
                        op_type=op_type,
                        field="block_type",
                        code="invalid_field_type",
                        message="block_type must be a non-empty string if provided.",
                    )
                )
            else:
                payload["block_type"] = block_type_val.strip()

    if op_type in {"add_connection", "remove_connection"}:
        endpoint_fields = ("src_block", "src_port", "dst_block", "dst_port")
        if op_type == "remove_connection":
            connection_id = candidate.get("connection_id")
            if connection_id is not None:
                if not isinstance(connection_id, str) or not connection_id.strip():
                    issues.append(
                        make_issue(
                            op_index=op_index,
                            op_type=op_type,
                            field="connection_id",
                            code="invalid_field_type",
                            message="connection_id must be a non-empty string.",
                        )
                    )
                else:
                    payload["connection_id"] = connection_id.strip()

            provided_endpoint_fields = [
                field_name for field_name in endpoint_fields if field_name in candidate
            ]
            if provided_endpoint_fields:
                for field_name in endpoint_fields:
                    if field_name not in candidate:
                        issues.append(
                            make_issue(
                                op_index=op_index,
                                op_type=op_type,
                                field=field_name,
                                code="missing_field",
                                message=f"Missing required field for {op_type}: {field_name}",
                            )
                        )
            elif "connection_id" not in payload:
                for field_name in endpoint_fields:
                    issues.append(
                        make_issue(
                            op_index=op_index,
                            op_type=op_type,
                            field=field_name,
                            code="missing_field",
                            message=f"Missing required field for {op_type}: {field_name}",
                        )
                    )

        for field_name in ("src_block", "dst_block"):
            if field_name not in candidate:
                continue
            value = candidate.get(field_name)
            if not isinstance(value, str) or not value.strip():
                issues.append(
                    make_issue(
                        op_index=op_index,
                        op_type=op_type,
                        field=field_name,
                        code="invalid_field_type",
                        message=f"{field_name} must be a non-empty string.",
                    )
                )
                continue
            payload[field_name] = value.strip()

        for field_name in ("src_port", "dst_port"):
            if field_name not in candidate:
                continue
            value = candidate.get(field_name)
            if isinstance(value, int):
                if value < 0:
                    issues.append(
                        make_issue(
                            op_index=op_index,
                            op_type=op_type,
                            field=field_name,
                            code="invalid_field_type",
                            message=f"{field_name} must be a non-negative integer.",
                        )
                    )
                    continue
                payload[field_name] = value
            elif isinstance(value, str) and value:
                payload[field_name] = value
            else:
                issues.append(
                    make_issue(
                        op_index=op_index,
                        op_type=op_type,
                        field=field_name,
                        code="invalid_field_type",
                        message=f"{field_name} must be a non-negative integer or a non-empty string port name.",
                    )
                )
                continue

    if op_type == "update_params":
        params = candidate.get("params")
        if not isinstance(params, dict):
            issues.append(
                make_issue(
                    op_index=op_index,
                    op_type=op_type,
                    field="params",
                    code="invalid_field_type",
                    message="params must be a mapping.",
                )
            )
        elif not params:
            issues.append(
                make_issue(
                    op_index=op_index,
                    op_type=op_type,
                    field="params",
                    code="empty_params",
                    message="params must contain at least one parameter update.",
                )
            )
        else:
            parameter_issues = _validate_parameter_mapping(
                op_index=op_index,
                op_type=op_type,
                field="params",
                parameters=params,
            )
            issues.extend(parameter_issues)
            if not parameter_issues:
                payload["params"] = copy.deepcopy(params)
        expected_params = candidate.get("expected_params")
        if expected_params is not None:
            if not isinstance(expected_params, dict):
                issues.append(
                    make_issue(
                        op_index=op_index,
                        op_type=op_type,
                        field="expected_params",
                        code="invalid_field_type",
                        message="expected_params must be a mapping when provided.",
                    )
                )
            else:
                expected_issues = _validate_parameter_mapping(
                    op_index=op_index,
                    op_type=op_type,
                    field="expected_params",
                    parameters=expected_params,
                )
                issues.extend(expected_issues)
                if not expected_issues:
                    payload["expected_params"] = copy.deepcopy(expected_params)

    if op_type == "update_states":
        state = candidate.get("state")
        if not isinstance(state, str) or not state.strip():
            issues.append(
                make_issue(
                    op_index=op_index,
                    op_type=op_type,
                    field="state",
                    code="invalid_field_type",
                    message="state must be a non-empty string.",
                )
            )
        else:
            normalized_state = state.strip()
            if normalized_state not in {"enabled", "disabled"}:
                issues.append(
                    make_issue(
                        op_index=op_index,
                        op_type=op_type,
                        field="state",
                        code="invalid_state_value",
                        message=f"Invalid block state: {normalized_state}",
                        hint="Valid values: enabled, disabled.",
                    )
                )
            else:
                payload["state"] = normalized_state

    if op_type == "add_block":
        block_type = candidate.get("block_type")
        if not isinstance(block_type, str) or not block_type.strip():
            issues.append(
                make_issue(
                    op_index=op_index,
                    op_type=op_type,
                    field="block_type",
                    code="invalid_field_type",
                    message="block_type must be a non-empty string.",
                )
            )
        else:
            payload["block_type"] = block_type.strip()

        parameters = candidate.get("parameters")
        if not isinstance(parameters, dict):
            issues.append(
                make_issue(
                    op_index=op_index,
                    op_type=op_type,
                    field="parameters",
                    code="invalid_field_type",
                    message="parameters must be a mapping.",
                )
            )
        else:
            parameter_issues = _validate_parameter_mapping(
                op_index=op_index,
                op_type=op_type,
                field="parameters",
                parameters=parameters,
            )
            issues.extend(parameter_issues)
            if not parameter_issues:
                payload["parameters"] = copy.deepcopy(parameters)

        states = candidate.get("states")
        if states is not None:
            if not isinstance(states, dict):
                issues.append(
                    make_issue(
                        op_index=op_index,
                        op_type=op_type,
                        field="states",
                        code="invalid_field_type",
                        message="states must be a mapping when provided.",
                    )
                )
            else:
                payload["states"] = copy.deepcopy(states)

    if op_type == "insert_block_on_connection":
        instance_name = candidate.get("instance_name")
        if not isinstance(instance_name, str) or not instance_name.strip():
            issues.append(
                make_issue(
                    op_index=op_index,
                    op_type=op_type,
                    field="instance_name",
                    code="invalid_field_type",
                    message="instance_name must be a non-empty string.",
                )
            )
        else:
            payload["instance_name"] = instance_name.strip()

        block_type = candidate.get("block_type")
        if not isinstance(block_type, str) or not block_type.strip():
            issues.append(
                make_issue(
                    op_index=op_index,
                    op_type=op_type,
                    field="block_type",
                    code="invalid_field_type",
                    message="block_type must be a non-empty string.",
                )
            )
        else:
            payload["block_type"] = block_type.strip()

        connection_id = candidate.get("connection_id")
        if not isinstance(connection_id, str) or not connection_id.strip():
            issues.append(
                make_issue(
                    op_index=op_index,
                    op_type=op_type,
                    field="connection_id",
                    code="invalid_field_type",
                    message="connection_id must be a non-empty string.",
                )
            )
        else:
            payload["connection_id"] = connection_id.strip()

        params = candidate.get("params")
        if params is not None:
            if not isinstance(params, dict):
                issues.append(
                    make_issue(
                        op_index=op_index,
                        op_type=op_type,
                        field="params",
                        code="invalid_field_type",
                        message="params must be a mapping when provided.",
                    )
                )
            else:
                parameter_issues = _validate_parameter_mapping(
                    op_index=op_index,
                    op_type=op_type,
                    field="params",
                    parameters=params,
                )
                issues.extend(parameter_issues)
                if not parameter_issues:
                    payload["params"] = copy.deepcopy(params)

        states = candidate.get("states")
        if states is not None:
            if not isinstance(states, dict):
                issues.append(
                    make_issue(
                        op_index=op_index,
                        op_type=op_type,
                        field="states",
                        code="invalid_field_type",
                        message="states must be a mapping when provided.",
                    )
                )
            else:
                payload["states"] = copy.deepcopy(states)

    if issues:
        return issues, None
    return issues, ValidationOperation(op_type=op_type, payload=payload)


def _validate_parameter_mapping(
    *,
    op_index: int,
    op_type: str,
    field: str,
    parameters: dict[str, Any],
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for parameter_id in sorted(parameters, key=str):
        if not isinstance(parameter_id, str) or not parameter_id.strip():
            issues.append(
                make_issue(
                    op_index=op_index,
                    op_type=op_type,
                    field=f"{field}.{parameter_id}",
                    code="invalid_param_key",
                    message="Parameter keys must be non-empty strings.",
                )
            )
    return issues


@lru_cache(maxsize=256)
def _get_cached_block_rules(
    block_type: str,
    catalog_root_text: str | None,
) -> BlockRulesLookup:
    payload = _describe_block_with_root(block_type, catalog_root=catalog_root_text)
    if not payload.get("ok"):
        return BlockRulesLookup(
            rules=None,
            error_type=str(payload.get("error_type") or "CatalogError"),
            message=str(payload.get("message") or format_catalog_lookup_message(block_type)),
        )

    parameter_rules = {
        parameter["id"]: ParameterRule(
            parameter_id=parameter["id"],
            dtype=_optional_text(parameter.get("dtype")),
            default=parameter.get("default"),
            options=tuple(str(option) for option in parameter.get("options", [])),
            option_attributes={
                str(key): tuple(values)
                for key, values in parameter.get("option_attributes", {}).items()
            },
        )
        for parameter in payload.get("parameters", [])
        if isinstance(parameter, dict) and isinstance(parameter.get("id"), str)
    }
    inputs = tuple(
        PortRule(
            domain=_optional_text(port.get("domain")),
            dtype=_optional_text(port.get("dtype")),
            vlen=port.get("vlen"),
            multiplicity=port.get("multiplicity"),
            optional=port.get("optional"),
        )
        for port in payload.get("inputs", [])
        if isinstance(port, dict)
    )
    outputs = tuple(
        PortRule(
            domain=_optional_text(port.get("domain")),
            dtype=_optional_text(port.get("dtype")),
            vlen=port.get("vlen"),
            multiplicity=port.get("multiplicity"),
            optional=port.get("optional"),
        )
        for port in payload.get("outputs", [])
        if isinstance(port, dict)
    )
    return BlockRulesLookup(
        rules=BlockRules(
            block_id=block_type,
            parameters=parameter_rules,
            inputs=inputs,
            outputs=outputs,
            asserts=tuple(str(item) for item in payload.get("asserts", [])),
        )
    )


def _coerce_parameter_value(value: Any, rule: ParameterRule) -> Any:
    if rule.dtype == "enum":
        text = "" if value is None else str(value)
        attributes: dict[str, Any] = {}
        if text in rule.options:
            index = rule.options.index(text)
            for attribute_name, attribute_values in rule.option_attributes.items():
                if index < len(attribute_values):
                    attributes[attribute_name] = attribute_values[index]
        return EnumChoice(text, attributes=attributes)

    if rule.dtype in {"int", "short", "hex"}:
        coerced = _coerce_int_literal(value)
        return coerced if coerced is not None else value
    if rule.dtype in {"float", "real"}:
        coerced = _coerce_float_literal(value)
        return coerced if coerced is not None else value
    if rule.dtype == "bool":
        coerced = _coerce_bool_literal(value)
        return coerced if coerced is not None else value
    return value


def _resolve_port_multiplicity(value: int | str | None, context: dict[str, Any]) -> int | None:
    if value is None:
        return 1

    resolved = _resolve_expression_value(value, context)
    if isinstance(resolved, bool):
        return int(resolved)
    if isinstance(resolved, int):
        return resolved
    if isinstance(resolved, str):
        return _coerce_int_literal(resolved)
    return None


def _resolve_optional_value(value: bool | int | str | None, context: dict[str, Any]) -> bool | None:
    if value is None:
        return False

    resolved = _resolve_expression_value(value, context)
    if isinstance(resolved, bool):
        return resolved
    if isinstance(resolved, int):
        return bool(resolved)
    if isinstance(resolved, str):
        return _coerce_bool_literal(resolved)
    return None


def _resolve_port_vlen(value: int | str | None, context: dict[str, Any]) -> int | None:
    if value is None:
        return 1

    resolved = _resolve_expression_value(value, context)
    if isinstance(resolved, bool):
        return int(resolved)
    if isinstance(resolved, int):
        return resolved
    if isinstance(resolved, str):
        return _coerce_int_literal(resolved)
    return None


def _resolve_text_expression(value: str | None, context: dict[str, Any]) -> str | None:
    if value is None:
        return None

    resolved = _resolve_expression_value(value, context)
    if resolved is _UNRESOLVED or resolved is None:
        return None
    if isinstance(resolved, str):
        text = resolved.strip()
        return text or None
    return str(resolved)


def _resolve_expression_value(value: int | str | bool | None, context: dict[str, Any]) -> Any | object:
    if isinstance(value, str):
        expression = _unwrap_expression(value)
        if expression is None:
            return value
        return evaluate_expression(expression, context)
    return value


def _unwrap_expression(value: str) -> str | None:
    stripped = value.strip()
    if stripped.startswith(_EXPRESSION_PATTERN) and stripped.endswith("}"):
        return stripped[2:-1].strip()
    return None


def _coerce_int_literal(value: Any) -> int | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if not isinstance(value, str):
        return None

    stripped = value.strip()
    if not stripped:
        return None
    try:
        return int(stripped, 0)
    except ValueError:
        return None


def _coerce_float_literal(value: Any) -> float | None:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None

    stripped = value.strip()
    if not stripped:
        return None
    try:
        return float(stripped)
    except ValueError:
        return None


def _coerce_bool_literal(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    if not isinstance(value, str):
        return None

    lowered = value.strip().lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    return None


def _optional_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


class _SafeExpressionEvaluator(ast.NodeVisitor):
    """Evaluate a small safe subset of GNU block metadata expressions."""

    _ALLOWED_FUNCTIONS = {
        "int": int,
        "float": float,
        "bool": bool,
        "str": str,
        "len": len,
    }
    _ALLOWED_METHODS = {"startswith", "endswith"}

    def __init__(self, context: dict[str, Any]) -> None:
        self._context = context

    def visit_Constant(self, node: ast.Constant) -> Any:
        return node.value

    def visit_Name(self, node: ast.Name) -> Any:
        return self._context[node.id]

    def visit_Attribute(self, node: ast.Attribute) -> Any:
        value = self.visit(node.value)
        if node.attr.startswith("_"):
            raise ValueError("Private attributes are not allowed.")
        if isinstance(value, EnumChoice):
            return getattr(value, node.attr)
        raise ValueError("Unsupported attribute access.")

    def visit_UnaryOp(self, node: ast.UnaryOp) -> Any:
        operand = self.visit(node.operand)
        if isinstance(node.op, ast.Not):
            return not operand
        if isinstance(node.op, ast.UAdd):
            return +operand
        if isinstance(node.op, ast.USub):
            return -operand
        raise ValueError("Unsupported unary operator.")

    def visit_BoolOp(self, node: ast.BoolOp) -> Any:
        if isinstance(node.op, ast.And):
            result = True
            for value in node.values:
                result = self.visit(value)
                if not result:
                    return result
            return result
        if isinstance(node.op, ast.Or):
            result = False
            for value in node.values:
                result = self.visit(value)
                if result:
                    return result
            return result
        raise ValueError("Unsupported boolean operator.")

    def visit_BinOp(self, node: ast.BinOp) -> Any:
        left = self.visit(node.left)
        right = self.visit(node.right)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            return left / right
        if isinstance(node.op, ast.FloorDiv):
            return left // right
        if isinstance(node.op, ast.Mod):
            return left % right
        raise ValueError("Unsupported binary operator.")

    def visit_Compare(self, node: ast.Compare) -> Any:
        left = self.visit(node.left)
        for operator, comparator_node in zip(node.ops, node.comparators):
            right = self.visit(comparator_node)
            if isinstance(operator, ast.Eq):
                matched = left == right
            elif isinstance(operator, ast.NotEq):
                matched = left != right
            elif isinstance(operator, ast.Lt):
                matched = left < right
            elif isinstance(operator, ast.LtE):
                matched = left <= right
            elif isinstance(operator, ast.Gt):
                matched = left > right
            elif isinstance(operator, ast.GtE):
                matched = left >= right
            else:
                raise ValueError("Unsupported comparison operator.")
            if not matched:
                return False
            left = right
        return True

    def visit_IfExp(self, node: ast.IfExp) -> Any:
        return self.visit(node.body if self.visit(node.test) else node.orelse)

    def visit_Call(self, node: ast.Call) -> Any:
        if node.keywords:
            raise ValueError("Keyword arguments are not supported.")

        if isinstance(node.func, ast.Name):
            function_name = node.func.id
            function = self._ALLOWED_FUNCTIONS.get(function_name)
            if function is None:
                raise ValueError("Unsupported function call.")
            return function(*(self.visit(argument) for argument in node.args))

        if isinstance(node.func, ast.Attribute):
            base_value = self.visit(node.func.value)
            method_name = node.func.attr
            if method_name not in self._ALLOWED_METHODS or not isinstance(base_value, str):
                raise ValueError("Unsupported method call.")
            method = getattr(base_value, method_name)
            return method(*(self.visit(argument) for argument in node.args))

        raise ValueError("Unsupported call target.")

    def visit_List(self, node: ast.List) -> Any:
        return [self.visit(item) for item in node.elts]

    def visit_Tuple(self, node: ast.Tuple) -> Any:
        return tuple(self.visit(item) for item in node.elts)

    def generic_visit(self, node: ast.AST) -> Any:
        raise ValueError(f"Unsupported expression node: {type(node).__name__}")
