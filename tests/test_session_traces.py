"""Tests for the refactored session handling and the per-turn reasoning trace.

No LLM, no GUI — fast and hermetic (each test redirects GRC_AGENT_ENV to a
fresh tmp path so db.py's per-path init guard re-inits cleanly).
"""

import json
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
    db._cleanup_done.clear()
    yield


def _open_raw_connection():
    """A raw sqlite3 connection to the same DB file, WITHOUT going through
    db.get_connection() — used to independently verify PRAGMAs are persisted
    at the file level (journal_mode) and that the schema is on disk."""
    from grc_agent.db import get_db_path

    return sqlite3.connect(str(get_db_path()))


# ==========================================
# Phase 1: db.py reliability + schema versioning + first_message
# ==========================================


def test_wal_and_busy_timeout_applied():
    from grc_agent.db import get_connection, init_db

    init_db()  # ensures the DB exists
    with get_connection() as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        busy = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    assert mode == "wal"
    assert busy == 5000
    assert fk == 1, "foreign_keys must be ON per-connection so ON DELETE CASCADE fires"


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


def test_schema_version_meta_present_and_latest():
    from grc_agent.db import LATEST_SCHEMA_VERSION, init_db

    init_db()
    with _open_raw_connection() as raw:
        row = raw.execute("SELECT value FROM _meta WHERE key = 'schema_version'").fetchone()
    assert row is not None, "_meta must record the schema version"
    assert int(row[0]) == LATEST_SCHEMA_VERSION


def test_init_db_is_idempotent():
    from grc_agent.db import init_db

    init_db()
    init_db()  # second call must not raise or duplicate migrations
    init_db()
    with _open_raw_connection() as raw:
        row = raw.execute("SELECT value FROM _meta WHERE key = 'schema_version'").fetchone()
    assert int(row[0]) >= 2


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


def test_delete_all_sessions_cascades_to_traces(tmp_path):
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    from grc_agent.db import delete_all_sessions, save_session
    from grc_agent.trace import TraceRecorder, get_turn_traces_for_session, save_turn_trace

    f = tmp_path / "g.grc"
    f.touch()
    sid = save_session(None, str(f), [ModelRequest(parts=[UserPromptPart(content="p")])])

    row = TraceRecorder(
        session_id=sid,
        provider="p",
        model="m",
        base_url="b",
        user_prompt="p",
        origin_page_path=str(f),
    ).finalize(run=None)
    assert save_turn_trace(row) is not None
    assert len(get_turn_traces_for_session(sid)) == 1

    delete_all_sessions()
    assert len(get_turn_traces_for_session(sid)) == 0


# ==========================================
# Phase 2: TraceRecorder + save_turn_trace
# ==========================================


def test_trace_recorder_records_part_starts_and_tool_events():
    from pydantic_ai import FunctionToolCallEvent, FunctionToolResultEvent, PartStartEvent
    from pydantic_ai.messages import (
        TextPart,
        ThinkingPart,
        ToolCallPart,
        ToolReturnPart,
    )

    from grc_agent.trace import TraceRecorder

    rec = TraceRecorder(
        session_id=None,
        provider="p",
        model="m",
        base_url="b",
        user_prompt="up",
        origin_page_path=None,
    )
    # Build real pydantic-ai events the recorder will see in production
    rec.on_event(PartStartEvent(index=0, part=ThinkingPart(content="reasoning...")))
    rec.on_event(PartStartEvent(index=1, part=TextPart(content="answer")))
    rec.on_event(
        PartStartEvent(
            index=2,
            part=ToolCallPart(
                tool_name="inspect_graph", args={"view": "overview"}, tool_call_id="c1"
            ),
        )
    )

    rec.on_event(
        FunctionToolCallEvent(
            part=ToolCallPart(
                tool_name="inspect_graph", args={"view": "overview"}, tool_call_id="c1"
            ),
        )
    )
    rec.on_event(
        FunctionToolResultEvent(
            part=ToolReturnPart(tool_name="inspect_graph", content="result", tool_call_id="c1"),
        )
    )

    row = rec.finalize(run=None)
    events = json.loads(row["events"])
    kinds = [e["kind"] for e in events]
    assert kinds == ["part_start", "part_start", "part_start", "tool_call", "tool_result"]
    assert events[0]["part_type"] == "ThinkingPart"
    assert events[0]["content_preview"] == "reasoning..."
    assert events[2]["tool_name"] == "inspect_graph"
    assert events[3]["args"] == {"view": "overview"}
    assert events[4]["content"] == "result"


def test_trace_recorder_does_not_record_part_delta_events():
    """Per-token PartDeltaEvent is deliberately NOT recorded — consolidated
    content lives in the saved message history. This is the explicit design
    decision (see trace.py module docstring) and must not regress."""
    from pydantic_ai import PartDeltaEvent, PartStartEvent, TextPartDelta
    from pydantic_ai.messages import TextPart

    from grc_agent.trace import TraceRecorder

    rec = TraceRecorder(
        session_id=None,
        provider="p",
        model="m",
        base_url="b",
        user_prompt="up",
        origin_page_path=None,
    )
    rec.on_event(PartStartEvent(index=0, part=TextPart(content="")))
    # Per-token deltas — must be ignored
    for chunk in ("Hello", " ", "world"):
        rec.on_event(PartDeltaEvent(index=0, delta=TextPartDelta(content_delta=chunk)))

    events = json.loads(rec.finalize(run=None)["events"])
    assert len(events) == 1, "PartDeltaEvents must NOT be recorded"
    assert events[0]["kind"] == "part_start"


def test_trace_recorder_records_error():
    from grc_agent.trace import TraceRecorder

    rec = TraceRecorder(
        session_id=None,
        provider="p",
        model="m",
        base_url="b",
        user_prompt="up",
        origin_page_path=None,
    )
    rec.record_error("run", ValueError("boom"))
    row = rec.finalize(run=None, exc=ValueError("boom"))
    events = json.loads(row["events"])
    assert events[-1]["kind"] == "error"
    assert events[-1]["exc_type"] == "ValueError"
    assert "boom" in events[-1]["message"]
    assert row["error"] == "ValueError: boom"


def test_trace_recorder_drops_with_explicit_marker():
    """A pathological turn producing > _MAX_EVENTS events must drop oldest
    with an explicit `{kind: "dropped", count: N}` marker — never silently."""
    import pydantic_ai
    from pydantic_ai.messages import TextPart

    from grc_agent import trace as trace_mod
    from grc_agent.trace import TraceRecorder

    rec = TraceRecorder(
        session_id=None,
        provider="p",
        model="m",
        base_url="b",
        user_prompt="up",
        origin_page_path=None,
    )
    # Patch the cap down so we don't have to emit 5000 events
    original = trace_mod._MAX_EVENTS
    trace_mod._MAX_EVENTS = 3
    try:
        for i in range(10):
            rec.on_event(pydantic_ai.PartStartEvent(index=i, part=TextPart(content=str(i))))
    finally:
        trace_mod._MAX_EVENTS = original

    events = json.loads(rec.finalize(run=None)["events"])
    # Last entry must be the explicit drop marker
    assert events[-1]["kind"] == "dropped"
    assert events[-1]["count"] == 7  # 10 emitted, 3 kept


def test_save_turn_trace_returns_none_for_missing_parent(tmp_path):
    """If the parent session row is gone (cleared concurrently), the insert
    must be skipped rather than raising IntegrityError against the FK."""
    from grc_agent.trace import TraceRecorder, save_turn_trace

    rec = TraceRecorder(
        session_id=99999,  # non-existent
        provider="p",
        model="m",
        base_url="b",
        user_prompt="up",
        origin_page_path=str(tmp_path / "x.grc"),
    )
    row = rec.finalize(run=None)
    assert save_turn_trace(row) is None


def test_save_turn_trace_returns_none_for_none_session_id():
    from grc_agent.trace import TraceRecorder, save_turn_trace

    rec = TraceRecorder(
        session_id=None,
        provider="p",
        model="m",
        base_url="b",
        user_prompt="up",
        origin_page_path=None,
    )
    assert save_turn_trace(rec.finalize(run=None)) is None


def test_save_turn_trace_persists_full_row(tmp_path):
    from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

    from grc_agent.db import save_session
    from grc_agent.trace import TraceRecorder, get_turn_traces_for_session, save_turn_trace

    f = tmp_path / "g.grc"
    f.touch()
    sid = save_session(None, str(f), [ModelRequest(parts=[UserPromptPart(content="p")])])

    class _FakeUsage:
        input_tokens = 100
        output_tokens = 20
        total_tokens = 120
        details = {"reasoning_tokens": 50}

    class _FakeRun:
        run_id = "run-abc"
        conversation_id = "conv-xyz"

        @property
        def usage(self):
            # The run's own aggregated usage — the authoritative per-turn
            # totals (finalize reads output/reasoning/total from here, not
            # from the messages' last response).
            return _FakeUsage()

        def all_messages(self):
            return [
                ModelRequest(parts=[UserPromptPart(content="p")], instructions="be helpful"),
                ModelResponse(parts=[TextPart(content="done")], usage=_FakeUsage()),
            ]

        @property
        def result(self):
            class _R:
                output = "done"

            return _R()

    rec = TraceRecorder(
        session_id=sid,
        provider="ollama",
        model="qwen",
        base_url="http://x",
        user_prompt="the prompt",
        origin_page_path=str(f),
    )
    row = rec.finalize(run=_FakeRun())

    import hashlib

    expected_hash = hashlib.sha256(b"be helpful").hexdigest()

    tid = save_turn_trace(row)
    assert tid is not None

    rows = get_turn_traces_for_session(sid)
    assert len(rows) == 1
    r = rows[0]
    assert r["run_id"] == "run-abc"
    assert r["conversation_id"] == "conv-xyz"
    assert r["provider"] == "ollama"
    assert r["model"] == "qwen"
    assert r["base_url"] == "http://x"
    assert r["system_prompt_hash"] == expected_hash
    assert r["user_prompt"] == "the prompt"
    assert r["origin_page_path"] == str(f)
    assert r["final_output"] == "done"
    assert r["error"] is None
    assert r["input_tokens"] == 100
    assert r["output_tokens"] == 20
    assert r["reasoning_tokens"] == 50
    assert r["total_tokens"] == 120
    assert r["duration_ms"] >= 0
    # events is a valid JSON array
    assert json.loads(r["events"]) == []


def test_save_turn_trace_with_error_turn(tmp_path):
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    from grc_agent.db import save_session
    from grc_agent.trace import TraceRecorder, get_turn_traces_for_session, save_turn_trace

    f = tmp_path / "g.grc"
    f.touch()
    sid = save_session(None, str(f), [ModelRequest(parts=[UserPromptPart(content="p")])])

    rec = TraceRecorder(
        session_id=sid,
        provider="p",
        model="m",
        base_url="b",
        user_prompt="up",
        origin_page_path=str(f),
    )
    exc = RuntimeError("agent exploded")
    rec.record_error("run", exc)
    row = rec.finalize(run=None, exc=exc)

    tid = save_turn_trace(row)
    assert tid is not None
    rows = get_turn_traces_for_session(sid)
    assert len(rows) == 1
    assert rows[0]["error"] == "RuntimeError: agent exploded"
    assert rows[0]["final_output"] == ""
    events = json.loads(rows[0]["events"])
    assert events[-1]["kind"] == "error"


def test_clear_generation_guard_prevents_trace_resurrection(tmp_path):
    """Mirror of test_unit.py's clear_generation guard for save_session, but
    for save_turn_trace: if a global Clear lands while the worker-thread
    save_turn_trace is in flight, the resurrected trace row must be removed."""
    import asyncio

    from pydantic_ai.messages import ModelRequest, UserPromptPart

    from grc_agent.db import delete_all_sessions, save_session
    from grc_agent.trace import TraceRecorder, get_turn_traces_for_session, save_turn_trace

    f = tmp_path / "g.grc"
    f.touch()
    sid = save_session(None, str(f), [ModelRequest(parts=[UserPromptPart(content="p")])])

    rec = TraceRecorder(
        session_id=sid,
        provider="p",
        model="m",
        base_url="b",
        user_prompt="up",
        origin_page_path=str(f),
    )
    row = rec.finalize(run=None)

    # Simulate the clear landing AFTER the gen capture but BEFORE the await
    # returns — i.e. save_turn_trace completes, then the guard fires.
    async def _main():

        # We can't easily instantiate ChatSidebar without GTK; replicate the
        # exact guard logic inline to verify the contract save_turn_trace +
        # delete_turn_trace together provide.
        gen = 0
        new_id = await asyncio.to_thread(save_turn_trace, row)
        assert new_id is not None
        assert len(get_turn_traces_for_session(sid)) == 1
        # Simulate clear: bump gen + delete session rows (Clear History path)
        gen_after_clear = gen + 1
        delete_all_sessions()
        # Guard fires:
        if new_id is not None and gen != gen_after_clear:
            from grc_agent.trace import delete_turn_trace

            delete_turn_trace(new_id)
        assert len(get_turn_traces_for_session(sid)) == 0

    asyncio.run(_main())


def test_v0_to_v1_migration_backfills_first_message(tmp_path, monkeypatch):
    """A pre-existing v0 sessions table (no first_message column, no _meta)
    must be migrated to v1 by backfilling first_message from each row's
    messages blob, then bumped to LATEST_SCHEMA_VERSION."""
    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))
    # Build a v0-style DB manually: sessions table without first_message, no _meta
    from grc_agent.db import get_db_path

    db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    import sqlite3

    from pydantic_ai import ModelMessagesTypeAdapter
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    msgs = [ModelRequest(parts=[UserPromptPart(content="legacy first prompt")])]
    blob = ModelMessagesTypeAdapter.dump_json(msgs).decode("utf-8")

    with sqlite3.connect(str(db_path)) as raw:
        raw.execute(
            "CREATE TABLE sessions ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "grc_file_path TEXT NOT NULL, "
            "messages TEXT NOT NULL, "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
            "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        )
        raw.execute(
            "INSERT INTO sessions (grc_file_path, messages) VALUES (?, ?)",
            (str(tmp_path / "old.grc"), blob),
        )
        raw.commit()

    # Now run init_db() — it must migrate v0 -> v1 (add first_message, backfill)
    # and then v1 -> v2 (turn_traces).
    from grc_agent import db as db_mod

    db_mod._initialized_paths.clear()
    db_mod.init_db()

    with sqlite3.connect(str(db_path)) as raw:
        cols = [r[1] for r in raw.execute("PRAGMA table_info(sessions)").fetchall()]
        assert "first_message" in cols, "ALTER TABLE must add first_message"
        row = raw.execute("SELECT first_message FROM sessions").fetchone()
        assert row[0] == "legacy first prompt", "backfill must extract the first user prompt"
        version = raw.execute("SELECT value FROM _meta WHERE key = 'schema_version'").fetchone()
        assert version is not None and int(version[0]) == db_mod.LATEST_SCHEMA_VERSION
        # turn_traces table must now exist
        tables = [
            r[0]
            for r in raw.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        ]
        assert "turn_traces" in tables


def test_v0_to_v1_migration_survives_crash_after_alter(tmp_path, monkeypatch):
    """Regression (review issue #1, blocking): Python's sqlite3 auto-commits
    DDL (ALTER TABLE) but NOT DML (UPDATE). If the process is killed after
    the ALTER but before the backfill UPDATEs commit, the column is present
    on restart but unbackfilled. The idempotent backfill (outside the elif
    branch) must catch this on re-init."""
    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))
    from grc_agent.db import get_db_path

    db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    import sqlite3

    from pydantic_ai import ModelMessagesTypeAdapter
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    msgs = [ModelRequest(parts=[UserPromptPart(content="prompt that must survive crash")])]
    blob = ModelMessagesTypeAdapter.dump_json(msgs).decode("utf-8")

    # Simulate a crashed v0→v1 migration: the ALTER TABLE auto-committed
    # (column is present) but the backfill UPDATEs were rolled back (values
    # are still the column default ''). The _meta table is NOT bumped.
    with sqlite3.connect(str(db_path)) as raw:
        raw.execute(
            "CREATE TABLE sessions ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "grc_file_path TEXT NOT NULL, "
            "messages TEXT NOT NULL, "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
            "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        )
        raw.execute(
            "INSERT INTO sessions (grc_file_path, messages) VALUES (?, ?)",
            (str(tmp_path / "crashed.grc"), blob),
        )
        raw.commit()
        # Simulate the ALTER TABLE auto-commit (DDL commits implicitly)
        raw.execute("ALTER TABLE sessions ADD COLUMN first_message TEXT NOT NULL DEFAULT ''")
        # Do NOT commit any backfill UPDATE — simulate the crash.
        # first_message stays '' (the column default).

    # Verify the crash state: column exists, value is empty
    with sqlite3.connect(str(db_path)) as raw:
        cols = [r[1] for r in raw.execute("PRAGMA table_info(sessions)").fetchall()]
        assert "first_message" in cols
        val = raw.execute("SELECT first_message FROM sessions").fetchone()[0]
        assert val == "", "pre-condition: backfill was lost in the crash"

    # Now re-init — the idempotent backfill must recover the lost data
    from grc_agent import db as db_mod

    db_mod._initialized_paths.clear()
    db_mod.init_db()

    with sqlite3.connect(str(db_path)) as raw:
        val = raw.execute("SELECT first_message FROM sessions").fetchone()[0]
        assert val == "prompt that must survive crash", (
            "idempotent backfill must recover the first_message even after "
            "a crash between ALTER (auto-committed) and backfill (rolled back)"
        )


def test_trace_finalize_uses_run_aggregated_usage_not_last_response():
    """Per-turn output/reasoning/total must come from the run's own aggregated
    usage (pydantic-ai sums every request in THIS run). The old extraction
    took the LAST ModelResponse's usage, which undercounts multi-request
    turns — and all_messages() includes prior turns' responses, so summing
    those would leak across turns. Verified live: a tool-calling turn's
    run.usage.output_tokens equals the sum of both responses, while
    all_messages() also contains the previous turn's response."""
    from types import SimpleNamespace

    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        RequestUsage,
        TextPart,
        UserPromptPart,
    )
    from pydantic_ai.usage import RunUsage

    from grc_agent.trace import TraceRecorder

    # all_messages() includes a PRIOR turn's response (4 output tokens) plus
    # this turn's two responses (4 + 9 = 13). The run's own usage is 13.
    prior_turn_response = ModelResponse(
        parts=[TextPart(content="prior")], usage=RequestUsage(input_tokens=10, output_tokens=4)
    )
    this_turn_responses = [
        ModelResponse(
            parts=[TextPart(content="first")], usage=RequestUsage(input_tokens=20, output_tokens=4)
        ),
        ModelResponse(
            parts=[TextPart(content="final")], usage=RequestUsage(input_tokens=30, output_tokens=9)
        ),
    ]
    messages = [
        ModelRequest(parts=[UserPromptPart(content="prior prompt")]),
        prior_turn_response,
        ModelRequest(parts=[UserPromptPart(content="this turn")]),
        *this_turn_responses,
    ]

    run = SimpleNamespace(
        run_id="run-1",
        conversation_id="conv-1",
        usage=RunUsage(
            input_tokens=50, output_tokens=13, details={"reasoning_tokens": 5}
        ),
        all_messages=lambda: messages,
        result=SimpleNamespace(output="final"),
    )

    rec = TraceRecorder(
        session_id=None, provider="p", model="m", base_url="b", user_prompt="u", origin_page_path=None
    )
    row = rec.finalize(run)

    assert row["output_tokens"] == 13, "must be the run's total, not the last response's 9"
    assert row["reasoning_tokens"] == 5, "reasoning must come from run.usage.details"
    assert row["total_tokens"] == 63, "must be the run's total (50+13), not conversation-wide"
    # input_tokens keeps the last-response semantic: the context size at the
    # end of the turn (what the sidebar's context label displays).
    assert row["input_tokens"] == 30
    assert row["run_id"] == "run-1"
    assert row["conversation_id"] == "conv-1"


def test_trace_finalize_without_run_records_zero_usage():
    from grc_agent.trace import TraceRecorder

    rec = TraceRecorder(
        session_id=None, provider="p", model="m", base_url="b", user_prompt="u", origin_page_path=None
    )
    row = rec.finalize(run=None)
    assert row["output_tokens"] == 0
    assert row["reasoning_tokens"] == 0
    assert row["total_tokens"] == 0
    assert row["input_tokens"] == 0


def test_trace_finalize_generation_ms_from_native_timestamps():
    """generation_ms must come from pydantic-ai's own ModelRequest/ModelResponse
    timestamps (the delta per pair = that request's model processing time),
    summed over THIS run's messages only — tool execution happens between a
    response and the next request, so it is excluded by construction, and
    new_messages() excludes prior turns' messages."""
    from datetime import UTC, datetime, timedelta

    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        TextPart,
        UserPromptPart,
    )

    from grc_agent.trace import TraceRecorder

    t0 = datetime(2026, 8, 17, 12, 0, 0, tzinfo=UTC)

    def req(offset_s: float) -> ModelRequest:
        return ModelRequest(
            parts=[UserPromptPart(content="p")], timestamp=t0 + timedelta(seconds=offset_s)
        )

    def resp(offset_s: float) -> ModelResponse:
        return ModelResponse(
            parts=[TextPart(content="t")], timestamp=t0 + timedelta(seconds=offset_s)
        )

    # This run: request at +0.0 -> response at +0.4 (400ms generation),
    # then a 2s tool gap, then request at +2.4 -> response at +2.7 (300ms).
    new_msgs = [req(0.0), resp(0.4), req(2.4), resp(2.7)]

    class _Result:
        output = "done"

        def new_messages(self):
            return new_msgs

    class _Run:
        run_id = "r"
        conversation_id = "c"
        usage = None
        result = _Result()

        def all_messages(self):
            return new_msgs

    rec = TraceRecorder(
        session_id=None, provider="p", model="m", base_url="b", user_prompt="u", origin_page_path=None
    )
    row = rec.finalize(_Run())

    # 400 + 300 = 700ms of generation; the 2s tool gap is excluded.
    assert row["generation_ms"] == 700, f"expected 700ms of generation, got {row['generation_ms']}"
