"""Unit tests for llama eval phase-runner helpers."""

from __future__ import annotations

import unittest

from tests.llama_eval.harness import requested_tool_calls_since
from tests.llama_eval.run_phase2 import _successful_tools_appear_in_expected_order
from tests.llama_eval.run_phase4 import PHASE4_CASES, _evaluate_case_postconditions as _evaluate_phase4_postconditions
from tests.llama_eval.run_phase6 import PHASE6_CASES, _evaluate_case_postconditions as _evaluate_phase6_postconditions
from tests.llama_eval.run_phase3 import PHASE3_CASES, _evaluate_postconditions
from tests.llama_eval.run_phase5 import (
    PHASE5_CASES,
    ExecutedToolSpec,
    _evaluate_case_postconditions,
    _executed_tools_match,
)


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

    def test_remove_variable_postcondition_requires_target_absent(self) -> None:
        case = next(
            case
            for case in PHASE3_CASES
            if case.name == "remove_samp_rate_keep_valid"
        )
        session = type(
            "SessionStub",
            (),
            {
                "flowgraph": type(
                    "FlowgraphStub",
                    (),
                    {
                        "blocks": [
                            type("BlockStub", (), {"instance_name": "samp_rate"})(),
                            type("BlockStub", (), {"instance_name": "other"})(),
                        ]
                    },
                )()
            },
        )()

        result = _evaluate_postconditions(
            case,
            requested_tool_names=["apply_edit"],
            session=session,
        )

        self.assertFalse(result["passed"])
        self.assertFalse(result["required_absent_nodes"]["samp_rate"])


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

    def test_case_postconditions_require_summary_and_validation_when_expected(self) -> None:
        case = next(
            case
            for case in PHASE4_CASES
            if case.name == "add_then_validate_then_summary"
        )
        session = type("SessionStub", (), {"flowgraph": type("FlowgraphStub", (), {"blocks": []})()})()

        result = _evaluate_phase4_postconditions(
            case,
            requested_tool_names=["validate_graph"],
            session=session,
        )

        self.assertFalse(result["passed"])
        self.assertTrue(result["validate_called"])
        self.assertFalse(result["summary_called"])


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

    def test_case_postcondition_requires_removed_node_absent(self) -> None:
        case = next(
            case
            for case in PHASE5_CASES
            if case.name == "preview_reference_fail_then_apply_fix"
        )
        session = type(
            "SessionStub",
            (),
            {
                "flowgraph": type(
                    "FlowgraphStub",
                    (),
                    {
                        "blocks": [
                            type("BlockStub", (), {"instance_name": "samp_rate"})(),
                        ]
                    },
                )()
            },
        )()

        result = _evaluate_case_postconditions(
            case,
            requested_tool_names=["propose_edit", "apply_edit"],
            session=session,
        )

        self.assertFalse(result["passed"])
        self.assertFalse(result["required_absent_nodes"]["samp_rate"])


class Phase6RunnerTests(unittest.TestCase):
    def test_case_postconditions_require_validate_when_expected(self) -> None:
        case = next(
            case for case in PHASE6_CASES if case.name == "psk_explore_then_edit"
        )
        session = type("SessionStub", (), {"flowgraph": type("FlowgraphStub", (), {"blocks": []})()})()

        result = _evaluate_phase6_postconditions(
            case,
            requested_tool_names=["apply_edit"],
            session=session,
        )

        self.assertFalse(result["passed"])
        self.assertFalse(result["validate_called"])


if __name__ == "__main__":
    unittest.main()
