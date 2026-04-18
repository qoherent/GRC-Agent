#!/usr/bin/env python3
"""Phase 6 model evaluation: compound workflows and multi-turn depth."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from grc_agent.agent import GrcAgent
from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.llama_server import LlamaServerError, run_bounded_llama_turn

from tests.llama_eval.harness import (
    DEFAULT_FIXTURE_NAME,
    build_client,
    ensure_llama_server,
    extract_requested_tool_calls,
    isolated_fixture_workspace,
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


def _render_prompt(prompt: str, target_path: str, save_path: str) -> str:
    return prompt.format(target_path=target_path, save_path=save_path)


def _render_value_templates(value: Any, *, target_path: str, save_path: str) -> Any:
    if isinstance(value, str):
        return value.format(target_path=target_path, save_path=save_path)
    if isinstance(value, dict):
        return {
            key: _render_value_templates(
                nested_value, target_path=target_path, save_path=save_path
            )
            for key, nested_value in value.items()
        }
    if isinstance(value, list):
        return [
            _render_value_templates(item, target_path=target_path, save_path=save_path)
            for item in value
        ]
    return value


def _requested_tool_calls_since(
    history: list[dict[str, Any]], start_index: int
) -> list[dict[str, Any]]:
    return extract_requested_tool_calls(history[start_index:])


def _check_turn(
    turn_spec: TurnSpec,
    requested_tool_calls: list[dict[str, Any]],
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
            for tc in relevant_calls
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
                        "error": turn_error,
                        "passed": False,
                        "tools_called": [],
                        "routing_matched": False,
                        "tool_arg_matched": None,
                        "transaction_matched": None,
                        "arg_matched": None,
                        "text_matched": None,
                        "elapsed_seconds": round(elapsed, 3),
                        "assistant_text": "",
                    }
                )
                break

            requested_tool_calls = _requested_tool_calls_since(
                agent.history, history_start
            )
            assistant_text = result.get("assistant_text", "")

            check = _check_turn(
                turn_spec,
                requested_tool_calls,
                assistant_text,
                target_path=target_path,
                save_path=save_path,
            )

            turn_results.append(
                {
                    "turn_index": turn_index,
                    "prompt": prompt,
                    "ok": result.get("ok", False),
                    "error": turn_error,
                    "elapsed_seconds": round(elapsed, 3),
                    "assistant_text": assistant_text,
                    **check,
                }
            )

            if not result.get("ok"):
                ok = False
                break

    return {
        "turn_results": turn_results,
        "ok": ok,
        "error": error_message,
        "all_turns_passed": all(tr.get("passed", False) for tr in turn_results),
    }


def _run_eval(
    server_url: str,
    model: str,
    cases: list[MultiTurnCase],
    n_runs: int,
) -> dict[str, Any]:
    resolved_url, resolved_model = ensure_llama_server(server_url, model)
    client = build_client(resolved_url)

    results = []
    total = len(cases) * n_runs
    done = 0

    for case in cases:
        runs = []
        for run_index in range(n_runs):
            done += 1
            print(
                f"[{done}/{total}] {case.category}/{case.name} run {run_index + 1}/{n_runs}",
                end="",
                flush=True,
            )
            run_result = _run_case(client, resolved_model, case)
            n_turns = len(case.turns)
            n_passed = sum(
                1 for tr in run_result["turn_results"] if tr.get("passed", False)
            )
            status = "PASS" if run_result["all_turns_passed"] else "FAIL"
            print(f" -> {status} ({n_passed}/{n_turns} turns)")
            runs.append(run_result)

        pass_count = sum(1 for run in runs if run["all_turns_passed"])
        passed = pass_count > n_runs * MAJORITY_THRESHOLD

        per_turn_pass_counts: list[int] = [0] * len(case.turns)
        for run in runs:
            for tr in run.get("turn_results", []):
                idx = tr.get("turn_index", 0)
                if idx < len(per_turn_pass_counts) and tr.get("passed", False):
                    per_turn_pass_counts[idx] += 1

        results.append(
            {
                "category": case.category,
                "name": case.name,
                "n_turns": len(case.turns),
                "runs": runs,
                "pass_count": pass_count,
                "pass_rate": pass_count / n_runs,
                "passed": passed,
                "per_turn_pass_counts": per_turn_pass_counts,
            }
        )

    by_category: dict[str, dict[str, int]] = {}
    for result in results:
        category = result["category"]
        if category not in by_category:
            by_category[category] = {"passed": 0, "total": 0}
        by_category[category]["total"] += 1
        if result["passed"]:
            by_category[category]["passed"] += 1

    total_passed = sum(1 for result in results if result["passed"])

    return {
        "phase": 6,
        "model": resolved_model,
        "temperature": client.temperature,
        "n_runs": n_runs,
        "majority_threshold": MAJORITY_THRESHOLD,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "cases": results,
        "summary": {
            "total": len(cases),
            "passed": total_passed,
            "pass_rate": round(total_passed / len(cases), 4) if cases else 0,
            "by_category": by_category,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Phase 6 model eval: compound workflows and multi-turn depth."
    )
    parser.add_argument(
        "--server-url",
        default=os.environ.get("GRC_AGENT_LIVE_LLAMA_URL"),
        help="llama.cpp server URL. Defaults to config.",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("GRC_AGENT_LIVE_LLAMA_MODEL"),
        help="llama.cpp model alias. Defaults to config.",
    )
    parser.add_argument(
        "--n-runs",
        type=int,
        default=DEFAULT_N_RUNS,
        help=f"Number of runs per case. Default: {DEFAULT_N_RUNS}.",
    )
    parser.add_argument(
        "--category",
        type=str,
        default=None,
        help="Run only cases in this category.",
    )
    parser.add_argument(
        "--case",
        type=str,
        default=None,
        help="Run only the case with this name.",
    )
    args = parser.parse_args()

    cases = list(PHASE6_CASES)
    if args.category:
        cases = [case for case in cases if case.category == args.category]
    if args.case:
        cases = [case for case in cases if case.name == args.case]
    if not cases:
        print("No matching cases.", file=sys.stderr)
        return 1

    report = _run_eval(args.server_url, args.model, cases, args.n_runs)
    print("\n" + json.dumps(report, indent=2, sort_keys=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
