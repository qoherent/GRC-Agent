"""ToolAgents runtime contract tests for the flat MVP wrapper surface."""

from __future__ import annotations

import datetime
import unittest
import uuid
from typing import Any
from unittest import mock

from grc_agent.agent import GrcAgent
from grc_agent.runtime.tool_schemas import build_tool_schemas
from grc_agent.runtime.model_context import MVP_TOOL_SURFACE
from grc_agent.toolagents_runtime import (
    ToolAgentsHistoryAdapter,
    ToolAgentsRegistryBuilder,
    ToolAgentsToolDelegate,
    _function_tool_from_openai_tool,
    _is_missing_graph_evidence_response,
    _is_terminal_change_graph_failure,
    _tool_failure_text,
    _tool_retry_reminder,
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
        registry = ToolAgentsRegistryBuilder(agent).build(
            set(MVP_TOOL_SURFACE.model_tool_names)
        )

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
        delegate = ToolAgentsToolDelegate(agent, "apply_edit")

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


class ToolAgentsHistoryAdapterToolMessageTests(unittest.TestCase):
    """The JSON-only helper path (``ToolAgentsJsonClient``) still goes
    through ``ToolAgentsHistoryAdapter.from_openai_messages``. The runtime
    model path uses typed ``ChatMessage`` objects and ``ChatHistory`` now;
    the helper path is the only consumer of the dict adapter."""

    def test_user_message_round_trip(self) -> None:
        payload = {"role": "user", "content": "What is the sample rate?"}
        msgs = ToolAgentsHistoryAdapter.from_openai_messages([payload])
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0].role, ChatMessageRole.User)
        text = next(c for c in msgs[0].content if isinstance(c, TextContent))
        self.assertEqual(text.content, "What is the sample rate?")

    def test_assistant_message_round_trip(self) -> None:
        payload = {"role": "assistant", "content": "The sample rate is 32000."}
        msgs = ToolAgentsHistoryAdapter.from_openai_messages([payload])
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0].role, ChatMessageRole.Assistant)
        text = next(c for c in msgs[0].content if isinstance(c, TextContent))
        self.assertEqual(text.content, "The sample rate is 32000.")

    def test_empty_input_returns_empty_list(self) -> None:
        self.assertEqual(ToolAgentsHistoryAdapter.from_openai_messages([]), [])

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


class ToolAgentsRepairClassificationTests(unittest.TestCase):
    def test_ambiguity_clarification_is_not_forced_into_mutation(self) -> None:
        reminder = _tool_retry_reminder(
            user_message="Disable the QT GUI time sink and validate the graph.",
            assistant_text=(
                "I found two instances: `qtgui_time_sink_x_0` and "
                "`qtgui_time_sink_x_1`. Please specify which one to disable."
            ),
            tool_calls_requested=1,
            tool_calls_executed=1,
            tool_names_requested=["inspect_graph"],
            change_graph_schema_failure_pending=False,
            change_graph_committed=False,
            change_graph_control_response=False,
            change_graph_wrong_insert_pending=False,
            change_graph_missing_evidence_pending=False,
            graph_ambiguity_pending=True,
        )

        self.assertIsNone(reminder)

    def test_evidence_backed_clarification_is_not_forced_into_mutation(self) -> None:
        reminder = _tool_retry_reminder(
            user_message="Reconnect the time sink to the other random source.",
            assistant_text=(
                "Which random source should connect to which time sink input, "
                "and should the existing connection be removed?"
            ),
            tool_calls_requested=1,
            tool_calls_executed=1,
            tool_names_requested=["inspect_graph"],
            change_graph_schema_failure_pending=False,
            change_graph_committed=False,
            change_graph_control_response=False,
            change_graph_wrong_insert_pending=False,
            change_graph_missing_evidence_pending=False,
            graph_ambiguity_pending=False,
        )

        self.assertIsNone(reminder)

    def test_pre_evidence_clarification_gets_inspection_reminder(self) -> None:
        reminder = _tool_retry_reminder(
            user_message="Reconnect the message strobe to another debug block.",
            assistant_text=(
                "Please provide the instance names and ports for the message "
                "strobe and debug block."
            ),
            tool_calls_requested=0,
            tool_calls_executed=0,
            tool_names_requested=[],
            change_graph_schema_failure_pending=False,
            change_graph_committed=False,
            change_graph_control_response=False,
            change_graph_wrong_insert_pending=False,
            change_graph_missing_evidence_pending=False,
            graph_ambiguity_pending=False,
        )

        self.assertIn("current tool evidence", (reminder or "").lower())

    def test_ambiguous_stream_rewire_clarification_not_forced_into_mutation(self) -> None:
        reminder = _tool_retry_reminder(
            user_message="Reconnect time sink to other random source",
            assistant_text="Which of the two random sources do you want to connect to, and on which port?",
            tool_calls_requested=1,
            tool_calls_executed=1,
            tool_names_requested=["inspect_graph"],
            change_graph_schema_failure_pending=False,
            change_graph_committed=False,
            change_graph_control_response=False,
            change_graph_wrong_insert_pending=False,
            change_graph_missing_evidence_pending=False,
            graph_ambiguity_pending=True,
        )
        self.assertIsNone(reminder)

    def test_ambiguous_message_rewire_clarification_not_forced_into_mutation(self) -> None:
        reminder = _tool_retry_reminder(
            user_message="Reconnect message strobe to another debug block",
            assistant_text="There are multiple strobe and debug blocks in this graph. Which one should I rewire?",
            tool_calls_requested=1,
            tool_calls_executed=1,
            tool_names_requested=["inspect_graph"],
            change_graph_schema_failure_pending=False,
            change_graph_committed=False,
            change_graph_control_response=False,
            change_graph_wrong_insert_pending=False,
            change_graph_missing_evidence_pending=False,
            graph_ambiguity_pending=True,
        )
        self.assertIsNone(reminder)

    def test_forced_invalid_commit_is_not_terminal_failure(self) -> None:
        result = {
            "ok": True,
            "committed": True,
            "forced_validation_failure": True,
            "validation_result": {"status": "invalid"},
        }

        self.assertFalse(_is_terminal_change_graph_failure(result))

    def test_terminal_change_graph_failure_text_preserves_native_refusal_facts(self) -> None:
        text = _tool_failure_text(
            {
                "tool": "change_graph",
                "committed": False,
                "rejected_phase": "native_grc_validation",
                "graph_unchanged": True,
                "native_validation_errors": [
                    "Source - out(0): Port is not connected.",
                ],
            }
        )

        self.assertIn("did not commit", text)
        self.assertIn("The graph is unchanged.", text)
        self.assertIn("Source - out(0): Port is not connected.", text)
        self.assertIn("Please choose", text)

    def test_terminal_change_graph_failure_text_handles_minimal_validation_result(
        self,
    ) -> None:
        text = _tool_failure_text(
            {
                "committed": False,
                "error_type": "gnu_validation_failed",
                "message": "Candidate graph rejected by native GRC validation.",
                "validation_result": {
                    "native": {
                        "errors": ["Source - out(0): Port is not connected."]
                    }
                },
            }
        )

        self.assertIn("did not commit", text)
        self.assertIn("No changes were committed.", text)
        self.assertIn("Source - out(0): Port is not connected.", text)
        self.assertIn("force an invalid intermediate graph", text)

    def test_unknown_param_preflight_is_repairable_evidence_failure(self) -> None:
        result = {
            "ok": False,
            "error_type": "preflight_rejected",
            "message": "Transaction failed preflight validation.",
            "errors": [
                {
                    "code": "parameter_not_found",
                    "message": "Unknown parameter for block type analog_sig_source_x: frequency",
                    "hint": "Inspect the target block details and retry with an exact param_id.",
                }
            ],
        }

        self.assertTrue(_is_missing_graph_evidence_response(result))
        self.assertFalse(_is_terminal_change_graph_failure(result))

    def test_unknown_block_preflight_is_repairable_evidence_failure(self) -> None:
        result = {
            "ok": False,
            "error_type": "preflight_rejected",
            "message": "Transaction failed preflight validation.",
            "errors": [
                {
                    "code": "unknown_block_id",
                    "message": "Could not resolve block type: null_sink",
                    "hint": "Run search_blocks for the block concept and retry.",
                }
            ],
        }

        self.assertTrue(_is_missing_graph_evidence_response(result))
        self.assertFalse(_is_terminal_change_graph_failure(result))

    def test_missing_added_connection_endpoint_is_repairable(self) -> None:
        result = {
            "ok": False,
            "error_type": "preflight_rejected",
            "message": "Transaction failed preflight validation.",
            "errors": [
                {
                    "code": "block_not_found",
                    "message": "Block not found: blocks_null_sink",
                    "hint": (
                        "The endpoint must be an existing graph instance_name. "
                        "For a new sink/source, include add_blocks and add_connections in the same "
                        "change_graph call, and connect to the new instance_name, not the catalog block_id."
                    ),
                }
            ],
        }

        self.assertTrue(_is_missing_graph_evidence_response(result))
        self.assertFalse(_is_terminal_change_graph_failure(result))


class ToolAgentsProviderConfigTests(unittest.TestCase):
    def test_create_settings_omits_llama_extra_body(self) -> None:
        from grc_agent.toolagents_runtime import GrcOpenAIChatAPI, ToolAgentsLlamaProviderConfig
        mock_provider = mock.MagicMock(spec=GrcOpenAIChatAPI)
        mock_settings = mock.MagicMock()
        mock_provider.get_default_settings.return_value = mock_settings

        cfg = ToolAgentsLlamaProviderConfig(
            base_url="http://127.0.0.1:11434",
            model="qwen-1.5b",
        )
        cfg.create_settings(mock_provider)
        set_value_calls = [call[0] for call in mock_settings.set_value.call_args_list]
        self.assertNotIn("extra_body", [c[0] for c in set_value_calls])

    def test_openrouter_settings_includes_provider_extra_body(self) -> None:
        from grc_agent.toolagents_runtime import GrcOpenAIChatAPI, ToolAgentsLlamaProviderConfig
        mock_provider = mock.MagicMock(spec=GrcOpenAIChatAPI)
        mock_settings = mock.MagicMock()
        mock_provider.get_default_settings.return_value = mock_settings

        with mock.patch.dict("os.environ", {
            "OPENROUTER_PROVIDER_ORDER": "alibaba",
            "OPENROUTER_ALLOW_FALLBACKS": "false"
        }):
            cfg = ToolAgentsLlamaProviderConfig(
                base_url="https://openrouter.ai/api",
                model="qwen-1.5b",
            )
            cfg.create_settings(mock_provider)

        mock_settings.set_value.assert_any_call("extra_body", {
            "provider": {
                "order": ["alibaba"],
                "allow_fallbacks": False
            }
        })


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
            raise openai.APIConnectionError(
                request=mock.MagicMock()
            )

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


if __name__ == "__main__":
    unittest.main()
