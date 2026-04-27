#!/usr/bin/env python3
"""Phase 1 model evaluation: single-tool routing accuracy."""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from typing import Any

from grc_agent.agent import GrcAgent
from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.llama_server import LlamaServerError, run_bounded_llama_turn

from tests.llama_eval.harness import (
    DEFAULT_FIXTURE_NAME,
    build_phase_parser,
    extract_executed_tool_calls,
    extract_requested_tool_calls,
    isolated_fixture_workspace,
    majority_passed,
    run_phase_eval,
    select_cases,
)

DEFAULT_N_RUNS = 3
MAJORITY_THRESHOLD = 0.5


@dataclass(frozen=True)
class EvalCase:
    category: str
    name: str
    prompt: str
    expected_tool: str
    fixture_name: str = DEFAULT_FIXTURE_NAME
    target_fixture_name: str | None = None
    description: str = ""


PHASE1_CASES: list[EvalCase] = [
    # -- summarize_graph --
    EvalCase(
        "summarize", "summarize_direct", "Summarize this flowgraph.", "summarize_graph"
    ),
    EvalCase(
        "summarize",
        "summarize_what_does",
        "What does this flowgraph do?",
        "summarize_graph",
    ),
    EvalCase(
        "summarize",
        "summarize_overview",
        "Give me a quick overview of the loaded graph.",
        "summarize_graph",
    ),
    EvalCase(
        "summarize",
        "summarize_blocks",
        "What blocks are in here?",
        "summarize_graph",
    ),
    # -- load_grc --
    EvalCase(
        "load",
        "load_other_fixture",
        "Switch over to this other flowgraph: {target_path}",
        "load_grc",
        target_fixture_name="random_bit_generator_dual_sink.grc",
    ),
    # -- search_grc (catalog) --
    EvalCase(
        "search",
        "search_throttle",
        "Find throttle blocks in the GNU Radio block library.",
        "search_grc",
    ),
    EvalCase(
        "search",
        "search_time_sink",
        "What time sink blocks are available in GNU Radio?",
        "search_grc",
    ),
    EvalCase(
        "search",
        "search_qtgui",
        "Show me blocks related to QT GUI.",
        "search_grc",
    ),
    EvalCase(
        "search",
        "search_sink_blocks",
        "What sink blocks can I use?",
        "search_grc",
    ),
    # -- search_grc (session) --
    EvalCase(
        "search",
        "search_session_source",
        "Search the current graph for source blocks.",
        "search_grc",
    ),
    EvalCase(
        "search",
        "search_session_sink",
        "Look through my current graph for sink blocks.",
        "search_grc",
    ),
    # -- get_grc_context --
    EvalCase(
        "context",
        "context_samp_rate",
        "Show me what uses the samp_rate block.",
        "get_grc_context",
    ),
    EvalCase(
        "context",
        "context_throttle",
        "What is blocks_throttle2_0 connected to?",
        "get_grc_context",
    ),
    EvalCase(
        "context",
        "context_random_source",
        "Show me the neighborhood around analog_random_source_x_0.",
        "get_grc_context",
    ),
    EvalCase(
        "context",
        "context_time_sink",
        "What is near qtgui_time_sink_x_0 in this graph?",
        "get_grc_context",
    ),
    # -- describe_block --
    EvalCase(
        "describe",
        "describe_throttle",
        "Tell me about the blocks_throttle block.",
        "describe_block",
    ),
    EvalCase(
        "describe",
        "describe_time_sink",
        "What are the parameters on qtgui_time_sink_x?",
        "describe_block",
    ),
    EvalCase(
        "describe",
        "describe_char_to_float",
        "Tell me about blocks_char_to_float.",
        "describe_block",
    ),
    EvalCase(
        "describe",
        "describe_vector_source",
        "What does blocks_vector_source_b do?",
        "describe_block",
    ),
    # -- validate_graph --
    EvalCase("validate", "validate_direct", "Validate this graph.", "validate_graph"),
    EvalCase(
        "validate",
        "validate_check",
        "Check whether this flowgraph is valid.",
        "validate_graph",
    ),
    EvalCase(
        "validate",
        "validate_is_valid",
        "Will this compile cleanly?",
        "validate_graph",
    ),
    # -- apply_edit --
    EvalCase(
        "edit",
        "edit_samp_rate_48k",
        "Change samp_rate to 48000.",
        "apply_edit",
    ),
    EvalCase(
        "edit",
        "edit_samp_rate_32k",
        "Set the sample rate variable to 32000.",
        "apply_edit",
    ),
    EvalCase(
        "edit",
        "edit_samp_rate_16k",
        "Update samp_rate to 16000.",
        "apply_edit",
    ),
    EvalCase(
        "edit",
        "edit_samp_rate_verbal",
        "Can you bump the sample rate up to 96000?",
        "apply_edit",
    ),
    EvalCase(
        "edit",
        "edit_remove_connection",
        "Disconnect analog_random_source_x_0 output 0 from blocks_throttle2_0 input 0.",
        "apply_edit",
    ),
    EvalCase(
        "edit",
        "edit_second_trace",
        "Put the existing float stream on a second trace of qtgui_time_sink_x_0.",
        "apply_edit",
    ),
    # -- propose_edit --
    EvalCase(
        "propose",
        "propose_samp_rate",
        "Preview changing samp_rate to 64000 before you touch anything.",
        "propose_edit",
    ),
    EvalCase(
        "propose",
        "propose_remove_variable",
        "Preview removing the samp_rate variable while keeping the graph working.",
        "propose_edit",
    ),
    # -- save_graph --
    EvalCase("save", "save_direct", "Save the graph.", "save_graph"),
    EvalCase("save", "save_to_disk", "Write this flowgraph to disk.", "save_graph"),
    EvalCase("save", "save_persist", "Persist the current graph.", "save_graph"),
    # -- search_grc domain-specific --
    EvalCase(
        "search",
        "search_ofdm",
        "Find OFDM-related blocks in the GNU Radio library.",
        "search_grc",
    ),
    EvalCase(
        "search",
        "search_psk_mod",
        "What PSK modulation blocks are available?",
        "search_grc",
    ),
    # -- describe_block with colloquial names --
    EvalCase(
        "describe",
        "describe_agc_common",
        "Tell me about the AGC block.",
        "describe_block",
    ),
    EvalCase(
        "describe",
        "describe_sink_common",
        "What is a QT GUI time sink?",
        "describe_block",
    ),
    # -- apply_edit with add_block and explicit remove --
    EvalCase(
        "edit",
        "edit_add_variable",
        "Add a detached variable called debug_flag with value 0.",
        "apply_edit",
    ),
    EvalCase(
        "edit",
        "edit_remove_block_explicit",
        "Remove the samp_rate variable block.",
        "apply_edit",
    ),
    # -- save_graph with explicit path --
    EvalCase(
        "save",
        "save_to_explicit_path",
        "Save the graph to {save_path}.",
        "save_graph",
    ),
]


def _render_prompt(
    case: EvalCase, copied_fixtures: dict[str, Any], save_path: str
) -> str:
    target_path = ""
    if case.target_fixture_name:
        target_path = str(copied_fixtures[case.target_fixture_name])
    return case.prompt.format(target_path=target_path, save_path=save_path)


def _run_case(client: Any, model: str, case: EvalCase) -> dict[str, Any]:
    with isolated_fixture_workspace(case.fixture_name, case.target_fixture_name) as (
        workspace,
        copied_fixtures,
    ):
        session = FlowgraphSession()
        session.load(copied_fixtures[case.fixture_name])
        agent = GrcAgent(session)
        prompt = _render_prompt(
            case, copied_fixtures, str(workspace / "saved_copy.grc")
        )

        started_at = time.perf_counter()
        try:
            result = run_bounded_llama_turn(
                agent,
                client,
                prompt,
                model=model,
            )
            error_message = None
        except LlamaServerError as exc:
            result = None
            error_message = str(exc)
        elapsed_seconds = time.perf_counter() - started_at

    requested_tool_calls = extract_requested_tool_calls(agent.history)
    executed_tool_calls = extract_executed_tool_calls(agent.history)
    requested_tool_names = [tool_call["name"] for tool_call in requested_tool_calls]
    matched = (
        case.expected_tool in requested_tool_names if requested_tool_names else False
    )

    return {
        "tools_called": requested_tool_names,
        "requested_tool_calls": requested_tool_calls,
        "executed_tool_calls": executed_tool_calls,
        "matched": matched,
        "ok": result["ok"] if result else False,
        "error": error_message,
        "elapsed_seconds": round(elapsed_seconds, 3),
        "assistant_text": result.get("assistant_text", "") if result else "",
        "steps": result.get("steps") if result else None,
        "tool_calls_executed": result.get("tool_calls_executed") if result else None,
    }


def _render_run_status(case: EvalCase, run_result: dict[str, Any]) -> str:
    return (
        f"{'PASS' if run_result['matched'] else 'FAIL'} "
        f"({', '.join(run_result['tools_called']) or 'no tools'})"
    )


def _build_case_report(
    case: EvalCase,
    runs: list[dict[str, Any]],
    n_runs: int,
    majority_threshold: float,
) -> dict[str, Any]:
    match_count = sum(1 for run in runs if run["matched"])
    pass_rate = match_count / n_runs
    passed = majority_passed(match_count, n_runs, majority_threshold)
    return {
        "category": case.category,
        "name": case.name,
        "prompt": case.prompt,
        "expected_tool": case.expected_tool,
        "runs": runs,
        "match_count": match_count,
        "pass_rate": pass_rate,
        "passed": passed,
    }


def _run_eval(
    server_url: str,
    model: str,
    cases: list[EvalCase],
    n_runs: int,
    **kwargs: Any,
) -> dict[str, Any]:
    return run_phase_eval(
        phase=1,
        server_url=server_url,
        model=model,
        cases=cases,
        n_runs=n_runs,
        majority_threshold=MAJORITY_THRESHOLD,
        run_case=_run_case,
        build_case_report=_build_case_report,
        render_status=_render_run_status,
        retry_on_timeout=True,
        **kwargs,
    )


def main() -> int:
    parser = build_phase_parser(
        "Phase 1 model eval: single-tool routing accuracy.",
        default_n_runs=DEFAULT_N_RUNS,
        server_help="llama.cpp server URL. Defaults to GRC_AGENT_LIVE_LLAMA_URL or config.",
        model_help="llama.cpp model alias. Defaults to GRC_AGENT_LIVE_LLAMA_MODEL or config.",
    )
    args = parser.parse_args()
    n_runs = 1 if args.quick else args.n_runs

    cases = select_cases(
        PHASE1_CASES,
        category=args.category,
        case_name=args.case,
    )
    if not cases:
        print("No matching cases.", file=sys.stderr)
        return 1

    report = _run_eval(args.server_url, args.model, cases, n_runs)
    print("\n" + json.dumps(report, indent=2, sort_keys=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
