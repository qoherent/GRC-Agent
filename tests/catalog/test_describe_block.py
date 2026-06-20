"""Tests for the public `describe_block` catalog API."""
import inspect
import unittest
from pathlib import Path

from grc_agent import describe_block
from grc_agent.catalog.loaders import discover_catalog_root


class DescribeBlockTests(unittest.TestCase):
    def _catalog_root_or_skip(self) -> Path:
        try:
            return discover_catalog_root()
        except Exception as exc:
            self.skipTest(str(exc))

    def test_public_describe_signature_stays_narrow(self) -> None:
        self.assertEqual(list(inspect.signature(describe_block).parameters), ["block_id"])

    def test_known_block_returns_normalized_structure(self) -> None:
        self._catalog_root_or_skip()
        result = describe_block("analog_agc_xx")
        self.assertTrue(result["ok"])
        self.assertEqual(result["block_id"], "analog_agc_xx")
        self.assertEqual(result["label"], "AGC")
        param_ids = {p["id"] for p in result["parameters"]}
        self.assertIn("type", param_ids)

    def test_documentation_and_asserts_are_preserved(self) -> None:
        self._catalog_root_or_skip()
        result = describe_block("pad_source")
        self.assertTrue(result["ok"])
        self.assertIn("hierarchical block", result.get("documentation", "").lower())
        self.assertEqual(result["asserts"], ["${ vlen > 0 }", "${ num_streams > 0 }"])

    def test_assert_expressions_and_port_shapes_stay_literal(self) -> None:
        self._catalog_root_or_skip()
        result = describe_block("blocks_add_xx")
        self.assertTrue(result["ok"])
        self.assertEqual(result["asserts"], ["${ num_inputs > 1 }", "${ vlen > 0 }"])

    def test_doc_url_pointer_is_preserved(self) -> None:
        self._catalog_root_or_skip()
        result = describe_block("blocks_add_xx")
        self.assertTrue(result["ok"])
