"""Native MVP R4C add-variable eval (expansion track, non-release profile)."""

from __future__ import annotations

from typing import Any

from tests.llama_eval.harness import LiveScenario, LiveTurnSpec, ToolExpectation


def _add_variable_delta(*, variable_name: str, variable_value: str) -> dict[str, Any]:
    return {
        "added_blocks": [variable_name],
        "variables": {variable_name: variable_value},
        "dirty": True,
        "validation_status": "valid",
        "validation_returncode": 0,
    }


R4C_ADD_VARIABLE_CASES: list[LiveScenario] = [
    LiveScenario(
        category="add_variable",
        name="preview_add_variable",
        description="Preview add_variable without mutation.",
        release_profile="R4C_ADD_VARIABLE",
        turns=(
            LiveTurnSpec(
                prompt=(
                    "Call change_graph now with operation_kind add_variable, dry_run true, "
                    "variable_name noise_level, variable_value 0.1, and user_goal 'preview add variable'."
                ),
                expected_tool_calls=(
                    ToolExpectation(
                        "change_graph",
                        arguments={
                            "operation_kind": "add_variable",
                            "dry_run": True,
                            "variable_name": "noise_level",
                            "variable_value": "0.1",
                        },
                    ),
                ),
                semantic_checks=(
                    {"kind": "exact_graph_delta", "delta": {}},
                    {"kind": "no_mutation"},
                ),
            ),
        ),
    ),
    LiveScenario(
        category="add_variable",
        name="commit_add_variable",
        description="Commit add_variable with exact graph delta and validation.",
        release_profile="R4C_ADD_VARIABLE",
        turns=(
            LiveTurnSpec(
                prompt=(
                    "Call change_graph now with operation_kind add_variable, dry_run false, "
                    "variable_name noise_level, variable_value 0.1, and user_goal 'commit add variable'."
                ),
                expected_tool_calls=(
                    ToolExpectation(
                        "change_graph",
                        arguments={
                            "operation_kind": "add_variable",
                            "dry_run": False,
                            "variable_name": "noise_level",
                            "variable_value": "0.1",
                        },
                    ),
                ),
                semantic_checks=(
                    {
                        "kind": "exact_graph_delta",
                        "delta": _add_variable_delta(
                            variable_name="noise_level",
                            variable_value="0.1",
                        ),
                    },
                    {"kind": "variable_equals", "name": "noise_level", "value": "0.1"},
                ),
            ),
        ),
    ),
    LiveScenario(
        category="add_variable",
        name="duplicate_variable_refused",
        description="Duplicate variable name is refused with no mutation.",
        release_profile="R4C_ADD_VARIABLE",
        turns=(
            LiveTurnSpec(
                prompt=(
                    "This is an add variable request. "
                    "Call change_graph now with this exact JSON args object: "
                    "{{\"operation_kind\": \"add_variable\", \"dry_run\": false, "
                    "\"variable_name\": \"samp_rate\", \"variable_value\": \"44100\", "
                    "\"user_goal\": \"add duplicate variable\"}}."
                ),
                expected_tool_calls=(
                    ToolExpectation(
                        "change_graph",
                        arguments={
                            "operation_kind": "add_variable",
                            "dry_run": False,
                            "variable_name": "samp_rate",
                        },
                        require_result_ok=False,
                    ),
                ),
                semantic_checks=(
                    {"kind": "exact_graph_delta", "delta": {}},
                    {"kind": "no_mutation"},
                    {
                        "kind": "tool_result",
                        "tool": "change_graph",
                        "arguments": {
                            "ok": False,
                            "error_type": "block_already_exists",
                        },
                    },
                ),
            ),
        ),
    ),
    LiveScenario(
        category="add_variable",
        name="invalid_expression_refused",
        description="Invalid variable expression fails validation with no mutation.",
        release_profile="R4C_ADD_VARIABLE",
        turns=(
            LiveTurnSpec(
                prompt=(
                    "This is an add variable request. "
                    "Call change_graph now with this exact JSON args object: "
                    "{{\"operation_kind\": \"add_variable\", \"dry_run\": false, "
                    "\"variable_name\": \"broken_expr\", \"variable_value\": \"(\", "
                    "\"user_goal\": \"add invalid expression\"}}."
                ),
                expected_tool_calls=(
                    ToolExpectation(
                        "change_graph",
                        arguments={
                            "operation_kind": "add_variable",
                            "dry_run": False,
                            "variable_name": "broken_expr",
                        },
                        require_result_ok=False,
                    ),
                ),
                semantic_checks=(
                    {"kind": "exact_graph_delta", "delta": {}},
                    {"kind": "no_mutation"},
                    {
                        "kind": "tool_result",
                        "tool": "change_graph",
                        "arguments": {
                            "ok": False,
                            "error_type": "gnu_validation_failed",
                        },
                    },
                ),
            ),
        ),
    ),
    LiveScenario(
        category="add_variable",
        name="missing_variable_value_fails_closed",
        description="Missing variable_value fails closed and does not mutate.",
        release_profile="R4C_ADD_VARIABLE",
        turns=(
            LiveTurnSpec(
                prompt=(
                    "This is an add variable request. "
                    "Call change_graph now with this exact JSON args object: "
                    "{{\"operation_kind\": \"add_variable\", \"dry_run\": false, "
                    "\"variable_name\": \"missing_value\", \"user_goal\": \"missing value\"}}."
                ),
                expected_tool_calls=(
                    ToolExpectation(
                        "change_graph",
                        arguments={
                            "operation_kind": "add_variable",
                            "dry_run": False,
                            "variable_name": "missing_value",
                        },
                        require_result_ok=False,
                    ),
                ),
                semantic_checks=(
                    {"kind": "exact_graph_delta", "delta": {}},
                    {"kind": "no_mutation"},
                    {
                        "kind": "tool_result",
                        "tool": "change_graph",
                        "arguments": {
                            "ok": False,
                        },
                    },
                ),
            ),
        ),
    ),
]
