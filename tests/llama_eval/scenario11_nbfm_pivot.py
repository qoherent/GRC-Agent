#!/usr/bin/env python3
"""Scenario 11: NBFM Pivot — Convert dial tone to NBFM transmitter baseband.

Usage:
    uv run python -m tests.llama_eval.scenario11_nbfm_pivot
"""
from __future__ import annotations

import json
import random
import sys

from tests.llama_eval.harness import (
    build_phase_parser,
    run_live_scenario_once,
    run_phase_eval,
    dimension_pass_counts,
    majority_passed,
)
from tests.llama_eval.dsp_scenarios import (
    LiveScenario,
    LiveTurnSpec,
    FuzzedScenario,
)

# Budget
_NBFM_BUDGET = {
    "max_tool_rounds": 10,
    "max_tool_calls": 20,
    "max_prompt_tokens": 25000,
}

# Fuzz pools
ZMQ_ADDRESSES = (
    "tcp://127.0.0.1:5555",
    "tcp://0.0.0.0:6666",
    "tcp://10.0.0.1:7777",
)

QUAD_MULTIPLIERS = (4, 8, 10)


def generate_nbfm_scenarios(
    seed: int = 0,
    count: int = 3,
) -> list[FuzzedScenario]:
    rng = random.Random(seed)
    results: list[FuzzedScenario] = []

    for i in range(count):
        case_seed = rng.randint(0, 2**31 - 1)
        zmq_address = rng.choice(ZMQ_ADDRESSES)
        quad_multiplier = rng.choice(QUAD_MULTIPLIERS)

        prompt = (
            "We are converting this local dial tone example into a Narrowband FM "
            "(NBFM) transmitter baseband. First, remove the Audio Sink completely. "
            "Next, insert an NBFM Transmit block to modulate the combined audio signal. "
            "Set its audio rate parameter to the graph's `samp_rate`, and set its "
            f"quadrature rate to the exact expression `samp_rate * {quad_multiplier}`. "
            "Finally, the NBFM transmitter outputs complex IQ data; stream this output "
            "over the network by connecting it to a new ZeroMQ PUB Sink block listening "
            f"on address '{zmq_address}'."
        )

        scenario = LiveScenario(
            category="dsp",
            name=f"nbfm_pivot_z{zmq_address[-4:]}_q{quad_multiplier}",
            fixture_name="dial_tone.grc",
            description=(
                f"NBFM pivot: zmq={zmq_address}, quad_mult={quad_multiplier}"
            ),
            release_profile="R4B_REMOVE_BLOCK",
            fuzzed_variables={},
            param_seed=case_seed,
            turns=(
                LiveTurnSpec(
                    prompt=prompt,
                    accept_any_tool=True,
                    semantic_checks=(
                        {"kind": "mutation"},
                        {"kind": "saved_block_absent",
                         "instance_name": "audio_sink",
                         "path": "{after_path}"},
                        {"kind": "saved_block_present",
                         "instance_name": "nbfm_tx",
                         "path": "{after_path}"},
                        {"kind": "saved_block_present",
                         "instance_name": "zmq_pub_sink",
                         "path": "{after_path}"},
                        {"kind": "saved_path_valid",
                         "path": "{after_path}"},
                        {"kind": "dirty", "value": False},
                    ),
                    **_NBFM_BUDGET,
                ),
            ),
        )
        results.append(FuzzedScenario(
            scenario=scenario,
            param_seed=case_seed,
            prompt_vars={
                "zmq_address": zmq_address,
                "quad_multiplier": quad_multiplier,
            },
        ))

    return results


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
        "Scenario 11: NBFM Pivot (single targeted run)",
        default_n_runs=1,
        server_help="llama.cpp server URL.",
        model_help="Model alias.",
    )
    args = parser.parse_args()

    seed = args.seed if args.seed is not None else 11

    cases = [fz.scenario for fz in generate_nbfm_scenarios(seed=seed, count=1)]
    print(f"Scenario 11: {len(cases)} case(s) — {[c.name for c in cases]}")
    print(f"Prompt: {cases[0].turns[0].prompt}\n")

    report = run_phase_eval(
        phase=110,
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
