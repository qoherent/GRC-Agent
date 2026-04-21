"""Tests for the public `describe_block` catalog API."""

import inspect
from pathlib import Path
import unittest

from grc_agent import describe_block
from grc_agent.catalog.loaders import clear_catalog_snapshot_cache, discover_catalog_root


class DescribeBlockTests(unittest.TestCase):
    """Exercise normalized block description behavior on real GNU metadata."""

    def _catalog_root_or_skip(self) -> Path:
        try:
            return discover_catalog_root()
        except Exception as exc:  # pragma: no cover - depends on host GNU install.
            self.skipTest(str(exc))

    def setUp(self) -> None:
        clear_catalog_snapshot_cache()

    def test_public_describe_signature_stays_narrow(self) -> None:
        signature = inspect.signature(describe_block)

        self.assertEqual(list(signature.parameters), ["block_id"])

    def test_known_block_returns_normalized_structure(self) -> None:
        self._catalog_root_or_skip()

        result = describe_block("analog_agc_xx")

        self.assertTrue(result["ok"])
        self.assertEqual(result["block_id"], "analog_agc_xx")
        self.assertEqual(result["label"], "AGC")
        self.assertTrue(result["loaded_from"].endswith("analog_agc_xx.block.yml"))
        self.assertTrue(result["category_path"])
        self.assertEqual(result["category_path"][0], "Core")
        self.assertEqual(result["flags"], ["python", "cpp"])
        self.assertEqual(result["parameters"][0]["id"], "type")
        self.assertEqual(result["parameters"][0]["dtype"], "enum")
        self.assertEqual(result["inputs"][0]["domain"], "stream")
        self.assertEqual(result["outputs"][0]["domain"], "stream")
        self.assertEqual(result["asserts"], [])
        self.assertIn("analog_agc_xx(", result["signature"])

    def test_documentation_and_asserts_are_preserved(self) -> None:
        self._catalog_root_or_skip()

        result = describe_block("pad_source")

        self.assertTrue(result["ok"])
        self.assertIn("hierarchical block", result["documentation"].lower())
        self.assertEqual(result["asserts"], ["${ vlen > 0 }", "${ num_streams > 0 }"])
        self.assertEqual(result["outputs"][0]["multiplicity"], "${ num_streams }")

    def test_assert_expressions_and_port_shapes_stay_literal(self) -> None:
        self._catalog_root_or_skip()

        result = describe_block("blocks_add_xx")

        self.assertTrue(result["ok"])
        self.assertEqual(result["asserts"], ["${ num_inputs > 1 }", "${ vlen > 0 }"])
        self.assertEqual(result["inputs"][0]["dtype"], "${ type }")
        self.assertEqual(result["inputs"][0]["multiplicity"], "${ num_inputs }")

    def test_doc_url_pointer_is_preserved(self) -> None:
        self._catalog_root_or_skip()

        result = describe_block("uhd_fpga_fft")

        self.assertTrue(result["ok"])
        self.assertEqual(result["doc_url"], "UHD_FPGA_FFT")
        self.assertIsNone(result["documentation"])
        self.assertEqual(result["inputs"][0]["id"], "port0")
        self.assertEqual(result["outputs"][0]["id"], "port0")

    def test_hierarchical_wrapper_emits_warning(self) -> None:
        self._catalog_root_or_skip()

        result = describe_block("pfb_channelizer_hier_ccf")

        self.assertTrue(result["ok"])
        self.assertTrue(result["warnings"])
        self.assertTrue(
            any("Hierarchical" in warning for warning in result["warnings"]),
            msg=result["warnings"],
        )
        self.assertTrue(result["loaded_from"].endswith("filter_pfb_channelizer_hier.block.yml"))

    def test_unknown_block_returns_stable_error_shape(self) -> None:
        self._catalog_root_or_skip()

        result = describe_block("definitely_not_a_real_block")

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_type"], "block_not_found")
        self.assertEqual(result["details"]["block_id"], "definitely_not_a_real_block")
