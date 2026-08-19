import logging
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from pydantic_ai import ModelMessagesTypeAdapter
from pydantic_ai.messages import ModelMessage
from pydantic_ai_harness.step_persistence import SqliteStepStore

from .settings import env_path

_log = logging.getLogger(__name__)

# Latest on-disk schema version applied by init_db(). Each migration in
# _apply_migrations() bumps the version recorded in the `_meta` table so a
# crashed migration resumes cleanly and a stale-schema DB is detectable.
LATEST_SCHEMA_VERSION = 4

# Generous cap so the sessions table cannot grow without limit. The previous
# JSON-file store bounded itself to 10 on write; this only prunes well outside
# the recent window a user would reasonably scroll back to.
_MAX_SESSIONS = 200

# Per-db-path "already initialized" guard. init_db() is idempotent, but the
# guard avoids re-running the PRAGMA-table_info / migration probes on every
# call. Keyed on the resolved db path so test isolation via GRC_AGENT_ENV
# still re-inits for each fresh tmp path.
_initialized_paths: set[str] = set()

# Per-db-path "cleanup already ran" guard — same path-keying rationale as
# _initialized_paths, so test isolation via GRC_AGENT_ENV re-runs cleanup for
# each fresh tmp path rather than being skipped by a stale global flag.
_cleanup_done: set[str] = set()

# Guards the init_db check-then-add sequence. Worker threads can
# call init_db() concurrently with the main loop, so two threads could
# otherwise both pass the _initialized_paths guard and run the migrations
# concurrently.
_init_lock = threading.Lock()


def get_db_path() -> Path:
    """Resolve the SQLite database path inside `.grc_agent/`, residing in the
    same parent directory as the `.env` file."""
    base_dir = env_path().parent
    db_dir = base_dir / ".grc_agent"
    return db_dir / "chat_sessions.db"


def get_connection() -> sqlite3.Connection:
    """Open a connection with the standard desktop SQLite reliability pragmas.

    The app is single-threaded (gbulb), but session writes are dispatched via
    ``asyncio.to_thread`` (worker thread) while reads like
    ``get_recent_sessions`` run on the main loop — WAL + busy_timeout is the
    one uniform rule that keeps the two from blocking each other under the
    default rollback journal.
    """
    db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    # journal_mode persists at the DB-file level, but setting it per-connection
    # is idempotent and cheap (returns the current mode); busy_timeout and
    # foreign_keys are per-connection and MUST be set on every open.
    mode_row = conn.execute("PRAGMA journal_mode=WAL").fetchone()
    if mode_row is not None and mode_row[0] != "wal":
        _log.warning(
            "Failed to enable WAL journal mode (got %r); database may experience "
            "'database is locked' errors under concurrent access",
            mode_row[0],
        )
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


@contextmanager
def _conn():
    """A connection that is actually closed on exit.

    ``with sqlite3.connect(...) as conn`` only commits/rolls back — it does
    NOT call ``.close()``. This wrapper does, so connections are released
    deterministically instead of waiting for cyclic GC.
    """
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()


def _read_schema_version(conn: sqlite3.Connection) -> int:
    """Read the persisted schema version from `_meta`, or 0 if unset/corrupt."""
    row = conn.execute("SELECT value FROM _meta WHERE key = 'schema_version'").fetchone()
    try:
        return int(row["value"]) if row else 0
    except (ValueError, TypeError):
        return 0


def _set_schema_version(conn: sqlite3.Connection, version: int) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO _meta (key, value) VALUES ('schema_version', ?)",
        (str(version),),
    )
    conn.commit()


def _migrate_to_v1(conn: sqlite3.Connection) -> None:
    """v1: ``sessions`` table with a ``first_message`` column.

    Two-phase, fully idempotent:
    1. Ensure the ``first_message`` column exists — CREATE on a fresh DB,
       ALTER TABLE on a pre-existing v0 ``sessions`` table.
    2. Backfill any row whose ``first_message`` is empty AND whose messages
       blob is non-empty.

    The backfill runs unconditionally (not gated on whether the column was
    just added) because Python's sqlite3 module auto-commits DDL (ALTER
    TABLE) but not DML (UPDATE). If the process is killed after the ALTER
    but before the backfill UPDATEs commit, the column will be present on
    restart but unbackfilled. The idempotent backfill catches this on the
    next init. Re-runs after a successful backfill are a no-op because
    every populated row has ``first_message != ''``.
    """
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(sessions)").fetchall()}
    if not cols:
        conn.execute(
            """
            CREATE TABLE sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                grc_file_path TEXT NOT NULL,
                messages TEXT NOT NULL,
                first_message TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
    elif "first_message" not in cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN first_message TEXT NOT NULL DEFAULT ''")
    # Idempotent backfill — see method docstring for why this is outside the
    # elif. Only touches rows that still need it (first_message = '' AND
    # messages != '').
    for r in conn.execute(
        "SELECT id, messages FROM sessions WHERE first_message = '' AND messages != ''"
    ).fetchall():
        conn.execute(
            "UPDATE sessions SET first_message = ? WHERE id = ?",
            (_extract_first_user_prompt_json(r["messages"]), r["id"]),
        )


def _migrate_to_v2(conn: sqlite3.Connection) -> None:
    """v2 (superseded): `turn_traces` table. Kept so existing v0/v1 databases
    pass through the ordered migration chain to v4, which drops the table
    (replaced by pydantic-ai-harness `StepPersistence` events/snapshots)."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS turn_traces (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            run_id TEXT,
            conversation_id TEXT,
            provider TEXT,
            model TEXT,
            base_url TEXT,
            system_prompt_hash TEXT,
            user_prompt TEXT,
            origin_page_path TEXT,
            started_at REAL NOT NULL,
            ended_at REAL,
            duration_ms INTEGER,
            events TEXT NOT NULL DEFAULT '[]',
            final_output TEXT,
            error TEXT,
            input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            reasoning_tokens INTEGER NOT NULL DEFAULT 0,
            total_tokens INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_turn_traces_session ON turn_traces(session_id)")


def _migrate_to_v3(conn: sqlite3.Connection) -> None:
    """v3 (superseded): `turn_traces.generation_ms`. Kept as a no-op step so
    existing v2 databases pass through the ordered migration chain to v4,
    which drops the whole `turn_traces` table (replaced by pydantic-ai-harness
    `StepPersistence` events/snapshots)."""


def _migrate_to_v4(conn: sqlite3.Connection) -> None:
    """v4: drop `turn_traces` — per-turn observability is now owned by the
    harness `StepPersistence` capability (events/snapshots/tool_effects/runs
    tables, created by `SqliteStepStore` on the same file), grouped per chat
    session via `conversation_id = 'session-{id}'`. No bridge, no dual write."""
    conn.execute("DROP TABLE IF EXISTS turn_traces")


_MIGRATIONS = (
    _migrate_to_v1,
    _migrate_to_v2,
    _migrate_to_v3,
    _migrate_to_v4,
)


def _apply_migrations(conn: sqlite3.Connection, current: int) -> None:
    """Apply ordered, idempotent migrations to bring the schema from `current`
    to LATEST_SCHEMA_VERSION. Each step commits its own version bump so a
    crash mid-migration resumes cleanly on the next open."""
    for version, migrate in enumerate(_MIGRATIONS, start=1):
        if current < version:
            migrate(conn)
            _set_schema_version(conn, version)


def init_db() -> None:
    """Initialize the chat-sessions + turn-traces schema (idempotent per path).

    Ensures the `_meta` table exists, reads the persisted schema version, and
    applies any pending migrations. Subsequent calls short-circuit on the
    per-path ``_initialized_paths`` guard. Thread-safe via ``_init_lock`` so
    a worker-thread ``init_db()`` can't race the
    main loop's init.
    """
    db_path = str(get_db_path())
    if db_path in _initialized_paths:
        return
    with _init_lock:
        # Re-check inside the lock — another thread may have initialized
        # while we were waiting.
        if db_path in _initialized_paths:
            return
        with _conn() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS _meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            current = _read_schema_version(conn)
            if current < LATEST_SCHEMA_VERSION:
                _apply_migrations(conn, current)
            # The StepPersistence tables are created by SqliteStepStore on
            # first use; guard the sweep so it never runs against a file that
            # has none yet (fresh DB before the first agent turn).
            if _step_tables_exist(conn):
                _sweep_orphan_step_rows(conn)
                conn.commit()
        _initialized_paths.add(db_path)


def conversation_id_for_session(session_id: int) -> str:
    """The StepPersistence conversation id grouping every run of one chat
    session. Single source of truth — the sidebar passes it to `Agent.iter()`
    and the cleanup SQL below matches it."""
    return f"session-{session_id}"


_step_stores: dict[str, SqliteStepStore] = {}


def get_step_store() -> SqliteStepStore:
    """The process-wide `SqliteStepStore` co-located on `chat_sessions.db`.

    One store per resolved db path (so test isolation via `GRC_AGENT_ENV`
    gets a fresh store per tmp path), shared across agent live-swaps — the
    store outlives any single `Agent`. `max_snapshots_per_run=None` keeps
    EVERY settled tool-boundary snapshot (D3: ConversationSearch's
    `SnapshotHistorySource` recovers the union of a run's snapshots, so the
    pre-compaction originals must survive a mid-turn SummarizingCompaction —
    a 2-snapshot cap could leave only post-compact copies and permanently
    lose what the summary dropped). Media externalization (64 KiB
    threshold) keeps big tool-return blobs deduplicated in the sibling
    `media` table instead of repeated inside every snapshot row."""
    key = str(get_db_path())
    store = _step_stores.get(key)
    if store is None:
        init_db()
        store = SqliteStepStore(database=key, max_snapshots_per_run=None)
        _step_stores[key] = store
    return store


def _step_tables_exist(conn: sqlite3.Connection) -> bool:
    """The StepPersistence tables are created lazily on the store's first
    write — a fresh DB (or one whose sessions predate the capability) has no
    `runs` table, and every step-row cascade is a no-op there."""
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    return "runs" in tables


def _delete_step_rows_for_conversations(conn: sqlite3.Connection, conv_ids: list[str]) -> None:
    """Delete every StepPersistence row belonging to the given conversation
    ids (runs + their events/snapshots/tool_effects children). The `media`
    table is deliberately untouched — blobs are content-addressed and shared
    across snapshots, so orphan GC is out of scope (same call the upstream
    store makes). Runs with no session row are handled by the init-time
    orphan sweep, not this targeted delete."""
    if not conv_ids:
        return
    if not _step_tables_exist(conn):
        return
    placeholders = ",".join("?" for _ in conv_ids)
    run_ids = f"SELECT run_id FROM runs WHERE conversation_id IN ({placeholders})"
    for table in ("snapshots", "events", "tool_effects"):
        conn.execute(f"DELETE FROM {table} WHERE run_id IN ({run_ids})", conv_ids)
    conn.execute(f"DELETE FROM runs WHERE conversation_id IN ({placeholders})", conv_ids)


def _sweep_orphan_step_rows(conn: sqlite3.Connection) -> None:
    """Delete step rows grouped under a `session-{id}` conversation whose
    session row no longer exists — e.g. a Clear History that raced an
    in-flight turn (the capability keeps writing snapshots for the old
    conversation id after the sweep, so this also runs at init). One uniform
    rule: only `session-*` ids are candidates; rows with other or NULL
    conversation ids (ungrouped runs) are never touched."""
    orphans = [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT conversation_id FROM runs "
            "WHERE conversation_id LIKE 'session-%' "
            "AND conversation_id NOT IN (SELECT 'session-' || id FROM sessions)"
        ).fetchall()
    ]
    _delete_step_rows_for_conversations(conn, orphans)


def _cleanup_invalid_sessions() -> None:
    """Delete any corrupted database sessions where the path is a directory or empty."""
    with _conn() as conn:
        for r in conn.execute("SELECT id, grc_file_path FROM sessions").fetchall():
            p = r["grc_file_path"]
            if not p:
                conn.execute("DELETE FROM sessions WHERE id = ?", (r["id"],))
            else:
                try:
                    path_obj = Path(p)
                    if path_obj.exists() and path_obj.is_dir():
                        conn.execute("DELETE FROM sessions WHERE id = ?", (r["id"],))
                except Exception:
                    pass
        conn.commit()


def get_recent_sessions(limit: int = 10) -> list[dict[str, Any]]:
    """Load recently active GRC flowgraph sessions, newest first, filtered to
    paths still on disk. Bounded by a SQL LIMIT. The `first_message` column is
    read directly — no per-row messages-blob deserialization on the hot path
    (the column is populated at save_session time)."""
    init_db()
    db_path = str(get_db_path())
    if db_path not in _cleanup_done:
        _cleanup_invalid_sessions()
        _cleanup_done.add(db_path)

    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, grc_file_path, first_message, created_at, updated_at "
            "FROM sessions ORDER BY updated_at DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()

    res = []
    for r in rows:
        path_str = r["grc_file_path"]
        try:
            path_obj = Path(path_str)
            exists_and_file = path_obj.exists() and path_obj.is_file()
        except Exception:
            exists_and_file = False

        if exists_and_file:
            res.append(
                {
                    "id": r["id"],
                    "grc_file_path": path_str,
                    "first_message": r["first_message"] or "",
                    "created_at": r["created_at"],
                    "updated_at": r["updated_at"],
                }
            )
    return res


def load_session(session_id: int) -> dict[str, Any] | None:
    """Load a session by its ID."""
    init_db()
    with _conn() as conn:
        row = conn.execute(
            "SELECT id, grc_file_path, messages, first_message, created_at, updated_at "
            "FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
    if row:
        return {
            "id": row["id"],
            "grc_file_path": row["grc_file_path"],
            "messages": row["messages"],
            "first_message": row["first_message"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
    return None


def serialize_messages(messages: list[ModelMessage]) -> str:
    """Serialize Pydantic AI ModelMessages to a JSON string using the
    library's sanctioned one-step TypeAdapter (``dump_json``). This replaces
    the previous ``json.dumps(to_jsonable_python(...))`` double conversion and
    preserves every part field — including ``ThinkingPart`` (reasoning),
    ``ToolCallPart``/``ToolReturnPart``, ``ModelResponse.usage`` (incl.
    ``reasoning_tokens``), and ``run_id``/``conversation_id`` — exactly."""
    return ModelMessagesTypeAdapter.dump_json(messages).decode("utf-8")


def deserialize_messages(messages_json: str) -> list[ModelMessage]:
    """Deserialize a JSON string back to Pydantic AI ModelMessages via the
    library's sanctioned ``validate_json`` (single step).

    A malformed/incompatible payload (e.g. saved by a different pydantic-ai
    version) logs a warning and returns an empty list rather than raising —
    but the failure is surfaced in the log instead of silently presenting an
    empty chat indistinguishable from a brand-new one.
    """
    if not messages_json.strip():
        return []
    try:
        return ModelMessagesTypeAdapter.validate_json(messages_json)
    except Exception as e:
        _log.warning("Failed to deserialize chat session messages: %s", e, exc_info=True)
        return []


def _first_user_prompt(messages: list[ModelMessage]) -> str:
    """Extract the first user prompt's text from a list of ModelMessages.

    Used at save time to populate the `first_message` column (one rule, no
    per-row deserialize on the read path). Returns "" if there is no user
    prompt yet (e.g. an empty session just created to pin an id)."""
    for m in messages:
        for part in getattr(m, "parts", []):
            if part.__class__.__name__ != "UserPromptPart" or not part.content:
                continue
            content = part.content
            if not isinstance(content, str):
                pieces = []
                for item in content:
                    if hasattr(item, "text"):
                        pieces.append(item.text)
                    elif isinstance(item, str):
                        pieces.append(item)
                content = "".join(pieces)
            return content
    return ""


def _extract_first_user_prompt_json(messages_json: str) -> str:
    """Backfill helper used by the v0→v1 migration: extract the first user
    prompt from a stored messages blob. Best-effort — ``deserialize_messages``
    already catches all exceptions internally and returns ``[]`` on a malformed
    blob, so this never raises; a corrupt row yields ``""`` rather than
    blocking the migration."""
    return _first_user_prompt(deserialize_messages(messages_json))


def _prune_in(conn: sqlite3.Connection, keep: int = _MAX_SESSIONS) -> None:
    """Evict the oldest sessions beyond ``keep`` (by updated_at then id) using
    an already-open connection, taking each evicted session's StepPersistence
    rows (runs/events/snapshots/tool_effects) with it. Bounds the tables'
    growth; the deleted rows are the long-tail a user is unlikely to scroll
    back to."""
    evicted = [
        r[0]
        for r in conn.execute(
            "SELECT id FROM sessions WHERE id NOT IN ("
            "SELECT id FROM sessions ORDER BY updated_at DESC, id DESC LIMIT ?)",
            (keep,),
        ).fetchall()
    ]
    if evicted:
        _delete_step_rows_for_conversations(conn, [conversation_id_for_session(i) for i in evicted])
        conn.execute(
            "DELETE FROM sessions WHERE id IN (" + ",".join("?" for _ in evicted) + ")",
            evicted,
        )
    conn.commit()


def save_session(
    session_id: int | None, grc_file_path: str, messages: list[ModelMessage]
) -> int | None:
    """Save the session to SQLite. If session_id is None, inserts a new row
    and returns its id. If session_id is provided and still exists, updates
    it and returns the same id.

    If session_id is provided but no longer exists — e.g. a per-row delete
    (``_on_delete_recent_session``) or a global Clear History raced an
    in-flight save dispatched before the deletion — the save is skipped
    entirely rather than falling through to an INSERT, which used to
    silently resurrect the deleted session under a new row id. Returns None
    in that case so callers can tell "skipped" apart from a real save.

    `first_message` is extracted from `messages` once here so the
    recent-sessions list can read it as a plain column instead of
    re-deserializing the whole messages blob per rendered row.
    """
    init_db()
    messages_str = serialize_messages(messages)
    first_msg = _first_user_prompt(messages)
    abs_path = str(Path(grc_file_path).resolve())
    with _conn() as conn:
        if session_id is not None:
            row = conn.execute("SELECT 1 FROM sessions WHERE id = ?", (session_id,)).fetchone()
            if row:
                conn.execute(
                    "UPDATE sessions SET grc_file_path = ?, messages = ?, first_message = ?, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (abs_path, messages_str, first_msg, session_id),
                )
                conn.commit()
                _prune_in(conn)
                return session_id
            _log.warning(
                "save_session: session %s no longer exists (deleted concurrently?) "
                "— skipping save instead of resurrecting it under a new id",
                session_id,
            )
            return None
        cursor = conn.execute(
            "INSERT INTO sessions (grc_file_path, messages, first_message) VALUES (?, ?, ?)",
            (abs_path, messages_str, first_msg),
        )
        conn.commit()
        new_id = cursor.lastrowid
        _prune_in(conn)
    return new_id


def delete_session(session_id: int) -> None:
    """Delete a session from SQLite, together with its StepPersistence rows
    (runs/events/snapshots/tool_effects for `conversation_id =
    'session-{id}'`)."""
    init_db()
    with _conn() as conn:
        _delete_step_rows_for_conversations(conn, [conversation_id_for_session(session_id)])
        conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        conn.commit()


def delete_all_sessions() -> None:
    """Delete every saved session. Used by the toolbar 'Clear History' button,
    which clears the whole recent-sessions list the user sees — independent of
    which flowgraph (if any) is active. Per-session deletion stays available via
    the per-row delete buttons (delete_session). All StepPersistence rows for
    `session-*` conversations go with them; ungrouped runs (NULL conversation
    id) are left alone, and content-addressed `media` blobs are shared and
    deliberately kept."""
    init_db()
    with _conn() as conn:
        convs = []
        if _step_tables_exist(conn):
            convs = [
                r[0]
                for r in conn.execute(
                    "SELECT DISTINCT conversation_id FROM runs WHERE conversation_id LIKE 'session-%'"
                ).fetchall()
            ]
        _delete_step_rows_for_conversations(conn, convs)
        conn.execute("DELETE FROM sessions")
        conn.commit()
