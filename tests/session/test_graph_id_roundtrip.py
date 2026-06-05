"""Round-trip graph-id stability checks."""

import tempfile
import unittest
from pathlib import Path

from grc_agent.flowgraph_session import FlowgraphSession


class GraphIdRoundTripTests(unittest.TestCase):
    """Verify that load/serialize/load preserves the graph identifier."""

    def _fixture_path(self) -> Path:
        return Path(__file__).resolve().parents[1] / "data" / "random_bit_generator.grc"

    def test_round_trip_serialization_preserves_graph_id(self) -> None:
        session = FlowgraphSession()
        session.load(self._fixture_path())
        original_graph_id = session.graph_id()
        serialized = FlowgraphSession._serialize_raw_data(session.flowgraph.raw_data)

        with tempfile.TemporaryDirectory() as tmpdir:
            roundtrip_path = Path(tmpdir) / "roundtrip.grc"
            roundtrip_path.write_text(serialized, encoding="utf-8")

            reloaded = FlowgraphSession()
            reloaded.load(roundtrip_path)

        self.assertEqual(reloaded.graph_id(), original_graph_id)
        self.assertEqual(
            FlowgraphSession._serialize_raw_data(reloaded.flowgraph.raw_data),
            serialized,
        )


if __name__ == "__main__":
    unittest.main()
