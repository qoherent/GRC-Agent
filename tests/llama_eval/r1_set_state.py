"""Native MVP R1 set_state eval (expansion track, non-release profile).

Default MVP surface includes six wrappers; this suite intentionally expects only
change_graph calls for set_state behavior.
"""

from __future__ import annotations

from typing import Any

from tests.llama_eval.harness import LiveScenario, LiveTurnSpec, ToolExpectation
from tests.llama_eval.r0_r1_shared import _set_state


def _state_delta(instance_name: str, state: str) -> dict[str, Any]:
    return {
        "block_states": {instance_name: state},
        "dirty": True,
        "validation_status": "valid",
        "validation_returncode": 0,
    }


R1_SET_STATE_CASES: list[LiveScenario] = [
    LiveScenario(
        category="state",
        name="disable_secondary_sink_valid",
        description="Disable qtgui_time_sink_x_1 while preserving graph validity.",
        fixture_name="random_bit_generator_dual_sink.grc",
        release_profile="R1_SET_STATE",
        turns=(
            LiveTurnSpec(
                prompt="Disable qtgui_time_sink_x_1.",
                expected_tool_calls=_set_state("qtgui_time_sink_x_1", "disabled"),
                semantic_checks=(
                    {
                        "kind": "exact_graph_delta",
                        "delta": _state_delta("qtgui_time_sink_x_1", "disabled"),
                    },
                ),
            ),
        ),
    ),
    LiveScenario(
        category="state",
        name="enable_secondary_sink_valid",
        description="Enable qtgui_time_sink_x_1 from a disabled fixture baseline.",
        fixture_name="random_bit_generator_dual_sink_sink1_disabled.grc",
        release_profile="R1_SET_STATE",
        turns=(
            LiveTurnSpec(
                prompt="Enable qtgui_time_sink_x_1.",
                expected_tool_calls=_set_state("qtgui_time_sink_x_1", "enabled"),
                semantic_checks=(
                    {
                        "kind": "exact_graph_delta",
                        "delta": _state_delta("qtgui_time_sink_x_1", "enabled"),
                    },
                ),
            ),
        ),
    ),
    LiveScenario(
        category="state",
        name="disable_throttle_invalid_refused",
        description="Invalid set_state should be refused and rolled back.",
        fixture_name="random_bit_generator_dual_sink.grc",
        release_profile="R1_SET_STATE",
        turns=(
            LiveTurnSpec(
                prompt="Disable blocks_throttle2_0.",
                expected_tool_calls=(
                    ToolExpectation(
                        "change_graph",
                        arguments={
                            "operation_kind": "set_state",
                            "instance_name": "blocks_throttle2_0",
                            "state": "disabled",
                        },
                        require_result_ok=False,
                    ),
                ),
                semantic_checks=(
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
]
