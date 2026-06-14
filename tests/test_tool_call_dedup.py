"""Tests for the per-turn tool-call dedup.

The runtime must short-circuit an identical tool call (same
``(name, canonical_args)`` pair) that already returned successfully
earlier in the same turn. Without this guard, a small local model
that repeats itself can issue the same tool 7-10 times in a row,
exhausting the chat history with no progress.
"""

from __future__ import annotations

import unittest
from typing import Any
from unittest import mock

from grc_agent.agent import GrcAgent
from grc_agent.toolagents_runtime import (
    ToolAgentsLlamaProviderConfig,
    ToolAgentsRunner,
    _canonicalize_args,
)
from ToolAgents.data_models.messages import (
    ChatMessage,
    ChatMessageRole,
    TextContent,
    ToolCallContent,
)


def _now():
    import datetime
    return datetime.datetime.now()


def _make_assistant_with_tool_call(
    name: str, args: dict[str, Any], call_id: str
) -> ChatMessage:
    return ChatMessage(
        id=f"asst-{call_id}",
        role=ChatMessageRole.Assistant,
        content=[
            TextContent(content=""),
            ToolCallContent(
                tool_call_id=call_id,
                tool_call_name=name,
                tool_call_arguments=args,
            ),
        ],
        created_at=_now(),
        updated_at=_now(),
    )


def _make_assistant_text(text: str) -> ChatMessage:
    return ChatMessage(
        id="asst-final",
        role=ChatMessageRole.Assistant,
        content=[TextContent(content=text)],
        created_at=_now(),
        updated_at=_now(),
    )


class CanonicalizeArgsTests(unittest.TestCase):
    def test_dict_key_order_does_not_matter(self) -> None:
        a = _canonicalize_args({"b": 1, "a": 2})
        b = _canonicalize_args({"a": 2, "b": 1})
        self.assertEqual(a, b)

    def test_nested_dict_key_order_does_not_matter(self) -> None:
        a = _canonicalize_args({"outer": {"b": 1, "a": 2}})
        b = _canonicalize_args({"outer": {"a": 2, "b": 1}})
        self.assertEqual(a, b)

    def test_different_values_produce_different_keys(self) -> None:
        self.assertNotEqual(
            _canonicalize_args({"q": "foo"}),
            _canonicalize_args({"q": "bar"}),
        )

    def test_non_json_arguments_dont_crash(self) -> None:
        # Non-serialisable values should fall back to ``repr``.
        result = _canonicalize_args({"a": object()})
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)


class ToolCallDedupTests(unittest.TestCase):
    """Drive ``_run_turn_events`` with a stubbed chat_agent to verify
    that an identical tool call short-circuits on its second
    appearance in the same turn."""

    def _build_runner(
        self,
        step_responses: list[ChatMessage],
        delegate_result: dict[str, Any],
    ) -> tuple[ToolAgentsRunner, GrcAgent, mock.MagicMock]:
        agent = GrcAgent()
        agent.get_tool_schemas_for_turn = mock.MagicMock(return_value=[])  # type: ignore[assignment]

        chat_agent = mock.MagicMock()
        chat_agent.step.side_effect = step_responses
        chat_agent.get_default_settings.return_value = mock.MagicMock()

        provider_config = ToolAgentsLlamaProviderConfig(
            base_url="http://127.0.0.1:1", model="m"
        )
        provider = mock.MagicMock()
        provider.get_response.return_value = step_responses[0]

        runner = ToolAgentsRunner.__new__(ToolAgentsRunner)
        runner.provider_config = provider_config
        runner.provider = provider
        runner.chat_agent = chat_agent

        # Stub the registry builder so the loop sees one delegate.
        registry_builder = mock.MagicMock()
        delegate = mock.MagicMock()
        delegate.invoke.return_value = mock.MagicMock(
            result=delegate_result, executed=True
        )
        registry_builder.delegates = {"query_knowledge": delegate}
        registry_builder.build.return_value = mock.MagicMock()
        # Patch the symbol the loop imports.
        import grc_agent.toolagents_runtime as rt
        original = rt.ToolAgentsRegistryBuilder
        rt.ToolAgentsRegistryBuilder = mock.MagicMock(return_value=registry_builder)
        self.addCleanup(lambda: setattr(rt, "ToolAgentsRegistryBuilder", original))
        return runner, agent, delegate

    def test_identical_call_short_circuits_on_second_appearance(self) -> None:
        # Two turns: first model step has a tool call, second model
        # step has the same tool call again, then a final text answer.
        args = {"domain": "catalog", "query": "analog_noise_source_x"}
        call = _make_assistant_with_tool_call("query_knowledge", args, "c1")
        final = _make_assistant_text("Here is what I found.")
        delegate_result = {
            "ok": True,
            "results": [{"block_id": "analog_noise_source_x"}],
        }
        runner, agent, delegate = self._build_runner(
            [call, call, final], delegate_result
        )
        events = list(
            runner._run_turn_events(
                agent,
                "hi",
                model=None,
                wrapper_eval_telemetry=False,
                max_tool_rounds=4,
                on_tool_start=None,
                on_tool_end=None,
            )
        )
        # The delegate's ``invoke`` should have been called exactly
        # once across the two identical tool calls.
        self.assertEqual(delegate.invoke.call_count, 1)
        # The final event carries the assistant text.
        final_events = [e for e in events if e.get("event") == "final"]
        self.assertEqual(len(final_events), 1)
        self.assertEqual(
            final_events[0]["result"].get("assistant_text"),
            "Here is what I found.",
        )

    def test_different_args_do_not_short_circuit(self) -> None:
        call1 = _make_assistant_with_tool_call(
            "query_knowledge", {"domain": "catalog", "query": "foo"}, "c1"
        )
        call2 = _make_assistant_with_tool_call(
            "query_knowledge", {"domain": "catalog", "query": "bar"}, "c2"
        )
        final = _make_assistant_text("done")
        delegate_result = {"ok": True, "results": []}
        runner, agent, delegate = self._build_runner(
            [call1, call2, final], delegate_result
        )
        list(
            runner._run_turn_events(
                agent,
                "hi",
                model=None,
                wrapper_eval_telemetry=False,
                max_tool_rounds=4,
                on_tool_start=None,
                on_tool_end=None,
            )
        )
        self.assertEqual(delegate.invoke.call_count, 2)


class EndToEndRetryStormTests(unittest.TestCase):
    """Drive ``ToolAgentsRunner._run_turn_events`` with only
    ``chat_agent.step`` mocked. The registry, delegate, validation
    chain, and dedup cache are all real.

    This is the bug class the user hit in session #5: a small local
    model issued ``query_knowledge`` 7 times with identical
    arguments. The dedup must short-circuit the loop.
    """

    def _build_agent_with_query_knowledge(self) -> GrcAgent:
        agent = GrcAgent()
        # ``GrcAgent._tools`` is a name → callable map; ``execute_tool``
        # looks up there. Inject a stub that just returns
        # ``{"ok": True, "results": [...]}``.
        counter = {"calls": 0}

        def stub_query_knowledge(domain: str, query: str) -> dict:
            counter["calls"] += 1
            return {
                "ok": True,
                "domain": domain,
                "query": query,
                "results": [{"block_id": "analog_noise_source_x"}],
            }

        agent._tools["query_knowledge"] = stub_query_knowledge  # type: ignore[assignment]
        return agent, counter  # type: ignore[return-value]

    def test_retry_storm_does_not_re_execute(self) -> None:
        """A 5-call retry storm against ``query_knowledge`` with
        identical args must result in exactly 1 underlying call.
        """
        from unittest import mock

        from grc_agent.toolagents_runtime import (
            ToolAgentsLlamaProviderConfig,
            ToolAgentsRunner,
        )

        agent, counter = self._build_agent_with_query_knowledge()

        # Stub the chat agent to return the same tool call 5 times,
        # then a final text. The real ``_run_turn_events`` loop must
        # short-circuit the duplicates.
        import datetime

        from ToolAgents.data_models.messages import (
            ChatMessage,
            ChatMessageRole,
            TextContent,
            ToolCallContent,
        )
        now = datetime.datetime.now()

        def make_tool_call() -> ChatMessage:
            return ChatMessage(
                id="asst-1",
                role=ChatMessageRole.Assistant,
                content=[
                    TextContent(content=""),
                    ToolCallContent(
                        tool_call_id="call-1",
                        tool_call_name="query_knowledge",
                        tool_call_arguments={
                            "domain": "catalog",
                            "query": "analog_noise_source_x",
                        },
                    ),
                ],
                created_at=now,
                updated_at=now,
            )

        final_text = ChatMessage(
            id="asst-final",
            role=ChatMessageRole.Assistant,
            content=[TextContent(content="Here is what I found.")],
            created_at=now,
            updated_at=now,
        )

        chat_agent = mock.MagicMock()
        chat_agent.step.side_effect = [
            make_tool_call(),
            make_tool_call(),
            make_tool_call(),
            make_tool_call(),
            make_tool_call(),
            final_text,
        ]
        chat_agent.get_default_settings.return_value = mock.MagicMock()

        runner = ToolAgentsRunner.__new__(ToolAgentsRunner)
        runner.provider_config = ToolAgentsLlamaProviderConfig(
            base_url="http://127.0.0.1:1", model="m"
        )
        runner.provider = mock.MagicMock()
        runner.chat_agent = chat_agent

        events = list(
            runner._run_turn_events(
                agent,
                "hi",
                model=None,
                wrapper_eval_telemetry=False,
                max_tool_rounds=8,
                on_tool_start=None,
                on_tool_end=None,
            )
        )

        # The underlying tool was called exactly once despite 5
        # identical requests from the model.
        self.assertEqual(counter["calls"], 1)
        # The final event carries the assistant text.
        final_events = [e for e in events if e.get("event") == "final"]
        self.assertEqual(len(final_events), 1)
        self.assertEqual(
            final_events[0]["result"].get("assistant_text"),
            "Here is what I found.",
        )
        # tool_calls_executed counts the *real* tool executions,
        # not the dedup'd ones.
        self.assertEqual(
            final_events[0]["result"].get("tool_calls_executed"),
            1,
        )
        # tool_calls_requested still counts all 5 model requests.
        self.assertEqual(
            final_events[0]["result"].get("tool_calls_requested"),
            5,
        )

    def test_distinct_args_still_each_execute(self) -> None:
        """Sanity: different queries each get their own underlying
        call. The dedup must not over-match.
        """
        import datetime
        from unittest import mock

        from grc_agent.toolagents_runtime import (
            ToolAgentsLlamaProviderConfig,
            ToolAgentsRunner,
        )
        from ToolAgents.data_models.messages import (
            ChatMessage,
            ChatMessageRole,
            TextContent,
            ToolCallContent,
        )
        now = datetime.datetime.now()

        agent, counter = self._build_agent_with_query_knowledge()

        def make_call(query: str) -> ChatMessage:
            return ChatMessage(
                id=f"asst-{query}",
                role=ChatMessageRole.Assistant,
                content=[
                    TextContent(content=""),
                    ToolCallContent(
                        tool_call_id=f"call-{query}",
                        tool_call_name="query_knowledge",
                        tool_call_arguments={
                            "domain": "catalog",
                            "query": query,
                        },
                    ),
                ],
                created_at=now,
                updated_at=now,
            )

        final_text = ChatMessage(
            id="asst-final",
            role=ChatMessageRole.Assistant,
            content=[TextContent(content="done")],
            created_at=now,
            updated_at=now,
        )

        chat_agent = mock.MagicMock()
        chat_agent.step.side_effect = [
            make_call("foo"),
            make_call("bar"),
            make_call("baz"),
            final_text,
        ]
        chat_agent.get_default_settings.return_value = mock.MagicMock()

        runner = ToolAgentsRunner.__new__(ToolAgentsRunner)
        runner.provider_config = ToolAgentsLlamaProviderConfig(
            base_url="http://127.0.0.1:1", model="m"
        )
        runner.provider = mock.MagicMock()
        runner.chat_agent = chat_agent

        list(
            runner._run_turn_events(
                agent,
                "hi",
                model=None,
                wrapper_eval_telemetry=False,
                max_tool_rounds=8,
                on_tool_start=None,
                on_tool_end=None,
            )
        )
        self.assertEqual(counter["calls"], 3)


if __name__ == "__main__":
    unittest.main()
