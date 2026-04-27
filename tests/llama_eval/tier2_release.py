#!/usr/bin/env python3
"""Tier 2 release model eval: broader coverage for release-time checks.

36 cases selected from the original phase 1-6 suite, updated for
the current tool contract. Run only at release time or manually.

Run:
    uv run python -m tests.llama_eval.tier2_release
    uv run python -m tests.llama_eval.tier2_release --quick
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from typing import Any

from grc_agent.agent import GrcAgent
from grc_agent.llama_server import run_bounded_llama_turn

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

DEFAULT_N_RUNS = 1
MAJORITY_THRESHOLD = 0.5


@dataclass(frozen=True)
class Tier2Case:
    category: str
    name: str
    prompt: str
    expected_tools: list[str]
    accept_any_tool: bool = False
    fixture_name: str = DEFAULT_FIXTURE_NAME
    description: str = ""


TIER2_CASES: list[Tier2Case] = [
    # Representative single-tool routing coverage.
    Tier2Case("summarize", "summarize_what_does", "What does this flowgraph do?", ["summarize_graph"]),
    Tier2Case("summarize", "summarize_blocks", "What blocks are in here?", ["summarize_graph"]),
    Tier2Case("load", "load_other", "Switch over to this other flowgraph: {target_path}", ["load_grc"], fixture_name="random_bit_generator_dual_sink.grc"),
    Tier2Case("search", "search_time_sink", "What time sink blocks are available in GNU Radio?", ["search_grc"]),
    Tier2Case("search", "search_session_source", "Search the current graph for source blocks.", ["search_grc"]),
    Tier2Case("context", "context_throttle", "What is blocks_throttle2_0 connected to?", ["get_grc_context"]),
    Tier2Case("context", "context_samp_rate", "Show me what uses the samp_rate block.", ["get_grc_context"]),
    Tier2Case("describe", "describe_time_sink", "What are the parameters on qtgui_time_sink_x?", ["describe_block"]),
    Tier2Case("describe", "describe_char_to_float", "Tell me about blocks_char_to_float.", ["describe_block"]),
    Tier2Case("validate", "validate_check", "Check whether this flowgraph is valid.", ["validate_graph"]),
    Tier2Case("validate", "validate_compile", "Will this compile cleanly?", ["validate_graph"]),
    Tier2Case("save", "save_direct", "Save the graph.", ["save_graph"]),
    Tier2Case("save", "save_to_path", "Save the graph to {save_path}.", ["save_graph"]),
    Tier2Case("edit", "edit_samp_rate_48k", "Change samp_rate to 48000.", ["apply_edit"]),
    Tier2Case("edit", "edit_samp_rate_16k", "Update samp_rate to 16000.", ["apply_edit"]),
    Tier2Case("edit", "edit_remove_connection", "Disconnect analog_random_source_x_0 output 0 from blocks_throttle2_0 input 0.", ["apply_edit"]),
    Tier2Case("edit", "edit_add_variable", "Add a variable called noise_level set to 0.1.", ["apply_edit"]),
    Tier2Case("propose", "propose_samp_rate", "Preview changing samp_rate to 64000 before you touch anything.", ["propose_edit"]),
    # Multi-tool chains
    Tier2Case("chain", "search_describe_agc", "I need an AGC block. Find it and tell me what its ports look like.", ["search_grc", "describe_block"]),
    Tier2Case("chain", "search_describe_fir", "Find a FIR filter block and describe it.", ["search_grc", "describe_block"]),
    Tier2Case("chain", "context_then_edit", "Show me what is connected to samp_rate, then change its value to 22050.", ["get_grc_context", "apply_edit"]),
    Tier2Case("chain", "edit_then_validate", "Change the samp_rate variable to 96000 and then validate the graph.", ["apply_edit", "validate_graph"]),
    Tier2Case("chain", "edit_validate_save", "Set samp_rate to 16000, validate the graph, and save it.", ["apply_edit", "validate_graph", "save_graph"]),
    Tier2Case("chain", "summarize_then_edit", "Give me a quick summary of the graph, then update samp_rate to 8000.", ["summarize_graph", "apply_edit"]),
    Tier2Case("chain", "preview_apply_validate", "Preview setting samp_rate to 48000, apply it, and validate.", ["propose_edit", "apply_edit", "validate_graph"]),
    # Natural/vague prompts
    Tier2Case("natural", "what_am_i_looking_at", "What am I looking at here?", ["summarize_graph"]),
    Tier2Case("natural", "is_this_going_to_work", "Is this going to compile and run?", ["validate_graph"]),
    Tier2Case("natural", "write_it_out", "Go ahead and write it out.", ["save_graph"]),
    # Domain-specific search
    Tier2Case("domain", "need_carrier_recovery", "I need something for carrier recovery in my signal.", ["search_grc"]),
    Tier2Case("domain", "want_to_see_spectrum", "I want to see the spectrum of my signal.", ["search_grc"]),
    # Negative / unsupported
    Tier2Case("negative", "undo_request", "Undo the last change.", []),
    Tier2Case("negative", "redo_request", "Redo the last change.", []),
    # Expert (no-tool, text-only)
    Tier2Case("expert", "pmt_dict_immutability", "How do I add a key to a PMT dictionary without mutating it in place?", []),
    Tier2Case("expert", "binary_short_scaling", "What scale factor between floats and 16-bit shorts?", []),
    # Rewire
    Tier2Case("rewire", "second_trace", "Put that float stream on a second trace in the time sink.", ["apply_edit"]),
    Tier2Case("rewire", "disconnect_source", "Disconnect the random source from the throttle.", ["apply_edit"]),
]


def _run_case(client: Any, model: str, case: Tier2Case) -> dict[str, Any]:
    with isolated_fixture_workspace(case.fixture_name) as (workspace, paths):
        fixture_path = paths[case.fixture_name]
        save_path = str(workspace / "output.grc")
        target_path = ""
        if case.fixture_name != DEFAULT_FIXTURE_NAME:
            src = paths.get(case.fixture_name)
            if src:
                target_path = str(src)
        prompt = case.prompt.replace("{save_path}", save_path).replace("{target_path}", target_path)

        agent = GrcAgent()
        agent.execute_tool("load_grc", {"file_path": str(fixture_path)})

        result: dict[str, Any] = {}
        error_message = ""
        started_at = time.perf_counter()
        try:
            result = run_bounded_llama_turn(
                client=client, model=model, agent=agent, user_message=prompt,
            )
        except Exception as exc:
            error_message = str(exc)
        elapsed = time.perf_counter() - started_at

        req = extract_requested_tool_calls(agent.history)
        tool_names = [tc["name"] for tc in req]

        matched = False
        if case.accept_any_tool and tool_names:
            matched = True
        elif case.expected_tools:
            idx = 0
            for t in tool_names:
                if idx < len(case.expected_tools) and t == case.expected_tools[idx]:
                    idx += 1
            matched = idx == len(case.expected_tools)
        elif not tool_names:
            matched = True

        return {
            "tools_called": tool_names,
            "requested_tool_calls": req,
            "executed_tool_calls": extract_executed_tool_calls(agent.history),
            "matched": matched,
            "ok": result.get("ok", False) if result else False,
            "error": error_message,
            "elapsed_seconds": round(elapsed, 3),
        }


def _render_status(case: Tier2Case, run: dict) -> str:
    return f"{'PASS' if run['matched'] else 'FAIL'} ({', '.join(run['tools_called']) or 'no tools'})"


def _build_report(case: Tier2Case, runs: list, n_runs: int, threshold: float) -> dict:
    mc = sum(1 for r in runs if r["matched"])
    return {
        "category": case.category, "name": case.name, "runs": runs,
        "pass_count": mc, "passed": majority_passed(mc, n_runs, threshold),
    }


def main() -> int:
    parser = build_phase_parser(
        "Tier 2 release model eval: broader coverage.",
        default_n_runs=DEFAULT_N_RUNS,
        server_help="Server URL.", model_help="Model alias.",
    )
    args = parser.parse_args()
    n_runs = 1 if args.quick else args.n_runs
    cases = select_cases(TIER2_CASES, category=args.category, case_name=args.case)
    if not cases:
        print("No matching cases.", file=sys.stderr)
        return 1
    report = run_phase_eval(
        phase=20, server_url=args.server_url, model=args.model,
        cases=cases, n_runs=n_runs, majority_threshold=MAJORITY_THRESHOLD,
        run_case=_run_case, build_case_report=_build_report,
        render_status=_render_status, retry_on_timeout=True,
    )
    print("\n" + json.dumps(report, indent=2, sort_keys=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
