"""Native MVP R4A insert-on-connection eval (expansion track, non-release profile)."""

from __future__ import annotations

from typing import Any

from tests.llama_eval.harness import LiveScenario, LiveTurnSpec, ToolExpectation


def _insert_delta(
    *,
    old_connection: str,
    inserted_block: str,
    src_leg: str,
    dst_leg: str,
) -> dict[str, Any]:
    return {
        "added_blocks": [inserted_block],
        "removed_connections": [old_connection],
        "added_connections": [src_leg, dst_leg],
        "dirty": True,
        "validation_status": "valid",
        "validation_returncode": 0,
    }


R4A_INSERT_CASES: list[LiveScenario] = [
    LiveScenario(
        category="insert",
        name="preview_exact_compatible_insert",
        description="Preview exact compatible insert on a stream connection without mutation.",
        release_profile="R4A_INSERT",
        turns=(
            LiveTurnSpec(
                prompt=(
                    "Call change_graph now with op insert_in_connection, dry_run true, "
                    "and args containing connection_id analog_random_source_x_0:0->blocks_throttle2_0:0, "
                    "block_id blocks_abs_xx, and instance_name blocks_abs_preview."
                ),
                expected_tool_calls=(
                    ToolExpectation(
                        "change_graph",
                        arguments={
                            "op": "insert_in_connection",
                            "dry_run": True,
                        },
                    ),
                ),
                semantic_checks=(
                    {"kind": "exact_graph_delta", "delta": {}},
                    {"kind": "no_mutation"},
                    {"kind": "tool_result", "tool": "change_graph", "arguments": {"ok": True}},
                ),
            ),
        ),
    ),
    LiveScenario(
        category="insert",
        name="commit_exact_compatible_insert",
        description="Commit exact compatible insert with expected one-removed/two-added edge delta.",
        release_profile="R4A_INSERT",
        turns=(
            LiveTurnSpec(
                prompt=(
                    "Call change_graph now with op insert_in_connection, dry_run false, "
                    "and args containing connection_id analog_random_source_x_0:0->blocks_throttle2_0:0, "
                    "block_id blocks_throttle2, instance_name blocks_throttle2_r4a, "
                    "and insert_params {{type: byte, samples_per_second: 32000}}."
                ),
                expected_tool_calls=(
                    ToolExpectation(
                        "change_graph",
                        arguments={
                            "op": "insert_in_connection",
                            "dry_run": False,
                        },
                    ),
                ),
                semantic_checks=(
                    {
                        "kind": "exact_graph_delta",
                        "delta": _insert_delta(
                            old_connection="analog_random_source_x_0:0->blocks_throttle2_0:0",
                            inserted_block="blocks_throttle2_r4a",
                            src_leg="analog_random_source_x_0:0->blocks_throttle2_r4a:0",
                            dst_leg="blocks_throttle2_r4a:0->blocks_throttle2_0:0",
                        ),
                    },
                    {
                        "kind": "connection_absent",
                        "connection_id": "analog_random_source_x_0:0->blocks_throttle2_0:0",
                    },
                    {
                        "kind": "connection_present",
                        "connection_id": "analog_random_source_x_0:0->blocks_throttle2_r4a:0",
                    },
                    {
                        "kind": "connection_present",
                        "connection_id": "blocks_throttle2_r4a:0->blocks_throttle2_0:0",
                    },
                ),
            ),
        ),
    ),
    LiveScenario(
        category="insert",
        name="incompatible_candidate_refused",
        description="Incompatible insert candidate is safely refused with no mutation.",
        release_profile="R4A_INSERT",
        turns=(
            LiveTurnSpec(
                prompt=(
                    "Call change_graph now with op insert_in_connection, dry_run false, "
                    "and args containing connection_id analog_random_source_x_0:0->blocks_throttle2_0:0, "
                    "block_id blocks_add_xx, and instance_name add_xx_r4a."
                ),
                expected_tool_calls=(
                    ToolExpectation(
                        "change_graph",
                        arguments={
                            "op": "insert_in_connection",
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
                        },
                    },
                ),
            ),
        ),
    ),
    LiveScenario(
        category="insert",
        name="missing_candidate_fails_closed",
        description="Missing candidate identity fails closed and does not mutate.",
        release_profile="R4A_INSERT",
        turns=(
            LiveTurnSpec(
                prompt=(
                    "Call change_graph now with op insert_in_connection, dry_run false, "
                    "and args containing connection_id analog_random_source_x_0:0->blocks_throttle2_0:0. "
                    "Do not provide block_id."
                ),
                expected_tool_calls=(
                    ToolExpectation(
                        "change_graph",
                        arguments={
                            "op": "insert_in_connection",
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
                        },
                    },
                ),
            ),
        ),
    ),
    LiveScenario(
        category="insert",
        name="stale_state_revision_rejected",
        description="Stale insert state_revision is rejected before mutation.",
        release_profile="R4A_INSERT",
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
                    "Call change_graph now with op insert_in_connection, dry_run false, "
                    "args containing connection_id analog_random_source_x_0:0->blocks_throttle2_0:0, "
                    "block_id blocks_throttle2, instance_name stale_insert_r4a, "
                    "insert_params {{type: byte, samples_per_second: 32000}}, "
                    "state_revision 1, and include user_goal as 'stale insert attempt'."
                ),
                expected_tool_calls=(
                    ToolExpectation(
                        "change_graph",
                        arguments={
                            "op": "insert_in_connection",
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
                        },
                    },
                ),
            ),
        ),
    ),
]
