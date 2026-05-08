"""Native MVP Beta eval: complex mutations, multi-step chains, external examples.

These cases are informational only and not release-gating.
Expected tool surface: inspect_graph, search_blocks, ask_grc_docs, change_graph.
Includes: add_variable, multi-step chains, external edits, catalog search, vague queries.

Run:
    uv run python -m tests.llama_eval.beta_cases --quick
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tests.llama_eval.harness import (
    LiveScenario,
    LiveTurnSpec,
    ToolExpectation,
)
from tests.llama_eval.r0_r1_shared import (
    READ_ONLY_CHECKS,
    _add_variable,
    _set_param,
    _set_param_delta,
    _variable_delta,
)

GNU_EXAMPLES = Path("/usr/share/gnuradio/examples")


def _scenario_if_present(
    *,
    category: str,
    name: str,
    relative_path: str,
    prompt: str,
    expected_tool_calls: tuple[ToolExpectation, ...],
    semantic_checks: tuple[dict[str, Any], ...],
    description: str,
) -> LiveScenario | None:
    graph_path = GNU_EXAMPLES / relative_path
    if not graph_path.exists():
        return None
    return LiveScenario(
        category=category,
        name=name,
        description=f"{description} Source: {graph_path}",
        fixture_name=str(graph_path),
        release_profile="BETA_COMPLEX_MUTATION",
        turns=(
            LiveTurnSpec(
                prompt=prompt,
                expected_tool_calls=expected_tool_calls,
                semantic_checks=semantic_checks,
            ),
        ),
    )


BETA_CASES: list[LiveScenario] = [
    # ── Vague / advisory queries (not release-gating) ──
    LiveScenario(
        category="search",
        name="need_carrier_recovery",
        description="Vague domain query; model may search, ask docs, or request clarification.",
        release_profile="BETA_COMPLEX_MUTATION",
        turns=(
            LiveTurnSpec(
                prompt="I need something for carrier recovery in my signal.",
                expected_tool_calls=(ToolExpectation("search_blocks"),),
                semantic_checks=READ_ONLY_CHECKS(),
                allow_safe_text_only=True,
            ),
        ),
    ),
    LiveScenario(
        category="search",
        name="want_to_see_spectrum",
        description="Vague domain query; model may search, inspect, or request clarification.",
        release_profile="BETA_COMPLEX_MUTATION",
        turns=(
            LiveTurnSpec(
                prompt="I want to see the spectrum of my signal.",
                expected_tool_calls=(ToolExpectation("search_blocks"),),
                semantic_checks=READ_ONLY_CHECKS(),
                allow_safe_text_only=True,
            ),
        ),
    ),
    # ── Variable creation (not R1) ──
    LiveScenario(
        category="edit",
        name="edit_add_variable",
        description="Add a variable called noise_level set to 0.1 via change_graph add_variable.",
        release_profile="BETA_COMPLEX_MUTATION",
        turns=(
            LiveTurnSpec(
                prompt="Add a variable called noise_level set to 0.1.",
                expected_tool_calls=_add_variable("noise_level", "0.1"),
                semantic_checks=(
                    {
                        "kind": "exact_graph_delta",
                        "delta": {
                            "added_blocks": ["noise_level"],
                            "variables": {"noise_level": "0.1"},
                            "dirty": True,
                            "validation_status": "valid",
                            "validation_returncode": 0,
                        },
                    },
                ),
            ),
        ),
    ),
    # ── Multi-step chains (not R1) ──
    LiveScenario(
        category="chain",
        name="context_then_edit",
        description="Inspect context then perform a simple param edit.",
        release_profile="BETA_COMPLEX_MUTATION",
        turns=(
            LiveTurnSpec(
                prompt="Show me what uses the samp_rate block, then change its value to 22050.",
                expected_tool_calls=(
                    ToolExpectation("inspect_graph", arguments={"operation": "context"}),
                    *_set_param("samp_rate", "value", "22050"),
                ),
                semantic_checks=(
                    {
                        "kind": "exact_graph_delta",
                        "delta": _variable_delta("samp_rate", "22050"),
                    },
                ),
            ),
        ),
    ),
    LiveScenario(
        category="chain",
        name="edit_then_validate",
        description="Edit param then validate via inspect_graph.",
        release_profile="BETA_COMPLEX_MUTATION",
        turns=(
            LiveTurnSpec(
                prompt="Change the samp_rate variable to 96000 and then validate the graph.",
                expected_tool_calls=(
                    *_set_param("samp_rate", "value", "96000"),
                    ToolExpectation("inspect_graph", arguments={"operation": "validate"}),
                ),
                semantic_checks=(
                    {
                        "kind": "exact_graph_delta",
                        "delta": _variable_delta("samp_rate", "96000"),
                    },
                    {
                        "kind": "tool_result",
                        "tool": "inspect_graph",
                        "arguments": {"valid": True},
                    },
                ),
            ),
        ),
    ),
    LiveScenario(
        category="chain",
        name="summarize_then_edit",
        description="Summarize then perform a simple param edit.",
        release_profile="BETA_COMPLEX_MUTATION",
        turns=(
            LiveTurnSpec(
                prompt="Give me a quick summary of the graph, then update samp_rate to 8000.",
                expected_tool_calls=(
                    ToolExpectation("inspect_graph", arguments={"operation": "summarize"}),
                    *_set_param("samp_rate", "value", "8000"),
                ),
                semantic_checks=(
                    {
                        "kind": "exact_graph_delta",
                        "delta": _variable_delta("samp_rate", "8000"),
                    },
                ),
            ),
        ),
    ),
    # ── External examples (not R1) ──
    _scenario_if_present(
        category="external_edit",
        name="dial_tone_samp_rate_edit_validate",
        relative_path="audio/dial_tone.grc",
        prompt="Change samp_rate to 44100 in this installed GNU Radio example, then validate it.",
        expected_tool_calls=(
            *_set_param("samp_rate", "value", "44100"),
            ToolExpectation("inspect_graph", arguments={"operation": "validate"}),
        ),
        semantic_checks=(
            {
                "kind": "exact_graph_delta",
                "delta": _variable_delta("samp_rate", "44100"),
            },
            {
                "kind": "tool_result",
                "tool": "inspect_graph",
                "arguments": {"valid": True},
            },
        ),
        description="Verified sample-rate edit on a copied installed audio example.",
    ),
    _scenario_if_present(
        category="external_edit",
        name="selector_signal_source_amp_edit_validate",
        relative_path="blocks/selector.grc",
        prompt="Set analog_sig_source_x_0 amp to 0.5 in this installed selector example, then validate it.",
        expected_tool_calls=(
            *_set_param("analog_sig_source_x_0", "amp", "0.5"),
            ToolExpectation("inspect_graph", arguments={"operation": "validate"}),
        ),
        semantic_checks=(
            {
                "kind": "exact_graph_delta",
                "delta": _set_param_delta("analog_sig_source_x_0", "amp", "0.5"),
            },
            {
                "kind": "tool_result",
                "tool": "inspect_graph",
                "arguments": {"valid": True},
            },
        ),
        description="Verified non-variable block-parameter edit on an installed blocks example.",
    ),
    _scenario_if_present(
        category="external_edit",
        name="var_to_msg_test_value_edit_validate",
        relative_path="blocks/var_to_msg.grc",
        prompt=(
            "Set the test block value parameter to 7 in this installed "
            "var-to-message example, then validate it."
        ),
        expected_tool_calls=(
            *_set_param("test", "value", "7"),
            ToolExpectation("inspect_graph", arguments={"operation": "validate"}),
        ),
        semantic_checks=(
            {
                "kind": "exact_graph_delta",
                "delta": _set_param_delta("test", "value", "7"),
            },
            {
                "kind": "tool_result",
                "tool": "inspect_graph",
                "arguments": {"valid": True},
            },
        ),
        description="Verified non-variable value edit on an installed message-port blocks example.",
    ),
]

BETA_CASES = [c for c in BETA_CASES if c is not None]
