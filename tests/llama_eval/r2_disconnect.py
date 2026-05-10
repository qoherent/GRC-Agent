"""Native MVP R2 disconnect eval (expansion track, non-release profile)."""

from __future__ import annotations

from typing import Any

from tests.llama_eval.harness import LiveScenario, LiveTurnSpec, ToolExpectation


def _disconnect_delta(connection_id: str) -> dict[str, Any]:
    return {
        "removed_connections": [connection_id],
        "dirty": True,
        "validation_status": "valid",
        "validation_returncode": 0,
    }


R2_DISCONNECT_CASES: list[LiveScenario] = [
    LiveScenario(
        category="disconnect",
        name="preview_exact_stream_disconnect",
        description="Preview exact stream disconnect by connection_id without mutation.",
        fixture_name="random_bit_generator_dual_sink_sink1_disabled.grc",
        release_profile="R2_DISCONNECT",
        turns=(
            LiveTurnSpec(
                prompt=(
                    "Preview disconnect of exact connection_id "
                    "blocks_char_to_float_0:0->qtgui_time_sink_x_1:0 and do not apply."
                ),
                expected_tool_calls=(
                    ToolExpectation(
                        "change_graph",
                        arguments={
                            "operation_kind": "disconnect",
                            "connection_id": "blocks_char_to_float_0:0->qtgui_time_sink_x_1:0",
                            "dry_run": True,
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
        category="disconnect",
        name="commit_exact_stream_disconnect",
        description="Commit exact stream disconnect by connection_id with valid end state.",
        fixture_name="random_bit_generator_dual_sink_sink1_disabled.grc",
        release_profile="R2_DISCONNECT",
        turns=(
            LiveTurnSpec(
                prompt=(
                    "Disconnect exact connection_id "
                    "blocks_char_to_float_0:0->qtgui_time_sink_x_1:0."
                ),
                expected_tool_calls=(
                    ToolExpectation(
                        "change_graph",
                        arguments={
                            "operation_kind": "disconnect",
                            "connection_id": "blocks_char_to_float_0:0->qtgui_time_sink_x_1:0",
                        },
                    ),
                ),
                semantic_checks=(
                    {
                        "kind": "exact_graph_delta",
                        "delta": _disconnect_delta(
                            "blocks_char_to_float_0:0->qtgui_time_sink_x_1:0"
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
        category="disconnect",
        name="invalid_stream_disconnect_refused",
        description="GNU-invalid stream disconnect is refused and rolled back.",
        fixture_name="random_bit_generator.grc",
        release_profile="R2_DISCONNECT",
        turns=(
            LiveTurnSpec(
                prompt=(
                    "Call change_graph now with operation_kind disconnect, dry_run false, "
                    "and connection_id blocks_char_to_float_0:0->qtgui_time_sink_x_0:0."
                ),
                expected_tool_calls=(
                    ToolExpectation(
                        "change_graph",
                        arguments={
                            "operation_kind": "disconnect",
                            "connection_id": "blocks_char_to_float_0:0->qtgui_time_sink_x_0:0",
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
                            "validation_result": {"status": "invalid"},
                        },
                    },
                ),
            ),
        ),
    ),
    LiveScenario(
        category="disconnect",
        name="ambiguous_endpoint_disconnect_clarifies",
        description="Ambiguous endpoint-hint disconnect should clarify without mutation.",
        fixture_name="rewire_stream_ambiguous.grc",
        release_profile="R2_DISCONNECT",
        turns=(
            LiveTurnSpec(
                prompt=(
                    "Call change_graph now with operation_kind disconnect and "
                    "endpoint hint dst_block=qtgui_time_sink_x_0 to remove one "
                    "matching edge."
                ),
                expected_tool_calls=(),
                semantic_checks=(
                    {"kind": "exact_graph_delta", "delta": {}},
                    {"kind": "no_mutation"},
                ),
            ),
        ),
    ),
    LiveScenario(
        category="disconnect",
        name="commit_exact_message_disconnect",
        description="Commit exact message-port disconnect by connection_id.",
        fixture_name="rewire_message_ambiguous.grc",
        release_profile="R2_DISCONNECT",
        turns=(
            LiveTurnSpec(
                prompt=(
                    "Disconnect exact connection_id strobe_0:strobe->debug_0:print."
                ),
                expected_tool_calls=(
                    ToolExpectation(
                        "change_graph",
                        arguments={
                            "operation_kind": "disconnect",
                            "connection_id": "strobe_0:strobe->debug_0:print",
                        },
                    ),
                ),
                semantic_checks=(
                    {
                        "kind": "exact_graph_delta",
                        "delta": _disconnect_delta("strobe_0:strobe->debug_0:print"),
                    },
                    {
                        "kind": "connection_absent",
                        "connection_id": "strobe_0:strobe->debug_0:print",
                    },
                ),
            ),
        ),
    ),
]
