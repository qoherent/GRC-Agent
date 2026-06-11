#!/usr/bin/env python3
"""Run the DSP Fuzzing Gauntlet — 50 parameterized scenarios.

Each scenario uses an isolated ``random.Random(seed)`` to fuzz sample rates,
center frequencies, modulation orders, and subcarrier counts. The gauntlet
reports per-scenario pass/fail across all 11 dimensions plus aggregate
budget and lint metrics.

Usage:
    uv run python -m tests.llama_eval.run_dsp_gauntlet --quick
    uv run python -m tests.llama_eval.run_dsp_gauntlet --n-runs 1 --seed 42 --output-dir /tmp/gauntlet
"""

from __future__ import annotations

import json
import sys
from typing import Any

from tests.llama_eval.dsp_scenarios import (
    GENERATOR_REGISTRY,
    fuzzed_to_live,
)
from tests.llama_eval.harness import (
    LiveScenario,
    build_phase_parser,
    dimension_pass_counts,
    majority_passed,
    run_live_scenario_once,
    run_phase_eval,
)

DEFAULT_N_RUNS = 1
MAJORITY_THRESHOLD = 0.5


def _run_case(client: Any, model: str, case: LiveScenario) -> dict[str, Any]:
    return run_live_scenario_once(
        client=client,
        model=model,
        scenario=case,
        mvp_tool_profile=True,
    )


def _render_status(case: LiveScenario, run: dict) -> str:
    budget_msg = f"budget={run.get('budget_pass')}" if run.get("budget_pass") is not None else ""
    lint_msg = f"lint={run.get('lint_pass')}" if run.get("lint_pass") is not None else ""
    extras = "; ".join(filter(None, [budget_msg, lint_msg]))
    return (
        f"{'PASS' if run.get('matched') else 'FAIL'} "
        f"({', '.join(run.get('tools_called', [])) or 'no tools'}"
        f"{'; ' + extras if extras else ''})"
    )


def _build_report(case: LiveScenario, runs: list, n_runs: int, threshold: float) -> dict:
    mc = sum(1 for r in runs if r["matched"])
    dims = dimension_pass_counts([{"runs": runs}]) if runs else {}
    return {
        "category": case.category,
        "name": case.name,
        "fixture": case.fixture_name,
        "release_profile": case.release_profile,
        "fuzzed_variables": getattr(case, "fuzzed_variables", {}),
        "runs": runs,
        "pass_count": mc,
        "passed": majority_passed(mc, n_runs, threshold),
        "dimension_pass_counts": dims,
        "description": case.description,
    }


def _build_summary(results: list[dict], total_cases: int) -> dict:
    total_passed = sum(1 for r in results if r["passed"])
    dims = dimension_pass_counts(results)
    # Aggregate budget and lint details across all runs
    budget_total = dims.get("budget_pass", {}).get("total", 0)
    budget_passed = dims.get("budget_pass", {}).get("passed", 0) if budget_total else "N/A"
    lint_total = dims.get("lint_pass", {}).get("total", 0)
    lint_passed = dims.get("lint_pass", {}).get("passed", 0) if lint_total else "N/A"

    return {
        "total": total_cases,
        "passed": total_passed,
        "pass_rate": round(total_passed / total_cases, 4) if total_cases else 0.0,
        "dimension_pass_counts": dims,
        "budget_aggregate": f"{budget_passed}/{budget_total}" if budget_total else "N/A",
        "lint_aggregate": f"{lint_passed}/{lint_total}" if lint_total else "N/A",
    }


def build_gauntlet_cases(seed: int = 0, count: int = 50) -> list[LiveScenario]:
    """Generate exactly ``count`` scenarios across all DSP categories."""
    cases: list[LiveScenario] = []
    # Distribute seeds round-robin across categories for diversity
    per_cat = max(1, count // max(len(GENERATOR_REGISTRY), 1))
    for cat_name, generator in GENERATOR_REGISTRY.items():
        cat_cases = generator(seed=seed, count=per_cat)
        cases.extend(fuzzed_to_live(fz) for fz in cat_cases)
    # Shuffle so seeds interleave rather than block-per-category
    import random as rnd
    rnd.Random(seed).shuffle(cases)
    # Trim or pad to exact count
    if len(cases) > count:
        cases = cases[:count]
    return cases


def main() -> int:
    parser = build_phase_parser(
        "DSP Fuzzing Gauntlet: 50 parameterized scenarios.",
        default_n_runs=DEFAULT_N_RUNS,
        server_help="Server URL.",
        model_help="Model alias.",
    )
    parser.add_argument(
        "--count", type=int, default=50,
        help="Total number of fuzzed scenarios to run.",
    )
    args = parser.parse_args()

    n_runs = 1 if args.quick else args.n_runs
    cases = build_gauntlet_cases(seed=args.seed, count=args.count)
    if args.case:
        cases = [c for c in cases if c.name == args.case]
    print(f"Gauntlet: {len(cases)} scenarios (base_seed={args.seed}, n_runs={n_runs})")

    report = run_phase_eval(
        phase=60,
        server_url=args.server_url,
        model=args.model,
        cases=cases,
        n_runs=n_runs,
        majority_threshold=MAJORITY_THRESHOLD,
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
