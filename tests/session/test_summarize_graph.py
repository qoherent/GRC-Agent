"""Tests for the phase 3 structured graph summary helper."""

from pathlib import Path
import unittest

from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.session import load_grc, summarize_graph


class SummarizeGraphTests(unittest.TestCase):
    """Check the bounded structured summary payload for one loaded graph."""

    def _fixture_path(self) -> Path:
        return Path(__file__).resolve().parents[1] / "data" / "random_bit_generator.grc"

    def test_summary_payload_is_structured_and_bounded(self) -> None:
        session = load_grc(self._fixture_path())

        payload = summarize_graph(session, max_blocks=3)

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["path"], str(self._fixture_path()))
        self.assertTrue(payload["graph_id"].startswith("grc:"))
        self.assertEqual(payload["block_count"], 5)
        self.assertEqual(payload["connection_count"], 3)
        self.assertEqual(
            payload["connections"],
            [
                {
                    "connection_id": "analog_random_source_x_0:0->blocks_throttle2_0:0",
                    "src_block": "analog_random_source_x_0",
                    "src_port": 0,
                    "dst_block": "blocks_throttle2_0",
                    "dst_port": 0,
                },
                {
                    "connection_id": "blocks_char_to_float_0:0->qtgui_time_sink_x_0:0",
                    "src_block": "blocks_char_to_float_0",
                    "src_port": 0,
                    "dst_block": "qtgui_time_sink_x_0",
                    "dst_port": 0,
                },
                {
                    "connection_id": "blocks_throttle2_0:0->blocks_char_to_float_0:0",
                    "src_block": "blocks_throttle2_0",
                    "src_port": 0,
                    "dst_block": "blocks_char_to_float_0",
                    "dst_port": 0,
                },
            ],
        )
        self.assertEqual(payload["variable_count"], 1)
        self.assertFalse(payload["dirty"])
        self.assertEqual(payload["validation"]["status"], "unknown")
        self.assertIn("random_bit_generator.grc", payload["summary"])
        self.assertIn("5 blocks", payload["summary"])
        self.assertIn("3 connections", payload["summary"])
        self.assertIn("samp_rate (variable)", payload["summary"])
        self.assertIn("... +2 more", payload["summary"])

    def test_summary_reflects_validation_state(self) -> None:
        session = load_grc(self._fixture_path())
        self.assertTrue(session.validate())

        payload = summarize_graph(session)

        self.assertEqual(payload["validation"]["status"], "valid")
        self.assertEqual(payload["validation"]["returncode"], 0)

    def test_unloaded_session_returns_snake_case_error(self) -> None:
        session = FlowgraphSession()

        payload = summarize_graph(session)

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error_type"], "invalid_request")
