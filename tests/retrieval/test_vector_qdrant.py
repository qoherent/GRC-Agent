"""Local Qdrant behavior required by vector retrieval."""

import json
from pathlib import Path
import tempfile
import unittest

from qdrant_client import QdrantClient, models

from grc_agent.retrieval.vector import (
    DEFAULT_VECTOR_COLLECTION_ALIAS,
    local_qdrant_alias_swap_smoke,
    prune_vector_collections,
)


class VectorQdrantLocalTests(unittest.TestCase):
    def test_local_qdrant_alias_bootstrap_and_swap(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            payload = local_qdrant_alias_swap_smoke(Path(tmpdir))

        self.assertTrue(payload["ok"], payload)
        self.assertEqual(payload["alias"], DEFAULT_VECTOR_COLLECTION_ALIAS)
        self.assertEqual(payload["first_title"], "first")
        self.assertEqual(payload["second_title"], "second")

    def test_prune_vector_collections_keeps_active_and_previous(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            index_dir = Path(tmpdir) / "qdrant"
            client = QdrantClient(path=str(index_dir))
            try:
                active = f"{DEFAULT_VECTOR_COLLECTION_ALIAS}_staging_active"
                previous = f"{DEFAULT_VECTOR_COLLECTION_ALIAS}_staging_previous"
                stale = f"{DEFAULT_VECTOR_COLLECTION_ALIAS}_staging_stale"
                unrelated = "other_collection"
                for name in (active, previous, stale, unrelated):
                    client.create_collection(
                        collection_name=name,
                        vectors_config=models.VectorParams(size=2, distance=models.Distance.COSINE),
                    )
                client.upsert(
                    collection_name=active,
                    points=[models.PointStruct(id=1, vector=[1.0, 0.0], payload={"title": "active"})],
                )
                client.update_collection_aliases(
                    change_aliases_operations=[
                        models.CreateAliasOperation(
                            create_alias=models.CreateAlias(
                                collection_name=active,
                                alias_name=DEFAULT_VECTOR_COLLECTION_ALIAS,
                            )
                        )
                    ]
                )
            finally:
                client.close()
            manifest = {
                "active_collection": active,
                "previous_collection": previous,
            }
            (index_dir.parent).mkdir(parents=True, exist_ok=True)
            (index_dir.parent / "manifest.json").write_text(
                json.dumps(manifest),
                encoding="utf-8",
            )

            dry_run = prune_vector_collections(index_dir=index_dir, dry_run=True)
            applied = prune_vector_collections(index_dir=index_dir, dry_run=False)

            client = QdrantClient(path=str(index_dir))
            try:
                remaining = {collection.name for collection in client.get_collections().collections}
            finally:
                client.close()

        self.assertTrue(dry_run["ok"], dry_run)
        self.assertEqual(
            dry_run["retention_policy"],
            "keep active alias target plus one previous retired collection",
        )
        self.assertEqual(dry_run["deleted_collections"], [])
        self.assertEqual(dry_run["would_delete_collections"], [stale])
        self.assertTrue(applied["ok"], applied)
        self.assertEqual(applied["deleted_collections"], [stale])
        self.assertIn(active, remaining)
        self.assertIn(previous, remaining)
        self.assertIn(unrelated, remaining)
        self.assertNotIn(stale, remaining)


if __name__ == "__main__":
    unittest.main()
