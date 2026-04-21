"""Unit tests for llama eval phase-runner helpers."""

from __future__ import annotations

import unittest

from tests.llama_eval.harness import requested_tool_calls_since
from tests.llama_eval.run_phase2 import _successful_tools_appear_in_expected_order
from tests.llama_eval.run_phase3 import PHASE3_CASES
from tests.llama_eval.run_phase5 import ExecutedToolSpec, _executed_tools_match


class Phase2RunnerTests(unittest.TestCase):
    def test_preview_case_can_count_expected_failed_tool_result(self) -> None:
        executed_tool_calls = [
            {
                "name": "propose_edit",
                "arguments": {"ok": False, "message": "Preview failed."},
            }
        ]

        self.assertFalse(
            _successful_tools_appear_in_expected_order(
                executed_tool_calls,
                ["propose_edit"],
            )
        )
        self.assertTrue(
            _successful_tools_appear_in_expected_order(
                executed_tool_calls,
                ["propose_edit"],
                require_successful_tool_results=False,
            )
        )


class Phase3RunnerTests(unittest.TestCase):
    def test_change_rate_validate_save_checks_apply_edit_arguments(self) -> None:
        case = next(
            case
            for case in PHASE3_CASES
            if case.name == "change_rate_validate_save"
        )

        self.assertEqual(case.checked_tool_name, "apply_edit")


class Phase4RunnerTests(unittest.TestCase):
    def test_requested_tool_calls_since_uses_only_current_turn_history(self) -> None:
        history = [
            {
                "role": "assistant",
                "tool_calls": [{"name": "apply_edit", "arguments": {}}],
            },
            {
                "role": "assistant",
                "tool_calls": [{"name": "validate_graph", "arguments": {}}],
            },
        ]

        self.assertEqual(
            requested_tool_calls_since(history, 1),
            [{"name": "validate_graph", "arguments": {}}],
        )


class Phase5RunnerTests(unittest.TestCase):
    def test_executed_tools_match_supports_failure_then_recovery_sequences(self) -> None:
        executed_tool_calls = [
            {"name": "apply_edit", "arguments": {"ok": False}},
            {"name": "apply_edit", "arguments": {"ok": True}},
        ]

        self.assertTrue(
            _executed_tools_match(
                executed_tool_calls,
                [
                    ExecutedToolSpec("apply_edit", {"ok": False}),
                    ExecutedToolSpec("apply_edit", {"ok": True}),
                ],
            )
        )


if __name__ == "__main__":
    unittest.main()
