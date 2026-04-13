"""Quality-oriented retrieval tests for representative search queries."""

from pathlib import Path
import unittest

from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.retrieval import clear_catalog_index_cache, discover_catalog_root, search_grc
from grc_agent.retrieval.search import _bind_retrieval_context, _clear_retrieval_context


class RetrievalQualityTests(unittest.TestCase):
    """Check that the tuned retrieval shape stays block-centric and useful."""

    def _catalog_root_or_skip(self) -> Path:
        try:
            return discover_catalog_root()
        except Exception as exc:  # pragma: no cover - depends on host GNU install.
            self.skipTest(str(exc))

    def _fixture_path(self) -> Path:
        return Path(__file__).resolve().parents[1] / "data" / "random_bit_generator.grc"

    def setUp(self) -> None:
        clear_catalog_index_cache()
        _clear_retrieval_context()

    def test_bandpass_query_prefers_band_pass_filter_block(self) -> None:
        catalog_root = self._catalog_root_or_skip()
        _bind_retrieval_context(catalog_root=str(catalog_root))

        result = search_grc("bandpass filter", scope="catalog", k=5)

        self.assertTrue(result["ok"])
        self.assertEqual(result["results"][0]["node_type"], "block")
        self.assertEqual(result["results"][0]["label"], "Band Pass Filter")

    def test_qt_gui_time_sink_prefers_block_over_leaf_context(self) -> None:
        catalog_root = self._catalog_root_or_skip()
        _bind_retrieval_context(catalog_root=str(catalog_root))

        result = search_grc("qt gui time sink", scope="catalog", k=5)

        self.assertTrue(result["ok"])
        self.assertEqual(result["results"][0]["node_type"], "block")
        self.assertEqual(result["results"][0]["label"], "QT GUI Time Sink")

    def test_session_parameter_lookup_returns_parent_block_first(self) -> None:
        session = FlowgraphSession()
        session.load(self._fixture_path())
        _bind_retrieval_context(session=session)

        result = search_grc("samp_rate", scope="session", k=5)

        self.assertTrue(result["ok"])
        self.assertEqual(result["results"][0]["node_type"], "session_block")
        self.assertEqual(result["results"][0]["label"], "samp_rate")
        self.assertTrue(all(entry["node_type"] == "session_block" for entry in result["results"]))

    def test_catalog_results_stay_block_centric(self) -> None:
        catalog_root = self._catalog_root_or_skip()
        _bind_retrieval_context(catalog_root=str(catalog_root))

        result = search_grc("random source", scope="catalog", k=5)

        self.assertTrue(result["ok"])
        self.assertGreater(len(result["results"]), 0)
        allowed_node_types = {"block", "category", "domain"}
        self.assertTrue(all(entry["node_type"] in allowed_node_types for entry in result["results"]))
