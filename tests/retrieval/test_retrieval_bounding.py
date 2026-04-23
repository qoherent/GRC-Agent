"""Tests for retrieval output bounding and determinism."""

from pathlib import Path
import unittest
from unittest import mock

import grc_agent.retrieval.search as retrieval_search
from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.retrieval import (
    MAX_RESULT_LIMIT,
    bind_retrieval_context,
    clear_catalog_index_cache,
    discover_catalog_root,
    search_grc,
)
from grc_agent.retrieval.search import _clear_retrieval_context


class RetrievalBoundingTests(unittest.TestCase):
    """Ensure retrieval stays bounded, compact, and deterministic."""

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
        bind_retrieval_context(catalog_root=str(catalog_root))

        result = search_grc("analog", scope="catalog", k=3)

        self.assertTrue(result["ok"])
        self.assertLessEqual(len(result["results"]), 3)

    def test_large_k_is_capped_and_warned(self) -> None:
        catalog_root = self._catalog_root_or_skip()
        bind_retrieval_context(catalog_root=str(catalog_root))

        result = search_grc("analog", scope="catalog", k=999)

        self.assertTrue(result["ok"])
        self.assertLessEqual(len(result["results"]), MAX_RESULT_LIMIT)
        self.assertIn(f"k capped at {MAX_RESULT_LIMIT}.", result["warnings"])

    def test_non_matching_query_returns_stable_empty_shape(self) -> None:
        catalog_root = self._catalog_root_or_skip()
        bind_retrieval_context(catalog_root=str(catalog_root))

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
        bind_retrieval_context(session=session)

        first = search_grc("qtgui", scope="session", k=5)
        second = search_grc("qtgui", scope="session", k=5)

        self.assertEqual(first, second)

    def test_session_index_is_reused_until_session_revision_changes(self) -> None:
        session = self._load_session()
        bind_retrieval_context(session=session)

        with mock.patch(
            "grc_agent.retrieval.search.build_session_index",
            wraps=retrieval_search.build_session_index,
        ) as build_session_index:
            first = search_grc("qtgui", scope="session", k=5)
            second = search_grc("qtgui", scope="session", k=5)
            session.set_param("samp_rate", "value", "48000")
            third = search_grc("qtgui", scope="session", k=5)

        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        self.assertTrue(third["ok"])
        self.assertEqual(build_session_index.call_count, 2)

    def test_results_stay_compact(self) -> None:
        catalog_root = self._catalog_root_or_skip()
        bind_retrieval_context(catalog_root=str(catalog_root))

        result = search_grc("analog", scope="catalog", k=5)

        self.assertTrue(result["ok"])
        self.assertGreater(len(result["results"]), 0)
        for entry in result["results"]:
            self.assertIn("node_id", entry)
            self.assertIn("label", entry)
            self.assertIn("summary", entry)
            self.assertLessEqual(len(entry["summary"]), 160)
            self.assertNotIn("provenance", entry)
            self.assertNotIn("score", entry)
            self.assertNotIn("source_scope", entry)
            self.assertNotIn("reason", entry)
            self.assertNotIn("field_summary", entry)
            self.assertNotIn("block_description", entry)
            self.assertNotIn("adjacency_summary", entry)
            self.assertNotIn("related_node_labels", entry)
