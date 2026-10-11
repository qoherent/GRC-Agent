"""StopGracefully drives the real pydantic-ai loop (FunctionModel, both the
non-streaming and the node.stream paths); nothing here counts on hand-made
ctx mocks."""

import asyncio
import json

import pytest
from pydantic_ai import Agent, ToolFailed
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel
from pydantic_ai.usage import RunUsage
from pydantic_graph import End

from grc_agent.agent import StopGracefully


def _failure_message(tool: str) -> str:
    return (
        f"`{tool}` failed terminally 3 times this turn. Stopping rather than retrying it "
        "further; completed actions may remain. You can send a follow-up to continue."
    )


def _limit_message(n: int) -> str:
    return (
        f"The agent reached the {n}-request limit for this turn. Stopping rather than "
        "continuing the tool loop; completed actions may remain. You can send a follow-up "
        "to continue."
    )


class Script:
    """Model that answers each request with the next scripted batch of tool
    names, then plain text. Serves both the request and the stream path."""

    def __init__(self, batches: list[list[str]]):
        self.batches = batches
        self.calls = 0

    def _next(self) -> list[str]:
        batch = self.batches[self.calls] if self.calls < len(self.batches) else []
        self.calls += 1
        return batch

    def model(self) -> FunctionModel:
        async def respond(_m: list[ModelMessage], _i: AgentInfo) -> ModelResponse:
            batch = self._next()
            if not batch:
                return ModelResponse(parts=[TextPart(content="done")])
            return ModelResponse(
                parts=[ToolCallPart(tool_name=n, args={}, tool_call_id=f"c{self.calls}_{i}")
                       for i, n in enumerate(batch)]
            )

        async def stream(_m: list[ModelMessage], _i: AgentInfo):
            batch = self._next()
            if not batch:
                yield "done"
                return
            yield {
                i: DeltaToolCall(name=n, json_args=json.dumps({}), tool_call_id=f"c{self.calls}_{i}")
                for i, n in enumerate(batch)
            }

        return FunctionModel(respond, stream_function=stream)


def make_agent(script: Script, **capability) -> Agent:
    agent = Agent(script.model(), capabilities=[StopGracefully(**capability)])

    @agent.tool_plain
    def ok() -> str:
        return "fine"

    for name in ("dead", "dead_a", "dead_b", "dead_c"):
        agent.tool_plain(name=name)(_fail)
    return agent


def _fail() -> str:
    raise ToolFailed("nope")


def run_nonstreaming(agent: Agent, prompt="go", **kw):
    return asyncio.run(agent.run(prompt, **kw))


def run_streaming(agent: Agent, prompt="go", **kw):
    async def go():
        async with agent.iter(prompt, **kw) as run:
            node = run.next_node
            while node is not None and not isinstance(node, End):
                if Agent.is_model_request_node(node):
                    async with node.stream(run.ctx) as stream:
                        async for _ in stream:
                            pass
                node = await run.next(node)
        return run.result

    return asyncio.run(go())


@pytest.fixture(params=[run_nonstreaming, run_streaming], ids=["request", "stream"])
def run(request):
    return request.param


def test_three_identical_terminal_failures_stop_before_fourth_model_call(run):
    script = Script([["dead"]] * 10)
    with pytest.raises(UsageLimitExceeded) as err:
        run(make_agent(script))
    assert str(err.value).startswith(_failure_message("dead"))
    assert script.calls == 3


def test_a_success_breaks_the_streak(run):
    script = Script([["dead"], ["dead"], ["ok"], ["dead"], ["dead"]])
    assert run(make_agent(script)).output == "done"
    assert script.calls == 6


def test_different_failing_tools_do_not_sum(run):
    script = Script([["dead_a"], ["dead_b"], ["dead_c"]])
    assert run(make_agent(script)).output == "done"


def test_mixed_batch_orders_parts_newest_first(run):
    stopped = Script([["ok", "dead", "dead", "dead"]] * 3)
    with pytest.raises(UsageLimitExceeded):
        run(make_agent(stopped))
    assert stopped.calls == 1

    recovered = Script([["dead", "dead", "dead", "ok"], ["dead"]])
    assert run(make_agent(recovered)).output == "done"
    assert recovered.calls == 3


def test_prior_turn_failures_do_not_count_against_a_new_user_prompt(run):
    history: list[ModelMessage] = []
    for i in range(3):
        history += [
            ModelRequest(parts=[UserPromptPart(content=f"u{i}")] if i == 0 else []),
            ModelResponse(parts=[ToolCallPart(tool_name="dead", args={}, tool_call_id=f"h{i}")]),
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="dead", content="nope", tool_call_id=f"h{i}", outcome="failed"
                    )
                ]
            ),
        ]
    history.append(ModelResponse(parts=[TextPart(content="gave up")]))
    history = [m for m in history if m.parts]
    script = Script([[]])
    assert run(make_agent(script), "try again", message_history=history).output == "done"
    assert script.calls == 1


@pytest.mark.parametrize("limit", [2])
def test_request_limit_stops_exactly_before_the_next_request(run, limit):
    script = Script([["ok"]] * 10)
    with pytest.raises(UsageLimitExceeded) as err:
        run(make_agent(script, max_requests=limit))
    assert str(err.value).startswith(_limit_message(limit))
    assert script.calls == limit


def test_shared_usage_carries_the_request_count_across_runs():
    script = Script([["ok"]] * 10)
    agent = make_agent(script, max_requests=2)
    usage = RunUsage()
    with pytest.raises(UsageLimitExceeded):
        asyncio.run(agent.run("go", usage=usage))
    assert usage.requests == 2
    with pytest.raises(UsageLimitExceeded):
        asyncio.run(agent.run("again", usage=usage))
    assert script.calls == 2
