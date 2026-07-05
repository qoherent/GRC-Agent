"""Regression tests for the per-backend vector store design.

Locks in:
  * per-backend DB paths (catalog_<backend>.db / docs_<backend>.db)
  * dimension is probed from the embedding, not hardcoded
  * the store stamps its embedding model and rebuilds on model change
  * no ``_EMBED_DIM`` literal remains anywhere in the runtime
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class PerBackendPathTests(unittest.TestCase):
    def test_catalog_and_docs_paths_are_backend_keyed(self) -> None:
        from grc_agent.runtime.catalog_vector import catalog_db_path
        from grc_agent.runtime.doc_answer import docs_db_path

        self.assertEqual(catalog_db_path("ollama").name, "catalog_ollama.db")
        self.assertEqual(catalog_db_path("openrouter").name, "catalog_openrouter.db")
        self.assertEqual(docs_db_path("ollama").name, "docs_ollama.db")
        self.assertEqual(docs_db_path("openrouter").name, "docs_openrouter.db")
        # The two backends resolve to DIFFERENT files (coexisting indexes).
        self.assertNotEqual(catalog_db_path("ollama"), catalog_db_path("openrouter"))
        self.assertNotEqual(docs_db_path("ollama"), docs_db_path("openrouter"))

    def test_legacy_constants_are_the_ollama_paths(self) -> None:
        from grc_agent.runtime.catalog_vector import CATALOG_DB_PATH, catalog_db_path
        from grc_agent.runtime.doc_answer import DB_PATH, docs_db_path

        self.assertEqual(CATALOG_DB_PATH, catalog_db_path("ollama"))
        self.assertEqual(DB_PATH, docs_db_path("ollama"))


class NoHardcodedDimensionTests(unittest.TestCase):
    def test_no_embed_dim_literal_in_runtime(self) -> None:
        """The hardcoded _EMBED_DIM=768 must stay deleted — dimension is probed."""
        import grc_agent.runtime._embedding_config as cfg

        self.assertFalse(hasattr(cfg, "_EMBED_DIM"))
        # Re-export from catalog_vector must also be gone.
        import grc_agent.runtime.catalog_vector as cv

        self.assertFalse(hasattr(cv, "_EMBED_DIM"))


class ModelStampRebuildTests(unittest.TestCase):
    """The store stamps its embedding model + dim and rebuilds on mismatch."""

    def _block(self, block_id: str = "b1") -> dict:
        return {
            "block_id": block_id,
            "label": block_id,
            "categories": ["Core"],
            "parameters": ["samp_rate"],
            "param_values": {"samp_rate": "1"},
            "ports": ["in0", "out0"],
            "documentation": "a test block",
        }

    def test_rebuild_when_embedding_model_changes(self) -> None:
        from grc_agent.runtime.catalog_vector import VectorCatalogStore

        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "catalog_test.db"
            # Mocked embedder returns a fixed 16-d vector for every call
            # (probe + per-block), so no live backend is needed.
            with mock.patch(
                "grc_agent.runtime.catalog_vector.get_embedding",
                return_value=[0.1] * 16,
            ):
                store_a = VectorCatalogStore(db, "http://x", "modelA")
                store_a.ingest_if_needed(blocks=[self._block()], server_url="http://x")

                # Stamp recorded as modelA / 16.
                conn = sqlite3.connect(str(db))
                row = conn.execute("SELECT model, dim FROM embed_meta").fetchone()
                conn.close()
                self.assertEqual(row, ("modelA", 16))

                # Re-open with a DIFFERENT embedding model → must rebuild.
                store_b = VectorCatalogStore(db, "http://x", "modelB")
                store_b.ingest_if_needed(blocks=[self._block()], server_url="http://x")

                conn = sqlite3.connect(str(db))
                row = conn.execute("SELECT model, dim FROM embed_meta").fetchone()
                self.assertEqual(row, ("modelB", 16))
                # Still exactly one block (rebuilt, not duplicated).
                n = conn.execute("SELECT count(*) FROM catalog_chunks").fetchone()[0]
                conn.close()
                self.assertEqual(n, 1)

    def test_same_model_is_idempotent_no_rebuild(self) -> None:
        from grc_agent.runtime.catalog_vector import VectorCatalogStore

        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "catalog_test.db"
            calls = 0

            def fake_embed(*_a, **_k):
                nonlocal calls
                calls += 1
                return [0.2] * 8

            with mock.patch(
                "grc_agent.runtime.catalog_vector.get_embedding", side_effect=fake_embed
            ):
                s1 = VectorCatalogStore(db, "http://x", "sameModel")
                s1.ingest_if_needed(blocks=[self._block()], server_url="http://x")
                first_calls = calls
                # Second ingest with the SAME model is a no-op (no re-embedding).
                s2 = VectorCatalogStore(db, "http://x", "sameModel")
                s2.ingest_if_needed(blocks=[self._block()], server_url="http://x")
                self.assertEqual(calls, first_calls)


if __name__ == "__main__":
    unittest.main()
