#!/usr/bin/env python3
"""Run native MVP R7 external-example validation suites."""

from __future__ import annotations

import json
import sys
from typing import Any

from tests.llama_eval.harness import (
    LiveScenario,
    MVP_RELEASE_MODEL_TOOLS,
    build_phase_parser,
    default_phase_summary,
    dimension_pass_counts,
    majority_passed,
    run_live_scenario_once,
    run_phase_eval,
    scenario_expected_tools_only,
    select_cases,
)
from tests.llama_eval.r7_external_examples import exact_cases, natural_cases, release_cases

DEFAULT_N_RUNS = 1
MAJORITY_THRESHOLD = 0.5
TRACKS = {"exact", "natural"}
PHASE_BY_TRACK = {"exact": 71, "natural": 72}


def _cases(track: str) -> list[LiveScenario]:
    if track == "exact":
        scenarios = exact_cases()
    elif track == "natural":
        scenarios = natural_cases()
    else:
        scenarios = release_cases()
    for scenario in scenarios:
        if not scenario_expected_tools_only(
            scenario,
            allowed_tool_names=MVP_RELEASE_MODEL_TOOLS,
        ):
            raise RuntimeError(
                f"R7 {track} case contains non-MVP expected tools: {scenario.name}"
            )
    return scenarios


def _run_case(client: Any, model: str, case: LiveScenario) -> dict[str, Any]:
    return run_live_scenario_once(
        client=client,
        model=model,
        scenario=case,
        mvp_tool_profile=True,
    )


def _render_status(case: LiveScenario, run: dict[str, Any]) -> str:
    dimensions = (
        f"routing={run.get('routing_pass')}, "
        f"argument={run.get('argument_pass')}, "
        f"tool_success={run.get('tool_success_pass')}, "
        f"semantic={run.get('semantic_pass')}, "
        f"model_contract={run.get('model_contract_pass')}, "
        f"runtime_safety={run.get('runtime_safety_pass')}, "
        f"end_state={run.get('end_state_pass')}, "
        f"recovery={run.get('recovery_pass')}"
    )
    return (
        f"{'PASS' if run.get('matched') else 'FAIL'} "
        f"({', '.join(run.get('tools_called', [])) or 'no tools'}; {dimensions})"
    )


def _tool_names(calls: Any) -> list[str]:
    if not isinstance(calls, list):
        return []
    return [str(call.get("name")) for call in calls if isinstance(call, dict) and call.get("name")]


def _payload(call: dict[str, Any]) -> dict[str, Any]:
    args = call.get("arguments") if isinstance(call, dict) else None
    return args if isinstance(args, dict) else {}


def _nonempty_delta(delta: Any) -> bool:
    if not isinstance(delta, dict):
        return False
    return any(bool(value) for value in delta.values())


def _expected_tool_names(case: LiveScenario) -> set[str]:
    return {
        expectation.name
        for turn in case.turns
        for expectation in turn.expected_tool_calls
    }


def _run_diagnostic_counts(case: LiveScenario, run: dict[str, Any]) -> dict[str, int]:
    counts = {
        "no_call": 0,
        "wrong_wrapper": 0,
        "missing_arg": 0,
        "safe_clarification": 0,
        "runtime_safety_pass": 0,
        "task_success_pass": 0,
        "raw_legacy_attempts": 0,
        "failed_validation_commits": 0,
    }
    if run.get("runtime_safety_pass") is True:
        counts["runtime_safety_pass"] = 1
    if run.get("matched") is True:
        counts["task_success_pass"] = 1

    expected_names = _expected_tool_names(case)
    requested_names = _tool_names(run.get("requested_tool_calls"))
    if expected_names and not requested_names:
        counts["no_call"] = 1
    elif expected_names and requested_names and not any(name in expected_names for name in requested_names):
        counts["wrong_wrapper"] = 1
    if run.get("routing_pass") is True and run.get("argument_pass") is False:
        counts["missing_arg"] = 1

    for turn in run.get("turn_results", []):
        if not isinstance(turn, dict):
            continue
        turn_requested = _tool_names(turn.get("requested_tool_calls"))
        assistant_text = str(turn.get("assistant_text") or "").strip()
        if expected_names and not turn_requested and assistant_text and turn.get("runtime_safety_pass") is True:
            counts["safe_clarification"] += 1
        raw_names = set(_tool_names(turn.get("requested_tool_calls_raw")))
        raw_names.update(_tool_names(turn.get("executed_tool_calls_raw")))
        counts["raw_legacy_attempts"] += len(raw_names - MVP_RELEASE_MODEL_TOOLS)
        for call in turn.get("executed_tool_calls", []):
            payload = _payload(call)
            if payload.get("error_type") != "gnu_validation_failed":
                continue
            trace = turn.get("trace") if isinstance(turn.get("trace"), dict) else {}
            if _nonempty_delta(trace.get("graph_delta")):
                counts["failed_validation_commits"] += 1
    return counts


def _sum_counts(items: list[dict[str, int]]) -> dict[str, int]:
    totals: dict[str, int] = {}
    for item in items:
        for key, value in item.items():
            totals[key] = totals.get(key, 0) + int(value)
    return totals


def _build_report(
    case: LiveScenario,
    runs: list[dict[str, Any]],
    n_runs: int,
    threshold: float,
) -> dict[str, Any]:
    pass_count = sum(1 for run in runs if run.get("matched") is True)
    diagnostic_counts = _sum_counts([_run_diagnostic_counts(case, run) for run in runs])
    return {
        "category": case.category,
        "name": case.name,
        "description": case.description,
        "source_fixture": case.fixture_name,
        "target_fixture": case.target_fixture_name,
        "runs": runs,
        "pass_count": pass_count,
        "passed": majority_passed(pass_count, n_runs, threshold),
        "dimension_pass_counts": dimension_pass_counts([{"runs": runs}]),
        "diagnostic_counts": diagnostic_counts,
        "release_profile": case.release_profile,
    }


def _build_summary(results: list[dict[str, Any]], total_cases: int) -> dict[str, Any]:
    summary = default_phase_summary(results, total_cases)
    summary["diagnostic_counts"] = _sum_counts(
        [result.get("diagnostic_counts", {}) for result in results]
    )
    return summary


def main() -> int:
    parser = build_phase_parser(
        "Native MVP R7 external-example validation suite.",
        default_n_runs=DEFAULT_N_RUNS,
        server_help="Server URL.",
        model_help="Model alias.",
    )
    parser.add_argument(
        "--track",
        choices=sorted(TRACKS),
        default="exact",
        help="R7 diagnostic track to run. Default: exact.",
    )
    args = parser.parse_args()
    n_runs = 1 if args.quick else args.n_runs
    cases = select_cases(_cases(args.track), category=args.category, case_name=args.case)
    if not cases:
        print("No matching cases.", file=sys.stderr)
        return 1
    report = run_phase_eval(
        phase=PHASE_BY_TRACK[args.track],
        server_url=args.server_url,
        model=args.model,
        cases=cases,
        n_runs=n_runs,
        majority_threshold=MAJORITY_THRESHOLD,
        run_case=_run_case,
        build_case_report=_build_report,
        build_summary=_build_summary,
        render_status=_render_status,
        retry_on_timeout=True,
        results_path=args.results_path,
        resume=args.resume,
        rerun_failed=args.rerun_failed,
        max_tokens=args.max_tokens,
        stability_threshold=args.stability_threshold,
        mvp_tool_profile=True,
    )
    print("\n" + json.dumps(report, indent=2, sort_keys=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
