"""Native MVP R4B remove-block eval (expansion track, non-release profile)."""

from __future__ import annotations

from typing import Any

from tests.llama_eval.harness import LiveScenario, LiveTurnSpec, ToolExpectation


def _detached_remove_delta(*, removed_block: str) -> dict[str, Any]:
    return {
        "removed_blocks": [removed_block],
        "dirty": True,
        "validation_status": "valid",
        "validation_returncode": 0,
    }


def _attached_remove_delta(*, removed_block: str, removed_connection: str) -> dict[str, Any]:
    return {
        "removed_blocks": [removed_block],
        "removed_connections": [removed_connection],
        "dirty": True,
        "validation_status": "valid",
        "validation_returncode": 0,
    }


R4B_REMOVE_CASES: list[LiveScenario] = [
    LiveScenario(
        category="remove",
        name="preview_detached_remove",
        description="Preview detached remove without mutation.",
        fixture_name="random_bit_generator_with_unused_var.grc",
        release_profile="R4B_REMOVE",
        turns=(
            LiveTurnSpec(
                prompt=(
                    "Call change_graph now with dry_run true, "
                    "operation_kind remove_block, instance_name unused_var, "
                    "and user_goal 'preview remove detached unused_var'."
                ),
                expected_tool_calls=(
                    ToolExpectation(
                        "change_graph",
                        arguments={
                            "operation_kind": "remove_block",
                            "dry_run": True,
                            "instance_name": "unused_var",
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
        category="remove",
        name="commit_detached_remove",
        description="Commit detached remove with one removed block and zero removed connections.",
        fixture_name="random_bit_generator_with_unused_var.grc",
        release_profile="R4B_REMOVE",
        turns=(
            LiveTurnSpec(
                prompt=(
                    "Use the change_graph tool now with exactly: dry_run false, "
                    "operation_kind remove_block, instance_name unused_var, "
                    "user_goal 'remove detached unused_var', and no detach arguments."
                ),
                expected_tool_calls=(
                    ToolExpectation(
                        "change_graph",
                        arguments={
                            "operation_kind": "remove_block",
                            "dry_run": False,
                            "instance_name": "unused_var",
                        },
                    ),
                ),
                semantic_checks=(
                    {
                        "kind": "exact_graph_delta",
                        "delta": _detached_remove_delta(removed_block="unused_var"),
                    },
                ),
            ),
        ),
    ),
    LiveScenario(
        category="remove",
        name="attached_remove_refused_without_explicit_detach",
        description="Attached remove must refuse without explicit detach confirmation.",
        fixture_name="random_bit_generator_dual_sink_sink1_disabled.grc",
        release_profile="R4B_REMOVE",
        turns=(
            LiveTurnSpec(
                prompt=(
                    "Call change_graph now with dry_run false, "
                    "operation_kind remove_block, instance_name qtgui_time_sink_x_1, "
                    "and user_goal 'remove sink without detach confirmation'."
                ),
                expected_tool_calls=(
                    ToolExpectation(
                        "change_graph",
                        arguments={
                            "operation_kind": "remove_block",
                            "dry_run": False,
                            "instance_name": "qtgui_time_sink_x_1",
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
                            "error_type": "clarification_required",
                        },
                    },
                ),
            ),
        ),
    ),
    LiveScenario(
        category="remove",
        name="attached_remove_with_explicit_detach_commit",
        description="Attached remove commits only with explicit detach request.",
        fixture_name="random_bit_generator_dual_sink_sink1_disabled.grc",
        release_profile="R4B_REMOVE",
        turns=(
            LiveTurnSpec(
                prompt=(
                    "Call change_graph now with this exact JSON args object: "
                    "{{\"dry_run\": false, \"operation_kind\": \"remove_block\", "
                    "\"instance_name\": \"qtgui_time_sink_x_1\", "
                    "\"detach_connection_ids\": [\"blocks_char_to_float_0:0->qtgui_time_sink_x_1:0\"], "
                    "\"user_goal\": \"remove sink and explicitly detach its connections\"}}."
                ),
                expected_tool_calls=(
                    ToolExpectation(
                        "change_graph",
                        arguments={
                            "operation_kind": "remove_block",
                            "dry_run": False,
                            "instance_name": "qtgui_time_sink_x_1",
                        },
                    ),
                ),
                semantic_checks=(
                    {
                        "kind": "exact_graph_delta",
                        "delta": _attached_remove_delta(
                            removed_block="qtgui_time_sink_x_1",
                            removed_connection="blocks_char_to_float_0:0->qtgui_time_sink_x_1:0",
                        ),
                    },
                    {
                        "kind": "connection_absent",
                        "connection_id": "blocks_char_to_float_0:0->qtgui_time_sink_x_1:0",
                    },
                ),
            ),
        ),
    ),
    LiveScenario(
        category="remove",
        name="duplicate_target_clarifies_no_mutation",
        description="Duplicate same-name target clarifies and does not mutate.",
        fixture_name="random_bit_generator_dual_sink_duplicate_sink_name.grc",
        release_profile="R4B_REMOVE",
        turns=(
            LiveTurnSpec(
                prompt=(
                    "Call change_graph now with dry_run false, "
                    "operation_kind remove_block, instance_name qtgui_time_sink_x_0, "
                    "and user_goal 'remove duplicate sink target'."
                ),
                expected_tool_calls=(
                    ToolExpectation(
                        "change_graph",
                        arguments={
                            "operation_kind": "remove_block",
                            "dry_run": False,
                            "instance_name": "qtgui_time_sink_x_0",
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
                            "error_type": "ambiguous_block",
                        },
                    },
                ),
            ),
        ),
    ),
    LiveScenario(
        category="remove",
        name="stale_target_ref_rejected",
        description="Stale guarded target_ref is rejected fail-closed.",
        fixture_name="random_bit_generator_with_unused_var.grc",
        release_profile="R4B_REMOVE",
        turns=(
            LiveTurnSpec(
                pre_turn_tool_name="apply_edit",
                pre_turn_tool_args={
                    "transaction": {
                        "op_type": "update_params",
                        "instance_name": "samp_rate",
                        "params": {"value": "48000"},
                    }
                },
                prompt=(
                    "Call change_graph now with this exact JSON args object: "
                    "{{\"dry_run\": false, \"operation_kind\": \"remove_block\", "
                    "\"user_goal\": \"stale guarded remove\", "
                    "\"target_ref\": {{\"uid\": \"block:bf45fbb32d34fa07\", "
                    "\"instance_name\": \"unused_var\", "
                    "\"block_type\": \"variable\", "
                    "\"base_state_revision\": 1}}}}."
                ),
                expected_tool_calls=(
                    ToolExpectation(
                        "change_graph",
                        arguments={
                            "operation_kind": "remove_block",
                            "dry_run": False,
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
                            "error_type": "stale_revision",
                        },
                    },
                ),
            ),
        ),
    ),
    LiveScenario(
        category="remove",
        name="referenced_dependency_remove_refused",
        description="Referenced dependency removal is refused unless explicitly repaired.",
        fixture_name="random_bit_generator.grc",
        release_profile="R4B_REMOVE",
        turns=(
            LiveTurnSpec(
                prompt=(
                    "Call change_graph now with dry_run false, "
                    "operation_kind remove_block, instance_name samp_rate, "
                    "and user_goal 'remove referenced samp_rate without repair'."
                ),
                expected_tool_calls=(
                    ToolExpectation(
                        "change_graph",
                        arguments={
                            "operation_kind": "remove_block",
                            "dry_run": False,
                            "instance_name": "samp_rate",
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
                            "error_type": "preflight_rejected",
                        },
                    },
                ),
            ),
        ),
    ),
]
