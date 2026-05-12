"""Native MVP R7 external-example eval.

This suite uses installed GNU Radio examples as source graphs, but the shared
live-eval harness copies every fixture into an isolated temporary workspace
before loading or mutating it. Installed examples must never be edited in-place.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tests.llama_eval.harness import LiveScenario, LiveTurnSpec, ToolExpectation

GNU_EXAMPLES = Path("/usr/share/gnuradio/examples")
GNU_RADIO_VERSION = "3.10.9.2"


@dataclass(frozen=True)
class ExternalCandidate:
    capability: str
    relative_path: str
    block_count: int
    connection_count: int
    why_useful: str
    expected_safe_operation: str
    expected_graph_delta: dict[str, Any]

    @property
    def source_path(self) -> Path:
        return GNU_EXAMPLES / self.relative_path


EXTERNAL_CANDIDATES: tuple[ExternalCandidate, ...] = (
    ExternalCandidate(
        capability="set_param",
        relative_path="audio/dial_tone.grc",
        block_count=8,
        connection_count=4,
        why_useful="small installed graph with canonical samp_rate variable usage",
        expected_safe_operation="set samp_rate to 44100 and validate copied graph",
        expected_graph_delta={
            "variables": {"samp_rate": "44100"},
            "block_params": {"samp_rate": {"value": "44100"}},
            "dirty": True,
            "validation_status": "valid",
            "validation_returncode": 0,
        },
    ),
    ExternalCandidate(
        capability="set_state",
        relative_path="digital/packet/simple_bpsk_tx.grc",
        block_count=25,
        connection_count=14,
        why_useful="installed packet graph with disabled tag debug block that can be enabled",
        expected_safe_operation="enable blocks_tag_debug_0 and validate copied graph",
        expected_graph_delta={
            "block_states": {"blocks_tag_debug_0": "enabled"},
            "dirty": True,
            "validation_status": "valid",
            "validation_returncode": 0,
        },
    ),
    ExternalCandidate(
        capability="disconnect",
        relative_path="digital/packet/tx_stage0.grc",
        block_count=4,
        connection_count=3,
        why_useful="small installed message-port graph with a removable debug connection",
        expected_safe_operation="remove one exact message connection and validate copied graph",
        expected_graph_delta={
            "removed_connections": [
                "pdu_random_pdu_0:pdus->blocks_message_debug_0:print_pdu"
            ],
            "dirty": True,
            "validation_status": "valid",
            "validation_returncode": 0,
        },
    ),
    ExternalCandidate(
        capability="rewire",
        relative_path="digital/burst_shaper.grc",
        block_count=16,
        connection_count=10,
        why_useful="installed stream graph with exact old edge and compatible alternate source",
        expected_safe_operation="rewire tag_debug input from throttle to vector source and validate",
        expected_graph_delta={
            "removed_connections": ["blocks_throttle_0:0->blocks_tag_debug_0:0"],
            "added_connections": [
                "blocks_vector_source_x_0_0:0->blocks_tag_debug_0:0"
            ],
            "dirty": True,
            "validation_status": "valid",
            "validation_returncode": 0,
        },
    ),
    ExternalCandidate(
        capability="insert_block",
        relative_path="audio/dial_tone.grc",
        block_count=8,
        connection_count=4,
        why_useful="small installed stream graph with a compatible float connection",
        expected_safe_operation="insert one throttle block on an exact stream connection",
        expected_graph_delta={
            "added_blocks": ["blocks_throttle2_r7"],
            "removed_connections": ["analog_sig_source_x_0:0->blocks_add_xx:0"],
            "added_connections": [
                "analog_sig_source_x_0:0->blocks_throttle2_r7:0",
                "blocks_throttle2_r7:0->blocks_add_xx:0",
            ],
            "dirty": True,
            "validation_status": "valid",
            "validation_returncode": 0,
        },
    ),
    ExternalCandidate(
        capability="remove_block",
        relative_path="blocks/selector.grc",
        block_count=10,
        connection_count=5,
        why_useful="installed graph where connected remove must fail closed without detach",
        expected_safe_operation="refuse connected block removal without mutation",
        expected_graph_delta={},
    ),
    ExternalCandidate(
        capability="add_variable",
        relative_path="audio/dial_tone.grc",
        block_count=8,
        connection_count=4,
        why_useful="small installed graph that validates after adding an unused variable",
        expected_safe_operation="add r7_gain variable and validate copied graph",
        expected_graph_delta={
            "added_blocks": ["r7_gain"],
            "dirty": True,
            "validation_status": "valid",
            "validation_returncode": 0,
        },
    ),
    ExternalCandidate(
        capability="save_load",
        relative_path="blocks/selector.grc",
        block_count=10,
        connection_count=5,
        why_useful="installed graph suitable for explicit save-copy and load-copy lifecycle checks",
        expected_safe_operation="save to explicit copy path or load copied target path",
        expected_graph_delta={},
    ),
)


def _source_path(relative_path: str) -> str:
    return str((GNU_EXAMPLES / relative_path).resolve())


def _metadata_description(candidate: ExternalCandidate) -> str:
    return (
        f"Source: {candidate.source_path}; copied working path: isolated temp workspace; "
        f"GNU Radio: {GNU_RADIO_VERSION}; blocks: {candidate.block_count}; "
        f"connections: {candidate.connection_count}; why: {candidate.why_useful}; "
        f"expected safe operation: {candidate.expected_safe_operation}; "
        f"expected graph delta: {candidate.expected_graph_delta}"
    )


def _scenario_if_present(candidate: ExternalCandidate, scenario: LiveScenario) -> LiveScenario | None:
    if not candidate.source_path.exists():
        return None
    return scenario


def _r7_cases() -> list[LiveScenario]:
    by_capability = {candidate.capability: candidate for candidate in EXTERNAL_CANDIDATES}
    cases: list[LiveScenario | None] = [
        _scenario_if_present(
            by_capability["set_param"],
            LiveScenario(
                category="external_set_param",
                name="dial_tone_set_samp_rate",
                fixture_name=_source_path("audio/dial_tone.grc"),
                release_profile="R7_EXTERNAL_EXAMPLES",
                description=_metadata_description(by_capability["set_param"]),
                turns=(
                    LiveTurnSpec(
                        prompt=(
                            "Use the change_graph tool now with this exact JSON args object: "
                            "{{\"operation_kind\":\"set_param\",\"dry_run\":false,"
                            "\"instance_name\":\"samp_rate\",\"param\":\"value\","
                            "\"value\":\"44100\",\"user_goal\":\"external set_param validation\"}}."
                        ),
                        expected_tool_calls=(
                            ToolExpectation(
                                "change_graph",
                                arguments={
                                    "operation_kind": "set_param",
                                    "dry_run": False,
                                    "instance_name": "samp_rate",
                                    "param": "value",
                                    "value": "44100",
                                },
                            ),
                        ),
                        semantic_checks=(
                            {"kind": "exact_graph_delta", "delta": by_capability["set_param"].expected_graph_delta},
                            {"kind": "variable_equals", "name": "samp_rate", "value": "44100"},
                        ),
                    ),
                ),
            ),
        ),
        _scenario_if_present(
            by_capability["set_state"],
            LiveScenario(
                category="external_set_state",
                name="simple_bpsk_enable_tag_debug",
                fixture_name=_source_path("digital/packet/simple_bpsk_tx.grc"),
                release_profile="R7_EXTERNAL_EXAMPLES",
                description=_metadata_description(by_capability["set_state"]),
                turns=(
                    LiveTurnSpec(
                        prompt=(
                            "Use the change_graph tool now with this exact JSON args object: "
                            "{{\"operation_kind\":\"set_state\",\"dry_run\":false,"
                            "\"instance_name\":\"blocks_tag_debug_0\",\"state\":\"enabled\","
                            "\"user_goal\":\"external set_state validation\"}}."
                        ),
                        expected_tool_calls=(
                            ToolExpectation(
                                "change_graph",
                                arguments={
                                    "operation_kind": "set_state",
                                    "dry_run": False,
                                    "instance_name": "blocks_tag_debug_0",
                                    "state": "enabled",
                                },
                            ),
                        ),
                        semantic_checks=(
                            {"kind": "exact_graph_delta", "delta": by_capability["set_state"].expected_graph_delta},
                            {
                                "kind": "block_state_equals",
                                "instance_name": "blocks_tag_debug_0",
                                "state": "enabled",
                            },
                        ),
                    ),
                ),
            ),
        ),
        _scenario_if_present(
            by_capability["disconnect"],
            LiveScenario(
                category="external_disconnect",
                name="tx_stage0_message_disconnect",
                fixture_name=_source_path("digital/packet/tx_stage0.grc"),
                release_profile="R7_EXTERNAL_EXAMPLES",
                description=_metadata_description(by_capability["disconnect"]),
                turns=(
                    LiveTurnSpec(
                        prompt=(
                            "Use the change_graph tool now with this exact JSON args object: "
                            "{{\"operation_kind\":\"disconnect\",\"dry_run\":false,"
                            "\"connection_id\":\"pdu_random_pdu_0:pdus->blocks_message_debug_0:print_pdu\","
                            "\"user_goal\":\"external disconnect validation\"}}."
                        ),
                        expected_tool_calls=(
                            ToolExpectation(
                                "change_graph",
                                arguments={
                                    "operation_kind": "disconnect",
                                    "dry_run": False,
                                    "connection_id": "pdu_random_pdu_0:pdus->blocks_message_debug_0:print_pdu",
                                },
                            ),
                        ),
                        semantic_checks=(
                            {"kind": "exact_graph_delta", "delta": by_capability["disconnect"].expected_graph_delta},
                            {
                                "kind": "connection_absent",
                                "connection_id": "pdu_random_pdu_0:pdus->blocks_message_debug_0:print_pdu",
                            },
                        ),
                    ),
                ),
            ),
        ),
        _scenario_if_present(
            by_capability["rewire"],
            LiveScenario(
                category="external_rewire",
                name="burst_shaper_stream_rewire",
                fixture_name=_source_path("digital/burst_shaper.grc"),
                release_profile="R7_EXTERNAL_EXAMPLES",
                description=_metadata_description(by_capability["rewire"]),
                turns=(
                    LiveTurnSpec(
                        prompt=(
                            "Use the change_graph tool now with this exact JSON args object: "
                            "{{\"operation_kind\":\"rewire\",\"dry_run\":false,"
                            "\"connection_id\":\"blocks_throttle_0:0->blocks_tag_debug_0:0\","
                            "\"new_src_block\":\"blocks_vector_source_x_0_0\",\"new_src_port\":0,"
                            "\"new_dst_block\":\"blocks_tag_debug_0\",\"new_dst_port\":0,"
                            "\"user_goal\":\"external rewire validation\"}}."
                        ),
                        expected_tool_calls=(
                            ToolExpectation(
                                "change_graph",
                                arguments={
                                    "operation_kind": "rewire",
                                    "dry_run": False,
                                },
                            ),
                        ),
                        semantic_checks=(
                            {"kind": "exact_graph_delta", "delta": by_capability["rewire"].expected_graph_delta},
                            {
                                "kind": "connection_absent",
                                "connection_id": "blocks_throttle_0:0->blocks_tag_debug_0:0",
                            },
                            {
                                "kind": "connection_present",
                                "connection_id": "blocks_vector_source_x_0_0:0->blocks_tag_debug_0:0",
                            },
                        ),
                    ),
                ),
            ),
        ),
        _scenario_if_present(
            by_capability["insert_block"],
            LiveScenario(
                category="external_insert",
                name="dial_tone_insert_throttle_on_connection",
                fixture_name=_source_path("audio/dial_tone.grc"),
                release_profile="R7_EXTERNAL_EXAMPLES",
                description=_metadata_description(by_capability["insert_block"]),
                turns=(
                    LiveTurnSpec(
                        prompt=(
                            "Use the change_graph tool now with this exact JSON args object: "
                            "{{\"operation_kind\":\"insert_block\",\"dry_run\":false,"
                            "\"connection_id\":\"analog_sig_source_x_0:0->blocks_add_xx:0\","
                            "\"block_id\":\"blocks_throttle2\",\"instance_name\":\"blocks_throttle2_r7\","
                            "\"insert_params\":{{\"type\":\"float\",\"samples_per_second\":\"samp_rate\"}},"
                            "\"user_goal\":\"external insert validation\"}}."
                        ),
                        expected_tool_calls=(
                            ToolExpectation(
                                "change_graph",
                                arguments={"operation_kind": "insert_block", "dry_run": False},
                            ),
                        ),
                        semantic_checks=(
                            {"kind": "exact_graph_delta", "delta": by_capability["insert_block"].expected_graph_delta},
                            {
                                "kind": "connection_absent",
                                "connection_id": "analog_sig_source_x_0:0->blocks_add_xx:0",
                            },
                            {
                                "kind": "connection_present",
                                "connection_id": "analog_sig_source_x_0:0->blocks_throttle2_r7:0",
                            },
                            {
                                "kind": "connection_present",
                                "connection_id": "blocks_throttle2_r7:0->blocks_add_xx:0",
                            },
                        ),
                    ),
                ),
            ),
        ),
        _scenario_if_present(
            by_capability["remove_block"],
            LiveScenario(
                category="external_remove",
                name="selector_connected_remove_refused",
                fixture_name=_source_path("blocks/selector.grc"),
                release_profile="R7_EXTERNAL_EXAMPLES",
                description=_metadata_description(by_capability["remove_block"]),
                turns=(
                    LiveTurnSpec(
                        prompt=(
                            "Use the change_graph tool now with this exact JSON args object: "
                            "{{\"operation_kind\":\"remove_block\",\"dry_run\":false,"
                            "\"instance_name\":\"blocks_selector_0\","
                            "\"user_goal\":\"external connected remove without detach\"}}."
                        ),
                        expected_tool_calls=(
                            ToolExpectation(
                                "change_graph",
                                arguments={"operation_kind": "remove_block", "dry_run": False},
                                require_result_ok=False,
                            ),
                        ),
                        semantic_checks=(
                            {"kind": "exact_graph_delta", "delta": {}},
                            {"kind": "no_mutation"},
                            {
                                "kind": "tool_result",
                                "tool": "change_graph",
                                "arguments": {"ok": False},
                            },
                        ),
                    ),
                ),
            ),
        ),
        _scenario_if_present(
            by_capability["add_variable"],
            LiveScenario(
                category="external_add_variable",
                name="dial_tone_add_variable",
                fixture_name=_source_path("audio/dial_tone.grc"),
                release_profile="R7_EXTERNAL_EXAMPLES",
                description=_metadata_description(by_capability["add_variable"]),
                turns=(
                    LiveTurnSpec(
                        prompt=(
                            "Use the change_graph tool now with this exact JSON args object: "
                            "{{\"operation_kind\":\"add_variable\",\"dry_run\":false,"
                            "\"variable_name\":\"r7_gain\",\"variable_value\":\"0.25\","
                            "\"user_goal\":\"external add_variable validation\"}}."
                        ),
                        expected_tool_calls=(
                            ToolExpectation(
                                "change_graph",
                                arguments={
                                    "operation_kind": "add_variable",
                                    "dry_run": False,
                                    "variable_name": "r7_gain",
                                    "variable_value": "0.25",
                                },
                            ),
                        ),
                        semantic_checks=(
                            {"kind": "exact_graph_delta", "delta": by_capability["add_variable"].expected_graph_delta},
                            {"kind": "variable_equals", "name": "r7_gain", "value": "0.25"},
                        ),
                    ),
                ),
            ),
        ),
        _scenario_if_present(
            by_capability["save_load"],
            LiveScenario(
                category="external_lifecycle",
                name="selector_save_copy",
                fixture_name=_source_path("blocks/selector.grc"),
                release_profile="R7_EXTERNAL_EXAMPLES",
                description=_metadata_description(by_capability["save_load"]),
                turns=(
                    LiveTurnSpec(
                        prompt="Save this copied installed selector graph to {save_path}.",
                        expected_tool_calls=(
                            ToolExpectation("save_graph_explicit", arguments={"path": "{save_path}"}),
                        ),
                        semantic_checks=(
                            {"kind": "saved_path_valid", "path": "{save_path}", "copy": True},
                        ),
                    ),
                ),
            ),
        ),
        _scenario_if_present(
            by_capability["save_load"],
            LiveScenario(
                category="external_lifecycle",
                name="selector_load_copy",
                fixture_name=_source_path("audio/dial_tone.grc"),
                target_fixture_name=_source_path("blocks/selector.grc"),
                release_profile="R7_EXTERNAL_EXAMPLES",
                description=_metadata_description(by_capability["save_load"]),
                turns=(
                    LiveTurnSpec(
                        prompt="Load the copied installed selector graph at {target_path}.",
                        expected_tool_calls=(
                            ToolExpectation("load_graph_explicit", arguments={"path": "{target_path}"}),
                        ),
                        semantic_checks=(
                            {
                                "kind": "tool_result",
                                "tool": "load_graph_explicit",
                                "arguments": {"ok": True, "path": "{target_path}", "valid": True},
                            },
                            {"kind": "path_equals", "path": "{target_path}"},
                        ),
                    ),
                ),
            ),
        ),
    ]
    return [case for case in cases if case is not None]


def release_cases() -> list[LiveScenario]:
    return _r7_cases()


R7_EXTERNAL_CASES = release_cases()
