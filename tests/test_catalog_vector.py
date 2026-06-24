"""Tests for the catalog vector store (sqlite-vec + embeddinggemma)."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import sqlite_vec
from grc_agent.runtime.catalog_vector import (
    compose_block_embed_text,
    embed_block_text,
    is_catalog_db_usable,
)


def _write_test_db(db_path: Path, vectors: list[list[float]]) -> None:
    """Create a real sqlite-vec DB and insert ``vectors`` rows."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    try:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.execute(
            "CREATE TABLE catalog_chunks(rowid INTEGER PRIMARY KEY, block_id TEXT, payload TEXT)"
        )
        conn.execute(
            "CREATE VIRTUAL TABLE catalog_idx USING vec0(embedding float[768])"
        )
        for i, vec in enumerate(vectors, start=1):
            conn.execute("INSERT INTO catalog_chunks VALUES(?, ?, ?)", (i, f"b{i}", "{}"))
            conn.execute(
                "INSERT INTO catalog_idx(rowid, embedding) VALUES(?, ?)",
                (i, sqlite_vec.serialize_float32(vec)),
            )
        conn.commit()
    finally:
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
            _write_test_db(db, [[0.1] * 768, [0.2] * 768])
            self.assertTrue(is_catalog_db_usable(db))


if __name__ == "__main__":
    unittest.main()
