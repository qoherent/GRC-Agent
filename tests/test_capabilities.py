"""Tests for change_graph capability metadata contracts."""

from __future__ import annotations

import unittest

from grc_agent.runtime.capabilities import (
    CAPABILITY_SPECS,
    capability_specs_for_change_graph,
    change_graph_operation_kinds,
    get_capability_spec,
)


class CapabilitySpecTests(unittest.TestCase):
    def test_every_change_graph_operation_has_exactly_one_spec(self) -> None:
        kinds = set(change_graph_operation_kinds())
        self.assertEqual(kinds, set(CAPABILITY_SPECS.keys()))

    def test_specs_are_returned_in_schema_order(self) -> None:
        ordered_specs = capability_specs_for_change_graph()
        self.assertEqual(
            [spec.operation_kind for spec in ordered_specs],
            list(change_graph_operation_kinds()),
        )

    def test_documented_statuses_match_current_classification(self) -> None:
        expected = {
            "set_param": "release_validated",
            "set_state": "beta_validated",
            "disconnect": "beta_validated",
            "rewire": "beta_validated",
            "insert_block": "beta_validated",
            "remove_block": "beta_validated",
            "add_variable": "unvalidated",
        }
        for operation_kind, status in expected.items():
            with self.subTest(operation_kind=operation_kind):
                self.assertEqual(get_capability_spec(operation_kind).status, status)

    def test_release_and_beta_specs_require_eval_suite(self) -> None:
        for spec in CAPABILITY_SPECS.values():
            with self.subTest(operation_kind=spec.operation_kind):
                if spec.status in {"release_validated", "beta_validated"}:
                    self.assertIsInstance(spec.eval_suite, str)
                    self.assertTrue(spec.eval_suite)

    def test_add_variable_remains_unvalidated(self) -> None:
        spec = get_capability_spec("add_variable")
        self.assertEqual(spec.status, "unvalidated")
        self.assertIsNone(spec.eval_suite)

    def test_unknown_operation_kind_is_not_silent(self) -> None:
        with self.assertRaises(KeyError):
            get_capability_spec("not_real")


if __name__ == "__main__":
    unittest.main()
