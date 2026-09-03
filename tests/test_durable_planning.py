"""Durable Planning integration over the chat-session SQLite database.

Hermetic: every test redirects ``GRC_AGENT_ENV`` to a fresh temporary path;
no display, network, or real model is required.
"""

import asyncio
import sqlite3

import pytest
from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage
from pydantic_ai_harness.planning import (
    InMemoryPlanStore,
    PlanItem,
    Planning,
    SqlitePlanStore,
)


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path, monkeypatch):
    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))
    from grc_agent import db

    db._initialized_paths.clear()
    db._step_stores.clear()
    yield


def _make_session(tmp_path, name: str = "g.grc") -> int:
    from grc_agent.db import save_session

    path = tmp_path / name
    path.touch()
    session_id = save_session(None, str(path), [])
    assert session_id is not None
    return session_id


def _conversation(session_id: int) -> str:
    from grc_agent.db import conversation_id_for_session

    return conversation_id_for_session(session_id)


def _plan_store(conversation_id: str) -> SqlitePlanStore:
    from grc_agent.db import get_db_path

    return SqlitePlanStore(str(get_db_path()), session=conversation_id)


def _seed_plan(conversation_id: str, *contents: str) -> None:
    asyncio.run(
        _plan_store(conversation_id).set_items([PlanItem(content=content) for content in contents])
    )


def _plan_contents(conversation_id: str) -> list[str]:
    return [item.content for item in asyncio.run(_plan_store(conversation_id).get_items())]


def _planning_agent(call_tools: list[str]) -> Agent:
    from grc_agent.agent_factory import _plan_store_resolver

    return Agent(
        TestModel(call_tools=call_tools),
        output_type=str,
        capabilities=[Planning(store_resolver=_plan_store_resolver)],
    )


def test_plan_persists_across_two_runs_same_conversation(tmp_path):
    conversation_id = _conversation(_make_session(tmp_path))
    agent = _planning_agent(["write_plan"])

    first = asyncio.run(agent.run("Write a plan.", conversation_id=conversation_id))
    first_contents = _plan_contents(conversation_id)
    assert first_contents

    asyncio.run(
        _planning_agent([]).run(
            "Continue with the existing plan.",
            conversation_id=conversation_id,
            message_history=first.all_messages(),
        )
    )
    assert _plan_contents(conversation_id) == first_contents


def test_new_session_gets_empty_plan(tmp_path):
    conversation_a = _conversation(_make_session(tmp_path, "a.grc"))
    conversation_b = _conversation(_make_session(tmp_path, "b.grc"))
    _seed_plan(conversation_a, "Keep A isolated")

    assert _plan_contents(conversation_a) == ["Keep A isolated"]
    assert _plan_contents(conversation_b) == []


def test_ungrouped_run_falls_back_to_in_memory():
    from grc_agent.db import get_db_path, init_db

    asyncio.run(_planning_agent(["write_plan"]).run("Write an ephemeral plan."))
    init_db()
    with sqlite3.connect(str(get_db_path())) as conn:
        table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'plan_items'"
        ).fetchone()
    assert table is None


def test_delete_session_cascades_plan_rows(tmp_path):
    from grc_agent.db import delete_session

    session_id = _make_session(tmp_path)
    conversation_id = _conversation(session_id)
    _seed_plan(conversation_id, "Delete me")

    delete_session(session_id)
    assert _plan_contents(conversation_id) == []


def test_delete_all_sessions_cascades_only_session_plans(tmp_path):
    from grc_agent.db import delete_all_sessions

    conversation_a = _conversation(_make_session(tmp_path, "a.grc"))
    conversation_b = _conversation(_make_session(tmp_path, "b.grc"))
    _seed_plan(conversation_a, "A")
    _seed_plan(conversation_b, "B")
    _seed_plan("planner-scratch", "Keep unrelated plan")

    delete_all_sessions()

    assert _plan_contents(conversation_a) == []
    assert _plan_contents(conversation_b) == []
    assert _plan_contents("planner-scratch") == ["Keep unrelated plan"]


def test_prune_sessions_takes_plan_rows_along(tmp_path):
    from grc_agent.db import _conn, _prune_in

    old_id = _make_session(tmp_path, "old.grc")
    kept_id = _make_session(tmp_path, "kept.grc")
    old_conversation = _conversation(old_id)
    kept_conversation = _conversation(kept_id)
    _seed_plan(old_conversation, "Old")
    _seed_plan(kept_conversation, "Kept")

    with _conn() as conn:
        _prune_in(conn, keep=1)

    assert _plan_contents(old_conversation) == []
    assert _plan_contents(kept_conversation) == ["Kept"]


def test_orphan_sweep_removes_unmothered_session_plans():
    from grc_agent import db

    db.init_db()
    _seed_plan("session-999", "Orphan")
    _seed_plan("planner-scratch", "Keep")

    db._initialized_paths.clear()
    db.init_db()

    assert _plan_contents("session-999") == []
    assert _plan_contents("planner-scratch") == ["Keep"]


def test_plan_items_table_created_on_first_store_operation():
    from grc_agent.db import get_db_path, init_db

    init_db()
    with sqlite3.connect(str(get_db_path())) as conn:
        before = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'plan_items'"
        ).fetchone()
    assert before is None

    assert asyncio.run(_plan_store("session-1").get_items()) == []
    with sqlite3.connect(str(get_db_path())) as conn:
        after = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'plan_items'"
        ).fetchone()
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert after is not None
    assert journal_mode == "wal"


def test_plan_store_resolver_unit():
    from grc_agent.agent_factory import _plan_store_resolver

    def context(conversation_id):
        return RunContext(
            deps=None,
            model=TestModel(),
            usage=RunUsage(),
            conversation_id=conversation_id,
        )

    assert isinstance(_plan_store_resolver(context(None)), InMemoryPlanStore)
    assert isinstance(_plan_store_resolver(context("other-conversation")), InMemoryPlanStore)
    durable = _plan_store_resolver(context("session-42"))
    assert isinstance(durable, SqlitePlanStore)
    assert durable._session == "session-42"


def test_plan_survives_agent_live_swap(tmp_path):
    conversation_id = _conversation(_make_session(tmp_path))
    asyncio.run(_planning_agent(["write_plan"]).run("Write the plan.", conversation_id=conversation_id))
    before = _plan_contents(conversation_id)
    assert before

    asyncio.run(_planning_agent([]).run("Read it after a swap.", conversation_id=conversation_id))
    assert _plan_contents(conversation_id) == before


def test_plan_reminder_never_leaks_into_message_history(tmp_path):
    conversation_id = _conversation(_make_session(tmp_path))
    result = asyncio.run(
        _planning_agent(["write_plan"]).run("Write a plan.", conversation_id=conversation_id)
    )

    rendered = repr(result.all_messages())
    assert "<plan-reminder>" not in rendered
    assert "CachePoint" not in rendered


def test_harness_compact_now_replaces_history():
    # Harness-level check: compact_now is a pure in-memory call (no DB in
    # its path), so only the history-shape assertion is real — a plan-row
    # assertion here would be guaranteed by construction, not by behavior.
    # The app's own compact-now path (snapshot-before-replace) is covered by
    # test_chat_sidebar's compact button tests.
    from pydantic_ai_harness.compaction import SlidingWindowCompaction, compact_now

    history = [
        ModelRequest(parts=[UserPromptPart(content=f"user-{i}")])
        if i % 2 == 0
        else ModelResponse(parts=[TextPart(content=f"assistant-{i}")])
        for i in range(8)
    ]
    compacted = asyncio.run(
        compact_now(
            SlidingWindowCompaction(
                max_tokens=1,
                keep_messages=2,
                preserve_first_user_message=True,
            ),
            history,
            model=TestModel(),
        )
    )

    assert compacted != history


def test_planner_factory_uses_durable_store_and_executor_has_no_planning(monkeypatch):
    from grc_agent.agent_factory import build_agents_from_cfg

    monkeypatch.setattr(
        "grc_agent.agent_factory.resolve_model_context_length", lambda *_args, **_kwargs: None
    )
    agents = build_agents_from_cfg(
        {
            "provider": "ollama_local",
            "model": "test-model",
            "ollama_base_url": "http://127.0.0.1:11434",
        }
    )
    assert agents.model_build_error is None

    capabilities = []
    agents.planner._root_capability.apply(capabilities.append)
    planning = next(cap for cap in capabilities if isinstance(cap, Planning))
    assert planning.store_resolver is not None

    executor_capabilities = []
    agents.executor._root_capability.apply(executor_capabilities.append)
    assert not any(isinstance(cap, Planning) for cap in executor_capabilities)


def test_coerce_plan_items_handles_stringified_json_and_aliases():
    from pydantic import TypeAdapter
    from pydantic_ai_harness.planning import TaskStatus

    from grc_agent.agent_factory import CoercedPlanItems

    ta = TypeAdapter(CoercedPlanItems)

    raw_json = '[{"id": 1, "name": "Add variables", "status": "in_progress"}, {"id": 2, "step": "Build chain", "status": "done"}]'
    items = ta.validate_python(raw_json)
    assert len(items) == 2
    assert items[0].id == "1"
    assert items[0].content == "Add variables"
    assert items[0].status == TaskStatus.in_progress
    assert items[1].id == "2"
    assert items[1].content == "Build chain"
    assert items[1].status == TaskStatus.completed


def test_coerce_plan_items_handles_plain_strings():
    from pydantic import TypeAdapter

    from grc_agent.agent_factory import CoercedPlanItems

    ta = TypeAdapter(CoercedPlanItems)
    items = ta.validate_python(["First step", "Second step"])
    assert len(items) == 2
    assert items[0].content == "First step"
    assert items[1].content == "Second step"


def test_coerce_plan_items_raises_model_retry_on_invalid():
    from pydantic import TypeAdapter
    from pydantic_ai import ModelRetry

    from grc_agent.agent_factory import CoercedPlanItems

    ta = TypeAdapter(CoercedPlanItems)
    with pytest.raises(ModelRetry) as exc_info:
        ta.validate_python("not json at all")
    assert "Invalid JSON" in str(exc_info.value)

    with pytest.raises(ModelRetry) as exc_info2:
        ta.validate_python(12345)
    assert "Invalid plan items" in str(exc_info2.value)


def test_planner_executes_write_plan_with_stringified_json(tmp_path):
    from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
    from pydantic_ai.models.function import FunctionModel

    from grc_agent.agent_factory import build_agents_from_cfg

    agents = build_agents_from_cfg(
        {
            "provider": "ollama_local",
            "model": "test-model",
            "ollama_base_url": "http://127.0.0.1:11434",
        }
    )
    conversation_id = _conversation(_make_session(tmp_path))

    def model(messages, _info):
        wrote_plan = any(
            getattr(part, "tool_name", None) == "write_plan"
            for message in messages
            for part in getattr(message, "parts", [])
        )
        if not wrote_plan:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="write_plan",
                        # Stringified JSON array with integer IDs and 'name' alias (exactly as ling-flash emitted)
                        args={
                            "items": '[{"id": 1, "name": "Variable setup", "status": "in_progress"}, {"id": 2, "name": "Modulator", "status": "pending"}]'
                        },
                    ),
                ]
            )
        return ModelResponse(parts=[TextPart(content="Plan created.")])

    async def run():
        with agents.planner.override(
            model=FunctionModel(model, profile=agents.planner.model.profile)
        ):
            return await agents.planner.run(
                "Plan the flowgraph.", conversation_id=conversation_id
            )

    result = asyncio.run(run())
    assert "Plan created." in result.output

    items = _plan_contents(conversation_id)
    assert items == ["Variable setup", "Modulator"]


def test_text_plan_fallback_recovery_in_session(tmp_path):
    from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

    from grc_agent.chat.session import SessionMixin

    session_id = _make_session(tmp_path)
    conversation_id = _conversation(session_id)

    # Durable plan is currently empty
    assert _plan_contents(conversation_id) == []

    class FakeChat(SessionMixin):
        def __init__(self):
            self._active_session_id = session_id
            self._agent_mode = "planner"
            self._message_history = [
                ModelRequest(parts=[UserPromptPart(content="Design QPSK flowgraph")]),
                ModelResponse(
                    parts=[
                        TextPart(
                            content="### Step 1 — Variables\nSet samp_rate\n### Step 2 — Modulator\nAdd psk_mod"
                        )
                    ]
                ),
            ]
            self._appended_action = False
            self._busy = False

        def _append_implement_plan_action(self, _sid):
            self._appended_action = True

        def set_status(self, msg, error=False):
            pass

    chat = FakeChat()
    asyncio.run(chat._show_implement_plan_if_ready(session_id))

    # Assert that plan was recovered into SqlitePlanStore and action button was enabled
    assert chat._appended_action is True
    assert _plan_contents(conversation_id) == ["Variables", "Modulator"]


