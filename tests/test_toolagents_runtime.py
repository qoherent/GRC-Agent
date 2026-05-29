"""ToolAgents runtime contract tests for the flat MVP wrapper surface."""

from __future__ import annotations

import datetime
import uuid
import unittest
from unittest import mock

from ToolAgents import FunctionTool
from ToolAgents.agents import ChatToolAgent
from ToolAgents.data_models.messages import ChatMessage, ChatMessageRole, TextContent

from grc_agent.agent import GrcAgent
from grc_agent.runtime.tool_schemas import build_tool_schemas
from grc_agent.runtime.tool_surface import MVP_TOOL_SURFACE
from grc_agent.toolagents_runtime import (
    ToolAgentsRegistryBuilder,
    ToolAgentsToolDelegate,
    _function_tool_from_openai_tool,
    _is_missing_graph_evidence_response,
    _is_terminal_change_graph_failure,
    _tool_failure_text,
    _tool_retry_reminder,
)


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
            "add_variables",
            "update_variables",
            "remove_variables",
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

        self.assertIn("current tool evidence", reminder or "")

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


if __name__ == "__main__":
    unittest.main()
