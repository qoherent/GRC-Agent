"""Tests for the phase 3 session load helper."""

from pathlib import Path
import unittest

from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.session import load_grc


class LoadGrcTests(unittest.TestCase):
    """Check that the read-only session package reuses FlowgraphSession cleanly."""

    def _fixture_path(self) -> Path:
        return Path(__file__).resolve().parents[1] / "data" / "random_bit_generator.grc"

    def test_load_grc_returns_loaded_session(self) -> None:
        fixture_path = self._fixture_path()

        session = load_grc(fixture_path)

        self.assertIsInstance(session, FlowgraphSession)
        self.assertEqual(session.path, fixture_path)
        self.assertIsNotNone(session.flowgraph)
        self.assertFalse(session.is_dirty)
        self.assertEqual(session.state_revision, 1)
