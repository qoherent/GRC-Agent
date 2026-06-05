"""Tests for the phase 3 bounded session-context helper."""

import unittest
from pathlib import Path

from grc_agent.session import get_grc_context, load_grc


class GetGrcContextTests(unittest.TestCase):
    """Check bounded neighborhood inspection against the canonical fixture."""

    def _fixture_path(self) -> Path:
        return Path(__file__).resolve().parents[1] / "data" / "random_bit_generator.grc"

    def test_context_returns_bounded_neighborhood(self) -> None:
        session = load_grc(self._fixture_path())

        payload = get_grc_context(session, "blocks_throttle2_0", hops=1, max_nodes=20)

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["node_id"], "blocks_throttle2_0")
        self.assertEqual(payload["target"]["block_type"], "blocks_throttle2")
        self.assertEqual(payload["target"]["incoming"], ["analog_random_source_x_0"])
        self.assertEqual(payload["target"]["outgoing"], ["blocks_char_to_float_0"])
        self.assertEqual([node["node_id"] for node in payload["nodes"]], [
            "blocks_throttle2_0",
            "analog_random_source_x_0",
            "blocks_char_to_float_0",
        ])
        self.assertEqual(
            payload["edges"],
            [
                {
                    "connection_id": "analog_random_source_x_0:0->blocks_throttle2_0:0",
                    "src_block": "analog_random_source_x_0",
                    "src_port": 0,
                    "dst_block": "blocks_throttle2_0",
                    "dst_port": 0,
                    "source": "analog_random_source_x_0",
                    "source_port": 0,
                    "target": "blocks_throttle2_0",
                    "target_port": 0,
                },
                {
                    "connection_id": "blocks_throttle2_0:0->blocks_char_to_float_0:0",
                    "src_block": "blocks_throttle2_0",
                    "src_port": 0,
                    "dst_block": "blocks_char_to_float_0",
                    "dst_port": 0,
                    "source": "blocks_throttle2_0",
                    "source_port": 0,
                    "target": "blocks_char_to_float_0",
                    "target_port": 0,
                },
            ],
        )
        self.assertEqual(payload["provenance"]["file_format"], 1)
        self.assertEqual(payload["provenance"]["grc_version"], "3.10.9.2")
        self.assertFalse(payload["truncated"])

    def test_context_marks_truncation_when_max_nodes_is_tight(self) -> None:
        session = load_grc(self._fixture_path())

        payload = get_grc_context(session, "blocks_throttle2_0", hops=2, max_nodes=2)

        self.assertTrue(payload["ok"])
        self.assertEqual(len(payload["nodes"]), 2)
        self.assertTrue(payload["truncated"])

    def test_unknown_node_returns_stable_error(self) -> None:
        session = load_grc(self._fixture_path())

        payload = get_grc_context(session, "does_not_exist")

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error_type"], "block_not_found")
        self.assertEqual(payload["details"]["node_id"], "does_not_exist")
