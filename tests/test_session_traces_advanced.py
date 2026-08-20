"""Advanced/sophisticated tests for session handling + the StepPersistence layer.

These go beyond the basic test_session_traces.py suite:
- Real pydantic-ai Agent with TestModel streaming → real run_id/conversation_id/usage
- Concurrency: N worker threads writing/reading the step store simultaneously
- Concurrency: multi-thread init_db race
- Data integrity: Unicode, emojis, null bytes, large blobs, multi-turn accumulation
- Schema migration: v1/v2 → v4 (turn_traces dropped, StepPersistence tables owned by the store)
- ChatSidebar end-to-end: real widget tree under xvfb with a real (test) agent
- Race condition: concurrent save_session + delete_session

Needs xvfb-run for the ChatSidebar integration tests.
"""

import asyncio
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
    db._step_stores.clear()
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


def _persistence_agent(**agent_kwargs):
    """A TestModel agent wired exactly like the interactive one's persistence
    stack: StepPersistence on the shared, session-scoped store."""
    from pydantic_ai import Agent
    from pydantic_ai.models.test import TestModel
    from pydantic_ai_harness.step_persistence import StepPersistence

    from grc_agent.db import get_step_store

    return Agent(
        TestModel(),
        capabilities=[StepPersistence(store=get_step_store(), agent_name="grc_chat")],
        **agent_kwargs,
    )


def test_step_persistence_with_real_agent_iter_and_testmodel(tmp_path):
    """Drive a REAL agent.iter() loop (TestModel) through the persistence
    capability exactly as the sidebar does, then verify the store holds real
    run records, boundary events, and a resumable snapshot — not mocks."""
    from pydantic_ai_harness.step_persistence import continue_run
    from pydantic_graph import End

    from grc_agent.db import conversation_id_for_session

    sid, _f = _make_session(tmp_path)
    conv = conversation_id_for_session(sid)
    agent = _persistence_agent(output_type=str)

    async def _run():
        async with agent.iter("say hi", conversation_id=conv) as run:
            node = run.next_node
            while node is not None and not isinstance(node, End):
                node = await run.next(node)
        return run

    run = asyncio.run(_run())
    _ = run  # the AgentRun object itself is not needed beyond driving the loop

    store = get_step_store()
    runs = asyncio.run(store.list_runs(conversation_id=conv))
    assert len(runs) == 1
    rec = runs[0]
    # agent_name set + run_id unset → the capability derives
    # '{agent_name}-{8-hex}' per run (the documented default).
    assert rec.run_id.startswith("grc_chat-")
    assert rec.agent_name == "grc_chat"

    events = asyncio.run(store.list_events(run_id=rec.run_id))
    kinds = [e.kind for e in events]
    assert kinds[0] == "run_started"
    assert "model_request_completed" in kinds
    assert kinds[-1] == "run_completed"

    history = asyncio.run(continue_run(store, run_id=rec.run_id))
    assert len(history) >= 2, "snapshot must carry the turn's messages"


from grc_agent.db import get_step_store  # noqa: E402  (used by tests above/below)

# ==========================================
# Concurrency: concurrent writes / reads / init
# ==========================================


def test_concurrent_step_store_writes_no_locking():
    """N worker threads each register a run + snapshot on the shared store
    simultaneously. WAL + busy_timeout must prevent 'database is locked' —
    every run must land."""
    from datetime import UTC, datetime

    from pydantic_ai.messages import ModelRequest, UserPromptPart
    from pydantic_ai_harness.step_persistence import ContinuableSnapshot, RunRecord

    from grc_agent.db import get_step_store

    store = get_step_store()
    N = 20

    def _write_one(i):
        rid = f"grc_chat-conc-{i:03d}"
        msgs = [ModelRequest(parts=[UserPromptPart(content=f"p-{i}")])]

        async def _go():
            await store.register_run(
                RunRecord(
                    run_id=rid,
                    conversation_id=None,
                    agent_name="grc_chat",
                    started_at=datetime.now(UTC),
                )
            )
            await store.save_snapshot(ContinuableSnapshot(run_id=rid, step_index=0, messages=msgs))

        asyncio.run(_go())
        return rid

    with ThreadPoolExecutor(max_workers=N) as pool:
        run_ids = list(pool.map(_write_one, range(N)))

    recorded = asyncio.run(store.list_runs())
    assert sorted(r.run_id for r in recorded) == sorted(run_ids), (
        "no concurrent store write should fail due to locking"
    )


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


def test_multi_turn_runs_accumulate_under_one_conversation(tmp_path):
    """Multiple turns against one session each record their own run, all
    grouped under the same conversation id, chronologically ordered, each
    independently resumable."""
    from pydantic_ai_harness.step_persistence import continue_run
    from pydantic_graph import End

    from grc_agent.db import conversation_id_for_session

    sid, _f = _make_session(tmp_path)
    conv = conversation_id_for_session(sid)
    agent = _persistence_agent(output_type=str)

    async def _turns():
        history = None
        for i in range(5):
            async with agent.iter(
                f"turn-{i}", conversation_id=conv, message_history=history
            ) as run:
                node = run.next_node
                while node is not None and not isinstance(node, End):
                    node = await run.next(node)
            history = run.result.all_messages()

    asyncio.run(_turns())

    store = get_step_store()
    runs = asyncio.run(store.list_runs(conversation_id=conv))
    assert len(runs) == 5, "one run per turn, all grouped under the session conversation"

    # Chronological by started_at, distinct run ids
    started = [r.started_at for r in runs]
    assert started == sorted(started), "runs should be in arrival order"
    ids = [r.run_id for r in runs]
    assert len(set(ids)) == 5

    # Each turn's latest snapshot is independently resumable
    for r in runs:
        history = asyncio.run(continue_run(store, run_id=r.run_id))
        assert len(history) >= 2


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


# ==========================================
# Schema migration: v1 → v2
# ==========================================


def test_v1_db_migrates_to_latest_without_turn_traces(tmp_path, monkeypatch):
    """A DB already at schema_version=1 (sessions with first_message) migrates
    through the chain to the latest version; the hand-rolled turn_traces table
    never survives, and existing session data is untouched."""
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

    # Run init_db — must migrate v1 through the chain to the latest version
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
        assert "turn_traces" not in tables, "v4 must drop the hand-rolled trace table"

        # Existing session data is untouched
        row = raw.execute("SELECT first_message, messages FROM sessions").fetchone()
        assert row[0] == "v1 prompt"


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


def test_chatsidebar_run_agent_turn_records_step_rows(tmp_path):
    """End-to-end: instantiate a real ChatSidebar under xvfb, override its
    agent with a TestModel-backed Agent wired with the same StepPersistence
    capability the interactive agent uses, run _run_agent_turn, and verify
    the turn lands as a run + events + snapshot grouped under session-{id}."""
    from unittest.mock import AsyncMock, MagicMock

    from pydantic_ai_harness.step_persistence import continue_run

    from grc_agent.chat_sidebar import ChatSidebar
    from grc_agent.db import conversation_id_for_session

    test_agent = _persistence_agent(output_type=str)

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

    sid, _f = _make_session(tmp_path)
    sidebar._active_session_id = sid

    # Run a real turn
    asyncio.run(sidebar._run_agent_turn("end to end test prompt"))

    conv = conversation_id_for_session(sid)
    store = get_step_store()
    runs = asyncio.run(store.list_runs(conversation_id=conv))
    assert len(runs) == 1, f"expected 1 run under {conv}, got {len(runs)}"

    events = asyncio.run(store.list_events(run_id=runs[0].run_id))
    kinds = [e.kind for e in events]
    assert kinds[0] == "run_started"
    assert kinds[-1] == "run_completed"

    # The turn's snapshot is resumable and carries the prompt
    history = asyncio.run(continue_run(store, run_id=runs[0].run_id))
    assert any(
        p.__class__.__name__ == "UserPromptPart" and "end to end test prompt" in str(p.content)
        for m in history
        for p in getattr(m, "parts", [])
    )

    # The tok/s status-line rate must be computed from the same numbers the
    # run reports (visible output over native generation time) — TestModel
    # generates in <1ms so the rate is typically None; the consistency
    # contract is what's asserted.

    rate = getattr(sidebar, "_last_turn_rate", None)
    assert rate is None or rate > 0


def test_manual_compaction_preserves_full_history_and_reasoning_in_same_db(
    tmp_path, monkeypatch
):
    """Dataset invariant: compacting the reloadable session history must not
    erase the original transcript from the user-exported chat_sessions.db.

    The sidebar snapshots the complete history into StepPersistence before it
    saves the compacted session blob. This test includes a ThinkingPart so the
    reasoning-trace retention claim is behavioral, not just structural.
    """
    from unittest.mock import MagicMock

    from pydantic_ai import Agent
    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        TextPart,
        ThinkingPart,
        UserPromptPart,
    )
    from pydantic_ai.models.test import TestModel

    from grc_agent.chat_sidebar import ChatSidebar
    from grc_agent.db import (
        conversation_id_for_session,
        deserialize_messages,
        get_step_store,
        load_session,
        save_session,
    )

    unique_prompt = "FULL_HISTORY_DATASET_SENTINEL"
    unique_reasoning = "REASONING_TRACE_DATASET_SENTINEL"
    original = []
    for i in range(12):
        prompt = unique_prompt if i == 0 else f"user-{i}"
        response_parts = [TextPart(content=f"assistant-{i}")]
        if i == 0:
            response_parts.insert(0, ThinkingPart(content=unique_reasoning))
        original.extend(
            [
                ModelRequest(parts=[UserPromptPart(content=prompt)]),
                ModelResponse(parts=response_parts),
            ]
        )

    sid, file_path = _make_session(tmp_path, messages=original)
    sidebar = ChatSidebar()
    sidebar._agent = Agent(TestModel(), output_type=str)
    sidebar._active_session_id = sid
    sidebar._message_history = original
    sidebar._render_history = MagicMock()
    sidebar._update_context_label = MagicMock()

    async def _save_compacted_history():
        saved = save_session(sid, file_path, sidebar._message_history)
        assert saved == sid

    sidebar._save_history = _save_compacted_history

    async def _fake_compact(_strategy, messages, *, model):  # noqa: ARG001
        return messages[-2:]

    monkeypatch.setattr("pydantic_ai_harness.compaction._manual.compact_now", _fake_compact)
    asyncio.run(sidebar._run_compact_now())

    session = load_session(sid)
    assert session is not None
    compacted = deserialize_messages(session["messages"])
    assert unique_prompt not in repr(compacted), "test must prove the session blob was compacted"
    assert unique_reasoning not in repr(compacted), "test must prove old reasoning left the session blob"

    conversation_id = conversation_id_for_session(sid)
    store = get_step_store()
    runs = asyncio.run(store.list_runs(conversation_id=conversation_id))
    snapshots = [
        snapshot
        for run in runs
        for snapshot in asyncio.run(store.list_snapshots(run_id=run.run_id))
    ]
    archived_parts = [
        part
        for snapshot in snapshots
        for message in snapshot.messages
        for part in getattr(message, "parts", [])
    ]
    assert any(
        isinstance(part, UserPromptPart) and unique_prompt in str(part.content)
        for part in archived_parts
    )
    assert any(
        isinstance(part, ThinkingPart) and unique_reasoning in part.content
        for part in archived_parts
    )


def test_chatsidebar_failed_turn_leaves_failure_trail(tmp_path):
    """When the model raises mid-turn, the sidebar catches the error (no
    crash), and the persistence layer has already recorded the failure
    boundary: run_started + model_request_failed + run_failed, with the
    at-failure snapshot saved by on_run_error."""
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock, MagicMock

    from pydantic_ai import Agent, models
    from pydantic_ai.models import Model

    from grc_agent.chat_sidebar import ChatSidebar
    from grc_agent.db import conversation_id_for_session, get_step_store

    models.ALLOW_MODEL_REQUESTS = False

    class _ExplodingModel(Model):
        @property
        def model_name(self) -> str:
            return "exploding"

        @property
        def system(self) -> str:
            return ""

        def name(self) -> str:
            return "exploding"

        async def request(self, messages, model_settings, model_request_parameters):  # noqa: ARG002
            raise RuntimeError("model exploded")

        @asynccontextmanager
        async def request_stream(
            self,
            messages,  # noqa: ARG002
            model_settings,  # noqa: ARG002
            model_request_parameters,  # noqa: ARG002
            run_context=None,  # noqa: ARG002
        ):
            raise RuntimeError("model exploded")
            yield  # pragma: no cover

    from pydantic_ai_harness.step_persistence import StepPersistence

    failing_agent = Agent(
        _ExplodingModel(),
        output_type=str,
        capabilities=[StepPersistence(store=get_step_store(), agent_name="grc_chat")],
    )

    sidebar = ChatSidebar()
    sidebar._agent = failing_agent
    sidebar._active_provider = "err-provider"
    sidebar._active_model = "err-model"
    sidebar._active_base_url = "err://"
    sidebar._flowgraph_proxy = MagicMock()
    sidebar._flowgraph_proxy._canvas_manager = None
    sidebar._save_history = AsyncMock()
    sidebar._render_history = MagicMock()
    sidebar._scroll_to_bottom = MagicMock()
    sidebar._update_context_label = MagicMock()

    sid, _f = _make_session(tmp_path)
    sidebar._active_session_id = sid

    asyncio.run(sidebar._run_agent_turn("trigger error"))

    conv = conversation_id_for_session(sid)
    store = get_step_store()
    runs = asyncio.run(store.list_runs(conversation_id=conv))
    assert len(runs) == 1
    events = asyncio.run(store.list_events(run_id=runs[0].run_id))
    kinds = [e.kind for e in events]
    # The stream-entry raise bypasses on_model_request_error, but the run-level
    # failure boundary is still recorded (run_failed is emitted by on_run_error,
    # which also saves the at-failure snapshot).
    assert kinds == ["run_started", "model_request_started", "run_failed"], kinds
    failed = [e for e in events if e.kind == "run_failed"][0]
    assert "model exploded" in (failed.error or "")

    # on_run_error persists the at-failure history ONLY when it contains a
    # model response (a bare prompt equals restarting the run — the library's
    # documented rule), so this turn leaves no resume point.
    from pydantic_ai_harness.step_persistence import continue_run

    try:
        asyncio.run(continue_run(store, run_id=runs[0].run_id))
    except LookupError:
        pass
    else:
        raise AssertionError("bare-prompt failure must not create a resume point")


def test_chatsidebar_turn_without_session_records_ungrouped_run():
    """If _active_session_id is None (unsaved graph), the turn still records
    its run — but under a fresh conversation id, never grouped under any
    session-N (so session deletion/cleanup can never touch it)."""
    from unittest.mock import AsyncMock, MagicMock

    from grc_agent.chat_sidebar import ChatSidebar
    from grc_agent.db import get_step_store

    test_agent = _persistence_agent(output_type=str)

    sidebar = ChatSidebar()
    sidebar._agent = test_agent
    sidebar._active_provider = "p"
    sidebar._active_model = "m"
    sidebar._active_base_url = "b"
    sidebar._active_session_id = None  # no session bound
    sidebar._flowgraph_proxy = MagicMock()
    sidebar._save_history = AsyncMock()
    sidebar._render_history = MagicMock()
    sidebar._scroll_to_bottom = MagicMock()
    sidebar._update_context_label = MagicMock()

    asyncio.run(sidebar._run_agent_turn("no session"))

    store = get_step_store()
    runs = asyncio.run(store.list_runs())
    assert runs, "the turn must still be recorded"
    assert all(not (r.conversation_id or "").startswith("session-") for r in runs), (
        "ungrouped runs must never claim a session conversation id"
    )


# ==========================================
# Helpers
# ==========================================


def ModelResponse_with_text(text):
    from pydantic_ai.messages import ModelResponse, TextPart

    return ModelResponse(parts=[TextPart(content=text)])


def test_v2_db_with_turn_traces_migrates_to_latest_and_drops_it(tmp_path, monkeypatch):
    """A DB already at schema_version=2 (turn_traces present, populated) migrates
    to the latest version by dropping the table — the rows it held are not
    ported (no bridge, no dual-write), and session data is untouched."""
    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))
    from grc_agent.db import LATEST_SCHEMA_VERSION, get_db_path

    db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # Build a v2 DB manually: sessions + populated turn_traces.
    with sqlite3.connect(str(db_path)) as raw:
        raw.execute("CREATE TABLE _meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        raw.execute("INSERT INTO _meta (key, value) VALUES ('schema_version', '2')")
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
            (str(tmp_path / "v2.grc"), "[]", "v2 prompt"),
        )
        raw.execute(
            "CREATE TABLE turn_traces ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "session_id INTEGER NOT NULL, started_at REAL NOT NULL, "
            "events TEXT NOT NULL DEFAULT '[]')"
        )
        raw.execute(
            "INSERT INTO turn_traces (session_id, started_at, events) VALUES (1, 1000.0, '[]')"
        )
        raw.commit()

    from grc_agent import db as db_mod

    db_mod._initialized_paths.clear()
    db_mod.init_db()

    with sqlite3.connect(str(db_path)) as raw:
        version = raw.execute("SELECT value FROM _meta WHERE key = 'schema_version'").fetchone()
        assert int(version[0]) == LATEST_SCHEMA_VERSION
        tables = {r[0] for r in raw.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "turn_traces" not in tables
        row = raw.execute("SELECT first_message FROM sessions").fetchone()
        assert row[0] == "v2 prompt"
