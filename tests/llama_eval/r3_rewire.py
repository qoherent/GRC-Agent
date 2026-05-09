"""Native MVP R3 rewire eval (expansion track, non-release profile)."""

from __future__ import annotations

from typing import Any

from tests.llama_eval.harness import LiveScenario, LiveTurnSpec, ToolExpectation


def _rewire_delta(old_connection: str, new_connection: str) -> dict[str, Any]:
    return {
        "removed_connections": [old_connection],
        "added_connections": [new_connection],
        "dirty": True,
        "validation_status": "valid",
        "validation_returncode": 0,
    }


R3_REWIRE_CASES: list[LiveScenario] = [
    LiveScenario(
        category="rewire",
        name="preview_exact_stream_rewire",
        description="Preview exact stream rewire by connection_id without mutation.",
        release_profile="R3_REWIRE",
        turns=(
            LiveTurnSpec(
                prompt=(
                    "Preview exact rewire of old endpoint "
                    "blocks_throttle2_0:0->blocks_char_to_float_0:0 to "
                    "analog_random_source_x_0:0->blocks_char_to_float_0:0. "
                    "Use change_graph with operation_kind rewire, dry_run=true, "
                    "state_revision 1, src_block=blocks_throttle2_0, src_port=0, "
                    "dst_block=blocks_char_to_float_0, dst_port=0, "
                    "new_src_block=analog_random_source_x_0, new_src_port=0, "
                    "new_dst_block=blocks_char_to_float_0, new_dst_port=0."
                ),
                expected_tool_calls=(),
                accept_any_tool=True,
                allow_safe_text_only=True,
                semantic_checks=(
                    {"kind": "exact_graph_delta", "delta": {}},
                    {"kind": "no_mutation"},
                ),
            ),
        ),
    ),
    LiveScenario(
        category="rewire",
        name="commit_exact_stream_rewire",
        description="Commit exact stream rewire by connection_id.",
        release_profile="R3_REWIRE",
        turns=(
            LiveTurnSpec(
                prompt=(
                    "Rewire exact connection_id blocks_throttle2_0:0->blocks_char_to_float_0:0 "
                    "to analog_random_source_x_0:0->blocks_char_to_float_0:0. "
                    "Use change_graph with operation_kind rewire, dry_run=false, state_revision 1, "
                    "new_src_block=analog_random_source_x_0, new_src_port=0, "
                    "new_dst_block=blocks_char_to_float_0, new_dst_port=0."
                ),
                expected_tool_calls=(
                    ToolExpectation(
                        "change_graph",
                        arguments={
                            "operation_kind": "rewire",
                            "connection_id": "blocks_throttle2_0:0->blocks_char_to_float_0:0",
                            "dry_run": False,
                        },
                    ),
                ),
                semantic_checks=(
                    {
                        "kind": "exact_graph_delta",
                        "delta": _rewire_delta(
                            "blocks_throttle2_0:0->blocks_char_to_float_0:0",
                            "analog_random_source_x_0:0->blocks_char_to_float_0:0",
                        ),
                    },
                    {
                        "kind": "connection_absent",
                        "connection_id": "blocks_throttle2_0:0->blocks_char_to_float_0:0",
                    },
                    {
                        "kind": "connection_present",
                        "connection_id": "analog_random_source_x_0:0->blocks_char_to_float_0:0",
                    },
                ),
            ),
        ),
    ),
    LiveScenario(
        category="rewire",
        name="commit_exact_message_rewire",
        description="Commit exact message-port rewire by connection_id.",
        fixture_name="rewire_message_ambiguous.grc",
        release_profile="R3_REWIRE",
        turns=(
            LiveTurnSpec(
                prompt=(
                    "Rewire exact connection_id strobe_0:strobe->debug_0:print to "
                    "strobe_0:strobe->debug_1:print. "
                    "Use change_graph with operation_kind rewire, dry_run=false, state_revision 1, "
                    "new_src_block=strobe_0, new_src_port=strobe, "
                    "new_dst_block=debug_1, new_dst_port=print. Keep message ports as strings."
                ),
                expected_tool_calls=(
                    ToolExpectation(
                        "change_graph",
                        arguments={
                            "operation_kind": "rewire",
                            "connection_id": "strobe_0:strobe->debug_0:print",
                            "dry_run": False,
                        },
                    ),
                ),
                semantic_checks=(
                    {
                        "kind": "exact_graph_delta",
                        "delta": _rewire_delta(
                            "strobe_0:strobe->debug_0:print",
                            "strobe_0:strobe->debug_1:print",
                        ),
                    },
                    {
                        "kind": "connection_absent",
                        "connection_id": "strobe_0:strobe->debug_0:print",
                    },
                    {
                        "kind": "connection_present",
                        "connection_id": "strobe_0:strobe->debug_1:print",
                    },
                ),
            ),
        ),
    ),
    LiveScenario(
        category="rewire",
        name="invalid_rewire_refused",
        description="Invalid rewire is refused with no partial disconnect commit.",
        fixture_name="rewire_message_ambiguous.grc",
        release_profile="R3_REWIRE",
        turns=(
            LiveTurnSpec(
                prompt=(
                    "Rewire exact connection_id strobe_0:strobe->debug_0:print to "
                    "strobe_0:strobe->missing_debug:print. "
                    "Use change_graph with operation_kind rewire, dry_run=false, state_revision 1, "
                    "new_src_block=strobe_0, new_src_port=strobe, "
                    "new_dst_block=missing_debug, new_dst_port=print."
                ),
                expected_tool_calls=(
                    ToolExpectation(
                        "change_graph",
                        arguments={
                            "operation_kind": "rewire",
                            "connection_id": "strobe_0:strobe->debug_0:print",
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
                            "error_type": "preflight_rejected",
                        },
                    },
                ),
            ),
        ),
    ),
    LiveScenario(
        category="rewire",
        name="ambiguous_old_edge_clarifies",
        description="Ambiguous old-edge hints clarify and do not mutate.",
        fixture_name="rewire_message_ambiguous.grc",
        release_profile="R3_REWIRE",
        turns=(
            LiveTurnSpec(
                prompt=(
                    "Use change_graph with operation_kind rewire, dry_run=false, and state_revision 1. "
                    "Use old hints src_block strobe_0 and src_port strobe, and set "
                    "new_src_block strobe_0, new_src_port strobe, "
                    "new_dst_block debug_1, new_dst_port print."
                ),
                expected_tool_calls=(),
                accept_any_tool=True,
                allow_safe_text_only=True,
                semantic_checks=(
                    {"kind": "exact_graph_delta", "delta": {}},
                    {"kind": "no_mutation"},
                ),
            ),
        ),
    ),
    LiveScenario(
        category="rewire",
        name="ambiguous_new_endpoint_clarifies",
        description="Partial new-endpoint hints clarify and do not mutate.",
        fixture_name="rewire_message_ambiguous.grc",
        release_profile="R3_REWIRE",
        turns=(
            LiveTurnSpec(
                prompt=(
                    "Use change_graph with operation_kind rewire, dry_run=false, and state_revision 1. "
                    "Use connection_id strobe_0:strobe->debug_0:print, new_src_port strobe, "
                    "new_dst_block debug_1, and new_dst_port print."
                ),
                expected_tool_calls=(),
                accept_any_tool=True,
                allow_safe_text_only=True,
                semantic_checks=(
                    {"kind": "exact_graph_delta", "delta": {}},
                    {"kind": "no_mutation"},
                ),
            ),
        ),
    ),
    LiveScenario(
        category="rewire",
        name="stale_clarification_selection_rejected",
        description="Stored rewire clarification expires after state changes.",
        fixture_name="rewire_message_ambiguous.grc",
        release_profile="R3_REWIRE",
        turns=(
            LiveTurnSpec(
                pre_turn_tool_name="rewire_connection",
                pre_turn_tool_args={
                    "old_connection_id": "strobe_0:strobe->debug_0:print",
                    "new_src_port": "strobe",
                    "new_dst_block": "debug_1",
                    "new_dst_port": "print",
                },
                pre_turn_allow_clarification=True,
                prompt="Ready.",
                expected_tool_calls=(),
                semantic_checks=(
                    {"kind": "exact_graph_delta", "delta": {}},
                    {"kind": "no_mutation"},
                ),
            ),
            LiveTurnSpec(
                prompt="A",
                clarification_response=True,
                pre_turn_tool_name="apply_edit",
                pre_turn_tool_args={
                    "transaction": {
                        "op_type": "update_params",
                        "instance_name": "debug_1",
                        "params": {"log_level": "debug"},
                    }
                },
                semantic_checks=(
                    {"kind": "no_mutation"},
                    {"kind": "clarification_mode", "mode": "expired"},
                ),
            ),
        ),
    ),
]
