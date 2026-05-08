#!/usr/bin/env python3
"""Tier 2 release model eval: broader coverage for release-time checks.

Selected cases from the original phase 1-6 suite, updated for the current
tool contract. Run only at release time or manually.

Run:
    uv run python -m tests.llama_eval.tier2_release
    uv run python -m tests.llama_eval.tier2_release --quick
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import Any

from tests.llama_eval.harness import (
    DEFAULT_FIXTURE_NAME,
    LiveScenario,
    LiveTurnSpec,
    MVP_RELEASE_MODEL_TOOLS,
    ToolExpectation,
    align_scenario_to_mvp_release,
    build_phase_parser,
    dimension_pass_counts,
    majority_passed,
    run_phase_eval,
    run_live_scenario_once,
    scenario_expected_tools_only,
    select_cases,
    tool_expectations_from_names,
)

DEFAULT_N_RUNS = 1
MAJORITY_THRESHOLD = 0.5


@dataclass(frozen=True)
class Tier2Case:
    category: str
    name: str
    prompt: str
    expected_tools: list[str]
    accept_any_tool: bool = False
    fixture_name: str = DEFAULT_FIXTURE_NAME
    target_fixture_name: str | None = None
    semantic_checks: tuple[dict[str, Any], ...] = ()
    expected_tool_calls: tuple[ToolExpectation, ...] | None = None
    description: str = ""
    allow_safe_text_only: bool = False

    def to_live_scenario(self) -> LiveScenario:
        expected_tool_calls = (
            self.expected_tool_calls
            if self.expected_tool_calls is not None
            else tool_expectations_from_names(self.expected_tools)
        )
        return LiveScenario(
            category=self.category,
            name=self.name,
            turns=(
                LiveTurnSpec(
                    prompt=self.prompt,
                    expected_tool_calls=expected_tool_calls,
                    semantic_checks=self.semantic_checks,
                    accept_any_tool=self.accept_any_tool,
                    allow_safe_text_only=self.allow_safe_text_only,
                ),
            ),
            fixture_name=self.fixture_name,
            target_fixture_name=self.target_fixture_name,
            description=self.description,
        )


READ_ONLY_CHECKS = ({"kind": "no_mutation"},)
RAW_YAML_REFUSAL_CHECKS = (
    {"kind": "no_mutation"},
    {"kind": "no_mutation_tools"},
    {"kind": "assistant_text_contains", "needles": ["unsupported", "yaml", "raw"]},
)
SAMP_RATE_CONNECTION = "analog_random_source_x_0:0->blocks_throttle2_0:0"


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


def _samp_rate_delta(value: str, *, dirty: bool | None = True, validated: bool = True) -> dict:
    delta: dict[str, Any] = {
        "variables": {"samp_rate": value},
        "block_params": {"samp_rate": {"value": value}},
    }
    if dirty is not None:
        delta["dirty"] = dirty
    if validated:
        delta["validation_status"] = "valid"
        delta["validation_returncode"] = 0
    return delta


TIER2_CASES: list[Tier2Case] = [
    # Representative single-tool routing coverage.
    Tier2Case(
        "summarize",
        "summarize_what_does",
        "What does this flowgraph do?",
        ["summarize_graph"],
        semantic_checks=READ_ONLY_CHECKS,
    ),
    Tier2Case(
        "summarize",
        "summarize_blocks",
        "What blocks are in here?",
        ["summarize_graph"],
        semantic_checks=READ_ONLY_CHECKS,
    ),
    Tier2Case(
        "load",
        "load_other",
        "Switch over to this other flowgraph: {target_path}",
        ["load_grc"],
        target_fixture_name="random_bit_generator_dual_sink.grc",
        expected_tool_calls=(
            ToolExpectation("load_grc", arguments={"file_path": "{target_path}"}),
        ),
        semantic_checks=({"kind": "path_equals", "path": "{target_path}"},),
    ),
    Tier2Case(
        "search",
        "search_time_sink",
        "What time sink blocks are available in GNU Radio?",
        ["search_grc"],
        semantic_checks=READ_ONLY_CHECKS,
    ),
    Tier2Case(
        "search",
        "search_session_source",
        "Search the current graph for source blocks.",
        ["search_grc"],
        semantic_checks=READ_ONLY_CHECKS,
    ),
    Tier2Case(
        "context",
        "context_throttle",
        "What is blocks_throttle2_0 connected to?",
        ["get_grc_context"],
        semantic_checks=READ_ONLY_CHECKS,
    ),
    Tier2Case(
        "context",
        "context_samp_rate",
        "Show me what uses the samp_rate block.",
        ["get_grc_context"],
        semantic_checks=READ_ONLY_CHECKS,
    ),
    Tier2Case(
        "describe",
        "describe_time_sink",
        "What are the parameters on qtgui_time_sink_x?",
        ["describe_block"],
        semantic_checks=READ_ONLY_CHECKS,
    ),
    Tier2Case(
        "describe",
        "describe_char_to_float",
        "Tell me about blocks_char_to_float.",
        ["describe_block"],
        semantic_checks=READ_ONLY_CHECKS,
    ),
    Tier2Case(
        "validate",
        "validate_check",
        "Check whether this flowgraph is valid.",
        ["validate_graph"],
        semantic_checks=READ_ONLY_CHECKS
        + ({"kind": "tool_result", "tool": "validate_graph", "arguments": {"valid": True}},),
    ),
    Tier2Case(
        "validate",
        "validate_compile",
        "Will this compile cleanly?",
        ["validate_graph"],
        semantic_checks=READ_ONLY_CHECKS
        + ({"kind": "tool_result", "tool": "validate_graph", "arguments": {"valid": True}},),
    ),
    Tier2Case(
        "save",
        "save_direct",
        "Save the graph.",
        ["save_graph"],
        semantic_checks=({"kind": "saved_path_valid", "path": "{after_path}"},),
    ),
    Tier2Case(
        "save",
        "save_to_path",
        "Save the graph to {save_path}.",
        ["save_graph"],
        expected_tool_calls=(
            ToolExpectation("save_graph", arguments={"path": "{save_path}"}),
        ),
        semantic_checks=({"kind": "saved_path_valid", "path": "{save_path}"},),
    ),
    Tier2Case(
        "edit",
        "edit_samp_rate_48k",
        "Change samp_rate to 48000.",
        ["apply_edit"],
        expected_tool_calls=_set_samp_rate_expectation("48000"),
        semantic_checks=(
            {"kind": "exact_graph_delta", "delta": _samp_rate_delta("48000")},
        ),
    ),
    Tier2Case(
        "edit",
        "edit_samp_rate_16k",
        "Update samp_rate to 16000.",
        ["apply_edit"],
        expected_tool_calls=_set_samp_rate_expectation("16000"),
        semantic_checks=(
            {"kind": "exact_graph_delta", "delta": _samp_rate_delta("16000")},
        ),
    ),
    Tier2Case(
        "edit",
        "edit_remove_connection",
        "Disconnect analog_random_source_x_0 output 0 from blocks_throttle2_0 input 0.",
        ["remove_connection"],
        expected_tool_calls=(
            ToolExpectation(
                "remove_connection",
                arguments={
                    "connection_id": "analog_random_source_x_0:0->blocks_throttle2_0:0",
                },
                require_result_ok=False,
            ),
        ),
        semantic_checks=(
            {"kind": "no_mutation"},
            {"kind": "connection_present", "connection_id": SAMP_RATE_CONNECTION},
        ),
    ),
    Tier2Case(
        "edit",
        "edit_add_variable",
        "Add a variable called noise_level set to 0.1.",
        ["apply_edit"],
        semantic_checks=(
            {
                "kind": "exact_graph_delta",
                "delta": {
                    "added_blocks": ["noise_level"],
                    "variables": {"noise_level": "0.1"},
                    "dirty": True,
                    "validation_status": "valid",
                    "validation_returncode": 0,
                },
            },
        ),
    ),
    Tier2Case(
        "propose",
        "propose_samp_rate",
        "Preview changing samp_rate to 64000 before you touch anything.",
        ["propose_edit"],
        semantic_checks=READ_ONLY_CHECKS,
    ),
    # Multi-tool chains
    Tier2Case(
        "chain",
        "search_describe_agc",
        "I need an AGC block. Find it and tell me what its ports look like.",
        ["search_grc", "describe_block"],
        semantic_checks=READ_ONLY_CHECKS,
    ),
    Tier2Case(
        "chain",
        "search_describe_fir",
        "Find a FIR filter block and describe it.",
        ["search_grc", "describe_block"],
        semantic_checks=READ_ONLY_CHECKS,
    ),
    Tier2Case(
        "chain",
        "context_then_edit",
        "Show me what uses the samp_rate block, then change its value to 22050.",
        ["get_grc_context", "apply_edit"],
        expected_tool_calls=(
            ToolExpectation("get_grc_context"),
            *_set_samp_rate_expectation("22050"),
        ),
        semantic_checks=(
            {"kind": "exact_graph_delta", "delta": _samp_rate_delta("22050")},
        ),
    ),
    Tier2Case(
        "chain",
        "edit_then_validate",
        "Change the samp_rate variable to 96000 and then validate the graph.",
        ["apply_edit", "validate_graph"],
        expected_tool_calls=(
            *_set_samp_rate_expectation("96000"),
            ToolExpectation("validate_graph"),
        ),
        semantic_checks=(
            {
                "kind": "exact_graph_delta",
                "delta": _samp_rate_delta("96000", validated=True),
            },
            {"kind": "tool_result", "tool": "validate_graph", "arguments": {"valid": True}},
        ),
    ),
    Tier2Case(
        "chain",
        "edit_validate_save",
        "Set samp_rate to 16000, validate the graph, and save it.",
        ["apply_edit", "validate_graph", "save_graph"],
        expected_tool_calls=(
            *_set_samp_rate_expectation("16000"),
            ToolExpectation("validate_graph"),
            ToolExpectation("save_graph"),
        ),
        semantic_checks=(
            {
                "kind": "exact_graph_delta",
                "delta": _samp_rate_delta("16000", dirty=None, validated=True),
            },
            {"kind": "tool_result", "tool": "validate_graph", "arguments": {"valid": True}},
            {"kind": "saved_path_valid", "path": "{after_path}"},
        ),
    ),
    Tier2Case(
        "chain",
        "summarize_then_edit",
        "Give me a quick summary of the graph, then update samp_rate to 8000.",
        ["summarize_graph", "apply_edit"],
        expected_tool_calls=(
            ToolExpectation("summarize_graph"),
            *_set_samp_rate_expectation("8000"),
        ),
        semantic_checks=(
            {"kind": "exact_graph_delta", "delta": _samp_rate_delta("8000")},
        ),
    ),
    Tier2Case(
        "chain",
        "preview_apply_validate",
        "Preview setting samp_rate to 48000, apply it, and validate.",
        ["propose_edit", "apply_edit", "validate_graph"],
        expected_tool_calls=(
            ToolExpectation("propose_edit"),
            *_set_samp_rate_expectation("48000"),
            ToolExpectation("validate_graph"),
        ),
        semantic_checks=(
            {
                "kind": "exact_graph_delta",
                "delta": _samp_rate_delta("48000", validated=True),
            },
            {"kind": "tool_result", "tool": "validate_graph", "arguments": {"valid": True}},
        ),
    ),
    # Natural/vague prompts
    Tier2Case(
        "natural",
        "what_am_i_looking_at",
        "What am I looking at here?",
        ["summarize_graph"],
        semantic_checks=READ_ONLY_CHECKS,
    ),
    Tier2Case(
        "natural",
        "is_this_going_to_work",
        "Is this going to compile and run?",
        ["validate_graph"],
        semantic_checks=READ_ONLY_CHECKS
        + ({"kind": "tool_result", "tool": "validate_graph", "arguments": {"valid": True}},),
    ),
    Tier2Case(
        "natural",
        "write_it_out",
        "Go ahead and write it out.",
        ["save_graph"],
        semantic_checks=({"kind": "saved_path_valid", "path": "{after_path}"},),
    ),
    # Domain-specific search
    Tier2Case(
        "domain",
        "need_carrier_recovery",
        "I need something for carrier recovery in my signal.",
        ["search_grc"],
        semantic_checks=READ_ONLY_CHECKS,
    ),
    Tier2Case(
        "domain",
        "want_to_see_spectrum",
        "I want to see the spectrum of my signal.",
        ["search_grc"],
        semantic_checks=READ_ONLY_CHECKS,
    ),
    # Negative / unsupported
    Tier2Case(
        "negative",
        "undo_request",
        "Undo the last change.",
        [],
        semantic_checks=(
            {"kind": "no_mutation"},
            {"kind": "no_mutation_tools"},
            {"kind": "assistant_text_contains", "needles": ["unsupported", "undo", "cannot"]},
        ),
    ),
    Tier2Case(
        "negative",
        "redo_request",
        "Redo the last change.",
        [],
        semantic_checks=(
            {"kind": "no_mutation"},
            {"kind": "no_mutation_tools"},
            {"kind": "assistant_text_contains", "needles": ["unsupported", "redo", "cannot"]},
        ),
    ),
    # Expert (no-tool, text-only)
    Tier2Case(
        "expert",
        "pmt_dict_immutability",
        "How do I add a key to a PMT dictionary without mutating it in place?",
        ["search_manual"],
        semantic_checks=READ_ONLY_CHECKS,
        allow_safe_text_only=True,
    ),
    Tier2Case(
        "expert",
        "binary_short_scaling",
        "What scale factor between floats and 16-bit shorts?",
        ["search_manual"],
        semantic_checks=READ_ONLY_CHECKS,
    ),
    # Rewire
    Tier2Case(
        "rewire",
        "second_trace",
        "Put that float stream on a second trace in the time sink.",
        ["apply_edit"],
        semantic_checks=({"kind": "mutation"},),
    ),
    Tier2Case(
        "rewire",
        "exact_stream_rewire",
        (
            "Rewire connection_id "
            "blocks_throttle2_0:0->blocks_char_to_float_0:0 to "
            "analog_random_source_x_0:0->blocks_char_to_float_0:0, then validate."
        ),
        ["rewire_connection", "validate_graph"],
        expected_tool_calls=(
            ToolExpectation(
                "rewire_connection",
                arguments={
                    "old_connection_id": "blocks_throttle2_0:0->blocks_char_to_float_0:0",
                    "new_src_block": "analog_random_source_x_0",
                    "new_src_port": 0,
                    "new_dst_block": "blocks_char_to_float_0",
                    "new_dst_port": 0,
                },
            ),
            ToolExpectation("validate_graph"),
        ),
        semantic_checks=(
            {
                "kind": "exact_graph_delta",
                "delta": {
                    "added_connections": [
                        "analog_random_source_x_0:0->blocks_char_to_float_0:0"
                    ],
                    "removed_connections": [
                        "blocks_throttle2_0:0->blocks_char_to_float_0:0"
                    ],
                    "dirty": True,
                    "validation_status": "valid",
                    "validation_returncode": 0,
                },
            },
            {
                "kind": "connection_absent",
                "connection_id": "blocks_throttle2_0:0->blocks_char_to_float_0:0",
            },
            {
                "kind": "connection_present",
                "connection_id": "analog_random_source_x_0:0->blocks_char_to_float_0:0",
            },
            {"kind": "tool_result", "tool": "validate_graph", "arguments": {"valid": True}},
        ),
        description="Exact atomic stream rewire: remove old edge and add new edge in one transaction.",
    ),
    Tier2Case(
        "rewire",
        "disconnect_source",
        "Disconnect the random source from the throttle.",
        [],
        semantic_checks=(
            {"kind": "no_mutation"},
            {"kind": "no_mutation_tools"},
            {
                "kind": "assistant_text_contains",
                "needles": ["exact connection endpoints", "inspect"],
            },
        ),
    ),
]


def _as_release_scenario(case: Tier2Case) -> LiveScenario:
    scenario = align_scenario_to_mvp_release(case.to_live_scenario())
    if case.name in {"write_it_out", "save_direct", "save_to_path"}:
        # MVP wrapper profile keeps save non-model-facing; treat save asks as unsupported.
        return LiveScenario(
            category=scenario.category,
            name=scenario.name,
            fixture_name=scenario.fixture_name,
            target_fixture_name=scenario.target_fixture_name,
            description=scenario.description,
            turns=(
                LiveTurnSpec(
                    prompt=scenario.turns[0].prompt,
                    expected_tool_calls=(),
                    allow_safe_text_only=True,
                    semantic_checks=(
                        {"kind": "no_mutation"},
                        {"kind": "no_mutation_tools"},
                        {
                            "kind": "assistant_text_contains",
                            "needles": ["unsupported", "save"],
                        },
                    ),
                ),
            ),
        )
    if case.name == "edit_then_validate":
        # Wrapper-level contract: one committed mutation call with validation authority in runtime.
        return LiveScenario(
            category=scenario.category,
            name=scenario.name,
            fixture_name=scenario.fixture_name,
            target_fixture_name=scenario.target_fixture_name,
            description=scenario.description,
            turns=(
                LiveTurnSpec(
                    prompt=scenario.turns[0].prompt,
                    expected_tool_calls=(
                        ToolExpectation(
                            "change_graph",
                            arguments={"operation_kind": "set_param"},
                        ),
                    ),
                    semantic_checks=(
                        {
                            "kind": "exact_graph_delta",
                            "delta": _samp_rate_delta("96000", validated=True),
                        },
                    ),
                ),
            ),
        )
    if case.name == "edit_validate_save":
        # Save is non-model-facing in MVP; keep the committed edit expectation only.
        return LiveScenario(
            category=scenario.category,
            name=scenario.name,
            fixture_name=scenario.fixture_name,
            target_fixture_name=scenario.target_fixture_name,
            description=scenario.description,
            turns=(
                LiveTurnSpec(
                    prompt=scenario.turns[0].prompt,
                    expected_tool_calls=(
                        ToolExpectation(
                            "change_graph",
                            arguments={"operation_kind": "set_param"},
                        ),
                    ),
                    semantic_checks=(
                        {
                            "kind": "exact_graph_delta",
                            "delta": _samp_rate_delta("16000"),
                        },
                    ),
                ),
            ),
        )
    return scenario


def release_cases() -> list[LiveScenario]:
    scenarios = [_as_release_scenario(case) for case in TIER2_CASES]
    for scenario in scenarios:
        if not scenario_expected_tools_only(
            scenario,
            allowed_tool_names=MVP_RELEASE_MODEL_TOOLS,
        ):
            raise RuntimeError(
                f"Tier 2 MVP release case contains non-wrapper expected tools: {scenario.name}"
            )
    return scenarios


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
        f"safety={run.get('safety_pass')}, "
        f"end_state={run.get('end_state_pass')}, "
        f"recovery={run.get('recovery_pass')}"
    )
    return (
        f"{'PASS' if run.get('matched') else 'FAIL'} "
        f"({', '.join(run.get('tools_called', [])) or 'no tools'}; {dimensions})"
    )


def _build_report(case: LiveScenario, runs: list, n_runs: int, threshold: float) -> dict:
    mc = sum(1 for r in runs if r["matched"])
    routing_pass_count = sum(1 for r in runs if r.get("routing_pass") is True)
    argument_pass_count = sum(1 for r in runs if r.get("argument_pass") is True)
    tool_success_pass_count = sum(1 for r in runs if r.get("tool_success_pass") is True)
    semantic_pass_count = sum(1 for r in runs if r.get("semantic_pass") is True)
    safety_pass_count = sum(1 for r in runs if r.get("safety_pass") is True)
    end_state_pass_count = sum(1 for r in runs if r.get("end_state_pass") is True)
    return {
        "category": case.category, "name": case.name, "runs": runs,
        "pass_count": mc,
        "routing_pass_count": routing_pass_count,
        "argument_pass_count": argument_pass_count,
        "tool_success_pass_count": tool_success_pass_count,
        "semantic_pass_count": semantic_pass_count,
        "safety_pass_count": safety_pass_count,
        "end_state_pass_count": end_state_pass_count,
        "passed": majority_passed(mc, n_runs, threshold),
        "dimension_pass_counts": dimension_pass_counts([{"runs": runs}]),
        "report_scope": "routing, arguments, tool success, semantic state, safety, and end state",
    }


def main() -> int:
    parser = build_phase_parser(
        "Tier 2 release model eval: broader coverage.",
        default_n_runs=DEFAULT_N_RUNS,
        server_help="Server URL.", model_help="Model alias.",
    )
    args = parser.parse_args()
    n_runs = 1 if args.quick else args.n_runs
    cases = select_cases(release_cases(), category=args.category, case_name=args.case)
    if not cases:
        print("No matching cases.", file=sys.stderr)
        return 1
    report = run_phase_eval(
        phase=20, server_url=args.server_url, model=args.model,
        cases=cases, n_runs=n_runs, majority_threshold=MAJORITY_THRESHOLD,
        run_case=_run_case, build_case_report=_build_report,
        render_status=_render_status, retry_on_timeout=True,
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
