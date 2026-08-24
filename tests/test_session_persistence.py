"""Tests for chat-session persistence and the StepPersistence durability layer.

No LLM, no GUI — fast and hermetic (each test redirects GRC_AGENT_ENV to a
fresh tmp path so db.py's per-path init guard re-inits cleanly).

Session persistence (messages blob via the library's ModelMessagesTypeAdapter,
first_message preview column, WAL) is unchanged. The former
hand-rolled `turn_traces` layer is replaced by pydantic-ai-harness
`StepPersistence` (runs/events/snapshots/tool_effects on the same DB file,
grouped per chat session via `conversation_id = 'session-{id}'`).
"""

import logging
import sqlite3
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path, monkeypatch):
    """Point GRC_AGENT_ENV at a fresh tmp dir so every test gets a clean DB.
    Also resets db.py's per-path init/cleanup guards so the new path re-inits
    (test isolation convention used throughout the suite)."""
    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))
    from grc_agent import db

    db._initialized_paths.clear()
    db._step_stores.clear()
    yield


def _open_raw_connection():
    """A raw sqlite3 connection to the same DB file, WITHOUT going through
    db.get_connection() — used to independently verify PRAGMAs are persisted
    at the file level (journal_mode) and that the schema is on disk."""
    from grc_agent.db import get_db_path

    return sqlite3.connect(str(get_db_path()))


# ==========================================
# Phase 1: db.py reliability + first_message
# ==========================================


def test_wal_and_busy_timeout_applied():
    from grc_agent.db import get_connection, init_db

    init_db()  # ensures the DB exists
    with get_connection() as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        busy = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    assert mode == "wal"
    assert busy == 5000


def test_journal_mode_persists_at_file_level():
    """journal_mode is a DB-file setting; once set via get_connection it should
    persist even on a raw connection that didn't issue the PRAGMA itself."""
    from grc_agent.db import get_connection, init_db

    init_db()
    with get_connection() as _:
        pass  # triggers the PRAGMA once
    with _open_raw_connection() as raw:
        mode = raw.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"


def test_schema_tables_present_after_init():
    from grc_agent.db import init_db

    init_db()
    with _open_raw_connection() as raw:
        tables = {r[0] for r in raw.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        indexes = {r[0] for r in raw.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
    assert "sessions" in tables
    assert "idx_sessions_recent" in indexes


def test_sessions_recency_index_answers_the_order_by():
    """The session list is sorted on every render and pruned on every save.
    Without idx_sessions_recent both plan as a full scan plus a temp B-tree —
    assert the index actually answers the sort instead."""
    from grc_agent.db import init_db

    init_db()
    with _open_raw_connection() as raw:
        for sql in (
            "SELECT id, grc_file_path, first_message, created_at, updated_at "
            "FROM sessions ORDER BY updated_at DESC, id DESC LIMIT 20",
            "SELECT id FROM sessions WHERE id NOT IN ("
            "SELECT id FROM sessions ORDER BY updated_at DESC, id DESC LIMIT 200)",
        ):
            plan = " ".join(str(r[-1]) for r in raw.execute("EXPLAIN QUERY PLAN " + sql))
            assert "TEMP B-TREE" not in plan, f"unindexed sort: {plan}"
            assert "idx_sessions_recent" in plan, f"index unused: {plan}"


def test_init_db_is_idempotent():
    from grc_agent.db import init_db

    init_db()
    init_db()  # second call must not raise
    init_db()
    with _open_raw_connection() as raw:
        tables = {r[0] for r in raw.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        indexes = {r[0] for r in raw.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
    assert "sessions" in tables
    assert "idx_sessions_recent" in indexes


def test_first_message_populated_at_save_and_read_directly(tmp_path):
    """save_session must populate first_message so get_recent_sessions reads
    it as a column instead of re-deserializing the whole messages blob."""
    from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

    from grc_agent.db import get_recent_sessions, load_session, save_session

    f = tmp_path / "g.grc"
    f.touch()
    msgs = [
        ModelRequest(parts=[UserPromptPart(content="hello world first prompt")]),
        ModelResponse(parts=[TextPart(content="reply")]),
    ]
    sid = save_session(None, str(f), msgs)

    row = load_session(sid)
    assert row["first_message"] == "hello world first prompt"

    recent = get_recent_sessions()
    assert len(recent) == 1
    assert recent[0]["first_message"] == "hello world first prompt"
    # The hot-path row must NOT include the heavy messages blob
    assert "messages" not in recent[0]


def test_first_message_empty_for_session_with_no_user_prompt(tmp_path):
    from grc_agent.db import save_session

    f = tmp_path / "g.grc"
    f.touch()
    sid = save_session(None, str(f), [])
    from grc_agent.db import load_session

    assert load_session(sid)["first_message"] == ""


def test_first_message_updates_when_first_prompt_changes(tmp_path):
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    from grc_agent.db import load_session, save_session

    f = tmp_path / "g.grc"
    f.touch()
    sid = save_session(None, str(f), [ModelRequest(parts=[UserPromptPart(content="first")])])
    assert load_session(sid)["first_message"] == "first"

    save_session(
        sid,
        str(f),
        [
            ModelRequest(parts=[UserPromptPart(content="first")]),
            ModelRequest(parts=[UserPromptPart(content="second")]),
        ],
    )
    # Still the FIRST user prompt
    assert load_session(sid)["first_message"] == "first"


def test_serialize_deserialize_roundtrip_preserves_thinking_part():
    """The builtin dump_json/validate_json path must preserve ThinkingPart
    (reasoning) — the previous to_jsonable_python path did, and this refactor
    must not regress it."""
    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        TextPart,
        ThinkingPart,
        UserPromptPart,
    )

    from grc_agent.db import deserialize_messages, serialize_messages

    msgs = [
        ModelRequest(parts=[UserPromptPart(content="hi")]),
        ModelResponse(
            parts=[
                ThinkingPart(content="internal reasoning text"),
                TextPart(content="final answer"),
            ]
        ),
    ]
    s = serialize_messages(msgs)
    restored = deserialize_messages(s)
    assert len(restored) == 2
    assert restored[1].parts[0].__class__.__name__ == "ThinkingPart"
    assert restored[1].parts[0].content == "internal reasoning text"
    assert restored[1].parts[1].content == "final answer"


def test_deserialize_messages_logs_on_malformed_json(caplog):
    from grc_agent.db import deserialize_messages

    with caplog.at_level(logging.WARNING):
        result = deserialize_messages("{not valid json")
    assert result == []
    assert any(
        "deserialize" in r.message.lower() or "failed" in r.message.lower() for r in caplog.records
    )


def test_deserialize_empty_string_returns_empty_list():
    from grc_agent.db import deserialize_messages

    assert deserialize_messages("") == []
    assert deserialize_messages("   \n  ") == []


def test_no_legacy_migration_path_remains():
    """AGENTS.md: 'No Backward Compatibility'. The legacy chat_sessions.db
    -> .grc_agent/chat_sessions.db migration must be gone."""
    from grc_agent import db

    src = Path(db.__file__).read_text()
    assert "legacy_path" not in src, "legacy migration block must be removed"
    assert "legacy" not in src.lower(), "no stray 'legacy' references"


# ==========================================
# Phase 2: StepPersistence durability layer (replaces turn_traces)
# ==========================================


def _run_one_turn(store, text="hello"):
    """Drive one real TestModel turn through an agent wired exactly like the
    interactive one's persistence stack (StepPersistence on the shared store),
    under a fixed session-scoped conversation id. Returns the run record."""
    import asyncio

    from pydantic_ai import Agent
    from pydantic_ai.messages import ModelRequest, UserPromptPart
    from pydantic_ai.models.test import TestModel
    from pydantic_ai_harness.step_persistence import StepPersistence

    from grc_agent.db import save_session

    sid = save_session(None, "/tmp/step.grc", [ModelRequest(parts=[UserPromptPart(content="p")])])
    from grc_agent.db import conversation_id_for_session

    agent = Agent(
        TestModel(),
        capabilities=[StepPersistence(store=store, agent_name="grc_chat")],
    )

    async def _go():
        async with agent.iter(text, conversation_id=conversation_id_for_session(sid)) as run:
            async for _node in run:
                pass

    asyncio.run(_go())
    runs = asyncio.run(store.list_runs(conversation_id=conversation_id_for_session(sid)))
    assert runs, "turn must record a run"
    return sid, runs


def test_step_persistence_records_runs_events_and_snapshot():
    """One turn = one run row (grouped under session-{id}) with boundary
    events and a resumable snapshot — the durability layer turn_traces used
    to approximate by hand."""
    from grc_agent.db import get_step_store

    store = get_step_store()
    sid, runs = _run_one_turn(store)
    run = runs[0]
    assert run.agent_name == "grc_chat"
    assert run.conversation_id == f"session-{sid}"

    import asyncio

    events = asyncio.run(store.list_events(run_id=run.run_id))
    kinds = [e.kind for e in events]
    assert kinds[0] == "run_started"
    assert "model_request_completed" in kinds
    assert kinds[-1] == "run_completed"

    from pydantic_ai_harness.step_persistence import continue_run

    history = asyncio.run(continue_run(store, run_id=run.run_id))
    assert len(history) >= 2, "snapshot must carry the turn's messages"


def test_step_tables_created_on_shared_db_file():
    """The store co-locates runs/events/snapshots/tool_effects on
    chat_sessions.db (tables are created lazily on the first store write),
    and the schema never creates the hand-rolled turn_traces table."""
    from grc_agent.db import get_step_store

    store = get_step_store()
    _run_one_turn(store)  # first write creates the tables
    with _open_raw_connection() as raw:
        tables = {r[0] for r in raw.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"runs", "events", "snapshots", "tool_effects"} <= tables
    assert "turn_traces" not in tables, "the hand-rolled trace table must never exist"


def test_delete_session_cascades_step_rows():
    from grc_agent.db import delete_session, get_step_store

    store = get_step_store()
    sid, _runs = _run_one_turn(store)
    delete_session(sid)
    import asyncio

    runs = asyncio.run(store.list_runs(conversation_id=f"session-{sid}"))
    assert runs == [], "deleting a session must take its step rows with it"


def test_delete_all_sessions_cascades_step_rows():
    from grc_agent.db import delete_all_sessions, get_step_store

    store = get_step_store()
    sid, _runs = _run_one_turn(store)
    delete_all_sessions()
    import asyncio

    assert asyncio.run(store.list_runs(conversation_id=f"session-{sid}")) == []


def test_prune_sessions_takes_step_rows_along():
    from grc_agent.db import _conn, _prune_in, get_step_store

    store = get_step_store()
    sid, _runs = _run_one_turn(store)
    with _conn() as conn:
        _prune_in(conn, keep=0)
    import asyncio

    assert asyncio.run(store.list_runs(conversation_id=f"session-{sid}")) == []


def test_orphan_sweep_removes_unmothered_session_rows():
    """A Clear History racing an in-flight turn leaves step rows for a
    session id that no longer exists — the init-time sweep must remove them,
    while never touching rows grouped under other conversation ids."""
    from grc_agent.db import get_step_store

    store = get_step_store()
    # Simulate the orphan: a runs row for a session that never existed...
    import asyncio
    from datetime import UTC, datetime

    from pydantic_ai_harness.step_persistence import RunRecord

    async def _seed():
        await store.register_run(
            RunRecord(
                run_id="grc_chat-deadbeef",
                conversation_id="session-999",
                agent_name="grc_chat",
                started_at=datetime.now(UTC),
            )
        )
        await store.register_run(
            RunRecord(
                run_id="grc_chat-ungrouped",
                conversation_id=None,
                agent_name="grc_chat",
                started_at=datetime.now(UTC),
            )
        )

    asyncio.run(_seed())

    # Re-run init with the guards cleared — the sweep fires.
    from grc_agent import db

    db._initialized_paths.clear()
    db.init_db()

    with _open_raw_connection() as raw:
        convs = [r[0] for r in raw.execute("SELECT DISTINCT conversation_id FROM runs")]
    assert "session-999" not in convs, "orphaned session-N rows must be swept"
    assert None in convs, "ungrouped runs must never be touched by the sweep"

