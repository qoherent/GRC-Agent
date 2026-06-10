"""Eval harness for the ToolAgents-backed chat loop.

The eval feeds canned model responses (raw OpenAI-shaped JSON) into
the real ``ToolAgentsRunner._run_turn_events`` loop and asserts
post-conditions over the final result dict and the resulting
``ChatHistory``. No real llama.cpp, no network — runs in CI.

Each fixture is a JSON file with the shape:

```
{
  "id": "happy_path_tool_call",
  "description": "Model issues one tool call, gets a result, returns text.",
  "user_message": "Inspect the active graph.",
  "initial_chat_history": [...],   // optional seed messages
  "model_responses": [              // raw OpenAI-shape payloads
    {
      "choices": [{
        "index": 0,
        "finish_reason": "tool_calls",
        "message": {
          "role": "assistant",
          "content": null,
          "tool_calls": [{
            "id": "call-1",
            "type": "function",
            "function": {
              "name": "inspect_graph",
              "arguments": "{\"view\": \"overview\"}"
            }
          }]
        }
      }]
    },
    ...
  ],
  "tool_stubs": {                  // tool_name -> result_factory
    "inspect_graph": {
      "ok": true,
      "view": "overview",
      "summary": {"blocks": [], "counts": "b=0 c=0 v=0"}
    }
  },
  "expect": {
    "ok": true,
    "assistant_text": "The graph has no blocks.",
    "tool_calls_executed": 1,
    "tool_calls_requested": 1,
    "final_role": "assistant"
  }
}
```

The harness converts each ``model_responses`` entry into a
``ChatMessage`` via ``ChatMessage.from_dictionaries``, then drives
``_run_turn_events`` with a real ``GrcAgent`` whose ``_tools`` map
points at the stub factories.

The eval catches:
- retry-storm guard behavior (1 underlying call, N dedups)
- empty-assistant-bubble detection in the final result
- legacy-session refusal contract (separate fixture set)
- tool-not-allowed gating
- schema validation rejection
- max-tool-rounds safety ceiling
- change_graph ambiguity and preflight paths
- mid-loop reminder injection

See ``fixtures/*.json`` for the initial scenarios.
"""

from __future__ import annotations

import dataclasses
import datetime
import importlib
import importlib.util
import json
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

import contextlib

_EVAL_DIR = Path(__file__).resolve().parent
_FIXTURES_DIR = _EVAL_DIR / "fixtures"


def _now() -> datetime.datetime:
    return datetime.datetime.now()


def _build_agent(tool_stubs: dict[str, Any]) -> Any:
    """Build a real ``GrcAgent`` whose ``_tools`` map points at stub
    factories. The factories take keyword args matching the tool's
    schema and return the canned result dict.
    """
    from grc_agent.agent import GrcAgent
    from ToolAgents.data_models.chat_history import ChatHistory

    agent = GrcAgent()
    agent.chat_history = ChatHistory()

    def make_factory(name: str, payload: Any) -> Any:
        def factory(**kwargs: Any) -> Any:
            return payload

        factory.__name__ = f"stub_{name}"
        return factory

    for name, payload in tool_stubs.items():
        agent._tools[name] = make_factory(name, payload)  # type: ignore[assignment]
    return agent


def _build_runner(agent: Any, responses: list[Any]) -> Any:
    """Build a ``ToolAgentsRunner`` whose chat agent returns canned
    ``ChatMessage`` objects from the given list, in order.
    """
    from grc_agent.toolagents_runtime import (
        ToolAgentsLlamaProviderConfig,
        ToolAgentsRunner,
    )

    chat_agent = mock.MagicMock()
    chat_agent.get_default_settings.return_value = mock.MagicMock()

    def step_factory(responses: list[Any]):
        iterator = iter(responses)

        def step(*args: Any, **kwargs: Any) -> Any:
            try:
                return next(iterator)
            except StopIteration:
                # Caller's loop should have stopped by then; return a
                # benign no-tool-call assistant message as a safety net.
                from ToolAgents.data_models.messages import (
                    ChatMessage,
                    ChatMessageRole,
                    TextContent,
                )
                return ChatMessage(
                    id="safety-net",
                    role=ChatMessageRole.Assistant,
                    content=[TextContent(content="")],
                    created_at=_now(),
                    updated_at=_now(),
                )

        return step

    runner = ToolAgentsRunner.__new__(ToolAgentsRunner)
    runner.provider_config = ToolAgentsLlamaProviderConfig(
        base_url="http://127.0.0.1:1", model="m"
    )
    runner.provider = mock.MagicMock()
    runner._step_responses = responses
    runner.chat_agent = mock.MagicMock()
    runner.chat_agent.step.side_effect = step_factory(responses)
    return runner


def _model_responses_from_fixture(raw_responses: list[dict[str, Any]]) -> list[Any]:
    """Convert a fixture's raw OpenAI-shape payloads into
    ``ChatMessage`` objects.
    """
    from ToolAgents.data_models.messages import ChatMessage

    out: list[Any] = []
    for raw in raw_responses:
        # Each OpenAI response carries one assistant choice; wrap in
        # the standard {"role": "assistant", ...} shape the
        # library's from_dictionaries expects.
        choice = raw["choices"][0]
        msg = choice["message"]
        # ``from_dictionaries`` only handles simple role+content.
        # For assistant tool_calls, build the ChatMessage directly.
        if msg.get("tool_calls"):
            from ToolAgents.data_models.messages import (
                ChatMessageRole,
                ToolCallContent,
            )
            import json as _json
            now = _now()
            content = []
            if msg.get("content"):
                from ToolAgents.data_models.messages import TextContent
                content.append(TextContent(content=msg["content"]))
            for tc in msg["tool_calls"]:
                fn = tc.get("function", {})
                args = fn.get("arguments", "{}")
                try:
                    parsed_args = _json.loads(args)
                except _json.JSONDecodeError:
                    parsed_args = args
                content.append(
                    ToolCallContent(
                        tool_call_id=tc.get("id", "call"),
                        tool_call_name=fn.get("name", ""),
                        tool_call_arguments=parsed_args,
                    )
                )
            out.append(
                ChatMessage(
                    id=raw.get("id", str(_now().timestamp())),
                    role=ChatMessageRole.Assistant,
                    content=content,
                    created_at=now,
                    updated_at=now,
                )
            )
        else:
            # Pure text response.
            from ToolAgents.data_models.messages import (
                ChatMessageRole,
                TextContent,
            )
            now = _now()
            out.append(
                ChatMessage(
                    id=raw.get("id", str(_now().timestamp())),
                    role=ChatMessageRole.Assistant,
                    content=[TextContent(content=msg.get("content", "") or "")],
                    created_at=now,
                    updated_at=now,
                )
            )
    return out


@dataclasses.dataclass
class EvalResult:
    fixture_id: str
    description: str
    passed: bool
    failures: list[str]
    duration_ms: float


def run_fixture(fixture_path: Path) -> EvalResult:
    """Run one fixture end-to-end. Returns the outcome."""
    import time
    from ToolAgents.data_models.chat_history import ChatHistory

    started = time.perf_counter()
    raw = json.loads(fixture_path.read_text(encoding="utf-8"))
    fixture_id = raw["id"]
    description = raw.get("description", "")

    failures: list[str] = []

    try:
        agent = _build_agent(raw.get("tool_stubs", {}))
        # Seed chat history if the fixture provides one.
        for seed in raw.get("initial_chat_history", []):
            role = seed["role"]
            text = seed.get("text", "")
            if role == "user":
                agent.chat_history.add_user_message(text)
            elif role == "assistant":
                agent.chat_history.add_assistant_message(text)

        responses = _model_responses_from_fixture(raw["model_responses"])
        runner = _build_runner(agent, responses)

        events = list(
            runner._run_turn_events(
                agent,
                raw["user_message"],
                model=None,
                wrapper_eval_telemetry=False,
                max_tool_rounds=int(raw.get("max_tool_rounds", 4)),
                on_tool_start=None,
                on_tool_end=None,
            )
        )

        final = next(
            (e["result"] for e in events if e.get("event") == "final"), {}
        )

        # Assertions.
        expect = raw.get("expect", {})
        for key, expected in expect.items():
            actual = final.get(key)
            if actual != expected:
                failures.append(
                    f"expect.{key}: got {actual!r}, want {expected!r}"
                )

        # Always check: the final result has ok=True or a clear error.
        if "ok" in expect and "ok" not in final:
            failures.append(f"final result missing 'ok': {final!r}")

    except Exception as exc:
        failures.append(f"exception during run: {exc!r}")

    elapsed_ms = (time.perf_counter() - started) * 1000
    return EvalResult(
        fixture_id=fixture_id,
        description=description,
        passed=not failures,
        failures=failures,
        duration_ms=elapsed_ms,
    )


def run_all_fixtures() -> list[EvalResult]:
    """Run every fixture in the fixtures/ dir and return the results."""
    results = []
    for path in sorted(_FIXTURES_DIR.glob("*.json")):
        results.append(run_fixture(path))
    return results


def write_measured_behavior_block(results: list[EvalResult]) -> str:
    """Produce a Markdown block that documents the baseline eval run.

    Used to update BLUEPRINT's "Measured Behavior" section after a
    baseline eval pass.
    """
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    duration_total_ms = sum(r.duration_ms for r in results)
    lines = [
        "## Measured Behavior (eval_chat baseline)",
        "",
        f"Fixtures run: {total}. Passing: {passed}. "
        f"Total wall time: {duration_total_ms:.1f} ms.",
        "",
        "| Fixture | Status | Duration (ms) |",
        "| --- | --- | --- |",
    ]
    for r in results:
        status = "PASS" if r.passed else "FAIL"
        lines.append(f"| `{r.fixture_id}` | {status} | {r.duration_ms:.1f} |")
    failures = [r for r in results if not r.passed]
    if failures:
        lines.append("")
        lines.append("### Failures")
        for r in failures:
            lines.append(f"- `{r.fixture_id}`: {r.description}")
            for f in r.failures:
                lines.append(f"  - {f}")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    results = run_all_fixtures()
    print(write_measured_behavior_block(results))
    if not all(r.passed for r in results):
        sys.exit(1)
