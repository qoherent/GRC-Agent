"""Tests for the public read-only semantic search tool contract."""

from pathlib import Path
import fcntl
import json
import tempfile
import unittest
from unittest import mock

from grc_agent.agent import GrcAgent
from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.retrieval.vector import semantic_search_grc
from grc_agent.retrieval.vector import vector_index_stats
from grc_agent.runtime.tool_surface import MVP_MODEL_TOOL_NAMES


class SemanticSearchToolTests(unittest.TestCase):
    def _fixture_path(self) -> Path:
        return Path(__file__).resolve().parents[1] / "data" / "random_bit_generator.grc"

    def test_missing_index_returns_structured_error_without_autobuild(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            payload = semantic_search_grc("audio smoother", index_dir=Path(tmpdir) / "missing")

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error_type"], "missing_index")
        self.assertIn("grc-agent vector build", payload["message"])

    def test_stale_index_schema_fails_closed_before_query(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            index_dir = root / "qdrant"
            (root / "manifest.json").write_text(
                json.dumps(
                    {
                        "ok": True,
                        "active_collection": "old_collection",
                        "index_schema_version": "old-schema",
                    }
                ),
                encoding="utf-8",
            )

            payload = semantic_search_grc("audio smoother", index_dir=index_dir)
            stats = vector_index_stats(index_dir=index_dir)

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error_type"], "stale_index")
        self.assertFalse(stats["ok"])
        self.assertEqual(stats["error_type"], "stale_index")

    def test_stats_uses_exclusive_local_qdrant_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            index_dir = Path(tmpdir) / "qdrant"
            lock_path = index_dir.parent / "index.lock"
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            with lock_path.open("a+", encoding="utf-8") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
                payload = vector_index_stats(index_dir=index_dir)
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error_type"], "index_busy")

    def test_agent_semantic_search_tool_is_read_only(self) -> None:
        session = FlowgraphSession()
        session.load(self._fixture_path())
        before_revision = session.state_revision
        before_dirty = session.is_dirty
        agent = GrcAgent(session)

        with mock.patch(
            "grc_agent.agent.semantic_search_grc",
            return_value={
                "ok": False,
                "error_type": "missing_index",
                "message": "Vector index missing. Run `grc-agent vector build`.",
            },
        ):
            result = agent.execute_tool(
                "semantic_search_grc",
                {"query": "audio smoother", "scope": "catalog", "k": 5},
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_type"], "missing_index")
        self.assertEqual(session.state_revision, before_revision)
        self.assertEqual(session.is_dirty, before_dirty)

    def test_semantic_search_stays_internal_to_model_surface(self) -> None:
        session = FlowgraphSession()
        session.load(self._fixture_path())
        agent = GrcAgent(session)
        tool_names = [schema["function"]["name"] for schema in agent.get_tool_schemas_for_turn()]

        self.assertEqual(tool_names, list(MVP_MODEL_TOOL_NAMES))
        self.assertNotIn("semantic_search_grc", tool_names)


if __name__ == "__main__":
    unittest.main()
