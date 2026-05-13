"""Turn-scoped tool schema narrowing helpers."""

from __future__ import annotations

import copy
from typing import Any, Callable

from grc_agent.runtime.turn_plan import (
    INTENT_ADD_VARIABLE,
    INTENT_DISCONNECT,
    INTENT_INSERTION,
    INTENT_REWIRE,
)

ExactRewireEndpoint = tuple[str, int | str, str, int | str] | None


def schema_narrowed_for_turn(
    schema: dict[str, Any],
    *,
    turn_plan: Any,
    exact_new_rewire_endpoint: Callable[[], ExactRewireEndpoint],
) -> dict[str, Any]:
    name = schema["function"]["name"]
    if name == "change_graph":
        narrowed = _narrow_change_graph_for_turn(schema, turn_plan)
        if narrowed is not None:
            return narrowed
    if name == "get_grc_context" and turn_plan.target_ref:
        narrowed = copy.deepcopy(schema)
        parameters = narrowed["function"]["parameters"]
        node_id = parameters["properties"]["node_id"]
        node_id["enum"] = [turn_plan.target_ref]
        node_id["description"] = (
            "Exact loaded session node selected by the typed turn policy."
        )
        return narrowed
    if (
        name == "rewire_connection"
        and turn_plan.intent == INTENT_REWIRE
        and (exact_new_endpoint := exact_new_rewire_endpoint()) is not None
    ):
        narrowed = copy.deepcopy(schema)
        parameters = narrowed["function"]["parameters"]
        parameters["required"] = [
            "new_src_block",
            "new_src_port",
            "new_dst_block",
            "new_dst_port",
        ]
        src_block, src_port, dst_block, dst_port = exact_new_endpoint
        properties = parameters["properties"]
        properties["new_src_block"]["enum"] = [src_block]
        properties["new_src_port"]["enum"] = [src_port]
        properties["new_dst_block"]["enum"] = [dst_block]
        properties["new_dst_port"]["enum"] = [dst_port]
        return narrowed
    if (
        name not in {"apply_edit", "propose_edit"}
        or turn_plan.expected_op_types
        not in {("update_states",), ("update_params",)}
    ):
        return schema

    narrowed = copy.deepcopy(schema)
    parameters = narrowed["function"]["parameters"]
    transaction = parameters["properties"]["transaction"]
    if turn_plan.expected_op_types == ("update_params",):
        if not turn_plan.target_ref or not turn_plan.parameter_name:
            return schema
        transaction.clear()
        transaction.update(
            {
                "type": "object",
                "description": "One update_params operation for an exact loaded block parameter.",
                "properties": {
                    "op_type": {
                        "type": "string",
                        "enum": ["update_params"],
                    },
                    "instance_name": {
                        "type": "string",
                        "enum": [turn_plan.target_ref],
                    },
                    "params": {
                        "type": "object",
                        "properties": {
                            turn_plan.parameter_name: {
                                "type": ["string", "number", "integer", "boolean"],
                            }
                        },
                        "required": [turn_plan.parameter_name],
                        "additionalProperties": False,
                    },
                },
                "required": ["op_type", "instance_name", "params"],
                "additionalProperties": False,
            }
        )
        return narrowed

    transaction.clear()
    transaction.update(
        {
            "type": "object",
            "description": (
                "One update_states operation. Use this only to enable or disable "
                "one loaded block instance."
            ),
            "properties": {
                "op_type": {
                    "type": "string",
                    "enum": ["update_states"],
                },
                "instance_name": {
                    "type": "string",
                    "description": "Loaded block instance name.",
                },
                "state": {
                    "type": "string",
                    "enum": ["enabled", "disabled"],
                },
            },
            "required": ["op_type", "instance_name", "state"],
            "additionalProperties": False,
        }
    )
    return narrowed


def _narrow_change_graph_for_turn(
    schema: dict[str, Any],
    turn_plan: Any,
) -> dict[str, Any] | None:
    """Constrain the generic MVP mutation wrapper for clear single-operation turns."""
    required_by_intent: dict[str, tuple[str, ...]] = {
        INTENT_DISCONNECT: ("dry_run", "user_goal", "operation_kind", "connection_id"),
        INTENT_REWIRE: (
            "dry_run",
            "user_goal",
            "operation_kind",
            "connection_id",
            "new_src_block",
            "new_src_port",
            "new_dst_block",
            "new_dst_port",
        ),
        INTENT_INSERTION: (
            "dry_run",
            "user_goal",
            "operation_kind",
            "connection_id",
            "block_id",
            "instance_name",
        ),
        INTENT_ADD_VARIABLE: (
            "dry_run",
            "user_goal",
            "operation_kind",
            "variable_name",
            "variable_value",
        ),
    }
    operation_by_intent = {
        INTENT_DISCONNECT: "disconnect",
        INTENT_REWIRE: "rewire",
        INTENT_INSERTION: "insert_block",
        INTENT_ADD_VARIABLE: "add_variable",
    }
    required = required_by_intent.get(getattr(turn_plan, "intent", ""))
    operation_kind = operation_by_intent.get(getattr(turn_plan, "intent", ""))
    if required is None or operation_kind is None:
        return None

    narrowed = copy.deepcopy(schema)
    parameters = narrowed["function"]["parameters"]
    properties = parameters["properties"]
    parameters["required"] = list(required)
    properties["operation_kind"]["enum"] = [operation_kind]
    properties["operation_kind"]["description"] = (
        f"This turn is classified as {operation_kind}; use this exact operation_kind."
    )
    preview_requested = "propose_edit" in tuple(getattr(turn_plan, "required_actions", ()))
    properties["dry_run"]["enum"] = [preview_requested]
    if preview_requested:
        properties["dry_run"]["description"] = (
            "This turn is an explicit preview/dry-run request. Use dry_run=true "
            "and do not make a follow-up committed mutation in this turn."
        )
    else:
        properties["dry_run"]["description"] = (
            "This turn is an explicit commit request. Use dry_run=false."
        )
    if operation_kind == "insert_block":
        connection_id = getattr(turn_plan, "parameter_name", "")
        insert_param_type = getattr(turn_plan, "insert_param_type", "")
        insert_param_samples_per_second = getattr(
            turn_plan,
            "insert_param_samples_per_second",
            "",
        )
        properties["connection_id"]["description"] = (
            "Exact connection id in source_block:source_port->dest_block:dest_port "
            "format. Copy every colon and port number exactly; do not omit ports."
        )
        if isinstance(connection_id, str) and connection_id:
            properties["connection_id"]["enum"] = [connection_id]
            properties["connection_id"]["description"] = (
                "Use this exact connection_id from the user's insertion request. "
                "Copy it exactly, including both port numbers."
            )
        properties["insert_params"]["description"] = (
            "Optional parameter overrides for the inserted block. Include every "
            "parameter the user specified, such as {'type': 'float', "
            "'samples_per_second': 'samp_rate'}."
        )
        properties["insert_params"]["required"] = ["type"]
        properties["insert_params"]["properties"]["type"]["description"] = (
            "Required whenever insert_params is supplied. Copy the user's GNU "
            "item type exactly, for example 'byte' or 'float'."
        )
        if isinstance(insert_param_type, str) and insert_param_type:
            if "insert_params" not in parameters["required"]:
                parameters["required"].append("insert_params")
            properties["insert_params"]["description"] = (
                "Required for this insertion because the user supplied explicit "
                "insert_params. Include the type key exactly."
            )
            properties["insert_params"]["properties"]["type"]["enum"] = [insert_param_type]
            properties["insert_params"]["properties"]["type"]["description"] = (
                "Use this exact type from the user's insert_params."
            )
        if (
            isinstance(insert_param_samples_per_second, str)
            and insert_param_samples_per_second
        ):
            properties["insert_params"]["properties"]["samples_per_second"]["enum"] = [
                insert_param_samples_per_second
            ]
            properties["insert_params"]["properties"]["samples_per_second"][
                "description"
            ] = "Use this exact samples_per_second value from the user's insert_params."
        target_ref = getattr(turn_plan, "target_ref", "")
        if isinstance(target_ref, str) and target_ref:
            properties["instance_name"]["enum"] = [target_ref]
            properties["instance_name"]["description"] = (
                "Use this exact new instance name from the user's insertion request."
            )
    if operation_kind == "disconnect":
        target_ref = getattr(turn_plan, "target_ref", "")
        properties["connection_id"]["description"] = (
            "Exact connection id in source_block:source_port->dest_block:dest_port "
            "format. Copy every colon and port number exactly; do not omit the "
            "source port."
        )
        if isinstance(target_ref, str) and target_ref:
            properties["connection_id"]["enum"] = [target_ref]
            properties["connection_id"]["description"] = (
                "Use this exact connection_id from the user's request. Copy it "
                "exactly, including both port numbers."
            )
    if operation_kind == "rewire":
        properties["new_dst_block"]["description"] = (
            "Destination block after rewiring. If the prompt says a block input "
            "receives samples from a new source, this is that input block."
        )
        properties["new_dst_port"]["description"] = (
            "Destination port after rewiring. If the prompt says input N, use N."
        )
    return narrowed
