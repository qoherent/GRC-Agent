"""Edge-case tests for get_grc_context."""

from pathlib import Path
import unittest

from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.session import get_grc_context


class GetGrcContextEdgeTests(unittest.TestCase):
    def _fixture_path(self) -> Path:
        return Path(__file__).resolve().parents[1] / "data" / "random_bit_generator.grc"

    def _load_session(self) -> FlowgraphSession:
        session = FlowgraphSession()
        session.load(self._fixture_path())
        return session

    def test_hops_zero_returns_target_only(self) -> None:
        session = self._load_session()

        result = get_grc_context(session, "blocks_throttle2_0", hops=0)

        self.assertTrue(result["ok"])
        node_ids = [n["node_id"] for n in result["nodes"]]
        self.assertIn("blocks_throttle2_0", node_ids)

    def test_hops_larger_than_diameter_includes_all_connected(self) -> None:
        session = self._load_session()

        result = get_grc_context(session, "blocks_throttle2_0", hops=3, max_nodes=20)

        self.assertTrue(result["ok"])
        node_ids = [n["node_id"] for n in result["nodes"]]
        self.assertIn("blocks_throttle2_0", node_ids)

    def test_source_node_context(self) -> None:
        session = self._load_session()

        result = get_grc_context(session, "analog_random_source_x_0", hops=1)

        self.assertTrue(result["ok"])
        node_ids = [n["node_id"] for n in result["nodes"]]
        self.assertIn("analog_random_source_x_0", node_ids)

    def test_sink_node_context(self) -> None:
        session = self._load_session()

        result = get_grc_context(session, "qtgui_time_sink_x_0", hops=1)

        self.assertTrue(result["ok"])
        node_ids = [n["node_id"] for n in result["nodes"]]
        self.assertIn("qtgui_time_sink_x_0", node_ids)

    def test_max_nodes_one_returns_limited(self) -> None:
        session = self._load_session()

        result = get_grc_context(session, "blocks_throttle2_0", hops=2, max_nodes=1)

        self.assertTrue(result["ok"])
        self.assertLessEqual(len(result["nodes"]), 1)

    def test_unloaded_session_returns_error(self) -> None:
        session = FlowgraphSession()

        result = get_grc_context(session, "blocks_throttle2_0")

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_type"], "invalid_context_request")


if __name__ == "__main__":
    unittest.main()
