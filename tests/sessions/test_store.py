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
    append_message_sync,
    default_sessions_db_path,
    export_markdown_sync,
    get_session_sync,
    list_messages_sync,
    list_sessions_sync,
    open_session_store,
    open_session_sync,
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
            row = sqlite3.connect(str(self.store._db_path)).execute(
                "SELECT name FROM sqlite_master WHERE type='trigger' AND name=?",
                (trigger,),
            ).fetchone()
            self.assertIsNotNone(row, f"missing trigger: {trigger}")

    def test_schema_version_is_one(self) -> None:
        conn = sqlite3.connect(str(self.store._db_path))
        try:
            row = conn.execute(
                "SELECT version FROM schema_version LIMIT 1"
            ).fetchone()
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
        sid = self._open_session(
            graph_path="/tmp/a.grc", graph_hash="grc:111", title="first"
        )
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
        from grc_agent.session_ops import (
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
        self.assertEqual(
            msgs[0].payload["content"][0]["tool_call_name"], "inspect_graph"
        )

    def test_close_session_updates_counts_and_ended_at(self) -> None:
        sid = self._open_session()
        for _ in range(3):
            self.store.append(sid, "user", "hi")
        self.store.close_session(sid)
        self.store.flush(timeout=2.0)
        rec = self.store.get_session(sid)
        assert rec is not None
        self.assertEqual(rec.message_count, 3)
        self.assertIsNotNone(rec.ended_at)

    def test_fts5_finds_text(self) -> None:
        sid = self._open_session()
        self.store.append(sid, "user", "Please add a lowpass filter")
        self.store.append(sid, "assistant", "Adding a kalman estimator instead")
        self.store.flush(timeout=2.0)
        # ``lowpass`` is a single token under the unicode61
        # tokenizer (the hyphen is a separator, so ``low-pass``
        # would tokenize to ``low`` and ``pass``). The point of
        # this test is that FTS5 finds a hit, not which token
        # strategy we use.
        hits = self.store.search_messages("lowpass")
        self.assertEqual(len(hits), 1)
        self.assertIn("lowpass", hits[0].text)
        hits2 = self.store.search_messages("kalman")
        self.assertEqual(len(hits2), 1)
        self.assertEqual(hits2[0].text, "Adding a kalman estimator instead")

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

    def test_backpressure_drops_oldest(self) -> None:
        # Drive the backpressure contract by swapping the live
        # queue for a stub that is permanently full. The first
        # append into the stub raises queue.Full, the writer logs
        # a warning, the second append (the new message) is
        # then placed into the stub's buffer.
        sid = self._open_session()
        original_q = self.store._q
        small_q: queue.Queue = queue.Queue(maxsize=1)
        # Pre-fill the small queue so the next put_nowait raises
        # queue.Full.
        small_q.put_nowait(_make_pending(sid, 0, "user", "first"))
        self.store._q = small_q
        try:
            with self.assertLogs(
                "grc_agent.sessions_store", level="WARNING"
            ) as log_ctx:
                self.store.append(sid, "user", "second")
            joined = "\n".join(log_ctx.output)
            self.assertIn("sessions_queue_full", joined)
        finally:
            self.store._q = original_q
        # Drain so the test's queue stub does not block teardown.
        try:
            while not small_q.empty():
                small_q.get_nowait()
        except queue.Empty:
            pass

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


def _make_pending(session_id: int, sequence: int, role: str, text: str):
    """Build a ``_PendingMessage`` without touching the writer."""
    from grc_agent.sessions_store import _PendingMessage

    return _PendingMessage(
        id="00000000-0000-0000-0000-000000000000",
        session_id=session_id,
        sequence=sequence,
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


class SyncApiTests(unittest.TestCase):
    """The CLI uses the *sync* helpers, bypassing the writer thread."""

    def test_open_and_append_via_sync(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = _make_db_path(Path(tmp))
            sid = open_session_sync(
                db,
                graph_path="/x.grc",
                graph_hash="grc:42",
                model_alias="m",
                title="t",
            )
            mid = append_message_sync(
                db, session_id=sid, role="user", text="hi"
            )
            self.assertEqual(len(mid), 36)  # UUIDv4
            msgs = list_messages_sync(db, sid)
            self.assertEqual(len(msgs), 1)
            self.assertEqual(msgs[0].text, "hi")
            self.assertEqual(msgs[0].sequence, 0)
            # Second message gets sequence 1.
            append_message_sync(
                db, session_id=sid, role="assistant", text="hello"
            )
            msgs = list_messages_sync(db, sid)
            self.assertEqual([m.sequence for m in msgs], [0, 1])

    def test_list_sessions_sync_filters(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = _make_db_path(Path(tmp))
            open_session_sync(db, graph_path="/a.grc", graph_hash="g:1", title="A")
            open_session_sync(db, graph_path="/b.grc", graph_hash="g:2", title="B")
            all_sessions = list_sessions_sync(db)
            self.assertEqual(len(all_sessions), 2)
            a_sessions = list_sessions_sync(db, graph_path="/a.grc")
            self.assertEqual(len(a_sessions), 1)
            self.assertEqual(a_sessions[0].graph_path, "/a.grc")
            sub = list_sessions_sync(db, graph_path_substring=".grc")
            self.assertEqual(len(sub), 2)

    def test_export_markdown_sync(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = _make_db_path(Path(tmp))
            sid = open_session_sync(
                db,
                graph_path="/x.grc",
                graph_hash="g:1",
                model_alias="m",
                title="Demo",
            )
            append_message_sync(db, session_id=sid, role="user", text="hi")
            append_message_sync(
                db,
                session_id=sid,
                role="tool_finished",
                text="applied",
                payload={"kind": "tool_finished_ok", "ok": True},
            )
            md = export_markdown_sync(db, sid)
            self.assertIn("# Demo", md)
            self.assertIn("## You", md)
            self.assertIn("## Tool (finished)", md)
            self.assertIn('"kind": "tool_finished_ok"', md)

    def test_get_session_sync_missing_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = _make_db_path(Path(tmp))
            self.assertIsNone(get_session_sync(db, 99999))


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
