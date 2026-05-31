#!/usr/bin/env python3
"""Scenario 12: FFT Pipeline — Add frequency analysis chain to dial tone.

Usage:
    uv run python -m tests.llama_eval.scenario12_fft_pipeline
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
_FFT_BUDGET = {
    "max_tool_rounds": 10,
    "max_tool_calls": 20,
    "max_prompt_tokens": 25000,
}

# Fuzz pools
FFT_SIZE_VALS = (512, 1024, 2048)


def generate_fft_scenarios(
    seed: int = 0,
    count: int = 3,
) -> list[FuzzedScenario]:
    rng = random.Random(seed)
    results: list[FuzzedScenario] = []

    for i in range(count):
        case_seed = rng.randint(0, 2**31 - 1)
        fft_size_val = rng.choice(FFT_SIZE_VALS)

        prompt = (
            "We need to perform custom hardware-level frequency analysis on "
            "the combined dial tone signal. First, remove the Audio Sink. "
            f"Create a new variable named `fft_len` and set it to {fft_size_val}. "
            "Next, add a 'Stream to Vector' block, followed by a 'Forward FFT' "
            "block, and terminate the pipeline into a Null Sink. Configure both "
            "the Stream to Vector and the FFT blocks to use `fft_len` for their "
            "size/length parameters. The input signal is float, so ensure the "
            "data types are configured correctly. Connect the Add block to the "
            "Stream to Vector, then to the FFT, then to the Null Sink."
        )

        scenario = LiveScenario(
            category="dsp",
            name=f"fft_pipeline_n{fft_size_val}",
            fixture_name="dial_tone.grc",
            description=(
                f"FFT pipeline: fft_size={fft_size_val}"
            ),
            release_profile="R4C_ADD_VARIABLE",
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
                        {"kind": "saved_variable_equals",
                         "name": "fft_len",
                         "value": str(fft_size_val),
                         "path": "{after_path}"},
                        {"kind": "saved_block_present",
                         "instance_name": "blocks_stream_to_vector_0",
                         "path": "{after_path}"},
                        {"kind": "saved_block_present",
                         "instance_name": "fft_vxx_0",
                         "path": "{after_path}"},
                        {"kind": "saved_block_present",
                         "instance_name": "blocks_null_sink_0",
                         "path": "{after_path}"},
                        {"kind": "saved_block_param_equals",
                         "instance_name": "blocks_stream_to_vector_0",
                         "param": "num_items", "value": "fft_len",
                         "path": "{after_path}"},
                        {"kind": "saved_block_param_equals",
                         "instance_name": "blocks_stream_to_vector_0",
                         "param": "type", "value": "float",
                         "path": "{after_path}"},
                        {"kind": "saved_block_param_equals",
                         "instance_name": "fft_vxx_0",
                         "param": "fft_size", "value": "fft_len",
                         "path": "{after_path}"},
                        {"kind": "saved_connection_present",
                         "connection_id":
                         "blocks_add_xx:0->blocks_stream_to_vector_0:0",
                         "path": "{after_path}"},
                        {"kind": "saved_connection_present",
                         "connection_id":
                         "blocks_stream_to_vector_0:0->fft_vxx_0:0",
                         "path": "{after_path}"},
                        {"kind": "saved_connection_present",
                         "connection_id":
                         "fft_vxx_0:0->blocks_null_sink_0:0",
                         "path": "{after_path}"},
                    ),
                    **_FFT_BUDGET,
                ),
            ),
        )
        results.append(FuzzedScenario(
            scenario=scenario,
            param_seed=case_seed,
            prompt_vars={
                "fft_size_val": fft_size_val,
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
        "Scenario 12: FFT Pipeline (single targeted run)",
        default_n_runs=1,
        server_help="llama.cpp server URL.",
        model_help="Model alias.",
    )
    args = parser.parse_args()

    seed = args.seed if args.seed is not None else 12

    cases = [fz.scenario for fz in generate_fft_scenarios(seed=seed, count=1)]
    print(f"Scenario 12: {len(cases)} case(s) — {[c.name for c in cases]}")
    print(f"Prompt: {cases[0].turns[0].prompt}\n")

    report = run_phase_eval(
        phase=120,
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
