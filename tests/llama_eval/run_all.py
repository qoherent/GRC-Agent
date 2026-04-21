#!/usr/bin/env python3
"""Run all eval phases (1–6) in sequence after ensuring llama.cpp is available."""

from __future__ import annotations

import argparse
import os
import sys
import time

from tests.llama_eval import (
    run_phase1,
    run_phase2,
    run_phase3,
    run_phase4,
    run_phase5,
    run_phase6,
)
from tests.llama_eval.harness import ensure_llama_server

_ALL_PHASES = {1, 2, 3, 4, 5, 6}


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
    args = parser.parse_args()

    n_runs = 1 if args.quick else args.n_runs

    url, model, _ = ensure_llama_server(args.server_url, args.model)

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
        report = run_eval(url, model, cases, n_runs)
        phase_elapsed = time.perf_counter() - phase_start

        summary = report.get("summary", {})
        total = summary.get("total", len(cases))
        passed = summary.get("passed", 0)
        print(f"\nPhase {phase_num}: {passed}/{total} passed  ({phase_elapsed:.1f}s)")

        summaries.append(
            {
                "phase": phase_num,
                "total": total,
                "passed": passed,
                "failed": total - passed,
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
    print(f"{'Phase':<8} {'Cases':<8} {'Pass':<8} {'Fail':<8} {'Time'}")
    print("-" * 44)
    total_total = 0
    total_passed = 0
    for s in summaries:
        total_total += s["total"]
        total_passed += s["passed"]
        print(
            f"{s['phase']:<8} {s['total']:<8} {s['passed']:<8} {s['failed']:<8} {s['elapsed']:.1f}s"
        )
    print("-" * 44)
    print(
        f"{'ALL':<8} {total_total:<8} {total_passed:<8} {total_total - total_passed:<8} {overall_elapsed:.1f}s"
    )

    return 0 if total_passed == total_total else 1


if __name__ == "__main__":
    sys.exit(main())
