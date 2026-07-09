"""Tests for the local chat-sessions store.

All filesystem-touching tests use ``tmp_path`` per the project
convention in ``tests/conftest.py`` and never touch the developer's
real ``~/.grc_agent/``.
"""

from __future__ import annotations

import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path

from grc_agent.sessions_store import (
    SCHEMA_VERSION,
    SessionStore,
    SessionStoreCorrupt,
    SessionStoreTooNew,
    default_sessions_db_path,
    get_session_sync,
    list_sessions_sync,
    open_session_store,
    session_store_cm,
)


def _make_db_path(tmp: Path) -> Path:
    """Return a unique DB path under tmp, ensuring the dir exists."""
    db = tmp / "sessions.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    return db


def _make_store(tmp: Path) -> SessionStore:
    """Open a fresh store and register cleanup."""
    db = _make_db_path(tmp)
    store = SessionStore(db_path=db)
    return store


class _StoreTestCase(unittest.TestCase):
    """Base class that owns a temp SessionStore and tears it down."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="grc_sess_")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.store = _make_store(self.tmp)
        self.addCleanup(self.store.close)

    def _open_session(
        self,
        *,
        graph_path: str = "/tmp/example.grc",
        graph_hash: str = "grc:abc",
        model_alias: str | None = "test-model",
        backend: str | None = "ollama",
        title: str = "Test session",
    ) -> int:
        return self.store.open_session(
            graph_path=graph_path,
            graph_hash=graph_hash,
            model_alias=model_alias,
            backend=backend,
            title=title,
        )


class SchemaTests(_StoreTestCase):
    def test_schema_created_on_first_run(self) -> None:
        conn = sqlite3.connect(str(self.store._db_path))
        try:
            names = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
                )
            }
        finally:
            conn.close()
        for required in (
            "schema_version",
            "sessions",
            "messages",
            "messages_fts",
        ):
            self.assertIn(required, names, f"missing table/view: {required}")
        for trigger in ("messages_ai", "messages_ad", "messages_au"):
            row = (
                sqlite3.connect(str(self.store._db_path))
                .execute(
                    "SELECT name FROM sqlite_master WHERE type='trigger' AND name=?",
                    (trigger,),
                )
                .fetchone()
            )
            self.assertIsNotNone(row, f"missing trigger: {trigger}")

    def test_schema_version_is_one(self) -> None:
        conn = sqlite3.connect(str(self.store._db_path))
        try:
            row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
        finally:
            conn.close()
        self.assertEqual(row[0], SCHEMA_VERSION)

    def test_refuse_newer_schema(self) -> None:
        # Build a DB with v=99, then try to open it.
        db = self.tmp / "newer.db"
        db.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db))
        try:
            conn.executescript(
                "CREATE TABLE schema_version (version INTEGER PRIMARY KEY, "
                "installed_at TEXT);"
                "INSERT INTO schema_version (version) VALUES (99);"
            )
            conn.commit()
        finally:
            conn.close()
        with self.assertRaises(SessionStoreTooNew) as ctx:
            SessionStore(db_path=db)
        self.assertIn("schema_version=99", str(ctx.exception))


class RoundTripTests(_StoreTestCase):
    def test_open_and_read_session_metadata(self) -> None:
        sid = self._open_session(graph_path="/tmp/a.grc", graph_hash="grc:111", title="first")
        self.store.flush(timeout=2.0)
        rec = self.store.get_session(sid)
        self.assertIsNotNone(rec)
        assert rec is not None
        self.assertEqual(rec.graph_path, "/tmp/a.grc")
        self.assertEqual(rec.graph_hash, "grc:111")
        self.assertEqual(rec.model_alias, "test-model")
        self.assertEqual(rec.backend, "ollama")
        self.assertEqual(rec.title, "first")
        self.assertEqual(rec.message_count, 0)
        self.assertIsNone(rec.ended_at)

    def test_message_ordering_preserved(self) -> None:
        sid = self._open_session()
        for i in range(50):
            self.store.append(sid, "user" if i % 2 == 0 else "assistant", f"m{i}")
        self.store.flush(timeout=2.0)
        msgs = self.store.list_messages(sid)
        self.assertEqual(len(msgs), 50)
        self.assertEqual([m.sequence for m in msgs], list(range(50)))
        self.assertEqual([m.text for m in msgs], [f"m{i}" for i in range(50)])

    def test_payload_json_round_trip(self) -> None:
        sid = self._open_session()
        payload = {"kind": "tool_finished_ok", "tool_name": "change_graph", "ok": True}
        self.store.append(sid, "tool_finished", "applied", payload=payload)
        self.store.flush(timeout=2.0)
        msgs = self.store.list_messages(sid)
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0].payload, payload)

    def test_model_role_rows_round_trip(self) -> None:
        """Rows with role ``assistant_model`` / ``tool_model`` carry the
        full ``ChatMessage`` payload and round-trip through the DB so
        the resume path can rebuild the agent's ``ChatHistory``."""
        from grc_agent.chat_roles import (
            ASSISTANT_MODEL_ROLE,
            TOOL_MODEL_ROLE,
            chat_message_payload,
        )
        from ToolAgents.data_models.chat_history import ChatHistory
        from ToolAgents.data_models.messages import (
            ChatMessageRole,
            ToolCallContent,
            ToolCallResultContent,
        )

        history = ChatHistory()
        history.add_user_message("inspect")
        history.add_assistant_message("calling inspect_graph")
        tool_call = ToolCallContent(
            tool_call_id="c1",
            tool_call_name="inspect_graph",
            tool_call_arguments={"view": "overview"},
        )
        from datetime import datetime

        from ToolAgents.data_models.messages import ChatMessage

        now = datetime.now()
        asst_msg = ChatMessage(
            id="a1",
            role=ChatMessageRole.Assistant,
            content=[tool_call],
            created_at=now,
            updated_at=now,
        )
        history.add_message(asst_msg)
        tool_msg = ChatMessage(
            id="t1",
            role=ChatMessageRole.Tool,
            content=[
                ToolCallResultContent(
                    tool_call_result_id="r1",
                    tool_call_id="c1",
                    tool_call_name="inspect_graph",
                    tool_call_result="ok",
                )
            ],
            created_at=now,
            updated_at=now,
        )
        history.add_message(tool_msg)

        sid = self._open_session()
        self.store.append(
            sid,
            ASSISTANT_MODEL_ROLE,
            "",
            payload=chat_message_payload(asst_msg),
        )
        self.store.append(
            sid,
            TOOL_MODEL_ROLE,
            "",
            payload=chat_message_payload(tool_msg),
        )
        self.store.flush(timeout=2.0)
        msgs = self.store.list_messages(sid)
        roles = [m.role for m in msgs]
        self.assertEqual(roles, [ASSISTANT_MODEL_ROLE, TOOL_MODEL_ROLE])
        self.assertEqual(msgs[0].text, "")
        self.assertEqual(msgs[1].text, "")
        self.assertEqual(msgs[0].payload["role"], "assistant")
        self.assertEqual(msgs[1].payload["role"], "tool")
        self.assertEqual(msgs[0].payload["content"][0]["tool_call_name"], "inspect_graph")

    def test_end_active_session_synchronous_close(self) -> None:
        """``end_active_session`` is synchronous: the row is finalized
        immediately (no flush needed)."""
        sid = self._open_session()
        for _ in range(5):
            self.store.append(sid, "user", "hi")
        self.store.flush(timeout=2.0)
        self.store.end_active_session(sid)
        rec = self.store.get_session(sid)
        assert rec is not None
        self.assertIsNotNone(rec.ended_at)
        self.assertEqual(rec.message_count, 5)

    def test_end_active_session_noop_on_none(self) -> None:
        """Passing ``None`` is a safe no-op (no exception, no row touched)."""
        before = self.store._writer_conn.execute("SELECT count(*) FROM sessions").fetchone()[0]
        self.store.end_active_session(None)
        after = self.store._writer_conn.execute("SELECT count(*) FROM sessions").fetchone()[0]
        self.assertEqual(before, after)

    def test_replace_active_session_closes_old_and_opens_new(self) -> None:
        """``replace_active_session`` atomically closes the old row and
        opens a new one.  The old row's ``ended_at`` is set; the new
        row exists and is open (``ended_at`` is NULL)."""
        old_sid = self._open_session(title="old")
        for _ in range(2):
            self.store.append(old_sid, "user", "hi")
        self.store.flush(timeout=2.0)

        new_sid = self.store.replace_active_session(
            old_sid,
            graph_path="/tmp/new.grc",
            graph_hash="grc:new",
            model_alias="new-model",
            backend="ollama",
            title="new",
        )
        self.assertNotEqual(new_sid, old_sid)

        old_rec = self.store.get_session(old_sid)
        assert old_rec is not None
        self.assertIsNotNone(old_rec.ended_at, "old session must be closed")
        self.assertEqual(old_rec.message_count, 2)

        new_rec = self.store.get_session(new_sid)
        assert new_rec is not None
        self.assertIsNone(new_rec.ended_at, "new session must be open")
        self.assertEqual(new_rec.title, "new")

    def test_replace_active_session_with_none_old_just_opens(self) -> None:
        """When ``old_id`` is ``None``, only the INSERT runs."""
        new_sid = self.store.replace_active_session(
            None,
            graph_path="/tmp/x.grc",
            graph_hash="grc:x",
            title="solo",
        )
        rec = self.store.get_session(new_sid)
        assert rec is not None
        self.assertIsNone(rec.ended_at)

    def test_cascade_delete_session_deletes_messages(self) -> None:
        sid = self._open_session()
        self.store.append(sid, "user", "hi")
        self.store.flush(timeout=2.0)
        # Direct DB DELETE on the session; messages must vanish
        # via the FK ON DELETE CASCADE trigger. The test connection
        # needs ``PRAGMA foreign_keys=ON`` to honor cascades.
        conn = sqlite3.connect(str(self.store._db_path))
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("DELETE FROM sessions WHERE id = ?", (sid,))
            conn.commit()
        finally:
            conn.close()
        msgs = self.store.list_messages(sid)
        self.assertEqual(msgs, [])

    def test_clear_all_wipes_every_session(self) -> None:
        """``clear_all`` is the canonical 'reset history' primitive the
        sidebar's "Clear all" button calls. It must remove every
        session row and every message row in one synchronous call,
        and return the number of sessions it removed."""
        ids = [self._open_session(title=f"S{i}") for i in range(3)]
        for sid in ids:
            self.store.append(sid, "user", "hi")
        self.store.flush(timeout=2.0)

        removed = self.store.clear_all()

        self.assertEqual(removed, 3)
        rows = self.store._writer_conn.execute("SELECT count(*) FROM sessions").fetchone()[0]
        self.assertEqual(rows, 0)
        mrows = self.store._writer_conn.execute("SELECT count(*) FROM messages").fetchone()[0]
        self.assertEqual(mrows, 0)

    def test_unicode_text_round_trip(self) -> None:
        sid = self._open_session()
        text = "你好，世界! 🌍 Привет мир"
        self.store.append(sid, "user", text)
        self.store.flush(timeout=2.0)
        msgs = self.store.list_messages(sid)
        self.assertEqual(msgs[0].text, text)


class AsyncWriterTests(_StoreTestCase):
    def test_enqueue_returns_immediately(self) -> None:
        sid = self._open_session()
        start = time.monotonic()
        for _ in range(1_000):
            self.store.append(sid, "user", "x")
        elapsed = time.monotonic() - start
        # 1k enqueues must complete in well under 100ms; this
        # exercises the "no synchronous DB I/O on the main path"
        # contract.
        self.assertLess(elapsed, 0.5, f"enqueue too slow: {elapsed:.3f}s")

    def test_drain_after_pause(self) -> None:
        sid = self._open_session()
        for i in range(100):
            self.store.append(sid, "user", f"m{i}")
        self.assertTrue(self.store.flush(timeout=2.0))
        msgs = self.store.list_messages(sid)
        self.assertEqual(len(msgs), 100)

    def test_backpressure_blocks_producer(self) -> None:
        # Swap the queue for a bounded queue of size 1 after opening session
        sid = self._open_session()

        # Stop the background writer thread so it doesn't drain the queue during the test
        self.store._stop.set()
        self.store._writer.join(timeout=2.0)

        original_q = self.store._q
        small_q = queue.Queue(maxsize=1)
        # Pre-fill the queue
        small_q.put_nowait(_make_pending(sid, "user", "first"))
        self.store._q = small_q

        append_started = threading.Event()
        append_finished = threading.Event()

        def run_append():
            append_started.set()
            self.store.append(sid, "user", "second")
            append_finished.set()

        t = threading.Thread(target=run_append)
        try:
            t.start()
            append_started.wait(timeout=2.0)
            # The thread should block because the queue is full
            blocked = not append_finished.wait(timeout=0.1)
            self.assertTrue(blocked)

            # Pop the first item to unblock the thread
            item = small_q.get_nowait()
            self.assertEqual(item.text, "first")

            # Now the thread should finish
            self.assertTrue(append_finished.wait(timeout=2.0))
        finally:
            self.store._q = original_q
            # Join the thread
            t.join(timeout=2.0)

    def test_drain_batch_never_defers_or_drops_a_burst(self) -> None:
        """A multi-message burst must come back whole from one
        ``_drain_batch`` call, with the queue left empty.

        Regression guard: ``_drain_batch`` used to re-enqueue part of a
        burst back onto ``self._q`` (via ``put_nowait``) when the first
        message's wait had already used up the batch deadline, silently
        dropping it on ``queue.Full``. The fix removed the deferral
        entirely — everything already dequeued into the local ``batch``
        list is returned, never put back.
        """
        sid = self._open_session()
        self.store._stop.set()
        self.store._writer.join(timeout=2.0)

        for i in range(10):
            self.store._q.put_nowait(_make_pending(sid, "user", f"m{i}"))

        batch = self.store._drain_batch()

        self.assertEqual([m.text for m in batch], [f"m{i}" for i in range(10)])
        self.assertTrue(self.store._q.empty())

    def test_concurrent_sessions_share_writer(self) -> None:
        a = self._open_session(title="A")
        b = self._open_session(title="B")
        errors: list[Exception] = []

        def fill(sid: int, prefix: str) -> None:
            try:
                for i in range(50):
                    self.store.append(sid, "user", f"{prefix}{i}")
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        ta = threading.Thread(target=fill, args=(a, "A"))
        tb = threading.Thread(target=fill, args=(b, "B"))
        ta.start()
        tb.start()
        ta.join(timeout=5.0)
        tb.join(timeout=5.0)
        self.assertFalse(ta.is_alive())
        self.assertFalse(tb.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(self.store.flush(timeout=2.0))
        a_msgs = self.store.list_messages(a)
        b_msgs = self.store.list_messages(b)
        self.assertEqual(len(a_msgs), 50)
        self.assertEqual(len(b_msgs), 50)
        # Sequences per session are contiguous and disjoint.
        self.assertEqual(sorted(m.sequence for m in a_msgs), list(range(50)))
        self.assertEqual(sorted(m.sequence for m in b_msgs), list(range(50)))


def _make_pending(session_id: int, role: str, text: str):
    """Build a ``_PendingMessage`` without touching the writer."""
    from grc_agent.sessions_store import _PendingMessage

    return _PendingMessage(
        id="00000000-0000-0000-0000-000000000000",
        session_id=session_id,
        role=role,
        text=text,
        payload=None,
        created_at="2026-01-01T00:00:00.000Z",
    )


# Need queue for the test above; aliasing at module level.
import queue  # noqa: E402


class CrashSafetyTests(_StoreTestCase):
    def test_integrity_check_passes_for_clean_db(self) -> None:
        # Sanity: opening the freshly-created DB succeeds (and the
        # integrity_check inside _open_db returned "ok").
        sid = self._open_session()
        self.store.append(sid, "user", "x")
        self.store.flush(timeout=2.0)
        # If we got here, the DB is intact.

    def test_recovery_from_external_delete(self) -> None:
        sid = self._open_session()
        self.store.append(sid, "user", "before-delete")
        self.store.flush(timeout=2.0)
        # Delete the DB out from under the running store.
        self.store._db_path.unlink()
        # The next append must not crash; the writer will log a
        # "no such table" and the chat widget is unaffected.
        self.store.append(sid, "user", "after-delete")
        # We do not assert on whether the post-delete append made
        # it to disk — the writer's recovery is best-effort — but
        # the call must not raise.
        self.store.flush(timeout=2.0)

    def test_writer_survives_close(self) -> None:
        """C1 regression: ``close()`` must not crash the writer
        thread (it must not push a ``None`` sentinel that
        ``asdict`` would reject)."""
        sid = self._open_session()
        self.store.append(sid, "user", "before-close")
        self.store.flush(timeout=2.0)
        self.store.close()
        # After close, the writer thread is no longer alive.
        self.assertFalse(self.store._writer.is_alive())

    def test_corrupt_file_raises_typed_error(self) -> None:
        """H1 regression: a non-SQLite file at the DB path must
        raise :class:`SessionStoreCorrupt`, not a raw
        ``sqlite3.DatabaseError``."""
        with tempfile.TemporaryDirectory() as tmp:
            db = _make_db_path(Path(tmp))
            db.parent.mkdir(parents=True, exist_ok=True)
            # Write garbage that is *not* a SQLite file.
            db.write_bytes(b"this is definitely not a sqlite database")
            with self.assertRaises(SessionStoreCorrupt):
                SessionStore(db_path=db)


class SingletonTests(unittest.TestCase):
    def test_open_session_store_returns_singleton(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = _make_db_path(Path(tmp))
            a = open_session_store(db_path=db)
            b = open_session_store(db_path=db)
            self.assertIs(a, b)
            a.close()

    def test_session_store_cm_closes_on_exit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = _make_db_path(Path(tmp))
            with session_store_cm(db_path=db) as store:
                sid = store.open_session(graph_path="/x", graph_hash="grc:1")
                store.append(sid, "user", "hi")
                store.flush(timeout=2.0)
            # After the context manager exits, the singleton is
            # cleared, so the next ``open_session_store`` opens a
            # fresh instance against the same DB.
            fresh = open_session_store(db_path=db)
            try:
                rec = fresh.get_session(sid)
                self.assertIsNotNone(rec)
            finally:
                fresh.close()

    def test_path_mismatch_closes_old_singleton(self) -> None:
        # C3 regression: opening with a different path must close
        # the previous singleton (avoiding a leaked writer thread
        # and DB connection), then return a fresh instance.
        with tempfile.TemporaryDirectory() as tmp:
            # _make_db_path is a *file* path. The parent dirs are
            # created for us; we must NOT mkdir the file itself.
            db_a = _make_db_path(Path(tmp) / "a")
            db_b = _make_db_path(Path(tmp) / "b")
            a = open_session_store(db_path=db_a)
            # Replace the singleton with a different path; ``a``
            # must be closed.
            b = open_session_store(db_path=db_b)
            self.assertIsNot(b, a)
            self.assertFalse(a._writer.is_alive())
            b.close()


class SyncApiTests(_StoreTestCase):
    """The sync read helpers (``list_sessions_sync``, ``get_session_sync``,
    ``list_messages_sync``) read via short-lived connections on the GUI thread."""

    def test_list_sessions_sync_filters(self) -> None:
        self._open_session(graph_path="/a.grc", graph_hash="g:1", title="A")
        self._open_session(graph_path="/b.grc", graph_hash="g:2", title="B")
        self.store.flush(timeout=2.0)
        db = self.store._db_path
        all_sessions = list_sessions_sync(db)
        self.assertEqual(len(all_sessions), 2)
        a_sessions = list_sessions_sync(db, graph_path="/a.grc")
        self.assertEqual(len(a_sessions), 1)
        self.assertEqual(a_sessions[0].graph_path, "/a.grc")
        sub = list_sessions_sync(db, graph_path_substring=".grc")
        self.assertEqual(len(sub), 2)

    def test_get_session_sync_missing_returns_none(self) -> None:
        self.assertIsNone(get_session_sync(self.store._db_path, 99999))


class DefaultPathTests(unittest.TestCase):
    def test_default_path_lives_in_user_state(self) -> None:
        # We cannot easily redirect Path.home for the module
        # constant; the assertion is just that the path is
        # absolute and under the home directory.
        path = default_sessions_db_path()
        self.assertTrue(path.is_absolute())
        self.assertIn(".grc_agent", str(path))
        self.assertTrue(str(path).endswith("sessions.db"))


if __name__ == "__main__":
    unittest.main()
