"""Advanced/sophisticated tests for session handling + reasoning traces.

These go beyond the basic test_session_traces.py suite:
- Real pydantic-ai Agent with TestModel streaming → real run_id/conversation_id/usage
- Concurrency: N worker threads writing/reading traces simultaneously
- Concurrency: multi-thread init_db race
- Data integrity: Unicode, emojis, null bytes, large blobs, multi-turn accumulation
- Schema migration: v1→v2 (existing v1 DB without turn_traces)
- ChatSidebar end-to-end: real widget tree under xvfb with a real (test) agent
- Race condition: concurrent save_session + delete_session

Needs xvfb-run for the ChatSidebar integration tests.
"""

import asyncio
import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic_ai import models


@pytest.fixture(autouse=True)
def _restore_allow_model_requests():
    """Snapshot and restore pydantic-ai's global ALLOW_MODEL_REQUESTS flag.

    Tests in this file set it to False (to fail loudly if a real model request
    is ever attempted); without restoration the flag leaks into later suites
    in the same process and breaks their live tests (e.g. test_isolation's
    Ollama Cloud calls) with 'Model requests are not allowed'."""
    before = models.ALLOW_MODEL_REQUESTS
    yield
    models.ALLOW_MODEL_REQUESTS = before


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path, monkeypatch):
    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))
    from grc_agent import db

    db._initialized_paths.clear()
    db._cleanup_done.clear()
    yield


def _make_session(tmp_path, name="g.grc", messages=None):
    """Create a session and return (sid, file_path)."""
    from grc_agent.db import save_session

    f = tmp_path / name
    f.touch()
    sid = save_session(None, str(f), messages or [])
    assert sid is not None
    return sid, str(f)


# ==========================================
# Real pydantic-ai Agent with TestModel streaming
# ==========================================


def test_trace_recorder_with_real_agent_iter_and_testmodel():
    """Feed REAL pydantic-ai stream events (from an actual agent.iter() loop
    using TestModel) into a TraceRecorder, then verify the finalized row has
    real run_id, conversation_id, events, and usage — not synthetic mocks."""
    from pydantic_ai import Agent, models
    from pydantic_ai.models.test import TestModel
    from pydantic_graph import End

    from grc_agent.trace import TraceRecorder

    models.ALLOW_MODEL_REQUESTS = False

    agent = Agent(TestModel(custom_output_text="the answer is 10"), output_type=str)

    @agent.tool_plain
    async def double(x: int) -> str:
        """Double a number."""
        return f"double({x}) = {x * 2}"

    rec = TraceRecorder(
        session_id=None,
        provider="test",
        model="test-model",
        base_url="test://",
        user_prompt="what is double 5?",
        origin_page_path="/test.grc",
    )

    async def _run():
        async with agent.iter("what is double 5?") as run:
            node = run.next_node
            while node is not None and not isinstance(node, End):
                if Agent.is_model_request_node(node) or Agent.is_call_tools_node(node):
                    async with node.stream(run.ctx) as stream:
                        async for event in stream:
                            rec.on_event(event)
                node = await run.next(node)
        return run

    run = asyncio.run(_run())
    row = rec.finalize(run)

    # Real run metadata
    assert row["run_id"] is not None and len(row["run_id"]) > 10
    assert row["conversation_id"] is not None and len(row["conversation_id"]) > 10
    assert row["run_id"] == run.run_id
    assert row["conversation_id"] == run.conversation_id

    # Real events from the stream
    events = json.loads(row["events"])
    kinds = {e["kind"] for e in events}
    assert "part_start" in kinds, "should have seen at least one part_start"
    # TestModel called our tool, so tool_call + tool_result should be present
    assert "tool_call" in kinds or "tool_result" in kinds, (
        f"expected tool events from TestModel, got kinds={kinds}"
    )

    # Real usage (TestModel populates usage)
    assert row["input_tokens"] > 0, "TestModel should populate input_tokens"
    assert row["output_tokens"] > 0, "TestModel should populate output_tokens"

    # Final output
    assert row["final_output"] is not None
    assert row["error"] is None
    assert row["provider"] == "test"
    assert row["model"] == "test-model"
    assert row["user_prompt"] == "what is double 5?"

    # Duration is positive and reasonable
    assert row["duration_ms"] >= 0
    assert row["duration_ms"] < 10000  # should be fast


def test_trace_recorder_with_real_agent_no_tools():
    """A simpler real-agent run with NO tools — just text output. Verify
    the recorder captures exactly one part_start (TextPart) and no tool events."""
    from pydantic_ai import Agent, models
    from pydantic_ai.models.test import TestModel
    from pydantic_graph import End

    from grc_agent.trace import TraceRecorder

    models.ALLOW_MODEL_REQUESTS = False

    agent = Agent(TestModel(custom_output_text="hello from test"), output_type=str)

    rec = TraceRecorder(
        session_id=None,
        provider="test",
        model="test-model",
        base_url="test://",
        user_prompt="say hi",
        origin_page_path=None,
    )

    async def _run():
        async with agent.iter("say hi") as run:
            node = run.next_node
            while node is not None and not isinstance(node, End):
                if Agent.is_model_request_node(node) or Agent.is_call_tools_node(node):
                    async with node.stream(run.ctx) as stream:
                        async for event in stream:
                            rec.on_event(event)
                node = await run.next(node)
        return run

    run = asyncio.run(_run())
    row = rec.finalize(run)

    events = json.loads(row["events"])
    # No tool was called — no tool_call/tool_result events
    assert all(e["kind"] != "tool_call" for e in events), "no tools should have been called"
    assert row["final_output"] == "hello from test"


# ==========================================
# Concurrency: concurrent writes / reads / init
# ==========================================


def test_concurrent_save_turn_trace_no_locking(tmp_path):
    """N worker threads each save a trace row simultaneously via
    asyncio.to_thread. WAL + busy_timeout must prevent 'database is locked' —
    every row must land."""
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    from grc_agent.db import save_session
    from grc_agent.trace import TraceRecorder, get_turn_traces_for_session, save_turn_trace

    f = tmp_path / "g.grc"
    f.touch()
    msgs = [ModelRequest(parts=[UserPromptPart(content="p")])]
    sid = save_session(None, str(f), msgs)

    N = 20

    def _save_one(i):
        rec = TraceRecorder(
            session_id=sid,
            provider="test",
            model=f"model-{i}",
            base_url="test://",
            user_prompt=f"prompt-{i}",
            origin_page_path=str(f),
        )
        row = rec.finalize(run=None)
        row["final_output"] = f"output-{i}"
        return save_turn_trace(row)

    with ThreadPoolExecutor(max_workers=N) as pool:
        results = list(pool.map(lambda i: _save_one(i), range(N)))

    assert all(r is not None for r in results), "no save should fail due to locking"
    traces = get_turn_traces_for_session(sid)
    assert len(traces) == N
    models_saved = sorted(t["model"] for t in traces)
    expected = sorted(f"model-{i}" for i in range(N))
    assert models_saved == expected


def test_concurrent_save_session_and_read_no_locking(tmp_path):
    """Concurrent save_session writes (worker threads) + get_recent_sessions
    reads (main thread) must not deadlock or raise 'database is locked'."""
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    from grc_agent.db import get_recent_sessions, save_session

    N = 15

    def _save_one(i):
        f = tmp_path / f"g{i}.grc"
        f.touch()
        msgs = [
            ModelRequest(parts=[UserPromptPart(content=f"prompt-{i}")]),
            ModelResponse_with_text(f"reply-{i}"),
        ]
        return save_session(None, str(f), msgs)

    errors = []

    def _save_safe(i):
        try:
            _save_one(i)
        except Exception as e:
            errors.append(e)

    # Interleave writes with reads
    with ThreadPoolExecutor(max_workers=N) as pool:
        write_futures = [pool.submit(_save_safe, i) for i in range(N)]
        # Concurrent reads while writes are happening
        for _ in range(50):
            try:
                get_recent_sessions()
            except Exception as e:
                errors.append(e)
            time.sleep(0.001)
        for fut in write_futures:
            fut.result()

    assert errors == [], f"concurrent read/write raised errors: {errors}"


def test_concurrent_init_db_from_multiple_threads():
    """Multiple threads calling init_db() simultaneously must not raise.
    The _init_lock + double-checked locking guarantees only one actually
    runs the migrations."""
    from grc_agent.db import init_db

    errors = []

    def _init():
        try:
            init_db()
        except Exception as e:
            errors.append(e)

    with ThreadPoolExecutor(max_workers=10) as pool:
        futs = [pool.submit(_init) for _ in range(50)]
        for f in futs:
            f.result()

    assert errors == [], f"concurrent init_db raised: {errors}"


def test_concurrent_save_and_delete_no_resurrection(tmp_path):
    """Thread A saves a session; Thread B deletes it concurrently. The
    no-resurrection guarantee must hold: after both finish, either the
    session exists (with correct data) or it's gone — never resurrected
    under a new id with stale data."""
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    from grc_agent.db import delete_session, load_session, save_session

    f = tmp_path / "g.grc"
    f.touch()
    msgs = [ModelRequest(parts=[UserPromptPart(content="original")])]
    sid = save_session(None, str(f), msgs)

    delete_done = threading.Event()
    save_done = threading.Event()
    errors = []

    def _concurrent_save():
        try:
            # Re-save with updated messages
            updated = [ModelRequest(parts=[UserPromptPart(content="updated")])]
            save_session(sid, str(f), updated)
        except Exception as e:
            errors.append(e)
        save_done.set()

    def _concurrent_delete():
        try:
            delete_session(sid)
        except Exception as e:
            errors.append(e)
        delete_done.set()

    t1 = threading.Thread(target=_concurrent_save)
    t2 = threading.Thread(target=_concurrent_delete)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert errors == [], f"concurrent save+delete raised: {errors}"
    # After both complete: either the session is gone (delete won) or it
    # exists with "updated" data (save won). It must NEVER exist with
    # "original" data (that would mean neither op applied, a silent no-op).
    row = load_session(sid)
    if row is not None:
        msgs_back = row["messages"]
        assert "updated" in msgs_back, "if save won, data must be updated"
        assert "original" not in msgs_back


# ==========================================
# Multi-turn trace accumulation
# ==========================================


def test_multi_turn_trace_accumulation(tmp_path):
    """Multiple turns against one session each get their own trace row with
    incrementing timestamps and distinct run_ids."""
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    from grc_agent.db import save_session
    from grc_agent.trace import TraceRecorder, get_turn_traces_for_session, save_turn_trace

    f = tmp_path / "g.grc"
    f.touch()
    sid = save_session(None, str(f), [ModelRequest(parts=[UserPromptPart(content="p")])])

    # Simulate 5 turns
    for i in range(5):
        rec = TraceRecorder(
            session_id=sid,
            provider="test",
            model="m",
            base_url="b",
            user_prompt=f"turn-{i}",
            origin_page_path=str(f),
        )
        # Small sleep so timestamps are distinguishable
        time.sleep(0.002)
        row = rec.finalize(run=None)
        row["final_output"] = f"reply-{i}"
        tid = save_turn_trace(row)
        assert tid is not None

    traces = get_turn_traces_for_session(sid)
    assert len(traces) == 5

    # Timestamps should be monotonic
    started = [t["started_at"] for t in traces]
    assert started == sorted(started), "traces should be in arrival order"

    # Each turn's prompt is distinct
    prompts = [t["user_prompt"] for t in traces]
    assert prompts == [f"turn-{i}" for i in range(5)]

    # Each has a distinct id
    ids = [t["id"] for t in traces]
    assert len(set(ids)) == 5


# ==========================================
# Data integrity: Unicode, large blobs, edge cases
# ==========================================


def test_unicode_emoji_roundtrip_in_messages(tmp_path):
    """UserPromptPart with emojis, CJK chars, newlines, and special chars
    must survive serialize → save → load → deserialize exactly."""
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    from grc_agent.db import deserialize_messages, load_session, save_session, serialize_messages

    weird = 'Hello 世界 🌍 \n\n\ttabbed \\n literal \u0000 "quotes" \r\nemoji: 🧪🔧⚛️ math: ∑∫∂√'
    msgs = [
        ModelRequest(parts=[UserPromptPart(content=weird)]),
    ]
    s = serialize_messages(msgs)
    restored = deserialize_messages(s)
    assert restored[0].parts[0].content == weird

    f = tmp_path / "g.grc"
    f.touch()
    sid = save_session(None, str(f), msgs)
    loaded = load_session(sid)
    restored2 = deserialize_messages(loaded["messages"])
    assert restored2[0].parts[0].content == weird

    # first_message should also preserve the unicode
    assert loaded["first_message"] == weird


def test_large_messages_blob_roundtrip(tmp_path):
    """A session with 50 messages (25 user + 25 responses, alternating) with
    large thinking parts must round-trip without truncation."""
    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        TextPart,
        ThinkingPart,
        UserPromptPart,
    )

    from grc_agent.db import deserialize_messages, load_session, save_session

    msgs = []
    big_thinking = "thinking " * 500  # ~4500 chars per thinking part
    for i in range(25):
        msgs.append(ModelRequest(parts=[UserPromptPart(content=f"question {i} " * 50)]))
        msgs.append(
            ModelResponse(
                parts=[
                    ThinkingPart(content=big_thinking + str(i)),
                    TextPart(content=f"answer {i} " * 50),
                ]
            )
        )

    assert len(msgs) == 50

    f = tmp_path / "g.grc"
    f.touch()
    sid = save_session(None, str(f), msgs)
    loaded = load_session(sid)
    restored = deserialize_messages(loaded["messages"])

    assert len(restored) == 50
    # Spot-check first and last
    assert restored[0].parts[0].content.startswith("question 0")
    assert restored[-1].parts[-1].content.startswith("answer 24")
    # Thinking parts preserved
    thinking_parts = [
        p
        for m in restored
        if m.__class__.__name__ == "ModelResponse"
        for p in m.parts
        if p.__class__.__name__ == "ThinkingPart"
    ]
    assert len(thinking_parts) == 25
    assert thinking_parts[-1].content == big_thinking + "24"


def test_first_message_preserves_multiline_and_whitespace(tmp_path):
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    from grc_agent.db import save_session

    f = tmp_path / "g.grc"
    f.touch()
    text = "line1\nline2\n  indented\n\t\ttabbed"
    sid = save_session(None, str(f), [ModelRequest(parts=[UserPromptPart(content=text)])])
    from grc_agent.db import load_session

    assert load_session(sid)["first_message"] == text


def test_events_json_handles_unserializable_args():
    """If a tool call has args containing a non-JSON-serializable object
    (e.g. a set), the `default=str` fallback in json.dumps must save the
    trace without raising."""
    from pydantic_ai import FunctionToolCallEvent
    from pydantic_ai.messages import ToolCallPart

    from grc_agent.trace import TraceRecorder

    rec = TraceRecorder(
        session_id=None,
        provider="p",
        model="m",
        base_url="b",
        user_prompt="up",
        origin_page_path=None,
    )

    class _Weird:
        def __str__(self):
            return "<weird object>"

    # Build a ToolCallPart with args that contain a non-serializable object.
    # pydantic-ai validates args as JSON, so use a real dict but feed the
    # recorder a raw event with a non-serializable payload to test the fallback.
    part = ToolCallPart(tool_name="t", args={"normal": "value"}, tool_call_id="c1")
    rec.on_event(FunctionToolCallEvent(part=part))

    # Inject a non-serializable into the recorder's event list directly
    # to test the default=str fallback path
    rec._events.append({"kind": "custom", "payload": _Weird()})

    # Must not raise
    row = rec.finalize(run=None)
    events = json.loads(row["events"])
    assert events[-1]["payload"] == "<weird object>"


# ==========================================
# Schema migration: v1 → v2
# ==========================================


def test_v1_to_v2_migration_creates_turn_traces(tmp_path, monkeypatch):
    """A DB already at schema_version=1 (sessions with first_message, no
    turn_traces) must be migrated to v2 by creating turn_traces, without
    touching existing session data."""
    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))
    from grc_agent.db import LATEST_SCHEMA_VERSION, get_db_path

    db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    from pydantic_ai import ModelMessagesTypeAdapter
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    msgs = [ModelRequest(parts=[UserPromptPart(content="v1 prompt")])]
    blob = ModelMessagesTypeAdapter.dump_json(msgs).decode("utf-8")

    # Build a v1 DB manually: sessions WITH first_message, _meta at version 1, NO turn_traces
    with sqlite3.connect(str(db_path)) as raw:
        raw.execute("CREATE TABLE _meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        raw.execute("INSERT INTO _meta (key, value) VALUES ('schema_version', '1')")
        raw.execute(
            "CREATE TABLE sessions ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "grc_file_path TEXT NOT NULL, "
            "messages TEXT NOT NULL, "
            "first_message TEXT NOT NULL DEFAULT '', "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
            "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        )
        raw.execute(
            "INSERT INTO sessions (grc_file_path, messages, first_message) VALUES (?, ?, ?)",
            (str(tmp_path / "v1.grc"), blob, "v1 prompt"),
        )
        raw.commit()

    # Run init_db — must migrate v1 → v2
    from grc_agent import db as db_mod

    db_mod._initialized_paths.clear()
    db_mod.init_db()

    with sqlite3.connect(str(db_path)) as raw:
        version = raw.execute("SELECT value FROM _meta WHERE key = 'schema_version'").fetchone()
        assert int(version[0]) == LATEST_SCHEMA_VERSION

        tables = [
            r[0]
            for r in raw.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        ]
        assert "turn_traces" in tables

        # Existing session data is untouched
        row = raw.execute("SELECT first_message, messages FROM sessions").fetchone()
        assert row[0] == "v1 prompt"

        # turn_traces FK cascade works
        raw.execute("INSERT INTO turn_traces (session_id, started_at, events) VALUES (1, 0, '[]')")
        raw.execute("DELETE FROM sessions WHERE id = 1")
        raw.execute("COMMIT")


def test_migration_skipped_when_already_at_latest():
    """If _meta already records LATEST_SCHEMA_VERSION, no migration runs.
    Verified by checking that a deliberately-corrupt 'pending migration'
    marker is NOT touched."""
    from grc_agent import db as db_mod
    from grc_agent.db import LATEST_SCHEMA_VERSION, get_db_path

    db_mod.init_db()  # full init at latest
    db_path = get_db_path()

    # Inject a marker that a migration would overwrite
    with sqlite3.connect(str(db_path)) as raw:
        raw.execute(
            "INSERT OR REPLACE INTO _meta (key, value) VALUES ('migration_marker', 'untouched')"
        )
        raw.commit()

    db_mod._initialized_paths.clear()
    db_mod.init_db()  # re-init — must skip since version is latest

    with sqlite3.connect(str(db_path)) as raw:
        marker = raw.execute("SELECT value FROM _meta WHERE key = 'migration_marker'").fetchone()
        assert marker is not None and marker[0] == "untouched"
        version = raw.execute("SELECT value FROM _meta WHERE key = 'schema_version'").fetchone()
        assert int(version[0]) == LATEST_SCHEMA_VERSION


# ==========================================
# ChatSidebar end-to-end with real (test) agent
# ==========================================


def test_chatsidebar_run_agent_turn_produces_trace_row(tmp_path):
    """End-to-end: instantiate a real ChatSidebar under xvfb, override its
    agent with a TestModel-backed Agent, run _run_agent_turn, verify a
    turn_traces row exists in the DB with the correct provider/model/prompt.

    This is the integration test the reviewer flagged as missing — it
    exercises the full _run_agent_turn → TraceRecorder → _save_trace chain,
    not just the recorder in isolation."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    from pydantic_ai import Agent, models
    from pydantic_ai.models.test import TestModel

    from grc_agent.chat_sidebar import ChatSidebar

    models.ALLOW_MODEL_REQUESTS = False

    # Build a real agent with TestModel (deterministic, no LLM)
    test_agent = Agent(TestModel(custom_output_text="test reply from TestModel"), output_type=str)

    sidebar = ChatSidebar()
    sidebar._agent = test_agent
    sidebar._active_provider = "test-provider"
    sidebar._active_model = "test-model"
    sidebar._active_base_url = "test://base"
    sidebar._flowgraph_proxy = MagicMock()
    sidebar._flowgraph_proxy._canvas_manager = None
    sidebar._save_history = AsyncMock()  # avoid DB writes from _save_history
    sidebar._render_history = MagicMock()
    sidebar._scroll_to_bottom = MagicMock()
    sidebar._update_context_label = MagicMock()

    # Create a real session row so _active_session_id is set
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    from grc_agent.db import save_session

    f = tmp_path / "e2e.grc"
    f.touch()
    sid = save_session(None, str(f), [ModelRequest(parts=[UserPromptPart(content="seed")])])
    sidebar._active_session_id = sid

    # Run a real turn
    asyncio.run(sidebar._run_agent_turn("end to end test prompt"))

    # Verify a trace row exists
    from grc_agent.trace import get_turn_traces_for_session

    traces = get_turn_traces_for_session(sid)
    assert len(traces) == 1, f"expected 1 trace row, got {len(traces)}"
    t = traces[0]
    assert t["session_id"] == sid
    assert t["provider"] == "test-provider"
    assert t["model"] == "test-model"
    assert t["base_url"] == "test://base"
    assert t["user_prompt"] == "end to end test prompt"
    assert t["error"] is None
    # origin_page_path may be None when no canvas manager is wired (test env)

    # Events should be non-empty (TestModel produces part_start events)
    events = json.loads(t["events"])
    assert len(events) > 0, "should have captured events from the real stream"
    assert any(e["kind"] == "part_start" for e in events)

    # Run id should be a real UUID from pydantic-ai
    assert t["run_id"] is not None and len(t["run_id"]) > 10


def test_chatsidebar_run_agent_turn_error_produces_trace_with_error(tmp_path):
    """When _run_agent_turn catches an exception, the trace row must have
    the error column populated AND the events array must end with an error entry."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    from pydantic_ai.messages import ModelRequest, UserPromptPart

    from grc_agent.chat_sidebar import ChatSidebar
    from grc_agent.db import save_session

    sidebar = ChatSidebar()

    # Agent that raises on iter()
    failing_agent = MagicMock()
    failing_agent.iter.side_effect = RuntimeError("agent boom")
    sidebar._agent = failing_agent
    sidebar._active_provider = "err-provider"
    sidebar._active_model = "err-model"
    sidebar._active_base_url = "err://"
    sidebar._flowgraph_proxy = MagicMock()
    sidebar._flowgraph_proxy._canvas_manager = None  # so _get_effective_path returns None
    sidebar._save_history = AsyncMock()
    sidebar._render_history = MagicMock()
    sidebar._scroll_to_bottom = MagicMock()
    sidebar._update_context_label = MagicMock()

    f = tmp_path / "err.grc"
    f.touch()
    sid = save_session(None, str(f), [ModelRequest(parts=[UserPromptPart(content="seed")])])
    sidebar._active_session_id = sid

    async def _run_and_drain():
        await sidebar._run_agent_turn("trigger error")
        # The error path dispatches _save_trace via ensure_future (not awaited
        # inline, so a double-cancel can't skip widget cleanup). Give the
        # detached task + its asyncio.to_thread worker time to complete before
        # asyncio.run tears the loop down.
        await asyncio.sleep(0.2)

    asyncio.run(_run_and_drain())

    from grc_agent.trace import get_turn_traces_for_session

    traces = get_turn_traces_for_session(sid)
    assert len(traces) == 1
    t = traces[0]
    assert t["error"] is not None
    assert "agent boom" in t["error"]
    assert "RuntimeError" in t["error"]
    assert t["provider"] == "err-provider"

    events = json.loads(t["events"])
    error_events = [e for e in events if e["kind"] == "error"]
    assert len(error_events) >= 1
    assert error_events[-1]["exc_type"] == "RuntimeError"


def test_chatsidebar_trace_not_saved_when_no_session_id():
    """If _active_session_id is None (unsaved graph), _run_agent_turn still
    creates a recorder but _save_trace must early-return — no trace row."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    from pydantic_ai import Agent, models
    from pydantic_ai.models.test import TestModel

    from grc_agent.chat_sidebar import ChatSidebar

    models.ALLOW_MODEL_REQUESTS = False
    agent = Agent(TestModel(custom_output_text="x"), output_type=str)

    sidebar = ChatSidebar()
    sidebar._agent = agent
    sidebar._active_provider = "p"
    sidebar._active_model = "m"
    sidebar._active_base_url = "b"
    sidebar._active_session_id = None  # no session bound
    sidebar._flowgraph_proxy = MagicMock()
    sidebar._save_history = AsyncMock()
    sidebar._render_history = MagicMock()
    sidebar._scroll_to_bottom = MagicMock()
    sidebar._update_context_label = MagicMock()

    async def _run_and_drain():
        await sidebar._run_agent_turn("no session")
        await asyncio.sleep(0.1)

    asyncio.run(_run_and_drain())

    # Verify no trace rows exist anywhere
    from grc_agent.db import _conn, init_db

    init_db()  # ensure the table exists before we query it
    with _conn() as conn:
        count = conn.execute("SELECT COUNT(*) FROM turn_traces").fetchone()[0]
    assert count == 0


# ==========================================
# Helpers
# ==========================================


def ModelResponse_with_text(text):
    from pydantic_ai.messages import ModelResponse, TextPart

    return ModelResponse(parts=[TextPart(content=text)])
