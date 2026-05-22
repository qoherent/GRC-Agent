"""Native MVP R0 release eval: read-only operations.

Expected tool surface: inspect_graph, search_blocks, ask_grc_docs.
No change_graph calls. No legacy tool names.

Run:
    uv run python -m tests.llama_eval.r0_release --quick
    uv run python -m tests.llama_eval.r0_release --n-runs 3
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
    _docs,
    _inspect,
    _search,
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
        release_profile="R0_READ_ONLY",
        turns=(
            LiveTurnSpec(
                prompt=prompt,
                expected_tool_calls=expected_tool_calls,
                semantic_checks=semantic_checks,
            ),
        ),
    )


R0_CASES: list[LiveScenario] = [
    # ── Inspection ──
    LiveScenario(
        category="inspect",
        name="summarize_what_does",
        description="Basic graph summary via inspect_graph.",
        release_profile="R0_READ_ONLY",
        turns=(
            LiveTurnSpec(
                prompt="What does this flowgraph do?",
                expected_tool_calls=_inspect("summarize"),
                semantic_checks=READ_ONLY_CHECKS(),
            ),
        ),
    ),
    LiveScenario(
        category="inspect",
        name="summarize_blocks",
        description="List blocks in the graph via inspect_graph.",
        release_profile="R0_READ_ONLY",
        turns=(
            LiveTurnSpec(
                prompt="What blocks are in here?",
                expected_tool_calls=(ToolExpectation("inspect_graph"),),
                semantic_checks=READ_ONLY_CHECKS(),
            ),
        ),
    ),
    LiveScenario(
        category="inspect",
        name="context_throttle",
        description="Context lookup by block instance name.",
        release_profile="R0_READ_ONLY",
        turns=(
            LiveTurnSpec(
                prompt="What is blocks_throttle2_0 connected to?",
                expected_tool_calls=(ToolExpectation("inspect_graph"),),
                semantic_checks=READ_ONLY_CHECKS(),
            ),
        ),
    ),
    LiveScenario(
        category="inspect",
        name="context_samp_rate",
        description="Context lookup for variable usage.",
        release_profile="R0_READ_ONLY",
        turns=(
            LiveTurnSpec(
                prompt="Show me what uses the samp_rate block.",
                expected_tool_calls=(ToolExpectation("inspect_graph"),),
                semantic_checks=READ_ONLY_CHECKS(),
            ),
        ),
    ),
    LiveScenario(
        category="inspect",
        name="status_check",
        description="Graph status via inspect_graph overview.",
        release_profile="R0_READ_ONLY",
        turns=(
            LiveTurnSpec(
                prompt="Show the current graph status.",
                expected_tool_calls=_inspect("overview"),
                semantic_checks=READ_ONLY_CHECKS(),
            ),
        ),
    ),
    LiveScenario(
        category="inspect",
        name="compile_status",
        description="Current compile/validation status via inspect_graph overview.",
        release_profile="R0_READ_ONLY",
        turns=(
            LiveTurnSpec(
                prompt="What is the current validation status for the loaded graph?",
                expected_tool_calls=_inspect("overview"),
                semantic_checks=READ_ONLY_CHECKS(),
            ),
        ),
    ),
    # ── Search ──
    LiveScenario(
        category="search",
        name="search_time_sink",
        description="Catalog search for time sink blocks.",
        release_profile="R0_READ_ONLY",
        turns=(
            LiveTurnSpec(
                prompt="What time sink blocks are available in GNU Radio?",
                expected_tool_calls=_search("time sink"),
                semantic_checks=READ_ONLY_CHECKS(),
            ),
        ),
    ),
    LiveScenario(
        category="search",
        name="search_session_source",
        description="In-graph block listing for source blocks.",
        release_profile="R0_READ_ONLY",
        turns=(
            LiveTurnSpec(
                prompt="Search the current graph for source blocks.",
                expected_tool_calls=(ToolExpectation("inspect_graph"),),
                semantic_checks=READ_ONLY_CHECKS(),
            ),
        ),
    ),
    # ── Docs ──
    LiveScenario(
        category="docs",
        name="pmt_dict_immutability",
        description="Documentation question about PMT dictionaries.",
        release_profile="R0_READ_ONLY",
        turns=(
            LiveTurnSpec(
                prompt="How do I add a key to a PMT dictionary without mutating it in place?",
                expected_tool_calls=_docs("PMT dictionary add key immutable"),
                semantic_checks=READ_ONLY_CHECKS(),
                allow_safe_text_only=True,
            ),
        ),
    ),
    LiveScenario(
        category="docs",
        name="binary_short_scaling",
        description="Documentation question about scaling factors.",
        release_profile="R0_READ_ONLY",
        turns=(
            LiveTurnSpec(
                prompt="What scale factor between floats and 16-bit shorts?",
                expected_tool_calls=(ToolExpectation("ask_grc_docs"),),
                semantic_checks=READ_ONLY_CHECKS(),
                allow_safe_text_only=True,
            ),
        ),
    ),
    # ── External examples ──
    _scenario_if_present(
        category="external",
        name="dial_tone_summary",
        relative_path="audio/dial_tone.grc",
        prompt="Summarize this installed GNU Radio example flowgraph.",
        expected_tool_calls=_inspect("summarize"),
        semantic_checks=READ_ONLY_CHECKS(),
        description="Read-only summary on a small installed audio example.",
    ),
    _scenario_if_present(
        category="external",
        name="resampler_status",
        relative_path="filter/resampler_demo.grc",
        prompt="Summarize the current status of this installed GNU Radio resampler example.",
        expected_tool_calls=_inspect("overview"),
        semantic_checks=READ_ONLY_CHECKS(),
        description="Status overview on an installed filter example.",
    ),
    _scenario_if_present(
        category="external",
        name="stream_mux_status",
        relative_path="blocks/stream_mux_demo.grc",
        prompt="Summarize the current status of this installed GNU Radio stream mux example.",
        expected_tool_calls=_inspect("overview"),
        semantic_checks=READ_ONLY_CHECKS(),
        description="Status overview on an installed stream-mux blocks example.",
    ),
    _scenario_if_present(
        category="external",
        name="sig_source_msg_ports_context",
        relative_path="analog/sig_source_msg_ports.grc",
        prompt=(
            "Show me what is around analog_sig_source_x_0 in this installed "
            "message-port signal-source example."
        ),
        expected_tool_calls=_inspect("context"),
        semantic_checks=READ_ONLY_CHECKS(),
        description="Read-only context on an installed analog example with message ports.",
    ),
]

R0_CASES = [c for c in R0_CASES if c is not None]
