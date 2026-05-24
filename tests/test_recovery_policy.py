"""Tests for typed recovery classification shared by runtime and live evals."""

from __future__ import annotations

import unittest

from grc_agent.recovery import (
    RECOVERABLE_CLARIFICATION,
    RECOVERABLE_MISSING_ARGUMENTS,
    RECOVERABLE_SAVE_REFUSED,
    NONRECOVERABLE_INVALID_END_STATE,
    NONRECOVERABLE_UNSUPPORTED,
    classify_tool_result_for_recovery,
)


class RecoveryPolicyTests(unittest.TestCase):
    def test_missing_apply_edit_fields_are_recoverable_with_bounded_tools(self) -> None:
        decision = classify_tool_result_for_recovery(
            "apply_edit",
            {
                "ok": False,
                "error_type": "preflight_rejected",
                "errors": [
                    {
                        "op_type": "remove_connection",
                        "code": "missing_field",
                        "field": "src_block",
                    }
                ],
            },
        )

        self.assertEqual(decision.recovery_class, RECOVERABLE_MISSING_ARGUMENTS)
        self.assertTrue(decision.recoverable)
        self.assertEqual(decision.max_mutation_retries, 1)
        self.assertIn("get_grc_context", decision.allowed_tools)
        self.assertIn("apply_edit", decision.allowed_tools)
        self.assertIn("call apply_edit", decision.prompt)
        self.assertIn("Do not call propose_edit", decision.prompt)

    def test_schema_missing_apply_edit_transaction_is_recoverable(self) -> None:
        decision = classify_tool_result_for_recovery(
            "apply_edit",
            {
                "ok": False,
                "error_type": "tool_call_invalid",
                "validation_errors": [
                    {
                        "code": "missing_required",
                        "field": "transaction",
                    }
                ],
            },
        )

        self.assertEqual(decision.recovery_class, RECOVERABLE_MISSING_ARGUMENTS)
        self.assertTrue(decision.recoverable)
        self.assertEqual(decision.max_mutation_retries, 1)

    def test_dangling_port_gnu_failure_is_not_recoverable(self) -> None:
        decision = classify_tool_result_for_recovery(
            "apply_edit",
            {
                "ok": False,
                "error_type": "gnu_validation_failed",
                "validation": {
                    "stdout": "Source - out(0):\n\tPort is not connected."
                },
            },
        )

        self.assertEqual(decision.recovery_class, NONRECOVERABLE_INVALID_END_STATE)
        self.assertFalse(decision.recoverable)
        self.assertEqual(decision.allowed_tools, ())

    def test_dirty_save_refusal_is_recoverable_by_validation_then_save(self) -> None:
        decision = classify_tool_result_for_recovery(
            "save_graph",
            {
                "ok": False,
                "error_type": "save_refused",
                "requires_validation": True,
            },
        )

        self.assertEqual(decision.recovery_class, RECOVERABLE_SAVE_REFUSED)
        self.assertTrue(decision.recoverable)
        self.assertEqual(
            decision.allowed_tools,
            ("validate_graph", "save_graph"),
        )
        self.assertEqual(decision.max_mutation_retries, 0)

    def test_clarification_payload_is_recoverable_without_model_mutation_retry(self) -> None:
        decision = classify_tool_result_for_recovery(
            "auto_insert_block",
            {
                "ok": False,
                "clarification_required": True,
                "options": [],
            },
        )

        self.assertEqual(decision.recovery_class, RECOVERABLE_CLARIFICATION)
        self.assertTrue(decision.recoverable)
        self.assertEqual(decision.allowed_tools, ())
        self.assertEqual(decision.max_mutation_retries, 0)

    def test_unsupported_requests_are_not_recoverable(self) -> None:
        decision = classify_tool_result_for_recovery(
            "apply_edit",
            {
                "ok": False,
                "error_type": "unsupported",
            },
        )

        self.assertEqual(decision.recovery_class, NONRECOVERABLE_UNSUPPORTED)
        self.assertFalse(decision.recoverable)


if __name__ == "__main__":
    unittest.main()
