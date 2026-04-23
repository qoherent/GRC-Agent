"""Tests for the public `search_grc` retrieval API."""

import inspect
from pathlib import Path
from threading import Event, Thread
import tempfile
import unittest

from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.retrieval import (
    bind_retrieval_context,
    clear_catalog_index_cache,
    discover_catalog_root,
    search_grc,
)
from grc_agent.retrieval.search import _clear_retrieval_context


class SearchGrcTests(unittest.TestCase):
    """Exercise catalog and session search behavior on real inputs."""

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

    def _write_alt_fixture(self, directory: Path) -> Path:
        alt_path = directory / "random_bit_generator_alt.grc"
        alt_path.write_text(
            self._fixture_path()
            .read_text(encoding="utf-8")
            .replace("samp_rate", "fresh_clock_value"),
            encoding="utf-8",
        )
        return alt_path

    def setUp(self) -> None:
        clear_catalog_index_cache()
        _clear_retrieval_context()

    def test_public_search_signature_stays_narrow(self) -> None:
        signature = inspect.signature(search_grc)

        self.assertEqual(list(signature.parameters), ["query", "scope", "k"])

    def test_catalog_search_returns_expected_block(self) -> None:
        catalog_root = self._catalog_root_or_skip()
        bind_retrieval_context(catalog_root=str(catalog_root))

        result = search_grc("analog_agc_xx", scope="catalog", k=5)

        self.assertTrue(result["ok"])
        self.assertEqual(result["scope"], "catalog")
        self.assertEqual(result["query"], "analog_agc_xx")
        self.assertGreater(len(result["results"]), 0)
        top_result = result["results"][0]
        self.assertEqual(top_result["node_id"], "catalog:block:analog_agc_xx")
        self.assertEqual(top_result["block_id"], "analog_agc_xx")
        self.assertEqual(top_result["label"], "AGC")
        self.assertIn("summary", top_result)

    def test_session_search_returns_expected_loaded_block(self) -> None:
        session = self._load_session()
        bind_retrieval_context(session=session)

        result = search_grc("samp_rate", scope="session", k=5)

        self.assertTrue(result["ok"])
        self.assertEqual(result["scope"], "session")
        self.assertGreater(len(result["results"]), 0)
        top_result = result["results"][0]
        self.assertEqual(top_result["node_id"], "session:block:samp_rate")
        self.assertEqual(top_result["block_id"], "variable")
        self.assertEqual(top_result["label"], "samp_rate")
        self.assertIn("summary", top_result)

    def test_scope_selection_changes_the_result_set(self) -> None:
        catalog_root = self._catalog_root_or_skip()
        session = self._load_session()
        bind_retrieval_context(session=session, catalog_root=str(catalog_root))

        catalog_result = search_grc("qtgui_time_sink_x_0", scope="catalog", k=5)
        session_result = search_grc("qtgui_time_sink_x_0", scope="session", k=5)

        self.assertTrue(catalog_result["ok"])
        self.assertTrue(session_result["ok"])
        self.assertGreater(len(session_result["results"]), 0)
        self.assertNotIn(
            "session:block:qtgui_time_sink_x_0",
            [entry["node_id"] for entry in catalog_result["results"]],
        )
        self.assertIn(
            "session:block:qtgui_time_sink_x_0",
            [entry["node_id"] for entry in session_result["results"]],
        )

    def test_bound_session_context_is_isolated_per_thread(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            alt_path = self._write_alt_fixture(Path(tmpdir))
            session_a = self._load_session()
            session_b = FlowgraphSession()
            session_b.load(alt_path)

            first_bound = Event()
            second_bound = Event()
            results: dict[str, dict] = {}

            def worker_a() -> None:
                bind_retrieval_context(session=session_a)
                first_bound.set()
                second_bound.wait()
                results["a"] = search_grc("samp_rate", scope="session", k=5)

            def worker_b() -> None:
                first_bound.wait()
                bind_retrieval_context(session=session_b)
                second_bound.set()
                results["b"] = search_grc("fresh_clock_value", scope="session", k=5)

            thread_a = Thread(target=worker_a)
            thread_b = Thread(target=worker_b)
            thread_a.start()
            thread_b.start()
            thread_a.join()
            thread_b.join()

        self.assertTrue(results["a"]["ok"])
        self.assertTrue(results["b"]["ok"])
        self.assertEqual(results["a"]["results"][0]["node_id"], "session:block:samp_rate")
        self.assertEqual(
            results["b"]["results"][0]["node_id"],
            "session:block:fresh_clock_value",
        )

    def test_empty_query_fails_clearly(self) -> None:
        result = search_grc("   ", scope="catalog")

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_type"], "invalid_request")
        self.assertIn("non-empty string", result["message"])

    def test_unsupported_scope_fails_clearly(self) -> None:
        result = search_grc("analog", scope="hybrid")

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_type"], "invalid_request")
        self.assertEqual(result["details"]["supported_scopes"], ["catalog", "session"])
