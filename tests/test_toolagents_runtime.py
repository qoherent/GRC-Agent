"""ToolAgents runtime contract tests."""

from __future__ import annotations

import datetime
from pathlib import Path
import unittest
from unittest import mock
import uuid

from ToolAgents import FunctionTool
from ToolAgents.agents import ChatToolAgent
from ToolAgents.data_models.messages import (
    ChatMessage,
    ChatMessageRole,
    TextContent,
    ToolCallContent,
)

from grc_agent.agent import GrcAgent
from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.llama_probe import LlamaHealthProbe, extract_model_context_limit
from grc_agent.runtime.tool_schemas import build_tool_schemas
from grc_agent.runtime.tool_surface import MVP_TOOL_SURFACE
from grc_agent.toolagents_runtime import (
    ToolAgentsJsonClient,
    ToolAgentsLlamaProviderConfig,
    ToolAgentsRegistryBuilder,
    ToolAgentsRunner,
    ToolAgentsToolDelegate,
    _function_tool_from_openai_tool,
)


class _FakeProvider:
    def __init__(self, responses: list[ChatMessage]) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, object]] = []
        self._settings_provider = ToolAgentsLlamaProviderConfig(
            base_url="http://127.0.0.1:1",
            model="test-model",
        ).create_provider()

    def get_default_settings(self):
        return self._settings_provider.get_default_settings()

    def get_response(self, messages, settings=None, tools=None):
        self.requests.append(
            {
                "messages": messages,
                "settings": settings,
                "tools": tools or [],
            }
        )
        if not self.responses:
            raise AssertionError("fake provider response queue exhausted")
        return self.responses.pop(0)

    def get_streaming_response(self, messages, settings=None, tools=None):
        raise NotImplementedError

    def get_provider_identifier(self) -> str:
        return "fake"


def _assistant_text(text: str) -> ChatMessage:
    now = datetime.datetime.now()
    return ChatMessage(
        id=str(uuid.uuid4()),
        role=ChatMessageRole.Assistant,
        content=[TextContent(content=text)],
        created_at=now,
        updated_at=now,
    )


def _assistant_tool(name: str, arguments: object, call_id: str = "call_1") -> ChatMessage:
    now = datetime.datetime.now()
    return ChatMessage(
        id=str(uuid.uuid4()),
        role=ChatMessageRole.Assistant,
        content=[
            ToolCallContent(
                tool_call_id=call_id,
                tool_call_name=name,
                tool_call_arguments=arguments,
            )
        ],
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

    def test_openai_schema_conversion_preserves_contract_fields(self) -> None:
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
            converted["function"]["parameters"]["required"],
            schema["function"]["parameters"]["required"],
        )
        self.assertEqual(
            converted["function"]["parameters"]["additionalProperties"],
            schema["function"]["parameters"]["additionalProperties"],
        )
        self.assertEqual(
            converted["function"]["parameters"]["properties"]["op"]["enum"],
            schema["function"]["parameters"]["properties"]["op"]["enum"],
        )


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
        delegate = ToolAgentsToolDelegate(agent, "apply_edit")

        with mock.patch.object(agent, "execute_tool") as execute_tool:
            result = delegate.invoke(
                {"transaction": {}},
                allowed_tool_names=set(MVP_TOOL_SURFACE.model_tool_names),
            )

        self.assertFalse(result.executed)
        self.assertEqual(result.result["error_type"], "tool_not_allowed_for_surface")
        execute_tool.assert_not_called()


class ToolAgentsRunnerTests(unittest.TestCase):
    def _loaded_agent(self) -> GrcAgent:
        session = FlowgraphSession()
        fixture = Path(__file__).resolve().parent / "data" / "random_bit_generator.grc"
        session.load(fixture)
        return GrcAgent(session)

    def _dial_tone_agent(self) -> GrcAgent:
        session = FlowgraphSession()
        fixture = (
            Path(__file__).resolve().parents[1]
            / "playground"
            / "grc_agent_interactive"
            / "dial_tone_interactive.grc"
        )
        session.load(fixture)
        return GrcAgent(session)

    def _runner(self, responses: list[ChatMessage]) -> ToolAgentsRunner:
        provider_config = ToolAgentsLlamaProviderConfig(
            base_url="http://127.0.0.1:1",
            model="test-model",
            max_tokens=128,
        )
        fake_provider = _FakeProvider(responses)
        return ToolAgentsRunner(
            provider_config,
            chat_agent=ChatToolAgent(chat_api=fake_provider),
        )

    def _runner_and_provider(
        self, responses: list[ChatMessage]
    ) -> tuple[ToolAgentsRunner, _FakeProvider]:
        provider_config = ToolAgentsLlamaProviderConfig(
            base_url="http://127.0.0.1:1",
            model="test-model",
            max_tokens=128,
        )
        fake_provider = _FakeProvider(responses)
        return (
            ToolAgentsRunner(
                provider_config,
                chat_agent=ChatToolAgent(chat_api=fake_provider),
            ),
            fake_provider,
        )

    def test_scripted_tool_call_then_final_text_preserves_trace(self) -> None:
        agent = GrcAgent()
        runner = self._runner(
            [
                _assistant_tool(
                    "inspect_graph",
                    {"view": "overview", "targets": [], "params": ["all"]},
                ),
                _assistant_text("Graph inspected."),
            ]
        )

        result = runner.run_turn(
            agent,
            "Summarize this graph.",
            model="test-model",
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["assistant_text"], "Graph inspected.")
        requested = [
            turn for turn in agent.history
            if turn.get("role") == "assistant" and turn.get("tool_calls")
        ]
        executed = [turn for turn in agent.history if turn.get("role") == "tool"]
        self.assertEqual(len(requested), 1)
        self.assertEqual(len(executed), 1)
        self.assertEqual(executed[0]["name"], "inspect_graph")

    def test_invalid_tool_args_return_structured_repair_payload_no_execution(self) -> None:
        agent = GrcAgent()
        runner = self._runner(
            [
                _assistant_tool("inspect_graph", {"bogus": True}),
                _assistant_text("I need valid inspect arguments."),
            ]
        )

        result = runner.run_turn(
            agent,
            "Summarize this graph.",
            model="test-model",
        )

        self.assertTrue(result["ok"])
        tool_turns = [turn for turn in agent.history if turn.get("role") == "tool"]
        self.assertEqual(len(tool_turns), 1)
        payload = tool_turns[0]["content"]
        self.assertFalse(payload["ok"])
        self.assertIn("error_type", payload)
        self.assertEqual(result["tool_calls_executed"], 0)

    def test_attempted_internal_tool_is_rejected(self) -> None:
        agent = GrcAgent()
        runner = self._runner([_assistant_tool("apply_edit", {"transaction": {}})])

        result = runner.run_turn(
            agent,
            "Mutate with an internal tool.",
            model="test-model",
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_type"], "tool_not_allowed_for_surface")
        self.assertEqual(result["tool_calls_executed"], 0)

    def test_tool_round_ceiling_fails_closed_before_execution(self) -> None:
        agent = GrcAgent()
        runner = self._runner(
            [
                _assistant_tool(
                    "inspect_graph",
                    {"view": "overview", "targets": [], "params": ["all"]},
                )
            ]
        )

        result = runner.run_turn(
            agent,
            "Summarize this graph.",
            model="test-model",
            max_tool_rounds=0,
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_type"], "safety_ceiling_reached")
        self.assertEqual(result["tool_calls_executed"], 0)

    def test_model_turn_sends_only_four_wrapper_schemas(self) -> None:
        agent = GrcAgent()
        runner, fake_provider = self._runner_and_provider(
            [
                _assistant_tool("inspect_graph", {"view": "overview"}),
                _assistant_text("I need a loaded graph to inspect."),
            ]
        )

        runner.run_turn(
            agent,
            "Can you summarize this flowgraph in plain English?",
            model="test-model",
        )

        tools = fake_provider.requests[0]["tools"]
        names = [
            tool.to_openai_tool()["function"]["name"]
            for tool in tools
            if isinstance(tool, FunctionTool)
        ]
        self.assertEqual(names, list(MVP_TOOL_SURFACE.model_tool_names))

    def test_retry_when_assistant_says_it_needs_inspection_without_tool(self) -> None:
        agent = GrcAgent()
        runner, fake_provider = self._runner_and_provider(
            [
                _assistant_text("I would need to inspect the graph first."),
                _assistant_tool("inspect_graph", {"view": "overview"}),
                _assistant_text("Inspected after the reminder."),
            ]
        )

        result = runner.run_turn(
            agent,
            "What blocks are in this graph?",
            model="test-model",
            max_tool_rounds=2,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["assistant_text"], "Inspected after the reminder.")
        self.assertEqual(result["correction_retries_used"], 1)
        self.assertEqual(result["tool_calls_executed"], 1)
        self.assertEqual(len(fake_provider.requests), 3)
        second_request = fake_provider.requests[1]["messages"]
        self.assertTrue(
            any("Runtime reminder" in str(message) for message in second_request)
        )

    def test_graph_local_answer_without_tools_gets_one_retry(self) -> None:
        agent = GrcAgent()
        runner = self._runner(
            [
                _assistant_text("The graph has two sources."),
                _assistant_tool("inspect_graph", {"view": "overview"}),
                _assistant_text("Now grounded in inspect output."),
            ]
        )

        result = runner.run_turn(
            agent,
            "Tell me the graph blocks.",
            model="test-model",
            max_tool_rounds=2,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["assistant_text"], "Now grounded in inspect output.")
        self.assertEqual(result["correction_retries_used"], 1)
        self.assertEqual(result["tool_calls_executed"], 1)

    def test_edit_request_cannot_end_without_change_graph(self) -> None:
        agent = self._loaded_agent()
        runner = self._runner(
            [
                _assistant_tool("search_blocks", {"query": "signal source", "k": 3}),
                _assistant_text(
                    "I found analog_sig_source_x, but you need to add it in GRC."
                ),
                _assistant_tool(
                    "change_graph",
                    {
                        "dry_run": False,
                        "op": "clarify",
                        "user_goal": "add a 1000 Hz signal source and connect it",
                    },
                ),
                _assistant_text("I need exact placement before changing the graph."),
            ]
        )

        result = runner.run_turn(
            agent,
            "I want to add another signal source with frequency 1000 and connect it.",
            model="test-model",
            max_tool_rounds=3,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(
            result["assistant_text"],
            "I need exact placement before changing the graph.",
        )
        self.assertEqual(result["correction_retries_used"], 1)
        requested_names = []
        for turn in agent.history:
            if turn.get("role") != "assistant":
                continue
            for tool_call in turn.get("tool_calls", []):
                function_payload = tool_call.get("function", {})
                requested_names.append(function_payload.get("name"))
        self.assertIn("search_blocks", requested_names)
        self.assertIn("change_graph", requested_names)

    def test_invalid_change_graph_args_get_one_repair_retry(self) -> None:
        agent = self._loaded_agent()
        runner = self._runner(
            [
                _assistant_tool("search_blocks", {"query": "signal source", "k": 3}),
                _assistant_text("You need to add the block manually in GRC."),
                _assistant_tool(
                    "change_graph",
                    {
                        "dry_run": False,
                        "user_goal": "add a 1000 Hz signal source and connect it",
                    },
                ),
                _assistant_text("The change_graph call failed because op is missing."),
                _assistant_tool(
                    "change_graph",
                    {
                        "dry_run": False,
                        "op": "clarify",
                        "user_goal": "add a 1000 Hz signal source and connect it",
                    },
                ),
                _assistant_text("I need exact placement before changing the graph."),
            ]
        )

        result = runner.run_turn(
            agent,
            "I want to add another signal source with frequency 1000 and connect it.",
            model="test-model",
            max_tool_rounds=4,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["correction_retries_used"], 2)
        self.assertEqual(
            result["assistant_text"],
            "I need exact placement before changing the graph.",
        )

    def test_preview_call_for_commit_request_gets_commit_retry(self) -> None:
        agent = self._loaded_agent()
        runner = self._runner(
            [
                _assistant_tool(
                    "change_graph",
                    {
                        "dry_run": True,
                        "op": "set_param",
                        "user_goal": "set samp_rate to 48000",
                        "args": {
                            "instance_name": "samp_rate",
                            "param_key": "value",
                            "param_value": "48000",
                        },
                    },
                ),
                _assistant_text("Previewed the samp_rate edit."),
                _assistant_tool(
                    "change_graph",
                    {
                        "dry_run": False,
                        "op": "set_param",
                        "user_goal": "set samp_rate to 48000",
                        "args": {
                            "instance_name": "samp_rate",
                            "param_key": "value",
                            "param_value": "48000",
                        },
                    },
                    call_id="call_2",
                ),
                _assistant_text("Committed the samp_rate edit."),
            ]
        )

        result = runner.run_turn(
            agent,
            "Set samp_rate to 48000.",
            model="test-model",
            max_tool_rounds=2,
        )

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["assistant_text"], "Committed the samp_rate edit.")
        self.assertEqual(result["correction_retries_used"], 1)
        self.assertEqual(result["tool_calls_executed"], 2)
        tool_results = [turn["content"] for turn in agent.history if turn.get("role") == "tool"]
        self.assertGreaterEqual(len(tool_results), 2)
        self.assertFalse(tool_results[-2].get("committed"), tool_results[-2])
        self.assertTrue(tool_results[-1].get("committed"), tool_results[-1])

    def test_structural_preview_token_does_not_force_same_turn_commit_retry(self) -> None:
        agent = self._dial_tone_agent()
        runner, fake_provider = self._runner_and_provider(
            [
                _assistant_tool(
                    "change_graph",
                    {
                        "dry_run": True,
                        "op": "add_signal_source_to_sum",
                        "user_goal": "add a 1000 Hz signal source and connect it",
                        "args": {"block_id": "analog_sig_source_x", "freq": 1000},
                    },
                ),
                _assistant_text("Preview ready; please confirm before commit."),
            ]
        )

        result = runner.run_turn(
            agent,
            "Add another signal source with frequency 1000 and connect it.",
            model="test-model",
            max_tool_rounds=3,
        )

        self.assertTrue(result["ok"], result)
        self.assertIn("Preview ready", result["assistant_text"])
        self.assertIn("No graph changes were committed", result["assistant_text"])
        self.assertEqual(result["correction_retries_used"], 0)
        self.assertEqual(result["tool_calls_executed"], 1)
        self.assertEqual(len(fake_provider.requests), 1)
        tool_results = [turn["content"] for turn in agent.history if turn.get("role") == "tool"]
        self.assertIsInstance(tool_results[0].get("preview_token"), str)
        self.assertFalse(tool_results[0].get("committed"), tool_results[0])

    def test_generic_add_block_signal_source_call_is_not_normalized_before_validation(self) -> None:
        agent = self._loaded_agent()
        agent._turn_user_message = (
            "I want to add another signal source with frequency 1000 and connect it."
        )
        normalized = agent.normalize_tool_call_arguments(
            "change_graph",
            {
                "dry_run": True,
                "user_goal": "Add a new signal source block named analog_sig_source_x_2",
                "args": {
                    "op": "add_block",
                    "block_id": "analog_sig_source_x",
                    "instance_name": "analog_sig_source_x_2",
                },
            },
            model_tool_call=True,
        )

        self.assertEqual(normalized.get("op"), "add_block")
        self.assertEqual(normalized.get("args", {}).get("block_id"), "analog_sig_source_x")
        self.assertIsNone(normalized.get("args", {}).get("freq"))

    def test_generic_add_block_repair_does_not_parse_input_index_as_frequency(self) -> None:
        agent = self._loaded_agent()
        agent._turn_user_message = (
            "add another signal source to input 2 with frequency 1000 and connect it"
        )
        normalized = agent.normalize_tool_call_arguments(
            "change_graph",
            {
                "dry_run": False,
                "op": "add_block",
                "user_goal": "Add a new signal source block and connect it",
                "args": {"block_id": "analog_sig_source_x"},
            },
            model_tool_call=True,
        )

        self.assertEqual(normalized.get("op"), "add_block")
        self.assertIsNone(normalized.get("args", {}).get("freq"))

    def test_generic_add_block_schema_error_preserves_raw_requested_trace(self) -> None:
        agent = self._loaded_agent()
        runner = self._runner(
            [
                _assistant_tool(
                    "change_graph",
                    {
                        "dry_run": True,
                        "user_goal": "Add a new signal source block named analog_sig_source_x_2",
                        "args": {
                            "op": "add_block",
                            "block_id": "analog_sig_source_x",
                            "instance_name": "analog_sig_source_x_2",
                        },
                    },
                ),
                _assistant_text("The generic add_block call was invalid."),
                _assistant_tool(
                    "change_graph",
                    {
                        "dry_run": True,
                        "op": "clarify",
                        "user_goal": "add a signal source and connect it",
                    },
                    call_id="call_2",
                ),
                _assistant_text("I need a valid change_graph operation."),
            ]
        )

        result = runner.run_turn(
            agent,
            "I want to add another signal source with frequency 1000 and connect it.",
            model="test-model",
            max_tool_rounds=2,
        )

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["assistant_text"], "I need a valid change_graph operation.")
        assistant_calls = [
            tool_call
            for turn in agent.history
            if turn.get("role") == "assistant"
            for tool_call in turn.get("tool_calls", [])
        ]
        self.assertTrue(assistant_calls, agent.history)
        raw_args = assistant_calls[0].get("function", {}).get("arguments", "")
        self.assertIn('"op": "add_block"', raw_args)
        tool_results = [turn["content"] for turn in agent.history if turn.get("role") == "tool"]
        self.assertTrue(tool_results, agent.history)
        self.assertEqual(tool_results[0].get("error_type"), "tool_call_invalid")
        self.assertEqual(result["tool_calls_executed"], 1)

    def test_wrong_insert_in_connection_gets_composite_retry_reminder(self) -> None:
        agent = self._loaded_agent()
        runner, fake_provider = self._runner_and_provider(
            [
                _assistant_tool(
                    "change_graph",
                    {
                        "dry_run": True,
                        "op": "insert_in_connection",
                        "user_goal": "add a 1000 Hz signal source and connect it",
                        "args": {
                            "block_id": "analog_sig_source_x",
                            "instance_name": "analog_sig_source_x_2",
                            "insert_params": {"freq": 1000},
                        },
                    },
                ),
                _assistant_text("I need a connection_id before I can add it."),
                _assistant_tool(
                    "change_graph",
                    {
                        "dry_run": False,
                        "op": "add_signal_source_to_sum",
                        "user_goal": "add a 1000 Hz signal source and connect it",
                        "state_revision": agent.session.state_revision,
                        "preview_token": "pt_invalid_for_this_plan",
                        "args": {"block_id": "analog_sig_source_x", "freq": 1000},
                    },
                    call_id="call_2",
                ),
                _assistant_text("I retried with add_signal_source_to_sum."),
            ]
        )

        result = runner.run_turn(
            agent,
            "I want to add another signal source with frequency 1000 and connect it.",
            model="test-model",
            max_tool_rounds=2,
        )

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["correction_retries_used"], 1)
        self.assertEqual(result["assistant_text"], "I retried with add_signal_source_to_sum.")
        self.assertEqual(result["tool_calls_executed"], 2)
        self.assertEqual(len(fake_provider.requests), 4)
        final_request = fake_provider.requests[2]["messages"]
        self.assertTrue(
            any("op=add_signal_source_to_sum" in str(message) for message in final_request),
            final_request,
        )
        requested_ops = []
        for turn in agent.history:
            if turn.get("role") != "assistant":
                continue
            for tool_call in turn.get("tool_calls", []):
                function_payload = tool_call.get("function", {})
                arguments = function_payload.get("arguments")
                if isinstance(arguments, str):
                    requested_ops.append(arguments)
        self.assertTrue(any('"op": "add_signal_source_to_sum"' in item for item in requested_ops))

    def test_non_graph_chitchat_does_not_force_tool_retry(self) -> None:
        agent = GrcAgent()
        runner, fake_provider = self._runner_and_provider(
            [_assistant_text("Hello.")]
        )

        result = runner.run_turn(agent, "hello", model="test-model")

        self.assertTrue(result["ok"])
        self.assertEqual(result["assistant_text"], "Hello.")
        self.assertEqual(result["correction_retries_used"], 0)
        self.assertEqual(len(fake_provider.requests), 1)


class ToolAgentsProviderConfigTests(unittest.TestCase):
    def test_provider_settings_include_required_llama_fields(self) -> None:
        config = ToolAgentsLlamaProviderConfig(
            base_url="http://127.0.0.1:8080",
            model="alias",
            max_tokens=321,
            enable_thinking=True,
        )
        provider = config.create_provider()
        settings = config.create_settings(provider)
        request_settings = settings.to_dict()["REQUEST_SETTINGS"]

        self.assertEqual(request_settings["temperature"], 0.0)
        self.assertEqual(request_settings["max_tokens"], 321)
        self.assertEqual(request_settings["tool_choice"], "auto")
        self.assertIs(request_settings["parallel_tool_calls"], False)
        self.assertEqual(
            request_settings["extra_body"]["parse_tool_calls"],
            True,
        )
        self.assertEqual(
            request_settings["extra_body"]["chat_template_kwargs"]["enable_thinking"],
            True,
        )


class ToolAgentsJsonClientTests(unittest.TestCase):
    def test_json_client_returns_openai_style_response_without_custom_http_client(self) -> None:
        config = ToolAgentsLlamaProviderConfig(
            base_url="http://127.0.0.1:1",
            model="test-model",
            max_tokens=128,
        )
        client = ToolAgentsJsonClient(config)
        fake_provider = _FakeProvider([_assistant_text('{"answer":"ok"}')])
        client.agent = ChatToolAgent(chat_api=fake_provider)

        response = client.create_chat_completion(
            model="test-model",
            messages=[{"role": "user", "content": "Return JSON."}],
            response_format={"type": "json_object"},
        )

        message = response["choices"][0]["message"]
        self.assertEqual(message["role"], "assistant")
        self.assertEqual(message["content"], '{"answer":"ok"}')
        self.assertEqual(response["model"], "test-model")


class LlamaProbeTests(unittest.TestCase):
    def test_extract_model_context_limit_from_props(self) -> None:
        self.assertEqual(
            extract_model_context_limit(
                {"default_generation_settings": {"params": {"n_ctx": 120000}}}
            ),
            120000,
        )

    def test_unreachable_probe_fails_not_ready(self) -> None:
        probe = LlamaHealthProbe("http://127.0.0.1:1", timeout_seconds=0.05)
        with self.assertRaises(Exception):
            probe.require_ready()


if __name__ == "__main__":
    unittest.main()
