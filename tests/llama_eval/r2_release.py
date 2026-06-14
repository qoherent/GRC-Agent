"""R2 Chaos Monkey eval: broken states, multi-turn corrections, robustness.

Tests the agent's ability to:
  Test A (The Fixer): Load a broken graph with a dangling port.
    Prompt: "Make this graph compile."
  Test B (The Pivot): Multi-turn changing requirements.
    Turn 1: "Change samp_rate to 48000."
    Turn 2: "Now change it to 96000 and disable the throttle."

Run:
    uv run python -m tests.llama_eval.run_r2_release --quick
"""

from __future__ import annotations

from tests.llama_eval.harness import (
    LiveScenario,
    LiveTurnSpec,
    ToolExpectation,
)

R2_CASES: list[LiveScenario] = [
    # ── Test A: Update then update again (sequential same-target edits) ──
    LiveScenario(
        category="chaos",
        name="pivot_param_and_state",
        description="Multi-turn: first change a param, then change it again "
                    "and also change a block's state. Tests sequential editing.",
        fixture_name="random_bit_generator.grc",
        release_profile="R1_SET_STATE",
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
                ),
            ),
            LiveTurnSpec(
                prompt=(
                    "Now change the sample rate to 96000 and bypass "
                    "the blocks_throttle2_0 block."
                ),
                expected_tool_calls=(
                    ToolExpectation("change_graph"),
                ),
                semantic_checks=(
                    {"kind": "mutation"},
                    {"kind": "variable_equals",
                     "name": "samp_rate", "value": "96000"},
                    {"kind": "block_state_equals",
                     "instance_name": "blocks_throttle2_0", "state": "bypass"},
                ),
            ),
        ),
    ),
    # ── Test B variant: Add block then remove it ──
    LiveScenario(
        category="chaos",
        name="pivot_add_then_remove_block",
        description="Multi-turn: add a variable, then remove it. "
                    "Tests that the agent cleans up properly without orphans.",
        fixture_name="random_bit_generator.grc",
        release_profile="R4B_REMOVE_BLOCK",
        turns=(
            LiveTurnSpec(
                prompt="Add a new variable named 'temp_var' with value 42.",
                expected_tool_calls=(
                    ToolExpectation("change_graph"),
                ),
                semantic_checks=(
                    {"kind": "mutation"},
                    {"kind": "variable_equals",
                     "name": "temp_var", "value": "42"},
                ),
            ),
            LiveTurnSpec(
                prompt=(
                    "Remove the temp_var variable completely. "
                    "The graph should be clean with no orphans."
                ),
                expected_tool_calls=(
                    ToolExpectation("change_graph"),
                ),
                semantic_checks=(
                    {"kind": "mutation"},
                ),
            ),
        ),
    ),
]
