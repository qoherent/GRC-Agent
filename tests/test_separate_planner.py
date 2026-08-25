"""Hermetic contracts for the manual, read-only planner handoff."""

import asyncio

import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ThinkingPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai_harness.planning import PlanItem, SqlitePlanStore


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path, monkeypatch):
    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))
    monkeypatch.setattr(
        "grc_agent.agent_factory.resolve_model_context_length", lambda *_args, **_kwargs: None
    )
    from grc_agent import db

    db._initialized_paths.clear()
    db._step_stores.clear()


def _bundle():
    from grc_agent.agent_factory import build_agents_from_cfg

    return build_agents_from_cfg(
        {
            "provider": "ollama_local",
            "model": "test-model",
            "ollama_base_url": "http://127.0.0.1:11434",
        }
    )


def _conversation(tmp_path) -> str:
    from grc_agent.db import conversation_id_for_session, save_session

    graph = tmp_path / "planner.grc"
    graph.touch()
    session_id = save_session(None, str(graph), [])
    assert session_id is not None
    return conversation_id_for_session(session_id)


def _function_tools(agent) -> set[str]:
    seen: set[str] = set()

    def model(_messages, info):
        seen.update(tool.name for tool in info.function_tools)
        return ModelResponse(parts=[TextPart(content="done")])

    async def run():
        with agent.override(model=FunctionModel(model, profile=agent.model.profile)):
            await agent.run("Inspect the available tools.")

    asyncio.run(run())
    return seen


def test_executor_and_planner_have_disjoint_mutation_surfaces():
    agents = _bundle()
    executor_tools = _function_tools(agents.executor)
    planner_tools = _function_tools(agents.planner)

    assert {"change_graph", "save_block", "write_file", "edit_file"} <= executor_tools
    assert not ({"write_plan", "read_plan", "add_task", "update_task_status"} & executor_tools)
    assert {"write_plan", "read_plan", "inspect_graph", "read_file"} <= planner_tools
    assert not (
        {"change_graph", "save_block", "write_file", "edit_file", "create_directory"}
        & planner_tools
    )


def test_run_and_shell_tools_never_reach_the_planner():
    """The planner is structurally read-only: the run/stop flowgraph tools
    (and, when the shell capability lands, its exec tools) are physical-world
    side effects and must be absent from the planner's model-visible surface
    by construction — the fail-closed allowlist, not prompt text."""
    agents = _bundle()
    planner_tools = _function_tools(agents.planner)
    assert not (
        {"run_flowgraph", "stop_flowgraph", "run_command", "start_command"} & planner_tools
    )
    # The executor, meanwhile, does get the run tools.
    assert {"run_flowgraph", "stop_flowgraph"} <= _function_tools(agents.executor)


def test_planner_writes_plan_and_preserves_prior_history(tmp_path):
    agents = _bundle()
    conversation_id = _conversation(tmp_path)
    saw_prior_context = False

    def model(messages, _info):
        nonlocal saw_prior_context
        saw_prior_context = any(
            getattr(part, "content", None) == "Earlier executor answer"
            for message in messages
            for part in getattr(message, "parts", [])
        )
        wrote_plan = any(
            getattr(part, "tool_name", None) == "write_plan"
            for message in messages
            for part in getattr(message, "parts", [])
        )
        if not wrote_plan:
            return ModelResponse(
                parts=[
                    ThinkingPart(content="Ground the handoff before proposing edits."),
                    ToolCallPart(
                        tool_name="write_plan",
                        args={"items": [{"content": "Inspect the active flowgraph"}]},
                    ),
                ]
            )
        return ModelResponse(parts=[TextPart(content="Plan: inspect the active flowgraph.")])

    prior = [ModelResponse(parts=[TextPart(content="Earlier executor answer")])]

    async def run():
        with agents.planner.override(
            model=FunctionModel(model, profile=agents.planner.model.profile)
        ):
            return await agents.planner.run(
                "Create the plan.",
                message_history=prior,
                conversation_id=conversation_id,
            )

    result = asyncio.run(run())
    from grc_agent.db import get_db_path, get_step_store

    items = asyncio.run(
        SqlitePlanStore(str(get_db_path()), session=conversation_id).get_items()
    )
    runs = asyncio.run(get_step_store().list_runs(conversation_id=conversation_id))

    assert saw_prior_context is True
    assert [item.content for item in items] == ["Inspect the active flowgraph"]
    assert "Plan: inspect" in result.output
    assert any(run.agent_name == "grc_planner" for run in runs)
    assert any(
        isinstance(part, ThinkingPart)
        for message in result.all_messages()
        for part in getattr(message, "parts", [])
    )


def test_executor_receives_durable_plan_as_ephemeral_read_only_handoff(tmp_path):
    agents = _bundle()
    conversation_id = _conversation(tmp_path)
    from grc_agent.db import get_db_path

    asyncio.run(
        SqlitePlanStore(str(get_db_path()), session=conversation_id).set_items(
            [PlanItem(content="Inspect, then change the graph")]
        )
    )
    seen_text = ""

    def model(messages, _info):
        nonlocal seen_text
        seen_text = "\n".join(
            str(getattr(part, "content", ""))
            for message in messages
            for part in getattr(message, "parts", [])
        )
        return ModelResponse(parts=[TextPart(content="ready")])

    async def run():
        with agents.executor.override(
            model=FunctionModel(model, profile=agents.executor.model.profile)
        ):
            return await agents.executor.run(
                "Use the approved plan.", conversation_id=conversation_id
            )

    result = asyncio.run(run())
    persisted_text = "\n".join(
        str(getattr(part, "content", ""))
        for message in result.all_messages()
        for part in getattr(message, "parts", [])
    )

    assert "<execution-plan>" in seen_text
    assert "Inspect, then change the graph" in seen_text
    assert "<execution-plan>" not in persisted_text
