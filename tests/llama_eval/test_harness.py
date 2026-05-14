"""Unit tests for shared llama eval harness helpers."""

from __future__ import annotations

import copy
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from types import SimpleNamespace
from unittest import mock

import yaml

from grc_agent.agent import GrcAgent
from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.recovery import (
    NONRECOVERABLE_INVALID_END_STATE,
)

from tests.llama_eval.harness import (
    RUN_STATUS_INFRA_BANNER,
    RUN_STATUS_INFRA_FAIL,
    LiveScenario,
    LiveTurnSpec,
    ToolExpectation,
    case_run_stability,
    collect_backend_metadata,
    build_persisted_run_entry,
    dimension_pass_counts,
    default_phase_summary,
    evaluate_semantic_checks,
    evaluate_tool_expectations,
    evaluate_turn_recovery,
    executed_tool_calls_since,
    extract_executed_tool_calls,
    extract_requested_tool_calls,
    fixture_path,
    format_run_status_for_cli,
    graph_delta,
    graph_block_param_value,
    graph_block_state,
    graph_block_uid,
    graph_block_param_value_by_uid,
    graph_block_state_by_uid,
    graph_snapshot,
    graph_variable_value,
    is_infra_error_message,
    normalize_transaction_operations,
    render_prompt,
    render_value_templates,
    run_live_scenario_once,
    requested_tool_calls_since,
    run_phase_eval,
    run_result_is_infra_failure,
    saved_graph_reloads_and_validates,
    snapshot_changed,
    stability_summary,
    text_contains_any,
    tool_call_matches_argument_checks,
    tool_call_matches_transaction_checks,
    tools_appear_in_expected_order,
    uid_graph_delta,
)


class ExtractToolCallsTests(unittest.TestCase):
    def test_extract_requested_tool_calls_parses_openai_style_payload(self) -> None:
        history = [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "apply_edit",
                            "arguments": '{"transaction": {"op_type": "update_params"}}',
                        },
                    }
                ],
            }
        ]

        self.assertEqual(
            extract_requested_tool_calls(history),
            [
                {
                    "name": "apply_edit",
                    "arguments": {"transaction": {"op_type": "update_params"}},
                }
            ],
        )

    def test_extract_requested_tool_calls_parses_fake_runtime_payload(self) -> None:
        history = [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "name": "validate_graph",
                        "arguments": {},
                    }
                ],
            }
        ]

        self.assertEqual(
            extract_requested_tool_calls(history),
            [{"name": "validate_graph", "arguments": {}}],
        )

    def test_extract_executed_tool_calls_reads_tool_turns(self) -> None:
        history = [
            {"role": "tool", "name": "search_grc", "content": {"ok": True}},
            {"role": "assistant", "content": "done"},
        ]

        self.assertEqual(
            extract_executed_tool_calls(history),
            [{"name": "search_grc", "arguments": {"ok": True}}],
        )


class SequenceMatchingTests(unittest.TestCase):
    def test_expected_tools_must_appear_in_order(self) -> None:
        self.assertTrue(
            tools_appear_in_expected_order(
                ["search_grc", "describe_block", "apply_edit"],
                ["search_grc", "describe_block"],
            )
        )

    def test_later_tool_cannot_appear_before_earlier_expected_tool(self) -> None:
        self.assertFalse(
            tools_appear_in_expected_order(
                ["describe_block", "search_grc", "describe_block"],
                ["search_grc", "describe_block"],
            )
        )

    def test_empty_expectation_requires_no_tools(self) -> None:
        self.assertTrue(tools_appear_in_expected_order([], []))
        self.assertFalse(tools_appear_in_expected_order(["save_graph"], []))

    def test_duplicate_expected_tools_require_duplicate_calls(self) -> None:
        self.assertTrue(
            tools_appear_in_expected_order(
                ["apply_edit", "apply_edit", "validate_graph"],
                ["apply_edit", "apply_edit"],
            )
        )
        self.assertFalse(
            tools_appear_in_expected_order(
                ["apply_edit", "validate_graph"],
                ["apply_edit", "apply_edit"],
            )
        )


class TransactionMatchingTests(unittest.TestCase):
    def test_argument_checks_match_partial_raw_arguments(self) -> None:
        tool_call = {
            "arguments": {
                "file_path": "/tmp/example.grc",
                "mode": "ignored",
            }
        }

        self.assertTrue(
            tool_call_matches_argument_checks(
                tool_call,
                {"file_path": "/tmp/example.grc"},
            )
        )

    def test_normalize_transaction_operations_handles_single_object(self) -> None:
        self.assertEqual(
            normalize_transaction_operations(
                {"transaction": {"op_type": "remove_connection"}}
            ),
            [{"op_type": "remove_connection"}],
        )

    def test_normalize_transaction_operations_handles_list(self) -> None:
        self.assertEqual(
            normalize_transaction_operations(
                {
                    "transaction": [
                        {"op_type": "update_params"},
                        {"op_type": "add_connection"},
                    ]
                }
            ),
            [{"op_type": "update_params"}, {"op_type": "add_connection"}],
        )

    def test_normalize_transaction_operations_expands_remove_connection_id(self) -> None:
        self.assertEqual(
            normalize_transaction_operations(
                {
                    "transaction": {
                        "op_type": "remove_connection",
                        "connection_id": "analog_random_source_x_0:0->blocks_throttle2_0:0",
                    }
                }
            ),
            [
                {
                    "op_type": "remove_connection",
                    "connection_id": "analog_random_source_x_0:0->blocks_throttle2_0:0",
                    "src_block": "analog_random_source_x_0",
                    "src_port": 0,
                    "dst_block": "blocks_throttle2_0",
                    "dst_port": 0,
                }
            ],
        )

    def test_transaction_checks_match_ordered_partial_operations(self) -> None:
        tool_call = {
            "arguments": {
                "transaction": [
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
                ]
            }
        }

        self.assertTrue(
            tool_call_matches_transaction_checks(
                tool_call,
                [
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
            )
        )

    def test_transaction_checks_fail_when_expected_operation_missing(self) -> None:
        tool_call = {
            "arguments": {
                "transaction": {
                    "op_type": "remove_connection",
                    "src_block": "analog_random_source_x_0",
                    "src_port": 0,
                    "dst_block": "blocks_throttle2_0",
                    "dst_port": 0,
                }
            }
        }

        self.assertFalse(
            tool_call_matches_transaction_checks(
                tool_call,
                [
                    {
                        "op_type": "remove_connection",
                        "src_block": "blocks_char_to_float_0",
                    }
                ],
            )
        )


class ToolExpectationTests(unittest.TestCase):
    def test_evaluate_tool_expectations_splits_routing_arguments_and_success(self) -> None:
        requested = [
            {
                "name": "apply_edit",
                "arguments": {
                    "transaction": {
                        "op_type": "update_params",
                        "instance_name": "samp_rate",
                        "params": {"value": "48000"},
                    }
                },
            }
        ]
        executed = [{"name": "apply_edit", "arguments": {"ok": True}}]

        result = evaluate_tool_expectations(
            requested_tool_calls=requested,
            executed_tool_calls=executed,
            expected_tool_calls=(
                ToolExpectation(
                    name="apply_edit",
                    transaction_operations=(
                        {
                            "op_type": "update_params",
                            "instance_name": "samp_rate",
                            "params": {"value": "48000"},
                        },
                    ),
                ),
            ),
        )

        self.assertTrue(result["routing_pass"])
        self.assertTrue(result["argument_pass"])
        self.assertTrue(result["tool_success_pass"])

    def test_evaluate_tool_expectations_detects_wrong_arguments(self) -> None:
        requested = [
            {
                "name": "apply_edit",
                "arguments": {
                    "transaction": {
                        "op_type": "update_params",
                        "instance_name": "samp_rate",
                        "params": {"value": "96000"},
                    }
                },
            }
        ]
        executed = [{"name": "apply_edit", "arguments": {"ok": True}}]

        result = evaluate_tool_expectations(
            requested_tool_calls=requested,
            executed_tool_calls=executed,
            expected_tool_calls=(
                ToolExpectation(
                    name="apply_edit",
                    transaction_operations=(
                        {
                            "op_type": "update_params",
                            "instance_name": "samp_rate",
                            "params": {"value": "48000"},
                        },
                    ),
                ),
            ),
        )

        self.assertTrue(result["routing_pass"])
        self.assertFalse(result["argument_pass"])
        self.assertTrue(result["tool_success_pass"])

    def test_evaluate_tool_expectations_skips_malformed_same_name_retry(self) -> None:
        requested = [
            {
                "name": "apply_edit",
                "arguments": {"__invalid_json_arguments__": "{not valid json"},
            },
            {
                "name": "apply_edit",
                "arguments": {
                    "transaction": {
                        "op_type": "update_params",
                        "instance_name": "samp_rate",
                        "params": {"value": "48000"},
                    }
                },
            },
        ]
        executed = [
            {"name": "apply_edit", "arguments": {"ok": False}},
            {"name": "apply_edit", "arguments": {"ok": True}},
        ]

        result = evaluate_tool_expectations(
            requested_tool_calls=requested,
            executed_tool_calls=executed,
            expected_tool_calls=(
                ToolExpectation(
                    name="apply_edit",
                    transaction_operations=(
                        {
                            "op_type": "update_params",
                            "instance_name": "samp_rate",
                            "params": {"value": "48000"},
                        },
                    ),
                ),
            ),
        )

        self.assertTrue(result["routing_pass"], result)
        self.assertTrue(result["argument_pass"], result)
        self.assertTrue(result["tool_success_pass"], result)

    def test_evaluate_tool_expectations_allows_safe_text_only_when_enabled(self) -> None:
        result = evaluate_tool_expectations(
            requested_tool_calls=[],
            executed_tool_calls=[],
            expected_tool_calls=(ToolExpectation(name="search_manual"),),
            allow_safe_text_only=True,
        )

        self.assertTrue(result["routing_pass"])
        self.assertTrue(result["argument_pass"])
        self.assertTrue(result["tool_success_pass"])


class TextMatchingTests(unittest.TestCase):
    def test_text_contains_any_matches_case_insensitively(self) -> None:
        self.assertTrue(text_contains_any("Undo is not supported.", ["undo", "cannot"]))
        self.assertFalse(text_contains_any("Saved the graph.", ["undo", "cannot"]))


class PartialMatchTests(unittest.TestCase):
    def test_type_coercion_matches_int_against_string(self) -> None:
        from tests.llama_eval.harness import _partial_match

        self.assertTrue(_partial_match(32000, "32000"))
        self.assertTrue(_partial_match("32000", 32000))

    def test_nested_dict_partial_match(self) -> None:
        from tests.llama_eval.harness import _partial_match

        self.assertTrue(_partial_match({"a": 1, "b": 2}, {"a": 1}))
        self.assertFalse(_partial_match({"a": 1}, {"a": 2}))

    def test_list_requires_exact_length(self) -> None:
        from tests.llama_eval.harness import _partial_match

        self.assertTrue(_partial_match([1, 2], [1, 2]))
        self.assertFalse(_partial_match([1, 2, 3], [1, 2]))


class RenderHelpersTests(unittest.TestCase):
    def test_render_prompt_substitutes_target_and_save_path(self) -> None:
        result = render_prompt(
            "Load {target_path} and save to {save_path}.",
            target_path="/a/b.grc",
            save_path="/c/d.grc",
        )
        self.assertEqual(result, "Load /a/b.grc and save to /c/d.grc.")

    def test_render_value_templates_recurses_into_dicts_and_lists(self) -> None:
        value = {
            "path": "{target_path}",
            "nested": ["{save_path}", 42],
        }
        result = render_value_templates(
            value, target_path="/x/y.grc", save_path="/z/w.grc"
        )
        self.assertEqual(result["path"], "/x/y.grc")
        self.assertEqual(result["nested"], ["/z/w.grc", 42])

    def test_render_value_templates_passes_through_non_string_scalars(self) -> None:
        self.assertEqual(
            render_value_templates(123, target_path="", save_path=""), 123
        )

    def test_render_value_templates_preserves_unknown_placeholders(self) -> None:
        self.assertEqual(
            render_value_templates(
                "{after_path}",
                target_path="/x/y.grc",
                save_path="/z/w.grc",
            ),
            "{after_path}",
        )


class FixtureWorkspaceTests(unittest.TestCase):
    def test_isolated_fixture_workspace_accepts_absolute_fixture_paths(self) -> None:
        from tests.llama_eval.harness import isolated_fixture_workspace

        source = fixture_path()
        with isolated_fixture_workspace(str(source)) as (_workspace, paths):
            copied = paths[str(source)]

            self.assertTrue(copied.exists())
            self.assertNotEqual(copied, source)
            self.assertEqual(copied.name, source.name)


class SliceHelpersTests(unittest.TestCase):
    def _make_history(self) -> list:
        return [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "type": "function",
                        "function": {"name": "apply_edit", "arguments": "{}"},
                    }
                ],
            },
            {
                "role": "tool",
                "name": "apply_edit",
                "content": {"ok": True},
            },
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "type": "function",
                        "function": {"name": "validate_graph", "arguments": "{}"},
                    }
                ],
            },
            {
                "role": "tool",
                "name": "validate_graph",
                "content": {"ok": True},
            },
        ]

    def test_requested_tool_calls_since_slices_from_index(self) -> None:
        history = self._make_history()
        result = requested_tool_calls_since(history, 2)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "validate_graph")

    def test_executed_tool_calls_since_slices_from_index(self) -> None:
        history = self._make_history()
        result = executed_tool_calls_since(history, 2)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "validate_graph")

    def test_requested_tool_calls_since_zero_returns_all(self) -> None:
        history = self._make_history()
        result = requested_tool_calls_since(history, 0)
        self.assertEqual(len(result), 2)


class _ScriptedClient:
    temperature = 0.0

    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.responses = responses

    def require_model_alias(self, _model: str) -> None:
        return None

    def create_chat_completion(
        self,
        *,
        model: str,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]],
        tool_choice: str | dict[str, object] = "auto",
        response_format: dict[str, object] | None = None,
    ) -> dict[str, object]:
        _ = (model, messages, tools, tool_choice, response_format)
        if not self.responses:
            raise AssertionError("no scripted response remaining")
        return self.responses.pop(0)

    def parse_assistant_message(
        self,
        response: dict[str, object],
        *,
        fallback_transaction_checker: object = None,
        allowed_tool_names: set[str] | None = None,
        assistant_text_fallback_enabled: bool = True,
    ) -> tuple[str | None, list[object]]:
        _ = (
            fallback_transaction_checker,
            allowed_tool_names,
            assistant_text_fallback_enabled,
        )
        return response["assistant_text"], response["tool_calls"]  # type: ignore[return-value]


class _PropsClient:
    def __init__(self, props: dict[str, object] | Exception) -> None:
        self.props = props

    def get_server_properties(self) -> dict[str, object]:
        if isinstance(self.props, Exception):
            raise self.props
        return self.props


class _ScriptedToolCall:
    def __init__(self, name: str, arguments: dict[str, object]) -> None:
        self.id = f"{name}_1"
        self.name = name
        self.arguments = arguments

    def as_history_tool_call(self) -> dict[str, object]:
        return {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": __import__("json").dumps(self.arguments),
            },
        }


class LiveScenarioRunnerTests(unittest.TestCase):

    def test_mvp_run_with_legacy_tool_call_fails_model_contract_and_passes_runtime_safety(self) -> None:
        client = _ScriptedClient(
            [
                {
                    "assistant_text": None,
                    "tool_calls": [
                        _ScriptedToolCall(
                            "apply_edit",
                            {
                                "transaction": {
                                    "op_type": "update_params",
                                    "instance_name": "samp_rate",
                                    "params": {"value": "48000"},
                                }
                            },
                        )
                    ],
                },
            ]
        )
        scenario = LiveScenario(
            category="edit",
            name="legacy_blocked",
            turns=(
                LiveTurnSpec(
                    prompt="Change samp_rate to 48000.",
                    expected_tool_calls=(
                        ToolExpectation(
                            "change_graph",
                            arguments={"operation_kind": "set_param"},
                        ),
                    ),
                    semantic_checks=(
                        {"kind": "no_mutation"},
                    ),
                ),
            ),
        )

        result = run_live_scenario_once(
            client=client,
            model="model",
            scenario=scenario,
            mvp_tool_profile=True,
        )

        turn = result["turn_results"][0]
        self.assertFalse(result["passed"], result)
        self.assertFalse(turn["model_contract_pass"], turn)
        self.assertTrue(turn["runtime_safety_pass"], turn)
        self.assertFalse(turn["routing_pass"], turn)
        self.assertIn(
            turn["executed_tool_calls_raw"][0]["arguments"]["error_type"],
            {"route_mismatch", "tool_not_allowed_for_surface"},
        )

    def test_persisted_entry_supports_live_turn_specs(self) -> None:
        scenario = LiveScenario(
            category="edit",
            name="set_samp_rate",
            turns=(
                LiveTurnSpec(
                    prompt="Change samp_rate.",
                    expected_tool_calls=(ToolExpectation("apply_edit"),),
                ),
            ),
        )

        entry = build_persisted_run_entry(
            phase=30,
            case=scenario,
            run_index=0,
            run_result={"matched": True, "tools_called": ["apply_edit"]},
            backend_restart_count=0,
        )

        self.assertEqual(
            entry["expected_chain"],
            [{"turn_index": 0, "prompt": "Change samp_rate.", "expected_tools": ["apply_edit"]}],
        )



    def test_run_live_scenario_once_blocks_legacy_execution_for_clarification_reply_in_mvp(self) -> None:
        scenario = LiveScenario(
            category="setup",
            name="pre_turn_clarification",
            fixture_name="rewire_stream_ambiguous.grc",
            turns=(
                LiveTurnSpec(
                    prompt="C",
                    clarification_response=True,
                    pre_turn_tool_name="rewire_connection",
                    pre_turn_tool_args={
                        "old_connection_id": (
                            "blocks_throttle2_0:0->blocks_char_to_float_0:0"
                        ),
                        "new_src_port": 0,
                        "new_dst_block": "blocks_char_to_float_0",
                        "new_dst_port": 0,
                    },
                    pre_turn_allow_clarification=True,
                    expected_tool_calls=(
                        ToolExpectation("rewire_connection", require_result_ok=False),
                    ),
                    semantic_checks=(
                        {"kind": "clarification_mode", "mode": "executed"},
                        {"kind": "no_mutation"},
                        {
                            "kind": "tool_result",
                            "tool": "rewire_connection",
                            "arguments": {
                                "ok": False,
                                "error_type": "tool_not_allowed_for_surface",
                            },
                        },
                    ),
                ),
            ),
        )

        result = run_live_scenario_once(
            client=_ScriptedClient([]),
            model="model",
            scenario=scenario,
            mvp_tool_profile=True,
        )

        self.assertFalse(result["matched"], result)
        self.assertEqual(result["tools_called"], ["rewire_connection"])
        self.assertEqual(
            [call["name"] for call in result["turn_results"][0]["requested_tool_calls_raw"]],
            ["rewire_connection"],
        )
        self.assertEqual(
            [entry["tool"] for entry in result["turn_results"][0]["pre_turn_setup"]],
            ["rewire_connection", "validate_graph"],
        )
        self.assertTrue(
            result["turn_results"][0]["pre_turn_setup"][0]["clarification_required"]
        )
        self.assertTrue(result["turn_results"][0]["pre_turn_setup"][1]["valid"])
        tool_result = result["turn_results"][0]["executed_tool_calls"][0]["arguments"]
        self.assertEqual(tool_result.get("error_type"), "tool_not_allowed_for_surface")

    def test_run_live_scenario_once_rejects_non_mutation_pre_turn_setup(self) -> None:
        scenario = LiveScenario(
            category="setup",
            name="pre_turn_rejects_save",
            turns=(
                LiveTurnSpec(
                    prompt="Validate the current graph.",
                    pre_turn_tool_name="save_graph",
                    pre_turn_tool_args={"path": "{save_path}"},
                    expected_tool_calls=(ToolExpectation("validate_graph"),),
                ),
            ),
        )

        with self.assertRaisesRegex(RuntimeError, "unsupported pre-turn setup tool"):
            run_live_scenario_once(
                client=_ScriptedClient([]),
                model="model",
                scenario=scenario,
            )

    def test_turn_trace_is_present_and_preserves_raw_calls(self) -> None:
        client = _ScriptedClient(
            [
                {
                    "assistant_text": None,
                    "tool_calls": [
                        _ScriptedToolCall(
                            "change_graph",
                            {
                                "dry_run": True,
                                "operation_kind": "set_param",
                                "user_goal": "preview samp_rate edit",
                                "instance_name": "samp_rate",
                                "param_key": "value",
                                "param_value": "48000",
                            },
                        )
                    ],
                },
                {"assistant_text": "Preview complete.", "tool_calls": []},
            ]
        )
        scenario = LiveScenario(
            category="trace",
            name="trace_preserves_raw",
            turns=(
                LiveTurnSpec(
                    prompt="Preview setting samp_rate to 48000.",
                    expected_tool_calls=(
                        ToolExpectation(
                            "change_graph",
                            arguments={"operation_kind": "set_param", "dry_run": True},
                        ),
                    ),
                    semantic_checks=(
                        {"kind": "no_mutation"},
                    ),
                ),
            ),
        )

        result = run_live_scenario_once(
            client=client,
            model="model",
            scenario=scenario,
            mvp_tool_profile=True,
        )

        turn = result["turn_results"][0]
        trace = turn.get("trace")
        self.assertIsInstance(trace, dict, turn)
        self.assertEqual(trace.get("prompt"), scenario.turns[0].prompt)
        self.assertEqual(trace.get("active_tool_surface"), "mvp")
        self.assertEqual(
            trace.get("raw_requested_tool_calls"),
            turn.get("requested_tool_calls_raw"),
        )
        self.assertIn("failure_category", trace)

    def test_trace_captures_model_contract_failure_for_raw_legacy_call(self) -> None:
        client = _ScriptedClient(
            [
                {
                    "assistant_text": None,
                    "tool_calls": [
                        _ScriptedToolCall(
                            "apply_edit",
                            {
                                "transaction": {
                                    "op_type": "update_params",
                                    "instance_name": "samp_rate",
                                    "params": {"value": "48000"},
                                }
                            },
                        )
                    ],
                },
            ]
        )
        scenario = LiveScenario(
            category="trace",
            name="trace_model_contract_failure",
            turns=(
                LiveTurnSpec(
                    prompt="Change samp_rate to 48000.",
                    expected_tool_calls=(ToolExpectation("change_graph"),),
                    semantic_checks=({"kind": "no_mutation"},),
                ),
            ),
        )

        result = run_live_scenario_once(
            client=client,
            model="model",
            scenario=scenario,
            mvp_tool_profile=True,
        )

        turn = result["turn_results"][0]
        trace = turn["trace"]
        self.assertFalse(turn["model_contract_pass"], turn)
        self.assertEqual(trace["failure_category"], "model_contract", trace)

    def test_later_tool_result_match_does_not_hide_earlier_mutation(self) -> None:
        before_snapshot = {
            "raw_hash": "before",
            "state_revision": 1,
            "connection_ids": [],
            "block_names": [],
            "variable_values": {},
            "blocks_by_name": {},
            "blocks_by_uid": {},
            "duplicate_block_groups": {},
            "dirty": False,
            "validation_status": "valid",
            "validation_returncode": 0,
        }
        after_snapshot = {
            **before_snapshot,
            "raw_hash": "after",
            "state_revision": 2,
            "dirty": True,
        }
        run_result = {
            "executed_tool_calls": [
                {
                    "name": "change_graph",
                    "arguments": {"ok": False, "error_type": "invalid_request"},
                },
                {
                    "name": "change_graph",
                    "arguments": {"ok": True, "message": "later payload"},
                },
            ],
            "requested_tool_calls": [],
            "assistant_text": "",
            "clarification_result": None,
        }
        result = evaluate_semantic_checks(
            checks=(
                {"kind": "tool_result", "tool": "change_graph", "arguments": {"ok": True}},
                {"kind": "no_mutation"},
            ),
            before_snapshot=before_snapshot,
            after_snapshot=after_snapshot,
            run_result=run_result,
            save_path="",
        )

        self.assertTrue(result["semantic_details"][0]["passed"], result["semantic_details"])
        self.assertFalse(result["semantic_details"][1]["passed"], result["semantic_details"])
        self.assertFalse(result["safety_pass"], result)
        self.assertFalse(result["end_state_pass"], result)
        self.assertFalse(result["semantic_pass"], result)


class RecoveryHarnessTests(unittest.TestCase):
    def test_nonrecoverable_failure_is_classified_without_model_retry(self) -> None:
        client = _ScriptedClient([])
        agent = GrcAgent()
        history_start = len(agent.history)
        executed_tool_calls = [
            {
                "name": "apply_edit",
                "arguments": {
                    "ok": False,
                    "error_type": "gnu_validation_failed",
                    "validation": {"stdout": "Port is not connected."},
                },
            }
        ]

        result = evaluate_turn_recovery(
            client=client,
            model="model",
            agent=agent,
            history_start=history_start,
            executed_tool_calls=executed_tool_calls,
            recovery_enabled=True,
            expected_recovery_class=NONRECOVERABLE_INVALID_END_STATE,
        )

        self.assertTrue(result["recovery_pass"], result)
        self.assertFalse(result["recovery_attempted"], result)
        self.assertEqual(
            result["recovery_decision"]["recovery_class"],
            NONRECOVERABLE_INVALID_END_STATE,
        )

    def test_remove_connection_invalid_end_state_is_classified_without_retry(self) -> None:
        client = _ScriptedClient([])
        agent = GrcAgent()
        history_start = len(agent.history)
        executed_tool_calls = [
            {
                "name": "remove_connection",
                "arguments": {
                    "ok": False,
                    "error_type": "gnu_validation_failed",
                    "validation": {"stdout": "Port is not connected."},
                },
            }
        ]

        result = evaluate_turn_recovery(
            client=client,
            model="model",
            agent=agent,
            history_start=history_start,
            executed_tool_calls=executed_tool_calls,
            recovery_enabled=True,
            expected_recovery_class=NONRECOVERABLE_INVALID_END_STATE,
        )

        self.assertTrue(result["recovery_pass"], result)
        self.assertFalse(result["recovery_attempted"], result)
        self.assertEqual(
            result["recovery_decision"]["recovery_class"],
            NONRECOVERABLE_INVALID_END_STATE,
        )







    def test_clarification_recovery_classification_does_not_retry_model(self) -> None:
        client = _ScriptedClient([])
        agent = GrcAgent()

        result = evaluate_turn_recovery(
            client=client,
            model="model",
            agent=agent,
            history_start=len(agent.history),
            executed_tool_calls=[
                {
                    "name": "auto_insert_block",
                    "arguments": {
                        "ok": False,
                        "clarification_required": True,
                        "options": [],
                    },
                }
            ],
            recovery_enabled=True,
        )

        self.assertTrue(result["recovery_pass"], result)
        self.assertFalse(result["recovery_attempted"], result)
        self.assertEqual(
            result["recovery_decision"]["recovery_class"],
            "recoverable_clarification",
        )

    def test_prior_failure_does_not_trigger_recovery_after_later_tool_success(self) -> None:
        client = _ScriptedClient([])
        agent = GrcAgent()
        history_start = len(agent.history)
        executed_tool_calls = [
            {
                "name": "apply_edit",
                "arguments": {
                    "ok": False,
                    "error_type": "preflight_rejected",
                    "errors": [{"code": "missing_field", "field": "src_block"}],
                },
            },
            {"name": "apply_edit", "arguments": {"ok": True}},
        ]

        result = evaluate_turn_recovery(
            client=client,
            model="model",
            agent=agent,
            history_start=history_start,
            executed_tool_calls=executed_tool_calls,
            recovery_enabled=True,
        )

        self.assertTrue(result["recovery_pass"], result)
        self.assertFalse(result["recovery_attempted"], result)
        self.assertEqual(result["recovery_decision"]["recovery_class"], "no_recovery_needed")


class BackendMetadataTests(unittest.TestCase):
    def test_collect_backend_metadata_records_tool_template_evidence(self) -> None:
        metadata = collect_backend_metadata(
            _PropsClient(
                {
                    "chat_template": "x" * 300,
                    "chat_template_tool_use": "template",
                    "tool_call_parser": "native",
                }
            ),
            server_url="http://server",
            model="model",
            temperature=0,
        )

        self.assertTrue(metadata["props_available"])
        self.assertEqual(metadata["backend_tool_call_risk"], "low")
        self.assertEqual(metadata["tool_call_parser"], "native")
        self.assertTrue(metadata["chat_template_present"])
        self.assertEqual(metadata["chat_template_chars"], 300)
        self.assertNotIn("chat_template", metadata)

    def test_collect_backend_metadata_tolerates_missing_props(self) -> None:
        metadata = collect_backend_metadata(
            _PropsClient(RuntimeError("404")),
            server_url="http://server",
            model="model",
            temperature=0,
        )

        self.assertFalse(metadata["props_available"])
        self.assertIn("props_error", metadata)


class GraphSnapshotTests(unittest.TestCase):
    def _loaded_session(self) -> FlowgraphSession:
        session = FlowgraphSession()
        session.load(fixture_path())
        return session

    def _duplicate_session(self) -> FlowgraphSession:
        base = self._loaded_session()
        assert base.flowgraph is not None
        raw_data = copy.deepcopy(base.flowgraph.raw_data)
        raw_data["blocks"].append(
            {
                "name": "dup",
                "id": "variable",
                "parameters": {"comment": "", "value": "1"},
                "states": {"coordinate": [64, 64], "rotation": 0, "state": "enabled"},
            }
        )
        raw_data["blocks"].append(
            {
                "name": "dup",
                "id": "variable",
                "parameters": {"comment": "", "value": "2"},
                "states": {"coordinate": [128, 64], "rotation": 0, "state": "disabled"},
            }
        )
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "duplicates.grc"
            path.write_text(
                yaml.safe_dump(raw_data, sort_keys=False, allow_unicode=True),
                encoding="utf-8",
            )
            session = FlowgraphSession()
            session.load(path)
        return session

    def test_graph_snapshot_includes_stable_values_and_connection_ids(self) -> None:
        session = self._loaded_session()

        snapshot = graph_snapshot(session)

        self.assertEqual(snapshot["path"], str(fixture_path()))
        self.assertEqual(snapshot["variable_values"]["samp_rate"], "32000")
        self.assertIn("samp_rate", snapshot["block_names"])
        self.assertIn("samp_rate", snapshot["blocks_by_name"])
        self.assertIn("blocks_by_uid", snapshot)
        self.assertIn("duplicate_block_groups", snapshot)
        self.assertIn(
            "analog_random_source_x_0:0->blocks_throttle2_0:0",
            snapshot["connection_ids"],
        )
        self.assertIsInstance(snapshot["raw_hash"], str)

    def test_uid_snapshot_distinguishes_same_name_same_type_duplicates(self) -> None:
        session = self._duplicate_session()
        snapshot = graph_snapshot(session)

        first_uid = graph_block_uid(
            snapshot,
            instance_name="dup",
            block_type="variable",
            index=0,
        )
        second_uid = graph_block_uid(
            snapshot,
            instance_name="dup",
            block_type="variable",
            index=1,
        )

        self.assertIsNotNone(first_uid)
        self.assertIsNotNone(second_uid)
        self.assertNotEqual(first_uid, second_uid)
        self.assertEqual(
            snapshot["duplicate_block_groups"]["dup|variable"],
            [first_uid, second_uid],
        )
        self.assertEqual(len([name for name in snapshot["block_names"] if name == "dup"]), 2)
        self.assertEqual(
            graph_block_param_value_by_uid(snapshot, first_uid, "value"),
            "1",
        )
        self.assertEqual(
            graph_block_param_value_by_uid(snapshot, second_uid, "value"),
            "2",
        )

    def test_uid_graph_delta_identifies_selected_duplicate_param_only(self) -> None:
        session = self._duplicate_session()
        before = graph_snapshot(session)
        selected_uid = graph_block_uid(before, instance_name="dup", block_type="variable", index=1)
        other_uid = graph_block_uid(before, instance_name="dup", block_type="variable", index=0)
        assert selected_uid is not None
        assert other_uid is not None

        session.set_param_by_uid(
            selected_uid,
            "value",
            "42",
            expected_instance_name="dup",
            expected_block_type="variable",
        )
        after = graph_snapshot(session)

        self.assertEqual(
            uid_graph_delta(before, after),
            {
                "block_params_by_uid": {selected_uid: {"value": "42"}},
                "dirty": True,
            },
        )
        self.assertEqual(graph_block_param_value_by_uid(after, selected_uid, "value"), "42")
        self.assertEqual(graph_block_param_value_by_uid(after, other_uid, "value"), "1")

    def test_uid_graph_delta_identifies_selected_duplicate_state_only(self) -> None:
        session = self._duplicate_session()
        before = graph_snapshot(session)
        selected_uid = graph_block_uid(before, instance_name="dup", block_type="variable", index=0)
        other_uid = graph_block_uid(before, instance_name="dup", block_type="variable", index=1)
        assert selected_uid is not None
        assert other_uid is not None

        session.set_block_state_by_uid(
            selected_uid,
            "disabled",
            expected_instance_name="dup",
            expected_block_type="variable",
        )
        after = graph_snapshot(session)

        self.assertEqual(
            uid_graph_delta(before, after),
            {
                "block_states_by_uid": {selected_uid: "disabled"},
                "dirty": True,
            },
        )
        self.assertEqual(graph_block_state_by_uid(after, selected_uid), "disabled")
        self.assertEqual(graph_block_state_by_uid(after, other_uid), "disabled")

    def test_uid_exact_graph_delta_check_proves_selected_duplicate_only(self) -> None:
        session = self._duplicate_session()
        before = graph_snapshot(session)
        selected_uid = graph_block_uid(before, instance_name="dup", block_type="variable", index=1)
        assert selected_uid is not None
        session.set_param_by_uid(
            selected_uid,
            "value",
            "42",
            expected_instance_name="dup",
            expected_block_type="variable",
        )
        after = graph_snapshot(session)

        result = evaluate_semantic_checks(
            checks=(
                {
                    "kind": "uid_exact_graph_delta",
                    "delta": {
                        "block_params_by_uid": {selected_uid: {"value": "42"}},
                        "dirty": True,
                    },
                },
                {
                    "kind": "uid_block_param_equals",
                    "block_uid": selected_uid,
                    "param": "value",
                    "value": "42",
                },
            ),
            before_snapshot=before,
            after_snapshot=after,
            run_result={"requested_tool_calls": [], "executed_tool_calls": []},
            save_path="",
        )

        self.assertTrue(result["semantic_pass"], result)
        self.assertTrue(result["end_state_pass"], result)

    def test_uid_exact_graph_delta_detects_wrong_duplicate_change(self) -> None:
        session = self._duplicate_session()
        before = graph_snapshot(session)
        expected_uid = graph_block_uid(before, instance_name="dup", block_type="variable", index=1)
        wrong_uid = graph_block_uid(before, instance_name="dup", block_type="variable", index=0)
        assert expected_uid is not None
        assert wrong_uid is not None
        session.set_param_by_uid(
            wrong_uid,
            "value",
            "42",
            expected_instance_name="dup",
            expected_block_type="variable",
        )
        after = graph_snapshot(session)

        result = evaluate_semantic_checks(
            checks=(
                {
                    "kind": "uid_exact_graph_delta",
                    "delta": {
                        "block_params_by_uid": {expected_uid: {"value": "42"}},
                        "dirty": True,
                    },
                },
            ),
            before_snapshot=before,
            after_snapshot=after,
            run_result={"requested_tool_calls": [], "executed_tool_calls": []},
            save_path="",
        )

        self.assertFalse(result["semantic_pass"], result)
        actual_delta = result["semantic_details"][0]["actual_delta"]
        self.assertEqual(actual_delta["block_params_by_uid"], {wrong_uid: {"value": "42"}})

    def test_uid_delta_proves_stale_preview_and_unsupported_failures_unchanged(self) -> None:
        session = self._duplicate_session()
        agent = GrcAgent(session)
        before = graph_snapshot(agent)
        selected_uid = graph_block_uid(before, instance_name="dup", block_type="variable", index=1)
        assert selected_uid is not None
        target_ref = {
            "block_uid": selected_uid,
            "expected_instance_name": "dup",
            "expected_block_type": "variable",
            "base_state_revision": session.state_revision,
        }

        preview = agent.execute_tool(
            "propose_edit",
            {
                "transaction": {
                    "op_type": "update_params",
                    "target_ref": target_ref,
                    "params": {"value": "42"},
                }
            },
        )
        after_preview = graph_snapshot(agent)
        self.assertTrue(preview["ok"], preview)
        self.assertEqual(uid_graph_delta(before, after_preview), {})

        session.set_param("samp_rate", "value", "48000")
        before_stale = graph_snapshot(agent)
        stale = agent.execute_tool(
            "apply_edit",
            {
                "transaction": {
                    "op_type": "update_params",
                    "target_ref": target_ref,
                    "params": {"value": "42"},
                }
            },
        )
        after_stale = graph_snapshot(agent)
        self.assertFalse(stale["ok"], stale)
        self.assertEqual(uid_graph_delta(before_stale, after_stale), {})

        fresh_ref = {
            **target_ref,
            "base_state_revision": session.state_revision,
        }
        before_unsupported = graph_snapshot(agent)
        unsupported = agent.execute_tool(
            "apply_edit",
            {
                "transaction": {
                    "op_type": "add_connection",
                    "target_ref": fresh_ref,
                    "src_block": "dup",
                    "src_port": 0,
                    "dst_block": "dup",
                    "dst_port": 0,
                }
            },
        )
        after_unsupported = graph_snapshot(agent)
        self.assertFalse(unsupported["ok"], unsupported)
        self.assertEqual(uid_graph_delta(before_unsupported, after_unsupported), {})

    def test_name_keyed_delta_is_ambiguous_but_uid_delta_is_precise(self) -> None:
        session = self._duplicate_session()
        before = graph_snapshot(session)
        selected_uid = graph_block_uid(before, instance_name="dup", block_type="variable", index=0)
        assert selected_uid is not None

        session.set_param_by_uid(
            selected_uid,
            "value",
            "99",
            expected_instance_name="dup",
            expected_block_type="variable",
        )
        after = graph_snapshot(session)

        name_delta = graph_delta(before, after)
        precise_delta = uid_graph_delta(before, after)
        self.assertNotIn("block_params_by_uid", name_delta)
        self.assertEqual(
            precise_delta,
            {
                "block_params_by_uid": {selected_uid: {"value": "99"}},
                "dirty": True,
            },
        )

    def test_graph_snapshot_detects_parameter_change(self) -> None:
        session = self._loaded_session()
        before = graph_snapshot(session)

        session.set_param("samp_rate", "value", "48000")
        after = graph_snapshot(session)

        self.assertTrue(snapshot_changed(before, after))
        self.assertEqual(graph_variable_value(after, "samp_rate"), "48000")
        self.assertNotEqual(before["raw_hash"], after["raw_hash"])

    def test_graph_snapshot_reports_block_parameter_value(self) -> None:
        session = self._loaded_session()
        snapshot = graph_snapshot(session)

        self.assertEqual(
            graph_block_param_value(snapshot, "blocks_throttle2_0", "samples_per_second"),
            "samp_rate",
        )

    def test_graph_snapshot_reports_block_state(self) -> None:
        session = self._loaded_session()
        snapshot = graph_snapshot(session)

        self.assertEqual(graph_block_state(snapshot, "blocks_throttle2_0"), "enabled")

    def test_propose_edit_leaves_snapshot_unchanged(self) -> None:
        session = self._loaded_session()
        agent = GrcAgent(session)
        before = graph_snapshot(agent)

        result = agent.execute_tool(
            "propose_edit",
            {
                "transaction": {
                    "op_type": "update_params",
                    "instance_name": "samp_rate",
                    "params": {"value": "64000"},
                }
            },
        )
        after = graph_snapshot(agent)

        self.assertTrue(result.get("ok"), result)
        self.assertFalse(snapshot_changed(before, after))
        self.assertEqual(before["state_revision"], after["state_revision"])

    def test_saved_graph_reloads_and_validates(self) -> None:
        session = self._loaded_session()
        agent = GrcAgent(session)

        with TemporaryDirectory() as tmpdir:
            save_path = Path(tmpdir) / "saved.grc"
            result = agent.execute_tool("save_graph", {"path": str(save_path)})

            self.assertTrue(result.get("ok"), result)
            validation = saved_graph_reloads_and_validates(save_path)

        self.assertTrue(validation["exists"], validation)
        self.assertTrue(validation["loaded"], validation)
        self.assertTrue(validation["valid"], validation)
        self.assertIn(
            "analog_random_source_x_0:0->blocks_throttle2_0:0",
            validation["snapshot"]["connection_ids"],
        )

    def test_saved_connection_checks_reload_saved_graph(self) -> None:
        session = self._loaded_session()
        agent = GrcAgent(session)

        with TemporaryDirectory() as tmpdir:
            save_path = Path(tmpdir) / "saved.grc"
            before = graph_snapshot(agent)
            validation_result = agent.execute_tool("validate_graph", {})
            self.assertTrue(validation_result.get("ok"), validation_result)
            result = agent.execute_tool("save_graph", {"path": str(save_path)})
            self.assertTrue(result.get("ok"), result)
            after = graph_snapshot(agent)

            present = evaluate_semantic_checks(
                checks=(
                    {
                        "kind": "saved_connection_present",
                        "path": str(save_path),
                        "connection_id": "analog_random_source_x_0:0->blocks_throttle2_0:0",
                    },
                    {
                        "kind": "saved_connection_absent",
                        "path": str(save_path),
                        "connection_id": "missing:0->missing:0",
                    },
                ),
                before_snapshot=before,
                after_snapshot=after,
                run_result={},
                save_path=str(save_path),
            )

        self.assertTrue(present["semantic_pass"], present)
        self.assertTrue(present["end_state_pass"], present)

    def test_saved_value_checks_reload_saved_graph(self) -> None:
        session = self._loaded_session()
        agent = GrcAgent(session)
        session.set_param("samp_rate", "value", "64000")

        with TemporaryDirectory() as tmpdir:
            save_path = Path(tmpdir) / "saved.grc"
            before = graph_snapshot(agent)
            validation_result = agent.execute_tool("validate_graph", {})
            self.assertTrue(validation_result.get("ok"), validation_result)
            result = agent.execute_tool("save_graph", {"path": str(save_path)})
            self.assertTrue(result.get("ok"), result)
            after = graph_snapshot(agent)

            saved = evaluate_semantic_checks(
                checks=(
                    {
                        "kind": "saved_path_valid",
                        "path": str(save_path),
                        "copy": True,
                    },
                    {
                        "kind": "saved_variable_equals",
                        "path": str(save_path),
                        "name": "samp_rate",
                        "value": 64000,
                    },
                    {
                        "kind": "saved_block_param_equals",
                        "path": str(save_path),
                        "instance_name": "samp_rate",
                        "param": "value",
                        "value": 64000,
                    },
                    {
                        "kind": "saved_block_state_equals",
                        "path": str(save_path),
                        "instance_name": "blocks_throttle2_0",
                        "state": "enabled",
                    },
                    {
                        "kind": "saved_block_present",
                        "path": str(save_path),
                        "instance_name": "blocks_throttle2_0",
                    },
                    {
                        "kind": "saved_block_absent",
                        "path": str(save_path),
                        "instance_name": "missing_block",
                    },
                ),
                before_snapshot=before,
                after_snapshot=after,
                run_result={},
                save_path=str(save_path),
            )

        self.assertTrue(saved["semantic_pass"], saved)
        self.assertTrue(saved["end_state_pass"], saved)

    def test_evaluate_semantic_checks_reports_variable_and_mutation_state(self) -> None:
        session = self._loaded_session()
        before = graph_snapshot(session)
        session.set_param("samp_rate", "value", "48000")
        after = graph_snapshot(session)

        result = evaluate_semantic_checks(
            checks=(
                {"kind": "mutation"},
                {"kind": "variable_equals", "name": "samp_rate", "value": "48000"},
                {"kind": "dirty", "value": True},
            ),
            before_snapshot=before,
            after_snapshot=after,
            run_result={"requested_tool_calls": [], "executed_tool_calls": []},
            save_path="",
        )

        self.assertTrue(result["semantic_pass"], result)
        self.assertTrue(result["end_state_pass"], result)

    def test_evaluate_semantic_checks_variable_value_compares_numeric_semantically(self) -> None:
        session = self._loaded_session()
        before = graph_snapshot(session)
        session.set_param("samp_rate", "value", 48000)
        after = graph_snapshot(session)

        result = evaluate_semantic_checks(
            checks=(
                {"kind": "variable_equals", "name": "samp_rate", "value": "48000"},
            ),
            before_snapshot=before,
            after_snapshot=after,
            run_result={"requested_tool_calls": [], "executed_tool_calls": []},
            save_path="",
        )

        self.assertTrue(result["semantic_pass"], result)
        self.assertTrue(result["end_state_pass"], result)

    def test_evaluate_semantic_checks_reports_block_parameter_value(self) -> None:
        session = self._loaded_session()
        before = graph_snapshot(session)
        session.set_param("blocks_throttle2_0", "samples_per_second", "48000")
        after = graph_snapshot(session)

        result = evaluate_semantic_checks(
            checks=(
                {
                    "kind": "block_param_equals",
                    "instance_name": "blocks_throttle2_0",
                    "param": "samples_per_second",
                    "value": "48000",
                },
            ),
            before_snapshot=before,
            after_snapshot=after,
            run_result={"requested_tool_calls": [], "executed_tool_calls": []},
            save_path="",
        )

        self.assertTrue(result["semantic_pass"], result)
        self.assertTrue(result["end_state_pass"], result)

    def test_evaluate_semantic_checks_reports_block_state(self) -> None:
        session = self._loaded_session()
        before = graph_snapshot(session)
        session.set_block_state("blocks_throttle2_0", "disabled")
        after = graph_snapshot(session)

        result = evaluate_semantic_checks(
            checks=(
                {
                    "kind": "block_state_equals",
                    "instance_name": "blocks_throttle2_0",
                    "state": "disabled",
                },
            ),
            before_snapshot=before,
            after_snapshot=after,
            run_result={"requested_tool_calls": [], "executed_tool_calls": []},
            save_path="",
        )

        self.assertTrue(result["semantic_pass"], result)
        self.assertTrue(result["end_state_pass"], result)

    def test_evaluate_semantic_checks_coerces_numeric_block_parameter_value(self) -> None:
        session = self._loaded_session()
        before = graph_snapshot(session)
        session.set_param("blocks_throttle2_0", "samples_per_second", 48000)
        after = graph_snapshot(session)

        result = evaluate_semantic_checks(
            checks=(
                {
                    "kind": "block_param_equals",
                    "instance_name": "blocks_throttle2_0",
                    "param": "samples_per_second",
                    "value": "48000",
                },
            ),
            before_snapshot=before,
            after_snapshot=after,
            run_result={"requested_tool_calls": [], "executed_tool_calls": []},
            save_path="",
        )

        self.assertTrue(result["semantic_pass"], result)

    def test_evaluate_semantic_checks_detects_mutating_preview(self) -> None:
        session = self._loaded_session()
        before = graph_snapshot(session)
        session.set_param("samp_rate", "value", "48000")
        after = graph_snapshot(session)

        result = evaluate_semantic_checks(
            checks=({"kind": "no_mutation"},),
            before_snapshot=before,
            after_snapshot=after,
            run_result={"requested_tool_calls": [], "executed_tool_calls": []},
            save_path="",
        )

        self.assertFalse(result["semantic_pass"], result)
        self.assertFalse(result["safety_pass"], result)

    def test_evaluate_semantic_checks_reports_connection_presence(self) -> None:
        session = self._loaded_session()
        snapshot = graph_snapshot(session)
        connection_id = "analog_random_source_x_0:0->blocks_throttle2_0:0"

        present = evaluate_semantic_checks(
            checks=({"kind": "connection_present", "connection_id": connection_id},),
            before_snapshot=snapshot,
            after_snapshot=snapshot,
            run_result={"requested_tool_calls": [], "executed_tool_calls": []},
            save_path="",
        )
        absent = evaluate_semantic_checks(
            checks=({"kind": "connection_absent", "connection_id": connection_id},),
            before_snapshot=snapshot,
            after_snapshot=snapshot,
            run_result={"requested_tool_calls": [], "executed_tool_calls": []},
            save_path="",
        )

        self.assertTrue(present["semantic_pass"], present)
        self.assertFalse(absent["semantic_pass"], absent)

    def test_evaluate_semantic_checks_reports_exact_graph_delta(self) -> None:
        session = self._loaded_session()
        before = graph_snapshot(session)
        session.set_param("samp_rate", "value", "48000")
        after = graph_snapshot(session)

        result = evaluate_semantic_checks(
            checks=(
                {
                    "kind": "exact_graph_delta",
                    "delta": {
                        "variables": {"samp_rate": "48000"},
                        "block_params": {"samp_rate": {"value": "48000"}},
                        "dirty": True,
                    },
                },
            ),
            before_snapshot=before,
            after_snapshot=after,
            run_result={"requested_tool_calls": [], "executed_tool_calls": []},
            save_path="",
        )

        self.assertTrue(result["semantic_pass"], result)
        self.assertTrue(result["end_state_pass"], result)

    def test_evaluate_semantic_checks_exact_graph_delta_detects_unlisted_change(self) -> None:
        session = self._loaded_session()
        before = graph_snapshot(session)
        session.set_param("samp_rate", "value", "48000")
        session.set_block_state("blocks_throttle2_0", "disabled")
        after = graph_snapshot(session)

        result = evaluate_semantic_checks(
            checks=(
                {
                    "kind": "exact_graph_delta",
                    "delta": {
                        "variables": {"samp_rate": "48000"},
                        "block_params": {"samp_rate": {"value": "48000"}},
                        "dirty": True,
                    },
                },
            ),
            before_snapshot=before,
            after_snapshot=after,
            run_result={"requested_tool_calls": [], "executed_tool_calls": []},
            save_path="",
        )

        self.assertFalse(result["semantic_pass"], result)
        self.assertFalse(result["end_state_pass"], result)
        self.assertFalse(result["safety_pass"], result)

    def test_evaluate_semantic_checks_exact_graph_delta_safe_refusal_not_unsafe(self) -> None:
        session = self._loaded_session()
        snapshot = graph_snapshot(session)

        result = evaluate_semantic_checks(
            checks=(
                {
                    "kind": "exact_graph_delta",
                    "delta": {
                        "variables": {"samp_rate": "48000"},
                        "block_params": {"samp_rate": {"value": "48000"}},
                        "dirty": True,
                    },
                },
            ),
            before_snapshot=snapshot,
            after_snapshot=snapshot,
            run_result={"requested_tool_calls": [], "executed_tool_calls": []},
            save_path="",
        )

        self.assertFalse(result["semantic_pass"], result)
        self.assertFalse(result["end_state_pass"], result)
        self.assertTrue(result["safety_pass"], result)

    def test_evaluate_semantic_checks_exact_graph_delta_accepts_no_change(self) -> None:
        session = self._loaded_session()
        snapshot = graph_snapshot(session)

        result = evaluate_semantic_checks(
            checks=({"kind": "exact_graph_delta", "delta": {}},),
            before_snapshot=snapshot,
            after_snapshot=snapshot,
            run_result={"requested_tool_calls": [], "executed_tool_calls": []},
            save_path="",
        )

        self.assertTrue(result["semantic_pass"], result)

    def test_evaluate_semantic_checks_tool_result_matches_any_executed_payload(self) -> None:
        session = self._loaded_session()
        snapshot = graph_snapshot(session)

        run_result = {
            "requested_tool_calls": [],
            "executed_tool_calls": [
                {
                    "name": "change_graph",
                    "arguments": {
                        "ok": False,
                        "error_type": "clarification_required",
                    },
                },
                {
                    "name": "change_graph",
                    "arguments": {
                        "ok": False,
                        "error_type": "gnu_validation_failed",
                        "validation_result": {"status": "invalid"},
                    },
                },
            ],
        }
        result = evaluate_semantic_checks(
            checks=(
                {
                    "kind": "tool_result",
                    "tool": "change_graph",
                    "arguments": {
                        "ok": False,
                        "error_type": "gnu_validation_failed",
                        "validation_result": {"status": "invalid"},
                    },
                },
            ),
            before_snapshot=snapshot,
            after_snapshot=snapshot,
            run_result=run_result,
            save_path="",
        )

        self.assertTrue(result["semantic_pass"], result)
        self.assertTrue(result["end_state_pass"], result)

    def test_evaluate_semantic_checks_tool_result_fails_when_no_payload_matches(self) -> None:
        session = self._loaded_session()
        snapshot = graph_snapshot(session)

        run_result = {
            "requested_tool_calls": [],
            "executed_tool_calls": [
                {
                    "name": "change_graph",
                    "arguments": {
                        "ok": False,
                        "error_type": "clarification_required",
                    },
                },
                {
                    "name": "change_graph",
                    "arguments": {
                        "ok": False,
                        "error_type": "preflight_rejected",
                    },
                },
            ],
        }
        result = evaluate_semantic_checks(
            checks=(
                {
                    "kind": "tool_result",
                    "tool": "change_graph",
                    "arguments": {
                        "ok": False,
                        "error_type": "gnu_validation_failed",
                    },
                },
            ),
            before_snapshot=snapshot,
            after_snapshot=snapshot,
            run_result=run_result,
            save_path="",
        )

        self.assertFalse(result["semantic_pass"], result)
        self.assertFalse(result["end_state_pass"], result)

    def test_evaluate_semantic_checks_tool_result_match_does_not_hide_safety_violation(self) -> None:
        session = self._loaded_session()
        before = graph_snapshot(session)
        session.set_param("samp_rate", "value", "48000")
        after = graph_snapshot(session)

        run_result = {
            "requested_tool_calls": [],
            "executed_tool_calls": [
                {
                    "name": "change_graph",
                    "arguments": {
                        "ok": True,
                        "operation_kind": "set_param",
                    },
                },
                {
                    "name": "change_graph",
                    "arguments": {
                        "ok": False,
                        "error_type": "gnu_validation_failed",
                        "validation_result": {"status": "invalid"},
                    },
                },
            ],
        }
        result = evaluate_semantic_checks(
            checks=(
                {"kind": "no_mutation"},
                {
                    "kind": "tool_result",
                    "tool": "change_graph",
                    "arguments": {
                        "ok": False,
                        "error_type": "gnu_validation_failed",
                        "validation_result": {"status": "invalid"},
                    },
                },
            ),
            before_snapshot=before,
            after_snapshot=after,
            run_result=run_result,
            save_path="",
        )

        self.assertFalse(result["semantic_pass"], result)
        self.assertFalse(result["safety_pass"], result)
        self.assertFalse(result["end_state_pass"], result)
        details = result.get("semantic_details", [])
        no_mutation = next(d for d in details if d.get("kind") == "no_mutation")
        tool_result = next(d for d in details if d.get("kind") == "tool_result")
        self.assertFalse(no_mutation.get("passed"))
        self.assertTrue(tool_result.get("passed"))

    def test_evaluate_semantic_checks_exact_graph_delta_treats_numeric_scalars_semantically(self) -> None:
        before = {
            "raw_hash": "before",
            "block_names": ["samp_rate"],
            "connection_ids": [],
            "variable_values": {"samp_rate": "32000"},
            "blocks_by_name": {
                "samp_rate": {
                    "parameters": {"value": "32000"},
                    "state": "enabled",
                }
            },
            "dirty": False,
            "validation_status": "unknown",
            "validation_returncode": None,
        }
        after = {
            **before,
            "raw_hash": "after",
            "variable_values": {"samp_rate": 96000},
            "blocks_by_name": {
                "samp_rate": {
                    "parameters": {"value": 96000},
                    "state": "enabled",
                }
            },
            "dirty": True,
            "validation_status": "valid",
            "validation_returncode": 0,
        }

        result = evaluate_semantic_checks(
            checks=(
                {
                    "kind": "exact_graph_delta",
                    "delta": {
                        "variables": {"samp_rate": "96000"},
                        "block_params": {"samp_rate": {"value": "96000"}},
                        "dirty": True,
                        "validation_status": "valid",
                        "validation_returncode": 0,
                    },
                },
            ),
            before_snapshot=before,
            after_snapshot=after,
            run_result={"requested_tool_calls": [], "executed_tool_calls": []},
            save_path="",
        )

        self.assertTrue(result["semantic_pass"], result)


class InfraFailureTests(unittest.TestCase):
    def test_is_infra_error_message_detects_backend_connect_timeout(self) -> None:
        self.assertTrue(
            is_infra_error_message(
                "Timed out connecting to llama.cpp server at http://127.0.0.1:8080/v1/chat/completions."
            )
        )

    def test_run_result_is_infra_failure_requires_no_tool_activity(self) -> None:
        self.assertTrue(
            run_result_is_infra_failure(
                {
                    "tools_called": [],
                    "requested_tool_calls": [],
                    "executed_tool_calls": [],
                    "error": "Timed out connecting to llama.cpp server at http://127.0.0.1:8080/v1/chat/completions.",
                }
            )
        )
        self.assertFalse(
            run_result_is_infra_failure(
                {
                    "tools_called": ["save_graph"],
                    "requested_tool_calls": [{"name": "save_graph", "arguments": {}}],
                    "executed_tool_calls": [],
                }
            )
        )
        self.assertTrue(
            run_result_is_infra_failure(
                {
                    "tools_called": ["describe_block"],
                    "requested_tool_calls": [{"name": "describe_block", "arguments": {}}],
                    "executed_tool_calls": [],
                    "error": "Timed out connecting to llama.cpp server at http://127.0.0.1:8080/v1/chat/completions.",
                }
            )
        )
        self.assertTrue(
            run_result_is_infra_failure(
                {
                    "tools_called": ["describe_block"],
                    "error_type": "connect_timeout",
                }
            )
        )


class RunPhaseEvalTests(unittest.TestCase):
    def _build_case_report(
        self,
        case: SimpleNamespace,
        runs: list[dict[str, object]],
        _n_runs: int,
        _majority_threshold: float,
    ) -> dict[str, object]:
        return {
            "category": case.category,
            "name": case.name,
            "runs": runs,
            "passed": False,
        }

    def test_run_phase_eval_retries_infra_failure_once(self) -> None:
        case = SimpleNamespace(category="cat", name="case1", prompt="hello")
        client = SimpleNamespace(temperature=0.7)
        run_case = mock.Mock(
            side_effect=[
                {
                    "tools_called": [],
                    "requested_tool_calls": [],
                    "executed_tool_calls": [],
                    "error": "Timed out connecting to llama.cpp server at http://127.0.0.1:8080/v1/chat/completions.",
                },
                {
                    "tools_called": [],
                    "requested_tool_calls": [],
                    "executed_tool_calls": [],
                    "error": "Timed out connecting to llama.cpp server at http://127.0.0.1:8080/v1/chat/completions.",
                },
            ]
        )

        with (
            TemporaryDirectory() as tmpdir,
            mock.patch(
                "tests.llama_eval.harness.ensure_llama_server",
                return_value=("http://server", "model", client),
            ),
            mock.patch(
                "tests.llama_eval.harness.restart_llama_server",
                return_value=("http://server", "model", client),
            ) as restart_mock,
        ):
            report = run_phase_eval(
                phase=99,
                server_url="http://server",
                model="model",
                cases=[case],
                n_runs=1,
                majority_threshold=0.5,
                run_case=run_case,
                build_case_report=self._build_case_report,
                render_status=lambda _case, run: str(run.get("status")),
                results_path=Path(tmpdir) / "results.json",
            )

        restart_mock.assert_called_once()
        self.assertEqual(run_case.call_count, 2)
        run_result = report["cases"][0]["runs"][0]
        self.assertEqual(run_result["status"], RUN_STATUS_INFRA_FAIL)
        self.assertEqual(run_result["backend_restart_count"], 1)
        self.assertEqual(report["summary"]["infra_failures"], 1)
        self.assertEqual(report["summary"]["model_attempts"], 0)

    def test_run_phase_eval_prints_neutral_infra_banner(self) -> None:
        case = SimpleNamespace(category="cat", name="case1", prompt="hello")
        client = SimpleNamespace(temperature=0.7)
        run_case = mock.Mock(
            return_value={
                "tools_called": [],
                "requested_tool_calls": [],
                "executed_tool_calls": [],
                "error": "Timed out connecting to llama.cpp server at http://127.0.0.1:8080/v1/chat/completions.",
            }
        )

        buffer = StringIO()
        with (
            mock.patch(
                "tests.llama_eval.harness.ensure_llama_server",
                return_value=("http://server", "model", client),
            ),
            mock.patch(
                "tests.llama_eval.harness.restart_llama_server",
                return_value=("http://server", "model", client),
            ),
            redirect_stdout(buffer),
        ):
            run_phase_eval(
                phase=99,
                server_url="http://server",
                model="model",
                cases=[case],
                n_runs=1,
                majority_threshold=0.5,
                run_case=run_case,
                build_case_report=self._build_case_report,
                render_status=lambda _case, run: str(run.get("status")),
            )

        output = buffer.getvalue()
        self.assertIn(RUN_STATUS_INFRA_BANNER, output)
        self.assertNotIn(" -> INFRA_FAIL", output)


class InfraBannerFormattingTests(unittest.TestCase):
    def _build_case_report(
        self,
        case: SimpleNamespace,
        runs: list[dict[str, object]],
        _n_runs: int,
        _majority_threshold: float,
    ) -> dict[str, object]:
        return {
            "category": case.category,
            "name": case.name,
            "runs": runs,
            "passed": False,
        }

    def test_format_run_status_for_cli_maps_infra_fail_to_neutral_label(self) -> None:
        run_result = {
            "status": RUN_STATUS_INFRA_FAIL,
            "error": "connection refused to llama.cpp",
        }
        rendered = format_run_status_for_cli(run_result)
        self.assertIn(RUN_STATUS_INFRA_BANNER, rendered)
        self.assertNotIn(RUN_STATUS_INFRA_FAIL, rendered)

    def test_run_phase_eval_applies_live_generation_token_bound(self) -> None:
        case = SimpleNamespace(category="cat", name="case1", prompt="hello")
        client = SimpleNamespace(temperature=0.7, max_tokens=100000)

        def run_case(active_client, _model, _case):
            return {
                "tools_called": ["summarize_graph"],
                "requested_tool_calls": [{"name": "summarize_graph", "arguments": {}}],
                "executed_tool_calls": [
                    {"name": "summarize_graph", "arguments": {"ok": True}}
                ],
                "matched": active_client.max_tokens == 2048,
            }

        with mock.patch(
            "tests.llama_eval.harness.ensure_llama_server",
            return_value=("http://server", "model", client),
        ):
            report = run_phase_eval(
                phase=99,
                server_url="http://server",
                model="model",
                cases=[case],
                n_runs=1,
                majority_threshold=0.5,
                run_case=run_case,
                build_case_report=self._build_case_report,
                render_status=lambda _case, run: str(run.get("status")),
            )

        self.assertEqual(client.max_tokens, 2048)
        self.assertTrue(report["cases"][0]["runs"][0]["matched"])

    def test_run_phase_eval_reports_repeat_run_stability(self) -> None:
        case = SimpleNamespace(category="cat", name="case1", prompt="hello")
        client = SimpleNamespace(temperature=0.7)
        run_case = mock.Mock(
            side_effect=[
                {
                    "tools_called": ["summarize_graph"],
                    "requested_tool_calls": [{"name": "summarize_graph", "arguments": {}}],
                    "executed_tool_calls": [
                        {"name": "summarize_graph", "arguments": {"ok": True}}
                    ],
                    "matched": True,
                },
                {
                    "tools_called": [],
                    "requested_tool_calls": [],
                    "executed_tool_calls": [],
                    "matched": False,
                },
            ]
        )

        with mock.patch(
            "tests.llama_eval.harness.ensure_llama_server",
            return_value=("http://server", "model", client),
        ):
            report = run_phase_eval(
                phase=99,
                server_url="http://server",
                model="model",
                cases=[case],
                n_runs=2,
                majority_threshold=0.5,
                run_case=run_case,
                build_case_report=self._build_case_report,
                render_status=lambda _case, run: str(run.get("status")),
                stability_threshold=1.0,
            )

        self.assertFalse(report["cases"][0]["stability"]["stable"])
        self.assertEqual(report["cases"][0]["stability"]["model_pass_rate"], 0.5)
        self.assertEqual(report["summary"]["stability"]["unstable_cases"], ["cat/case1"])
        self.assertFalse(report["summary"]["stability"]["release_stable"])

    def test_run_phase_eval_resume_reuses_persisted_run(self) -> None:
        case = SimpleNamespace(category="cat", name="case1", prompt="hello")
        client = SimpleNamespace(temperature=0.7)
        initial_run_case = mock.Mock(
            return_value={
                "tools_called": ["save_graph"],
                "requested_tool_calls": [{"name": "save_graph", "arguments": {}}],
                "executed_tool_calls": [{"name": "save_graph", "arguments": {"ok": True}}],
                "error": None,
                "matched": True,
            }
        )

        with TemporaryDirectory() as tmpdir:
            results_path = Path(tmpdir) / "results.json"
            with mock.patch(
                "tests.llama_eval.harness.ensure_llama_server",
                return_value=("http://server", "model", client),
            ):
                run_phase_eval(
                    phase=99,
                    server_url="http://server",
                    model="model",
                    cases=[case],
                    n_runs=1,
                    majority_threshold=0.5,
                    run_case=initial_run_case,
                    build_case_report=self._build_case_report,
                    render_status=lambda _case, run: str(run.get("status")),
                    results_path=results_path,
                )

            resumed_run_case = mock.Mock()
            with mock.patch(
                "tests.llama_eval.harness.ensure_llama_server"
            ) as ensure_mock:
                report = run_phase_eval(
                    phase=99,
                    server_url="http://server",
                    model="model",
                    cases=[case],
                    n_runs=1,
                    majority_threshold=0.5,
                    run_case=resumed_run_case,
                    build_case_report=self._build_case_report,
                    render_status=lambda _case, run: str(run.get("status")),
                    results_path=results_path,
                    resume=True,
                )

        resumed_run_case.assert_not_called()
        ensure_mock.assert_not_called()
        self.assertEqual(report["cases"][0]["runs"][0]["status"], "PASS")


class SummaryTests(unittest.TestCase):
    def test_case_run_stability_reports_repeat_run_brittleness(self) -> None:
        stability = case_run_stability(
            [
                {"status": "PASS"},
                {"status": "FAIL"},
                {"status": "INFRA_FAIL"},
            ],
            threshold=1.0,
        )

        self.assertEqual(stability["total_scheduled_runs"], 3)
        self.assertEqual(stability["model_attempts"], 2)
        self.assertEqual(stability["model_passes"], 1)
        self.assertEqual(stability["infra_failures"], 1)
        self.assertEqual(stability["model_pass_rate"], 0.5)
        self.assertFalse(stability["stable"])

    def test_stability_summary_lists_unstable_cases(self) -> None:
        summary = stability_summary(
            [
                {
                    "category": "stable",
                    "name": "all_green",
                    "runs": [{"status": "PASS"}, {"status": "PASS"}],
                },
                {
                    "category": "flaky",
                    "name": "one_fail",
                    "runs": [{"status": "PASS"}, {"status": "FAIL"}],
                },
            ],
            threshold=1.0,
        )

        self.assertEqual(summary["stable_cases"], 1)
        self.assertEqual(summary["unstable_cases"], ["flaky/one_fail"])
        self.assertFalse(summary["release_stable"])

    def test_default_phase_summary_reports_model_attempts_and_infra_failures(self) -> None:
        summary = default_phase_summary(
            [
                {
                    "category": "cat",
                    "passed": True,
                    "runs": [
                        {"status": "PASS"},
                        {"status": "FAIL"},
                        {"status": "INFRA_FAIL"},
                    ],
                }
            ],
            1,
        )
        self.assertEqual(summary["model_passes"], 1)
        self.assertEqual(summary["model_attempts"], 2)
        self.assertEqual(summary["infra_failures"], 1)
        self.assertEqual(summary["total_scheduled_runs"], 3)
        self.assertFalse(summary["complete"])
        self.assertEqual(summary["stability"]["unstable_cases"], ["cat/<unknown>"])

    def test_default_phase_summary_reports_dimension_counts(self) -> None:
        results = [
            {
                "category": "cat",
                "passed": True,
                "runs": [
                    {
                        "routing_pass": True,
                        "argument_pass": True,
                        "tool_success_pass": True,
                        "semantic_pass": False,
                        "safety_pass": True,
                        "end_state_pass": False,
                    }
                ],
            }
        ]

        self.assertEqual(
            dimension_pass_counts(results),
            {
                "routing_pass": {"passed": 1, "total": 1},
                "argument_pass": {"passed": 1, "total": 1},
                "tool_success_pass": {"passed": 1, "total": 1},
                "semantic_pass": {"passed": 0, "total": 1},
                "safety_pass": {"passed": 1, "total": 1},
                "runtime_safety_pass": {"passed": 0, "total": 0},
                "model_contract_pass": {"passed": 0, "total": 0},
                "end_state_pass": {"passed": 0, "total": 1},
                "recovery_pass": {"passed": 0, "total": 0},
            },
        )
        summary = default_phase_summary(results, 1)
        self.assertEqual(summary["dimension_pass_counts"]["semantic_pass"]["passed"], 0)


