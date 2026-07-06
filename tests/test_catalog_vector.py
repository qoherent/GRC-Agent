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
        conn.execute("CREATE VIRTUAL TABLE catalog_idx USING vec0(embedding float[768])")
        for i, vec in enumerate(vectors, start=1):
            conn.execute("INSERT INTO catalog_chunks VALUES(?, ?, ?)", (i, f"b{i}", "{}"))
            conn.execute(
                "INSERT INTO catalog_idx(rowid, embedding) VALUES(?, ?)",
                (i, sqlite_vec.serialize_float32(vec)),
            )
        conn.commit()
    finally:
        conn.close()


def _write_test_db_with_fts(db_path: Path, blocks: list[tuple[str, str, list[float]]]) -> None:
    """Build a sqlite-vec DB WITH the FTS5 porter table populated.

    ``blocks`` is a list of (block_id, payload_text, vector).
    """
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
        conn.execute("CREATE VIRTUAL TABLE catalog_idx USING vec0(embedding float[768])")
        conn.execute("CREATE VIRTUAL TABLE catalog_fts USING fts5(content, tokenize='porter')")
        for i, (bid, payload, vec) in enumerate(blocks, start=1):
            conn.execute("INSERT INTO catalog_chunks VALUES(?, ?, ?)", (i, bid, payload))
            conn.execute(
                "INSERT INTO catalog_idx(rowid, embedding) VALUES(?, ?)",
                (i, sqlite_vec.serialize_float32(vec)),
            )
            conn.execute("INSERT INTO catalog_fts(rowid, content) VALUES(?, ?)", (i, payload))
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


class FuseRanksTests(unittest.TestCase):
    """Weighted Reciprocal Rank Fusion — the consultant-approved hybrid core.

    Vector rank is weighted 2x lexical so lexical acts as a boost-only signal
    and cannot demote a vector match with lexical-only noise.
    """

    def test_lexical_only_noise_cannot_beat_a_vector_hit(self) -> None:
        from grc_agent.runtime.catalog_vector import fuse_ranks

        # 'A' is vector #1; 'B' is a lexical-only hit (not in vector list).
        # With w_vec=2, A (2/61) must outrank B (1/61).
        result = fuse_ranks(["A"], ["B"], w_vec=2.0)
        self.assertEqual(result, ["A", "B"])

    def test_dual_hit_item_can_promote_above_vector_only_leader(self) -> None:
        from grc_agent.runtime.catalog_vector import fuse_ranks

        # vec: A=1,B=2,C=3 ; lex: C=1,B=2 ; w_vec=2, k=60
        #   A: 2/61            = 0.03279
        #   B: 2/62 + 1/62     = 0.04839
        #   C: 2/63 + 1/61     = 0.04814
        # Fused order: B, C, A
        result = fuse_ranks(["A", "B", "C"], ["C", "B"], w_vec=2.0)
        self.assertEqual(result, ["B", "C", "A"])

    def test_empty_lexical_returns_pure_vector_order(self) -> None:
        from grc_agent.runtime.catalog_vector import fuse_ranks

        self.assertEqual(fuse_ranks(["A", "B", "C"], [], w_vec=2.0), ["A", "B", "C"])


class FtsPorterStemmingTests(unittest.TestCase):
    """The Porter stemmer is what closes the multiplier<->multiply morphology gap."""

    def test_porter_matches_multiplier_to_multiply(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "catalog_v1.db"
            _write_test_db_with_fts(
                db,
                [
                    (
                        "blocks_multiply_xx",
                        "block_id: blocks_multiply_xx label: Multiply",
                        [0.1] * 768,
                    ),
                    ("blocks_add_xx", "block_id: blocks_add_xx label: Add", [0.2] * 768),
                ],
            )
            from grc_agent.runtime.catalog_vector import VectorCatalogStore

            store = VectorCatalogStore(db, "http://localhost:11434", "embeddinggemma:latest")
            conn = store._get_connection()
            try:
                from grc_agent.runtime.catalog_vector import fts_match

                rowids = fts_match(conn, "multiplier", limit=5)
                block_ids = {
                    conn.execute(
                        "SELECT block_id FROM catalog_chunks WHERE rowid=?", (r,)
                    ).fetchone()[0]
                    for r in rowids
                }
                self.assertIn("blocks_multiply_xx", block_ids)
            finally:
                conn.close()


class HybridSearchTests(unittest.TestCase):
    """VectorCatalogStore.search now fuses vector + FTS5 via weighted RRF."""

    def test_search_accepts_query_text_and_returns_fused_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "catalog_v1.db"
            _write_test_db_with_fts(
                db,
                [
                    (
                        "blocks_multiply_xx",
                        "block_id: blocks_multiply_xx Multiply",
                        [1.0] + [0.0] * 767,
                    ),
                    ("blocks_add_xx", "block_id: blocks_add_xx Add", [0.0, 1.0] + [0.0] * 766),
                ],
            )
            from grc_agent.runtime.catalog_vector import VectorCatalogStore

            store = VectorCatalogStore(db, "http://localhost:11434", "embeddinggemma:latest")
            # query vector identical to the multiply block → vector #1.
            # query text "multiplier" → lexical stem match on multiply.
            results = store.search("multiplier", [1.0] + [0.0] * 767, limit=5)
            self.assertTrue(results)
            for r in results:
                self.assertIn("block_id", r)
                self.assertIn("distance", r)
            # The multiply block must rank #1 (vector #1 AND lexical hit).
            self.assertEqual(results[0]["block_id"], "blocks_multiply_xx")

    def test_search_degrades_to_vector_when_fts_table_absent(self) -> None:
        # Old/minimal DBs (vec only, no FTS5 table) must not crash search.
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "catalog_v1.db"
            _write_test_db(db, [[1.0] + [0.0] * 767, [0.0, 1.0] + [0.0] * 766])
            from grc_agent.runtime.catalog_vector import VectorCatalogStore

            store = VectorCatalogStore(db, "http://localhost:11434", "embeddinggemma:latest")
            results = store.search("anything", [1.0] + [0.0] * 767, limit=5)
            self.assertTrue(results)
            # Pure vector: nearest neighbour is b1.
            self.assertEqual(results[0]["block_id"], "b1")


if __name__ == "__main__":
    unittest.main()
