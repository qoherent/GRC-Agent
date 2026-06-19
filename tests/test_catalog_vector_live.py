"""Regression tests for VectorCatalogStore using the real embeddinggemma model.

These tests require:
  * ``vec1.so`` at the repo root.
  * Ollama running at $GRC_AGENT_LLAMA_SERVER_URL (default http://localhost:11434).
  * The ``embeddinggemma:latest`` model pulled.

They are GATED behind ``GRC_AGENT_LIVE_EMBED=1`` so the deterministic unit
suite (no Ollama) still runs by default. Run with:

    GRC_AGENT_LIVE_EMBED=1 uv run python -m pytest tests/test_catalog_vector_live.py -v

These tests document the *real* ranking of queries against the catalog.
They will FAIL if the embed text becomes bloated with GUI-styling params
or other low-signal fields, because that pollutes the embedding.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path

import pytest


LIVE = os.environ.get("GRC_AGENT_LIVE_EMBED") == "1"
# conftest.py sets GRC_AGENT_VECTORS_DIR to a tmpdir at session start so unit
# tests don't touch the real DB. These live tests need the real DB instead.
REAL_VECTORS_DIR = Path(".grc_agent/vectors")


@unittest.skipUnless(LIVE, "set GRC_AGENT_LIVE_EMBED=1 to run live embed tests")
class VectorCatalogLiveTests(unittest.TestCase):
    """Deterministic expectations against the real embeddinggemma model."""

    @classmethod
    def setUpClass(cls) -> None:
        # Point at the real DB before importing the module so CATALOG_DB_PATH
        # captures the right path.
        os.environ["GRC_AGENT_VECTORS_DIR"] = str(REAL_VECTORS_DIR.resolve())
        from grc_agent.runtime.catalog_vector import (
            CATALOG_DB_PATH,
            VectorCatalogStore,
            embed_query,
        )

        # Copy the live DB so the test is read-only against production.
        cls.tmpdir = tempfile.mkdtemp(prefix="cat_live_")
        cls.test_db = Path(cls.tmpdir) / "catalog_v1.db"
        shutil.copy(str(CATALOG_DB_PATH), str(cls.test_db))
        cls.store = VectorCatalogStore(cls.test_db, "http://localhost:11434")
        cls.embed_query = embed_query

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def _top(self, query: str, k: int = 5) -> list[tuple[str, float]]:
        qv = type(self).embed_query("http://localhost:11434", query)
        results = self.store.search(qv, k)
        return [(r["block_id"], r["distance"]) for r in results]

    def test_time_sink_ranks_qtgui_time_sink_x_first(self) -> None:
        """'time sink' must rank the actual time-sink GUI block #1.

        Regression: bloated embed text (e.g. GUI-styling params) caused
        ``qtgui_time_sink_x`` to lose to ``digital_packet_sink``.
        """
        top = self._top("time sink", k=5)
        self.assertEqual(
            top[0][0], "qtgui_time_sink_x",
            f"time sink must rank qtgui_time_sink_x first, got {top}",
        )

    def test_time_domain_visualization_ranks_time_sinks(self) -> None:
        """A specific time-domain query should still find time sinks."""
        top = self._top("time domain visualization", k=5)
        ids = [bid for bid, _ in top]
        self.assertIn(
            "qtgui_time_sink_x", ids,
            f"time domain visualization should include time sink, got {top}",
        )

    def test_null_sink_ranks_blocks_null_sink_first(self) -> None:
        """Sanity: simple matches still work."""
        top = self._top("null sink", k=3)
        self.assertEqual(top[0][0], "blocks_null_sink", f"got {top}")

    def test_variable_holding_value_ranks_variable_first(self) -> None:
        """Sanity: variable should rank first for natural phrasing."""
        top = self._top("variable holding a value", k=3)
        self.assertEqual(top[0][0], "variable", f"got {top}")


if __name__ == "__main__":
    unittest.main()
