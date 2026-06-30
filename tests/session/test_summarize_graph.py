"""Tests for the phase 3 structured graph summary helper."""

import unittest
from pathlib import Path

from grc_agent.domain_models import ErrorCode
from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.session import load_grc, summarize_graph


class SummarizeGraphTests(unittest.TestCase):
    """Check the bounded structured summary payload for one loaded graph."""

    def _fixture_path(self) -> Path:
        return Path(__file__).resolve().parents[1] / "data" / "dial_tone.grc"

    def test_summary_payload_is_structured_and_bounded(self) -> None:
        session = load_grc(self._fixture_path())
        fg = session.flowgraph
        options_block = getattr(fg, "options_block", None)
        options_name = options_block.name if options_block is not None else None
        expected_blocks = sum(1 for b in fg.blocks if b.name != options_name)
        expected_conns = len(fg.connections)

        payload = summarize_graph(session, max_blocks=3)

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["path"], str(self._fixture_path()))
        self.assertTrue(payload["graph_id"].startswith("grc:"))
        self.assertEqual(payload["block_count"], expected_blocks)
        self.assertEqual(payload["connection_count"], expected_conns)
        self.assertEqual(len(payload["connections"]), expected_conns)
        self.assertEqual(payload["connections"], sorted(payload["connections"]))
        self.assertFalse(payload["dirty"])
        self.assertEqual(payload["validation"]["status"], "unknown")
        self.assertIn(self._fixture_path().name, payload["summary"])
        self.assertIn(f"{expected_blocks} blocks", payload["summary"])
        self.assertIn(f"{expected_conns} connections", payload["summary"])
        if expected_blocks > 3:
            self.assertIn(f"... +{expected_blocks - 3} more", payload["summary"])

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
        self.assertEqual(payload["error_type"], ErrorCode.INVALID_REQUEST)
