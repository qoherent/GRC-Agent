#!/usr/bin/env python3
"""Tier 4 live eval: smoke behavior on installed GNU Radio example graphs.

Run:
    uv run python -m tests.llama_eval.tier4_external_examples --quick
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any

from tests.llama_eval.harness import (
    LiveScenario,
    LiveTurnSpec,
    ToolExpectation,
    build_phase_parser,
    dimension_pass_counts,
    majority_passed,
    run_live_scenario_once,
    run_phase_eval,
    select_cases,
)

DEFAULT_N_RUNS = 1
MAJORITY_THRESHOLD = 0.5
GNU_EXAMPLES = Path("/usr/share/gnuradio/examples")


def _scenario_if_present(
    *,
    category: str,
    name: str,
    relative_path: str,
    prompt: str,
    expected_tool_calls: tuple[ToolExpectation, ...],
    semantic_checks: tuple[dict[str, Any], ...],
    description: str,
) -> LiveScenario | None:
    graph_path = GNU_EXAMPLES / relative_path
    if not graph_path.exists():
        return None
    return LiveScenario(
        category=category,
        name=name,
        description=f"{description} Source: {graph_path}",
        fixture_name=str(graph_path),
        turns=(
            LiveTurnSpec(
                prompt=prompt,
                expected_tool_calls=expected_tool_calls,
                semantic_checks=semantic_checks,
            ),
        ),
    )


def _probe_cases() -> list[LiveScenario]:
    cases = [
        _scenario_if_present(
            category="external_edit_probe",
            name="grfreedv_message_debug_disable_validate",
            relative_path="vocoder/grfreedv.grc",
            prompt=(
                "Disable the blocks_message_debug_0 block in this installed GNU Radio "
                "FreeDV example, then validate it."
            ),
            expected_tool_calls=(
                ToolExpectation(
                    "apply_edit",
                    transaction_operations=(
                        {
                            "op_type": "update_states",
                            "instance_name": "blocks_message_debug_0",
                            "state": "disabled",
                        },
                    ),
                ),
                ToolExpectation("validate_graph"),
            ),
            semantic_checks=(
                {
                    "kind": "block_state_equals",
                    "instance_name": "blocks_message_debug_0",
                    "state": "disabled",
                },
                {
                    "kind": "tool_result",
                    "tool": "validate_graph",
                    "arguments": {"valid": True},
                },
            ),
            description=(
                "Opt-in verified block-state edit on an installed vocoder example. "
                "Promote only after repeated stable live runs."
            ),
        )
    ]
    return [case for case in cases if case is not None]


def _available_cases(*, include_probes: bool = False) -> list[LiveScenario]:
    cases = [
        _scenario_if_present(
            category="external",
            name="dial_tone_summary",
            relative_path="audio/dial_tone.grc",
            prompt="Summarize this installed GNU Radio example flowgraph.",
            expected_tool_calls=(ToolExpectation("summarize_graph"),),
            semantic_checks=({"kind": "no_mutation"},),
            description="Read-only summary on a small installed audio example.",
        ),
        _scenario_if_present(
            category="external_edit",
            name="dial_tone_samp_rate_edit_validate",
            relative_path="audio/dial_tone.grc",
            prompt="Change samp_rate to 44100 in this installed GNU Radio example, then validate it.",
            expected_tool_calls=(
                ToolExpectation(
                    "apply_edit",
                    transaction_operations=(
                        {
                            "op_type": "update_params",
                            "instance_name": "samp_rate",
                            "params": {"value": "44100"},
                        },
                    ),
                ),
                ToolExpectation("validate_graph"),
            ),
            semantic_checks=(
                {"kind": "variable_equals", "name": "samp_rate", "value": "44100"},
                {
                    "kind": "tool_result",
                    "tool": "validate_graph",
                    "arguments": {"valid": True},
                },
            ),
            description="Verified sample-rate edit on a copied installed audio example.",
        ),
        _scenario_if_present(
            category="external",
            name="resampler_validate",
            relative_path="filter/resampler_demo.grc",
            prompt="Validate this installed GNU Radio resampler example.",
            expected_tool_calls=(ToolExpectation("validate_graph"),),
            semantic_checks=(
                {"kind": "no_mutation"},
                {
                    "kind": "tool_result",
                    "tool": "validate_graph",
                    "arguments": {"valid": True},
                },
            ),
            description="Validation on an installed filter example.",
        ),
        _scenario_if_present(
            category="external",
            name="selector_save_copy",
            relative_path="blocks/selector.grc",
            prompt="Save a copy of this installed GNU Radio example to {save_path}.",
            expected_tool_calls=(
                ToolExpectation("save_graph", arguments={"path": "{save_path}"}),
            ),
            semantic_checks=({"kind": "saved_path_valid", "path": "{save_path}"},),
            description="Explicit save-copy on an installed blocks example.",
        ),
        _scenario_if_present(
            category="external_edit",
            name="selector_signal_source_amp_edit_validate",
            relative_path="blocks/selector.grc",
            prompt="Set analog_sig_source_x_0 amp to 0.5 in this installed selector example, then validate it.",
            expected_tool_calls=(
                ToolExpectation(
                    "apply_edit",
                    transaction_operations=(
                        {
                            "op_type": "update_params",
                            "instance_name": "analog_sig_source_x_0",
                            "params": {"amp": "0.5"},
                        },
                    ),
                ),
                ToolExpectation("validate_graph"),
            ),
            semantic_checks=(
                {
                    "kind": "block_param_equals",
                    "instance_name": "analog_sig_source_x_0",
                    "param": "amp",
                    "value": "0.5",
                },
                {
                    "kind": "tool_result",
                    "tool": "validate_graph",
                    "arguments": {"valid": True},
                },
            ),
            description="Verified non-variable block-parameter edit on an installed blocks example.",
        ),
        _scenario_if_present(
            category="external_edit",
            name="selector_samp_rate_edit_validate_save",
            relative_path="blocks/selector.grc",
            prompt=(
                "Change samp_rate to 48000 in this installed selector example, "
                "validate it, then save a copy to {save_path}."
            ),
            expected_tool_calls=(
                ToolExpectation(
                    "apply_edit",
                    transaction_operations=(
                        {
                            "op_type": "update_params",
                            "instance_name": "samp_rate",
                            "params": {"value": "48000"},
                        },
                    ),
                ),
                ToolExpectation("validate_graph"),
                ToolExpectation("save_graph", arguments={"path": "{save_path}"}),
            ),
            semantic_checks=(
                {"kind": "variable_equals", "name": "samp_rate", "value": "48000"},
                {
                    "kind": "tool_result",
                    "tool": "validate_graph",
                    "arguments": {"valid": True},
                },
                {"kind": "saved_path_valid", "path": "{save_path}"},
            ),
            description="Verified edit, validate, and explicit save-copy on an installed blocks example.",
        ),
    ]
    available = [case for case in cases if case is not None]
    if include_probes:
        available.extend(_probe_cases())
    return available


def _run_case(client: Any, model: str, case: LiveScenario) -> dict[str, Any]:
    return run_live_scenario_once(client=client, model=model, scenario=case)


def _render_status(case: LiveScenario, run: dict[str, Any]) -> str:
    dimensions = (
        f"routing={run.get('routing_pass')}, "
        f"argument={run.get('argument_pass')}, "
        f"tool_success={run.get('tool_success_pass')}, "
        f"semantic={run.get('semantic_pass')}, "
        f"safety={run.get('safety_pass')}, "
        f"end_state={run.get('end_state_pass')}, "
        f"recovery={run.get('recovery_pass')}"
    )
    return (
        f"{'PASS' if run.get('matched') else 'FAIL'} "
        f"({', '.join(run.get('tools_called', [])) or 'no tools'}; {dimensions})"
    )


def _build_report(
    case: LiveScenario,
    runs: list[dict[str, Any]],
    n_runs: int,
    threshold: float,
) -> dict[str, Any]:
    pass_count = sum(1 for run in runs if run.get("matched") is True)
    return {
        "category": case.category,
        "name": case.name,
        "description": case.description,
        "runs": runs,
        "pass_count": pass_count,
        "passed": majority_passed(pass_count, n_runs, threshold),
        "dimension_pass_counts": dimension_pass_counts([{"runs": runs}]),
    }


def main() -> int:
    parser = build_phase_parser(
        "Tier 4 live model eval: installed GNU Radio example smoke behavior.",
        default_n_runs=DEFAULT_N_RUNS,
        server_help="Server URL.",
        model_help="Model alias.",
    )
    parser.add_argument(
        "--include-probes",
        action="store_true",
        help="Include opt-in known-gap probes. Do not use for release gates.",
    )
    args = parser.parse_args()
    n_runs = 1 if args.quick else args.n_runs
    cases = select_cases(
        _available_cases(include_probes=args.include_probes),
        category=args.category,
        case_name=args.case,
    )
    if not cases:
        print("No matching installed GNU Radio example cases.", file=sys.stderr)
        return 1

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
    )
    print("\n" + json.dumps(report, indent=2, sort_keys=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
