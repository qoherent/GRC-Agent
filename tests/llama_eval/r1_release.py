"""Native MVP R1 release eval: parameter and state mutations.

Expected tool surface: inspect_graph, query_knowledge, change_graph.
Mutations are topology-only — no coordinate checks.

Semantic checks verify final graph state: variable values, block parameters,
block states, block existence, connection existence, save/reload integrity.

Run:
    uv run python -m tests.llama_eval.run_r1_release --quick
    uv run python -m tests.llama_eval.run_r1_release --n-runs 3
"""

from __future__ import annotations

from tests.llama_eval.harness import (
    LiveScenario,
    LiveTurnSpec,
    ToolExpectation,
)
from tests.llama_eval.r0_r1_shared import (
    _add_variable,
    _set_state,
)

MUTATION_CHECK = ({"kind": "mutation"},)
READ_ONLY_CHECK = ({"kind": "no_mutation"},)


R1_CASES: list[LiveScenario] = [
    # ── Parameter Updates ──
    LiveScenario(
        category="mutation",
        name="set_samp_rate",
        description="Update samp_rate variable to 48000 and verify persistence.",
        fixture_name="random_bit_generator.grc",
        release_profile="R1_SET_PARAM_ONLY",
        turns=(
            LiveTurnSpec(
                prompt="Change the sample rate to 48000.",
                expected_tool_calls=(
                    ToolExpectation("change_graph"),
                ),
                semantic_checks=(
                    {"kind": "mutation"},
                    {"kind": "variable_equals",
                     "name": "samp_rate", "value": "48000"},
                    {"kind": "saved_variable_equals",
                     "path": "{after_path}",
                     "name": "samp_rate", "value": "48000"},
                ),
            ),
        ),
    ),
    LiveScenario(
        category="mutation",
        name="set_samp_rate_via_update_variables",
        description="Update samp_rate using update_variables.",
        fixture_name="random_bit_generator.grc",
        release_profile="R1_SET_PARAM_ONLY",
        turns=(
            LiveTurnSpec(
                prompt="Set the samp_rate variable to 96000.",
                expected_tool_calls=(
                    ToolExpectation("change_graph"),
                ),
                semantic_checks=(
                    {"kind": "mutation"},
                    {"kind": "variable_equals",
                     "name": "samp_rate", "value": "96000"},
                ),
            ),
        ),
    ),
    # ── State Changes ──
    LiveScenario(
        category="mutation",
        name="disable_throttle",
        description="Bypass the throttle block via update_states (bypass is the valid GRC "
                    "state for mid-chain blocks; 'disabled' severs connections and invalidates the graph).",
        fixture_name="random_bit_generator.grc",
        release_profile="R1_SET_STATE",
        turns=(
            LiveTurnSpec(
                prompt="Bypass the blocks_throttle2_0 block.",
                expected_tool_calls=_set_state("blocks_throttle2_0", "bypass"),
                semantic_checks=(
                    {"kind": "mutation"},
                    {"kind": "block_state_equals",
                     "instance_name": "blocks_throttle2_0", "state": "bypass"},
                ),
            ),
        ),
    ),
    LiveScenario(
        category="mutation",
        name="enable_throttle",
        description="Enable a previously-disabled throttle.",
        fixture_name="random_bit_generator_dual_sink_sink1_disabled.grc",
        release_profile="R1_SET_STATE",
        turns=(
            LiveTurnSpec(
                prompt="Enable the qtgui_time_sink_x_1 block.",
                expected_tool_calls=_set_state("qtgui_time_sink_x_1", "enabled"),
                semantic_checks=(
                    {"kind": "mutation"},
                    {"kind": "block_state_equals",
                     "instance_name": "qtgui_time_sink_x_1", "state": "enabled"},
                ),
            ),
        ),
    ),
    # ── Variable Operations ──
    LiveScenario(
        category="mutation",
        name="add_variable",
        description="Add a new variable to the graph.",
        fixture_name="random_bit_generator.grc",
        release_profile="R1_SET_PARAM_ONLY",
        turns=(
            LiveTurnSpec(
                prompt="Add a new variable named 'carrier_freq' with value 10000.",
                expected_tool_calls=_add_variable("carrier_freq", "10000"),
                semantic_checks=(
                    {"kind": "mutation"},
                    {"kind": "variable_equals",
                     "name": "carrier_freq", "value": "10000"},
                    {"kind": "saved_variable_equals",
                     "path": "{after_path}",
                     "name": "carrier_freq", "value": "10000"},
                ),
            ),
        ),
    ),
    LiveScenario(
        category="mutation",
        name="remove_variable",
        description="Remove an existing variable from the graph.",
        fixture_name="random_bit_generator_with_unused_var.grc",
        release_profile="R1_SET_PARAM_ONLY",
        turns=(
            LiveTurnSpec(
                prompt="Remove the unused_var variable from the graph. The samp_rate variable should remain.",
                expected_tool_calls=(
                    ToolExpectation("change_graph"),
                ),
                semantic_checks=(
                    {"kind": "mutation"},
                    {"kind": "variable_equals",
                     "name": "samp_rate", "value": "32000"},
                    {"kind": "saved_block_absent", "path": "{after_path}", "instance_name": "unused_var"},
                ),
            ),
        ),
    ),
    # ── Block Add + Connect ──
    LiveScenario(
        category="mutation",
        name="add_null_sink",
        description="Add a null sink and connect it to the random source's output. "
                    "Source analog_random_source_x_0 outputs byte, so null_sink "
                    "must be type=byte to satisfy dtype preflight constraints.",
        fixture_name="random_bit_generator.grc",
        release_profile="R2_DISCONNECT",
        turns=(
            LiveTurnSpec(
                prompt=(
                    "Add a blocks_null_sink block named 'null_sink' with type=byte. "
                    "Connect it from analog_random_source_x_0 port 0. "
                    "Keep the existing connections intact."
                ),
                expected_tool_calls=(
                    ToolExpectation("change_graph"),
                ),
                max_tool_rounds=15,
                semantic_checks=(
                    {"kind": "mutation"},
                    {"kind": "block_param_equals",
                     "instance_name": "null_sink", "param": "type",
                     "value": "byte"},
                    {"kind": "connection_present",
                     "connection_id": "analog_random_source_x_0:0->null_sink:0"},
                ),
            ),
        ),
    ),
    # ── Guard: No mutation on read-only operation ──
    LiveScenario(
        category="mutation",
        name="read_only_guard",
        description="Read-only inspection must not mutate the graph.",
        fixture_name="random_bit_generator.grc",
        release_profile="R0_READ_ONLY",
        turns=(
            LiveTurnSpec(
                prompt="What is the current sample rate?",
                expected_tool_calls=(ToolExpectation("inspect_graph"),),
                semantic_checks=READ_ONLY_CHECK,
            ),
        ),
    ),
]
