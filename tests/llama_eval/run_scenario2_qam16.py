#!/usr/bin/env python3
"""Run Scenario 2: 16-QAM Link Upgrade.

Usage:
    uv run python -m tests.llama_eval.run_scenario2_qam16
"""
from __future__ import annotations

import json
import sys

from tests.llama_eval.dsp_scenarios import generate_qam_scenarios
from tests.llama_eval.harness import (
    build_phase_parser,
    run_live_scenario_once,
    run_phase_eval,
    dimension_pass_counts,
    majority_passed,
)


def _run_case(client, model, case):
    return run_live_scenario_once(
        client=client,
        model=model,
        scenario=case,
        mvp_tool_profile=True,
    )


def _build_report(case, runs, n_runs, threshold):
    mc = sum(1 for r in runs if r["matched"])
    dims = dimension_pass_counts([{"runs": runs}]) if runs else {}
    return {
        "name": case.name,
        "fixture": case.fixture_name,
        "prompt": case.turns[0].prompt,
        "runs": runs,
        "pass_count": mc,
        "passed": majority_passed(mc, n_runs, threshold),
        "dimension_pass_counts": dims,
    }


def _build_summary(results, total_cases):
    total_passed = sum(1 for r in results if r["passed"])
    dims = dimension_pass_counts(results)
    return {
        "total": total_cases,
        "passed": total_passed,
        "pass_rate": round(total_passed / total_cases, 4) if total_cases else 0.0,
        "dimension_pass_counts": dims,
    }


def _render_status(case, run):
    return (
        f"{'PASS' if run.get('matched') else 'FAIL'} "
        f"({', '.join(run.get('tools_called', [])) or 'no tools'})"
    )


def main() -> int:
    parser = build_phase_parser(
        "Scenario 2: 16-QAM Link Upgrade (single targeted run)",
        default_n_runs=1,
        server_help="llama.cpp server URL.",
        model_help="Model alias.",
    )
    args = parser.parse_args()

    # Use seed from base parser; default to 2 which reliably gives qam_order16
    seed = args.seed if args.seed is not None else 2

    # Generate exactly one 16-QAM scenario
    cases = [fz.scenario for fz in generate_qam_scenarios(seed=seed, count=1)]
    print(f"Scenario 2: {len(cases)} case(s) — {[c.name for c in cases]}")
    print(f"Prompt: {cases[0].turns[0].prompt}\n")

    report = run_phase_eval(
        phase=62,
        server_url=args.server_url,
        model=args.model,
        cases=cases,
        n_runs=1,
        majority_threshold=0.5,
        run_case=_run_case,
        build_case_report=_build_report,
        render_status=_render_status,
        build_summary=_build_summary,
        retry_on_timeout=True,
        results_path=args.results_path,
        resume=args.resume,
        rerun_failed=args.rerun_failed,
        max_tokens=args.max_tokens,
        stability_threshold=args.stability_threshold,
        mvp_tool_profile=True,
    )

    print("\n" + json.dumps(report, indent=2, sort_keys=False))
    return 0 if report.get("summary", {}).get("pass_rate", 0) >= 0.5 else 1


if __name__ == "__main__":
    sys.exit(main())
