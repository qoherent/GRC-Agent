import logging
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic_ai import ModelMessagesTypeAdapter
from pydantic_ai.messages import ModelMessage, UserPromptPart
from pydantic_ai_harness.planning import PlanItem, SqlitePlanStore
from pydantic_ai_harness.step_persistence import (
    ContinuableSnapshot,
    RunRecord,
    SqliteStepStore,
)

from .settings import env_path

_log = logging.getLogger(__name__)

# Generous cap so the sessions table cannot grow without limit. The previous
# JSON-file store bounded itself to 10 on write; this only prunes well outside
# the recent window a user would reasonably scroll back to.
_MAX_SESSIONS = 200

# Per-db-path "already initialized" guard. init_db() is idempotent, but the
# guard avoids re-running the schema/table-existence probes on every call.
# Keyed on the resolved db path so test isolation via GRC_AGENT_ENV still
# re-inits for each fresh tmp path.
_initialized_paths: set[str] = set()

# Guards the init_db check-then-add sequence. Worker threads can
# call init_db() concurrently with the main loop, so two threads could
# otherwise both pass the _initialized_paths guard and run the
# CREATE TABLE IF NOT EXISTS + orphan sweeps concurrently.
_init_lock = threading.Lock()


def get_db_path() -> Path:
    """Resolve the SQLite database path inside `.grc_agent/`, residing in the
    same parent directory as the `.env` file."""
    base_dir = env_path().parent
    db_dir = base_dir / ".grc_agent"
    return db_dir / "chat_sessions.db"


async def load_plan_items(session_id: int) -> list[PlanItem]:
    """Read one saved chat's durable plan through the harness-owned store."""
    init_db()
    store = SqlitePlanStore(
        str(get_db_path()),
        session=conversation_id_for_session(session_id),
    )
    return await store.get_items()


def get_connection() -> sqlite3.Connection:
    """Open a connection with the standard desktop SQLite reliability pragmas.

    The app is single-threaded (gbulb), but session writes are dispatched via
    ``asyncio.to_thread`` (worker thread) while reads like
    ``get_recent_sessions`` run on the main loop — WAL is what keeps the two
    from blocking each other under the default rollback journal, paired with
    the 5s busy timeout sqlite3.connect() applies by default.
    """
    db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    # journal_mode persists at the DB-file level, but setting it per-connection
    # is idempotent and cheap (returns the current mode). The busy timeout is
    # per-connection and comes from sqlite3.connect()'s own `timeout=5.0`
    # default, which calls sqlite3_busy_timeout(5000) — an explicit
    # `PRAGMA busy_timeout=5000` here only re-set the same value (verified).
    mode_row = conn.execute("PRAGMA journal_mode=WAL").fetchone()
    if mode_row is not None and mode_row[0] != "wal":
        _log.warning(
            "Failed to enable WAL journal mode (got %r); database may experience "
            "'database is locked' errors under concurrent access",
            mode_row[0],
        )
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


def init_db() -> None:
    """Initialize the SQLite schema directly (idempotent per path).

    Creates the `sessions` table and its recency index, and sweeps orphan
    rows if harness-managed step or plan tables exist. Thread-safe via
    `_init_lock`.
    """
    db_path = str(get_db_path())
    if db_path in _initialized_paths:
        return
    with _init_lock:
        if db_path in _initialized_paths:
            return
        with _conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    grc_file_path TEXT NOT NULL,
                    messages TEXT NOT NULL,
                    first_message TEXT NOT NULL DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            # Covers both reads that sort the session list: get_recent_sessions
            # and _prune_in (which runs on every save). Without it SQLite plans
            # each as `SCAN sessions` + `USE TEMP B-TREE FOR ORDER BY` — the
            # column order here matches the ORDER BY exactly so the sort is
            # answered from the index.
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sessions_recent "
                "ON sessions(updated_at DESC, id DESC)"
            )

            # Harness-owned tables are created lazily by their respective
            # stores. Guard each sweep so a fresh DB remains valid before the
            # first agent or planning operation.
            if _step_tables_exist(conn):
                _sweep_orphan_step_rows(conn)
            if _plan_table_exists(conn):
                _sweep_orphan_plan_rows(conn)
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


async def archive_transcript(
    messages: list[ModelMessage],
    *,
    conversation_id: str,
    agent_name: str,
    kind: str,
    step_index: int = 0,
) -> str:
    """Persist `messages` as a standalone run in the shared step store.

    Used wherever a history is about to be replaced and the original must stay
    recoverable by ConversationSearch — automatic compaction, the manual Compact
    button, and the truncated-thinking archive. All three previously inlined the
    same register_run + save_snapshot pair and each re-derived StepPersistence's
    own run-id shape (`{agent_name}-{8-hex}`) by hand from a comment; this is now
    the single place that replicates it, and `kind` labels the run consistently
    in both the id and the metadata.

    Returns the run id. Deliberately does not catch: a store failure must reach
    the caller, since compaction would otherwise destroy the only durable copy.
    """
    store = get_step_store()
    run_id = f"{agent_name}-{kind}-{uuid4().hex[:8]}"
    await store.register_run(
        RunRecord(
            run_id=run_id,
            conversation_id=conversation_id,
            agent_name=agent_name,
            metadata={"kind": kind},
        )
    )
    await store.save_snapshot(
        ContinuableSnapshot(
            run_id=run_id,
            step_index=step_index,
            messages=messages,
            conversation_id=conversation_id,
            agent_name=agent_name,
        )
    )
    return run_id


def _step_tables_exist(conn: sqlite3.Connection) -> bool:
    """The StepPersistence tables are created lazily on the store's first
    write — a fresh DB (or one whose sessions predate the capability) has no
    `runs` table, and every step-row cascade is a no-op there."""
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    return "runs" in tables


def _plan_table_exists(conn: sqlite3.Connection) -> bool:
    """Whether the harness-owned, lazily-created plan table exists."""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        ("plan_items",),
    ).fetchone()
    return row is not None


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


def _delete_plan_rows_for_conversations(
    conn: sqlite3.Connection, conv_ids: list[str]
) -> None:
    """Delete durable plans belonging to the given chat conversations."""
    if not conv_ids or not _plan_table_exists(conn):
        return
    placeholders = ",".join("?" for _ in conv_ids)
    conn.execute(f"DELETE FROM plan_items WHERE session IN ({placeholders})", conv_ids)


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


def _sweep_orphan_plan_rows(conn: sqlite3.Connection) -> None:
    """Delete ``session-*`` plans whose owning chat session is gone."""
    if not _plan_table_exists(conn):
        return
    conn.execute(
        "DELETE FROM plan_items WHERE session LIKE ? "
        "AND session NOT IN (SELECT 'session-' || id FROM sessions)",
        ("session-%",),
    )


def get_recent_sessions(limit: int = 10) -> list[dict[str, Any]]:
    """Load recently active GRC flowgraph sessions, newest first, filtered to
    paths still on disk. Bounded by a SQL LIMIT. The `first_message` column is
    read directly — no per-row messages-blob deserialization on the hot path
    (the column is populated at save_session time)."""
    init_db()

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


def user_prompt_text(part: UserPromptPart) -> str:
    """Flatten a `UserPromptPart`'s content to plain text.

    `content` is `str | Sequence[UserContent]`, so a multimodal prompt has to be
    reduced to its text pieces. Pydantic AI exposes no accessor for this
    (`user_text_prompt` is a constructor, not a getter), so this is the one
    implementation — the sidebar's history renderer and `_first_user_prompt`
    below both used to carry their own copy of it.
    """
    content = part.content
    if isinstance(content, str):
        return content
    return "".join(
        item if isinstance(item, str) else getattr(item, "text", "") for item in content
    )


def _first_user_prompt(messages: list[ModelMessage]) -> str:
    """Extract the first user prompt's text from a list of ModelMessages.

    Used at save time to populate the `first_message` column (one rule, no
    per-row deserialize on the read path). Returns "" if there is no user
    prompt yet (e.g. an empty session just created to pin an id)."""
    for m in messages:
        for part in getattr(m, "parts", []):
            if isinstance(part, UserPromptPart) and part.content:
                return user_prompt_text(part)
    return ""


def _prune_in(conn: sqlite3.Connection, keep: int = _MAX_SESSIONS) -> None:
    """Evict the oldest sessions beyond ``keep`` (by updated_at then id) using
    an already-open connection, taking each evicted session's StepPersistence
    rows (runs/events/snapshots/tool_effects) and durable plan with it. Bounds
    the tables' growth; the deleted rows are the long-tail a user is unlikely
    to scroll back to."""
    evicted = [
        r[0]
        for r in conn.execute(
            "SELECT id FROM sessions WHERE id NOT IN ("
            "SELECT id FROM sessions ORDER BY updated_at DESC, id DESC LIMIT ?)",
            (keep,),
        ).fetchall()
    ]
    if evicted:
        conversations = [conversation_id_for_session(i) for i in evicted]
        _delete_step_rows_for_conversations(conn, conversations)
        _delete_plan_rows_for_conversations(conn, conversations)
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
    """Delete a session with its StepPersistence rows and durable plan."""
    init_db()
    with _conn() as conn:
        conversation = conversation_id_for_session(session_id)
        _delete_step_rows_for_conversations(conn, [conversation])
        _delete_plan_rows_for_conversations(conn, [conversation])
        conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        conn.commit()


def delete_all_sessions() -> None:
    """Delete every saved session. Used by the toolbar 'Clear History' button,
    which clears the whole recent-sessions list the user sees — independent of
    which flowgraph (if any) is active. Per-session deletion stays available via
    the per-row delete buttons (delete_session). All StepPersistence rows and
    durable plans for `session-*` conversations go with them; ungrouped runs
    and non-session plans are left alone, and content-addressed `media` blobs
    are shared and deliberately kept."""
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
        if _plan_table_exists(conn):
            conn.execute("DELETE FROM plan_items WHERE session LIKE ?", ("session-%",))
        conn.execute("DELETE FROM sessions")
        conn.commit()
