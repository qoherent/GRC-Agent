"""Unit tests for shared llama eval harness helpers."""

from __future__ import annotations

import unittest

from tests.llama_eval.harness import (
    executed_tool_calls_since,
    extract_executed_tool_calls,
    extract_requested_tool_calls,
    normalize_transaction_operations,
    render_prompt,
    render_value_templates,
    requested_tool_calls_since,
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


if __name__ == "__main__":
    unittest.main()
