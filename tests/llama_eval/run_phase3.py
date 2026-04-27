#!/usr/bin/env python3
"""Phase 3 model evaluation: realistic prompts plus argument checks."""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, field
from typing import Any

from grc_agent.agent import GrcAgent
from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.llama_server import LlamaServerError, run_bounded_llama_turn

from tests.llama_eval.harness import (
    DEFAULT_FIXTURE_NAME,
    build_phase_parser,
    default_phase_summary,
    extract_executed_tool_calls,
    extract_requested_tool_calls,
    isolated_fixture_workspace,
    majority_passed,
    render_value_templates as _render_value_templates,
    run_phase_eval,
    select_cases,
    text_contains_any,
    tool_call_matches_argument_checks,
    tool_call_matches_transaction_checks,
    tools_appear_in_expected_order,
)

DEFAULT_N_RUNS = 3
MAJORITY_THRESHOLD = 0.5


@dataclass(frozen=True)
class RealisticCase:
    category: str
    name: str
    prompt: str
    expected_tools_in_order: list[str] = field(default_factory=list)
    checked_tool_name: str | None = None
    tool_arg_checks: dict[str, Any] | None = None
    transaction_checks: list[dict[str, Any]] | None = None
    transaction_checks_ordered: bool = True
    text_contains_any_checks: list[str] | None = None
    required_absent_nodes: tuple[str, ...] = ()
    require_save_called: bool = False
    require_summary_called: bool = False
    require_validate_called: bool = False
    accept_apply_edit_validation: bool = False
    fixture_name: str = DEFAULT_FIXTURE_NAME
    target_fixture_name: str | None = None
    description: str = ""


PHASE3_CASES: list[RealisticCase] = [
    # -- vague goal-oriented requests --
    RealisticCase(
        "goal",
        "faster_sample_rate",
        "This is running too slow, can you speed it up to 48k?",
        ["apply_edit"],
        transaction_checks=[
            {"op_type": "update_params", "instance_name": "samp_rate"},
        ],
    ),
    RealisticCase(
        "goal",
        "make_it_slower",
        "Slow this down to 8k.",
        ["apply_edit"],
        transaction_checks=[
            {"op_type": "update_params", "instance_name": "samp_rate"},
        ],
    ),
    RealisticCase(
        "goal",
        "higher_rate",
        "I need a higher sample rate, set it to 96000.",
        ["apply_edit"],
        transaction_checks=[
            {"op_type": "update_params", "instance_name": "samp_rate"},
        ],
    ),
    # -- natural language without tool hints --
    RealisticCase(
        "natural",
        "what_am_i_looking_at",
        "What am I looking at here?",
        ["summarize_graph"],
        require_summary_called=True,
    ),
    RealisticCase(
        "natural",
        "is_this_going_to_work",
        "Is this going to compile and run?",
        ["validate_graph"],
        require_validate_called=True,
    ),
    RealisticCase(
        "natural",
        "write_it_out",
        "Go ahead and write it out.",
        ["save_graph"],
        require_save_called=True,
    ),
    # -- block/session inspection without tool naming --
    RealisticCase(
        "inspect",
        "whats_the_source",
        "What's generating the signal here?",
        ["summarize_graph"],
    ),
    RealisticCase(
        "inspect",
        "show_me_connections",
        "Show me how things are wired up around the throttle.",
        ["get_grc_context"],
    ),
    RealisticCase(
        "inspect",
        "what_does_this_do",
        "The time sink block, what parameters does it have?",
        ["describe_block"],
    ),
    # -- deep knowledge: vague domain requests --
    RealisticCase(
        "domain",
        "need_carrier_recovery",
        "I need something for carrier recovery in my signal.",
        ["search_grc"],
    ),
    RealisticCase(
        "domain",
        "want_to_see_spectrum",
        "I want to see the spectrum of my signal. What block should I use?",
        ["search_grc"],
    ),
    RealisticCase(
        "domain",
        "need_scrambling",
        "How would I scramble my data stream?",
        ["search_grc"],
    ),
    # -- multi-step with natural language --
    RealisticCase(
        "multi",
        "inspect_and_change",
        "Take a quick look at samp_rate, then set it to 44100.",
        ["get_grc_context", "apply_edit"],
        transaction_checks=[
            {"op_type": "update_params", "instance_name": "samp_rate"},
        ],
    ),
    RealisticCase(
        "multi",
        "find_and_tweak",
        "I need automatic gain control. Find the right block and tell me about it.",
        ["search_grc", "describe_block"],
    ),
    RealisticCase(
        "multi",
        "look_and_validate",
        "Tell me what the throttle block does, then make sure the graph is valid.",
        ["describe_block", "validate_graph"],
    ),
    # -- rate changes with colloquial language --
    RealisticCase(
        "rate",
        "make_it_48k",
        "Change to 48k.",
        ["apply_edit"],
        transaction_checks=[
            {"op_type": "update_params", "instance_name": "samp_rate"},
        ],
    ),
    RealisticCase(
        "rate",
        "set_rate_to_44100",
        "Set the rate to 44100.",
        ["apply_edit"],
        transaction_checks=[
            {"op_type": "update_params", "instance_name": "samp_rate"},
        ],
    ),
    RealisticCase(
        "rate",
        "bump_to_96k",
        "Can you bump the sample rate up to 96k?",
        ["apply_edit"],
        transaction_checks=[
            {"op_type": "update_params", "instance_name": "samp_rate"},
        ],
    ),
    RealisticCase(
        "expression",
        "preserve_symbolic_rate_expression",
        "Set the time sink sample rate to samp_rate/2.",
        ["apply_edit"],
        transaction_checks=[
            {
                "op_type": "update_params",
                "instance_name": "qtgui_time_sink_x_0",
                "params": {"srate": "samp_rate/2"},
            },
        ],
    ),
    # -- loading and saving explicit paths --
    RealisticCase(
        "load",
        "load_other_fixture_overview",
        "Open this other flowgraph and give me a quick overview: {target_path}",
        ["load_grc", "summarize_graph"],
        checked_tool_name="load_grc",
        tool_arg_checks={"file_path": "{target_path}"},
        target_fixture_name="random_bit_generator_dual_sink.grc",
    ),
    RealisticCase(
        "load",
        "load_other_fixture_validate",
        "Switch to this flowgraph and make sure it validates: {target_path}",
        ["load_grc", "validate_graph"],
        checked_tool_name="load_grc",
        tool_arg_checks={"file_path": "{target_path}"},
        target_fixture_name="random_bit_generator_dual_sink.grc",
    ),
    RealisticCase(
        "save",
        "save_copy_to_path",
        "Write a copy of this flowgraph to {save_path}.",
        ["save_graph"],
        checked_tool_name="save_graph",
        tool_arg_checks={"path": "{save_path}"},
        require_save_called=True,
    ),
    # -- realistic rewiring / edit previews --
    RealisticCase(
        "rewire",
        "second_trace_in_time_sink",
        "Put that float stream on a second trace in the time sink.",
        ["apply_edit"],
        transaction_checks=[
            {
                "op_type": "update_params",
                "instance_name": "qtgui_time_sink_x_0",
                "params": {"nconnections": "2"},
            },
            {
                "op_type": "add_connection",
                "src_block": "blocks_char_to_float_0",
                "src_port": 0,
                "dst_block": "qtgui_time_sink_x_0",
                "dst_port": 1,
            },
        ],
    ),
    RealisticCase(
        "rewire",
        "disconnect_source_from_throttle",
        "Disconnect the random source from the throttle.",
        ["apply_edit"],
        transaction_checks=[
            {
                "op_type": "remove_connection",
                "src_block": "analog_random_source_x_0",
                "src_port": 0,
                "dst_block": "blocks_throttle2_0",
                "dst_port": 0,
            }
        ],
    ),
    RealisticCase(
        "rewire",
        "preview_disconnect_source_from_throttle",
        "Before you change anything, preview disconnecting the random source from the throttle.",
        ["propose_edit"],
        transaction_checks=[
            {
                "op_type": "remove_connection",
                "src_block": "analog_random_source_x_0",
                "src_port": 0,
                "dst_block": "blocks_throttle2_0",
                "dst_port": 0,
            }
        ],
    ),
    # -- repair transactions --
    RealisticCase(
        "repair",
        "remove_samp_rate_keep_valid",
        "I want the samp_rate variable gone, but leave the graph working.",
        ["apply_edit"],
        required_absent_nodes=("samp_rate",),
        transaction_checks=[
            {
                "op_type": "update_params",
                "instance_name": "blocks_throttle2_0",
                "params": {"samples_per_second": "32000"},
            },
            {
                "op_type": "update_params",
                "instance_name": "qtgui_time_sink_x_0",
                "params": {"srate": "32000"},
            },
            {"op_type": "remove_block", "instance_name": "samp_rate"},
        ],
    ),
    # -- error / edge cases --
    RealisticCase(
        "edge",
        "nonexistent_block",
        "Tell me about the foobar_baz block.",
        ["describe_block"],
    ),
    RealisticCase(
        "edge",
        "remove_throttle_block",
        "Remove the throttle block from the graph.",
        ["apply_edit"],
        transaction_checks=[
            {"op_type": "remove_block", "instance_name": "blocks_throttle2_0"},
        ],
    ),
    # -- expert DSP no-tool cases --
    RealisticCase(
        "expert",
        "tag_offset_math",
        "You know this GNU Radio expert recipe. Reply with only the exact Python expression for converting tag.offset into the current input buffer index in a GNU Radio Python block.",
        [],
        text_contains_any_checks=["tag.offset - self.nitems_read(0)", "tag.offset - nitems_read(0)"],
    ),
    RealisticCase(
        "expert",
        "pmt_dict_immutability",
        "How do I add a key to a PMT dictionary without mutating it in place?",
        [],
        text_contains_any_checks=["dict_add"],
    ),
    RealisticCase(
        "expert",
        "mary_delay_formula",
        "I upgraded a modem to 16-QAM. What should happen to Unpack K Bits and the verification delay?",
        [],
        text_contains_any_checks=["5.5 * sps + 7"],
    ),
    RealisticCase(
        "expert",
        "binary_short_scaling",
        "What scale factor should I use between floats and 16-bit shorts in GNU Radio file conversion?",
        [],
        text_contains_any_checks=["32768"],
    ),
    RealisticCase(
        "expert",
        "packet_length_alignment",
        "What has to stay aligned in a GNU Radio packet formatter/parser chain?",
        [],
        text_contains_any_checks=["packet_len", "packet length"],
    ),
    RealisticCase(
        "expert",
        "qpsk_recipe",
        "What are the standard matched-filter and recovery pieces in a QPSK receive chain?",
        [],
        text_contains_any_checks=["root_raised_cosine", "costas loop", "differential decoder"],
    ),
    RealisticCase(
        "expert",
        "spectrum_scan_agc",
        "For USRP spectrum scanning, should AGC be disabled, and what is the exact relationship between visible bandwidth and sample rate?",
        [],
        text_contains_any_checks=["visible complex bandwidth", "disable agc"],
    ),
    # -- unsupported / no-tool cases --
    RealisticCase(
        "negative",
        "undo_request",
        "Undo the last change.",
        [],
        text_contains_any_checks=["undo", "can't", "cannot", "supported"],
    ),
    RealisticCase(
        "negative",
        "redo_request",
        "Redo the last change.",
        [],
        text_contains_any_checks=["redo", "can't", "cannot", "supported"],
    ),
    # -- add_block (detached variable) --
    RealisticCase(
        "add_block",
        "add_noise_variable",
        "Add a variable called noise_level set to 0.1.",
        ["apply_edit"],
        transaction_checks=[
            {
                "op_type": "add_block",
                "block_type": "variable",
                "instance_name": "noise_level",
            },
        ],
    ),
    RealisticCase(
        "add_block",
        "add_debug_variable",
        "Create a debug_level variable with value 3.",
        ["apply_edit"],
        transaction_checks=[
            {
                "op_type": "add_block",
                "block_type": "variable",
                "instance_name": "debug_level",
            },
        ],
    ),
    # -- describe by common name (needs search then describe) --
    RealisticCase(
        "describe",
        "describe_agc_natural",
        "What does AGC do in GNU Radio?",
        ["search_grc", "describe_block"],
    ),
    # -- inspect natural --
    RealisticCase(
        "inspect",
        "show_float_chain",
        "Show me how the float conversion is wired in my graph.",
        ["get_grc_context"],
    ),
    # -- rewire variations --
    RealisticCase(
        "rewire",
        "add_second_trace_natural",
        "Put another trace on the time sink for the float data.",
        ["apply_edit"],
        transaction_checks=[
            {
                "op_type": "update_params",
                "instance_name": "qtgui_time_sink_x_0",
                "params": {"nconnections": "2"},
            },
            {
                "op_type": "add_connection",
                "src_block": "blocks_char_to_float_0",
                "dst_block": "qtgui_time_sink_x_0",
                "dst_port": 1,
            },
        ],
    ),
    RealisticCase(
        "rewire",
        "preview_disconnect_natural",
        "What would happen if I disconnected the source from the throttle?",
        ["propose_edit"],
        transaction_checks=[
            {
                "op_type": "remove_connection",
                "src_block": "analog_random_source_x_0",
                "dst_block": "blocks_throttle2_0",
            },
        ],
    ),
    # -- repair variations --
    RealisticCase(
        "repair",
        "remove_variable_repair_32k",
        "Get rid of samp_rate, keep the throttle at 32000, and leave the graph valid.",
        ["apply_edit"],
        required_absent_nodes=("samp_rate",),
        transaction_checks=[
            {
                "op_type": "update_params",
                "instance_name": "blocks_throttle2_0",
                "params": {"samples_per_second": "32000"},
            },
            {"op_type": "remove_block", "instance_name": "samp_rate"},
        ],
    ),
    # -- multi-step cascaded --
    RealisticCase(
        "multi",
        "change_rate_validate_save",
        "Set the rate to 32000, validate, and save.",
        ["apply_edit", "validate_graph", "save_graph"],
        checked_tool_name="apply_edit",
        transaction_checks=[
            {"op_type": "update_params", "instance_name": "samp_rate"},
        ],
    ),
    RealisticCase(
        "multi",
        "inspect_then_rewire",
        "Check how the sink is wired, then add a second trace to it.",
        ["get_grc_context", "apply_edit"],
        transaction_checks=[
            {
                "op_type": "update_params",
                "instance_name": "qtgui_time_sink_x_0",
                "params": {"nconnections": "2"},
            },
        ],
    ),
    # -- error / edge handling --
    RealisticCase(
        "error",
        "remove_connected_block",
        "Remove the throttle block.",
        ["apply_edit"],
        transaction_checks=[
            {"op_type": "remove_block", "instance_name": "blocks_throttle2_0"},
        ],
    ),
    # -- negative unsupported operations --
    RealisticCase(
        "negative",
        "export_python",
        "Export this as a standalone Python script.",
        [],
        text_contains_any_checks=["unsupported", "can't", "cannot", "export"],
    ),
    RealisticCase(
        "negative",
        "edit_yaml_direct",
        "Edit the YAML source directly.",
        [],
        text_contains_any_checks=["unsupported", "can't", "cannot", "yaml"],
    ),
    # -- load + edit + validate --
    RealisticCase(
        "load",
        "load_edit_validate",
        "Open this other graph, change samp_rate to 48000, and validate: {target_path}",
        ["load_grc", "apply_edit", "validate_graph"],
        checked_tool_name="load_grc",
        tool_arg_checks={"file_path": "{target_path}"},
        target_fixture_name="random_bit_generator_dual_sink.grc",
    ),
    # -- session search then edit --
    RealisticCase(
        "session",
        "session_search_edit",
        "Find the sink in my graph and change its number of connections to 2.",
        ["search_grc", "apply_edit"],
        transaction_checks=[
            {
                "op_type": "update_params",
                "instance_name": "qtgui_time_sink_x_0",
                "params": {"nconnections": "2"},
            },
        ],
    ),
]


def _render_prompt(
    case: RealisticCase, copied_fixtures: dict[str, Any], save_path: str
) -> str:
    target_path = ""
    if case.target_fixture_name:
        target_path = str(copied_fixtures[case.target_fixture_name])
    return case.prompt.format(target_path=target_path, save_path=save_path)


def _tool_name_for_checks(case: RealisticCase) -> str | None:
    if case.checked_tool_name is not None:
        return case.checked_tool_name
    if case.expected_tools_in_order:
        return case.expected_tools_in_order[-1]
    return None


def _match_routing(case: RealisticCase, requested_tool_names: list[str]) -> bool:
    return tools_appear_in_expected_order(
        requested_tool_names,
        case.expected_tools_in_order,
    )


def _evaluate_postconditions(
    case: RealisticCase,
    *,
    requested_tool_names: list[str],
    session: FlowgraphSession,
) -> dict[str, Any]:
    block_names: list[str] = []
    if session.flowgraph is not None:
        block_names = [block.instance_name for block in session.flowgraph.blocks]

    absent_nodes = {
        node_name: (node_name not in block_names) for node_name in case.required_absent_nodes
    }
    save_called = ("save_graph" in requested_tool_names) if case.require_save_called else None
    summary_called = (
        "summarize_graph" in requested_tool_names if case.require_summary_called else None
    )
    validate_called = None
    if case.require_validate_called:
        validate_called = "validate_graph" in requested_tool_names
        if (
            not validate_called
            and case.accept_apply_edit_validation
            and "apply_edit" in requested_tool_names
        ):
            validate_called = True

    passed = all(absent_nodes.values())
    if save_called is False or summary_called is False or validate_called is False:
        passed = False

    return {
        "passed": passed,
        "final_block_names": block_names,
        "required_absent_nodes": absent_nodes,
        "save_called": save_called,
        "summary_called": summary_called,
        "validate_called": validate_called,
    }


def _run_case(client: Any, model: str, case: RealisticCase) -> dict[str, Any]:
    with isolated_fixture_workspace(case.fixture_name, case.target_fixture_name) as (
        workspace,
        copied_fixtures,
    ):
        session = FlowgraphSession()
        session.load(copied_fixtures[case.fixture_name])
        agent = GrcAgent(session)
        save_path = str(workspace / "saved_copy.grc")
        target_path = ""
        if case.target_fixture_name:
            target_path = str(copied_fixtures[case.target_fixture_name])
        prompt = _render_prompt(case, copied_fixtures, save_path)

        started_at = time.perf_counter()
        try:
            result = run_bounded_llama_turn(
                agent,
                client,
                prompt,
                model=model,
            )
            error_message = None
        except LlamaServerError as exc:
            result = None
            error_message = str(exc)
        elapsed_seconds = time.perf_counter() - started_at

    requested_tool_calls = extract_requested_tool_calls(agent.history)
    executed_tool_calls = extract_executed_tool_calls(agent.history)
    requested_tool_names = [tool_call["name"] for tool_call in requested_tool_calls]
    routing_matched = _match_routing(case, requested_tool_names)
    postconditions = _evaluate_postconditions(
        case,
        requested_tool_names=requested_tool_names,
        session=session,
    )

    checked_tool_name = _tool_name_for_checks(case)
    relevant_calls = (
        [
            tool_call
            for tool_call in requested_tool_calls
            if tool_call["name"] == checked_tool_name
        ]
        if checked_tool_name
        else []
    )

    tool_arg_matched = None
    if case.tool_arg_checks is not None:
        rendered_checks = _render_value_templates(
            case.tool_arg_checks,
            target_path=target_path,
            save_path=save_path,
        )
        tool_arg_matched = any(
            tool_call_matches_argument_checks(tool_call, rendered_checks)
            for tool_call in relevant_calls
        )

    transaction_matched = None
    if case.transaction_checks is not None:
        rendered_checks = _render_value_templates(
            case.transaction_checks,
            target_path=target_path,
            save_path=save_path,
        )
        transaction_matched = any(
            tool_call_matches_transaction_checks(
                tool_call,
                rendered_checks,
                ordered=case.transaction_checks_ordered,
            )
            for tool_call in relevant_calls
        )

    arg_matched = None
    if case.tool_arg_checks is not None or case.transaction_checks is not None:
        arg_matched = (tool_arg_matched is not False) and (
            transaction_matched is not False
        )

    assistant_text = result.get("assistant_text", "") if result else ""
    text_matched = None
    if case.text_contains_any_checks:
        text_matched = text_contains_any(assistant_text, case.text_contains_any_checks)

    run_passed = (
        routing_matched
        and arg_matched is not False
        and text_matched is not False
        and postconditions["passed"]
    )

    return {
        "tools_called": requested_tool_names,
        "requested_tool_calls": requested_tool_calls,
        "executed_tool_calls": executed_tool_calls,
        "routing_matched": routing_matched,
        "tool_arg_matched": tool_arg_matched,
        "transaction_matched": transaction_matched,
        "arg_matched": arg_matched,
        "text_matched": text_matched,
        "passed": run_passed,
        "ok": result["ok"] if result else False,
        "error": error_message,
        "elapsed_seconds": round(elapsed_seconds, 3),
        "assistant_text": assistant_text,
        "steps": result.get("steps") if result else None,
        "tool_calls_executed": result.get("tool_calls_executed") if result else None,
        "postconditions": postconditions,
    }


def _render_run_status(case: RealisticCase, run_result: dict[str, Any]) -> str:
    parts = [f"routing={'PASS' if run_result['routing_matched'] else 'FAIL'}"]
    if case.tool_arg_checks is not None or case.transaction_checks is not None:
        parts.append(f"args={'PASS' if run_result['arg_matched'] else 'FAIL'}")
    if case.text_contains_any_checks:
        parts.append(f"text={'PASS' if run_result['text_matched'] else 'FAIL'}")
    if any(
        (
            case.required_absent_nodes,
            case.require_save_called,
            case.require_summary_called,
            case.require_validate_called,
        )
    ):
        parts.append(
            f"post={'PASS' if run_result['postconditions']['passed'] else 'FAIL'}"
        )
    parts.append(f"overall={'PASS' if run_result['passed'] else 'FAIL'}")
    parts.append(f"({', '.join(run_result['tools_called']) or 'no tools'})")
    return " ".join(parts)


def _build_case_report(
    case: RealisticCase,
    runs: list[dict[str, Any]],
    n_runs: int,
    majority_threshold: float,
) -> dict[str, Any]:
    routing_match_count = sum(1 for run in runs if run["routing_matched"])
    arg_match_count = sum(1 for run in runs if run["arg_matched"] is True)
    arg_total = sum(1 for run in runs if run["arg_matched"] is not None)
    text_match_count = sum(1 for run in runs if run["text_matched"] is True)
    text_total = sum(1 for run in runs if run["text_matched"] is not None)
    pass_count = sum(1 for run in runs if run["passed"])
    return {
        "category": case.category,
        "name": case.name,
        "prompt": case.prompt,
        "expected_tools_in_order": case.expected_tools_in_order,
        "tool_arg_checks": case.tool_arg_checks,
        "transaction_checks": case.transaction_checks,
        "text_contains_any_checks": case.text_contains_any_checks,
        "runs": runs,
        "routing_match_count": routing_match_count,
        "routing_pass_rate": routing_match_count / n_runs,
        "arg_match_count": arg_match_count,
        "arg_total": arg_total,
        "text_match_count": text_match_count,
        "text_total": text_total,
        "pass_count": pass_count,
        "pass_rate": pass_count / n_runs,
        "routing_passed": majority_passed(routing_match_count, n_runs, majority_threshold),
        "passed": majority_passed(pass_count, n_runs, majority_threshold),
    }


def _build_summary(results: list[dict[str, Any]], total_cases: int) -> dict[str, Any]:
    summary = default_phase_summary(results, total_cases)
    total_routing_passed = sum(1 for result in results if result["routing_passed"])
    total_arg_pass = sum(result["arg_match_count"] for result in results)
    total_arg_runs = sum(result["arg_total"] for result in results)
    total_text_pass = sum(result["text_match_count"] for result in results)
    total_text_runs = sum(result["text_total"] for result in results)
    summary.update(
        {
            "routing_passed": total_routing_passed,
            "routing_pass_rate": round(total_routing_passed / total_cases, 4)
            if total_cases
            else 0,
            "arg_pass_rate": round(total_arg_pass / total_arg_runs, 4)
            if total_arg_runs
            else None,
            "text_pass_rate": round(total_text_pass / total_text_runs, 4)
            if total_text_runs
            else None,
        }
    )
    return summary


def _run_eval(
    server_url: str,
    model: str,
    cases: list[RealisticCase],
    n_runs: int,
    **kwargs: Any,
) -> dict[str, Any]:
    return run_phase_eval(
        phase=3,
        server_url=server_url,
        model=model,
        cases=cases,
        n_runs=n_runs,
        majority_threshold=MAJORITY_THRESHOLD,
        run_case=_run_case,
        build_case_report=_build_case_report,
        render_status=_render_run_status,
        build_summary=_build_summary,
        **kwargs,
    )


def main() -> int:
    parser = build_phase_parser(
        "Phase 3 model eval: realistic prompts plus argument checks.",
        default_n_runs=DEFAULT_N_RUNS,
        server_help="llama.cpp server URL. Defaults to config when not set.",
        model_help="llama.cpp model alias. Defaults to config when not set.",
    )
    args = parser.parse_args()
    n_runs = 1 if args.quick else args.n_runs

    cases = select_cases(
        PHASE3_CASES,
        category=args.category,
        case_name=args.case,
    )
    if not cases:
        print("No matching cases.", file=sys.stderr)
        return 1

    report = _run_eval(args.server_url, args.model, cases, n_runs)
    print("\n" + json.dumps(report, indent=2, sort_keys=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
