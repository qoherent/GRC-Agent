"""Quality-oriented retrieval tests for representative search queries."""

from pathlib import Path
import unittest

from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.retrieval import (
    bind_retrieval_context,
    clear_catalog_index_cache,
    discover_catalog_root,
    search_grc,
)
from grc_agent.retrieval.search import _clear_retrieval_context


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
        bind_retrieval_context(catalog_root=str(catalog_root))

        result = search_grc("bandpass filter", scope="catalog", k=5)

        self.assertTrue(result["ok"])
        self.assertEqual(result["results"][0]["label"], "Band Pass Filter")
        self.assertEqual(result["results"][0]["block_id"], "band_pass_filter")

    def test_qt_gui_time_sink_prefers_block_over_leaf_context(self) -> None:
        catalog_root = self._catalog_root_or_skip()
        bind_retrieval_context(catalog_root=str(catalog_root))

        result = search_grc("qt gui time sink", scope="catalog", k=5)

        self.assertTrue(result["ok"])
        self.assertEqual(result["results"][0]["label"], "QT GUI Time Sink")
        self.assertEqual(result["results"][0]["block_id"], "qtgui_time_sink_x")

    def test_session_parameter_lookup_returns_parent_block_first(self) -> None:
        session = FlowgraphSession()
        session.load(self._fixture_path())
        bind_retrieval_context(session=session)

        result = search_grc("samp_rate", scope="session", k=5)

        self.assertTrue(result["ok"])
        self.assertEqual(result["results"][0]["label"], "samp_rate")
        self.assertTrue(all(entry["label"] for entry in result["results"]))
        self.assertTrue(all("summary" in entry for entry in result["results"]))

    def test_catalog_results_stay_block_centric(self) -> None:
        catalog_root = self._catalog_root_or_skip()
        bind_retrieval_context(catalog_root=str(catalog_root))

        result = search_grc("random source", scope="catalog", k=5)

        self.assertTrue(result["ok"])
        self.assertGreater(len(result["results"]), 0)
        self.assertTrue(all(entry["label"] for entry in result["results"]))
        self.assertTrue(all("summary" in entry for entry in result["results"]))
