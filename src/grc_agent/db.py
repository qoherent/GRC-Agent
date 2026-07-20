import json
import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from pydantic_ai import ModelMessagesTypeAdapter
from pydantic_ai.messages import ModelMessage
from pydantic_core import to_jsonable_python

from .settings import env_path

_log = logging.getLogger(__name__)

# Generous cap so the sessions table cannot grow without limit. The previous
# JSON-file store bounded itself to 10 on write; this only prunes well outside
# the recent window a user would reasonably scroll back to.
_MAX_SESSIONS = 200

# Per-db-path "already initialized" guard. init_db() issues an idempotent
# CREATE TABLE IF NOT EXISTS, but skipping the connection + statement entirely
# after the first init per path avoids re-opening a second connection on every
# single db call (the DB-4 double-open). Keyed on the resolved db path so test
# isolation via GRC_AGENT_ENV still re-inits for each fresh tmp path.
_initialized_paths: set[str] = set()

# Set when a session row is written; cleared once _cleanup_invalid_sessions
# has run. Avoids a full-table Path stat sweep on every get_recent_sessions()
# render — cleanup now runs once on first access and only again after a write.
_cleanup_needed: bool = True


def get_db_path() -> Path:
    """Resolve the SQLite database path, residing in the same directory as the .env file."""
    return env_path().parent / "chat_sessions.db"


def get_connection() -> sqlite3.Connection:
    """Get a connection to the SQLite database with Row factory enabled."""
    db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
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
    """Initialize the sessions database table (idempotent per db path)."""
    db_path = str(get_db_path())
    if db_path in _initialized_paths:
        return
    with _conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                grc_file_path TEXT NOT NULL,
                messages TEXT NOT NULL,
                last_message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Migrate pre-last_message databases: add the column and backfill the
        # preview from each existing messages blob (one-time cost at first open).
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(sessions)").fetchall()}
        if "last_message" not in cols:
            conn.execute("ALTER TABLE sessions ADD COLUMN last_message TEXT")
            for r in conn.execute("SELECT id, messages FROM sessions").fetchall():
                conn.execute(
                    "UPDATE sessions SET last_message = ? WHERE id = ?",
                    (_extract_last_message(r["messages"]), r["id"]),
                )
        conn.commit()
    _initialized_paths.add(db_path)


def _cleanup_invalid_sessions() -> None:
    """Delete any corrupted database sessions where the path is a directory or empty."""
    with _conn() as conn:
        all_rows = conn.execute("SELECT id, grc_file_path FROM sessions").fetchall()
        for r in all_rows:
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
    paths still on disk. Bounded by a SQL LIMIT."""
    init_db()
    global _cleanup_needed
    if _cleanup_needed:
        _cleanup_invalid_sessions()
        _cleanup_needed = False

    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, grc_file_path, last_message, created_at, updated_at "
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
            res.append({
                "id": r["id"],
                "grc_file_path": path_str,
                "last_message": r["last_message"] or "",
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
            })
    return res


def load_session(session_id: int) -> dict[str, Any] | None:
    """Load a session by its ID."""
    init_db()
    with _conn() as conn:
        row = conn.execute(
            "SELECT id, grc_file_path, messages, created_at, updated_at FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
    if row:
        return {
            "id": row["id"],
            "grc_file_path": row["grc_file_path"],
            "messages": row["messages"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
    return None


def serialize_messages(messages: list[ModelMessage]) -> str:
    """Serialize Pydantic AI ModelMessages to a JSON string."""
    return json.dumps(to_jsonable_python(messages))


def deserialize_messages(messages_json: str) -> list[ModelMessage]:
    """Deserialize a JSON string back to Pydantic AI ModelMessages.

    A malformed/incompatible payload (e.g. saved by a different pydantic-ai
    version) logs a warning and returns an empty list rather than raising — but
    the failure is surfaced in the log instead of silently presenting an empty
    chat indistinguishable from a brand-new one.
    """
    if not messages_json.strip():
        return []
    try:
        data = json.loads(messages_json)
        return ModelMessagesTypeAdapter.validate_python(data)
    except Exception as e:
        _log.warning("Failed to deserialize chat session messages: %s", e, exc_info=True)
        return []


def _extract_last_message(messages_json: str) -> str:
    """Extract the most recent user/assistant text from a session's serialized
    messages, for the recent-sessions preview — so get_recent_sessions() need
    not deserialize the full history of every row on each render."""
    try:
        msgs = deserialize_messages(messages_json)
        for m in reversed(msgs):
            for part in reversed(getattr(m, "parts", [])):
                cls_name = part.__class__.__name__
                if cls_name in ("UserPromptPart", "TextPart") and part.content:
                    return part.content
    except Exception:
        pass
    return ""


def _prune_in(conn: sqlite3.Connection, keep: int = _MAX_SESSIONS) -> None:
    """Evict the oldest sessions beyond ``keep`` (by updated_at then id) using
    an already-open connection. Bounds the table's growth; the deleted rows are
    the long-tail a user is unlikely to scroll back to."""
    conn.execute(
        "DELETE FROM sessions WHERE id NOT IN ("
        "SELECT id FROM sessions ORDER BY updated_at DESC, id DESC LIMIT ?)",
        (keep,),
    )
    conn.commit()


def prune_sessions(keep: int = _MAX_SESSIONS) -> None:
    """Evict the oldest sessions beyond ``keep``. Standalone entry point that
    opens its own connection; ``save_session`` prunes within its own connection
    to avoid a second open."""
    init_db()
    with _conn() as conn:
        _prune_in(conn, keep)


def save_session(
    session_id: int | None, grc_file_path: str, messages: list[ModelMessage]
) -> int | None:
    """Save the session to SQLite. If session_id is None, inserts a new row
    and returns its id. If session_id is provided and still exists, updates
    it and returns the same id.

    If session_id is provided but no longer exists — e.g. a per-row delete
    (`_on_delete_recent_session`) or a global Clear History raced an
    in-flight save dispatched before the deletion — the save is skipped
    entirely rather than falling through to an INSERT, which used to
    silently resurrect the deleted session under a new row id. Returns None
    in that case so callers can tell "skipped" apart from a real save.
    """
    init_db()
    global _cleanup_needed
    _cleanup_needed = True
    messages_str = serialize_messages(messages)
    last_message = _extract_last_message(messages_str)
    abs_path = str(Path(grc_file_path).resolve())
    with _conn() as conn:
        if session_id is not None:
            row = conn.execute("SELECT 1 FROM sessions WHERE id = ?", (session_id,)).fetchone()
            if row:
                conn.execute(
                    "UPDATE sessions SET grc_file_path = ?, messages = ?, last_message = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (abs_path, messages_str, last_message, session_id),
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
            "INSERT INTO sessions (grc_file_path, messages, last_message) VALUES (?, ?, ?)",
            (abs_path, messages_str, last_message),
        )
        conn.commit()
        new_id = cursor.lastrowid
        _prune_in(conn)
    return new_id


def delete_session(session_id: int) -> None:
    """Delete a session from SQLite."""
    init_db()
    with _conn() as conn:
        conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        conn.commit()


def delete_all_sessions() -> None:
    """Delete every saved session. Used by the toolbar 'Clear History' button,
    which clears the whole recent-sessions list the user sees — independent of
    which flowgraph (if any) is active. Per-session deletion stays available via
    the per-row delete buttons (delete_session)."""
    init_db()
    with _conn() as conn:
        conn.execute("DELETE FROM sessions")
        conn.commit()
