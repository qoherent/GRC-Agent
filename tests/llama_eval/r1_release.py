"""Native MVP R1 release eval: simple mutations.

Expected tool surface: inspect_graph, search_blocks, ask_grc_docs, change_graph.
change_graph operation_kind restricted to: set_param, set_state.
No variable creation, rewires, disconnects, inserts, removals, save, load, or multi-step chains.
No legacy tool names.

Run:
    uv run python -m tests.llama_eval.r1_release --quick
    uv run python -m tests.llama_eval.r1_release --n-runs 3
"""

from __future__ import annotations


from tests.llama_eval.harness import (
    LiveScenario,
    LiveTurnSpec,
)
from tests.llama_eval.r0_r1_shared import (
    _set_param,
    _variable_delta,
)


R1_CASES: list[LiveScenario] = [
    # ── Simple param edits ──
    LiveScenario(
        category="edit",
        name="edit_samp_rate_48k",
        description="Change samp_rate variable to 48000 via change_graph set_param.",
        release_profile="R1_SET_PARAM_ONLY",
        turns=(
            LiveTurnSpec(
                prompt="Change samp_rate to 48000.",
                expected_tool_calls=_set_param("samp_rate", "value", "48000"),
                semantic_checks=(
                    {
                        "kind": "exact_graph_delta",
                        "delta": _variable_delta("samp_rate", "48000"),
                    },
                ),
            ),
        ),
    ),
    LiveScenario(
        category="edit",
        name="edit_samp_rate_16k",
        description="Update samp_rate variable to 16000 via change_graph set_param.",
        release_profile="R1_SET_PARAM_ONLY",
        turns=(
            LiveTurnSpec(
                prompt="Update samp_rate to 16000.",
                expected_tool_calls=_set_param("samp_rate", "value", "16000"),
                semantic_checks=(
                    {
                        "kind": "exact_graph_delta",
                        "delta": _variable_delta("samp_rate", "16000"),
                    },
                ),
            ),
        ),
    ),
]
