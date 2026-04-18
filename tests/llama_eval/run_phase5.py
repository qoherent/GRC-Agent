#!/usr/bin/env python3
"""Phase 5 model evaluation: deeper failure handling and recovery."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
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
    text_contains_any,
    tool_call_matches_argument_checks,
    tool_call_matches_transaction_checks,
    tools_appear_in_expected_order,
)

DEFAULT_N_RUNS = 3
MAJORITY_THRESHOLD = 0.5


@dataclass(frozen=True)
class ExecutedToolSpec:
    name: str
    result_checks: dict[str, Any] | None = None


@dataclass(frozen=True)
class TurnSpec:
    prompt: str
    expected_tools_in_order: list[str] = field(default_factory=list)
    expected_executed_in_order: list[ExecutedToolSpec] = field(default_factory=list)
    forbidden_tools: list[str] = field(default_factory=list)
    checked_tool_name: str | None = None
    tool_arg_checks: dict[str, Any] | None = None
    transaction_checks: list[dict[str, Any]] | None = None
    transaction_checks_ordered: bool = True
    text_contains_any_checks: list[str] | None = None


@dataclass(frozen=True)
class RecoveryCase:
    category: str
    name: str
    turns: list[TurnSpec]
    fixture_name: str = DEFAULT_FIXTURE_NAME
    target_fixture_name: str | None = None
    description: str = ""


PHASE5_CASES: list[RecoveryCase] = [
    RecoveryCase(
        "report_failure",
        "preview_connected_block_reports_error",
        [
            TurnSpec(
                "Preview removing the throttle block. If it won't work, explain why.",
                ["propose_edit"],
                expected_executed_in_order=[
                    ExecutedToolSpec("propose_edit", {"ok": False, "error_count": 1}),
                ],
                text_contains_any_checks=["disconnect", "connected", "failed"],
            )
        ],
    ),
    RecoveryCase(
        "report_failure",
        "remove_referenced_variable_reports_error",
        [
            TurnSpec(
                "Remove the samp_rate variable.",
                ["apply_edit"],
                expected_executed_in_order=[
                    ExecutedToolSpec("apply_edit", {"ok": False, "error_count": 1}),
                ],
                transaction_checks=[
                    {"op_type": "remove_block", "instance_name": "samp_rate"},
                ],
                text_contains_any_checks=["referenced", "remove", "unable"],
            )
        ],
    ),
    RecoveryCase(
        "same_turn_recovery",
        "failed_remove_then_inspect_same_turn",
        [
            TurnSpec(
                "Use apply_edit to remove the throttle block. If that fails because it is connected, call get_grc_context for blocks_throttle2_0 so you can inspect its neighborhood.",
                ["apply_edit", "get_grc_context"],
                expected_executed_in_order=[
                    ExecutedToolSpec("apply_edit", {"ok": False}),
                    ExecutedToolSpec("get_grc_context", {"ok": True}),
                ],
                checked_tool_name="get_grc_context",
                tool_arg_checks={"node_id": "blocks_throttle2_0"},
            )
        ],
    ),
    RecoveryCase(
        "same_turn_recovery",
        "preview_then_apply_variable_fix_same_turn",
        [
            TurnSpec(
                "Use propose_edit to preview removing samp_rate. If the preview fails because the variable is still referenced, call apply_edit with one repair transaction that patches dependent parameters to 32000 and then removes samp_rate.",
                ["propose_edit", "apply_edit"],
                expected_executed_in_order=[
                    ExecutedToolSpec("propose_edit", {"ok": False}),
                    ExecutedToolSpec("apply_edit", {"ok": True}),
                ],
                transaction_checks=[
                    {
                        "op_type": "update_params",
                        "instance_name": "blocks_throttle2_0",
                        "params": {"samples_per_second": "32000"},
                    },
                    {
                        "op_type": "update_params",
                        "instance_name": "qtgui_time_sink_x_0",
                        "params": {"srate": "32000"},
                    },
                    {"op_type": "remove_block", "instance_name": "samp_rate"},
                ],
            )
        ],
    ),
    RecoveryCase(
        "same_turn_recovery",
        "retry_remove_variable_same_turn",
        [
            TurnSpec(
                "Use apply_edit to remove samp_rate. If that call fails because the variable is still referenced, call apply_edit again with one repair transaction that patches dependent parameters to 32000 and then removes samp_rate.",
                ["apply_edit", "apply_edit"],
                expected_executed_in_order=[
                    ExecutedToolSpec("apply_edit", {"ok": False}),
                    ExecutedToolSpec("apply_edit", {"ok": True}),
                ],
                transaction_checks=[
                    {
                        "op_type": "update_params",
                        "instance_name": "blocks_throttle2_0",
                        "params": {"samples_per_second": "32000"},
                    },
                    {
                        "op_type": "update_params",
                        "instance_name": "qtgui_time_sink_x_0",
                        "params": {"srate": "32000"},
                    },
                    {"op_type": "remove_block", "instance_name": "samp_rate"},
                ],
            )
        ],
    ),
    RecoveryCase(
        "cross_turn_recovery",
        "preview_reference_fail_then_apply_fix",
        [
            TurnSpec(
                "Preview removing samp_rate. If it won't work, explain why.",
                ["propose_edit"],
                expected_executed_in_order=[
                    ExecutedToolSpec("propose_edit", {"ok": False}),
                ],
            ),
            TurnSpec(
                "OK, patch dependent parameters to 32000 and then remove samp_rate in one repair transaction.",
                ["apply_edit"],
                expected_executed_in_order=[
                    ExecutedToolSpec("apply_edit", {"ok": True}),
                ],
                transaction_checks=[
                    {
                        "op_type": "update_params",
                        "instance_name": "blocks_throttle2_0",
                        "params": {"samples_per_second": "32000"},
                    },
                    {
                        "op_type": "update_params",
                        "instance_name": "qtgui_time_sink_x_0",
                        "params": {"srate": "32000"},
                    },
                    {"op_type": "remove_block", "instance_name": "samp_rate"},
                ],
            ),
        ],
    ),
    RecoveryCase(
        "cross_turn_recovery",
        "bad_edit_then_good_edit_follow_up",
        [
            TurnSpec(
                "Change foobar_variable to 100.",
                ["apply_edit"],
                expected_executed_in_order=[
                    ExecutedToolSpec("apply_edit", {"ok": False}),
                ],
            ),
            TurnSpec(
                "That variable does not exist. Change samp_rate to 48000 instead.",
                ["apply_edit"],
                expected_executed_in_order=[
                    ExecutedToolSpec("apply_edit", {"ok": True}),
                ],
                transaction_checks=[
                    {"op_type": "update_params", "instance_name": "samp_rate"},
                ],
            ),
        ],
    ),
    RecoveryCase(
        "cross_turn_recovery",
        "failed_remove_then_inspect_context",
        [
            TurnSpec(
                "Remove the throttle block.",
                ["apply_edit"],
                expected_executed_in_order=[
                    ExecutedToolSpec("apply_edit", {"ok": False}),
                ],
                transaction_checks=[
                    {"op_type": "remove_block", "instance_name": "blocks_throttle2_0"},
                ],
            ),
            TurnSpec(
                "Show me what's around the throttle so I can fix it.",
                ["get_grc_context"],
                expected_executed_in_order=[
                    ExecutedToolSpec("get_grc_context", {"ok": True}),
                ],
                checked_tool_name="get_grc_context",
                tool_arg_checks={"node_id": "blocks_throttle2_0"},
            ),
        ],
    ),
]


def _render_prompt(prompt: str, target_path: str, save_path: str) -> str:
    return prompt.format(target_path=target_path, save_path=save_path)


def _render_value_templates(value: Any, *, target_path: str, save_path: str) -> Any:
    if isinstance(value, str):
        return value.format(target_path=target_path, save_path=save_path)
    if isinstance(value, dict):
        return {
            key: _render_value_templates(
                nested_value, target_path=target_path, save_path=save_path
            )
            for key, nested_value in value.items()
        }
    if isinstance(value, list):
        return [
            _render_value_templates(item, target_path=target_path, save_path=save_path)
            for item in value
        ]
    return value


def _requested_tool_calls_since(
    history: list[dict[str, Any]], start_index: int
) -> list[dict[str, Any]]:
    return extract_requested_tool_calls(history[start_index:])


def _executed_tool_calls_since(
    history: list[dict[str, Any]], start_index: int
) -> list[dict[str, Any]]:
    return extract_executed_tool_calls(history[start_index:])


def _executed_tools_match(
    executed_tool_calls: list[dict[str, Any]],
    expected_executed_in_order: list[ExecutedToolSpec],
) -> bool:
    executed_index = 0
    for expected in expected_executed_in_order:
        while executed_index < len(executed_tool_calls):
            tool_call = executed_tool_calls[executed_index]
            executed_index += 1
            if tool_call["name"] != expected.name:
                continue
            if expected.result_checks is None or tool_call_matches_argument_checks(
                tool_call, expected.result_checks
            ):
                break
        else:
            return False
    return True


def _check_turn(
    turn_spec: TurnSpec,
    requested_tool_calls: list[dict[str, Any]],
    executed_tool_calls: list[dict[str, Any]],
    assistant_text: str,
    *,
    target_path: str,
    save_path: str,
) -> dict[str, Any]:
    requested_tool_names = [tc["name"] for tc in requested_tool_calls]
    executed_tool_names = [tc["name"] for tc in executed_tool_calls]
    routing_matched = tools_appear_in_expected_order(
        requested_tool_names, turn_spec.expected_tools_in_order
    )

    execution_matched = None
    if turn_spec.expected_executed_in_order:
        execution_matched = _executed_tools_match(
            executed_tool_calls, turn_spec.expected_executed_in_order
        )

    forbidden_tools_absent = not any(
        tool_name in turn_spec.forbidden_tools
        for tool_name in requested_tool_names + executed_tool_names
    )

    checked_tool_name = turn_spec.checked_tool_name
    if checked_tool_name is None and turn_spec.expected_tools_in_order:
        checked_tool_name = turn_spec.expected_tools_in_order[-1]

    relevant_calls = (
        [tc for tc in requested_tool_calls if tc["name"] == checked_tool_name]
        if checked_tool_name
        else []
    )

    tool_arg_matched = None
    if turn_spec.tool_arg_checks is not None:
        rendered = _render_value_templates(
            turn_spec.tool_arg_checks, target_path=target_path, save_path=save_path
        )
        tool_arg_matched = any(
            tool_call_matches_argument_checks(tc, rendered) for tc in relevant_calls
        )

    transaction_matched = None
    if turn_spec.transaction_checks is not None:
        rendered = _render_value_templates(
            turn_spec.transaction_checks, target_path=target_path, save_path=save_path
        )
        transaction_matched = any(
            tool_call_matches_transaction_checks(
                tc, rendered, ordered=turn_spec.transaction_checks_ordered
            )
            for tc in relevant_calls
        )

    arg_matched = None
    if (
        turn_spec.tool_arg_checks is not None
        or turn_spec.transaction_checks is not None
    ):
        arg_matched = (tool_arg_matched is not False) and (
            transaction_matched is not False
        )

    text_matched = None
    if turn_spec.text_contains_any_checks:
        text_matched = text_contains_any(
            assistant_text, turn_spec.text_contains_any_checks
        )

    passed = (
        routing_matched
        and execution_matched is not False
        and forbidden_tools_absent
        and arg_matched is not False
        and text_matched is not False
    )
    return {
        "tools_called": requested_tool_names,
        "executed_tools_called": executed_tool_names,
        "routing_matched": routing_matched,
        "execution_matched": execution_matched,
        "forbidden_tools_absent": forbidden_tools_absent,
        "tool_arg_matched": tool_arg_matched,
        "transaction_matched": transaction_matched,
        "arg_matched": arg_matched,
        "text_matched": text_matched,
        "passed": passed,
    }


def _run_case(
    client: Any,
    model: str,
    case: RecoveryCase,
) -> dict[str, Any]:
    with isolated_fixture_workspace(case.fixture_name, case.target_fixture_name) as (
        workspace,
        copied_fixtures,
    ):
        session = FlowgraphSession()
        session.load(copied_fixtures[case.fixture_name])
        agent = GrcAgent(session)
        save_path = str(workspace / "saved_copy.grc")
        target_path = ""
        if case.target_fixture_name:
            target_path = str(copied_fixtures[case.target_fixture_name])

        turn_results: list[dict[str, Any]] = []
        error_message: str | None = None
        ok = True

        for turn_index, turn_spec in enumerate(case.turns):
            prompt = _render_prompt(turn_spec.prompt, target_path, save_path)
            history_start = len(agent.history)
            started_at = time.perf_counter()
            try:
                result = run_bounded_llama_turn(agent, client, prompt, model=model)
                turn_error = None
            except LlamaServerError as exc:
                result = None
                turn_error = str(exc)
                ok = False
            elapsed = time.perf_counter() - started_at

            if result is None:
                turn_results.append(
                    {
                        "turn_index": turn_index,
                        "prompt": prompt,
                        "error": turn_error,
                        "passed": False,
                        "tools_called": [],
                        "executed_tools_called": [],
                        "routing_matched": False,
                        "execution_matched": None,
                        "forbidden_tools_absent": True,
                        "tool_arg_matched": None,
                        "transaction_matched": None,
                        "arg_matched": None,
                        "text_matched": None,
                        "elapsed_seconds": round(elapsed, 3),
                        "assistant_text": "",
                    }
                )
                break

            requested_tool_calls = _requested_tool_calls_since(agent.history, history_start)
            executed_tool_calls = _executed_tool_calls_since(agent.history, history_start)
            assistant_text = result.get("assistant_text", "")

            check = _check_turn(
                turn_spec,
                requested_tool_calls,
                executed_tool_calls,
                assistant_text,
                target_path=target_path,
                save_path=save_path,
            )

            turn_results.append(
                {
                    "turn_index": turn_index,
                    "prompt": prompt,
                    "ok": result.get("ok", False),
                    "error": turn_error,
                    "elapsed_seconds": round(elapsed, 3),
                    "assistant_text": assistant_text,
                    **check,
                }
            )

            if not result.get("ok"):
                ok = False
                break

    return {
        "turn_results": turn_results,
        "ok": ok,
        "error": error_message,
        "all_turns_passed": all(tr.get("passed", False) for tr in turn_results),
    }


def _run_eval(
    server_url: str,
    model: str,
    cases: list[RecoveryCase],
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
            n_turns = len(case.turns)
            n_passed = sum(
                1 for tr in run_result["turn_results"] if tr.get("passed", False)
            )
            status = "PASS" if run_result["all_turns_passed"] else "FAIL"
            print(f" -> {status} ({n_passed}/{n_turns} turns)")
            runs.append(run_result)

        pass_count = sum(1 for run in runs if run["all_turns_passed"])
        passed = pass_count > n_runs * MAJORITY_THRESHOLD

        per_turn_pass_counts: list[int] = [0] * len(case.turns)
        for run in runs:
            for tr in run.get("turn_results", []):
                idx = tr.get("turn_index", 0)
                if idx < len(per_turn_pass_counts) and tr.get("passed", False):
                    per_turn_pass_counts[idx] += 1

        results.append(
            {
                "category": case.category,
                "name": case.name,
                "n_turns": len(case.turns),
                "runs": runs,
                "pass_count": pass_count,
                "pass_rate": pass_count / n_runs,
                "passed": passed,
                "per_turn_pass_counts": per_turn_pass_counts,
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
        "phase": 5,
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
        description="Phase 5 model eval: deeper failure handling and recovery."
    )
    parser.add_argument(
        "--server-url",
        default=os.environ.get("GRC_AGENT_LIVE_LLAMA_URL"),
        help="llama.cpp server URL. Defaults to config.",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("GRC_AGENT_LIVE_LLAMA_MODEL"),
        help="llama.cpp model alias. Defaults to config.",
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

    cases = list(PHASE5_CASES)
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
