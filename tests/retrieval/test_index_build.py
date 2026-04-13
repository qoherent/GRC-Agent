"""Tests for retrieval graph construction and readiness."""

from pathlib import Path
import tempfile
import unittest

from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.retrieval import (
    build_catalog_index,
    build_session_index,
    clear_catalog_index_cache,
    discover_catalog_root,
    initialize_retrieval,
)


class RetrievalIndexBuildTests(unittest.TestCase):
    """Check that catalog and session retrieval indexes build on real inputs."""

    def _catalog_root_or_skip(self) -> Path:
        try:
            return discover_catalog_root()
        except Exception as exc:  # pragma: no cover - depends on host GNU install.
            self.skipTest(str(exc))

    def _fixture_path(self) -> Path:
        return Path(__file__).resolve().parents[1] / "data" / "random_bit_generator.grc"

    def setUp(self) -> None:
        clear_catalog_index_cache()

    def test_initialize_retrieval_reports_ready_state(self) -> None:
        catalog_root = self._catalog_root_or_skip()

        payload = initialize_retrieval(catalog_root=catalog_root, warm_catalog=True)

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["catalog_root"], str(catalog_root))
        self.assertGreater(payload["catalog_files"]["block"], 0)
        self.assertGreater(payload["catalog_files"]["tree"], 0)
        self.assertGreater(payload["catalog_files"]["domain"], 0)
        self.assertTrue(payload["catalog_index_warmed"])
        self.assertGreater(payload["catalog_index"]["nodes"], 0)
        self.assertGreater(payload["catalog_index"]["edges"], 0)

    def test_initialize_retrieval_rejects_empty_catalog_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            payload = initialize_retrieval(catalog_root=tmpdir)

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error_type"], "RetrievalNotReady")
        self.assertIn("incomplete", payload["message"])

    def test_catalog_index_contains_real_gnu_block_metadata(self) -> None:
        catalog_root = self._catalog_root_or_skip()

        index = build_catalog_index(catalog_root)

        self.assertEqual(index.scope, "catalog")
        self.assertGreater(index.graph.number_of_nodes(), 0)
        self.assertGreater(index.graph.number_of_edges(), 0)
        self.assertIn("catalog:block:analog_agc_xx", index.node_records)
        record = index.node_records["catalog:block:analog_agc_xx"]
        self.assertEqual(record.node_type, "block")
        self.assertEqual(record.block_id, "analog_agc_xx")
        self.assertTrue(record.summary)
        self.assertTrue(record.field_summary)
        self.assertTrue(record.adjacency_summary)
        self.assertTrue(record.related_node_labels)
        self.assertIn("agc", index.token_index)

    def test_session_index_builds_from_loaded_flowgraph(self) -> None:
        session = FlowgraphSession()
        session.load(self._fixture_path())

        index = build_session_index(session)

        self.assertEqual(index.scope, "session")
        self.assertIn("session:block:samp_rate", index.node_records)
        self.assertNotIn(
            "session:connection:analog_random_source_x_0:0->blocks_throttle2_0:0",
            index.node_records,
        )
        block_record = index.node_records["session:block:samp_rate"]
        self.assertEqual(block_record.node_type, "session_block")
        self.assertEqual(block_record.label, "samp_rate")
        self.assertEqual(block_record.provenance.pointer, "blocks[samp_rate]")
        self.assertTrue(block_record.summary)
        self.assertIn("value", " ".join(block_record.search_fields["related"].split()))
