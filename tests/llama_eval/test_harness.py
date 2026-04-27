"""Unit tests for shared llama eval harness helpers."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from types import SimpleNamespace
from unittest import mock

from tests.llama_eval.harness import (
    RUN_STATUS_INFRA_FAIL,
    default_phase_summary,
    executed_tool_calls_since,
    extract_executed_tool_calls,
    extract_requested_tool_calls,
    is_infra_error_message,
    normalize_transaction_operations,
    render_prompt,
    render_value_templates,
    requested_tool_calls_since,
    run_phase_eval,
    run_result_is_infra_failure,
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


if __name__ == "__main__":
    unittest.main()
