#!/usr/bin/env python3
"""Run R2 Chaos Monkey eval: broken states, multi-turn, robustness.

Run:
    uv run python -m tests.llama_eval.run_r2_release --quick
    uv run python -m tests.llama_eval.run_r2_release --n-runs 3 --results-path /tmp/r2.json
"""

from __future__ import annotations

import json
import sys
from typing import Any

from tests.llama_eval.harness import (
    MVP_RELEASE_MODEL_TOOLS,
    LiveScenario,
    build_phase_parser,
    dimension_pass_counts,
    majority_passed,
    run_live_scenario_once,
    run_phase_eval,
    scenario_expected_tools_only,
    select_cases,
)
from tests.llama_eval.r2_release import R2_CASES

DEFAULT_N_RUNS = 1
MAJORITY_THRESHOLD = 0.5

# Global force-telemetry accumulator
_force_attempts: int = 0
_force_commits: int = 0
_total_change_graph_calls: int = 0


def _reset_force_telemetry() -> None:
    global _force_attempts, _force_commits, _total_change_graph_calls
    _force_attempts = 0
    _force_commits = 0
    _total_change_graph_calls = 0


def _collect_force_telemetry(run: dict[str, Any]) -> None:
    global _force_attempts, _force_commits, _total_change_graph_calls
    was_force = False
    for call in run.get("requested_tool_calls", []):
        if call.get("name") != "change_graph":
            continue
        _total_change_graph_calls += 1
        args = call.get("arguments") or {}
        if isinstance(args, dict) and args.get("force") is True:
            _force_attempts += 1
            was_force = True
    if was_force:
        for call in run.get("executed_tool_calls", []):
            if call.get("name") != "change_graph":
                continue
            result = call.get("arguments") or {}
            if isinstance(result, dict) and result.get("committed") is True:
                _force_commits += 1


def _force_crutch_rate() -> float:
    global _force_attempts, _total_change_graph_calls
    if _total_change_graph_calls == 0:
        return 0.0
    return _force_attempts / _total_change_graph_calls


def release_cases() -> list[LiveScenario]:
    for scenario in R2_CASES:
        if not scenario_expected_tools_only(
            scenario,
            allowed_tool_names=MVP_RELEASE_MODEL_TOOLS,
        ):
            raise RuntimeError(
                f"R2 release case contains non-MVP expected tools: {scenario.name}"
            )
    return list(R2_CASES)


def _run_case(client: Any, model: str, case: LiveScenario) -> dict[str, Any]:
    return run_live_scenario_once(
        client=client,
        model=model,
        scenario=case,
        mvp_tool_profile=True,
    )


def _render_status(case: LiveScenario, run: dict) -> str:
    dimensions = (
        f"routing={run.get('routing_pass')}, "
        f"argument={run.get('argument_pass')}, "
        f"tool_success={run.get('tool_success_pass')}, "
        f"semantic={run.get('semantic_pass')}, "
        f"model_contract={run.get('model_contract_pass')}, "
        f"runtime_safety={run.get('runtime_safety_pass')}, "
        f"end_state={run.get('end_state_pass')}, "
        f"recovery={run.get('recovery_pass')}, "
        f"budget={run.get('budget_pass')}, "
        f"lint={run.get('lint_pass')}"
    )
    return (
        f"{'PASS' if run.get('matched') else 'FAIL'} "
        f"({', '.join(run.get('tools_called', [])) or 'no tools'}; {dimensions})"
    )


def _build_report(case: LiveScenario, runs: list, n_runs: int, threshold: float) -> dict:
    mc = sum(1 for r in runs if r.get("matched"))
    return {
        "category": case.category,
        "name": case.name,
        "runs": runs,
        "pass_count": mc,
        "passed": majority_passed(mc, n_runs, threshold),
        "dimension_pass_counts": dimension_pass_counts([{"runs": runs}]),
        "release_profile": case.release_profile,
    }


def main() -> int:
    parser = build_phase_parser(
        "R2 Chaos Monkey eval: broken states and multi-turn corrections.",
        default_n_runs=DEFAULT_N_RUNS,
        server_help="Server URL.",
        model_help="Model alias.",
    )
    args = parser.parse_args()
    n_runs = 1 if args.quick else args.n_runs
    cases = select_cases(release_cases(), category=args.category, case_name=args.case)
    if not cases:
        print("No matching cases.", file=sys.stderr)
        return 1

    _reset_force_telemetry()

    report = run_phase_eval(
        phase=40,
        server_url=args.server_url,
        model=args.model,
        cases=cases,
        n_runs=n_runs,
        majority_threshold=MAJORITY_THRESHOLD,
        run_case=_run_case,
        build_case_report=_build_report,
        render_status=_render_status,
        retry_on_timeout=True,
        results_path=args.results_path,
        resume=args.resume,
        rerun_failed=args.rerun_failed,
        max_tokens=args.max_tokens,
        stability_threshold=args.stability_threshold,
        mvp_tool_profile=True,
    )

    # Collect force telemetry from all runs
    for case_report in report.get("cases", []):
        for run in case_report.get("runs", []):
            for turn_result in (run.get("turn_results") or []):
                _collect_force_telemetry(turn_result)

    force_metrics = {
        "total_change_graph_calls": _total_change_graph_calls,
        "force_attempts": _force_attempts,
        "force_commits": _force_commits,
        "force_crutch_rate": round(_force_crutch_rate(), 4),
        "force_crutch_warning": (
            "ARCHITECTURAL INTERVENTION RECOMMENDED"
            if _force_crutch_rate() > 0.10
            else "OK"
        ),
    }
    report["force_telemetry"] = force_metrics

    print("\n" + json.dumps(report, indent=2, sort_keys=False))

    # Print force telemetry prominently
    print(f"\n{'=' * 60}")
    print("FORCE CRUTCH TELEMETRY")
    print(f"{'=' * 60}")
    print(f"  Total change_graph calls:  {_total_change_graph_calls}")
    print(f"  Force=true attempts:      {_force_attempts}")
    print(f"  Force=true commits:       {_force_commits}")
    print(f"  Force-Crutch-Rate:        {_force_crutch_rate():.1%}")
    print(f"  Verdict:                  {force_metrics['force_crutch_warning']}")
    print(f"{'=' * 60}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
