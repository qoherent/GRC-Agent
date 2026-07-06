"""Regression tests for VectorCatalogStore using the real embeddinggemma model.

These tests require:
  * the ``sqlite-vec`` package (pip-installed).
  * Ollama running at $GRC_AGENT_LLAMA_SERVER_URL (default http://localhost:11434).
  * The ``embeddinggemma:latest`` model pulled.
  * A populated catalog DB at ``src/grc_agent/vectors/catalog_ollama.db``.

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

LIVE = os.environ.get("GRC_AGENT_LIVE_EMBED") == "1"
# conftest.py sets GRC_AGENT_VECTORS_DIR to a tmpdir at session start so unit
# tests don't touch the real DB. We must point at the real DB BEFORE any
# import of grc_agent.runtime.catalog_vector, because CATALOG_DB_PATH is
# captured at module load. Tests in this module run only when LIVE is set,
# so the env override is safe.
_REAL_VECTORS_DIR = Path("src/grc_agent/vectors").resolve()
if LIVE and Path(_REAL_VECTORS_DIR / "catalog_ollama.db").exists():
    os.environ["GRC_AGENT_VECTORS_DIR"] = str(_REAL_VECTORS_DIR)


@unittest.skipUnless(LIVE, "set GRC_AGENT_LIVE_EMBED=1 to run live embed tests")
class VectorCatalogLiveTests(unittest.TestCase):
    """Deterministic expectations against the real embeddinggemma model."""

    @classmethod
    def setUpClass(cls) -> None:
        from grc_agent.runtime.catalog_vector import (
            CATALOG_DB_PATH,
            VectorCatalogStore,
            embed_query,
        )

        if not Path(CATALOG_DB_PATH).exists():
            raise unittest.SkipTest(
                f"catalog DB not found at {CATALOG_DB_PATH}; "
                "run grc_agent.retrieval.warmup_catalog_vector_index first"
            )

        # Copy the live DB so the test is read-only against production.
        cls.tmpdir = tempfile.mkdtemp(prefix="cat_live_")
        cls.test_db = Path(cls.tmpdir) / "catalog_ollama.db"
        shutil.copy(str(CATALOG_DB_PATH), str(cls.test_db))
        cls.store = VectorCatalogStore(
            cls.test_db, "http://localhost:11434", "embeddinggemma:latest"
        )
        cls.embed_query = embed_query

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def _top(self, query: str, k: int = 5) -> list[tuple[str, float]]:
        qv = type(self).embed_query("http://localhost:11434", query)
        results = self.store.search(query, qv, k)
        return [(r["block_id"], r["distance"]) for r in results]

    def test_time_sink_ranks_qtgui_time_sink_x_first(self) -> None:
        """'time sink' must rank the actual time-sink GUI block #1.

        Regression: bloated embed text (e.g. GUI-styling params) caused
        ``qtgui_time_sink_x`` to lose to ``digital_packet_sink``.
        """
        top = self._top("time sink", k=5)
        self.assertEqual(
            top[0][0],
            "qtgui_time_sink_x",
            f"time sink must rank qtgui_time_sink_x first, got {top}",
        )

    def test_time_domain_visualization_ranks_time_sinks(self) -> None:
        """A specific time-domain query should still find time sinks."""
        top = self._top("time domain visualization", k=5)
        ids = [bid for bid, _ in top]
        self.assertIn(
            "qtgui_time_sink_x",
            ids,
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

    def test_search_blocks_output_to_results(self) -> None:
        """Run search_blocks tool function for multiple queries and save outputs to .md files."""
        import json
        import re
        from unittest import mock

        from grc_agent.agent import GrcAgent
        from grc_agent.config import load_app_config
        from grc_agent.runtime.search_blocks import search_blocks

        config = load_app_config()
        agent = GrcAgent(config=config.agent)
        agent._llama_server_url = "http://localhost:11434"

        queries = [
            "float stream to complex stream",
            "low pass filter",
            "frequency sink",
            "variable slider",
            "signal source",
            "throttle",
            "add constant",
            "rational resampler",
            "complex conjugate",
            "null sink",
        ]

        out_dir = Path("tests/output/search_queries")
        out_dir.mkdir(parents=True, exist_ok=True)

        for q in queries:
            with mock.patch(
                "grc_agent.runtime.search_blocks.CATALOG_DB_PATH", str(type(self).test_db)
            ):
                result = search_blocks(agent, q)

            self.assertTrue(result.get("ok"))

            # Slugify query name for filename
            slug = re.sub(r"[^a-z0-9]+", "_", q.lower()).strip("_")
            out_file = out_dir / f"{slug}.md"

            md_content = f"# Search Query: {q}\n\n```json\n{json.dumps(result, indent=2)}\n```\n"
            out_file.write_text(md_content, encoding="utf-8")
            self.assertTrue(out_file.exists())


if __name__ == "__main__":
    unittest.main()
