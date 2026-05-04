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
FEC_DUPLICATE_POLYS_UID_A = "block:dbc46d86bc640163"


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


def _multi_turn_scenario_if_present(
    *,
    category: str,
    name: str,
    relative_path: str,
    turns: tuple[LiveTurnSpec, ...],
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
        turns=turns,
    )


def _probe_cases() -> list[LiveScenario]:
    cases = [
        _scenario_if_present(
            category="external_probe",
            name="dial_tone_duplicate_connection_rolls_back",
            relative_path="audio/dial_tone.grc",
            prompt=(
                "Call apply_edit with an add_connection transaction from "
                "analog_sig_source_x_0 port 0 to blocks_add_xx port 0 in this "
                "installed dial tone example. If that connection already exists, "
                "leave the graph unchanged."
            ),
            expected_tool_calls=(
                ToolExpectation(
                    "apply_edit",
                    transaction_operations=(
                        {
                            "op_type": "add_connection",
                            "src_block": "analog_sig_source_x_0",
                            "src_port": 0,
                            "dst_block": "blocks_add_xx",
                            "dst_port": 0,
                        },
                    ),
                    require_result_ok=False,
                ),
            ),
            semantic_checks=(
                {"kind": "exact_graph_delta", "delta": {}},
                {"kind": "no_mutation"},
                {
                    "kind": "connection_present",
                    "connection_id": "analog_sig_source_x_0:0->blocks_add_xx:0",
                },
                {
                    "kind": "tool_result",
                    "tool": "apply_edit",
                    "arguments": {
                        "ok": False,
                        "applied": False,
                        "error_type": "preflight_rejected",
                    },
                },
            ),
            description=(
                "Known-gap probe for duplicate stream add-connection preflight "
                "rollback on an installed audio example. The runtime rollback is "
                "valid, but the 2B model currently routes the request to safe "
                "clarification instead of apply_edit."
            ),
        ),
        _scenario_if_present(
            category="external_probe",
            name="dial_tone_occupied_input_add_rolls_back",
            relative_path="audio/dial_tone.grc",
            prompt=(
                "Call apply_edit with an add_connection transaction from "
                "analog_sig_source_x_0 port 0 to audio_sink port 0 in this installed "
                "dial tone example. If audio_sink input 0 is already occupied, "
                "leave the graph unchanged."
            ),
            expected_tool_calls=(
                ToolExpectation(
                    "apply_edit",
                    transaction_operations=(
                        {
                            "op_type": "add_connection",
                            "src_block": "analog_sig_source_x_0",
                            "src_port": 0,
                            "dst_block": "audio_sink",
                            "dst_port": 0,
                        },
                    ),
                    require_result_ok=False,
                ),
            ),
            semantic_checks=(
                {"kind": "exact_graph_delta", "delta": {}},
                {"kind": "no_mutation"},
                {
                    "kind": "connection_present",
                    "connection_id": "blocks_add_xx:0->audio_sink:0",
                },
                {
                    "kind": "connection_absent",
                    "connection_id": "analog_sig_source_x_0:0->audio_sink:0",
                },
                {
                    "kind": "tool_result",
                    "tool": "apply_edit",
                    "arguments": {
                        "ok": False,
                        "applied": False,
                        "error_type": "preflight_rejected",
                    },
                },
            ),
            description=(
                "Known-gap probe for occupied-input stream add-connection "
                "preflight rollback on an installed audio example. The runtime "
                "rollback is valid, but the 2B model currently routes the request "
                "to safe clarification instead of apply_edit."
            ),
        ),
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
                {
                    "kind": "exact_graph_delta",
                    "delta": {
                        "variables": {"samp_rate": "44100"},
                        "block_params": {"samp_rate": {"value": "44100"}},
                        "dirty": True,
                        "validation_status": "valid",
                        "validation_returncode": 0,
                    },
                },
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
            category="external_rollback",
            name="dial_tone_output_disconnect_rolls_back",
            relative_path="audio/dial_tone.grc",
            prompt=(
                "Remove the exact connection_id blocks_add_xx:0->audio_sink:0 "
                "from this installed dial tone example. If GNU validation fails, "
                "leave the graph unchanged."
            ),
            expected_tool_calls=(
                ToolExpectation(
                    "remove_connection",
                    arguments={"connection_id": "blocks_add_xx:0->audio_sink:0"},
                    require_result_ok=False,
                ),
            ),
            semantic_checks=(
                {"kind": "exact_graph_delta", "delta": {}},
                {"kind": "no_mutation"},
                {
                    "kind": "connection_present",
                    "connection_id": "blocks_add_xx:0->audio_sink:0",
                },
                {
                    "kind": "tool_result",
                    "tool": "remove_connection",
                    "arguments": {
                        "ok": False,
                        "applied": False,
                        "error_type": "gnu_validation_failed",
                    },
                },
            ),
            description=(
                "Promoted rollback proof for GNU-invalid stream disconnect on an "
                "installed audio example."
            ),
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
            name="stream_mux_validate",
            relative_path="blocks/stream_mux_demo.grc",
            prompt="Validate this installed GNU Radio stream mux example.",
            expected_tool_calls=(ToolExpectation("validate_graph"),),
            semantic_checks=(
                {
                    "kind": "exact_graph_delta",
                    "delta": {
                        "validation_status": "valid",
                        "validation_returncode": 0,
                    },
                },
                {
                    "kind": "tool_result",
                    "tool": "validate_graph",
                    "arguments": {"valid": True},
                },
            ),
            description="Validation on an installed stream-mux blocks example.",
        ),
        _scenario_if_present(
            category="external",
            name="sig_source_msg_ports_context",
            relative_path="analog/sig_source_msg_ports.grc",
            prompt=(
                "Show me what is around analog_sig_source_x_0 in this installed "
                "message-port signal-source example."
            ),
            expected_tool_calls=(
                ToolExpectation(
                    "get_grc_context",
                    arguments={"node_id": "analog_sig_source_x_0"},
                ),
            ),
            semantic_checks=(
                {"kind": "exact_graph_delta", "delta": {}},
                {"kind": "no_mutation"},
            ),
            description=(
                "Read-only context on an installed analog example with message ports."
            ),
        ),
        _scenario_if_present(
            category="external_rewire",
            name="sig_source_msg_random_disconnect_validate_save",
            relative_path="analog/sig_source_msg_ports.grc",
            prompt=(
                "Remove the exact connection_id "
                "blocks_message_strobe_random_0:strobe->analog_sig_source_x_0:cmd "
                "from this installed message-port signal-source example, validate it, "
                "then save a copy to {save_path}."
            ),
            expected_tool_calls=(
                ToolExpectation(
                    "remove_connection",
                    arguments={
                        "connection_id": (
                            "blocks_message_strobe_random_0:strobe->analog_sig_source_x_0:cmd"
                        ),
                    },
                ),
                ToolExpectation("validate_graph"),
                ToolExpectation("save_graph", arguments={"path": "{save_path}"}),
            ),
            semantic_checks=(
                {
                    "kind": "exact_graph_delta",
                    "delta": {
                        "removed_connections": [
                            "blocks_message_strobe_random_0:strobe->analog_sig_source_x_0:cmd"
                        ],
                        "validation_status": "valid",
                        "validation_returncode": 0,
                    },
                },
                {
                    "kind": "connection_absent",
                    "connection_id": (
                        "blocks_message_strobe_random_0:strobe->analog_sig_source_x_0:cmd"
                    ),
                },
                {
                    "kind": "tool_result",
                    "tool": "validate_graph",
                    "arguments": {"valid": True},
                },
                {"kind": "saved_path_valid", "path": "{save_path}"},
            ),
            description=(
                "Verified exact message-port disconnect, validate, and explicit save-copy "
                "on an installed analog message-port example."
            ),
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
            category="external_rollback",
            name="selector_output_disconnect_rolls_back",
            relative_path="blocks/selector.grc",
            prompt=(
                "Remove the exact connection_id "
                "blocks_selector_0:0->qtgui_time_sink_x_0:0 "
                "from this installed selector example. If GNU validation fails, "
                "leave the graph unchanged."
            ),
            expected_tool_calls=(
                ToolExpectation(
                    "remove_connection",
                    arguments={
                        "connection_id": "blocks_selector_0:0->qtgui_time_sink_x_0:0",
                    },
                    require_result_ok=False,
                ),
            ),
            semantic_checks=(
                {"kind": "exact_graph_delta", "delta": {}},
                {"kind": "no_mutation"},
                {
                    "kind": "connection_present",
                    "connection_id": "blocks_selector_0:0->qtgui_time_sink_x_0:0",
                },
                {
                    "kind": "tool_result",
                    "tool": "remove_connection",
                    "arguments": {
                        "ok": False,
                        "applied": False,
                        "error_type": "gnu_validation_failed",
                    },
                },
            ),
            description=(
                "Promoted rollback proof for a GNU-invalid exact disconnect on an "
                "installed selector example. The candidate failure must leave the "
                "live graph unchanged."
            ),
        ),
        _scenario_if_present(
            category="external_rollback",
            name="selector_occupied_rewire_rolls_back",
            relative_path="blocks/selector.grc",
            prompt=(
                "Rewire connection_id blocks_selector_0:0->qtgui_time_sink_x_0:0 "
                "to new endpoint blocks_selector_0:0->qtgui_time_sink_x_1:0 "
                "in this installed selector example. If the target input is already "
                "occupied, leave the graph unchanged."
            ),
            expected_tool_calls=(
                ToolExpectation(
                    "rewire_connection",
                    arguments={
                        "old_connection_id": "blocks_selector_0:0->qtgui_time_sink_x_0:0",
                        "new_src_block": "blocks_selector_0",
                        "new_src_port": 0,
                        "new_dst_block": "qtgui_time_sink_x_1",
                        "new_dst_port": 0,
                    },
                    require_result_ok=False,
                ),
            ),
            semantic_checks=(
                {"kind": "exact_graph_delta", "delta": {}},
                {"kind": "no_mutation"},
                {
                    "kind": "connection_present",
                    "connection_id": "blocks_selector_0:0->qtgui_time_sink_x_0:0",
                },
                {
                    "kind": "connection_present",
                    "connection_id": "blocks_selector_0:1->qtgui_time_sink_x_1:0",
                },
                {
                    "kind": "connection_absent",
                    "connection_id": "blocks_selector_0:0->qtgui_time_sink_x_1:0",
                },
                {
                    "kind": "tool_result",
                    "tool": "rewire_connection",
                    "arguments": {
                        "ok": False,
                        "applied": False,
                        "error_type": "preflight_rejected",
                    },
                },
            ),
            description=(
                "Promoted rollback proof for an occupied-input exact rewire on an "
                "installed selector example. No partial disconnect may commit."
            ),
        ),
        _scenario_if_present(
            category="external_rollback",
            name="selector_connected_block_remove_rolls_back",
            relative_path="blocks/selector.grc",
            prompt=(
                "Remove the blocks_selector_0 block from this installed selector "
                "example. If it is still connected, leave the graph unchanged."
            ),
            expected_tool_calls=(
                ToolExpectation(
                    "apply_edit",
                    transaction_operations=(
                        {
                            "op_type": "remove_block",
                            "instance_name": "blocks_selector_0",
                        },
                    ),
                    require_result_ok=False,
                ),
            ),
            semantic_checks=(
                {"kind": "exact_graph_delta", "delta": {}},
                {"kind": "no_mutation"},
                {
                    "kind": "tool_result",
                    "tool": "apply_edit",
                    "arguments": {
                        "ok": False,
                        "applied": False,
                        "error_type": "preflight_rejected",
                    },
                },
            ),
            description=(
                "Promoted rollback proof for connected block removal on an installed "
                "selector example. The preflight rejection must leave the graph "
                "unchanged."
            ),
        ),
        _multi_turn_scenario_if_present(
            category="external_rollback",
            name="selector_saved_connected_block_remove_rolls_back",
            relative_path="blocks/selector.grc",
            turns=(
                LiveTurnSpec(
                    prompt=(
                        "Save a copy of this installed selector example to "
                        "{save_path}."
                    ),
                    expected_tool_calls=(
                        ToolExpectation(
                            "save_graph",
                            arguments={"path": "{save_path}"},
                        ),
                    ),
                    semantic_checks=(
                        {"kind": "saved_path_valid", "path": "{save_path}", "copy": True},
                        {
                            "kind": "saved_block_present",
                            "path": "{save_path}",
                            "instance_name": "blocks_selector_0",
                        },
                        {
                            "kind": "saved_connection_present",
                            "path": "{save_path}",
                            "connection_id": (
                                "blocks_selector_0:0->qtgui_time_sink_x_0:0"
                            ),
                        },
                    ),
                ),
                LiveTurnSpec(
                    prompt=(
                        "Remove the blocks_selector_0 block from the saved selector "
                        "copy. If it is still connected, leave the graph unchanged "
                        "and do not save again."
                    ),
                    expected_tool_calls=(
                        ToolExpectation(
                            "apply_edit",
                            transaction_operations=(
                                {
                                    "op_type": "remove_block",
                                    "instance_name": "blocks_selector_0",
                                },
                            ),
                            require_result_ok=False,
                        ),
                    ),
                    semantic_checks=(
                        {"kind": "exact_graph_delta", "delta": {}},
                        {"kind": "no_mutation"},
                        {
                            "kind": "connection_present",
                            "connection_id": (
                                "blocks_selector_0:0->qtgui_time_sink_x_0:0"
                            ),
                        },
                        {
                            "kind": "tool_result",
                            "tool": "apply_edit",
                            "arguments": {
                                "ok": False,
                                "applied": False,
                                "error_type": "preflight_rejected",
                            },
                        },
                        {
                            "kind": "saved_block_present",
                            "path": "{save_path}",
                            "instance_name": "blocks_selector_0",
                        },
                        {
                            "kind": "saved_connection_present",
                            "path": "{save_path}",
                            "connection_id": (
                                "blocks_selector_0:0->qtgui_time_sink_x_0:0"
                            ),
                        },
                    ),
                ),
            ),
            description=(
                "Negative persistence proof on an installed selector example: "
                "after an explicit save-copy, connected block removal must fail "
                "unchanged and the saved graph must still reload with the original "
                "block and connection."
            ),
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
                    "kind": "exact_graph_delta",
                    "delta": {
                        "block_params": {"analog_sig_source_x_0": {"amp": "0.5"}},
                        "dirty": True,
                        "validation_status": "valid",
                        "validation_returncode": 0,
                    },
                },
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
            name="var_to_msg_test_value_edit_validate",
            relative_path="blocks/var_to_msg.grc",
            prompt=(
                "Set the test block value parameter to 7 in this installed "
                "var-to-message example, then validate it."
            ),
            expected_tool_calls=(
                ToolExpectation(
                    "apply_edit",
                    transaction_operations=(
                        {
                            "op_type": "update_params",
                            "instance_name": "test",
                            "params": {"value": "7"},
                        },
                    ),
                ),
                ToolExpectation("validate_graph"),
            ),
            semantic_checks=(
                {
                    "kind": "exact_graph_delta",
                    "delta": {
                        "block_params": {"test": {"value": 7}},
                        "dirty": True,
                        "validation_status": "valid",
                        "validation_returncode": 0,
                    },
                },
                {
                    "kind": "block_param_equals",
                    "instance_name": "test",
                    "param": "value",
                    "value": "7",
                },
                {
                    "kind": "tool_result",
                    "tool": "validate_graph",
                    "arguments": {"valid": True},
                },
            ),
            description=(
                "Verified non-variable value edit on an installed message-port blocks example."
            ),
        ),
        _scenario_if_present(
            category="external_rollback",
            name="var_to_msg_remove_throttle_rolls_back",
            relative_path="blocks/var_to_msg.grc",
            prompt="Remove the blocks_throttle_0 block from this installed var-to-message example.",
            expected_tool_calls=(
                ToolExpectation(
                    "apply_edit",
                    transaction_operations=(
                        {
                            "op_type": "remove_block",
                            "instance_name": "blocks_throttle_0",
                        },
                    ),
                    require_result_ok=False,
                ),
            ),
            semantic_checks=(
                {"kind": "exact_graph_delta", "delta": {}},
                {"kind": "no_mutation"},
            ),
            description=(
                "Promoted rollback proof for connected block removal on an installed "
                "blocks example. The preflight rejection must leave the graph unchanged."
            ),
        ),
        _scenario_if_present(
            category="external_safety",
            name="var_to_msg_stream_to_message_add_clarifies",
            relative_path="blocks/var_to_msg.grc",
            prompt=(
                "Connect blocks_null_source_0 stream output 0 to "
                "blocks_message_debug_0 message port store in this installed "
                "var-to-message example."
            ),
            expected_tool_calls=(),
            semantic_checks=(
                {"kind": "exact_graph_delta", "delta": {}},
                {"kind": "no_mutation"},
                {
                    "kind": "connection_absent",
                    "connection_id": (
                        "blocks_null_source_0:0->blocks_message_debug_0:store"
                    ),
                },
            ),
            description=(
                "Promoted safety proof for a mixed stream-to-message connection "
                "request on an installed message-port example. The model should "
                "clarify rather than guess an incompatible add_connection mutation."
            ),
        ),
        _scenario_if_present(
            category="external_safety",
            name="var_to_msg_message_to_stream_add_clarifies",
            relative_path="blocks/var_to_msg.grc",
            prompt=(
                "Connect blocks_var_to_msg_0 message output msgout to "
                "blocks_null_sink_0 stream input 0 in this installed "
                "var-to-message example."
            ),
            expected_tool_calls=(),
            semantic_checks=(
                {"kind": "exact_graph_delta", "delta": {}},
                {"kind": "no_mutation"},
                {
                    "kind": "connection_absent",
                    "connection_id": (
                        "blocks_var_to_msg_0:msgout->blocks_null_sink_0:0"
                    ),
                },
            ),
            description=(
                "Promoted safety proof for a mixed message-to-stream connection "
                "request on an installed message-port example. The model should "
                "clarify rather than guess an incompatible add_connection mutation."
            ),
        ),
        _scenario_if_present(
            category="external_edit",
            name="filter_cutoff_low_edit_validate",
            relative_path="filter/filter_taps.grc",
            prompt="Set cutoff_low to 3000 in this installed filter taps example, then validate it.",
            expected_tool_calls=(
                ToolExpectation(
                    "apply_edit",
                    transaction_operations=(
                        {
                            "op_type": "update_params",
                            "instance_name": "cutoff_low",
                            "params": {"value": "3000"},
                        },
                    ),
                ),
                ToolExpectation("validate_graph"),
            ),
            semantic_checks=(
                {
                    "kind": "exact_graph_delta",
                    "delta": {
                        "variables": {"cutoff_low": 3000},
                        "block_params": {"cutoff_low": {"value": 3000}},
                        "dirty": True,
                        "validation_status": "valid",
                        "validation_returncode": 0,
                    },
                },
                {
                    "kind": "tool_result",
                    "tool": "validate_graph",
                    "arguments": {"valid": True},
                },
            ),
            description="Verified variable edit on an installed filter-taps example.",
        ),
        _scenario_if_present(
            category="external_edit",
            name="filter_cutoff_low_edit_validate_save",
            relative_path="filter/filter_taps.grc",
            prompt=(
                "Set cutoff_low to 3000 in this installed filter taps example, "
                "validate it, then save a copy to {save_path}."
            ),
            expected_tool_calls=(
                ToolExpectation(
                    "apply_edit",
                    transaction_operations=(
                        {
                            "op_type": "update_params",
                            "instance_name": "cutoff_low",
                            "params": {"value": "3000"},
                        },
                    ),
                ),
                ToolExpectation("validate_graph"),
                ToolExpectation("save_graph", arguments={"path": "{save_path}"}),
            ),
            semantic_checks=(
                {
                    "kind": "exact_graph_delta",
                    "delta": {
                        "variables": {"cutoff_low": 3000},
                        "block_params": {"cutoff_low": {"value": 3000}},
                        "validation_status": "valid",
                        "validation_returncode": 0,
                    },
                },
                {
                    "kind": "tool_result",
                    "tool": "validate_graph",
                    "arguments": {"valid": True},
                },
                {"kind": "saved_path_valid", "path": "{save_path}", "copy": True},
                {
                    "kind": "saved_variable_equals",
                    "path": "{save_path}",
                    "name": "cutoff_low",
                    "value": "3000",
                },
                {
                    "kind": "saved_block_param_equals",
                    "path": "{save_path}",
                    "instance_name": "cutoff_low",
                    "param": "value",
                    "value": "3000",
                },
            ),
            description=(
                "Verified variable edit, validate, explicit save-copy, and saved "
                "parameter persistence on an installed filter-taps example."
            ),
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
                {
                    "kind": "exact_graph_delta",
                    "delta": {
                        "variables": {"samp_rate": "48000"},
                        "block_params": {"samp_rate": {"value": "48000"}},
                        "validation_status": "valid",
                        "validation_returncode": 0,
                    },
                },
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
        _scenario_if_present(
            category="external_edit",
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
                    "kind": "exact_graph_delta",
                    "delta": {
                        "block_states": {"blocks_message_debug_0": "disabled"},
                        "dirty": True,
                        "validation_status": "valid",
                        "validation_returncode": 0,
                    },
                },
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
                "Verified block-state edit on an installed vocoder example. "
                "Exercises typed tool narrowing for explicit disable requests."
            ),
        ),
        _scenario_if_present(
            category="external",
            name="pdu_simple_validate",
            relative_path="pdu/simple_pdu_to_stream_example.grc",
            prompt="Validate this installed GNU Radio PDU to stream example.",
            expected_tool_calls=(ToolExpectation("validate_graph"),),
            semantic_checks=(
                {
                    "kind": "exact_graph_delta",
                    "delta": {
                        "validation_status": "valid",
                        "validation_returncode": 0,
                    },
                },
                {
                    "kind": "tool_result",
                    "tool": "validate_graph",
                    "arguments": {"valid": True},
                },
            ),
            description="Validation on an installed PDU example with message-to-stream conversion.",
        ),
        _scenario_if_present(
            category="external_edit",
            name="pdu_tools_random_pdu_maxsize_edit_validate",
            relative_path="pdu/pdu_tools_demo.grc",
            prompt=(
                "Set random_pdu maxsize to 8192 in this installed PDU tools "
                "demo, then validate it."
            ),
            expected_tool_calls=(
                ToolExpectation(
                    "apply_edit",
                    transaction_operations=(
                        {
                            "op_type": "update_params",
                            "instance_name": "random_pdu",
                            "params": {"maxsize": "8192"},
                        },
                    ),
                ),
                ToolExpectation("validate_graph"),
            ),
            semantic_checks=(
                {
                    "kind": "exact_graph_delta",
                    "delta": {
                        "block_params": {"random_pdu": {"maxsize": "8192"}},
                        "dirty": True,
                        "validation_status": "valid",
                        "validation_returncode": 0,
                    },
                },
                {
                    "kind": "block_param_equals",
                    "instance_name": "random_pdu",
                    "param": "maxsize",
                    "value": "8192",
                },
                {
                    "kind": "tool_result",
                    "tool": "validate_graph",
                    "arguments": {"valid": True},
                },
            ),
            description="Verified non-variable PDU block-parameter edit on an installed PDU example.",
        ),
        _scenario_if_present(
            category="external_rollback",
            name="uhd_packet_rx_duplicate_constellation_edit_rejected",
            relative_path="digital/packet/uhd_packet_rx.grc",
            prompt=(
                "Set the Const_PLD block comment to duplicate check in this "
                "installed packet receiver example. If the target name is "
                "duplicated, leave the graph unchanged."
            ),
            expected_tool_calls=(
                ToolExpectation(
                    "apply_edit",
                    transaction_operations=(
                        {
                            "op_type": "update_params",
                            "instance_name": "Const_PLD",
                            "params": {"comment": "duplicate check"},
                        },
                    ),
                    require_result_ok=False,
                ),
            ),
            semantic_checks=(
                {"kind": "exact_graph_delta", "delta": {}},
                {"kind": "no_mutation"},
                {
                    "kind": "tool_result",
                    "tool": "apply_edit",
                    "arguments": {
                        "ok": False,
                        "clarification_required": True,
                        "error_type": "ambiguous_block",
                    },
                },
            ),
            description=(
                "Promoted duplicate-identity safety proof on an installed packet "
                "example with same-name same-type constellation variables. The "
                "runtime must clarify or reject the ambiguous name and avoid "
                "mutating the wrong duplicate."
            ),
        ),
        _scenario_if_present(
            category="external_rollback",
            name="uhd_wbfm_duplicate_tun_freq_edit_rejected",
            relative_path="uhd/uhd_wbfm_receive.grc",
            prompt=(
                "Set the tun_freq block comment to duplicate check in this "
                "installed WBFM receiver example. If the target name is "
                "duplicated, leave the graph unchanged."
            ),
            expected_tool_calls=(
                ToolExpectation(
                    "apply_edit",
                    transaction_operations=(
                        {
                            "op_type": "update_params",
                            "instance_name": "tun_freq",
                            "params": {"comment": "duplicate check"},
                        },
                    ),
                    require_result_ok=False,
                ),
            ),
            semantic_checks=(
                {"kind": "exact_graph_delta", "delta": {}},
                {"kind": "no_mutation"},
                {
                    "kind": "tool_result",
                    "tool": "apply_edit",
                    "arguments": {
                        "ok": False,
                        "clarification_required": True,
                        "error_type": "ambiguous_block",
                    },
                },
            ),
            description=(
                "Promoted duplicate-identity safety proof on an installed UHD "
                "WBFM receiver example. Same-name same-type variable range "
                "targets must clarify or reject without mutating a first or "
                "arbitrary duplicate."
            ),
        ),
        _scenario_if_present(
            category="external_rollback",
            name="pdu_tools_message_disconnect_rolls_back",
            relative_path="pdu/pdu_tools_demo.grc",
            prompt=(
                "Remove the exact connection_id random_pdu:pdus->pdu_set:pdus "
                "from this installed PDU tools demo. If preflight rejects the "
                "disconnect, leave the graph unchanged."
            ),
            expected_tool_calls=(
                ToolExpectation(
                    "remove_connection",
                    arguments={"connection_id": "random_pdu:pdus->pdu_set:pdus"},
                    require_result_ok=False,
                ),
            ),
            semantic_checks=(
                {"kind": "exact_graph_delta", "delta": {}},
                {"kind": "no_mutation"},
                {
                    "kind": "connection_present",
                    "connection_id": "random_pdu:pdus->pdu_set:pdus",
                },
                {
                    "kind": "tool_result",
                    "tool": "remove_connection",
                    "arguments": {
                        "ok": False,
                        "applied": False,
                        "error_type": "preflight_rejected",
                    },
                },
            ),
            description=(
                "Promoted rollback proof for message-port disconnect preflight "
                "rejection on an installed PDU example."
            ),
        ),
        _scenario_if_present(
            category="external_edit",
            name="simple_bpsk_tag_debug_enable_validate_save",
            relative_path="digital/packet/simple_bpsk_tx.grc",
            prompt=(
                "Enable the blocks_tag_debug_0 block in this installed simple BPSK "
                "transmitter example, validate it, then save a copy to {save_path}."
            ),
            expected_tool_calls=(
                ToolExpectation(
                    "apply_edit",
                    transaction_operations=(
                        {
                            "op_type": "update_states",
                            "instance_name": "blocks_tag_debug_0",
                            "state": "enabled",
                        },
                    ),
                ),
                ToolExpectation("validate_graph"),
                ToolExpectation("save_graph", arguments={"path": "{save_path}"}),
            ),
            semantic_checks=(
                {
                    "kind": "exact_graph_delta",
                    "delta": {
                        "block_states": {"blocks_tag_debug_0": "enabled"},
                        "validation_status": "valid",
                        "validation_returncode": 0,
                    },
                },
                {
                    "kind": "block_state_equals",
                    "instance_name": "blocks_tag_debug_0",
                    "state": "enabled",
                },
                {
                    "kind": "tool_result",
                    "tool": "validate_graph",
                    "arguments": {"valid": True},
                },
                {"kind": "saved_path_valid", "path": "{save_path}"},
                {
                    "kind": "saved_block_state_equals",
                    "path": "{save_path}",
                    "instance_name": "blocks_tag_debug_0",
                    "state": "enabled",
                },
            ),
            description=(
                "Verified state edit, validate, and explicit save-copy on an installed "
                "digital packet example."
            ),
        ),
        _scenario_if_present(
            category="external_rewire",
            name="tx_stage0_message_disconnect_validate",
            relative_path="digital/packet/tx_stage0.grc",
            prompt=(
                "Remove the exact connection_id "
                "pdu_random_pdu_0:pdus->blocks_message_debug_0:print_pdu "
                "from this installed packet example, then call validate_graph "
                "to validate it."
            ),
            expected_tool_calls=(
                ToolExpectation(
                    "remove_connection",
                    arguments={
                        "connection_id": (
                            "pdu_random_pdu_0:pdus->blocks_message_debug_0:print_pdu"
                        ),
                    },
                ),
                ToolExpectation("validate_graph"),
            ),
            semantic_checks=(
                {
                    "kind": "exact_graph_delta",
                    "delta": {
                        "removed_connections": [
                            "pdu_random_pdu_0:pdus->blocks_message_debug_0:print_pdu"
                        ],
                        "dirty": True,
                        "validation_status": "valid",
                        "validation_returncode": 0,
                    },
                },
                {
                    "kind": "connection_absent",
                    "connection_id": (
                        "pdu_random_pdu_0:pdus->blocks_message_debug_0:print_pdu"
                    ),
                },
                {
                    "kind": "tool_result",
                    "tool": "validate_graph",
                    "arguments": {"valid": True},
                },
            ),
            description=(
                "Verified exact message-port disconnect on an installed digital packet example."
            ),
        ),
        _scenario_if_present(
            category="external_rewire",
            name="tx_stage0_message_disconnect_validate_save",
            relative_path="digital/packet/tx_stage0.grc",
            prompt=(
                "Remove the exact connection_id "
                "pdu_random_pdu_0:pdus->blocks_message_debug_0:print_pdu "
                "from this installed packet example, validate it, then save a copy "
                "to {save_path}."
            ),
            expected_tool_calls=(
                ToolExpectation(
                    "remove_connection",
                    arguments={
                        "connection_id": (
                            "pdu_random_pdu_0:pdus->blocks_message_debug_0:print_pdu"
                        ),
                    },
                ),
                ToolExpectation("validate_graph"),
                ToolExpectation("save_graph", arguments={"path": "{save_path}"}),
            ),
            semantic_checks=(
                {
                    "kind": "exact_graph_delta",
                    "delta": {
                        "removed_connections": [
                            "pdu_random_pdu_0:pdus->blocks_message_debug_0:print_pdu"
                        ],
                        "validation_status": "valid",
                        "validation_returncode": 0,
                    },
                },
                {
                    "kind": "connection_absent",
                    "connection_id": (
                        "pdu_random_pdu_0:pdus->blocks_message_debug_0:print_pdu"
                    ),
                },
                {
                    "kind": "tool_result",
                    "tool": "validate_graph",
                    "arguments": {"valid": True},
                },
                {"kind": "saved_path_valid", "path": "{save_path}"},
            ),
            description=(
                "Verified exact message-port disconnect, validate, and explicit save-copy "
                "on an installed digital packet example."
            ),
        ),
        _scenario_if_present(
            category="external_rewire",
            name="qtgui_message_inputs_message_rewire_validate",
            relative_path="qt-gui/qtgui_message_inputs.grc",
            prompt=(
                "Rewire connection_id "
                "pdu_tagged_stream_to_pdu_0:pdus->qtgui_const_sink_x_0:in "
                "to new endpoint pdu_tagged_stream_to_pdu_1:pdus->qtgui_const_sink_x_0:in "
                "in this installed Qt GUI message inputs example."
            ),
            expected_tool_calls=(
                ToolExpectation(
                    "rewire_connection",
                    arguments={
                        "old_connection_id": (
                            "pdu_tagged_stream_to_pdu_0:pdus->qtgui_const_sink_x_0:in"
                        ),
                        "new_src_block": "pdu_tagged_stream_to_pdu_1",
                        "new_src_port": "pdus",
                        "new_dst_block": "qtgui_const_sink_x_0",
                        "new_dst_port": "in",
                    },
                ),
            ),
            semantic_checks=(
                {
                    "kind": "exact_graph_delta",
                    "delta": {
                        "removed_connections": [
                            "pdu_tagged_stream_to_pdu_0:pdus->qtgui_const_sink_x_0:in"
                        ],
                        "added_connections": [
                            "pdu_tagged_stream_to_pdu_1:pdus->qtgui_const_sink_x_0:in"
                        ],
                        "dirty": True,
                        "validation_status": "valid",
                        "validation_returncode": 0,
                    },
                },
                {
                    "kind": "connection_absent",
                    "connection_id": (
                        "pdu_tagged_stream_to_pdu_0:pdus->qtgui_const_sink_x_0:in"
                    ),
                },
                {
                    "kind": "connection_present",
                    "connection_id": (
                        "pdu_tagged_stream_to_pdu_1:pdus->qtgui_const_sink_x_0:in"
                    ),
                },
            ),
            description=(
                "Verified exact message-port rewire on an installed Qt GUI example."
            ),
        ),
        _scenario_if_present(
            category="external_rewire",
            name="burst_shaper_stream_rewire_validate",
            relative_path="digital/burst_shaper.grc",
            prompt=(
                "Rewire connection_id "
                "blocks_throttle_0:0->blocks_tag_debug_0:0 "
                "to new endpoint blocks_vector_source_x_0_0:0->blocks_tag_debug_0:0 "
                "in this installed burst shaper example, then validate it."
            ),
            expected_tool_calls=(
                ToolExpectation(
                    "rewire_connection",
                    arguments={
                        "old_connection_id": (
                            "blocks_throttle_0:0->blocks_tag_debug_0:0"
                        ),
                        "new_src_block": "blocks_vector_source_x_0_0",
                        "new_src_port": 0,
                        "new_dst_block": "blocks_tag_debug_0",
                        "new_dst_port": 0,
                    },
                ),
                ToolExpectation("validate_graph"),
            ),
            semantic_checks=(
                {
                    "kind": "exact_graph_delta",
                    "delta": {
                        "removed_connections": [
                            "blocks_throttle_0:0->blocks_tag_debug_0:0"
                        ],
                        "added_connections": [
                            "blocks_vector_source_x_0_0:0->blocks_tag_debug_0:0"
                        ],
                        "dirty": True,
                        "validation_status": "valid",
                        "validation_returncode": 0,
                    },
                },
                {
                    "kind": "connection_absent",
                    "connection_id": "blocks_throttle_0:0->blocks_tag_debug_0:0",
                },
                {
                    "kind": "connection_present",
                    "connection_id": (
                        "blocks_vector_source_x_0_0:0->blocks_tag_debug_0:0"
                    ),
                },
                {
                    "kind": "tool_result",
                    "tool": "validate_graph",
                    "arguments": {"valid": True},
                },
            ),
            description=(
                "Verified exact stream-port rewire on an installed digital example."
            ),
        ),
        _scenario_if_present(
            category="external_rewire",
            name="burst_shaper_stream_rewire_validate_save",
            relative_path="digital/burst_shaper.grc",
            prompt=(
                "Rewire connection_id "
                "blocks_throttle_0:0->blocks_tag_debug_0:0 "
                "to new endpoint blocks_vector_source_x_0_0:0->blocks_tag_debug_0:0 "
                "in this installed burst shaper example, validate it, then save a copy "
                "to {save_path}."
            ),
            expected_tool_calls=(
                ToolExpectation(
                    "rewire_connection",
                    arguments={
                        "old_connection_id": (
                            "blocks_throttle_0:0->blocks_tag_debug_0:0"
                        ),
                        "new_src_block": "blocks_vector_source_x_0_0",
                        "new_src_port": 0,
                        "new_dst_block": "blocks_tag_debug_0",
                        "new_dst_port": 0,
                    },
                ),
                ToolExpectation("validate_graph"),
                ToolExpectation("save_graph", arguments={"path": "{save_path}"}),
            ),
            semantic_checks=(
                {
                    "kind": "exact_graph_delta",
                    "delta": {
                        "removed_connections": [
                            "blocks_throttle_0:0->blocks_tag_debug_0:0"
                        ],
                        "added_connections": [
                            "blocks_vector_source_x_0_0:0->blocks_tag_debug_0:0"
                        ],
                        "validation_status": "valid",
                        "validation_returncode": 0,
                    },
                },
                {
                    "kind": "connection_absent",
                    "connection_id": "blocks_throttle_0:0->blocks_tag_debug_0:0",
                },
                {
                    "kind": "connection_present",
                    "connection_id": (
                        "blocks_vector_source_x_0_0:0->blocks_tag_debug_0:0"
                    ),
                },
                {
                    "kind": "tool_result",
                    "tool": "validate_graph",
                    "arguments": {"valid": True},
                },
                {"kind": "saved_path_valid", "path": "{save_path}"},
            ),
            description=(
                "Verified exact stream-port rewire, validate, and explicit save-copy "
                "on an installed digital example."
            ),
        ),
        _multi_turn_scenario_if_present(
            category="external_rewire",
            name="burst_shaper_clarified_rewire_validate_save",
            relative_path="digital/burst_shaper.grc",
            turns=(
                LiveTurnSpec(
                    prompt=(
                        "Call rewire_connection with old_src_block blocks_throttle_0, "
                        "old_src_port 0, new_src_block blocks_vector_source_x_0_0, "
                        "new_src_port 0, new_dst_block blocks_tag_debug_0, and "
                        "new_dst_port 0. Do not provide old_dst_block or old_dst_port; "
                        "if multiple old edges match, ask me to choose."
                    ),
                    expected_tool_calls=(
                        ToolExpectation("rewire_connection", require_result_ok=False),
                    ),
                    semantic_checks=(
                        {"kind": "exact_graph_delta", "delta": {}},
                        {"kind": "no_mutation"},
                        {
                            "kind": "tool_result",
                            "tool": "rewire_connection",
                            "arguments": {
                                "clarification_required": True,
                                "kind": "rewire_connection_disambiguation",
                            },
                        },
                    ),
                ),
                LiveTurnSpec(
                    prompt="A",
                    clarification_response=True,
                    expected_tool_calls=(ToolExpectation("rewire_connection"),),
                    semantic_checks=(
                        {"kind": "clarification_mode", "mode": "executed"},
                        {
                            "kind": "exact_graph_delta",
                            "delta": {
                                "removed_connections": [
                                    "blocks_throttle_0:0->blocks_tag_debug_0:0"
                                ],
                                "added_connections": [
                                    "blocks_vector_source_x_0_0:0->blocks_tag_debug_0:0"
                                ],
                                "dirty": True,
                                "validation_status": "valid",
                                "validation_returncode": 0,
                            },
                        },
                    ),
                ),
                LiveTurnSpec(
                    prompt="Validate it, then save a copy to {save_path}.",
                    expected_tool_calls=(
                        ToolExpectation("validate_graph"),
                        ToolExpectation("save_graph", arguments={"path": "{save_path}"}),
                    ),
                    semantic_checks=(
                        {
                            "kind": "connection_absent",
                            "connection_id": "blocks_throttle_0:0->blocks_tag_debug_0:0",
                        },
                        {
                            "kind": "connection_present",
                            "connection_id": (
                                "blocks_vector_source_x_0_0:0->blocks_tag_debug_0:0"
                            ),
                        },
                        {
                            "kind": "tool_result",
                            "tool": "validate_graph",
                            "arguments": {"valid": True},
                        },
                        {"kind": "saved_path_valid", "path": "{save_path}"},
                        {
                            "kind": "saved_connection_absent",
                            "path": "{save_path}",
                            "connection_id": "blocks_throttle_0:0->blocks_tag_debug_0:0",
                        },
                        {
                            "kind": "saved_connection_present",
                            "path": "{save_path}",
                            "connection_id": (
                                "blocks_vector_source_x_0_0:0->blocks_tag_debug_0:0"
                            ),
                        },
                    ),
                ),
            ),
            description=(
                "Clarification-backed old-edge rewire on an installed digital "
                "example, followed by explicit validate/save and saved-graph "
                "reload proof that the selected rewire persisted."
            ),
        ),
        _scenario_if_present(
            category="external",
            name="burst_shaper_duplicate_family_summary",
            relative_path="digital/burst_shaper.grc",
            prompt=(
                "Summarize this installed burst shaper example and include the "
                "duplicate-looking block families."
            ),
            expected_tool_calls=(ToolExpectation("summarize_graph"),),
            semantic_checks=(
                {"kind": "exact_graph_delta", "delta": {}},
                {"kind": "no_mutation"},
            ),
            description=(
                "Read-only identity/context inspection on an installed graph with "
                "multiple same-family block instances. block_uid remains read-only."
            ),
        ),
        _scenario_if_present(
            category="external_edit",
            name="stream_demux_lengths_edit_validate",
            relative_path="blocks/stream_demux_demo.grc",
            prompt=(
                "Set blocks_stream_demux_0 lengths to (1,3,5) in this installed "
                "stream demux demo, then validate it."
            ),
            expected_tool_calls=(
                ToolExpectation(
                    "apply_edit",
                    transaction_operations=(
                        {
                            "op_type": "update_params",
                            "instance_name": "blocks_stream_demux_0",
                            "params": {"lengths": [1, 3, 5]},
                        },
                    ),
                ),
                ToolExpectation("validate_graph"),
            ),
            semantic_checks=(
                {
                    "kind": "exact_graph_delta",
                    "delta": {
                        "block_params": {
                            "blocks_stream_demux_0": {"lengths": [1, 3, 5]}
                        },
                        "dirty": True,
                        "validation_status": "valid",
                        "validation_returncode": 0,
                    },
                },
                {
                    "kind": "block_param_equals",
                    "instance_name": "blocks_stream_demux_0",
                    "param": "lengths",
                    "value": [1, 3, 5],
                },
                {
                    "kind": "tool_result",
                    "tool": "validate_graph",
                    "arguments": {"valid": True},
                },
            ),
            description="Verified stream-demux block-parameter edit on an installed blocks example.",
        ),
        _scenario_if_present(
            category="external_edit",
            name="tags_samp_rate_edit_validate",
            relative_path="tags/test_tag_prop.grc",
            prompt="Change samp_rate to 16000 in this installed tag propagation example, then validate it.",
            expected_tool_calls=(
                ToolExpectation(
                    "apply_edit",
                    transaction_operations=(
                        {
                            "op_type": "update_params",
                            "instance_name": "samp_rate",
                            "params": {"value": "16000"},
                        },
                    ),
                ),
                ToolExpectation("validate_graph"),
            ),
            semantic_checks=(
                {
                    "kind": "exact_graph_delta",
                    "delta": {
                        "variables": {"samp_rate": "16000"},
                        "block_params": {"samp_rate": {"value": "16000"}},
                        "dirty": True,
                        "validation_status": "valid",
                        "validation_returncode": 0,
                    },
                },
                {"kind": "variable_equals", "name": "samp_rate", "value": "16000"},
                {
                    "kind": "tool_result",
                    "tool": "validate_graph",
                    "arguments": {"valid": True},
                },
            ),
            description="Verified variable edit on an installed tag propagation example.",
        ),
        _scenario_if_present(
            category="external_edit",
            name="qtgui_message_inputs_pkt_len_edit_validate",
            relative_path="qt-gui/qtgui_message_inputs.grc",
            prompt=(
                "Change pkt_len to 512 in this installed Qt GUI message inputs "
                "example, then validate it."
            ),
            expected_tool_calls=(
                ToolExpectation(
                    "apply_edit",
                    transaction_operations=(
                        {
                            "op_type": "update_params",
                            "instance_name": "pkt_len",
                            "params": {"value": "512"},
                        },
                    ),
                ),
                ToolExpectation("validate_graph"),
            ),
            semantic_checks=(
                {
                    "kind": "exact_graph_delta",
                    "delta": {
                        "variables": {"pkt_len": "512"},
                        "block_params": {"pkt_len": {"value": "512"}},
                        "dirty": True,
                        "validation_status": "valid",
                        "validation_returncode": 0,
                    },
                },
                {"kind": "variable_equals", "name": "pkt_len", "value": "512"},
                {
                    "kind": "tool_result",
                    "tool": "validate_graph",
                    "arguments": {"valid": True},
                },
            ),
            description=(
                "Verified variable edit on an installed Qt GUI/message-port example."
            ),
        ),
        _multi_turn_scenario_if_present(
            category="external_duplicate",
            name="fec_duplicate_polys_uid_target_ref_clarification",
            relative_path="fec/fecapi_cc_decoders.grc",
            turns=(
                LiveTurnSpec(
                    prompt=(
                        "Set the duplicate variable named polys to value 1 in "
                        "this installed FEC decoder example. If multiple blocks "
                        "match, ask me to choose the exact target."
                    ),
                    expected_tool_calls=(
                        ToolExpectation(
                            "apply_edit",
                            transaction_operations=(
                                {
                                    "op_type": "update_params",
                                    "instance_name": "polys",
                                    "params": {"value": "1"},
                                },
                            ),
                            require_result_ok=False,
                        ),
                    ),
                    semantic_checks=(
                        {"kind": "exact_graph_delta", "delta": {}},
                        {
                            "kind": "tool_result",
                            "tool": "apply_edit",
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
                        {
                            "kind": "uid_exact_graph_delta",
                            "delta": {
                                "block_params_by_uid": {
                                    FEC_DUPLICATE_POLYS_UID_A: {"value": "1"}
                                },
                                "dirty": True,
                                "validation_status": "valid",
                                "validation_returncode": 0,
                            },
                        },
                        {
                            "kind": "uid_block_param_equals",
                            "block_uid": FEC_DUPLICATE_POLYS_UID_A,
                            "param": "value",
                            "value": "1",
                        },
                    ),
                ),
            ),
            description=(
                "Guarded target_ref duplicate-block clarification on an installed "
                "FEC graph with UID-aware exact graph-delta proof."
            ),
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
