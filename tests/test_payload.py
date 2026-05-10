"""Tests for the shared payload helpers."""

import inspect
import unittest

import grc_agent._payload as payload_module
from grc_agent._payload import build_error_payload, join_non_empty


class BuildErrorPayloadTests(unittest.TestCase):
    def test_basic_error_payload(self) -> None:
        payload = build_error_payload(
            error_type="test_error",
            message="Something went wrong",
        )
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error_type"], "test_error")
        self.assertEqual(payload["message"], "Something went wrong")
        self.assertNotIn("details", payload)

    def test_error_payload_with_details(self) -> None:
        payload = build_error_payload(
            error_type="test_error",
            message="Details included",
            details={"field": "value"},
        )
        self.assertIn("details", payload)
        self.assertEqual(payload["details"]["field"], "value")

    def test_error_payload_omits_empty_details(self) -> None:
        payload = build_error_payload(
            error_type="test_error",
            message="No details",
            details=None,
        )
        self.assertNotIn("details", payload)


class JoinNonEmptyTests(unittest.TestCase):
    def test_joins_non_empty_parts(self) -> None:
        self.assertEqual(join_non_empty("hello", "world"), "hello world")

    def test_skips_empty_parts(self) -> None:
        self.assertEqual(join_non_empty("hello", "", "world"), "hello world")

    def test_all_empty_returns_empty(self) -> None:
        self.assertEqual(join_non_empty("", "", ""), "")

    def test_single_part(self) -> None:
        self.assertEqual(join_non_empty("hello"), "hello")

    def test_no_parts_returns_empty(self) -> None:
        self.assertEqual(join_non_empty(), "")

    def test_joins_with_internal_whitespace_preserved(self) -> None:
        result = join_non_empty("  hello  ", "  world  ")
        self.assertIn("hello", result)
        self.assertIn("world", result)


class PayloadModuleContractTests(unittest.TestCase):
    def test_module_exports_only_two_helper_functions(self) -> None:
        function_names = {
            name
            for name, value in inspect.getmembers(payload_module, inspect.isfunction)
            if value.__module__ == payload_module.__name__
        }
        self.assertEqual(
            function_names,
            {
                "audit_change_graph_result_shape",
                "build_error_payload",
                "join_non_empty",
            },
        )


if __name__ == "__main__":
    unittest.main()
