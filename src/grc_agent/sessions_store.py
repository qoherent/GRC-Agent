"""Local chat-session persistence for GRC Agent.

A small SQLite-backed store of chat conversations tied to a
``.grc`` file. Deliberately separate from the existing graph-
checkpoint journal in :mod:`grc_agent.history` — that journal is
a write-once *audit trail of graph state*; this module is a
navigable, queryable *user-facing record of conversations*.

Public surface:

- :class:`SessionStore` — opens (or creates) the DB, owns the
  async writer thread, and exposes non-blocking ``open_session``,
  ``append``, ``close_session``, and a blocking ``flush``.
- :class:`SessionRecord`, :class:`MessageRecord` — frozen
  dataclasses returned by the read-side helpers.
- :class:`SessionStoreTooNew`, :class:`SessionStoreCorrupt` —
  typed errors the loader raises on bad schemas or corrupt files.
- :func:`default_sessions_db_path` — the on-disk path.
- :func:`open_session_store` — module-level convenience that
  returns a singleton :class:`SessionStore` for the process.

Design constraints:

- The GUI/CLI enqueue messages via ``SessionStore.append``, which
  returns immediately (non-blocking ``queue.Queue.put_nowait``).
  All SQLite I/O happens on a single background ``daemon`` thread.
- Batched commits: the writer drains up to 64 messages per
  transaction with a 50 ms deadline.
- Crash safety: WAL + transactions + ``PRAGMA integrity_check``
  on open. A crashed-mid-batch DB re-opens cleanly.
- Backpressure: bounded queue (1000 entries); on overflow the
  *oldest* un-committed message is dropped with a warning. The
  in-memory chat widget is the source of truth for display.
- Single-writer rule: one writer thread per process. Multiple
  *processes* (two GUI windows) share the DB; SQLite WAL
  serializes them transparently.
"""

from __future__ import annotations

import concurrent.futures
import json
import logging
import queue
import sqlite3
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


SCHEMA_VERSION = 1
DEFAULT_DB_NAME = "sessions.db"

# Tunables. These are documented in the design doc; the
# ``grc-agent sessions`` CLI is the operator's surface to inspect
# them via ``--help``.
_QUEUE_MAX = 1000
_BATCH_MAX = 64
_BATCH_TIMEOUT_S = 0.05  # 50 ms ceiling on latency
_SHUTDOWN_TIMEOUT_S = 2.0  # join() timeout for the writer thread on close

# PRAGMAs applied at open time. Order matters (journal_mode must
# precede any transaction; foreign_keys before any DML; busy_timeout
# before any contended query).
PRAGMAS = (
    "PRAGMA journal_mode = WAL",
    "PRAGMA synchronous = NORMAL",
    "PRAGMA foreign_keys = ON",
    "PRAGMA busy_timeout = 5000",
    "PRAGMA temp_store = MEMORY",
    "PRAGMA cache_size = -2000",
)


def default_sessions_db_path() -> Path:
    """Return the default on-disk path of the sessions DB.

    Sibling of the existing :func:`grc_agent.config.user_state`
    directory at ``~/.grc_agent/``. The directory is created on
    first open, never on import.
    """
    return Path.home() / ".grc_agent" / DEFAULT_DB_NAME


class SessionStoreError(RuntimeError):
    """Base class for sessions-store errors."""


class SessionStoreTooNew(SessionStoreError):
    """The on-disk DB's schema_version is newer than this build supports."""


class SessionStoreCorrupt(SessionStoreError):
    """The on-disk DB failed ``PRAGMA integrity_check``."""


@dataclass(frozen=True)
class SessionRecord:
    id: int
    graph_path: str
    graph_hash: str
    started_at: str
    ended_at: str | None
    model_alias: str | None
    backend: str | None
    title: str
    message_count: int
    graph_exists: bool = True


@dataclass(frozen=True)
class MessageRecord:
    id: str  # UUIDv4
    session_id: int
    sequence: int
    role: str
    text: str
    payload: dict | None
    created_at: str


@dataclass
class _PendingMessage:
    """Internal envelope used by the writer thread."""

    id: str
    session_id: int
    role: str
    text: str
    payload: str | None  # pre-serialized JSON; matches the column name
    created_at: str


@dataclass
class _LifecycleCommand:
    """Strict actor pattern: main-thread lifecycle ops enqueue this.

    The main thread NEVER executes SQL on the writer's connection
    (shared connections across threads are unsafe for concurrent
    SQLite transactions).  Instead, lifecycle methods build one of
    these envelopes, push it onto ``_q``, and block on
    ``future.result(timeout)``.  The writer thread pops the command,
    runs the SQL on its exclusive connection, and resolves the
    future with the result (or exception) so the main thread can
    unblock.
    """

    kind: str  # "open", "end", "replace"
    kwargs: dict[str, Any]
    future: concurrent.futures.Future[Any]


_SQL_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (
    version      INTEGER PRIMARY KEY,
    installed_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS sessions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    graph_path    TEXT    NOT NULL,
    graph_hash    TEXT    NOT NULL,
    started_at    TEXT    NOT NULL,
    ended_at      TEXT,
    model_alias   TEXT,
    backend       TEXT,
    title         TEXT,
    message_count INTEGER NOT NULL DEFAULT 0,
    graph_exists  INTEGER NOT NULL DEFAULT 1,
    UNIQUE (graph_path, graph_hash, started_at)
);
CREATE INDEX IF NOT EXISTS idx_sessions_graph_path
    ON sessions (graph_path, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_graph_hash
    ON sessions (graph_hash, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_started_at
    ON sessions (started_at DESC);

CREATE TABLE IF NOT EXISTS messages (
    id         TEXT    PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    sequence   INTEGER NOT NULL,
    role       TEXT    NOT NULL,
    text       TEXT    NOT NULL DEFAULT '',
    payload    TEXT,
    created_at TEXT    NOT NULL,
    UNIQUE (session_id, sequence)
);
CREATE INDEX IF NOT EXISTS idx_messages_session_seq
    ON messages (session_id, sequence);

CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    text,
    content='messages',
    content_rowid='rowid',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, text) VALUES (new.rowid, new.text);
END;
CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, text)
        VALUES ('delete', old.rowid, old.text);
END;
CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, text)
        VALUES ('delete', old.rowid, old.text);
    INSERT INTO messages_fts(rowid, text) VALUES (new.rowid, new.text);
END;
"""


def _utcnow_iso() -> str:
    """Return the current UTC time as an ISO-8601 string with ``Z``."""
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _apply_pragmas(conn: sqlite3.Connection) -> None:
    for pragma in PRAGMAS:
        conn.execute(pragma)


def _init_schema(conn: sqlite3.Connection) -> None:
    """Create the schema and record v1 if missing. Idempotent."""
    conn.executescript(_SQL_SCHEMA)
    row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO schema_version (version) VALUES (?)",
            (SCHEMA_VERSION,),
        )
        conn.commit()


def _open_db(db_path: Path, validate_integrity: bool = False) -> sqlite3.Connection:
    """Open the DB, run PRAGMAs, run migrations, and optionally run integrity_check.

    Raises :class:`SessionStoreTooNew` for forward-incompatible
    schemas and :class:`SessionStoreCorrupt` for integrity
    failures. The caller owns the connection.

    Connections are opened with ``check_same_thread=False``. The
    writer thread holds one long-lived connection for writes;
    read-side helpers open a fresh short-lived connection per read.
    SQLite serializes writers at the file level under WAL.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        conn = sqlite3.connect(
            str(db_path),
            isolation_level=None,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
    except sqlite3.DatabaseError as exc:
        raise SessionStoreCorrupt(f"sessions DB at {db_path} could not be opened: {exc}") from exc
    try:
        _apply_pragmas(conn)
    except sqlite3.DatabaseError as exc:
        conn.close()
        raise SessionStoreCorrupt(
            f"sessions DB at {db_path} is not a valid SQLite database: {exc}"
        ) from exc
    # Detect a too-new schema before running migrations so we never
    # partially apply a migration to a DB we cannot read.
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
    ).fetchone()
    if row is not None:
        v = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
        file_version = int(v[0]) if v is not None else 0
        if file_version > SCHEMA_VERSION:
            conn.close()
            raise SessionStoreTooNew(
                f"sessions DB schema_version={file_version} is newer than "
                f"supported {SCHEMA_VERSION}. Upgrade GRC Agent or move "
                f"{db_path} aside to keep using the new schema."
            )
    _init_schema(conn)
    # integrity_check is a fast scan on a small DB; failure here is
    # unrecoverable from inside the writer, so we surface it
    # immediately. We only run it on DB initialization/construction
    # to avoid huge query-level overhead.
    if validate_integrity:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()
        if integrity is None or integrity[0] != "ok":
            conn.close()
            raise SessionStoreCorrupt(f"sessions DB at {db_path} failed integrity_check: {integrity!r}")
    return conn


# Shared read-SQL. The bound ``SessionStore`` read methods and the
# ``*_sync`` module-level helpers read through these so the column lists and
# filter builders live in one place (AGENTS.md "Simplify by removal").
_SESSION_COLUMNS = (
    "id, graph_path, graph_hash, started_at, ended_at, "
    "model_alias, backend, title, message_count, graph_exists"
)
_MESSAGES_COLUMNS = "id, session_id, sequence, role, text, payload, created_at"


def _select_sessions_sql(
    *,
    graph_path: str | None,
    graph_path_substring: str | None,
    limit: int,
) -> tuple[str, list[Any]]:
    sql = f"SELECT {_SESSION_COLUMNS} FROM sessions "
    params: list[Any] = []
    clauses: list[str] = []
    if graph_path is not None:
        clauses.append("graph_path = ?")
        params.append(graph_path)
    if graph_path_substring is not None:
        clauses.append("graph_path LIKE ?")
        params.append(f"%{graph_path_substring}%")
    if clauses:
        sql += "WHERE " + " AND ".join(clauses) + " "
    sql += "ORDER BY started_at DESC LIMIT ?"
    params.append(int(limit))
    return sql, params


def _select_session_by_id_sql() -> str:
    return f"SELECT {_SESSION_COLUMNS} FROM sessions WHERE id = ?"


def _select_messages_sql() -> str:
    return f"SELECT {_MESSAGES_COLUMNS} FROM messages WHERE session_id = ? ORDER BY sequence ASC"


def _row_to_session(row: sqlite3.Row) -> SessionRecord:
    return SessionRecord(
        id=int(row["id"]),
        graph_path=str(row["graph_path"]),
        graph_hash=str(row["graph_hash"]),
        started_at=str(row["started_at"]),
        ended_at=row["ended_at"],
        model_alias=row["model_alias"],
        backend=row["backend"],
        title=row["title"] or "",
        message_count=int(row["message_count"]),
        graph_exists=bool(row["graph_exists"]),
    )


def _row_to_message(row: sqlite3.Row) -> MessageRecord:
    raw_payload = row["payload"]
    payload: dict | None = None
    if raw_payload:
        try:
            payload = json.loads(raw_payload)
        except json.JSONDecodeError:
            logger.warning(
                "sessions: payload for message %s is not valid JSON; "
                "dropping the payload but keeping the message",
                row["id"],
            )
    return MessageRecord(
        id=str(row["id"]),
        session_id=int(row["session_id"]),
        sequence=int(row["sequence"]),
        role=str(row["role"]),
        text=str(row["text"]),
        payload=payload,
        created_at=str(row["created_at"]),
    )


# ---------------------------------------------------------------------------
# Async writer
# ---------------------------------------------------------------------------


class SessionStore:
    """Owner of one process-local writer thread + SQLite connection.

    Construct with a DB path. The constructor opens the DB,
    applies the schema, and starts the writer daemon thread.
    All public mutators (open_session, append, close_session) are
    non-blocking — they enqueue onto the writer's queue and
    return. Use :meth:`flush` to block until the queue is
    drained.
    """

    _instance_lock = threading.Lock()
    _instance: SessionStore | None = None

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = (db_path or default_sessions_db_path()).expanduser()
        # Message-write queue (append): high-volume, bounded by _QUEUE_MAX.
        self._q: queue.Queue[_PendingMessage] = queue.Queue(maxsize=_QUEUE_MAX)
        # Lifecycle-actor queue (open/end/replace/clear_all): low-volume,
        # bounded by _QUEUE_MAX.  Separate queue so the message-batch
        # driller doesn't have to discriminate types.
        self._lifecycle_q: queue.Queue[_LifecycleCommand] = queue.Queue(
            maxsize=_QUEUE_MAX
        )
        self._closed_sessions: queue.Queue[int] = queue.Queue(maxsize=_QUEUE_MAX)
        # ``_drained`` is cleared on every enqueue and set by the
        # writer when it has nothing to do. ``flush()`` blocks on
        # it.
        self._drained = threading.Event()
        self._drained.set()
        # The writer holds its own connection (a single connection
        # per thread is the SQLite-recommended pattern). The main
        # thread NEVER touches ``_writer_conn`` — the strict actor
        # pattern guarantees one writer at a time. Lifecycle
        # methods (open/end/replace) build a ``_LifecycleCommand``
        # with a Future and block the main thread on its result;
        # the writer thread executes the SQL on its exclusive
        # connection and resolves the future.
        self._writer_conn = _open_db(self._db_path, validate_integrity=True)

        # The writer's run() loop body.
        self._stop = threading.Event()
        self._writer = threading.Thread(target=self._run, name="grc-sessions-writer", daemon=True)
        self._writer.start()

    # --- public, non-blocking ---

    def _enqueue_lifecycle(
        self, kind: str, kwargs: dict[str, Any], *, timeout: float = 5.0
    ) -> Any:
        """Enqueue one ``_LifecycleCommand`` and block on its Future.

        Shared backbone for ``open_session``, ``end_active_session``,
        ``replace_active_session`` and ``clear_all``.  The caller-
        supplied ``timeout`` is bounded so a wedged writer can't lock
        the GUI thread indefinitely.
        """
        future: concurrent.futures.Future[Any] = concurrent.futures.Future()
        cmd = _LifecycleCommand(kind=kind, kwargs=kwargs, future=future)
        self._lifecycle_q.put(cmd)
        self._drained.clear()
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError as exc:
            raise RuntimeError(
                f"SessionStore writer thread did not handle {kind!r} "
                f"lifecycle command within {timeout:.1f}s"
            ) from exc

    def open_session(
        self,
        *,
        graph_path: str,
        graph_hash: str,
        model_alias: str | None = None,
        backend: str | None = None,
        title: str = "",
        started_at: str | None = None,
    ) -> int:
        """Open a new session and return its id.

        Strict actor pattern: the main thread enqueues a
        ``_LifecycleCommand(kind="open", ...)`` and blocks on its
        Future; the writer thread executes the INSERT on its
        exclusive connection.  No SQL ever runs on the main thread.

        ``started_at`` is exposed as a parameter for tests; the
        GUI/CLI should let it default to "now."
        """
        ts = started_at or _utcnow_iso()
        return int(
            self._enqueue_lifecycle(
                "open",
                {
                    "graph_path": graph_path,
                    "graph_hash": graph_hash,
                    "model_alias": model_alias,
                    "backend": backend,
                    "title": title,
                    "started_at": ts,
                },
            )
        )

    def append(
        self,
        session_id: int,
        role: str,
        text: str,
        payload: dict | None = None,
    ) -> str:
        """Enqueue a message for an open session. Non-blocking.

        Returns the message id (UUIDv4) assigned at enqueue time.
        The in-memory chat widget can use the id to refer to a
        specific message even before the writer commits it.
        """
        msg_id = str(uuid.uuid4())
        rec = _PendingMessage(
            id=msg_id,
            session_id=session_id,
            role=role,
            text=text,
            payload=json.dumps(payload) if payload is not None else None,
            created_at=_utcnow_iso(),
        )
        try:
            self._q.put_nowait(rec)
        except queue.Full:
            # Backpressure: drop the oldest and enqueue the new.
            try:
                dropped = self._q.get_nowait()
                self._q.put_nowait(rec)
                logger.warning(
                    "sessions_queue_full dropped_id=%s role=%s session=%s",
                    dropped.id,
                    dropped.role,
                    dropped.session_id,
                )
            except queue.Empty:  # extremely unlikely
                pass
        self._drained.clear()
        return msg_id

    def close_session(self, session_id: int) -> None:
        """Enqueue a session-close. Non-blocking.

        The writer thread applies the close when it next picks
        up the queue: it sets ``ended_at`` and updates
        ``message_count`` to reflect the final tally.
        """
        self._closed_sessions.put(session_id)
        self._drained.clear()

    def end_active_session(self, session_id: int | None) -> None:
        """Finalize a session row (``ended_at`` + ``message_count``).

        No-op when ``session_id`` is ``None``.  Strict actor pattern:
        enqueues an ``end`` lifecycle command and blocks on its
        Future; the writer thread executes the UPDATE on its
        exclusive connection so no SQL ever runs on the main thread.
        """
        if session_id is None:
            return
        self._enqueue_lifecycle(
            "end",
            {"session_id": session_id},
        )

    def replace_active_session(
        self,
        old_id: int | None,
        *,
        graph_path: str,
        graph_hash: str,
        model_alias: str | None = None,
        backend: str | None = None,
        title: str = "",
    ) -> int:
        """Atomically close ``old_id`` (if not ``None``) and open a new row.

        Returns the new session id.  Strict actor pattern: enqueues a
        ``replace`` lifecycle command and blocks on its Future; the
        writer thread executes the close + open inside one
        ``BEGIN``/``COMMIT`` block on its exclusive connection,
        guaranteeing transactional atomicity AND serializing all
        lifecycle operations behind a single writer thread.
        """
        return int(
            self._enqueue_lifecycle(
                "replace",
                {
                    "old_id": old_id,
                    "graph_path": graph_path,
                    "graph_hash": graph_hash,
                    "model_alias": model_alias,
                    "backend": backend,
                    "title": title,
                },
            )
        )

    def flush(self, timeout: float = 5.0) -> bool:
        """Block until the writer is caught up. Returns True on
        drain, False on timeout."""
        return self._drained.wait(timeout)

    def clear_all(self) -> int:
        """Delete every session and its messages from the on-disk DB.

        Strict actor pattern: enqueues a ``clear_all`` lifecycle
        command and blocks on its Future.  The writer thread counts,
        deletes, and returns the count via the Future.  The caller can
        re-open the sessions list immediately after the call returns
        and observe the empty state.

        Returns the number of session rows that were removed.
        """
        return int(self._enqueue_lifecycle("clear_all", {}))

    def close(self) -> None:
        """Stop the writer thread and close the DB.

        Idempotent. Safe to call multiple times. After ``close``,
        the store should not be used; the caller is expected to
        drop its reference. The shutdown is bounded by
        ``_SHUTDOWN_TIMEOUT_S`` to guarantee forward progress
        even if the writer thread is wedged in a SQLite call.
        """
        if self._stop.is_set():
            return
        # Signal the writer first so it stops picking up work
        # before we drain the queue. Then flush the queue, then
        # join.
        self._stop.set()
        # Drain whatever is queued so a clean shutdown commits
        # the in-flight messages. ``flush`` is bounded; if the
        # queue is deep, we still proceed to close the DB after
        # the timeout (any remaining messages are lost, which is
        # the explicit contract of a clean shutdown).
        self.flush(timeout=2.0)
        # The writer's loop checks ``self._stop`` at the top of
        # each iteration; setting it is sufficient to wake the
        # thread out of its blocking ``_q.get`` (which has a
        # 50ms timeout anyway) and let it exit cleanly. We do
        # NOT push a sentinel — the queue is typed as
        # ``_PendingMessage``; a ``None`` is not a valid item.
        self._writer.join(timeout=_SHUTDOWN_TIMEOUT_S)
        if self._writer.is_alive():
            logger.warning(
                "sessions writer thread did not exit within %.1fs; "
                "leaving daemon thread to be cleaned up at process exit",
                _SHUTDOWN_TIMEOUT_S,
            )
        try:
            self._writer_conn.close()
        except sqlite3.Error:
            pass
        # Allow a fresh store to be created. We do NOT hold the
        # instance lock here: ``open_session_store`` calls
        # ``close()`` from inside its own critical section, and
        # re-acquiring the same ``Lock`` would deadlock.
        if SessionStore._instance is self:
            SessionStore._instance = None

    # --- read-side helpers (fresh short-lived connection per read) ---

    @contextmanager
    def _read_conn(self) -> Iterator[sqlite3.Connection]:
        """Open a fresh connection on the calling thread for reads.

        The writer's connection is locked to its own thread (Python
        sqlite3 enforces this). Read methods must not touch it
        from the main thread; this helper gives every read its
        own short-lived connection.
        """
        conn = _open_db(self._db_path)
        try:
            yield conn
        finally:
            conn.close()

    def list_sessions(
        self,
        *,
        graph_path: str | None = None,
        graph_path_substring: str | None = None,
        limit: int = 50,
    ) -> list[SessionRecord]:
        """Return sessions ordered by started_at DESC, optionally
        filtered by exact graph_path or a substring match."""
        sql, params = _select_sessions_sql(
            graph_path=graph_path,
            graph_path_substring=graph_path_substring,
            limit=limit,
        )
        with self._read_conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_row_to_session(r) for r in rows]

    def get_session(self, session_id: int) -> SessionRecord | None:
        with self._read_conn() as conn:
            row = conn.execute(
                _select_session_by_id_sql(),
                (int(session_id),),
            ).fetchone()
        return _row_to_session(row) if row is not None else None

    def list_messages(self, session_id: int) -> list[MessageRecord]:
        with self._read_conn() as conn:
            rows = conn.execute(
                _select_messages_sql(),
                (int(session_id),),
            ).fetchall()
        return [_row_to_message(r) for r in rows]

    def gc(
        self,
        *,
        older_than_days: int = 180,
        only_orphans: bool = False,
    ) -> int:
        """Delete sessions older than ``older_than_days`` (or all
        sessions whose ``graph_path`` no longer exists on disk
        when ``only_orphans=True``). Returns the number of
        sessions deleted."""
        if only_orphans:
            sessions = self.list_sessions(limit=10_000)
            deleted = 0
            for s in sessions:
                if not Path(s.graph_path).exists():
                    with self._read_conn() as conn:
                        conn.execute("BEGIN")
                        try:
                            conn.execute("DELETE FROM sessions WHERE id = ?", (s.id,))
                            conn.execute("COMMIT")
                        except Exception:
                            conn.execute("ROLLBACK")
                            raise
                    deleted += 1
            return deleted
        # ``datetime('now', ??)`` is the SQLite way to compute
        # ``now - N days``. We pass the days as a negative
        # modifier.
        with self._read_conn() as conn:
            conn.execute("BEGIN")
            try:
                cur = conn.execute(
                    "DELETE FROM sessions WHERE started_at < datetime('now', ?)",
                    (f"-{int(older_than_days)} days",),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return int(cur.rowcount or 0)


    # --- internal: writer thread ---

    def _run(self) -> None:
        """Drain the lifecycle queue first, then the message batch.

        Runs forever until :attr:`_stop` is set.  Sleeps briefly
        when the queue is empty so we do not spin.

        Lifecycle commands are drained FIRST because they unblock a
        main thread waiting on a Future.  Message batches then commit
        under their own transaction.  All SQL runs exclusively on the
        writer thread — the main thread never touches ``_writer_conn``.
        """
        try:
            while not self._stop.is_set():
                lifecycle = self._drain_lifecycle()
                batch = self._drain_batch()
                closed = self._drain_closes()
                if not lifecycle and not batch and not closed:
                    self._drained.set()
                    time.sleep(0.005)
                    continue
                # Process lifecycle commands first (they hold Futures).
                if lifecycle:
                    self._drain_lifecycle_commands(lifecycle)
                # Then commit the message batch under its own BEGIN.
                if batch or closed:
                    try:
                        self._writer_conn.execute("BEGIN")
                        if batch:
                            for r in batch:
                                row = self._writer_conn.execute(
                                    "SELECT COALESCE(MAX(sequence), -1) + 1 "
                                    "FROM messages WHERE session_id = ?",
                                    (r.session_id,),
                                ).fetchone()
                                next_seq = int(row[0])
                                self._writer_conn.execute(
                                    "INSERT INTO messages "
                                    "(id, session_id, sequence, role, text, "
                                    "payload, created_at) "
                                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                                    (
                                        r.id,
                                        r.session_id,
                                        next_seq,
                                        r.role,
                                        r.text,
                                        r.payload,
                                        r.created_at,
                                    ),
                                )
                        if closed:
                            for sid in closed:
                                self._writer_conn.execute(
                                    "UPDATE sessions SET ended_at = ?, "
                                    "message_count = (SELECT COUNT(*) FROM "
                                    "messages WHERE session_id = ?) "
                                    "WHERE id = ?",
                                    (_utcnow_iso(), sid, sid),
                                )
                        self._writer_conn.execute("COMMIT")
                    except sqlite3.Error as exc:
                        logger.exception(
                            "sessions_writer_batch_failed: %s", exc
                        )
                        try:
                            self._writer_conn.execute("ROLLBACK")
                        except sqlite3.Error:
                            pass
        except Exception:  # noqa: BLE001 - we want a logged crash
            logger.exception("sessions_writer_crashed")

    def _drain_lifecycle(self) -> list[_LifecycleCommand]:
        """Pop all currently-queued lifecycle commands (non-blocking)."""
        commands: list[_LifecycleCommand] = []
        while True:
            try:
                commands.append(self._lifecycle_q.get_nowait())
            except queue.Empty:
                break
        return commands

    def _drain_lifecycle_commands(self, commands: list[_LifecycleCommand]) -> None:
        """Execute one lifecycle command at a time, resolving its Future.

        Each command runs in its own BEGIN/COMMIT transaction so the
        result can be returned to the main thread on success / raised
        as an exception on failure.  The writer thread is the only
        thread that touches ``_writer_conn`` — the actor pattern.
        """
        for cmd in commands:
            try:
                if cmd.kind == "open":
                    result = self._exec_open(cmd.kwargs)
                elif cmd.kind == "end":
                    self._exec_end(cmd.kwargs)
                    result = None
                elif cmd.kind == "replace":
                    result = self._exec_replace(cmd.kwargs)
                elif cmd.kind == "clear_all":
                    result = self._exec_clear_all(cmd.kwargs)
                else:
                    raise RuntimeError(f"unknown lifecycle kind: {cmd.kind!r}")
                cmd.future.set_result(result)
            except Exception as exc:
                cmd.future.set_exception(exc)
                logger.exception(
                    "sessions_lifecycle_command_failed kind=%s", cmd.kind
                )

    def _exec_open(self, kwargs: dict[str, Any]) -> int:
        # ``_writer_conn`` is in autocommit mode (isolation_level=None),
        # so ``with self._writer_conn:`` doesn't auto-transaction.
        # Wrap in explicit BEGIN/COMMIT for the atomic insert.
        self._writer_conn.execute("BEGIN")
        try:
            cur = self._writer_conn.execute(
                "INSERT INTO sessions "
                "(graph_path, graph_hash, started_at, model_alias, backend, title) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    kwargs["graph_path"],
                    kwargs["graph_hash"],
                    kwargs["started_at"],
                    kwargs["model_alias"],
                    kwargs["backend"],
                    kwargs["title"],
                ),
            )
            new_id = int(cur.lastrowid)
            self._writer_conn.execute("COMMIT")
        except Exception:
            self._writer_conn.execute("ROLLBACK")
            raise
        return new_id

    def _exec_end(self, kwargs: dict[str, Any]) -> None:
        sid = kwargs["session_id"]
        self._writer_conn.execute("BEGIN")
        try:
            self._writer_conn.execute(
                "UPDATE sessions SET ended_at = ?, "
                "message_count = (SELECT COUNT(*) FROM messages "
                "WHERE session_id = ?) WHERE id = ?",
                (_utcnow_iso(), sid, sid),
            )
            self._writer_conn.execute("COMMIT")
        except Exception:
            self._writer_conn.execute("ROLLBACK")
            raise

    def _exec_replace(self, kwargs: dict[str, Any]) -> int:
        old_id = kwargs.get("old_id")
        self._writer_conn.execute("BEGIN")
        try:
            if old_id is not None:
                self._writer_conn.execute(
                    "UPDATE sessions SET ended_at = ?, "
                    "message_count = (SELECT COUNT(*) FROM messages "
                    "WHERE session_id = ?) WHERE id = ?",
                    (_utcnow_iso(), old_id, old_id),
                )
            cur = self._writer_conn.execute(
                "INSERT INTO sessions "
                "(graph_path, graph_hash, started_at, model_alias, backend, title) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    kwargs["graph_path"],
                    kwargs["graph_hash"],
                    _utcnow_iso(),
                    kwargs["model_alias"],
                    kwargs["backend"],
                    kwargs["title"],
                ),
            )
            new_id = int(cur.lastrowid)
            self._writer_conn.execute("COMMIT")
        except Exception:
            self._writer_conn.execute("ROLLBACK")
            raise
        return new_id

    def _exec_clear_all(self, _kwargs: dict[str, Any]) -> int:
        self._writer_conn.execute("BEGIN")
        try:
            before = self._writer_conn.execute(
                "SELECT count(*) FROM sessions"
            ).fetchone()[0]
            self._writer_conn.execute("DELETE FROM messages")
            self._writer_conn.execute("DELETE FROM sessions")
            self._writer_conn.execute("COMMIT")
        except Exception:
            self._writer_conn.execute("ROLLBACK")
            raise
        return int(before)

    def _drain_batch(self) -> list[_PendingMessage]:
        """Pop up to ``_BATCH_MAX`` messages, blocking at most
        ``_BATCH_TIMEOUT_S`` total."""
        batch: list[_PendingMessage] = []
        deadline = time.monotonic() + _BATCH_TIMEOUT_S
        # First message: block until deadline (or one arrives).
        try:
            batch.append(self._q.get(timeout=_BATCH_TIMEOUT_S))
        except queue.Empty:
            return batch
        # Subsequent messages: non-blocking.
        while len(batch) < _BATCH_MAX:
            try:
                batch.append(self._q.get_nowait())
            except queue.Empty:
                break
        # Honor the deadline for the very first message: if we
        # took most of the budget getting it, re-enqueue the
        # tail so a later iteration can pick it up. We never
        # silently drop a message.
        if time.monotonic() >= deadline and len(batch) > 1:
            for msg in batch[1:]:
                try:
                    self._q.put_nowait(msg)
                except queue.Full:
                    # The queue is full because the writer is
                    # too slow. Log and drop; the in-memory chat
                    # widget is the display source of truth.
                    logger.warning(
                        "sessions_writer_overflow dropped_id=%s role=%s",
                        msg.id,
                        msg.role,
                    )
            return batch[:1]
        return batch

    def _drain_closes(self) -> list[int]:
        closes: list[int] = []
        while True:
            try:
                closes.append(self._closed_sessions.get_nowait())
            except queue.Empty:
                return closes


# ---------------------------------------------------------------------------
# Module-level singleton + sync API for the CLI
# ---------------------------------------------------------------------------


def open_session_store(db_path: Path | None = None) -> SessionStore:
    """Return a process-local singleton :class:`SessionStore`.

    The CLI uses this in its short-lived subcommands. The GUI
    holds its own store for the lifetime of the window. Re-calling
    this returns the existing instance unless the path differs
    (in which case the old singleton is closed to avoid leaking
    its writer thread and DB connection).
    """
    with SessionStore._instance_lock:
        if SessionStore._instance is not None:
            if db_path is None or SessionStore._instance._db_path == db_path.expanduser():
                return SessionStore._instance
            # Path mismatch: close the old store so we don't leak
            # its writer thread and DB connection.
            old = SessionStore._instance
            SessionStore._instance = None
            logger.debug("open_session_store: path mismatch, closing old store %s", old._db_path)
            try:
                old.close()
            except Exception:  # noqa: BLE001
                logger.debug("open_session_store: previous close() raised", exc_info=True)
            logger.debug("open_session_store: old store closed, opening new store %s", db_path)
        store = SessionStore(db_path=db_path)
        SessionStore._instance = store
        return store


@contextmanager
def session_store_cm(db_path: Path | None = None) -> Iterator[SessionStore]:
    """Context manager that opens a store and closes it on exit.

    Useful for CLI subcommands that want a fresh store they can
    ``close()`` deterministically.
    """
    store = open_session_store(db_path=db_path)
    try:
        yield store
    finally:
        store.close()


# ---------------------------------------------------------------------------
# Synchronous read helpers (short-lived connection per read; GUI read paths)
# ---------------------------------------------------------------------------


def list_sessions_sync(
    db_path: Path,
    *,
    graph_path: str | None = None,
    graph_path_substring: str | None = None,
    limit: int = 50,
) -> list[SessionRecord]:
    """Read-only list helper used by the GUI read path."""
    sql, params = _select_sessions_sql(
        graph_path=graph_path,
        graph_path_substring=graph_path_substring,
        limit=limit,
    )
    conn = _open_db(db_path)
    try:
        rows = conn.execute(sql, params).fetchall()
        return [_row_to_session(r) for r in rows]
    finally:
        conn.close()


def get_session_sync(db_path: Path, session_id: int) -> SessionRecord | None:
    conn = _open_db(db_path)
    try:
        row = conn.execute(_select_session_by_id_sql(), (int(session_id),)).fetchone()
        return _row_to_session(row) if row is not None else None
    finally:
        conn.close()


def list_messages_sync(db_path: Path, session_id: int) -> list[MessageRecord]:
    conn = _open_db(db_path)
    try:
        rows = conn.execute(_select_messages_sql(), (int(session_id),)).fetchall()
        return [_row_to_message(r) for r in rows]
    finally:
        conn.close()


__all__ = [
    "DEFAULT_DB_NAME",
    "MessageRecord",
    "SCHEMA_VERSION",
    "SessionRecord",
    "SessionStore",
    "SessionStoreCorrupt",
    "SessionStoreError",
    "SessionStoreTooNew",
    "default_sessions_db_path",
    "get_session_sync",
    "list_messages_sync",
    "list_sessions_sync",
    "open_session_store",
    "session_store_cm",
]
