#!/usr/bin/env python3
"""Run all eval phases (1–6) in sequence after ensuring llama.cpp is available."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import time

from tests.llama_eval import (
    harness,
    run_phase1,
    run_phase2,
    run_phase3,
    run_phase4,
    run_phase5,
    run_phase6,
)

_ALL_PHASES = {1, 2, 3, 4, 5, 6}
_DEFAULT_RESULTS_FILE = ".llama_eval/run_all_results.json"


def _parse_phases(value: str) -> set[int]:
    """Argparse type: parse comma-separated phase numbers and validate range."""
    try:
        phases = {int(p.strip()) for p in value.split(",")}
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"Invalid phase list {value!r}: expected comma-separated integers, e.g. '1,3,5'."
        )
    unknown = phases - _ALL_PHASES
    if unknown:
        raise argparse.ArgumentTypeError(
            f"Unknown phase numbers: {sorted(unknown)}. Valid phases are 1–6."
        )
    return phases

_PHASES = [
    (1, run_phase1.PHASE1_CASES, run_phase1._run_eval),
    (2, run_phase2.PHASE2_CASES, run_phase2._run_eval),
    (3, run_phase3.PHASE3_CASES, run_phase3._run_eval),
    (4, run_phase4.PHASE4_CASES, run_phase4._run_eval),
    (5, run_phase5.PHASE5_CASES, run_phase5._run_eval),
    (6, run_phase6.PHASE6_CASES, run_phase6._run_eval),
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run all 6 eval phases in sequence.")
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
        default=3,
        help="Number of runs per case. Default: 3.",
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Force n_runs=1 for a faster check.",
    )
    parser.add_argument(
        "--phases",
        type=_parse_phases,
        default=_ALL_PHASES,
        metavar="N[,N...]",
        help="Comma-separated phase numbers to run (e.g. '1,3,5'). Default: all.",
    )
    parser.add_argument(
        "--category",
        type=str,
        default=None,
        help="Run only cases in this category (across all selected phases).",
    )
    parser.add_argument(
        "--case",
        type=str,
        default=None,
        help="Run only the case with this name (across all selected phases).",
    )
    parser.add_argument(
        "--results-file",
        default=_DEFAULT_RESULTS_FILE,
        help=(
            "Path to the persisted run-results file used by --resume. "
            f"Default: {_DEFAULT_RESULTS_FILE}."
        ),
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from the persisted results file and skip completed runs.",
    )
    parser.add_argument(
        "--rerun-failed",
        action="store_true",
        help="With --resume, rerun only prior FAIL or INFRA_FAIL entries.",
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Delete any prior persisted results before starting.",
    )
    args = parser.parse_args()

    if args.rerun_failed and not args.resume:
        parser.error("--rerun-failed requires --resume")

    n_runs = 1 if args.quick else args.n_runs
    results_path = Path(args.results_file)

    if args.fresh and results_path.exists():
        results_path.unlink()

    if not args.resume:
        harness.write_run_store(
            results_path,
            {
                "version": 1,
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "runs": [],
            },
        )

    summaries: list[dict] = []
    overall_start = time.perf_counter()

    for phase_num, all_cases, run_eval in _PHASES:
        if phase_num not in args.phases:
            continue

        cases = list(all_cases)
        if args.category:
            cases = [c for c in cases if c.category == args.category]
        if args.case:
            cases = [c for c in cases if c.name == args.case]
        if not cases:
            continue

        sep = "=" * 60
        print(f"\n{sep}")
        print(f"Phase {phase_num}  ({len(cases)} cases, n_runs={n_runs})")
        print(sep)

        phase_start = time.perf_counter()
        report = run_eval(
            args.server_url,
            args.model,
            cases,
            n_runs,
            results_path=results_path,
            resume=args.resume,
            rerun_failed=args.rerun_failed,
        )
        phase_elapsed = time.perf_counter() - phase_start

        summary = report.get("summary", {})
        total = summary.get("total", len(cases))
        passed = summary.get("passed", 0)
        model_attempts = summary.get("model_attempts", 0)
        model_passes = summary.get("model_passes", 0)
        infra_failures = summary.get("infra_failures", 0)
        scheduled_runs = summary.get("total_scheduled_runs", len(cases) * n_runs)
        completeness = "complete" if infra_failures == 0 else "incomplete"
        print(
            f"\nPhase {phase_num}: {model_passes}/{model_attempts} model attempts, "
            f"{infra_failures} infra failures, {scheduled_runs} scheduled  "
            f"({phase_elapsed:.1f}s, {completeness})"
        )

        summaries.append(
            {
                "phase": phase_num,
                "total": total,
                "passed": passed,
                "failed": total - passed,
                "model_attempts": model_attempts,
                "model_passes": model_passes,
                "infra_failures": infra_failures,
                "scheduled_runs": scheduled_runs,
                "complete": infra_failures == 0,
                "elapsed": round(phase_elapsed, 1),
            }
        )

    if not summaries:
        print(
            "error: no cases matched the selected filters — nothing was run.",
            file=sys.stderr,
        )
        return 1

    overall_elapsed = time.perf_counter() - overall_start

    print(f"\n{'=' * 60}")
    print("OVERALL SUMMARY")
    print(f"{'=' * 60}")
    print(
        f"{'Phase':<8} {'Model':<14} {'Infra':<8} {'Sched':<8} {'State':<10} {'Time'}"
    )
    print("-" * 68)
    total_total = 0
    total_passed = 0
    total_model_attempts = 0
    total_model_passes = 0
    total_infra_failures = 0
    total_scheduled_runs = 0
    for s in summaries:
        total_total += s["total"]
        total_passed += s["passed"]
        total_model_attempts += s["model_attempts"]
        total_model_passes += s["model_passes"]
        total_infra_failures += s["infra_failures"]
        total_scheduled_runs += s["scheduled_runs"]
        model_ratio = f"{s['model_passes']}/{s['model_attempts']}"
        state_text = "complete" if s["complete"] else "incomplete"
        print(
            f"{s['phase']:<8} "
            f"{model_ratio:<14} "
            f"{s['infra_failures']:<8} "
            f"{s['scheduled_runs']:<8} "
            f"{state_text:<10} "
            f"{s['elapsed']:.1f}s"
        )
    print("-" * 68)
    overall_ratio = f"{total_model_passes}/{total_model_attempts}"
    overall_state = "complete" if total_infra_failures == 0 else "incomplete"
    print(
        f"{'ALL':<8} "
        f"{overall_ratio:<14} "
        f"{total_infra_failures:<8} "
        f"{total_scheduled_runs:<8} "
        f"{overall_state:<10} "
        f"{overall_elapsed:.1f}s"
    )

    return 0 if total_passed == total_total and total_infra_failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
