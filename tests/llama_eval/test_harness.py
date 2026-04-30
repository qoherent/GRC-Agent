"""Unit tests for shared llama eval harness helpers."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from types import SimpleNamespace
from unittest import mock

from grc_agent.agent import GrcAgent
from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.recovery import (
    NONRECOVERABLE_INVALID_END_STATE,
    RECOVERABLE_MISSING_ARGUMENTS,
    RECOVERABLE_SAVE_REFUSED,
)

from tests.llama_eval.harness import (
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
    graph_block_param_value,
    graph_block_state,
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
    ) -> tuple[str | None, list[object]]:
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
    def test_run_live_scenario_once_reports_argument_and_semantic_dimensions(self) -> None:
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
                {"assistant_text": "Updated samp_rate.", "tool_calls": []},
            ]
        )
        scenario = LiveScenario(
            category="edit",
            name="set_samp_rate",
            turns=(
                LiveTurnSpec(
                    prompt="Change samp_rate to 48000.",
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
                    ),
                    semantic_checks=(
                        {"kind": "variable_equals", "name": "samp_rate", "value": "48000"},
                    ),
                ),
            ),
        )

        result = run_live_scenario_once(client=client, model="model", scenario=scenario)

        self.assertTrue(result["routing_pass"], result)
        self.assertTrue(result["argument_pass"], result)
        self.assertTrue(result["tool_success_pass"], result)
        self.assertTrue(result["semantic_pass"], result)
        self.assertTrue(result["end_state_pass"], result)
        self.assertEqual(
            result["turn_results"][0]["turn_plan"]["intent"],
            "param_edit",
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

    def test_run_live_scenario_once_reports_recovery_dimension(self) -> None:
        client = _ScriptedClient(
            [
                {
                    "assistant_text": None,
                    "tool_calls": [
                        _ScriptedToolCall(
                            "remove_connection",
                            {},
                        )
                    ],
                },
                {
                    "assistant_text": "The disconnect was missing exact endpoints.",
                    "tool_calls": [],
                },
                {
                    "assistant_text": "I cannot infer the exact endpoint safely.",
                    "tool_calls": [],
                },
            ]
        )
        scenario = LiveScenario(
            category="recovery",
            name="missing_args",
            turns=(
                LiveTurnSpec(
                    prompt=(
                        "Disconnect analog_random_source_x_0 output 0 from "
                        "blocks_throttle2_0 input 0."
                    ),
                    expected_tool_calls=(
                        ToolExpectation("remove_connection", require_result_ok=False),
                    ),
                    recovery_enabled=True,
                    expected_recovery_class=RECOVERABLE_MISSING_ARGUMENTS,
                ),
            ),
        )

        result = run_live_scenario_once(client=client, model="model", scenario=scenario)

        self.assertTrue(result["routing_pass"], result)
        self.assertTrue(result["tool_success_pass"], result)
        self.assertTrue(result["recovery_pass"], result)
        self.assertTrue(result["turn_results"][0]["recovery_attempted"], result)

    def test_run_live_scenario_once_applies_pre_turn_setup_before_snapshot(self) -> None:
        client = _ScriptedClient(
            [
                {
                    "assistant_text": None,
                    "tool_calls": [_ScriptedToolCall("validate_graph", {})],
                },
                {"assistant_text": "Validated.", "tool_calls": []},
            ]
        )
        scenario = LiveScenario(
            category="setup",
            name="pre_turn_verified_tool",
            turns=(
                LiveTurnSpec(
                    prompt="Validate the current graph.",
                    pre_turn_tool_name="apply_edit",
                    pre_turn_tool_args={
                        "transaction": {
                            "op_type": "update_params",
                            "instance_name": "samp_rate",
                            "params": {"value": "48000"},
                        }
                    },
                    expected_tool_calls=(ToolExpectation("validate_graph"),),
                    semantic_checks=(
                        {"kind": "no_mutation"},
                        {"kind": "variable_equals", "name": "samp_rate", "value": "48000"},
                    ),
                ),
            ),
        )

        result = run_live_scenario_once(client=client, model="model", scenario=scenario)

        self.assertTrue(result["matched"], result)
        self.assertEqual(result["tools_called"], ["validate_graph"])
        self.assertEqual(
            result["turn_results"][0]["before_snapshot"]["variable_values"]["samp_rate"],
            "48000",
        )
        self.assertEqual(
            [entry["tool"] for entry in result["turn_results"][0]["pre_turn_setup"]],
            ["apply_edit", "validate_graph"],
        )
        self.assertTrue(result["turn_results"][0]["pre_turn_setup"][1]["valid"])

    def test_run_live_scenario_once_allows_pre_turn_clarification_setup(self) -> None:
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
                    expected_tool_calls=(ToolExpectation("rewire_connection"),),
                    semantic_checks=(
                        {"kind": "clarification_mode", "mode": "executed"},
                        {
                            "kind": "connection_present",
                            "connection_id": (
                                "blocks_throttle2_1:0->blocks_char_to_float_0:0"
                            ),
                        },
                    ),
                ),
            ),
        )

        result = run_live_scenario_once(
            client=_ScriptedClient([]),
            model="model",
            scenario=scenario,
        )

        self.assertTrue(result["matched"], result)
        self.assertEqual(result["tools_called"], ["rewire_connection"])
        self.assertEqual(
            [entry["tool"] for entry in result["turn_results"][0]["pre_turn_setup"]],
            ["rewire_connection", "validate_graph"],
        )
        self.assertTrue(
            result["turn_results"][0]["pre_turn_setup"][0]["clarification_required"]
        )
        self.assertTrue(result["turn_results"][0]["pre_turn_setup"][1]["valid"])

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

    def test_recoverable_missing_arguments_runs_one_bounded_follow_up(self) -> None:
        client = _ScriptedClient(
            [
                {
                    "assistant_text": "I checked the graph and cannot safely infer the endpoint.",
                    "tool_calls": [],
                }
            ]
        )
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
            }
        ]

        result = evaluate_turn_recovery(
            client=client,
            model="model",
            agent=agent,
            history_start=history_start,
            executed_tool_calls=executed_tool_calls,
            recovery_enabled=True,
            expected_recovery_class=RECOVERABLE_MISSING_ARGUMENTS,
        )

        self.assertTrue(result["recovery_attempted"], result)
        self.assertTrue(result["recovery_pass"], result)
        self.assertEqual(result["recovery_requested_tool_calls"], [])
        self.assertEqual(result["recovery_mutation_retry_count"], 0)

    def test_remove_connection_missing_arguments_uses_wrapper_for_bounded_follow_up(self) -> None:
        client = _ScriptedClient(
            [
                {
                    "assistant_text": "I checked the graph and cannot safely infer the endpoint.",
                    "tool_calls": [],
                }
            ]
        )
        agent = GrcAgent()
        history_start = len(agent.history)
        executed_tool_calls = [
            {
                "name": "remove_connection",
                "arguments": {
                    "ok": False,
                    "error_type": "tool_call_invalid",
                    "validation_errors": [
                        {"code": "missing_required", "field": "connection_id"}
                    ],
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
            expected_recovery_class=RECOVERABLE_MISSING_ARGUMENTS,
        )

        self.assertTrue(result["recovery_attempted"], result)
        self.assertTrue(result["recovery_pass"], result)
        self.assertIn("remove_connection", result["recovery_decision"]["allowed_tools"])
        self.assertNotIn("apply_edit", result["recovery_decision"]["allowed_tools"])

    def test_recovery_pass_fails_when_model_exceeds_mutation_retry_budget(self) -> None:
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
                        ),
                        _ScriptedToolCall(
                            "apply_edit",
                            {
                                "transaction": {
                                    "op_type": "update_params",
                                    "instance_name": "samp_rate",
                                    "params": {"value": "96000"},
                                }
                            },
                        ),
                    ],
                },
                {"assistant_text": "Retried.", "tool_calls": []},
            ]
        )
        agent = GrcAgent()
        agent.execute_tool("load_grc", {"file_path": str(fixture_path())})
        history_start = len(agent.history)
        executed_tool_calls = [
            {
                "name": "apply_edit",
                "arguments": {
                    "ok": False,
                    "error_type": "preflight_rejected",
                    "errors": [{"code": "missing_field", "field": "src_block"}],
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
            expected_recovery_class=RECOVERABLE_MISSING_ARGUMENTS,
        )

        self.assertFalse(result["recovery_pass"], result)
        self.assertFalse(result["recovery_retry_budget_pass"], result)
        self.assertEqual(result["recovery_mutation_retry_count"], 2)

    def test_recovery_records_post_recovery_snapshot_when_retry_mutates(self) -> None:
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
                        ),
                    ],
                },
                {"assistant_text": "Recovered.", "tool_calls": []},
            ]
        )
        agent = GrcAgent()
        agent.execute_tool("load_grc", {"file_path": str(fixture_path())})
        history_start = len(agent.history)
        before = graph_snapshot(agent)

        result = evaluate_turn_recovery(
            client=client,
            model="model",
            agent=agent,
            history_start=history_start,
            executed_tool_calls=[
                {
                    "name": "apply_edit",
                    "arguments": {
                        "ok": False,
                        "error_type": "preflight_rejected",
                        "errors": [{"code": "missing_field", "field": "src_block"}],
                    },
                }
            ],
            recovery_enabled=True,
            expected_recovery_class=RECOVERABLE_MISSING_ARGUMENTS,
        )

        self.assertTrue(result["recovery_pass"], result)
        self.assertTrue(result["recovery_changed_state"], result)
        self.assertTrue(result["recovery_end_state_pass"], result)
        self.assertNotEqual(
            before["raw_hash"],
            result["post_recovery_snapshot"]["raw_hash"],
        )
        self.assertEqual(
            result["post_recovery_snapshot"]["variable_values"]["samp_rate"],
            "48000",
        )

    def test_recovery_fails_when_model_uses_disallowed_tool(self) -> None:
        client = _ScriptedClient(
            [
                {
                    "assistant_text": None,
                    "tool_calls": [_ScriptedToolCall("save_graph", {})],
                },
                {"assistant_text": "Tried to save.", "tool_calls": []},
            ]
        )
        agent = GrcAgent()
        agent.execute_tool("load_grc", {"file_path": str(fixture_path())})

        result = evaluate_turn_recovery(
            client=client,
            model="model",
            agent=agent,
            history_start=len(agent.history),
            executed_tool_calls=[
                {
                    "name": "apply_edit",
                    "arguments": {
                        "ok": False,
                        "error_type": "preflight_rejected",
                        "errors": [{"code": "missing_field", "field": "src_block"}],
                    },
                }
            ],
            recovery_enabled=True,
            expected_recovery_class=RECOVERABLE_MISSING_ARGUMENTS,
        )

        self.assertFalse(result["recovery_pass"], result)
        self.assertFalse(result["recovery_allowed_tools_pass"], result)

    def test_save_refused_recovery_allows_validate_then_save(self) -> None:
        client = _ScriptedClient(
            [
                {
                    "assistant_text": None,
                    "tool_calls": [
                        _ScriptedToolCall("validate_graph", {}),
                        _ScriptedToolCall("save_graph", {}),
                    ],
                },
                {"assistant_text": "Validated and saved.", "tool_calls": []},
            ]
        )
        agent = GrcAgent()
        agent.execute_tool("load_grc", {"file_path": str(fixture_path())})

        result = evaluate_turn_recovery(
            client=client,
            model="model",
            agent=agent,
            history_start=len(agent.history),
            executed_tool_calls=[
                {
                    "name": "save_graph",
                    "arguments": {
                        "ok": False,
                        "error_type": "save_refused",
                        "requires_validation": True,
                    },
                }
            ],
            recovery_enabled=True,
            expected_recovery_class=RECOVERABLE_SAVE_REFUSED,
        )

        self.assertTrue(result["recovery_pass"], result)
        self.assertEqual(result["recovery_mutation_retry_count"], 0)
        self.assertEqual(
            [call["name"] for call in result["recovery_requested_tool_calls"]],
            ["validate_graph", "save_graph"],
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

    def test_graph_snapshot_includes_stable_values_and_connection_ids(self) -> None:
        session = self._loaded_session()

        snapshot = graph_snapshot(session)

        self.assertEqual(snapshot["path"], str(fixture_path()))
        self.assertEqual(snapshot["variable_values"]["samp_rate"], "32000")
        self.assertIn("samp_rate", snapshot["block_names"])
        self.assertIn("samp_rate", snapshot["blocks_by_name"])
        self.assertIn(
            "analog_random_source_x_0:0->blocks_throttle2_0:0",
            snapshot["connection_ids"],
        )
        self.assertIsInstance(snapshot["raw_hash"], str)

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
                "end_state_pass": {"passed": 0, "total": 1},
                "recovery_pass": {"passed": 0, "total": 0},
            },
        )
        summary = default_phase_summary(results, 1)
        self.assertEqual(summary["dimension_pass_counts"]["semantic_pass"]["passed"], 0)


class Tier1ReportTests(unittest.TestCase):
    def test_tier1_case_report_uses_dimensioned_passes(self) -> None:
        from tests.llama_eval.tier1_live import Tier1Case, _build_case_report

        case = Tier1Case(
            category="edit",
            name="simple_param_edit",
            prompt="Change samp_rate to 48000.",
            expected_tools=["apply_edit"],
            accept_outcomes=("PASS",),
        )
        report = _build_case_report(
            case,
            [
                {
                    "classification": "PASS",
                    "passed": False,
                    "routing_pass": True,
                    "argument_pass": True,
                    "tool_success_pass": True,
                    "semantic_pass": False,
                    "safety_pass": True,
                    "end_state_pass": False,
                }
            ],
            1,
            0.5,
        )

        self.assertFalse(report["passed"])
        self.assertEqual(report["pass_count"], 0)
        self.assertEqual(report["dimension_pass_counts"]["routing_pass"]["passed"], 1)
        self.assertEqual(report["dimension_pass_counts"]["semantic_pass"]["passed"], 0)

    def test_tier1_summary_reports_dimension_totals(self) -> None:
        from tests.llama_eval.tier1_live import _build_summary

        summary = _build_summary(
            [
                {
                    "category": "edit",
                    "passed": False,
                    "stop_the_line": False,
                    "classifications": ["PASS"],
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
            ],
            1,
        )

        self.assertEqual(summary["dimension_pass_counts"]["routing_pass"]["passed"], 1)
        self.assertEqual(summary["dimension_pass_counts"]["end_state_pass"]["passed"], 0)


class Tier2ReportTests(unittest.TestCase):
    def test_tier2_report_keeps_routing_pass_separate_from_tool_success(self) -> None:
        from tests.llama_eval.tier2_release import Tier2Case, _build_report

        case = Tier2Case(
            category="save",
            name="save_direct",
            prompt="Save the graph.",
            expected_tools=["save_graph"],
        )
        report = _build_report(
            case,
            [
                {
                    "matched": True,
                    "routing_pass": True,
                    "argument_pass": True,
                    "tool_success_pass": False,
                    "semantic_pass": True,
                    "safety_pass": True,
                    "end_state_pass": True,
                }
            ],
            1,
            0.5,
        )

        self.assertTrue(report["passed"])
        self.assertEqual(report["routing_pass_count"], 1)
        self.assertEqual(report["argument_pass_count"], 1)
        self.assertEqual(report["tool_success_pass_count"], 0)
        self.assertEqual(report["semantic_pass_count"], 1)


class Tier4ExternalExamplesTests(unittest.TestCase):
    def test_available_tier4_cases_use_absolute_installed_graph_paths(self) -> None:
        from tests.llama_eval.tier4_external_examples import _available_cases

        cases = _available_cases()
        if not cases:
            self.skipTest("No installed GNU Radio example graphs available")

        self.assertTrue(all(Path(case.fixture_name).is_absolute() for case in cases))
        self.assertTrue(all(Path(case.fixture_name).exists() for case in cases))
        self.assertTrue(all(case.turns for case in cases))

    def test_tier4_includes_external_samp_rate_edit_validate_case(self) -> None:
        from tests.llama_eval.tier4_external_examples import GNU_EXAMPLES, _available_cases

        if not (GNU_EXAMPLES / "audio/dial_tone.grc").exists():
            self.skipTest("dial_tone.grc not installed")
        cases = {case.name: case for case in _available_cases(include_probes=True)}

        case = cases["dial_tone_samp_rate_edit_validate"]
        turn = case.turns[0]

        self.assertEqual([tool.name for tool in turn.expected_tool_calls], ["apply_edit", "validate_graph"])
        self.assertIn(
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
            turn.semantic_checks,
        )

    def test_tier4_includes_external_edit_validate_save_case(self) -> None:
        from tests.llama_eval.tier4_external_examples import GNU_EXAMPLES, _available_cases

        if not (GNU_EXAMPLES / "blocks/selector.grc").exists():
            self.skipTest("selector.grc not installed")
        cases = {case.name: case for case in _available_cases()}

        case = cases["selector_samp_rate_edit_validate_save"]
        turn = case.turns[0]

        self.assertEqual(
            [tool.name for tool in turn.expected_tool_calls],
            ["apply_edit", "validate_graph", "save_graph"],
        )
        self.assertIn(
            {
                "kind": "exact_graph_delta",
                "delta": {
                    "variables": {"samp_rate": "48000"},
                    "block_params": {"samp_rate": {"value": "48000"}},
                    "validation_status": "valid",
                    "validation_returncode": 0,
                },
            },
            turn.semantic_checks,
        )
        self.assertIn({"kind": "saved_path_valid", "path": "{save_path}"}, turn.semantic_checks)

    def test_tier4_includes_promoted_block_param_edit_validate_case(self) -> None:
        from tests.llama_eval.tier4_external_examples import GNU_EXAMPLES, _available_cases

        if not (GNU_EXAMPLES / "blocks/selector.grc").exists():
            self.skipTest("selector.grc not installed")
        cases = {case.name: case for case in _available_cases()}

        case = cases["selector_signal_source_amp_edit_validate"]
        turn = case.turns[0]

        self.assertEqual([tool.name for tool in turn.expected_tool_calls], ["apply_edit", "validate_graph"])
        self.assertIn(
            {
                "kind": "exact_graph_delta",
                "delta": {
                    "block_params": {"analog_sig_source_x_0": {"amp": "0.5"}},
                    "dirty": True,
                    "validation_status": "valid",
                    "validation_returncode": 0,
                },
            },
            turn.semantic_checks,
        )

    def test_tier4_includes_promoted_state_edit_validate_case(self) -> None:
        from tests.llama_eval.tier4_external_examples import GNU_EXAMPLES, _available_cases

        if not (GNU_EXAMPLES / "vocoder/grfreedv.grc").exists():
            self.skipTest("grfreedv.grc not installed")
        cases = {case.name: case for case in _available_cases()}

        case = cases["grfreedv_message_debug_disable_validate"]
        turn = case.turns[0]

        self.assertEqual([tool.name for tool in turn.expected_tool_calls], ["apply_edit", "validate_graph"])
        self.assertIn(
            {
                "kind": "exact_graph_delta",
                "delta": {
                    "block_states": {"blocks_message_debug_0": "disabled"},
                    "dirty": True,
                    "validation_status": "valid",
                    "validation_returncode": 0,
                },
            },
            turn.semantic_checks,
        )

    def test_tier4_promoted_mutation_cases_have_exact_graph_delta(self) -> None:
        from tests.llama_eval.tier4_external_examples import _available_cases

        cases = _available_cases()
        if not cases:
            self.skipTest("No installed GNU Radio example graphs available")

        mutation_tools = {
            "apply_edit",
            "remove_connection",
            "rewire_connection",
            "insert_block_on_connection",
            "auto_insert_block",
        }
        promoted_mutations = [
            case
            for case in cases
            if any(
                tool.name in mutation_tools
                for turn in case.turns
                for tool in turn.expected_tool_calls
            )
        ]

        self.assertTrue(promoted_mutations)
        for case in promoted_mutations:
            for turn in case.turns:
                if any(tool.name in mutation_tools for tool in turn.expected_tool_calls):
                    self.assertTrue(
                        any(check.get("kind") == "exact_graph_delta" for check in turn.semantic_checks),
                        f"{case.name} missing exact_graph_delta",
                    )

    def test_tier4_expands_installed_example_coverage(self) -> None:
        from tests.llama_eval.tier4_external_examples import GNU_EXAMPLES, _available_cases

        required = (
            "pdu/simple_pdu_to_stream_example.grc",
            "digital/packet/tx_stage0.grc",
            "tags/test_tag_prop.grc",
            "qt-gui/qtgui_message_inputs.grc",
        )
        if not all((GNU_EXAMPLES / relative).exists() for relative in required):
            self.skipTest("required installed GNU Radio examples not installed")
        cases = {case.name: case for case in _available_cases()}

        self.assertIn("pdu_simple_validate", cases)
        self.assertIn("tx_stage0_message_disconnect_validate", cases)
        self.assertIn("tags_samp_rate_edit_validate", cases)
        self.assertIn("qtgui_message_inputs_pkt_len_edit_validate", cases)

        disconnect_turn = cases["tx_stage0_message_disconnect_validate"].turns[0]
        self.assertEqual(
            [tool.name for tool in disconnect_turn.expected_tool_calls],
            ["remove_connection", "validate_graph"],
        )
        self.assertIn(
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
            disconnect_turn.semantic_checks,
        )

    def test_tier4_includes_stream_message_and_rollback_coverage(self) -> None:
        from tests.llama_eval.tier4_external_examples import GNU_EXAMPLES, _available_cases

        required = (
            "blocks/stream_mux_demo.grc",
            "analog/sig_source_msg_ports.grc",
            "blocks/var_to_msg.grc",
        )
        if not all((GNU_EXAMPLES / relative).exists() for relative in required):
            self.skipTest("required installed GNU Radio examples not installed")
        release_cases = {case.name: case for case in _available_cases()}
        probe_cases = {case.name: case for case in _available_cases(include_probes=True)}

        self.assertIn("stream_mux_validate", release_cases)
        self.assertIn("sig_source_msg_ports_context", release_cases)
        self.assertIn("var_to_msg_test_value_edit_validate", release_cases)
        self.assertIn("var_to_msg_remove_throttle_rolls_back", release_cases)
        self.assertIn("var_to_msg_remove_throttle_rolls_back", probe_cases)

        rollback_turn = release_cases["var_to_msg_remove_throttle_rolls_back"].turns[0]
        self.assertEqual(
            release_cases["var_to_msg_remove_throttle_rolls_back"].category,
            "external_rollback",
        )
        self.assertEqual([tool.name for tool in rollback_turn.expected_tool_calls], ["apply_edit"])
        self.assertIn({"kind": "exact_graph_delta", "delta": {}}, rollback_turn.semantic_checks)
        self.assertIn({"kind": "no_mutation"}, rollback_turn.semantic_checks)

    def test_tier4_includes_save_reload_after_disconnect_and_rewire(self) -> None:
        from tests.llama_eval.tier4_external_examples import GNU_EXAMPLES, _available_cases

        required = (
            "digital/packet/tx_stage0.grc",
            "digital/burst_shaper.grc",
        )
        if not all((GNU_EXAMPLES / relative).exists() for relative in required):
            self.skipTest("required installed GNU Radio examples not installed")
        cases = {case.name: case for case in _available_cases()}

        disconnect_turn = cases["tx_stage0_message_disconnect_validate_save"].turns[0]
        self.assertEqual(
            [tool.name for tool in disconnect_turn.expected_tool_calls],
            ["remove_connection", "validate_graph", "save_graph"],
        )
        self.assertIn({"kind": "saved_path_valid", "path": "{save_path}"}, disconnect_turn.semantic_checks)
        self.assertIn(
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
            disconnect_turn.semantic_checks,
        )

        rewire_turn = cases["burst_shaper_stream_rewire_validate_save"].turns[0]
        self.assertEqual(
            [tool.name for tool in rewire_turn.expected_tool_calls],
            ["rewire_connection", "validate_graph", "save_graph"],
        )
        self.assertIn({"kind": "saved_path_valid", "path": "{save_path}"}, rewire_turn.semantic_checks)
        self.assertIn(
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
            rewire_turn.semantic_checks,
        )

        clarified_case = cases["burst_shaper_clarified_rewire_validate_save"]
        self.assertEqual(len(clarified_case.turns), 3)

        clarification_turn = clarified_case.turns[0]
        self.assertEqual(
            [tool.name for tool in clarification_turn.expected_tool_calls],
            ["rewire_connection"],
        )
        self.assertFalse(clarification_turn.expected_tool_calls[0].require_result_ok)
        self.assertIn({"kind": "exact_graph_delta", "delta": {}}, clarification_turn.semantic_checks)
        self.assertIn({"kind": "no_mutation"}, clarification_turn.semantic_checks)
        self.assertIn(
            {
                "kind": "tool_result",
                "tool": "rewire_connection",
                "arguments": {
                    "clarification_required": True,
                    "kind": "rewire_connection_disambiguation",
                },
            },
            clarification_turn.semantic_checks,
        )

        clarified_rewire_turn = clarified_case.turns[1]
        self.assertTrue(clarified_rewire_turn.clarification_response)
        self.assertEqual(
            [tool.name for tool in clarified_rewire_turn.expected_tool_calls],
            ["rewire_connection"],
        )
        self.assertIn(
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
            clarified_rewire_turn.semantic_checks,
        )

        clarified_save_turn = clarified_case.turns[2]
        self.assertEqual(
            [tool.name for tool in clarified_save_turn.expected_tool_calls],
            ["validate_graph", "save_graph"],
        )
        self.assertIn({"kind": "saved_path_valid", "path": "{save_path}"}, clarified_save_turn.semantic_checks)
        self.assertIn(
            {
                "kind": "saved_connection_absent",
                "path": "{save_path}",
                "connection_id": "blocks_throttle_0:0->blocks_tag_debug_0:0",
            },
            clarified_save_turn.semantic_checks,
        )
        self.assertIn(
            {
                "kind": "saved_connection_present",
                "path": "{save_path}",
                "connection_id": (
                    "blocks_vector_source_x_0_0:0->blocks_tag_debug_0:0"
                ),
            },
            clarified_save_turn.semantic_checks,
        )

    def test_tier4_includes_broader_installed_reliability_cases(self) -> None:
        from tests.llama_eval.tier4_external_examples import GNU_EXAMPLES, _available_cases

        required = (
            "analog/sig_source_msg_ports.grc",
            "digital/packet/simple_bpsk_tx.grc",
            "pdu/pdu_tools_demo.grc",
            "blocks/stream_demux_demo.grc",
            "filter/filter_taps.grc",
            "digital/burst_shaper.grc",
            "digital/packet/uhd_packet_rx.grc",
        )
        if not all((GNU_EXAMPLES / relative).exists() for relative in required):
            self.skipTest("required installed GNU Radio examples not installed")
        cases = {case.name: case for case in _available_cases()}

        identity_turn = cases["burst_shaper_duplicate_family_summary"].turns[0]
        self.assertEqual([tool.name for tool in identity_turn.expected_tool_calls], ["summarize_graph"])
        self.assertIn({"kind": "exact_graph_delta", "delta": {}}, identity_turn.semantic_checks)
        self.assertIn({"kind": "no_mutation"}, identity_turn.semantic_checks)

        disconnect_turn = cases["sig_source_msg_random_disconnect_validate_save"].turns[0]
        self.assertEqual(
            [tool.name for tool in disconnect_turn.expected_tool_calls],
            ["remove_connection", "validate_graph", "save_graph"],
        )
        self.assertIn({"kind": "saved_path_valid", "path": "{save_path}"}, disconnect_turn.semantic_checks)
        self.assertIn(
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
            disconnect_turn.semantic_checks,
        )

        state_turn = cases["simple_bpsk_tag_debug_enable_validate_save"].turns[0]
        self.assertEqual(
            [tool.name for tool in state_turn.expected_tool_calls],
            ["apply_edit", "validate_graph", "save_graph"],
        )
        self.assertIn(
            {
                "kind": "exact_graph_delta",
                "delta": {
                    "block_states": {"blocks_tag_debug_0": "enabled"},
                    "validation_status": "valid",
                    "validation_returncode": 0,
                },
            },
            state_turn.semantic_checks,
        )
        self.assertIn({"kind": "saved_path_valid", "path": "{save_path}"}, state_turn.semantic_checks)
        self.assertIn(
            {
                "kind": "saved_block_state_equals",
                "path": "{save_path}",
                "instance_name": "blocks_tag_debug_0",
                "state": "enabled",
            },
            state_turn.semantic_checks,
        )

        filter_save_turn = cases["filter_cutoff_low_edit_validate_save"].turns[0]
        self.assertEqual(
            [tool.name for tool in filter_save_turn.expected_tool_calls],
            ["apply_edit", "validate_graph", "save_graph"],
        )
        self.assertIn(
            {
                "kind": "exact_graph_delta",
                "delta": {
                    "variables": {"cutoff_low": 3000},
                    "block_params": {"cutoff_low": {"value": 3000}},
                    "validation_status": "valid",
                    "validation_returncode": 0,
                },
            },
            filter_save_turn.semantic_checks,
        )
        self.assertIn(
            {"kind": "saved_path_valid", "path": "{save_path}", "copy": True},
            filter_save_turn.semantic_checks,
        )
        self.assertIn(
            {
                "kind": "saved_variable_equals",
                "path": "{save_path}",
                "name": "cutoff_low",
                "value": "3000",
            },
            filter_save_turn.semantic_checks,
        )
        self.assertIn(
            {
                "kind": "saved_block_param_equals",
                "path": "{save_path}",
                "instance_name": "cutoff_low",
                "param": "value",
                "value": "3000",
            },
            filter_save_turn.semantic_checks,
        )

        pdu_turn = cases["pdu_tools_random_pdu_maxsize_edit_validate"].turns[0]
        self.assertEqual([tool.name for tool in pdu_turn.expected_tool_calls], ["apply_edit", "validate_graph"])
        self.assertIn(
            {
                "kind": "exact_graph_delta",
                "delta": {
                    "block_params": {"random_pdu": {"maxsize": "8192"}},
                    "dirty": True,
                    "validation_status": "valid",
                    "validation_returncode": 0,
                },
            },
            pdu_turn.semantic_checks,
        )

        pdu_rollback_turn = cases["pdu_tools_message_disconnect_rolls_back"].turns[0]
        self.assertEqual(
            [tool.name for tool in pdu_rollback_turn.expected_tool_calls],
            ["remove_connection"],
        )
        self.assertFalse(pdu_rollback_turn.expected_tool_calls[0].require_result_ok)
        self.assertIn({"kind": "exact_graph_delta", "delta": {}}, pdu_rollback_turn.semantic_checks)
        self.assertIn({"kind": "no_mutation"}, pdu_rollback_turn.semantic_checks)
        self.assertIn(
            {
                "kind": "tool_result",
                "tool": "remove_connection",
                "arguments": {
                    "ok": False,
                    "applied": False,
                    "error_type": "preflight_rejected",
                },
            },
            pdu_rollback_turn.semantic_checks,
        )

        demux_turn = cases["stream_demux_lengths_edit_validate"].turns[0]
        self.assertEqual([tool.name for tool in demux_turn.expected_tool_calls], ["apply_edit", "validate_graph"])
        self.assertIn(
            {
                "kind": "exact_graph_delta",
                "delta": {
                    "block_params": {"blocks_stream_demux_0": {"lengths": [1, 3, 5]}},
                    "dirty": True,
                    "validation_status": "valid",
                    "validation_returncode": 0,
                },
            },
            demux_turn.semantic_checks,
        )

        mixed_domain_turn = cases["var_to_msg_stream_to_message_add_clarifies"].turns[0]
        self.assertEqual(mixed_domain_turn.expected_tool_calls, ())
        self.assertIn({"kind": "exact_graph_delta", "delta": {}}, mixed_domain_turn.semantic_checks)
        self.assertIn({"kind": "no_mutation"}, mixed_domain_turn.semantic_checks)
        self.assertIn(
            {
                "kind": "connection_absent",
                "connection_id": "blocks_null_source_0:0->blocks_message_debug_0:store",
            },
            mixed_domain_turn.semantic_checks,
        )

        reverse_mixed_domain_turn = cases["var_to_msg_message_to_stream_add_clarifies"].turns[0]
        self.assertEqual(reverse_mixed_domain_turn.expected_tool_calls, ())
        self.assertIn(
            {"kind": "exact_graph_delta", "delta": {}},
            reverse_mixed_domain_turn.semantic_checks,
        )
        self.assertIn({"kind": "no_mutation"}, reverse_mixed_domain_turn.semantic_checks)
        self.assertIn(
            {
                "kind": "connection_absent",
                "connection_id": "blocks_var_to_msg_0:msgout->blocks_null_sink_0:0",
            },
            reverse_mixed_domain_turn.semantic_checks,
        )

        duplicate_name_turn = cases["uhd_packet_rx_duplicate_constellation_edit_rejected"].turns[0]
        self.assertEqual([tool.name for tool in duplicate_name_turn.expected_tool_calls], ["apply_edit"])
        self.assertFalse(duplicate_name_turn.expected_tool_calls[0].require_result_ok)
        self.assertIn({"kind": "exact_graph_delta", "delta": {}}, duplicate_name_turn.semantic_checks)
        self.assertIn({"kind": "no_mutation"}, duplicate_name_turn.semantic_checks)
        self.assertIn(
            {
                "kind": "tool_result",
                "tool": "apply_edit",
                "arguments": {
                    "ok": False,
                    "applied": False,
                    "error_type": "preflight_rejected",
                    "errors": [
                        {
                            "field": "instance_name",
                            "code": "block_name_not_unique",
                        }
                    ],
                },
            },
            duplicate_name_turn.semantic_checks,
        )

        duplicate_wbfm_turn = cases["uhd_wbfm_duplicate_tun_freq_edit_rejected"].turns[0]
        self.assertEqual([tool.name for tool in duplicate_wbfm_turn.expected_tool_calls], ["apply_edit"])
        self.assertFalse(duplicate_wbfm_turn.expected_tool_calls[0].require_result_ok)
        self.assertIn({"kind": "exact_graph_delta", "delta": {}}, duplicate_wbfm_turn.semantic_checks)
        self.assertIn({"kind": "no_mutation"}, duplicate_wbfm_turn.semantic_checks)
        self.assertIn(
            {
                "kind": "tool_result",
                "tool": "apply_edit",
                "arguments": {
                    "ok": False,
                    "applied": False,
                    "error_type": "preflight_rejected",
                    "errors": [
                        {
                            "field": "instance_name",
                            "code": "block_name_not_unique",
                        }
                    ],
                },
            },
            duplicate_wbfm_turn.semantic_checks,
        )

    def test_tier4_includes_negative_installed_reliability_cases(self) -> None:
        from tests.llama_eval.tier4_external_examples import GNU_EXAMPLES, _available_cases

        required = (
            "blocks/selector.grc",
            "audio/dial_tone.grc",
        )
        if not all((GNU_EXAMPLES / relative).exists() for relative in required):
            self.skipTest("required installed GNU Radio examples not installed")
        cases = {case.name: case for case in _available_cases()}
        probe_cases = {case.name: case for case in _available_cases(include_probes=True)}

        disconnect_turn = cases["selector_output_disconnect_rolls_back"].turns[0]
        self.assertEqual(
            [tool.name for tool in disconnect_turn.expected_tool_calls],
            ["remove_connection"],
        )
        self.assertFalse(disconnect_turn.expected_tool_calls[0].require_result_ok)
        self.assertIn({"kind": "exact_graph_delta", "delta": {}}, disconnect_turn.semantic_checks)
        self.assertIn({"kind": "no_mutation"}, disconnect_turn.semantic_checks)
        self.assertIn(
            {
                "kind": "tool_result",
                "tool": "remove_connection",
                "arguments": {
                    "ok": False,
                    "applied": False,
                    "error_type": "gnu_validation_failed",
                },
            },
            disconnect_turn.semantic_checks,
        )

        rewire_turn = cases["selector_occupied_rewire_rolls_back"].turns[0]
        self.assertEqual(
            [tool.name for tool in rewire_turn.expected_tool_calls],
            ["rewire_connection"],
        )
        self.assertFalse(rewire_turn.expected_tool_calls[0].require_result_ok)
        self.assertIn({"kind": "exact_graph_delta", "delta": {}}, rewire_turn.semantic_checks)
        self.assertIn({"kind": "no_mutation"}, rewire_turn.semantic_checks)
        self.assertIn(
            {
                "kind": "tool_result",
                "tool": "rewire_connection",
                "arguments": {
                    "ok": False,
                    "applied": False,
                    "error_type": "preflight_rejected",
                },
            },
            rewire_turn.semantic_checks,
        )

        remove_turn = cases["selector_connected_block_remove_rolls_back"].turns[0]
        self.assertEqual([tool.name for tool in remove_turn.expected_tool_calls], ["apply_edit"])
        self.assertFalse(remove_turn.expected_tool_calls[0].require_result_ok)
        self.assertIn({"kind": "exact_graph_delta", "delta": {}}, remove_turn.semantic_checks)
        self.assertIn({"kind": "no_mutation"}, remove_turn.semantic_checks)

        saved_remove_case = cases["selector_saved_connected_block_remove_rolls_back"]
        self.assertEqual(len(saved_remove_case.turns), 2)
        save_turn = saved_remove_case.turns[0]
        self.assertEqual([tool.name for tool in save_turn.expected_tool_calls], ["save_graph"])
        self.assertIn(
            {"kind": "saved_path_valid", "path": "{save_path}", "copy": True},
            save_turn.semantic_checks,
        )
        self.assertIn(
            {
                "kind": "saved_block_present",
                "path": "{save_path}",
                "instance_name": "blocks_selector_0",
            },
            save_turn.semantic_checks,
        )
        saved_remove_turn = saved_remove_case.turns[1]
        self.assertEqual([tool.name for tool in saved_remove_turn.expected_tool_calls], ["apply_edit"])
        self.assertFalse(saved_remove_turn.expected_tool_calls[0].require_result_ok)
        self.assertIn({"kind": "exact_graph_delta", "delta": {}}, saved_remove_turn.semantic_checks)
        self.assertIn({"kind": "no_mutation"}, saved_remove_turn.semantic_checks)
        self.assertIn(
            {
                "kind": "saved_connection_present",
                "path": "{save_path}",
                "connection_id": "blocks_selector_0:0->qtgui_time_sink_x_0:0",
            },
            saved_remove_turn.semantic_checks,
        )

        duplicate_probe = probe_cases["dial_tone_duplicate_connection_rolls_back"]
        self.assertEqual(duplicate_probe.category, "external_probe")
        self.assertNotIn("dial_tone_duplicate_connection_rolls_back", cases)
        duplicate_add_turn = duplicate_probe.turns[0]
        self.assertEqual([tool.name for tool in duplicate_add_turn.expected_tool_calls], ["apply_edit"])
        self.assertFalse(duplicate_add_turn.expected_tool_calls[0].require_result_ok)
        self.assertIn({"kind": "exact_graph_delta", "delta": {}}, duplicate_add_turn.semantic_checks)
        self.assertIn({"kind": "no_mutation"}, duplicate_add_turn.semantic_checks)
        self.assertIn(
            {
                "kind": "tool_result",
                "tool": "apply_edit",
                "arguments": {
                    "ok": False,
                    "applied": False,
                    "error_type": "preflight_rejected",
                },
            },
            duplicate_add_turn.semantic_checks,
        )

        occupied_probe = probe_cases["dial_tone_occupied_input_add_rolls_back"]
        self.assertEqual(occupied_probe.category, "external_probe")
        self.assertNotIn("dial_tone_occupied_input_add_rolls_back", cases)
        occupied_add_turn = occupied_probe.turns[0]
        self.assertEqual([tool.name for tool in occupied_add_turn.expected_tool_calls], ["apply_edit"])
        self.assertFalse(occupied_add_turn.expected_tool_calls[0].require_result_ok)
        self.assertIn({"kind": "exact_graph_delta", "delta": {}}, occupied_add_turn.semantic_checks)
        self.assertIn({"kind": "no_mutation"}, occupied_add_turn.semantic_checks)

        disconnect_turn = cases["dial_tone_output_disconnect_rolls_back"].turns[0]
        self.assertEqual(
            [tool.name for tool in disconnect_turn.expected_tool_calls],
            ["remove_connection"],
        )
        self.assertFalse(disconnect_turn.expected_tool_calls[0].require_result_ok)
        self.assertIn({"kind": "exact_graph_delta", "delta": {}}, disconnect_turn.semantic_checks)
        self.assertIn({"kind": "no_mutation"}, disconnect_turn.semantic_checks)


if __name__ == "__main__":
    unittest.main()
