#!/usr/bin/env python3
"""Tier 3 live model eval: multi-turn recovery and clarification behavior.

Run:
    uv run python -m tests.llama_eval.tier3_multiturn
    uv run python -m tests.llama_eval.tier3_multiturn --quick
"""

from __future__ import annotations

import json
import sys
from typing import Any

from grc_agent.recovery import (
    NONRECOVERABLE_INVALID_END_STATE,
)

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


def _set_samp_rate_expectation(value: str) -> tuple[ToolExpectation, ...]:
    return (
        ToolExpectation(
            name="apply_edit",
            transaction_operations=(
                {
                    "op_type": "update_params",
                    "instance_name": "samp_rate",
                    "params": {"value": value},
                },
            ),
        ),
    )


TIER3_CASES: list[LiveScenario] = [
    LiveScenario(
        category="followup",
        name="edit_then_validate_no_reapply",
        description="Second turn validates current edited graph without repeating the edit.",
        turns=(
            LiveTurnSpec(
                prompt="Change samp_rate to 48000.",
                expected_tool_calls=_set_samp_rate_expectation("48000"),
                semantic_checks=(
                    {"kind": "variable_equals", "name": "samp_rate", "value": "48000"},
                ),
            ),
            LiveTurnSpec(
                prompt="Now validate it.",
                expected_tool_calls=(ToolExpectation("validate_graph"),),
                semantic_checks=(
                    {"kind": "no_mutation"},
                    {
                        "kind": "tool_result",
                        "tool": "validate_graph",
                        "arguments": {"valid": True},
                    },
                ),
            ),
        ),
    ),
    LiveScenario(
        category="followup",
        name="edit_then_save_no_reapply",
        description="Second turn saves current edited graph without repeating the edit.",
        turns=(
            LiveTurnSpec(
                prompt="Set samp_rate to 16000.",
                expected_tool_calls=_set_samp_rate_expectation("16000"),
                semantic_checks=(
                    {"kind": "variable_equals", "name": "samp_rate", "value": "16000"},
                ),
            ),
            LiveTurnSpec(
                prompt="Save it now.",
                expected_tool_calls=(ToolExpectation("save_graph"),),
                semantic_checks=({"kind": "saved_path_valid", "path": "{after_path}"},),
            ),
        ),
    ),
    LiveScenario(
        category="preview",
        name="preview_then_apply_only_apply_mutates",
        description="Preview turn must not mutate; apply turn must perform requested edit.",
        turns=(
            LiveTurnSpec(
                prompt="Preview changing samp_rate to 64000 before applying anything.",
                expected_tool_calls=(ToolExpectation("propose_edit"),),
                semantic_checks=({"kind": "no_mutation"},),
            ),
            LiveTurnSpec(
                prompt="Apply that samp_rate change now.",
                expected_tool_calls=_set_samp_rate_expectation("64000"),
                semantic_checks=(
                    {"kind": "variable_equals", "name": "samp_rate", "value": "64000"},
                ),
            ),
        ),
    ),
    LiveScenario(
        category="clarification",
        name="clarification_invalid_reply_no_mutation",
        description="Invalid clarification reply keeps pending choice and does not mutate.",
        turns=(
            LiveTurnSpec(
                prompt=(
                    "Use auto_insert_block to insert a compatible block; "
                    "if multiple safe choices validate, ask me to choose."
                ),
                expected_tool_calls=(
                    ToolExpectation("auto_insert_block", require_result_ok=False),
                ),
                semantic_checks=(
                    {
                        "kind": "tool_result",
                        "tool": "auto_insert_block",
                        "arguments": {"clarification_required": True},
                    },
                ),
            ),
            LiveTurnSpec(
                prompt="Z",
                clarification_response=True,
                semantic_checks=(
                    {"kind": "no_mutation"},
                    {"kind": "clarification_mode", "mode": "reminder"},
                ),
            ),
        ),
    ),
    LiveScenario(
        category="clarification",
        name="clarification_option_a_executes",
        description="A valid clarification label executes the stored real tool option.",
        turns=(
            LiveTurnSpec(
                prompt=(
                    "Use auto_insert_block to insert a compatible block; "
                    "if multiple safe choices validate, ask me to choose."
                ),
                expected_tool_calls=(
                    ToolExpectation("auto_insert_block", require_result_ok=False),
                ),
                semantic_checks=(
                    {
                        "kind": "tool_result",
                        "tool": "auto_insert_block",
                        "arguments": {"clarification_required": True},
                    },
                ),
            ),
            LiveTurnSpec(
                prompt="A",
                clarification_response=True,
                accept_any_tool=True,
                semantic_checks=(
                    {"kind": "clarification_mode", "mode": "executed"},
                    {"kind": "mutation"},
                ),
            ),
        ),
    ),
    LiveScenario(
        category="recovery",
        name="invalid_disconnect_end_state_no_retry",
        description=(
            "A GNU-invalid exact disconnect is classified as a nonrecoverable "
            "end-state failure and must not trigger model repair."
        ),
        turns=(
            LiveTurnSpec(
                prompt=(
                    "Disconnect analog_random_source_x_0 output 0 from "
                    "blocks_throttle2_0 input 0."
                ),
                expected_tool_calls=(
                    ToolExpectation(
                        "remove_connection",
                        arguments={
                            "connection_id": "analog_random_source_x_0:0->blocks_throttle2_0:0",
                        },
                        require_result_ok=False,
                    ),
                ),
                semantic_checks=({"kind": "no_mutation"},),
                recovery_enabled=True,
                expected_recovery_class=NONRECOVERABLE_INVALID_END_STATE,
            ),
        ),
    ),
    LiveScenario(
        category="connection",
        name="vague_disconnect_inspects_before_edit",
        description=(
            "A vague disconnect request should inspect graph context before any mutation "
            "instead of guessing incomplete remove_connection arguments."
        ),
        turns=(
            LiveTurnSpec(
                prompt="Disconnect the random source.",
                allow_safe_text_only=True,
                semantic_checks=(
                    {"kind": "no_mutation"},
                    {"kind": "no_mutation_tools"},
                    {
                        "kind": "assistant_text_contains",
                        "needles": ["exact connection endpoints", "inspect"],
                    },
                ),
            ),
        ),
    ),
]


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
        "Tier 3 live model eval: multi-turn clarification and recovery behavior.",
        default_n_runs=DEFAULT_N_RUNS,
        server_help="Server URL.",
        model_help="Model alias.",
    )
    args = parser.parse_args()
    n_runs = 1 if args.quick else args.n_runs
    cases = select_cases(TIER3_CASES, category=args.category, case_name=args.case)
    if not cases:
        print("No matching cases.", file=sys.stderr)
        return 1

    report = run_phase_eval(
        phase=30,
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
