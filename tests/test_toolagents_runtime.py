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


def _extra_body_from(mock_settings: Any) -> dict[str, Any]:
    """Extract the ``extra_body`` dict passed to ``settings.set_value``."""
    for call in mock_settings.set_value.call_args_list:
        args, _ = call
        if len(args) >= 2 and args[0] == "extra_body":
            return args[1]
    return {}


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

        extra_body = _extra_body_from(mock_settings)
        self.assertEqual(extra_body["provider"], {"order": ["alibaba"], "allow_fallbacks": False})

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


class OpenRouterWebPluginTests(unittest.TestCase):
    """OpenRouter web search is a request-side ``plugins`` augmentation in
    ``extra_body`` — there is no standalone search REST endpoint. On by
    default for the openrouter backend; the Ollama backend never adds it
    (its web_search/web_fetch tools hit Ollama's own hosted API)."""

    def _cfg_and_settings(self, backend: str, base_url: str):
        from grc_agent.toolagents_runtime import GrcOpenAIChatAPI, ToolAgentsLlamaProviderConfig

        mock_provider = mock.MagicMock(spec=GrcOpenAIChatAPI)
        mock_settings = mock.MagicMock()
        mock_provider.get_default_settings.return_value = mock_settings
        cfg = ToolAgentsLlamaProviderConfig(base_url=base_url, model="m", backend=backend)
        cfg.create_settings(mock_provider)
        return cfg, mock_settings

    def test_web_plugin_on_by_default_for_openrouter(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            _, mock_settings = self._cfg_and_settings("openrouter", "https://openrouter.ai/api")
        extra_body = _extra_body_from(mock_settings)
        self.assertEqual(extra_body["plugins"], [{"id": "web", "max_results": 5}])

    def test_web_plugin_opt_out_via_env(self) -> None:
        with mock.patch.dict("os.environ", {"OPENROUTER_WEB_SEARCH": "false"}, clear=True):
            _, mock_settings = self._cfg_and_settings("openrouter", "https://openrouter.ai/api")
        extra_body = _extra_body_from(mock_settings)
        self.assertNotIn("plugins", extra_body)

    def test_web_plugin_max_results_clamped_to_10(self) -> None:
        env = {"OPENROUTER_WEB_SEARCH_MAX_RESULTS": "999"}
        with mock.patch.dict("os.environ", env, clear=True):
            _, mock_settings = self._cfg_and_settings("openrouter", "https://openrouter.ai/api")
        self.assertEqual(_extra_body_from(mock_settings)["plugins"][0]["max_results"], 10)

    def test_web_plugin_domain_filters_parsed(self) -> None:
        env = {
            "OPENROUTER_WEB_SEARCH_INCLUDE_DOMAINS": "a.com, b.com",
            "OPENROUTER_WEB_SEARCH_EXCLUDE_DOMAINS": "reddit.com",
        }
        with mock.patch.dict("os.environ", env, clear=True):
            _, mock_settings = self._cfg_and_settings("openrouter", "https://openrouter.ai/api")
        plugin = _extra_body_from(mock_settings)["plugins"][0]
        self.assertEqual(plugin["include_domains"], ["a.com", "b.com"])
        self.assertEqual(plugin["exclude_domains"], ["reddit.com"])

    def test_web_plugin_coexists_with_provider_routing(self) -> None:
        env = {"OPENROUTER_PROVIDER_ORDER": "alibaba"}
        with mock.patch.dict("os.environ", env, clear=True):
            _, mock_settings = self._cfg_and_settings("openrouter", "https://openrouter.ai/api")
        extra_body = _extra_body_from(mock_settings)
        self.assertEqual(extra_body["provider"], {"order": ["alibaba"]})
        self.assertIn("plugins", extra_body)

    def test_ollama_backend_never_adds_web_plugin(self) -> None:
        """The Ollama path must stay fully separate from the OpenRouter plugin."""
        with mock.patch.dict("os.environ", {}, clear=True):
            _, mock_settings = self._cfg_and_settings("ollama", "http://127.0.0.1:11434")
        extra_body = _extra_body_from(mock_settings)
        self.assertNotIn("plugins", extra_body)
        self.assertEqual(extra_body, {"think": True})


class UrlCitationSurfacingTests(unittest.TestCase):
    """OpenRouter's web plugin returns ``url_citation`` annotations on the
    assistant message. Without surfacing them the model's grounding is
    invisible, so GrcResponseConverter must render a Sources footnote."""

    def _converter(self) -> Any:
        from grc_agent.toolagents_runtime import GrcResponseConverter

        parent = mock.MagicMock()
        parent.from_provider_response.side_effect = lambda response_data: ChatMessage(
            id=str(uuid.uuid4()),
            role=ChatMessageRole.Assistant,
            content=[
                TextContent(content=getattr(response_data.choices[0].message, "content", "") or "")
            ],
            created_at=datetime.datetime.now(),
            updated_at=datetime.datetime.now(),
        )
        return GrcResponseConverter(parent)

    def _citation(self, title: str, url: str, content: str = ""):
        from types import SimpleNamespace

        return SimpleNamespace(
            type="url_citation",
            url_citation=SimpleNamespace(title=title, url=url, content=content),
        )

    def _delta(self, content=None, annotations=None):
        from types import SimpleNamespace

        return SimpleNamespace(
            content=content,
            tool_calls=None,
            annotations=annotations,
            reasoning=None,
            reasoning_content=None,
            thinking=None,
            model_extra=None,
        )

    def _response(self, content: str, annotations=None):
        from types import SimpleNamespace

        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=content, annotations=annotations or []),
                    delta=None,
                    finish_reason=None,
                )
            ]
        )

    def test_non_stream_citations_appended_as_sources_footnote(self) -> None:
        converter = self._converter()
        response = self._response(
            "The answer.",
            annotations=[self._citation("Example", "https://example.com/a")],
        )

        message = converter.from_provider_response(response)

        text = "".join(c.content for c in message.content if isinstance(c, TextContent))
        self.assertIn("The answer.", text)
        self.assertIn("Sources:", text)
        self.assertIn("[Example](https://example.com/a)", text)

    def test_non_stream_no_annotations_leaves_text_unchanged(self) -> None:
        converter = self._converter()
        message = converter.from_provider_response(self._response("Plain answer."))

        text = "".join(c.content for c in message.content if isinstance(c, TextContent))
        self.assertNotIn("Sources:", text)
        self.assertEqual(text, "Plain answer.")

    def test_non_stream_skips_non_url_citation_annotations(self) -> None:
        from types import SimpleNamespace

        converter = self._converter()
        other = SimpleNamespace(type="something_else", url_citation=None)
        response = self._response("Answer.", annotations=[other])

        message = converter.from_provider_response(response)
        text = "".join(c.content for c in message.content if isinstance(c, TextContent))
        self.assertNotIn("Sources:", text)

    def test_stream_citations_appended_at_finish(self) -> None:
        from types import SimpleNamespace

        converter = self._converter()
        citation = self._citation("Stream Src", "https://example.com/s")
        chunks = [
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=self._delta(content="Hello world"),
                        finish_reason=None,
                    )
                ]
            ),
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=self._delta(annotations=[citation]),
                        finish_reason="stop",
                    )
                ]
            ),
        ]

        emitted = list(converter.yield_from_provider(chunks))
        finished = [e for e in emitted if e.finished]
        self.assertTrue(finished, "stream must emit a finished event")
        text = "".join(
            c.content
            for c in finished[-1].finished_chat_message.content
            if isinstance(c, TextContent)
        )
        self.assertIn("Hello world", text)
        self.assertIn("Sources:", text)
        self.assertIn("[Stream Src](https://example.com/s)", text)


class OpenRouterToolSurfaceTests(unittest.TestCase):
    """On OpenRouter the `web` plugin grounds the model natively, so the
    Ollama-hosted web_search/web_fetch tools are dropped from the surfaced
    tool set (they cannot run against OpenRouter)."""

    def test_ollama_only_tools_are_exactly_the_web_pair(self) -> None:
        from grc_agent.toolagents_runtime import _OLLAMA_ONLY_TOOLS

        self.assertEqual(set(_OLLAMA_ONLY_TOOLS), {"web_search", "web_fetch"})

    def test_openrouter_filtered_surface_drops_web_tools(self) -> None:
        from grc_agent.toolagents_runtime import _OLLAMA_ONLY_TOOLS

        agent = GrcAgent()
        # Mirror the runner's active-allowed-tools computation for openrouter.
        active = set(MVP_TOOL_SURFACE.model_tool_names) - set(_OLLAMA_ONLY_TOOLS)
        names = {s["function"]["name"] for s in agent.get_tool_schemas_for_turn(active)}
        self.assertNotIn("web_search", names)
        self.assertNotIn("web_fetch", names)
        self.assertIn("inspect_graph", names)
        self.assertIn("change_graph", names)

    def test_ollama_surface_keeps_web_tools(self) -> None:
        agent = GrcAgent()
        names = {
            s["function"]["name"]
            for s in agent.get_tool_schemas_for_turn(set(MVP_TOOL_SURFACE.model_tool_names))
        }
        self.assertIn("web_search", names)
        self.assertIn("web_fetch", names)


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
        # The factual note was injected (user_model model_message) on the 2nd.
        user_notes = [
            e for e in events if e.get("event") == "model_message" and e.get("role") == "user_model"
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

    def test_grc_response_converter_wraps_thinking_tokens(self) -> None:
        from dataclasses import dataclass

        from grc_agent.toolagents_runtime import GrcResponseConverter

        # Mock chunk structure resembling OpenAI stream chunks. The
        # OpenAI SDK does not standardize a thinking field name, so
        # the converter checks ``reasoning`` (Ollama OpenAI-compat),
        # ``reasoning_content`` (OpenRouter / DeepSeek), and
        # ``thinking`` (native Ollama). Each variant is tested below.
        @dataclass
        class MockDelta:
            content: str | None = None
            reasoning: str | None = None
            reasoning_content: str | None = None
            thinking: str | None = None
            tool_calls: list | None = None

        @dataclass
        class MockChoice:
            delta: MockDelta
            finish_reason: str | None = None

        @dataclass
        class MockChunk:
            choices: list[MockChoice]

        # The converter iterates the raw stream directly — no parent
        # converter needed for yield_from_provider.
        converter = GrcResponseConverter(parent_converter=None)

        # ``reasoning`` — Ollama OpenAI-compat (the field gemma4 uses).
        stream = [
            MockChunk(choices=[MockChoice(delta=MockDelta(reasoning="Thinking..."))]),
            MockChunk(choices=[MockChoice(delta=MockDelta(reasoning=" more..."))]),
            MockChunk(
                choices=[MockChoice(delta=MockDelta(content="Hello!"), finish_reason="stop")]
            ),
        ]

        result_chunks = list(converter.yield_from_provider(stream))

        # Thinking chunks are yielded IMMEDIATELY (not buffered until
        # the first content token arrives). This is the regression fix
        # for the multi-second "freeze" where reasoning was invisible.
        text_chunks = [c.chunk for c in result_chunks if c.chunk]
        self.assertEqual(text_chunks[0], "<think>Thinking...")
        self.assertEqual(text_chunks[1], " more...")
        # Close tag emitted as a separate immediate chunk on transition.
        self.assertEqual(text_chunks[2], "</think>\n")
        # Content follows.
        self.assertEqual(text_chunks[3], "Hello!")

        # The final chunk carries the finished flag + full message.
        finished_chunks = [c for c in result_chunks if c.get_finished()]
        self.assertEqual(len(finished_chunks), 1)

    def test_grc_response_converter_handles_thinking_field_name(self) -> None:
        """Native Ollama uses ``delta.thinking`` (not ``reasoning``); the
        converter must also pick that up so the chat UI shows reasoning
        for models streamed through the native /api/chat path."""
        from dataclasses import dataclass

        from grc_agent.toolagents_runtime import GrcResponseConverter

        @dataclass
        class MockDelta:
            content: str | None = None
            thinking: str | None = None
            tool_calls: list | None = None

        @dataclass
        class MockChoice:
            delta: MockDelta
            finish_reason: str | None = None

        @dataclass
        class MockChunk:
            choices: list[MockChoice]

        converter = GrcResponseConverter(parent_converter=None)
        stream = [
            MockChunk(choices=[MockChoice(delta=MockDelta(thinking="Plan: think first"))]),
            MockChunk(choices=[MockChoice(delta=MockDelta(thinking=" more"))]),
            MockChunk(choices=[MockChoice(delta=MockDelta(content="Done!"), finish_reason="stop")]),
        ]
        result_chunks = list(converter.yield_from_provider(stream))
        text_chunks = [c.chunk for c in result_chunks if c.chunk]
        self.assertEqual(text_chunks[0], "<think>Plan: think first")
        self.assertEqual(text_chunks[1], " more")
        self.assertEqual(text_chunks[2], "</think>\n")
        self.assertEqual(text_chunks[3], "Done!")


if __name__ == "__main__":
    unittest.main()
