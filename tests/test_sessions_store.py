"""Core lifecycle coverage for SessionStore's background-writer thread.

This module had zero test coverage before this file — added as a safety
net before removing dead code from the writer's hot commit loop
(``_run()``), so a regression there is actually caught rather than only
noticed in the live GUI.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from grc_agent.sessions_store import SessionStore


class SessionStoreLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="grc_sessions_")
        self.db_path = Path(self._tmp.name) / "sessions.db"
        self.store = SessionStore(db_path=self.db_path)

    def tearDown(self) -> None:
        self.store.close()
        self._tmp.cleanup()

    def test_open_append_flush_roundtrip(self) -> None:
        session_id = self.store.open_session(
            graph_path="/tmp/dial_tone.grc",
            graph_hash="abc123",
            model_alias="gemma4:e4b-it-qat-120k",
            backend="ollama",
            title="test session",
        )
        self.store.append(session_id, "user", "hello")
        self.store.append(session_id, "assistant", "hi there")
        self.assertTrue(self.store.flush(timeout=5.0))

        record = self.store.get_session(session_id)
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record.graph_path, "/tmp/dial_tone.grc")
        # message_count is only finalized when a session ends (see
        # test_end_active_session_sets_ended_at) — not live-updated per
        # append, so it stays at its opening default here.
        self.assertIsNone(record.ended_at)

        messages = self.store.list_messages(session_id)
        self.assertEqual([m.text for m in messages], ["hello", "hi there"])
        self.assertEqual([m.sequence for m in messages], [0, 1])

    def test_end_active_session_sets_ended_at(self) -> None:
        session_id = self.store.open_session(graph_path="/tmp/x.grc", graph_hash="h")
        self.store.append(session_id, "user", "one message")
        self.store.end_active_session(session_id)
        self.assertTrue(self.store.flush(timeout=5.0))

        record = self.store.get_session(session_id)
        self.assertIsNotNone(record)
        assert record is not None
        self.assertIsNotNone(record.ended_at)
        self.assertEqual(record.message_count, 1)

    def test_end_active_session_noop_on_none(self) -> None:
        # Must not raise or block — the GUI calls this unconditionally on
        # app close even when no session was ever opened.
        self.store.end_active_session(None)

    def test_list_sessions_returns_newest_first(self) -> None:
        first = self.store.open_session(
            graph_path="/tmp/a.grc", graph_hash="h1", started_at="2026-01-01T00:00:00.000Z"
        )
        second = self.store.open_session(
            graph_path="/tmp/b.grc", graph_hash="h2", started_at="2026-01-02T00:00:00.000Z"
        )
        self.assertTrue(self.store.flush(timeout=5.0))

        sessions = self.store.list_sessions(limit=10)
        ids = [s.id for s in sessions]
        self.assertIn(first, ids)
        self.assertIn(second, ids)
        self.assertLess(ids.index(second), ids.index(first))

    def test_replace_active_session_closes_old_and_opens_new(self) -> None:
        old_id = self.store.open_session(graph_path="/tmp/old.grc", graph_hash="h1")
        self.store.append(old_id, "user", "in old session")
        new_id = self.store.replace_active_session(
            old_id,
            graph_path="/tmp/new.grc",
            graph_hash="h2",
        )
        self.assertTrue(self.store.flush(timeout=5.0))

        old_record = self.store.get_session(old_id)
        new_record = self.store.get_session(new_id)
        self.assertIsNotNone(old_record)
        assert old_record is not None
        self.assertIsNotNone(old_record.ended_at)
        self.assertIsNotNone(new_record)
        assert new_record is not None
        self.assertEqual(new_record.graph_path, "/tmp/new.grc")
        self.assertIsNone(new_record.ended_at)

    def test_clear_all_removes_everything(self) -> None:
        session_id = self.store.open_session(graph_path="/tmp/x.grc", graph_hash="h")
        self.store.append(session_id, "user", "hello")
        self.assertTrue(self.store.flush(timeout=5.0))

        count = self.store.clear_all()
        self.assertGreaterEqual(count, 1)
        self.assertEqual(self.store.list_sessions(), [])

    def test_close_is_idempotent(self) -> None:
        self.store.close()
        self.store.close()  # must not raise


if __name__ == "__main__":
    unittest.main()
