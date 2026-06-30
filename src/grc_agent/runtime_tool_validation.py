"""Runtime tool-call validation against the declared model-facing schemas."""

from __future__ import annotations

from typing import Any

from grc_agent.domain_models import ErrorCode, ToolValidationCode

ToolSchemaMap = dict[str, dict[str, Any]]


def build_tool_schema_map(tool_schemas: list[dict[str, Any]]) -> ToolSchemaMap:
    """Index the runtime tool schemas by tool name."""
    schema_map: ToolSchemaMap = {}
    for schema in tool_schemas:
        if not isinstance(schema, dict):
            continue
        function_schema = schema.get("function")
        if not isinstance(function_schema, dict):
            continue
        tool_name = function_schema.get("name")
        parameters = function_schema.get("parameters")
        if not isinstance(tool_name, str) or not isinstance(parameters, dict):
            continue
        schema_map[tool_name] = parameters
    return schema_map


def validate_runtime_tool_call(
    tool_name: str,
    arguments: Any,
    schema_map: ToolSchemaMap,
) -> dict[str, Any] | None:
    """Return one structured validation error payload or `None` when valid."""
    if tool_name not in schema_map:
        return {
            "error_type": ErrorCode.UNKNOWN_TOOL,
            "message": f"Unknown tool: {tool_name}",
            "validation_errors": [
                {
                    "code": ToolValidationCode.UNKNOWN_TOOL,
                    "field": None,
                    "message": f"Unknown tool: {tool_name}",
                    "supported_tools": sorted(schema_map),
                }
            ],
        }

    if not isinstance(arguments, dict):
        return {
            "error_type": ErrorCode.TOOL_CALL_INVALID,
            "message": f"Rejected invalid tool call for {tool_name}: arguments must be a JSON object.",
            "validation_errors": [
                {
                    "code": ToolValidationCode.INVALID_ARGUMENTS,
                    "field": None,
                    "message": "Tool arguments must be a JSON object.",
                    "received_type": _describe_value_type(arguments),
                }
            ],
            "schema_repair_instruction": _schema_repair_instruction(tool_name, []),
        }

    issues = _validate_object(arguments, schema_map[tool_name], field_path=None)
    if not issues:
        return None

    return {
        "error_type": ErrorCode.TOOL_CALL_INVALID,
        "message": f"Rejected invalid tool call for {tool_name}: {issues[0]['message']} No tool ran.",
        "validation_errors": issues,
        "schema_repair_instruction": _schema_repair_instruction(tool_name, issues),
    }


def _schema_repair_instruction(
    tool_name: str,
    issues: list[dict[str, Any]],
) -> dict[str, Any]:
    missing = [
        issue.get("field")
        for issue in issues
        if issue.get("code") == ToolValidationCode.MISSING_REQUIRED and isinstance(issue.get("field"), str)
    ]
    invalid = [
        issue.get("field")
        for issue in issues
        if issue.get("code") != ToolValidationCode.MISSING_REQUIRED and isinstance(issue.get("field"), str)
    ]
    return {
        "tool": tool_name,
        ToolValidationCode.NO_TOOL_RAN: True,
        "missing_fields": missing,
        "invalid_fields": invalid,
    }


def _validate_object(
    value: dict[str, Any],
    schema: dict[str, Any],
    *,
    field_path: str | None,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    properties = schema.get("properties")
    defined_properties = properties if isinstance(properties, dict) else {}
    required = schema.get("required")
    required_fields = required if isinstance(required, list) else []

    for required_field in required_fields:
        if required_field not in value:
            issues.append(
                {
                    "code": ToolValidationCode.MISSING_REQUIRED,
                    "field": _compose_field_path(field_path, str(required_field)),
                    "message": f"Missing required argument '{required_field}'.",
                    "required_fields": sorted(str(item) for item in required_fields),
                }
            )

    additional_properties = schema.get("additionalProperties", True)
    if additional_properties is False:
        for unexpected_field in sorted(key for key in value if key not in defined_properties):
            issues.append(
                {
                    "code": ToolValidationCode.UNEXPECTED_ARGUMENT,
                    "field": _compose_field_path(field_path, unexpected_field),
                    "message": f"Unsupported argument '{unexpected_field}'.",
                    "allowed_fields": sorted(defined_properties),
                }
            )

    for property_name, property_value in value.items():
        property_schema = defined_properties.get(property_name)
        if not isinstance(property_schema, dict):
            continue
        issues.extend(
            _validate_value(
                property_value,
                property_schema,
                field_path=_compose_field_path(field_path, property_name),
            )
        )

    return issues


def _validate_value(
    value: Any,
    schema: dict[str, Any],
    *,
    field_path: str,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []

    expected_types = _normalize_schema_types(schema.get("type"))
    if expected_types:
        matched_type = next((item for item in expected_types if _matches_type(value, item)), None)
        if matched_type is None:
            issues.append(
                {
                    "code": ToolValidationCode.INVALID_TYPE,
                    "field": field_path,
                    "message": (
                        f"Argument '{field_path}' must be "
                        f"{_render_expected_types(expected_types)}, got {_describe_value_type(value)}."
                    ),
                    "expected_types": expected_types,
                    "received_type": _describe_value_type(value),
                }
            )
            return issues

    enum_values = schema.get("enum")
    if isinstance(enum_values, list) and value not in enum_values:
        issues.append(
            {
                "code": ToolValidationCode.INVALID_ENUM,
                "field": field_path,
                "message": f"Argument '{field_path}' must be one of {enum_values}.",
                "allowed_values": enum_values,
            }
        )
        return issues

    if isinstance(value, dict) and isinstance(schema.get("properties"), dict):
        issues.extend(_validate_object(value, schema, field_path=field_path))

    item_schema = schema.get("items")
    if isinstance(value, list) and isinstance(item_schema, dict):
        min_items = schema.get("minItems")
        if isinstance(min_items, int) and len(value) < min_items:
            issues.append(
                {
                    "code": ToolValidationCode.TOO_FEW_ITEMS,
                    "field": field_path,
                    "message": (f"Argument '{field_path}' must contain at least {min_items} item."),
                    "min_items": min_items,
                    "received_items": len(value),
                }
            )
        max_items = schema.get("maxItems")
        if isinstance(max_items, int) and len(value) > max_items:
            issues.append(
                {
                    "code": ToolValidationCode.TOO_MANY_ITEMS,
                    "field": field_path,
                    "message": (f"Argument '{field_path}' must contain at most {max_items} items."),
                    "max_items": max_items,
                    "received_items": len(value),
                }
            )
        for index, item in enumerate(value):
            issues.extend(
                _validate_value(
                    item,
                    item_schema,
                    field_path=f"{field_path}[{index}]",
                )
            )

    return issues


def _normalize_schema_types(raw_type: Any) -> list[str]:
    if isinstance(raw_type, str):
        return [raw_type]
    if isinstance(raw_type, list):
        return [item for item in raw_type if isinstance(item, str)]
    return []


def _matches_type(value: Any, expected_type: str) -> bool:
    if expected_type == "object":
        return isinstance(value, dict)
    if expected_type == "array":
        return isinstance(value, list)
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "number":
        return isinstance(value, int | float) and not isinstance(value, bool)
    if expected_type == "boolean":
        return isinstance(value, bool)
    if expected_type == "null":
        return value is None
    return True


def _render_expected_types(expected_types: list[str]) -> str:
    if len(expected_types) == 1:
        return expected_types[0]
    return "one of " + ", ".join(expected_types)


def _describe_value_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, str):
        return "string"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    return type(value).__name__


def _compose_field_path(prefix: str | None, field_name: str) -> str:
    if not prefix:
        return field_name
    return f"{prefix}.{field_name}"
