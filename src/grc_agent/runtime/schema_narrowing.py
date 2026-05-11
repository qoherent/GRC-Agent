"""Turn-scoped tool schema narrowing helpers."""

from __future__ import annotations

import copy
from typing import Any, Callable

from grc_agent.runtime.turn_plan import INTENT_REWIRE

ExactRewireEndpoint = tuple[str, int | str, str, int | str] | None


def schema_narrowed_for_turn(
    schema: dict[str, Any],
    *,
    turn_plan: Any,
    exact_new_rewire_endpoint: Callable[[], ExactRewireEndpoint],
) -> dict[str, Any]:
    name = schema["function"]["name"]
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
