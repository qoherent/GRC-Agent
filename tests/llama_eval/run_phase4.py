#!/usr/bin/env python3
"""Phase 4 model evaluation: multi-turn conversation continuity."""

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


PHASE4_CASES: list[MultiTurnCase] = [
    # ── follow_up_edit ──────────────────────────────────────────────────
    MultiTurnCase(
        "follow_up_edit",
        "rate_change_then_bump",
        [
            TurnSpec(
                "Change samp_rate to 48000.",
                ["apply_edit"],
                transaction_checks=[
                    {"op_type": "update_params", "instance_name": "samp_rate"},
                ],
            ),
            TurnSpec(
                "Now make it 96000.",
                ["apply_edit"],
                transaction_checks=[
                    {"op_type": "update_params", "instance_name": "samp_rate"},
                ],
            ),
        ],
    ),
    MultiTurnCase(
        "follow_up_edit",
        "rate_slow_then_slower",
        [
            TurnSpec(
                "Slow this down to 8k.",
                ["apply_edit"],
                transaction_checks=[
                    {"op_type": "update_params", "instance_name": "samp_rate"},
                ],
            ),
            TurnSpec(
                "Even slower, 4k.",
                ["apply_edit"],
                transaction_checks=[
                    {"op_type": "update_params", "instance_name": "samp_rate"},
                ],
            ),
        ],
    ),
    MultiTurnCase(
        "follow_up_edit",
        "edit_then_rewire",
        [
            TurnSpec(
                "Set samp_rate to 32000.",
                ["apply_edit"],
                transaction_checks=[
                    {"op_type": "update_params", "instance_name": "samp_rate"},
                ],
            ),
            TurnSpec(
                "Now disconnect the source from the throttle.",
                ["apply_edit"],
                transaction_checks=[
                    {"op_type": "remove_connection"},
                ],
            ),
        ],
    ),
    MultiTurnCase(
        "follow_up_edit",
        "add_var_then_change",
        [
            TurnSpec(
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
            TurnSpec(
                "Change noise_level to 0.05.",
                ["apply_edit"],
                transaction_checks=[
                    {"op_type": "update_params", "instance_name": "noise_level"},
                ],
            ),
        ],
    ),
    MultiTurnCase(
        "follow_up_edit",
        "edit_then_validate",
        [
            TurnSpec(
                "Change samp_rate to 22050.",
                ["apply_edit"],
                transaction_checks=[
                    {"op_type": "update_params", "instance_name": "samp_rate"},
                ],
            ),
            TurnSpec(
                "Validate the graph.",
                ["validate_graph"],
            ),
        ],
    ),
    MultiTurnCase(
        "follow_up_edit",
        "edit_validate_save",
        [
            TurnSpec(
                "Set the sample rate to 48000.",
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
    # ── inspect_then_act ────────────────────────────────────────────────
    MultiTurnCase(
        "inspect_then_act",
        "summarize_then_change_rate",
        [
            TurnSpec(
                "Give me a quick overview.",
                ["summarize_graph"],
            ),
            TurnSpec(
                "Change samp_rate to 32000.",
                ["apply_edit"],
                transaction_checks=[
                    {"op_type": "update_params", "instance_name": "samp_rate"},
                ],
            ),
        ],
    ),
    MultiTurnCase(
        "inspect_then_act",
        "context_then_edit",
        [
            TurnSpec(
                "Show me what's connected to samp_rate.",
                ["get_grc_context"],
            ),
            TurnSpec(
                "Update its value to 44100.",
                ["apply_edit"],
                transaction_checks=[
                    {"op_type": "update_params", "instance_name": "samp_rate"},
                ],
            ),
        ],
    ),
    MultiTurnCase(
        "inspect_then_act",
        "context_then_disconnect",
        [
            TurnSpec(
                "Show me the neighborhood around blocks_throttle2_0.",
                ["get_grc_context"],
            ),
            TurnSpec(
                "Disconnect the source from the throttle.",
                ["apply_edit"],
                transaction_checks=[
                    {"op_type": "remove_connection"},
                ],
            ),
        ],
    ),
    MultiTurnCase(
        "inspect_then_act",
        "summarize_then_add_variable",
        [
            TurnSpec(
                "What am I looking at?",
                ["summarize_graph"],
            ),
            TurnSpec(
                "Add a debug_level variable with value 3.",
                ["apply_edit"],
                transaction_checks=[
                    {
                        "op_type": "add_block",
                        "block_type": "variable",
                        "instance_name": "debug_level",
                    },
                ],
            ),
        ],
    ),
    MultiTurnCase(
        "inspect_then_act",
        "context_then_second_trace",
        [
            TurnSpec(
                "How is the time sink wired?",
                ["get_grc_context"],
            ),
            TurnSpec(
                "Add a second trace for the float stream.",
                ["apply_edit"],
                transaction_checks=[
                    {
                        "op_type": "update_params",
                        "instance_name": "qtgui_time_sink_x_0",
                        "params": {"nconnections": "2"},
                    },
                ],
            ),
        ],
    ),
    MultiTurnCase(
        "inspect_then_act",
        "summarize_then_validate",
        [
            TurnSpec(
                "What blocks are in this graph?",
                ["summarize_graph"],
            ),
            TurnSpec(
                "Is it valid?",
                ["validate_graph"],
            ),
        ],
    ),
    # ── search_then_navigate ────────────────────────────────────────────
    MultiTurnCase(
        "search_then_navigate",
        "find_agc_describe",
        [
            TurnSpec(
                "Find an AGC block.",
                ["search_grc"],
            ),
            TurnSpec(
                "Describe the block you found.",
                ["describe_block"],
            ),
        ],
    ),
    MultiTurnCase(
        "search_then_navigate",
        "find_head_describe",
        [
            TurnSpec(
                "Search for the Head block.",
                ["search_grc"],
            ),
            TurnSpec(
                "Tell me about its ports and parameters.",
                ["describe_block"],
            ),
        ],
    ),
    MultiTurnCase(
        "search_then_navigate",
        "find_scrambler_describe",
        [
            TurnSpec(
                "I need a scrambler block.",
                ["search_grc"],
            ),
            TurnSpec(
                "What does that block look like?",
                ["describe_block"],
            ),
        ],
    ),
    MultiTurnCase(
        "search_then_navigate",
        "find_costas_then_session_context",
        [
            TurnSpec(
                "Find a Costas loop block.",
                ["search_grc"],
            ),
            TurnSpec(
                "Show me what's around the throttle in my graph.",
                ["get_grc_context"],
            ),
        ],
    ),
    MultiTurnCase(
        "search_then_navigate",
        "find_equalizer_describe",
        [
            TurnSpec(
                "Search the GNU Radio catalog for an equalizer block.",
                ["search_grc"],
            ),
            TurnSpec(
                "Describe the first result.",
                ["describe_block"],
            ),
        ],
    ),
    # ── state_awareness ─────────────────────────────────────────────────
    MultiTurnCase(
        "state_awareness",
        "edit_then_query_dirty",
        [
            TurnSpec(
                "Change samp_rate to 48000.",
                ["apply_edit"],
                transaction_checks=[
                    {"op_type": "update_params", "instance_name": "samp_rate"},
                ],
            ),
            TurnSpec(
                "Is the graph dirty?",
                ["summarize_graph"],
            ),
        ],
    ),
    MultiTurnCase(
        "state_awareness",
        "edit_validate_save_separate",
        [
            TurnSpec(
                "Set the rate to 32000.",
                ["apply_edit"],
                transaction_checks=[
                    {"op_type": "update_params", "instance_name": "samp_rate"},
                ],
            ),
            TurnSpec(
                "Validate it.",
                ["validate_graph"],
            ),
            TurnSpec(
                "Now save it.",
                ["save_graph"],
            ),
        ],
    ),
    MultiTurnCase(
        "state_awareness",
        "add_var_then_check_session",
        [
            TurnSpec(
                "Add a variable called threshold with value 0.5.",
                ["apply_edit"],
                transaction_checks=[
                    {
                        "op_type": "add_block",
                        "block_type": "variable",
                        "instance_name": "threshold",
                    },
                ],
            ),
            TurnSpec(
                "What variables are in my graph now?",
                ["summarize_graph"],
            ),
        ],
    ),
    MultiTurnCase(
        "state_awareness",
        "edit_check_summary_reflects_change",
        [
            TurnSpec(
                "Change samp_rate to 96000.",
                ["apply_edit"],
                transaction_checks=[
                    {"op_type": "update_params", "instance_name": "samp_rate"},
                ],
            ),
            TurnSpec(
                "Summarize the graph.",
                ["summarize_graph"],
            ),
        ],
    ),
    MultiTurnCase(
        "state_awareness",
        "validate_clean_then_edit",
        [
            TurnSpec(
                "Is the current graph valid?",
                ["validate_graph"],
            ),
            TurnSpec(
                "Now change samp_rate to 16000.",
                ["apply_edit"],
                transaction_checks=[
                    {"op_type": "update_params", "instance_name": "samp_rate"},
                ],
            ),
        ],
    ),
    # ── edit_then_query ─────────────────────────────────────────────────
    MultiTurnCase(
        "edit_then_query",
        "add_var_then_describe_type",
        [
            TurnSpec(
                "Add a variable called my_gain set to 10.",
                ["apply_edit"],
                transaction_checks=[
                    {
                        "op_type": "add_block",
                        "block_type": "variable",
                        "instance_name": "my_gain",
                    },
                ],
            ),
            TurnSpec(
                "Describe the variable block type.",
                ["describe_block"],
                checked_tool_name="describe_block",
                tool_arg_checks={"block_id": "variable"},
            ),
        ],
    ),
    MultiTurnCase(
        "edit_then_query",
        "edit_rate_then_ask_about_throttle",
        [
            TurnSpec(
                "Set samp_rate to 48000.",
                ["apply_edit"],
                transaction_checks=[
                    {"op_type": "update_params", "instance_name": "samp_rate"},
                ],
            ),
            TurnSpec(
                "What does the throttle block do?",
                ["describe_block"],
                checked_tool_name="describe_block",
                tool_arg_checks={"block_id": "blocks_throttle2"},
            ),
        ],
    ),
    MultiTurnCase(
        "edit_then_query",
        "rewire_then_inspect_sink",
        [
            TurnSpec(
                "Put the float stream on a second trace of the time sink.",
                ["apply_edit"],
                transaction_checks=[
                    {
                        "op_type": "update_params",
                        "instance_name": "qtgui_time_sink_x_0",
                        "params": {"nconnections": "2"},
                    },
                ],
            ),
            TurnSpec(
                "Show me what's connected to the time sink now.",
                ["get_grc_context"],
                checked_tool_name="get_grc_context",
                tool_arg_checks={"node_id": "qtgui_time_sink_x_0"},
            ),
        ],
    ),
    MultiTurnCase(
        "edit_then_query",
        "add_then_validate_then_summary",
        [
            TurnSpec(
                "Create a variable called my_offset with value 0.",
                ["apply_edit"],
                transaction_checks=[
                    {
                        "op_type": "add_block",
                        "block_type": "variable",
                        "instance_name": "my_offset",
                    },
                ],
            ),
            TurnSpec(
                "Validate and give me a summary.",
                ["validate_graph", "summarize_graph"],
            ),
        ],
    ),
    MultiTurnCase(
        "edit_then_query",
        "disconnect_then_check_context",
        [
            TurnSpec(
                "Disconnect the source from the throttle.",
                ["apply_edit"],
                transaction_checks=[
                    {"op_type": "remove_connection"},
                ],
            ),
            TurnSpec(
                "What does the throttle neighborhood look like?",
                ["get_grc_context"],
            ),
        ],
    ),
    # ── repair_flow ─────────────────────────────────────────────────────
    MultiTurnCase(
        "repair_flow",
        "remove_var_repair_then_validate",
        [
            TurnSpec(
                "Remove the samp_rate variable but keep the graph valid. You must patch dependent parameters to literal values before removing.",
                ["apply_edit"],
                transaction_checks=[
                    {"op_type": "update_params", "instance_name": "blocks_throttle2_0"},
                    {"op_type": "remove_block", "instance_name": "samp_rate"},
                ],
            ),
            TurnSpec(
                "Validate the graph.",
                ["validate_graph"],
            ),
        ],
    ),
    MultiTurnCase(
        "repair_flow",
        "remove_var_then_summarize",
        [
            TurnSpec(
                "Get rid of samp_rate, keep it working with 32000.",
                ["apply_edit"],
                transaction_checks=[
                    {"op_type": "update_params", "instance_name": "blocks_throttle2_0"},
                    {"op_type": "remove_block", "instance_name": "samp_rate"},
                ],
            ),
            TurnSpec(
                "Give me a summary of the graph now.",
                ["summarize_graph"],
            ),
        ],
    ),
    MultiTurnCase(
        "repair_flow",
        "repair_then_save",
        [
            TurnSpec(
                "Remove samp_rate and patch everything to 32000.",
                ["apply_edit"],
                transaction_checks=[
                    {"op_type": "update_params", "instance_name": "blocks_throttle2_0"},
                    {"op_type": "remove_block", "instance_name": "samp_rate"},
                ],
            ),
            TurnSpec(
                "Validate and save.",
                ["validate_graph", "save_graph"],
            ),
        ],
    ),
    MultiTurnCase(
        "repair_flow",
        "repair_then_add_var",
        [
            TurnSpec(
                "Remove samp_rate, hardcode throttle to 32000.",
                ["apply_edit"],
                transaction_checks=[
                    {"op_type": "update_params", "instance_name": "blocks_throttle2_0"},
                    {"op_type": "remove_block", "instance_name": "samp_rate"},
                ],
            ),
            TurnSpec(
                "Now add a variable called new_rate with value 32000.",
                ["apply_edit"],
                transaction_checks=[
                    {
                        "op_type": "add_block",
                        "block_type": "variable",
                        "instance_name": "new_rate",
                    },
                ],
            ),
        ],
    ),
    # ── error_then_fix ──────────────────────────────────────────────────
    MultiTurnCase(
        "error_then_fix",
        "remove_throttle_fail_then_disconnect",
        [
            TurnSpec(
                "Remove the throttle block.",
                ["apply_edit"],
                transaction_checks=[
                    {"op_type": "remove_block", "instance_name": "blocks_throttle2_0"},
                ],
            ),
            TurnSpec(
                "That didn't work because it's connected. Disconnect every attached wire from the throttle, then remove the throttle block, all in one transaction.",
                ["apply_edit"],
                transaction_checks=[
                    {
                        "op_type": "remove_connection",
                        "src_block": "analog_random_source_x_0",
                        "src_port": 0,
                        "dst_block": "blocks_throttle2_0",
                        "dst_port": 0,
                    },
                    {
                        "op_type": "remove_connection",
                        "src_block": "blocks_throttle2_0",
                        "src_port": 0,
                        "dst_block": "blocks_char_to_float_0",
                        "dst_port": 0,
                    },
                    {"op_type": "remove_block", "instance_name": "blocks_throttle2_0"},
                ],
            ),
        ],
    ),
    MultiTurnCase(
        "error_then_fix",
        "describe_nonexistent_then_real",
        [
            TurnSpec(
                "Tell me about the foobar_baz block.",
                ["describe_block"],
                text_contains_any_checks=[
                    "not found",
                    "does not exist",
                    "foobar_baz",
                    "no block",
                ],
            ),
            TurnSpec(
                "That block doesn't exist. Please call describe_block for blocks_throttle2 instead.",
                ["describe_block"],
                checked_tool_name="describe_block",
                tool_arg_checks={"block_id": "blocks_throttle2"},
            ),
        ],
    ),
    MultiTurnCase(
        "error_then_fix",
        "bad_edit_then_good_edit",
        [
            TurnSpec(
                "Change foobar_variable to 100.",
                ["apply_edit"],
            ),
            TurnSpec(
                "That's not a real block. Change samp_rate to 48000 instead.",
                ["apply_edit"],
                transaction_checks=[
                    {"op_type": "update_params", "instance_name": "samp_rate"},
                ],
            ),
        ],
    ),
    MultiTurnCase(
        "error_then_fix",
        "remove_referenced_variable_then_repair",
        [
            TurnSpec(
                "Remove the samp_rate variable.",
                ["apply_edit"],
                transaction_checks=[
                    {"op_type": "remove_block", "instance_name": "samp_rate"},
                ],
            ),
            TurnSpec(
                "That failed because the variable is still referenced. Keep the graph working by patching dependent parameters to 32000 and then remove samp_rate in one repair transaction.",
                ["apply_edit"],
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
        ],
    ),
    # ── natural_multi ───────────────────────────────────────────────────
    MultiTurnCase(
        "natural_multi",
        "explore_then_modify",
        [
            TurnSpec(
                "What am I looking at?",
                ["summarize_graph"],
            ),
            TurnSpec(
                "Speed it up to 96k.",
                ["apply_edit"],
                transaction_checks=[
                    {"op_type": "update_params", "instance_name": "samp_rate"},
                ],
            ),
            TurnSpec(
                "Does it still work?",
                ["validate_graph"],
            ),
        ],
    ),
    MultiTurnCase(
        "natural_multi",
        "curious_then_act",
        [
            TurnSpec(
                "What does the throttle do?",
                ["describe_block"],
                checked_tool_name="describe_block",
                tool_arg_checks={"block_id": "blocks_throttle2"},
            ),
            TurnSpec(
                "How is it wired in my graph?",
                ["get_grc_context"],
            ),
            TurnSpec(
                "OK, change the sample rate to 48000.",
                ["apply_edit"],
                transaction_checks=[
                    {"op_type": "update_params", "instance_name": "samp_rate"},
                ],
            ),
        ],
    ),
    MultiTurnCase(
        "natural_multi",
        "discover_and_use",
        [
            TurnSpec(
                "I need something for carrier recovery.",
                ["search_grc"],
            ),
            TurnSpec(
                "Describe the first result you found.",
                ["describe_block"],
            ),
            TurnSpec(
                "Now show me a summary of my graph.",
                ["summarize_graph"],
            ),
        ],
    ),
    MultiTurnCase(
        "natural_multi",
        "full_workflow_natural",
        [
            TurnSpec(
                "Give me a quick overview.",
                ["summarize_graph"],
            ),
            TurnSpec(
                "Change samp_rate to 32000.",
                ["apply_edit"],
                transaction_checks=[
                    {"op_type": "update_params", "instance_name": "samp_rate"},
                ],
            ),
            TurnSpec(
                "Check it and write it out.",
                ["validate_graph", "save_graph"],
            ),
        ],
    ),
    MultiTurnCase(
        "natural_multi",
        "session_search_edit_validate",
        [
            TurnSpec(
                "Search my graph for sinks.",
                ["search_grc"],
                checked_tool_name="search_grc",
                tool_arg_checks={"scope": "session"},
            ),
            TurnSpec(
                "Add a second trace to the time sink.",
                ["apply_edit"],
                transaction_checks=[
                    {
                        "op_type": "update_params",
                        "instance_name": "qtgui_time_sink_x_0",
                        "params": {"nconnections": "2"},
                    },
                ],
            ),
            TurnSpec(
                "Validate it.",
                ["validate_graph"],
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
        "phase": 4,
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
        description="Phase 4 model eval: multi-turn conversation continuity."
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

    cases = list(PHASE4_CASES)
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
