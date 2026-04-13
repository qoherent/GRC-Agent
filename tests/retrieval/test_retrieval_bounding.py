"""Tests for retrieval output bounding and determinism."""

from pathlib import Path
import unittest

from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.retrieval import MAX_RESULT_LIMIT, clear_catalog_index_cache, discover_catalog_root, search_grc
from grc_agent.retrieval.search import _bind_retrieval_context, _clear_retrieval_context


class RetrievalBoundingTests(unittest.TestCase):
    """Ensure retrieval stays bounded, provenance-aware, and deterministic."""

    def _catalog_root_or_skip(self) -> Path:
        try:
            return discover_catalog_root()
        except Exception as exc:  # pragma: no cover - depends on host GNU install.
            self.skipTest(str(exc))

    def _fixture_path(self) -> Path:
        return Path(__file__).resolve().parents[1] / "data" / "random_bit_generator.grc"

    def _load_session(self) -> FlowgraphSession:
        session = FlowgraphSession()
        session.load(self._fixture_path())
        return session

    def setUp(self) -> None:
        clear_catalog_index_cache()
        _clear_retrieval_context()

    def test_results_respect_requested_k(self) -> None:
        catalog_root = self._catalog_root_or_skip()
        _bind_retrieval_context(catalog_root=str(catalog_root))

        result = search_grc("analog", scope="catalog", k=3)

        self.assertTrue(result["ok"])
        self.assertLessEqual(len(result["results"]), 3)

    def test_large_k_is_capped_and_warned(self) -> None:
        catalog_root = self._catalog_root_or_skip()
        _bind_retrieval_context(catalog_root=str(catalog_root))

        result = search_grc("analog", scope="catalog", k=999)

        self.assertTrue(result["ok"])
        self.assertLessEqual(len(result["results"]), MAX_RESULT_LIMIT)
        self.assertIn(f"k capped at {MAX_RESULT_LIMIT}.", result["warnings"])

    def test_non_matching_query_returns_stable_empty_shape(self) -> None:
        catalog_root = self._catalog_root_or_skip()
        _bind_retrieval_context(catalog_root=str(catalog_root))

        result = search_grc(
            "zzzxqv987654321nomatch",
            scope="catalog",
            k=5,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["results"], [])
        self.assertEqual(
            result["warnings"],
            ["No catalog matches found for 'zzzxqv987654321nomatch'."],
        )

    def test_repeated_session_queries_are_deterministic(self) -> None:
        session = self._load_session()
        _bind_retrieval_context(session=session)

        first = search_grc("qtgui", scope="session", k=5)
        second = search_grc("qtgui", scope="session", k=5)

        self.assertEqual(first, second)

    def test_results_include_provenance_and_stay_compact(self) -> None:
        catalog_root = self._catalog_root_or_skip()
        _bind_retrieval_context(catalog_root=str(catalog_root))

        result = search_grc("analog", scope="catalog", k=5)

        self.assertTrue(result["ok"])
        self.assertGreater(len(result["results"]), 0)
        for entry in result["results"]:
            self.assertIn("provenance", entry)
            self.assertIn("path", entry["provenance"])
            self.assertIn("pointer", entry["provenance"])
            self.assertIn("score", entry)
            self.assertIn("source_scope", entry)
            self.assertIn("summary", entry)
            self.assertLessEqual(len(entry["summary"]), 160)
            self.assertNotIn("field_summary", entry)
            self.assertNotIn("block_description", entry)
            self.assertNotIn("adjacency_summary", entry)
            self.assertNotIn("related_node_labels", entry)
