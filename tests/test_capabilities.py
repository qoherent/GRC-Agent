"""Tests for change_graph capability metadata contracts."""

from __future__ import annotations

import json
from pathlib import Path
import unittest

from grc_agent.runtime.capabilities import (
    CAPABILITY_SPECS,
    CONTROL_OUTCOME_KINDS,
    EXPERIMENTAL_OPERATION_SPECS,
    capability_specs,
    change_graph_operation_kinds,
    get_capability_spec,
    non_capability_operation_kinds,
)
from tests.llama_eval.release_dashboard import load_capability_manifests


class CapabilitySpecTests(unittest.TestCase):
    def test_mutation_capability_set_is_expected(self) -> None:
        self.assertEqual(
            set(CAPABILITY_SPECS),
            {
                "set_param",
                "set_state",
                "disconnect",
                "rewire",
                "insert_block",
                "remove_block",
                "add_variable",
            },
        )

    def test_non_capability_kinds_are_control_or_experimental(self) -> None:
        non_capability = set(non_capability_operation_kinds())
        self.assertEqual(
            non_capability,
            set(CONTROL_OUTCOME_KINDS) | set(EXPERIMENTAL_OPERATION_SPECS),
        )

    def test_capability_specs_are_stable(self) -> None:
        self.assertEqual(
            [spec.operation_kind for spec in capability_specs()],
            list(CAPABILITY_SPECS.keys()),
        )

    def test_documented_statuses_match_current_classification(self) -> None:
        expected = {
            "set_param": "release_validated",
            "set_state": "beta_validated",
            "disconnect": "beta_validated",
            "rewire": "beta_validated",
            "insert_block": "beta_validated",
            "remove_block": "beta_validated",
            "add_variable": "beta_validated",
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

    def test_add_variable_is_beta_validated(self) -> None:
        spec = get_capability_spec("add_variable")
        self.assertEqual(spec.status, "beta_validated")
        self.assertEqual(spec.eval_suite, "R4C_ADD_VARIABLE")

    def test_unknown_operation_kind_is_not_silent(self) -> None:
        with self.assertRaises(KeyError):
            get_capability_spec("not_real")

    def test_auto_insert_is_experimental_non_gating(self) -> None:
        spec = EXPERIMENTAL_OPERATION_SPECS["auto_insert"]
        self.assertEqual(spec.status, "unvalidated")
        self.assertFalse(spec.release_gating)


class CapabilityAlignmentTests(unittest.TestCase):
    def test_specs_docs_and_manifests_agree(self) -> None:
        docs_path = Path("docs/capability_classification.json")
        docs = json.loads(docs_path.read_text(encoding="utf-8"))
        docs_caps = docs["change_graph_capabilities"]
        manifests = load_capability_manifests()

        self.assertEqual(set(docs_caps), set(CAPABILITY_SPECS))
        for operation_kind, spec in CAPABILITY_SPECS.items():
            with self.subTest(operation_kind=operation_kind):
                doc_entry = docs_caps[operation_kind]
                self.assertEqual(doc_entry["status"], spec.status)
                self.assertEqual(doc_entry["eval_suite"], spec.eval_suite)
                suite = spec.eval_suite
                if suite:
                    self.assertIn(suite, manifests)
                    manifest = manifests[suite]
                    self.assertEqual(manifest["status"], spec.status)
                    self.assertEqual(bool(manifest["release_gating"]), bool(doc_entry["release_gating"]))

    def test_docs_control_and_experimental_kinds_match_schema(self) -> None:
        docs = json.loads(Path("docs/capability_classification.json").read_text(encoding="utf-8"))
        control = set(docs["control_outcomes"])
        experimental = set(docs["experimental_non_gating"])
        schema_kinds = set(change_graph_operation_kinds())
        self.assertTrue(control <= schema_kinds)
        self.assertTrue(experimental <= schema_kinds)
        self.assertEqual(control, set(CONTROL_OUTCOME_KINDS))
        self.assertEqual(experimental, set(EXPERIMENTAL_OPERATION_SPECS))


if __name__ == "__main__":
    unittest.main()
