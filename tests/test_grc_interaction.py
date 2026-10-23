"""Exercise the registered interaction schema through the actual framework."""
import asyncio
from types import SimpleNamespace

import pytest
from pydantic_ai import Agent, DeferredToolRequests, DeferredToolResults, ToolDenied
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from grc_agent.agent import grc_tools


@pytest.fixture(autouse=True)
def ordinary_loop():
    previous = asyncio.get_event_loop_policy()
    asyncio.set_event_loop_policy(asyncio.DefaultEventLoopPolicy())
    yield
    asyncio.set_event_loop_policy(previous)


def agent_for(arguments):
    def model(messages, _info):
        if len(messages) == 1:
            return ModelResponse(parts=[ToolCallPart("grc_interaction", arguments, "interaction")])
        return ModelResponse(parts=[TextPart("done")])
    return Agent(FunctionModel(model), tools=grc_tools(), output_type=[str, DeferredToolRequests])


def test_start_defers_and_denial_never_executes():
    calls = []
    async def run_flowgraph(**kwargs):
        calls.append(kwargs)
        return {"status": "started"}
    agent = agent_for({"mode": "start"})
    deps = SimpleNamespace(run_flowgraph=run_flowgraph)
    result = agent.run_sync("run", deps=deps)
    assert isinstance(result.output, DeferredToolRequests)
    assert not calls
    resumed = agent.run_sync(
        message_history=result.all_messages(), deps=deps,
        deferred_tool_results=DeferredToolResults(approvals={"interaction": ToolDenied("No")}),
    )
    assert resumed.output == "done"
    assert not calls


def test_approved_start_uses_native_contract_and_stop_needs_no_approval():
    calls = []
    async def run_flowgraph(**kwargs):
        calls.append(kwargs)
        return {"status": "started", "run_id": 4}
    deps = SimpleNamespace(run_flowgraph=run_flowgraph)
    agent = agent_for({"mode": "start", "wait": False})
    result = agent.run_sync("run", deps=deps)
    agent.run_sync(message_history=result.all_messages(), deps=deps,
                   deferred_tool_results=DeferredToolResults(approvals={"interaction": True}))
    assert calls[0]["action"] == "start"
    assert calls[0]["wait"] is False
    result = agent_for({"mode": "stop"}).run_sync("stop", deps=deps)
    assert result.output == "done"
    assert calls[-1]["action"] == "stop"


@pytest.mark.parametrize("arguments", [
    {},  # Required mode is rejected by schema validation before approval.
    {"mode": "start", "count": 12},
    {"mode": "stop", "sink": "out"},
    {"mode": "start", "timeout_seconds": -1},
    {"mode": "start", "wait": False, "stop_after_seconds": 1},
    {"mode": "measure"},
    {"mode": "plot"},
    {"mode": "measure", "run_id": 1, "offset": -1},
    {"mode": "measure", "run_id": 1, "sample_rate": float("inf")},
])
def test_bad_arguments_rejected_before_approval_or_execution(arguments):
    calls = []

    async def run_flowgraph(**kwargs):
        calls.append(kwargs)
        return {"status": "unexpected"}

    result = agent_for(arguments).run_sync("inspect", deps=SimpleNamespace(run_flowgraph=run_flowgraph))
    assert not isinstance(result.output, DeferredToolRequests)
    assert not calls
    assert result.output == "done"
    parts = [p for m in result.all_messages() for p in m.parts]
    assert any(p.part_kind == "retry-prompt" for p in parts)
    assert not any(p.part_kind == "tool-return" and getattr(p, "outcome", None) == "success" for p in parts)


def test_measure_and_plot_dispatch_image_flag_and_share_argument_schema():
    from grc_agent.agent import grc_tools

    tool = next(t for t in grc_tools() if t.name == "grc_interaction")
    schema = tool.function_schema.json_schema
    assert "plot" in schema["properties"]["mode"]["enum"]
    mode_description = schema["properties"]["mode"]["description"]
    assert "measure=numbers" in mode_description
    assert "plot=numbers+image" in mode_description
    assert "image-capable model for plot/canvas" in mode_description
    for mode in ("measure", "plot"):
        tool.function_schema.validator.validate_python(
            {"mode": mode, "run_id": 2, "offset": 3, "count": 4, "sample_rate": 8.0}
        )
    async def run_observed_flowgraph():
        raise AssertionError("not used")

    calls = []
    def measure_run(run_id, offset, count, sample_rate, *, include_image):
        calls.append((run_id, offset, count, sample_rate, include_image))
        return {"include_image": include_image}

    deps = SimpleNamespace(
        run_observed_flowgraph=run_observed_flowgraph,
        measure_run=measure_run,
        capture_canvas=lambda: {"canvas": True},
    )
    for mode, expected in (("measure", False), ("plot", True)):
        result = agent_for({
            "mode": mode, "run_id": 2, "offset": 3, "count": 4, "sample_rate": 8.0,
        }).run_sync("inspect", deps=deps)
        assert result.output == "done"
        assert calls[-1] == (2, 3, 4, 8.0, expected)


def test_log_uses_retained_evidence_and_canvas_reports_missing_capability():
    result = agent_for({"mode": "log"}).run_sync(
        "inspect", deps=SimpleNamespace(get_run_log=lambda: {"run_id": 8, "ran_successfully": False})
    )
    parts = [p for m in result.all_messages() for p in m.parts]
    assert any(p.part_kind == "tool-return" and '"run_id": 8' in str(p.content) for p in parts)
    result = agent_for({"mode": "canvas"}).run_sync("inspect", deps=object())
    assert any(getattr(p, "outcome", None) == "failed" for m in result.all_messages() for p in m.parts)


def test_plot_content_does_not_hide_earlier_graph_mutation_from_final_validation():
    from unittest.mock import MagicMock

    from pydantic_ai import BinaryContent, ModelRetry
    from pydantic_ai.messages import ModelRequest, ToolReturnPart, UserPromptPart

    from grc_agent.agent import validate_flowgraph_state

    graph = MagicMock()
    graph.is_valid.return_value = False
    graph.iter_error_messages.return_value = [("sink", "input unconnected")]
    messages = [
        ModelRequest(parts=[UserPromptPart("edit and verify")]),
        ModelRequest(parts=[ToolReturnPart("change_graph", {"ok": True, "pre_existing_errors": []})]),
        ModelRequest(parts=[
            ToolReturnPart("grc_interaction", {"evidence": "topology"}),
            UserPromptPart([BinaryContent(data=b"png", media_type="image/png")]),
        ]),
    ]
    with pytest.raises(ModelRetry, match="input unconnected"):
        asyncio.run(validate_flowgraph_state(SimpleNamespace(deps=graph, messages=messages), "done"))
    graph.validate.assert_called_once()
