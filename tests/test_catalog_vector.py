"""Tests for the catalog vector store (vec1 + embeddinggemma)."""
from __future__ import annotations

import os
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from grc_agent.runtime.catalog_vector import (
    VectorCatalogStore,
    compose_block_embed_text,
    embed_block_text,
    is_catalog_db_populated,
    is_catalog_db_usable,
)


def _write_fake_vec1_db(db_path: Path, vectors: list[list[float]]) -> None:
    """Write a sqlite DB that loads vec1 and inserts `vectors` rows."""
    import sqlite3
    # We can't load vec1 in CI without the .so; skip if missing.
    if not (Path(__file__).resolve().parents[1] / "vec1.so").exists():
        raise unittest.SkipTest("vec1.so not available")
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.enable_load_extension(True)
    conn.load_extension(str((Path(__file__).resolve().parents[1] / "vec1.so").resolve()))
    conn.execute("CREATE TABLE catalog_chunks(rowid INTEGER PRIMARY KEY, block_id TEXT, payload TEXT)")
    conn.execute("CREATE VIRTUAL TABLE catalog_idx USING vec1(embedding)")
    for i, vec in enumerate(vectors, start=1):
        conn.execute("INSERT INTO catalog_chunks VALUES(?, ?, ?)", (i, f"b{i}", "{}"))
        conn.execute("INSERT INTO catalog_idx(rowid, embedding) VALUES(?, ?)",
                     (i, struct.pack(f"{len(vec)}f", *vec)))
    conn.execute("INSERT INTO catalog_idx(cmd, arg) VALUES('rebuild', '{\"index\": \"flat\", \"distance\": \"cos\"}')")
    conn.commit()
    conn.close()


class CatalogVectorEmbedTextTests(unittest.TestCase):
    def test_compose_block_embed_text_uniform_format(self) -> None:
        text = compose_block_embed_text(
            block_id="variable",
            label="Variable",
            categories=("Core", "Variables"),
            parameters=("value",),
            ports=(),
            documentation="This block maps a value to a unique variable.",
        )
        self.assertIn("task: search result | document:", text)
        self.assertIn("block_id: variable", text)
        self.assertIn("label: Variable", text)
        self.assertIn("category: Core/Variables", text)
        self.assertIn("param: value", text)
        self.assertIn("This block maps a value to a unique variable.", text)

    def test_embed_block_text_uses_doc_prefix_uniformly(self) -> None:
        # gemma-3-embedding spec: every document gets the same task prefix.
        with mock.patch("grc_agent.runtime.catalog_vector.get_embedding") as g:
            g.return_value = [0.0] * 768
            embed_block_text("http://x", "hello")
        args, _ = g.call_args
        self.assertTrue(args[1].startswith("task: search result | document: "))


class CatalogVectorReadinessTests(unittest.TestCase):
    def test_is_catalog_db_usable_false_on_missing(self) -> None:
        self.assertFalse(is_catalog_db_usable(Path("/nonexistent/catalog_v1.db")))

    def test_is_catalog_db_usable_true_on_populated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "catalog_v1.db"
            _write_fake_vec1_db(db, [[0.1] * 768, [0.2] * 768])
            self.assertTrue(is_catalog_db_usable(db))


if __name__ == "__main__":
    unittest.main()
