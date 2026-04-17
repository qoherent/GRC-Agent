#!/usr/bin/env python3
"""Phase 2 model evaluation: ordered multi-tool chains."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from grc_agent.agent import GrcAgent
from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.llama_server import LlamaServerError, run_bounded_llama_turn

from tests.llama_eval.harness import (
    DEFAULT_FIXTURE_NAME,
    build_client,
    ensure_llama_server,
    extract_executed_tool_calls,
    extract_requested_tool_calls,
    isolated_fixture_workspace,
    tools_appear_in_expected_order,
)

DEFAULT_N_RUNS = 3
MAJORITY_THRESHOLD = 0.5


@dataclass(frozen=True)
class ChainCase:
    category: str
    name: str
    prompt: str
    expected_tool_sequence: list[str]
    fixture_name: str = DEFAULT_FIXTURE_NAME
    target_fixture_name: str | None = None
    description: str = ""


PHASE2_CASES: list[ChainCase] = [
    # -- search then describe --
    ChainCase(
        "search_describe",
        "constellation_decoder",
        "I need a constellation decoder. Find the right block and tell me what its ports and parameters look like.",
        ["search_grc", "describe_block"],
    ),
    ChainCase(
        "search_describe",
        "costas_loop",
        "Find the Costas loop block and explain what it does, including its ports and parameters.",
        ["search_grc", "describe_block"],
    ),
    ChainCase(
        "search_describe",
        "additive_scrambler",
        "I need an additive scrambler. Find it and give me the full block details.",
        ["search_grc", "describe_block"],
    ),
    ChainCase(
        "search_describe",
        "agc_block",
        "Look up an AGC block and walk me through its parameters and ports.",
        ["search_grc", "describe_block"],
    ),
    ChainCase(
        "search_describe",
        "freq_sink",
        "Find a frequency sink block for visualization and tell me what it expects.",
        ["search_grc", "describe_block"],
    ),
    ChainCase(
        "search_describe",
        "polyphase_channelizer",
        "Find a polyphase channelizer and tell me how that block is shaped.",
        ["search_grc", "describe_block"],
    ),
    ChainCase(
        "search_describe",
        "fir_filter",
        "I need a decimating FIR filter. Find it and describe the block.",
        ["search_grc", "describe_block"],
    ),
    ChainCase(
        "search_describe",
        "head_block",
        "Find the Head block and tell me about its inputs, outputs, and parameters.",
        ["search_grc", "describe_block"],
    ),
    # -- search, describe, then edit --
    ChainCase(
        "search_describe_edit",
        "add_head_block",
        "I want to limit how many samples get through. Find the Head block, describe it, then add a variable called num_samples with value 1000.",
        ["search_grc", "describe_block", "apply_edit"],
    ),
    ChainCase(
        "search_describe_edit",
        "change_samp_rate_after_lookup",
        "Find the throttle block, explain its parameters, then change samp_rate to 48000.",
        ["search_grc", "describe_block", "apply_edit"],
    ),
    ChainCase(
        "search_describe_edit",
        "change_samp_rate_to_44100",
        "Look up throttle2, describe it, and then update samp_rate to 44100.",
        ["search_grc", "describe_block", "apply_edit"],
    ),
    # -- search, describe, then propose --
    ChainCase(
        "search_describe_propose",
        "head_then_preview_edit",
        "I think I need a Head block. Find it, tell me what it does, then preview changing samp_rate to 48000.",
        ["search_grc", "describe_block", "propose_edit"],
    ),
    # -- search, describe, then validate --
    ChainCase(
        "search_describe_validate",
        "describe_then_validate",
        "Find the FIR filter block, describe it, then make sure my current graph still validates.",
        ["search_grc", "describe_block", "validate_graph"],
    ),
    ChainCase(
        "search_describe_validate",
        "scrambler_then_validate",
        "Find a scrambler block, describe it, then validate the current graph.",
        ["search_grc", "describe_block", "validate_graph"],
    ),
    # -- inspect then edit / propose --
    ChainCase(
        "inspect_edit",
        "context_then_edit",
        "Show me what is connected to samp_rate, then change its value to 22050.",
        ["get_grc_context", "apply_edit"],
    ),
    ChainCase(
        "inspect_edit",
        "summarize_then_edit",
        "Give me a quick summary of the graph, then update samp_rate to 8000.",
        ["summarize_graph", "apply_edit"],
    ),
    ChainCase(
        "inspect_propose",
        "context_then_propose_disconnect",
        "Show me how blocks_throttle2_0 is wired, then preview disconnecting the source from it.",
        ["get_grc_context", "propose_edit"],
    ),
    # -- propose, apply, then validate --
    ChainCase(
        "propose_apply_validate",
        "preview_then_apply_rate_change",
        "Before you change anything, preview setting samp_rate to 48000. If that looks good, apply it and validate.",
        ["propose_edit", "apply_edit", "validate_graph"],
    ),
    # -- edit then validate --
    ChainCase(
        "edit_validate",
        "edit_then_validate",
        "Change the samp_rate variable to 96000 and then validate the graph.",
        ["apply_edit", "validate_graph"],
    ),
    # -- edit then save --
    ChainCase(
        "edit_validate_save",
        "edit_validate_save",
        "Set samp_rate to 16000, validate the graph, and save it.",
        ["apply_edit", "validate_graph", "save_graph"],
    ),
    # -- switch fixture, then inspect it --
    ChainCase(
        "load_summarize_validate",
        "load_other_fixture_then_validate",
        "Open this other flowgraph, give me a quick overview, then make sure it validates: {target_path}",
        ["load_grc", "summarize_graph", "validate_graph"],
        target_fixture_name="random_bit_generator_dual_sink.grc",
    ),
    # -- session-scope search then describe --
    ChainCase(
        "search_session_describe",
        "session_source_then_describe",
        "Search the current graph for source blocks, then describe the block type you find.",
        ["search_grc", "describe_block"],
    ),
    ChainCase(
        "search_session_describe",
        "session_sink_then_describe",
        "Look in my current graph for sinks, then describe one of the block types.",
        ["search_grc", "describe_block"],
    ),
    # -- search, describe, then add a variable --
    ChainCase(
        "search_describe_add_variable",
        "find_head_add_variable",
        "Find the Head block in the library, describe it, then add a detached variable called num_samples with value 1000.",
        ["search_grc", "describe_block", "apply_edit"],
    ),
    # -- summarize then context --
    ChainCase(
        "summarize_context",
        "overview_then_neighborhood",
        "Give me a quick summary of the graph, then show me what's around blocks_throttle2_0.",
        ["summarize_graph", "get_grc_context"],
    ),
    # -- context, edit, validate --
    ChainCase(
        "inspect_edit_validate",
        "context_edit_validate",
        "Show me what's connected to samp_rate, then change its value to 22050 and validate.",
        ["get_grc_context", "apply_edit", "validate_graph"],
    ),
    # -- load different fixture, edit, validate --
    ChainCase(
        "load_edit_validate",
        "dual_sink_edit_validate",
        "Open this other flowgraph, change samp_rate to 48000, then validate: {target_path}",
        ["load_grc", "apply_edit", "validate_graph"],
        target_fixture_name="random_bit_generator_dual_sink.grc",
    ),
    # -- search, describe, propose different block --
    ChainCase(
        "search_describe_propose",
        "find_equalizer_preview",
        "Find an equalizer block, describe it, then preview changing samp_rate to 64000.",
        ["search_grc", "describe_block", "propose_edit"],
    ),
    # -- edit then save --
    ChainCase(
        "edit_save",
        "edit_then_save",
        "Set samp_rate to 22050 and save the graph.",
        ["apply_edit", "save_graph"],
    ),
    # -- propose a bad edit (tool returns ok=false, chain still completes) --
    ChainCase(
        "propose_apply_validate",
        "preview_bad_removal",
        "Preview removing the throttle block. If it won't work, explain why.",
        ["propose_edit"],
    ),
]


def _render_prompt(
    case: ChainCase, copied_fixtures: dict[str, Any], save_path: str
) -> str:
    target_path = ""
    if case.target_fixture_name:
        target_path = str(copied_fixtures[case.target_fixture_name])
    return case.prompt.format(target_path=target_path, save_path=save_path)


def _successful_tools_appear_in_expected_order(
    executed_tool_calls: list[dict[str, Any]], expected_tool_sequence: list[str]
) -> bool:
    """Return whether the expected chain completed with successful tool results."""
    call_index = 0
    for expected_tool in expected_tool_sequence:
        while call_index < len(executed_tool_calls):
            executed_call = executed_tool_calls[call_index]
            call_index += 1
            if executed_call["name"] != expected_tool:
                continue
            payload = executed_call.get("arguments")
            if isinstance(payload, dict) and payload.get("ok") is True:
                break
        else:
            return False
    return True


def _run_case(client: Any, model: str, case: ChainCase) -> dict[str, Any]:
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
    requested_sequence_matched = tools_appear_in_expected_order(
        requested_tool_names, case.expected_tool_sequence
    )
    matched = _successful_tools_appear_in_expected_order(
        executed_tool_calls, case.expected_tool_sequence
    )

    return {
        "tools_called": requested_tool_names,
        "requested_tool_calls": requested_tool_calls,
        "executed_tool_calls": executed_tool_calls,
        "requested_sequence_matched": requested_sequence_matched,
        "sequence_matched": matched,
        "ok": result["ok"] if result else False,
        "error": error_message,
        "elapsed_seconds": round(elapsed_seconds, 3),
        "assistant_text": result.get("assistant_text", "") if result else "",
        "steps": result.get("steps") if result else None,
        "tool_calls_executed": result.get("tool_calls_executed") if result else None,
    }


def _run_eval(
    server_url: str,
    model: str,
    cases: list[ChainCase],
    n_runs: int,
) -> dict[str, Any]:
    resolved_url, resolved_model = ensure_llama_server(server_url, model)
    client = build_client(resolved_url)

    results = []
    total = len(cases) * n_runs
    done = 0

    for case in cases:
        runs = []
        for run_index in range(n_runs):
            done += 1
            print(
                f"[{done}/{total}] {case.category}/{case.name} run {run_index + 1}/{n_runs}",
                end="",
                flush=True,
            )
            run_result = _run_case(client, resolved_model, case)
            status = "PASS" if run_result["sequence_matched"] else "FAIL"
            print(
                f" -> {status} ({', '.join(run_result['tools_called']) or 'no tools'})"
            )
            runs.append(run_result)

        match_count = sum(1 for run in runs if run["sequence_matched"])
        pass_rate = match_count / n_runs
        passed = match_count > n_runs * MAJORITY_THRESHOLD

        results.append(
            {
                "category": case.category,
                "name": case.name,
                "prompt": case.prompt,
                "expected_tool_sequence": case.expected_tool_sequence,
                "runs": runs,
                "match_count": match_count,
                "pass_rate": pass_rate,
                "passed": passed,
            }
        )

    by_category: dict[str, dict[str, int]] = {}
    for result in results:
        category = result["category"]
        if category not in by_category:
            by_category[category] = {"passed": 0, "total": 0}
        by_category[category]["total"] += 1
        if result["passed"]:
            by_category[category]["passed"] += 1

    total_passed = sum(1 for result in results if result["passed"])

    return {
        "phase": 2,
        "model": resolved_model,
        "temperature": client.temperature,
        "n_runs": n_runs,
        "majority_threshold": MAJORITY_THRESHOLD,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "cases": results,
        "summary": {
            "total": len(cases),
            "passed": total_passed,
            "pass_rate": round(total_passed / len(cases), 4) if cases else 0,
            "by_category": by_category,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Phase 2 model eval: ordered multi-tool chains."
    )
    parser.add_argument(
        "--server-url",
        default=os.environ.get("GRC_AGENT_LIVE_LLAMA_URL"),
        help="llama.cpp server URL. Defaults to GRC_AGENT_LIVE_LLAMA_URL or config.",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("GRC_AGENT_LIVE_LLAMA_MODEL"),
        help="llama.cpp model alias. Defaults to GRC_AGENT_LIVE_LLAMA_MODEL or config.",
    )
    parser.add_argument(
        "--n-runs",
        type=int,
        default=DEFAULT_N_RUNS,
        help=f"Number of runs per case. Default: {DEFAULT_N_RUNS}.",
    )
    parser.add_argument(
        "--category",
        type=str,
        default=None,
        help="Run only cases in this category.",
    )
    parser.add_argument(
        "--case",
        type=str,
        default=None,
        help="Run only the case with this name.",
    )
    args = parser.parse_args()

    cases = list(PHASE2_CASES)
    if args.category:
        cases = [case for case in cases if case.category == args.category]
    if args.case:
        cases = [case for case in cases if case.name == args.case]
    if not cases:
        print("No matching cases.", file=sys.stderr)
        return 1

    report = _run_eval(args.server_url, args.model, cases, args.n_runs)
    print("\n" + json.dumps(report, indent=2, sort_keys=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
