#!/usr/bin/env python3
"""Run native MVP R5 save/load lifecycle eval."""

from __future__ import annotations

import json
import sys
from typing import Any

from tests.llama_eval.harness import (
    LiveScenario,
    MVP_RELEASE_MODEL_TOOLS,
    build_phase_parser,
    dimension_pass_counts,
    majority_passed,
    run_live_scenario_once,
    run_phase_eval,
    scenario_expected_tools_only,
    select_cases,
)
from tests.llama_eval.r5_save_load import R5_CASES

DEFAULT_N_RUNS = 1
MAJORITY_THRESHOLD = 0.5


def release_cases() -> list[LiveScenario]:
    for scenario in R5_CASES:
        if not scenario_expected_tools_only(
            scenario,
            allowed_tool_names=MVP_RELEASE_MODEL_TOOLS,
        ):
            raise RuntimeError(
                f"R5 save/load case contains non-MVP expected tools: {scenario.name}"
            )
    return list(R5_CASES)


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
        f"recovery={run.get('recovery_pass')}"
    )
    return (
        f"{'PASS' if run.get('matched') else 'FAIL'} "
        f"({', '.join(run.get('tools_called', [])) or 'no tools'}; {dimensions})"
    )


def _build_report(case: LiveScenario, runs: list, n_runs: int, threshold: float) -> dict:
    pass_count = sum(1 for run in runs if run["matched"])
    return {
        "category": case.category,
        "name": case.name,
        "runs": runs,
        "pass_count": pass_count,
        "passed": majority_passed(pass_count, n_runs, threshold),
        "dimension_pass_counts": dimension_pass_counts([{"runs": runs}]),
        "release_profile": case.release_profile,
    }


def main() -> int:
    parser = build_phase_parser(
        "Native MVP R5 save/load lifecycle eval.",
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
    report = run_phase_eval(
        phase=55,
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
    print("\n" + json.dumps(report, indent=2, sort_keys=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
