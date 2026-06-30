"""ToolAgents runtime contract tests for the flat MVP wrapper surface."""

from __future__ import annotations

import datetime
import unittest
import uuid
from typing import Any
from unittest import mock

from grc_agent.agent import GrcAgent
from grc_agent.runtime.model_context import MVP_TOOL_SURFACE
from grc_agent.runtime.tool_schemas import build_tool_schemas
from grc_agent.toolagents_runtime import (
    ToolAgentsRegistryBuilder,
    ToolAgentsToolDelegate,
    _function_tool_from_openai_tool,
)
from ToolAgents import FunctionTool
from ToolAgents.agents import ChatToolAgent
from ToolAgents.data_models.messages import ChatMessage, ChatMessageRole, TextContent


def _assistant_text(text: str) -> ChatMessage:
    now = datetime.datetime.now()
    return ChatMessage(
        id=str(uuid.uuid4()),
        role=ChatMessageRole.Assistant,
        content=[TextContent(content=text)],
        created_at=now,
        updated_at=now,
    )


class ToolAgentsImportTests(unittest.TestCase):
    def test_dependency_imports(self) -> None:
        from ToolAgents import ToolRegistry
        from ToolAgents.provider import OpenAIChatAPI

        self.assertIsNotNone(FunctionTool)
        self.assertIsNotNone(ToolRegistry)
        self.assertIsNotNone(ChatToolAgent)
        self.assertIsNotNone(OpenAIChatAPI)


class ToolAgentsRegistryTests(unittest.TestCase):
    def test_registry_builder_exposes_exactly_mvp_wrappers(self) -> None:
        agent = GrcAgent()
        registry = ToolAgentsRegistryBuilder(agent).build(set(MVP_TOOL_SURFACE.model_tool_names))

        tools = registry.get_openai_tools()
        names = [tool["function"]["name"] for tool in tools]
        self.assertEqual(names, list(MVP_TOOL_SURFACE.model_tool_names))

    def test_openai_schema_conversion_preserves_flat_change_graph_contract(self) -> None:
        schema = next(
            item
            for item in build_tool_schemas(("change_graph",))
            if item["function"]["name"] == "change_graph"
        )
        agent = GrcAgent()
        delegate = ToolAgentsToolDelegate(agent, "change_graph")
        tool = _function_tool_from_openai_tool(schema, delegate)
        converted = tool.to_openai_tool()

        self.assertEqual(converted["function"]["name"], "change_graph")
        self.assertEqual(
            converted["function"]["parameters"]["additionalProperties"],
            schema["function"]["parameters"]["additionalProperties"],
        )
        props = converted["function"]["parameters"]["properties"]
        for field in (
            "add_blocks",
            "remove_blocks",
            "update_params",
            "update_states",
            "add_connections",
            "remove_connections",
            "force",
        ):
            self.assertIn(field, props)
        for legacy_field in (
            "op",
            "args",
            "dry_run",
            "user_goal",
            "state_revision",
            "preview_token",
        ):
            self.assertNotIn(legacy_field, props)


class ToolAgentsDelegateTests(unittest.TestCase):
    def test_delegate_validates_schema_before_execution(self) -> None:
        agent = GrcAgent()
        delegate = ToolAgentsToolDelegate(agent, "inspect_graph")

        with mock.patch.object(agent, "execute_tool") as execute_tool:
            result = delegate.invoke(
                {"bogus": True},
                allowed_tool_names=set(MVP_TOOL_SURFACE.model_tool_names),
            )

        self.assertFalse(result.executed)
        self.assertFalse(result.result["ok"])
        self.assertIn("error_type", result.result)
        execute_tool.assert_not_called()

    def test_delegate_rejects_internal_tool_without_execution(self) -> None:
        agent = GrcAgent()
        delegate = ToolAgentsToolDelegate(agent, "legacy_internal_tool")

        with mock.patch.object(agent, "execute_tool") as execute_tool:
            result = delegate.invoke(
                {"transaction": {}},
                allowed_tool_names=set(MVP_TOOL_SURFACE.model_tool_names),
            )

        self.assertFalse(result.executed)
        self.assertEqual(result.result["error_type"], "tool_not_allowed_for_surface")
        execute_tool.assert_not_called()

    def test_delegate_rejects_legacy_change_graph_shape(self) -> None:
        agent = GrcAgent()
        delegate = ToolAgentsToolDelegate(agent, "change_graph")

        with mock.patch.object(agent, "execute_tool") as execute_tool:
            result = delegate.invoke(
                {
                    "op": "set_param",
                    "dry_run": False,
                    "args": {
                        "instance_name": "samp_rate",
                        "param_key": "value",
                        "param_value": "48000",
                    },
                },
                allowed_tool_names=set(MVP_TOOL_SURFACE.model_tool_names),
            )

        self.assertFalse(result.executed)
        self.assertFalse(result.result["ok"])
        execute_tool.assert_not_called()


class ToolAgentsHistoryTests(unittest.TestCase):
    def test_assistant_text_helper_builds_message(self) -> None:
        message = _assistant_text("hello")

        self.assertEqual(message.role, ChatMessageRole.Assistant)
        self.assertEqual(len(message.content), 1)


class RuntimeChatHistoryContractTests(unittest.TestCase):
    def test_runtime_path_uses_typed_chat_history(self) -> None:
        """The main ``run_turn`` path stores the assistant ``ChatMessage``
        returned by ``chat_agent.step`` directly into the agent's
        ``ChatHistory``. This is the contract the chat-history refactor
        relies on."""
        from grc_agent.agent import GrcAgent

        agent = GrcAgent()
        agent.chat_history.add_user_message("hi")
        agent.chat_history.add_message(_assistant_text("hello back"))
        self.assertEqual(agent.chat_history.get_message_count(), 2)
        last = agent.chat_history.get_messages()[-1]
        self.assertEqual(last.role, ChatMessageRole.Assistant)
        self.assertEqual(last.get_as_text(), "hello back")


class RuntimeDirectivePersistenceTests(unittest.TestCase):
    """``agent.chat_history`` is the source of truth and must not be
    permanently mutated by the turn loop. Episode pruning happens
    transiently on a snapshot inside ``render_model_messages`` at
    send-time; the persisted history keeps every message (including
    ``<runtime_directive>`` notes and the original message structure)."""

    def _runner_with_final(self, agent: GrcAgent) -> Any:
        from grc_agent.toolagents_runtime import (
            ToolAgentsLlamaProviderConfig,
            ToolAgentsRunner,
        )

        cfg = ToolAgentsLlamaProviderConfig(
            base_url="http://127.0.0.1:11434",
            model="m",
            timeout_seconds=1.0,
        )
        chat_agent = ChatToolAgent(chat_api=cfg.create_provider())

        def _step(*_args: Any, **_kwargs: Any) -> Any:
            return _assistant_text("done")

        chat_agent.step = _step  # type: ignore[method-assign]
        return ToolAgentsRunner(cfg, chat_agent=chat_agent)

    def test_runtime_directive_survives_a_turn(self) -> None:
        from grc_agent.agent import GrcAgent

        agent = GrcAgent()
        # A directive note sits in dead history (before the last human
        # message), exactly where the model_context pruner would strip it
        # if the turn loop mutated history in place.
        agent.chat_history.add_user_message(
            "<runtime_directive>\nprior loop note\n</runtime_directive>"
        )
        agent.chat_history.add_user_message("the prior user question")
        agent.chat_history.add_message(_assistant_text("prior answer"))
        runner = self._runner_with_final(agent)

        list(runner.stream_turn(agent, "a follow-up message"))

        texts = [m.get_as_text() for m in agent.chat_history.get_messages()]
        self.assertTrue(
            any("<runtime_directive>" in (t or "") for t in texts),
            "a runtime-directive message already in history must not be "
            "permanently stripped by the turn loop",
        )


class ToolAgentsProviderConfigTests(unittest.TestCase):
    def test_openrouter_settings_includes_provider_extra_body(self) -> None:
        from grc_agent.toolagents_runtime import GrcOpenAIChatAPI, ToolAgentsLlamaProviderConfig

        mock_provider = mock.MagicMock(spec=GrcOpenAIChatAPI)
        mock_settings = mock.MagicMock()
        mock_provider.get_default_settings.return_value = mock_settings

        with mock.patch.dict(
            "os.environ",
            {"OPENROUTER_PROVIDER_ORDER": "alibaba", "OPENROUTER_ALLOW_FALLBACKS": "false"},
        ):
            cfg = ToolAgentsLlamaProviderConfig(
                base_url="https://openrouter.ai/api",
                model="qwen-1.5b",
            )
            cfg.create_settings(mock_provider)

        mock_settings.set_value.assert_any_call(
            "extra_body",
            {"provider": {"order": ["alibaba"], "allow_fallbacks": False}},
        )

    def test_ollama_disables_thinking_by_default(self) -> None:
        """Thinking models (e.g. ornith-9b) emit EMPTY content until reasoning
        finishes, breaking the agent loop. ``enable_thinking=False`` (the
        default) must send ``think:false`` via extra_body on the Ollama path.
        """
        from grc_agent.toolagents_runtime import GrcOpenAIChatAPI, ToolAgentsLlamaProviderConfig

        mock_provider = mock.MagicMock(spec=GrcOpenAIChatAPI)
        mock_settings = mock.MagicMock()
        mock_provider.get_default_settings.return_value = mock_settings

        cfg = ToolAgentsLlamaProviderConfig(base_url="http://127.0.0.1:11434", model="x")
        cfg.create_settings(mock_provider)
        mock_settings.set_value.assert_any_call("extra_body", {"think": False})

    def test_ollama_provider_settings_has_no_per_request_num_ctx(self) -> None:
        """Ollama's /v1 endpoint silently ignores per-request num_ctx — the
        provider must NOT pass it. Context sizing lives on the model
        (Modelfile PARAMETER num_ctx), not in the request payload.
        Regression guard against re-introducing the dead `extra_body.options.num_ctx`.
        """
        from grc_agent.toolagents_runtime import GrcOpenAIChatAPI, ToolAgentsLlamaProviderConfig

        mock_provider = mock.MagicMock(spec=GrcOpenAIChatAPI)
        mock_settings = mock.MagicMock()
        mock_provider.get_default_settings.return_value = mock_settings

        cfg = ToolAgentsLlamaProviderConfig(
            base_url="http://127.0.0.1:11434",
            model="gemma4:e4b-it-qat-120k",
        )
        cfg.create_settings(mock_provider)

        # The only way `options.num_ctx` could reappear is via set_value on
        # an extra_body containing {"options": {"num_ctx": ...}}. Assert
        # no such call was made.
        for call in mock_settings.set_value.call_args_list:
            args, _ = call
            if not args:
                continue
            value = args[-1]
            if isinstance(value, dict) and "extra_body" in args[0]:
                assert "options" not in value.get("extra_body", {}), (
                    f"extra_body must not contain 'options' (num_ctx is dead on /v1): {value}"
                )


class OpenRouterDelegatesToSDKTests(unittest.TestCase):
    """The OpenRouter path must use the OpenAI SDK, not a hand-rolled
    ``requests.post`` + Mock response shim.

    The previous custom ``get_response`` override built a broken
    ``MockChatCompletion`` that never exposed ``.choices[0].message``
    (AttributeError on every call) and used an undeclared ``requests``
    dependency whose exceptions bypassed graceful degradation. This test
    guards against its reintroduction.
    """

    def test_get_response_is_not_overridden(self) -> None:
        from grc_agent.toolagents_runtime import GrcOpenAIChatAPI
        from ToolAgents.provider.chat_api_provider.open_ai import OpenAIChatAPI

        # The subclass must delegate get_response to the parent SDK path.
        self.assertIs(
            GrcOpenAIChatAPI.get_response,
            OpenAIChatAPI.get_response,
        )

    def test_no_requests_dependency_or_mock_classes(self) -> None:
        import inspect

        from grc_agent.toolagents_runtime import GrcOpenAIChatAPI

        source = inspect.getsource(GrcOpenAIChatAPI)
        self.assertNotIn("requests.post", source)
        self.assertNotIn("MockChatCompletion", source)
        self.assertNotIn("MockToolCall", source)
        self.assertNotIn("MockFunction", source)
        self.assertNotIn("_is_openrouter", source)

    def test_openrouter_provider_uses_sdk_client(self) -> None:
        from grc_agent.toolagents_runtime import GrcOpenAIChatAPI
        from openai import OpenAI

        provider = GrcOpenAIChatAPI(
            api_key="sk-test",
            model="m",
            base_url="https://openrouter.ai/api/v1",
            timeout_seconds=30.0,
        )
        # The client is a real OpenAI SDK instance bound to the
        # OpenRouter base_url — the SDK forwards extra_body natively.
        self.assertIsInstance(provider.client, OpenAI)
        self.assertEqual(str(provider.client.base_url), "https://openrouter.ai/api/v1/")


class ToolAgentsRunnerBackendUnreachableTests(unittest.TestCase):
    """When the underlying HTTP client cannot reach the backend
    (Connection refused, DNS failure, etc.), the runner must surface
    a typed ``backend_unreachable`` payload instead of an unhandled
    ``openai.APIConnectionError``."""

    def _make_runner_with_failing_step(self) -> Any:
        from grc_agent.toolagents_runtime import (
            ToolAgentsLlamaProviderConfig,
            ToolAgentsRunner,
        )
        from ToolAgents.agents import ChatToolAgent

        cfg = ToolAgentsLlamaProviderConfig(
            base_url="http://127.0.0.1:11434",
            model="qwen3.5:9b-q4_K_M",
            timeout_seconds=1.0,
        )
        provider = cfg.create_provider()
        # Make the first .step() raise an APIConnectionError as if the
        # TCP socket was refused. We patch on the agent so the manual
        # loop catches it through the public boundary.
        chat_agent = ChatToolAgent(chat_api=provider)

        def _raise_connection_error(*args, **kwargs):
            import openai

            raise openai.APIConnectionError(request=mock.MagicMock())

        chat_agent.step = _raise_connection_error  # type: ignore[method-assign]
        return ToolAgentsRunner(cfg, chat_agent=chat_agent)

    def test_run_turn_returns_typed_backend_unreachable(self) -> None:

        agent = GrcAgent()
        runner = self._make_runner_with_failing_step()

        result = runner.run_turn(agent, "hi")

        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error_type"), "backend_unreachable")
        text = str(result.get("assistant_text", ""))
        self.assertIn("Connection refused", text)
        # Platform-agnostic — no systemd, no service-manager-specific terms.
        lowered = text.lower()
        self.assertNotIn("systemctl", lowered)
        self.assertNotIn("journalctl", lowered)
        # Backend URL must be in the hint for actionable guidance.
        self.assertIn("http://127.0.0.1:11434", text)
        # Model name is preserved for context.
        self.assertEqual(result.get("model"), "qwen3.5:9b-q4_K_M")
        # The structured server_url must also be exposed for programmatic use.
        self.assertEqual(
            result.get("details", {}).get("server_url"),
            "http://127.0.0.1:11434",
        )

    def test_stream_turn_emits_backend_unreachable_final(self) -> None:
        agent = GrcAgent()
        runner = self._make_runner_with_failing_step()

        events = list(runner.stream_turn(agent, "hi"))
        final_events = [e for e in events if e.get("event") == "final"]
        self.assertEqual(len(final_events), 1)
        result = final_events[0].get("result", {})
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error_type"), "backend_unreachable")


class ToolAgentsRunnerLoopDetectionTests(unittest.TestCase):
    """Repeated identical failing change_graph calls must be detected: a
    factual note is injected on the 2nd occurrence and the turn stops on the
    3rd (the cause of every safety-ceiling in the agent_flow review)."""

    def _change_graph_call(self, args: Any) -> ChatMessage:
        import datetime
        import uuid

        from ToolAgents.data_models.messages import ToolCallContent

        tc = ToolCallContent(
            tool_call_id=str(uuid.uuid4()),
            tool_call_name="change_graph",
            tool_call_arguments=args,
        )
        now = datetime.datetime.now()
        return ChatMessage(
            id=str(uuid.uuid4()),
            role=ChatMessageRole.Assistant,
            content=[tc],
            created_at=now,
            updated_at=now,
        )

    def _runner_with_scripted_step(self, agent: GrcAgent, calls: list[ChatMessage]) -> Any:
        from grc_agent.toolagents_runtime import (
            ToolAgentsLlamaProviderConfig,
            ToolAgentsRunner,
        )

        cfg = ToolAgentsLlamaProviderConfig(
            base_url="http://127.0.0.1:11434",
            model="m",
            timeout_seconds=1.0,
        )
        chat_agent = ChatToolAgent(chat_api=cfg.create_provider())
        call_iter = iter(calls)

        def _step(*_args: Any, **_kwargs: Any) -> Any:
            return next(call_iter, _assistant_text("done"))

        chat_agent.step = _step  # type: ignore[method-assign]
        return ToolAgentsRunner(cfg, chat_agent=chat_agent)

    def test_repeated_identical_failing_change_graph_stops_turn(self) -> None:
        from pathlib import Path

        from grc_agent.flowgraph_session import FlowgraphSession

        session = FlowgraphSession()
        session.load(str(Path(__file__).resolve().parent / "data" / "dial_tone.grc"))
        agent = GrcAgent(session=session)
        # remove_blocks on a non-existent block fails identically every time.
        args = {"remove_blocks": ["does_not_exist"]}
        calls = [self._change_graph_call(args) for _ in range(5)]
        runner = self._runner_with_scripted_step(agent, calls)

        events = list(runner.stream_turn(agent, "remove nonexistent"))
        finals = [e for e in events if e.get("event") == "final"]
        self.assertEqual(len(finals), 1)
        result = finals[0].get("result", {})
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error_type"), "safety_ceiling_reached")
        self.assertIn("identically", result.get("assistant_text", ""))
        # The factual note was injected (user-role model_message) on the 2nd.
        user_notes = [
            e
            for e in events
            if e.get("event") == "model_message" and e.get("role") == "user"
        ]
        self.assertTrue(user_notes, "loop note should be injected on 2nd failure")


class ToolAgentsRunnerEmptyResponseTests(unittest.TestCase):
    """When the model returns empty content and no tool calls on every retry
    attempt, the runner must synthesize a factual, non-empty terminal
    assistant_text and mark the final result with a typed error_type
    (scenario 01: 'empty final text')."""

    def _runner_with_scripted_step(self, agent: GrcAgent, calls: list[ChatMessage]) -> Any:
        from grc_agent.toolagents_runtime import (
            ToolAgentsLlamaProviderConfig,
            ToolAgentsRunner,
        )

        cfg = ToolAgentsLlamaProviderConfig(
            base_url="http://127.0.0.1:11434",
            model="m",
            timeout_seconds=1.0,
        )
        chat_agent = ChatToolAgent(chat_api=cfg.create_provider())
        call_iter = iter(calls)

        def _step(*_args: Any, **_kwargs: Any) -> Any:
            return next(call_iter, _assistant_text("done"))

        chat_agent.step = _step  # type: ignore[method-assign]
        return ToolAgentsRunner(cfg, chat_agent=chat_agent)

    def test_empty_terminal_synthesizes_factual_text_and_typed_error(self) -> None:
        agent = GrcAgent()
        # Every retry attempt (3) returns empty content and no tool calls.
        calls = [_assistant_text("") for _ in range(5)]
        runner = self._runner_with_scripted_step(agent, calls)

        events = list(runner.stream_turn(agent, "hi"))
        finals = [e for e in events if e.get("event") == "final"]
        self.assertEqual(len(finals), 1)
        result = finals[0].get("result", {})

        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error_type"), "empty_model_response")
        text = str(result.get("assistant_text", ""))
        self.assertTrue(text, "assistant_text must be non-empty")
        self.assertIn("No response was generated", text)

    def test_empty_terminal_chunk_carries_synthesized_text(self) -> None:
        agent = GrcAgent()
        calls = [_assistant_text("") for _ in range(5)]
        runner = self._runner_with_scripted_step(agent, calls)

        events = list(runner.stream_turn(agent, "hi"))
        chunks = [e for e in events if e.get("event") == "chunk"]
        self.assertTrue(chunks, "a chunk event should be emitted")
        self.assertIn("No response was generated", str(chunks[-1].get("text", "")))


if __name__ == "__main__":
    unittest.main()
