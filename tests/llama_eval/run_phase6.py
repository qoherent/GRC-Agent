#!/usr/bin/env python3
"""Phase 6 model evaluation: compound workflows and multi-turn depth."""

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
    executed_tool_calls_since as _executed_tool_calls_since,
    isolated_fixture_workspace,
    majority_passed,
    render_prompt as _render_prompt,
    render_value_templates as _render_value_templates,
    requested_tool_calls_since as _requested_tool_calls_since,
    run_phase_eval,
    select_cases,
    text_contains_any,
    tool_call_matches_argument_checks,
    tool_call_matches_transaction_checks,
    tools_appear_in_expected_order,
)

DEFAULT_N_RUNS = 3
MAJORITY_THRESHOLD = 0.5
SECOND_FIXTURE = "random_bit_generator_dual_sink.grc"


@dataclass(frozen=True)
class TurnSpec:
    prompt: str
    expected_tools_in_order: list[str] = field(default_factory=list)
    checked_tool_name: str | None = None
    tool_arg_checks: dict[str, Any] | None = None
    transaction_checks: list[dict[str, Any]] | None = None
    transaction_checks_ordered: bool = True
    text_contains_any_checks: list[str] | None = None


@dataclass(frozen=True)
class MultiTurnCase:
    category: str
    name: str
    turns: list[TurnSpec]
    required_absent_nodes: tuple[str, ...] = ()
    accept_apply_edit_validation: bool = False
    fixture_name: str = DEFAULT_FIXTURE_NAME
    target_fixture_name: str | None = None
    description: str = ""


PHASE6_CASES: list[MultiTurnCase] = [
    # ── full_pipeline ──────────────────────────────────────────────────────
    # 5-turn: find → describe → edit → validate → save
    MultiTurnCase(
        "full_pipeline",
        "find_fir_describe_edit_validate_save",
        [
            TurnSpec(
                "Find a FIR filter block in the catalog.",
                ["search_grc"],
            ),
            TurnSpec(
                "Describe the first result.",
                ["describe_block"],
            ),
            TurnSpec(
                "Now set samp_rate to 44100.",
                ["apply_edit"],
                transaction_checks=[
                    {"op_type": "update_params", "instance_name": "samp_rate"},
                ],
            ),
            TurnSpec(
                "Validate the graph.",
                ["validate_graph"],
            ),
            TurnSpec(
                "Save it.",
                ["save_graph"],
            ),
        ],
    ),
    MultiTurnCase(
        "full_pipeline",
        "find_agc_describe_edit_save",
        [
            TurnSpec(
                "Find an AGC block in the catalog.",
                ["search_grc"],
            ),
            TurnSpec(
                "Tell me about that block.",
                ["describe_block"],
            ),
            TurnSpec(
                "Set samp_rate to 48000.",
                ["apply_edit"],
                transaction_checks=[
                    {"op_type": "update_params", "instance_name": "samp_rate"},
                ],
            ),
            TurnSpec(
                "Validate and save.",
                ["validate_graph", "save_graph"],
            ),
        ],
    ),
    MultiTurnCase(
        "full_pipeline",
        "find_costas_describe_apply_validate",
        [
            TurnSpec(
                "Find a Costas loop block in the catalog.",
                ["search_grc"],
            ),
            TurnSpec(
                "What parameters does it have?",
                ["describe_block"],
            ),
            TurnSpec(
                "Slow the sample rate to 8000.",
                ["apply_edit"],
                transaction_checks=[
                    {"op_type": "update_params", "instance_name": "samp_rate"},
                ],
            ),
            TurnSpec(
                "Validate it.",
                ["validate_graph"],
            ),
        ],
    ),
    MultiTurnCase(
        "full_pipeline",
        "find_scrambler_pipeline",
        [
            TurnSpec(
                "Look up a scrambler block in the catalog.",
                ["search_grc"],
            ),
            TurnSpec(
                "Tell me about it.",
                ["describe_block"],
            ),
            TurnSpec(
                "Set samp_rate to 8000.",
                ["apply_edit"],
                transaction_checks=[
                    {"op_type": "update_params", "instance_name": "samp_rate"},
                ],
            ),
            TurnSpec(
                "Validate.",
                ["validate_graph"],
            ),
            TurnSpec(
                "Write it out.",
                ["save_graph"],
            ),
        ],
    ),
    # ── rewire_complex ─────────────────────────────────────────────────────
    # inspect → edit (rewire or rate) → validate → save
    MultiTurnCase(
        "rewire_complex",
        "inspect_add_trace_validate_save",
        [
            TurnSpec(
                "Check how the sink is wired.",
                ["get_grc_context"],
            ),
            TurnSpec(
                "Add a second trace to the time sink.",
                ["apply_edit"],
                transaction_checks=[
                    {"op_type": "update_params", "instance_name": "qtgui_time_sink_x_0"},
                    {"op_type": "add_connection"},
                ],
                transaction_checks_ordered=True,
            ),
            TurnSpec(
                "Validate the change.",
                ["validate_graph"],
            ),
            TurnSpec(
                "Save it.",
                ["save_graph"],
            ),
        ],
    ),
    MultiTurnCase(
        "rewire_complex",
        "summarize_then_trace_validate",
        [
            TurnSpec(
                "What's in my graph?",
                ["summarize_graph"],
            ),
            TurnSpec(
                "Add a second trace to the time sink.",
                ["apply_edit"],
                transaction_checks=[
                    {"op_type": "update_params", "instance_name": "qtgui_time_sink_x_0"},
                    {"op_type": "add_connection"},
                ],
                transaction_checks_ordered=True,
            ),
            TurnSpec(
                "Validate.",
                ["validate_graph"],
            ),
            TurnSpec(
                "Write it out.",
                ["save_graph"],
            ),
        ],
    ),
    MultiTurnCase(
        "rewire_complex",
        "context_rate_validate",
        [
            TurnSpec(
                "Show me what surrounds samp_rate in the graph.",
                ["get_grc_context"],
            ),
            TurnSpec(
                "Change samp_rate to 96000.",
                ["apply_edit"],
                transaction_checks=[
                    {"op_type": "update_params", "instance_name": "samp_rate"},
                ],
            ),
            TurnSpec(
                "Validate.",
                ["validate_graph"],
            ),
        ],
    ),
    MultiTurnCase(
        "rewire_complex",
        "inspect_edit_save",
        [
            TurnSpec(
                "Show me the throttle's connections.",
                ["get_grc_context"],
            ),
            TurnSpec(
                "Update samp_rate to 48000.",
                ["apply_edit"],
                transaction_checks=[
                    {"op_type": "update_params", "instance_name": "samp_rate"},
                ],
            ),
            TurnSpec(
                "Validate and save.",
                ["validate_graph", "save_graph"],
            ),
        ],
    ),
    # ── multi_block_edit ───────────────────────────────────────────────────
    # coordinated edits with state checks between turns
    MultiTurnCase(
        "multi_block_edit",
        "add_var_summarize_edit_validate",
        [
            TurnSpec(
                "Add a variable called cutoff with value 1000.",
                ["apply_edit"],
                transaction_checks=[
                    {"op_type": "add_block", "block_type": "variable"},
                ],
            ),
            TurnSpec(
                "Give me a graph summary.",
                ["summarize_graph"],
            ),
            TurnSpec(
                "Now update samp_rate to 44100.",
                ["apply_edit"],
                transaction_checks=[
                    {"op_type": "update_params", "instance_name": "samp_rate"},
                ],
            ),
            TurnSpec(
                "Validate.",
                ["validate_graph"],
            ),
        ],
    ),
    MultiTurnCase(
        "multi_block_edit",
        "edit_summarize_trace_validate",
        [
            TurnSpec(
                "Change samp_rate to 44100.",
                ["apply_edit"],
                transaction_checks=[
                    {"op_type": "update_params", "instance_name": "samp_rate"},
                ],
            ),
            TurnSpec(
                "Summarize the current graph.",
                ["summarize_graph"],
            ),
            TurnSpec(
                "Add a second trace to the time sink.",
                ["apply_edit"],
                transaction_checks=[
                    {"op_type": "update_params", "instance_name": "qtgui_time_sink_x_0"},
                    {"op_type": "add_connection"},
                ],
                transaction_checks_ordered=True,
            ),
            TurnSpec(
                "Validate.",
                ["validate_graph"],
            ),
        ],
    ),
    MultiTurnCase(
        "multi_block_edit",
        "two_rate_edits_then_var",
        [
            TurnSpec(
                "Set samp_rate to 44100.",
                ["apply_edit"],
                transaction_checks=[
                    {"op_type": "update_params", "instance_name": "samp_rate"},
                ],
            ),
            TurnSpec(
                "Validate the graph.",
                ["validate_graph"],
            ),
            TurnSpec(
                "Now add a variable called new_rate with value 32000.",
                ["apply_edit"],
                transaction_checks=[
                    {"op_type": "add_block", "block_type": "variable"},
                ],
            ),
        ],
    ),
    MultiTurnCase(
        "multi_block_edit",
        "summarize_edit_summarize_validate",
        [
            TurnSpec(
                "Give me an overview of the graph.",
                ["summarize_graph"],
            ),
            TurnSpec(
                "Set samp_rate to 96000.",
                ["apply_edit"],
                transaction_checks=[
                    {"op_type": "update_params", "instance_name": "samp_rate"},
                ],
            ),
            TurnSpec(
                "Give me the updated overview.",
                ["summarize_graph"],
            ),
            TurnSpec(
                "Validate.",
                ["validate_graph"],
            ),
        ],
    ),
    # ── exploration_driven ─────────────────────────────────────────────────
    # vague intent → search → describe → apply
    MultiTurnCase(
        "exploration_driven",
        "carrier_recovery_search_apply",
        [
            TurnSpec(
                "I need a carrier recovery block for my signal chain.",
                ["search_grc"],
            ),
            TurnSpec(
                "Tell me about the first result.",
                ["describe_block"],
            ),
            TurnSpec(
                "For now just update samp_rate to 48000.",
                ["apply_edit"],
                transaction_checks=[
                    {"op_type": "update_params", "instance_name": "samp_rate"},
                ],
            ),
        ],
    ),
    MultiTurnCase(
        "exploration_driven",
        "channel_filter_explore_edit",
        [
            TurnSpec(
                "What channelizing or filtering blocks are available?",
                ["search_grc"],
            ),
            TurnSpec(
                "Describe the first result.",
                ["describe_block"],
            ),
            TurnSpec(
                "OK, just update samp_rate to 44100 for now.",
                ["apply_edit"],
                transaction_checks=[
                    {"op_type": "update_params", "instance_name": "samp_rate"},
                ],
            ),
            TurnSpec(
                "Validate.",
                ["validate_graph"],
            ),
        ],
    ),
    MultiTurnCase(
        "exploration_driven",
        "psk_explore_then_edit",
        [
            TurnSpec(
                "I want PSK modulation blocks.",
                ["search_grc"],
            ),
            TurnSpec(
                "Tell me about the first result.",
                ["describe_block"],
            ),
            TurnSpec(
                "Update samp_rate to 48000 and validate.",
                ["apply_edit", "validate_graph"],
                checked_tool_name="apply_edit",
                transaction_checks=[
                    {"op_type": "update_params", "instance_name": "samp_rate"},
                ],
            ),
        ],
    ),
    MultiTurnCase(
        "exploration_driven",
        "ofdm_research_then_validate",
        [
            TurnSpec(
                "Show me OFDM-related blocks.",
                ["search_grc"],
            ),
            TurnSpec(
                "Tell me about the first one.",
                ["describe_block"],
            ),
            TurnSpec(
                "Give me a summary of my current graph.",
                ["summarize_graph"],
            ),
        ],
    ),
    # ── cross_session ──────────────────────────────────────────────────────
    # load fixture A → do work → load fixture B → inspect/edit
    MultiTurnCase(
        "cross_session",
        "load_new_summarize_validate",
        target_fixture_name=SECOND_FIXTURE,
        turns=[
            TurnSpec(
                "Load this file: {target_path}",
                ["load_grc"],
                tool_arg_checks={"file_path": "{target_path}"},
            ),
            TurnSpec(
                "Summarize the new graph.",
                ["summarize_graph"],
            ),
            TurnSpec(
                "Validate it.",
                ["validate_graph"],
            ),
        ],
    ),
    MultiTurnCase(
        "cross_session",
        "edit_then_load_summarize",
        target_fixture_name=SECOND_FIXTURE,
        turns=[
            TurnSpec(
                "Update samp_rate to 44100.",
                ["apply_edit"],
                transaction_checks=[
                    {"op_type": "update_params", "instance_name": "samp_rate"},
                ],
            ),
            TurnSpec(
                "Now load {target_path}.",
                ["load_grc"],
                tool_arg_checks={"file_path": "{target_path}"},
            ),
            TurnSpec(
                "Give me a summary of the newly loaded graph.",
                ["summarize_graph"],
            ),
        ],
    ),
    MultiTurnCase(
        "cross_session",
        "summarize_then_load_validate",
        target_fixture_name=SECOND_FIXTURE,
        turns=[
            TurnSpec(
                "Give me a summary of the current graph.",
                ["summarize_graph"],
            ),
            TurnSpec(
                "Load {target_path} instead.",
                ["load_grc"],
                tool_arg_checks={"file_path": "{target_path}"},
            ),
            TurnSpec(
                "Validate it.",
                ["validate_graph"],
            ),
        ],
    ),
    MultiTurnCase(
        "cross_session",
        "load_and_summarize_validate",
        target_fixture_name=SECOND_FIXTURE,
        turns=[
            TurnSpec(
                "Load {target_path}.",
                ["load_grc"],
                tool_arg_checks={"file_path": "{target_path}"},
            ),
            TurnSpec(
                "Summarize it.",
                ["summarize_graph"],
            ),
            TurnSpec(
                "Validate it.",
                ["validate_graph"],
            ),
        ],
    ),
    # ── undo_workaround ────────────────────────────────────────────────────
    # edit → reverse the same edit
    MultiTurnCase(
        "undo_workaround",
        "set_rate_then_revert",
        [
            TurnSpec(
                "Change samp_rate to 44100.",
                ["apply_edit"],
                transaction_checks=[
                    {"op_type": "update_params", "instance_name": "samp_rate"},
                ],
            ),
            TurnSpec(
                "Revert that — set samp_rate back to 32000.",
                ["apply_edit"],
                transaction_checks=[
                    {"op_type": "update_params", "instance_name": "samp_rate"},
                ],
            ),
            TurnSpec(
                "Validate.",
                ["validate_graph"],
            ),
        ],
    ),
    MultiTurnCase(
        "undo_workaround",
        "add_var_then_remove",
        [
            TurnSpec(
                "Add a variable called tmp_flag with value 1.",
                ["apply_edit"],
                transaction_checks=[
                    {"op_type": "add_block", "block_type": "variable"},
                ],
            ),
            TurnSpec(
                "Remove that variable.",
                ["apply_edit"],
                transaction_checks=[
                    {"op_type": "remove_block"},
                ],
            ),
            TurnSpec(
                "Validate.",
                ["validate_graph"],
            ),
        ],
    ),
    MultiTurnCase(
        "undo_workaround",
        "bump_rate_revert_save",
        [
            TurnSpec(
                "Set samp_rate to 96000.",
                ["apply_edit"],
                transaction_checks=[
                    {"op_type": "update_params", "instance_name": "samp_rate"},
                ],
            ),
            TurnSpec(
                "Undo that — change samp_rate back to 32000.",
                ["apply_edit"],
                transaction_checks=[
                    {"op_type": "update_params", "instance_name": "samp_rate"},
                ],
            ),
            TurnSpec(
                "Validate and save.",
                ["validate_graph", "save_graph"],
            ),
        ],
    ),
    MultiTurnCase(
        "undo_workaround",
        "rate_change_then_different",
        [
            TurnSpec(
                "Set samp_rate to 44100.",
                ["apply_edit"],
                transaction_checks=[
                    {"op_type": "update_params", "instance_name": "samp_rate"},
                ],
            ),
            TurnSpec(
                "Actually 48000 is better — change it to 48000.",
                ["apply_edit"],
                transaction_checks=[
                    {"op_type": "update_params", "instance_name": "samp_rate"},
                ],
            ),
        ],
    ),
    # ── backtrack ──────────────────────────────────────────────────────────
    # apply → validate or inspect → change approach
    MultiTurnCase(
        "backtrack",
        "rate_validate_redecide",
        [
            TurnSpec(
                "Set samp_rate to 100.",
                ["apply_edit"],
                transaction_checks=[
                    {"op_type": "update_params", "instance_name": "samp_rate"},
                ],
            ),
            TurnSpec(
                "Validate.",
                ["validate_graph"],
            ),
            TurnSpec(
                "Too slow — set samp_rate to 44100 instead.",
                ["apply_edit"],
                transaction_checks=[
                    {"op_type": "update_params", "instance_name": "samp_rate"},
                ],
            ),
            TurnSpec(
                "Validate.",
                ["validate_graph"],
            ),
        ],
    ),
    MultiTurnCase(
        "backtrack",
        "add_trace_check_then_adjust_rate",
        [
            TurnSpec(
                "Add a second trace to the time sink.",
                ["apply_edit"],
                transaction_checks=[
                    {"op_type": "update_params", "instance_name": "qtgui_time_sink_x_0"},
                    {"op_type": "add_connection"},
                ],
                transaction_checks_ordered=True,
            ),
            TurnSpec(
                "Check the sink context.",
                ["get_grc_context"],
            ),
            TurnSpec(
                "Also update samp_rate to 44100.",
                ["apply_edit"],
                transaction_checks=[
                    {"op_type": "update_params", "instance_name": "samp_rate"},
                ],
            ),
            TurnSpec(
                "Validate.",
                ["validate_graph"],
            ),
        ],
    ),
    MultiTurnCase(
        "backtrack",
        "rate_change_retry",
        [
            TurnSpec(
                "Change samp_rate to 44100.",
                ["apply_edit"],
                transaction_checks=[
                    {"op_type": "update_params", "instance_name": "samp_rate"},
                ],
            ),
            TurnSpec(
                "Validate.",
                ["validate_graph"],
            ),
            TurnSpec(
                "I need 48000 not 44100 — update it.",
                ["apply_edit"],
                transaction_checks=[
                    {"op_type": "update_params", "instance_name": "samp_rate"},
                ],
            ),
            TurnSpec(
                "Validate and save.",
                ["validate_graph", "save_graph"],
            ),
        ],
    ),
    MultiTurnCase(
        "backtrack",
        "add_var_reconsider_validate_save",
        [
            TurnSpec(
                "Add a variable called offset with value 0.",
                ["apply_edit"],
                transaction_checks=[
                    {"op_type": "add_block", "block_type": "variable"},
                ],
            ),
            TurnSpec(
                "Actually remove it — I don't need it.",
                ["apply_edit"],
                transaction_checks=[
                    {"op_type": "remove_block"},
                ],
            ),
            TurnSpec(
                "Validate the graph.",
                ["validate_graph"],
            ),
            TurnSpec(
                "Save it.",
                ["save_graph"],
            ),
        ],
    ),
]


def _check_turn(
    turn_spec: TurnSpec,
    requested_tool_calls: list[dict[str, Any]],
    executed_tool_calls: list[dict[str, Any]],
    assistant_text: str,
    *,
    target_path: str,
    save_path: str,
) -> dict[str, Any]:
    requested_tool_names = [tc["name"] for tc in requested_tool_calls]
    routing_matched = tools_appear_in_expected_order(
        requested_tool_names, turn_spec.expected_tools_in_order
    )

    checked_tool_name = turn_spec.checked_tool_name
    if checked_tool_name is None and turn_spec.expected_tools_in_order:
        checked_tool_name = turn_spec.expected_tools_in_order[-1]

    relevant_calls = (
        [tc for tc in requested_tool_calls if tc["name"] == checked_tool_name]
        if checked_tool_name
        else []
    )
    relevant_executed_calls = (
        [tc for tc in executed_tool_calls if tc["name"] == checked_tool_name]
        if checked_tool_name
        else []
    )

    tool_arg_matched = None
    if turn_spec.tool_arg_checks is not None:
        rendered = _render_value_templates(
            turn_spec.tool_arg_checks, target_path=target_path, save_path=save_path
        )
        tool_arg_matched = any(
            tool_call_matches_argument_checks(tc, rendered) for tc in relevant_calls
        )

    transaction_matched = None
    if turn_spec.transaction_checks is not None:
        rendered = _render_value_templates(
            turn_spec.transaction_checks, target_path=target_path, save_path=save_path
        )
        transaction_matched = any(
            tool_call_matches_transaction_checks(
                tc, rendered, ordered=turn_spec.transaction_checks_ordered
            )
            for tc in relevant_calls + relevant_executed_calls
        )

    arg_matched = None
    if (
        turn_spec.tool_arg_checks is not None
        or turn_spec.transaction_checks is not None
    ):
        arg_matched = (tool_arg_matched is not False) and (
            transaction_matched is not False
        )

    text_matched = None
    if turn_spec.text_contains_any_checks:
        text_matched = text_contains_any(
            assistant_text, turn_spec.text_contains_any_checks
        )

    passed = routing_matched and arg_matched is not False and text_matched is not False
    return {
        "tools_called": requested_tool_names,
        "routing_matched": routing_matched,
        "tool_arg_matched": tool_arg_matched,
        "transaction_matched": transaction_matched,
        "arg_matched": arg_matched,
        "text_matched": text_matched,
        "passed": passed,
    }


def _run_case(
    client: Any,
    model: str,
    case: MultiTurnCase,
) -> dict[str, Any]:
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

        turn_results: list[dict[str, Any]] = []
        error_message: str | None = None
        ok = True
        case_started_at = time.perf_counter()
        all_requested_tool_names: list[str] = []

        for turn_index, turn_spec in enumerate(case.turns):
            prompt = _render_prompt(turn_spec.prompt, target_path, save_path)
            history_start = len(agent.history)
            started_at = time.perf_counter()
            try:
                result = run_bounded_llama_turn(agent, client, prompt, model=model)
                turn_error = None
            except LlamaServerError as exc:
                result = None
                turn_error = str(exc)
                ok = False
            elapsed = time.perf_counter() - started_at

            if result is None:
                turn_results.append(
                    {
                        "turn_index": turn_index,
                        "prompt": prompt,
                        "assistant_text": "",
                        "requested_tool_calls": [],
                        "ok": False,
                        "error": turn_error,
                        "tools_called": [],
                        "routing_matched": False,
                        "tool_arg_matched": None,
                        "transaction_matched": None,
                        "arg_matched": None,
                        "text_matched": None,
                        "passed": False,
                        "elapsed_seconds": round(elapsed, 3),
                    }
                )
                error_message = turn_error
                break

            requested_tool_calls = _requested_tool_calls_since(
                agent.history, history_start
            )
            executed_tool_calls = _executed_tool_calls_since(agent.history, history_start)
            all_requested_tool_names.extend(tc["name"] for tc in requested_tool_calls)
            assistant_text = result.get("assistant_text", "")
            turn_checks = _check_turn(
                turn_spec,
                requested_tool_calls,
                executed_tool_calls,
                assistant_text,
                target_path=target_path,
                save_path=save_path,
            )
            turn_results.append(
                {
                    "turn_index": turn_index,
                    "prompt": prompt,
                    "assistant_text": assistant_text,
                    "requested_tool_calls": requested_tool_calls,
                    "executed_tool_calls": executed_tool_calls,
                    "ok": result["ok"],
                    "error": None if result["ok"] else result.get("message"),
                    "elapsed_seconds": round(elapsed, 3),
                    "steps": result.get("steps"),
                    "tool_calls_executed": result.get("tool_calls_executed"),
                    **turn_checks,
                }
            )
            if not result["ok"]:
                ok = False
                error_message = result.get("message")
                break

        postconditions = _evaluate_case_postconditions(
            case,
            requested_tool_names=all_requested_tool_names,
            session=session,
        )

    return {
        "turn_results": turn_results,
        "all_turns_passed": all(tr.get("passed", False) for tr in turn_results)
        and postconditions["passed"],
        "ok": ok,
        "error": error_message,
        "elapsed_seconds": round(time.perf_counter() - case_started_at, 3),
        "postconditions": postconditions,
    }


def _evaluate_case_postconditions(
    case: MultiTurnCase,
    *,
    requested_tool_names: list[str],
    session: FlowgraphSession,
) -> dict[str, Any]:
    block_names: list[str] = []
    if session.flowgraph is not None:
        block_names = [block.instance_name for block in session.flowgraph.blocks]

    required_absent_nodes = {
        node_name: (node_name not in block_names) for node_name in case.required_absent_nodes
    }
    expected_tools = {
        tool_name
        for turn in case.turns
        for tool_name in turn.expected_tools_in_order
    }
    summary_called = (
        "summarize_graph" in requested_tool_names if "summarize_graph" in expected_tools else None
    )
    save_called = "save_graph" in requested_tool_names if "save_graph" in expected_tools else None
    validate_called = None
    if "validate_graph" in expected_tools:
        validate_called = "validate_graph" in requested_tool_names
        if (
            not validate_called
            and case.accept_apply_edit_validation
            and "apply_edit" in requested_tool_names
        ):
            validate_called = True

    passed = all(required_absent_nodes.values())
    if summary_called is False or save_called is False or validate_called is False:
        passed = False

    return {
        "passed": passed,
        "final_block_names": block_names,
        "required_absent_nodes": required_absent_nodes,
        "summary_called": summary_called,
        "save_called": save_called,
        "validate_called": validate_called,
        "requested_tool_names": requested_tool_names,
    }


def _render_run_status(case: MultiTurnCase, run_result: dict[str, Any]) -> str:
    n_turns = len(case.turns)
    n_passed = sum(
        1 for tr in run_result["turn_results"] if tr.get("passed", False)
    )
    status = "PASS" if run_result["all_turns_passed"] else "FAIL"
    return (
        f"{status} ({n_passed}/{n_turns} turns, "
        f"post={'PASS' if run_result['postconditions']['passed'] else 'FAIL'})"
    )


def _build_case_report(
    case: MultiTurnCase,
    runs: list[dict[str, Any]],
    n_runs: int,
    majority_threshold: float,
) -> dict[str, Any]:
    pass_count = sum(1 for run in runs if run["all_turns_passed"])
    per_turn_pass_counts: list[int] = [0] * len(case.turns)
    for run in runs:
        for turn_result in run.get("turn_results", []):
            idx = turn_result.get("turn_index", 0)
            if idx < len(per_turn_pass_counts) and turn_result.get("passed", False):
                per_turn_pass_counts[idx] += 1
    return {
        "category": case.category,
        "name": case.name,
        "n_turns": len(case.turns),
        "runs": runs,
        "pass_count": pass_count,
        "pass_rate": pass_count / n_runs,
        "passed": majority_passed(pass_count, n_runs, majority_threshold),
        "per_turn_pass_counts": per_turn_pass_counts,
    }


def _run_eval(
    server_url: str,
    model: str,
    cases: list[MultiTurnCase],
    n_runs: int,
    **kwargs: Any,
) -> dict[str, Any]:
    return run_phase_eval(
        phase=6,
        server_url=server_url,
        model=model,
        cases=cases,
        n_runs=n_runs,
        majority_threshold=MAJORITY_THRESHOLD,
        run_case=_run_case,
        build_case_report=_build_case_report,
        render_status=_render_run_status,
        **kwargs,
    )


def main() -> int:
    parser = build_phase_parser(
        "Phase 6 model eval: compound workflows and multi-turn depth.",
        default_n_runs=DEFAULT_N_RUNS,
        server_help="llama.cpp server URL. Defaults to config.",
        model_help="llama.cpp model alias. Defaults to config.",
    )
    args = parser.parse_args()
    n_runs = 1 if args.quick else args.n_runs

    cases = select_cases(
        PHASE6_CASES,
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
