"""Runtime tool-call validation against the declared model-facing schemas.

Validation delegates to the ``jsonschema`` library (standard, well-tested
JSON Schema implementation). The hand-rolled validator was replaced in favor
of a library that covers the full spec correctly.
"""

from __future__ import annotations

from typing import Any

from jsonschema import Draft7Validator
from jsonschema import ValidationError as JsonschemaValidationError

from grc_agent.domain_models import ErrorCode, ToolValidationCode

ToolSchemaMap = dict[str, dict[str, Any]]

# Map jsonschema validator names to our stable ToolValidationCode enum.
_VALIDATOR_CODE_MAP = {
    "required": ToolValidationCode.MISSING_REQUIRED,
    "type": ToolValidationCode.INVALID_TYPE,
    "enum": ToolValidationCode.INVALID_ENUM,
    "additionalProperties": ToolValidationCode.UNEXPECTED_ARGUMENT,
    "minItems": ToolValidationCode.TOO_FEW_ITEMS,
    "maxItems": ToolValidationCode.TOO_MANY_ITEMS,
}


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

    validator = Draft7Validator(schema_map[tool_name])
    errors = list(validator.iter_errors(arguments))
    if not errors:
        return None

    issues = [_jsonschema_error_to_issue(e) for e in errors]
    return {
        "error_type": ErrorCode.TOOL_CALL_INVALID,
        "message": f"Rejected invalid tool call for {tool_name}: {issues[0]['message']} No tool ran.",
        "validation_errors": issues,
        "schema_repair_instruction": _schema_repair_instruction(tool_name, issues),
    }


def _jsonschema_error_to_issue(error: JsonschemaValidationError) -> dict[str, Any]:
    code = _VALIDATOR_CODE_MAP.get(error.validator, "schema_violation")
    field = _compose_relative_path(error.relative_path)
    issue: dict[str, Any] = {
        "code": code,
        "field": field,
        "message": error.message,
    }
    # Attach extra context fields matching the original hand-rolled format.
    if error.validator == "required":
        issue["required_fields"] = sorted(error.validator_value)
    elif error.validator == "type":
        expected = error.validator_value
        issue["expected_types"] = expected if isinstance(expected, list) else [str(expected)]
        issue["received_type"] = _describe_value_type(error.instance)
    elif error.validator == "enum":
        issue["allowed_values"] = error.validator_value
    elif error.validator == "minItems":
        issue["min_items"] = error.validator_value
        issue["received_items"] = len(error.instance) if isinstance(error.instance, list) else 0
    elif error.validator == "maxItems":
        issue["max_items"] = error.validator_value
        issue["received_items"] = len(error.instance) if isinstance(error.instance, list) else 0
    elif error.validator == "additionalProperties":
        allowed = (
            set(error.schema.get("properties", {})) if isinstance(error.schema, dict) else set()
        )
        extra_keys = (
            [k for k in error.instance if k not in allowed]
            if isinstance(error.instance, dict)
            else []
        )
        if extra_keys:
            issue["field"] = extra_keys[0]
        issue["allowed_fields"] = sorted(allowed)
    return issue


def _compose_relative_path(path: tuple) -> str | None:
    if not path:
        return None
    return ".".join(str(p) for p in path)


def _schema_repair_instruction(
    tool_name: str,
    issues: list[dict[str, Any]],
) -> dict[str, Any]:
    missing = [
        issue.get("field")
        for issue in issues
        if issue.get("code") == ToolValidationCode.MISSING_REQUIRED
        and isinstance(issue.get("field"), str)
    ]
    invalid = [
        issue.get("field")
        for issue in issues
        if issue.get("code") != ToolValidationCode.MISSING_REQUIRED
        and isinstance(issue.get("field"), str)
    ]
    return {
        "tool": tool_name,
        ToolValidationCode.NO_TOOL_RAN: True,
        "missing_fields": missing,
        "invalid_fields": invalid,
    }


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
