"""Integration tests verifying the four agent-loop fixes.

Tests cover:
  Fix #1: No premature exit on commit (commit result flows as role:"tool")
  Fix #2: Transient reminder (not in agent history), recency bias (at end),
          template safety (Custom role tag, not role:"system" mid-stream)
  Fix #3: No forced_next_tool_name / forced tool_choice remnants
  Fix #4: update_states flat enum accepted; old object schema rejected
"""

from __future__ import annotations

import datetime
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any

from grc_agent.agent import GrcAgent
from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.runtime.change_graph import (
    _update_state_operation,
)
from grc_agent.runtime.model_context import build_system_prompt, render_model_messages
from grc_agent.toolagents_runtime import (
    _tool_retry_reminder,
)
from ToolAgents.data_models.chat_history import ChatHistory
from ToolAgents.data_models.messages import (
    ChatMessage,
    ChatMessageRole,
    TextContent,
    ToolCallContent,
    ToolCallResultContent,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> datetime.datetime:
    return datetime.datetime.now()


def _user_message(text: str) -> ChatMessage:
    return ChatMessage(
        id=f"u-{text[:8]}",
        role=ChatMessageRole.User,
        content=[TextContent(content=text)],
        created_at=_now(),
        updated_at=_now(),
    )


def _assistant_message(text: str) -> ChatMessage:
    return ChatMessage(
        id=f"a-{text[:8]}",
        role=ChatMessageRole.Assistant,
        content=[TextContent(content=text)],
        created_at=_now(),
        updated_at=_now(),
    )


def _assistant_with_tool_calls(
    calls: list[tuple[str, str, dict]]
) -> ChatMessage:
    content: list = []
    for call_id, name, args in calls:
        content.append(
            ToolCallContent(
                tool_call_id=call_id,
                tool_call_name=name,
                tool_call_arguments=args,
            )
        )
    return ChatMessage(
        id="a-tc",
        role=ChatMessageRole.Assistant,
        content=content,
        created_at=_now(),
        updated_at=_now(),
    )


def _tool_history_record(
    name: str, content: dict, call_id: str = "fake-call-id"
) -> ChatMessage:
    return ChatMessage(
        id=f"t-{call_id}",
        role=ChatMessageRole.Tool,
        content=[
            ToolCallResultContent(
                tool_call_result_id=f"r-{call_id}",
                tool_call_id=call_id,
                tool_call_name=name,
                tool_call_result=json.dumps(content, sort_keys=True),
            )
        ],
        created_at=_now(),
        updated_at=_now(),
    )


def _fixture_path(name: str) -> Path:
    return Path(__file__).resolve().parent / "data" / name


# ---------------------------------------------------------------------------
# Fix #2: Transient Reminder — never persisted to agent history
# ---------------------------------------------------------------------------


class Fix2TransientReminderTests(unittest.TestCase):
    """render_model_messages uses reminder transiently — not persisted."""

    def test_reminder_is_not_persisted_to_history(self) -> None:
        history = ChatHistory()
        history.add_message(_user_message("Change samp_rate to 48000"))
        _messages = render_model_messages(
            history,
            system_prompt=build_system_prompt("session"),
            semantic_search_result_preview=lambda _r: [],
            reminder="Call inspect_graph first.",
        )
        self.assertEqual(history.get_message_count(), 1)
        reminder_in_history = any(
            "Runtime reminder" in (item.content if isinstance(item, TextContent) else "")
            for message in history.get_messages()
            for item in message.content
            if isinstance(item, TextContent)
        )
        self.assertFalse(reminder_in_history)

    def test_no_reminder_when_none_passed(self) -> None:
        history = ChatHistory()
        history.add_message(_user_message("What is the sample rate?"))
        history.add_message(_tool_history_record("inspect_graph", {"ok": True, "view": "overview"}))
        history.add_message(_assistant_message("The sample rate is 32000."))
        messages = render_model_messages(
            history,
            system_prompt=build_system_prompt("session"),
            semantic_search_result_preview=lambda _r: [],
            reminder=None,
        )
        has_reminder = any(
            "Runtime reminder" in message.get_as_text() for message in messages
        )
        self.assertFalse(has_reminder)

    def test_reminder_does_not_alter_agent_get_model_messages_history(self) -> None:
        agent = GrcAgent()
        agent.chat_history.add_user_message("Disable the throttle block.")
        before_count = agent.chat_history.get_message_count()

        msgs = agent.get_model_messages(reminder="You need inspect_graph evidence.")
        after_count = agent.chat_history.get_message_count()

        self.assertEqual(after_count, before_count)
        has_reminder = any("runtime_directive" in m.get_as_text() for m in msgs)
        self.assertTrue(has_reminder)


# ---------------------------------------------------------------------------
# Fix #2: Recency Bias — reminder at END of messages array
# ---------------------------------------------------------------------------


class Fix2RecencyBiasTests(unittest.TestCase):
    """render_model_messages places the reminder at the END."""

    def test_reminder_after_all_history(self) -> None:
        history = ChatHistory()
        history.add_message(_user_message("Inspect the graph."))
        history.add_message(_tool_history_record("inspect_graph", {"ok": True, "summary": "..."}))
        history.add_message(_assistant_message("I see the graph."))
        history.add_message(_user_message("Now change samp_rate."))
        messages = render_model_messages(
            history,
            system_prompt=build_system_prompt("session"),
            semantic_search_result_preview=lambda _r: [],
            reminder="Use change_graph now.",
        )
        self.assertEqual(messages[-1].role, ChatMessageRole.User)
        self.assertIn("Use change_graph now.", messages[-1].get_as_text())
        self.assertIn("<runtime_directive>", messages[-1].get_as_text())

    def test_reminder_position_with_mixed_tool_history(self) -> None:
        history = ChatHistory()
        history.add_message(_user_message("Add an AGC block."))
        history.add_message(
            _assistant_with_tool_calls(
                [("c1", "inspect_graph", {"view": "overview"})]
            )
        )
        history.add_message(
            _tool_history_record(
                "inspect_graph",
                {"ok": True, "view": "overview", "summary": "3 blocks"},
                call_id="c1",
            )
        )
        history.add_message(
            _assistant_with_tool_calls(
                [("c2", "search_blocks", {"query": "AGC"})]
            )
        )
        history.add_message(
            _tool_history_record(
                "search_blocks",
                {"ok": True, "candidates": []},
                call_id="c2",
            )
        )
        history.add_message(_assistant_message("I need to search more."))
        messages = render_model_messages(
            history,
            system_prompt=build_system_prompt("session"),
            semantic_search_result_preview=lambda _r: [],
            reminder="Call the relevant tool now.",
        )
        self.assertGreater(len(messages), 2)
        self.assertEqual(messages[-1].role, ChatMessageRole.User)
        self.assertIn("Call the relevant tool now.", messages[-1].get_as_text())
        self.assertIn("<runtime_directive>", messages[-1].get_as_text())
        self.assertTrue(
            messages[-1].get_as_text().endswith("</runtime_directive>"),
            f"Reminder content should be wrapped: {messages[-1].get_as_text()!r}",
        )


# ---------------------------------------------------------------------------
# Fix #2: Template Safety — reminder role is Custom (tagged), not "system"
# ---------------------------------------------------------------------------


class Fix2TemplateSafetyTests(unittest.TestCase):
    """render_model_messages reminder is role:User — safe for all chat
    templates and standard on the OpenAI wire format."""

    def test_reminder_uses_user_role_not_system(self) -> None:
        history = ChatHistory()
        history.add_message(_user_message("Change sample rate."))
        messages = render_model_messages(
            history,
            system_prompt=build_system_prompt("session"),
            semantic_search_result_preview=lambda _r: [],
            reminder="Retry change_graph.",
        )
        self.assertEqual(messages[-1].role, ChatMessageRole.User)

    def test_reminder_is_wrapped_in_runtime_directive(self) -> None:
        """The reminder must be visually isolated from the human's
        text so the model can tell the control plane apart from the
        user. We wrap the body in ``<runtime_directive>`` tags.
        """
        history = ChatHistory()
        history.add_message(_user_message("hi"))
        messages = render_model_messages(
            history,
            system_prompt=build_system_prompt("session"),
            semantic_search_result_preview=lambda _r: [],
            reminder="Use change_graph now.",
        )
        body = messages[-1].get_as_text()
        self.assertTrue(body.startswith("<runtime_directive>"))
        self.assertTrue(body.endswith("</runtime_directive>"))
        self.assertIn("Use change_graph now.", body)

    def test_reminder_survives_openai_message_converter(self) -> None:
        """Reminder must produce a wire-valid role:user message; an
        earlier draft used a Custom-role tag that the OpenAI message
        converter mapped to ``role: "runtime_reminder"`` — a non-
        standard wire role that small backends may reject. Regression
        test for the runtime-reminder turn-failure reported when the
        chat-history refactor landed.
        """
        from ToolAgents.provider.message_converter.open_ai_message_converter import (
            OpenAIMessageConverter,
        )

        history = ChatHistory()
        history.add_message(_user_message("hi"))
        messages = render_model_messages(
            history,
            system_prompt=build_system_prompt("session"),
            semantic_search_result_preview=lambda _r: [],
            reminder="Use change_graph now.",
        )
        # Drive the converter the same way chat_api.get_response does.
        converter = OpenAIMessageConverter()
        converted = converter.to_provider_format(messages)
        wire_roles = {m["role"] for m in converted}
        self.assertIn("user", wire_roles)
        self.assertNotIn("runtime_reminder", wire_roles)
        self.assertNotIn("custom", wire_roles)

    def test_only_one_system_message_exists(self) -> None:
        """The main system prompt is the sole role:system message."""
        history = ChatHistory()
        history.add_message(_user_message("Hello."))
        history.add_message(_assistant_message("Hi! How can I help?"))
        messages = render_model_messages(
            history,
            system_prompt=build_system_prompt("session"),
            semantic_search_result_preview=lambda _r: [],
            reminder="Call the relevant tool.",
        )
        system_count = sum(1 for m in messages if m.role == ChatMessageRole.System)
        self.assertEqual(system_count, 1)

    def test_no_system_message_after_user_assistant_pairs(self) -> None:
        """No role:system message appears after user/assistant history begins."""
        history = ChatHistory()
        history.add_message(_user_message("Inspect."))
        history.add_message(_tool_history_record("inspect_graph", {"ok": True}))
        history.add_message(_assistant_message("Done."))
        messages = render_model_messages(
            history,
            system_prompt=build_system_prompt("session"),
            semantic_search_result_preview=lambda _r: [],
            reminder="Continue with change_graph.",
        )
        saw_first_user = False
        system_after_first_user = 0
        for m in messages:
            if m.role == ChatMessageRole.User and not saw_first_user:
                saw_first_user = True
            if saw_first_user and m.role == ChatMessageRole.System:
                system_after_first_user += 1
        self.assertEqual(
            system_after_first_user,
            0,
            f"Found system message(s) mid-conversation in {messages}",
        )


# ---------------------------------------------------------------------------
# Fix #2: _tool_retry_reminder integration — reminder generation behavior
# ---------------------------------------------------------------------------


class Fix2ReminderGenerationTests(unittest.TestCase):
    """_tool_retry_reminder triggers correctly under various conditions."""

    def test_schema_failure_triggers_change_graph_reminder(self) -> None:
        reminder = _tool_retry_reminder(
            user_message="Disable the throttle block.",
            assistant_text="I need to inspect first.",
            tool_calls_requested=1,
            tool_calls_executed=0,
            tool_names_requested=["change_graph"],
            change_graph_schema_failure_pending=True,
            change_graph_committed=False,
            change_graph_control_response=False,
            change_graph_wrong_insert_pending=False,
            change_graph_missing_evidence_pending=False,
            graph_ambiguity_pending=False,
        )
        self.assertIsNotNone(reminder)
        self.assertIn("change_graph", reminder.lower())

    def test_missing_evidence_triggers_agentic_reminder(self) -> None:
        reminder = _tool_retry_reminder(
            user_message="Add a new filter block.",
            assistant_text="I need to search.",
            tool_calls_requested=1,
            tool_calls_executed=0,
            tool_names_requested=["change_graph"],
            change_graph_schema_failure_pending=False,
            change_graph_committed=False,
            change_graph_control_response=False,
            change_graph_wrong_insert_pending=False,
            change_graph_missing_evidence_pending=True,
            graph_ambiguity_pending=False,
        )
        self.assertIsNotNone(reminder)
        self.assertIn("tool evidence", reminder.lower())

    def test_ambiguity_clarification_blocks_reminder(self) -> None:
        reminder = _tool_retry_reminder(
            user_message="Disable the QT GUI time sink.",
            assistant_text="Which sink — qtgui_time_sink_x_0 or qtgui_time_sink_x_1?",
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

    def test_committed_turn_does_not_trigger_mutation_reminder(self) -> None:
        reminder = _tool_retry_reminder(
            user_message="Disable the throttle.",
            assistant_text="Done.",
            tool_calls_requested=1,
            tool_calls_executed=1,
            tool_names_requested=["change_graph"],
            change_graph_schema_failure_pending=False,
            change_graph_committed=True,
            change_graph_control_response=False,
            change_graph_wrong_insert_pending=False,
            change_graph_missing_evidence_pending=False,
            graph_ambiguity_pending=False,
        )
        self.assertIsNone(reminder)


# ---------------------------------------------------------------------------
# Fix #2 (loop-level): reminder is injected through _run_turn_events
# ---------------------------------------------------------------------------


class ReminderLoopInjectionTests(unittest.TestCase):
    """Verify the END-TO-END reminder control flow in _run_turn_events.

    The pure-function ``_tool_retry_reminder`` tests above pass hand-set
    booleans. These tests drive the REAL loop with only ``chat_agent.step``
    mocked and assert that a non-None reminder is wired into chat_history
    as a ``<runtime_directive>`` user message, and that the model is
    re-invoked (correction_retries_used increments).
    """

    def _build_runner(self) -> tuple[Any, Any]:
        from unittest import mock

        from grc_agent.toolagents_runtime import (
            ToolAgentsLlamaProviderConfig,
            ToolAgentsRunner,
        )

        chat_agent = mock.MagicMock()
        chat_agent.get_default_settings.return_value = mock.MagicMock()
        runner = ToolAgentsRunner.__new__(ToolAgentsRunner)
        runner.provider_config = ToolAgentsLlamaProviderConfig(
            base_url="http://127.0.0.1:1", model="m"
        )
        runner.provider = mock.MagicMock()
        runner.chat_agent = chat_agent
        return runner, chat_agent

    def test_mutation_request_injects_reminder_into_history(self) -> None:
        """Same scenario but we hold the agent reference and assert the
        ``<runtime_directive>`` wrapper actually lands in chat_history."""
        runner, chat_agent = self._build_runner()
        agent = GrcAgent()
        chat_agent.step.side_effect = [
            _assistant_message("Sure, done!"),
            _assistant_message("I added the block now."),
        ]

        list(
            runner._run_turn_events(
                agent,
                "Add a new low pass filter block to the graph.",
                model=None,
                wrapper_eval_telemetry=False,
                max_tool_rounds=4,
                on_tool_start=None,
                on_tool_end=None,
            )
        )

        directive_messages = [
            m
            for m in agent.chat_history.get_messages()
            if m.role == ChatMessageRole.User
            and "<runtime_directive>" in m.get_as_text()
        ]
        self.assertGreaterEqual(
            len(directive_messages),
            1,
            "expected a <runtime_directive> user message in chat_history",
        )

    def test_reminder_not_re_injected_on_second_pass(self) -> None:
        """A given reminder key fires at most once (retry_reminders_used
        dedup). The second text-only answer must NOT trigger a second
        injection — the loop terminates instead."""
        runner, chat_agent = self._build_runner()
        agent = GrcAgent()
        chat_agent.step.side_effect = [
            _assistant_message("Sure, done!"),
            _assistant_message("Done again."),
        ]

        events = list(
            runner._run_turn_events(
                agent,
                "Add a new low pass filter block to the graph.",
                model=None,
                wrapper_eval_telemetry=False,
                max_tool_rounds=4,
                on_tool_start=None,
                on_tool_end=None,
            )
        )
        final_events = [e for e in events if e.get("event") == "final"]
        self.assertEqual(len(final_events), 1)
        result = final_events[0]["result"]
        # Exactly one retry — the second identical reminder is suppressed.
        self.assertEqual(result.get("correction_retries_used"), 1, result)
        # Only one <runtime_directive> message in history.
        directive_count = sum(
            1
            for m in agent.chat_history.get_messages()
            if m.role == ChatMessageRole.User
            and "<runtime_directive>" in m.get_as_text()
        )
        self.assertEqual(directive_count, 1)


# ---------------------------------------------------------------------------
# Fix #1: No premature exit on commit — commit result is role:"tool"
# ---------------------------------------------------------------------------


class Fix1CommitResultTests(unittest.TestCase):
    """Commit results flow as role:"tool" — no fake assistant synthesis."""

    def test_commit_result_appended_as_tool_role(self) -> None:
        """Drive the REAL loop: after a committed change_graph, the
        tool result must be recorded as role:Tool — no fabricated
        assistant synthesis message is inserted between the tool call
        and the tool result.

        The previous version of this test only asserted ``ok``/``committed``
        on the raw execute_tool result and never inspected chat_history,
        so the Fix #1 invariant was unverified.
        """
        from unittest import mock

        from grc_agent.toolagents_runtime import (
            ToolAgentsLlamaProviderConfig,
            ToolAgentsRunner,
        )

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        src = _fixture_path("random_bit_generator.grc")
        dst = Path(tmp.name) / "test.grc"
        shutil.copy2(src, dst)
        session = FlowgraphSession()
        session.load(dst)
        agent = GrcAgent(session)

        change_call = _assistant_with_tool_calls(
            [
                (
                    "c1",
                    "change_graph",
                    {
                        "update_params": [
                            {
                                "instance_name": "samp_rate",
                                "params": {"value": "48000"},
                            }
                        ]
                    },
                )
            ]
        )
        final_text_msg = _assistant_message("I updated the sample rate to 48000.")

        chat_agent = mock.MagicMock()
        chat_agent.step.side_effect = [change_call, final_text_msg]
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
                "Set the sample rate to 48000.",
                model=None,
                wrapper_eval_telemetry=False,
                max_tool_rounds=4,
                on_tool_start=None,
                on_tool_end=None,
            )
        )

        final_events = [e for e in events if e.get("event") == "final"]
        self.assertEqual(len(final_events), 1)
        result = final_events[0]["result"]
        self.assertTrue(result.get("ok"), result)
        self.assertTrue(result.get("committed") or result.get("tool_calls_executed"), result)

        # The commit result must flow as role:Tool, never as a fabricated
        # assistant synthesis message. Inspect the recorded chat history.
        messages = agent.chat_history.get_messages()
        tool_messages = [
            m for m in messages if m.role == ChatMessageRole.Tool
        ]
        # At least one Tool-role message exists (the change_graph result).
        self.assertGreaterEqual(
            len(tool_messages), 1, "change_graph result must be recorded as role:Tool"
        )
        # The change_graph committed — verify the tool result carries ok=True.
        committed_tool_payload = None
        for m in tool_messages:
            for item in m.content:
                if isinstance(item, ToolCallResultContent):
                    payload = json.loads(item.tool_call_result)
                    if (
                        isinstance(payload, dict)
                        and payload.get("tool") == "change_graph"
                        and payload.get("committed") is True
                    ):
                        committed_tool_payload = payload
        self.assertIsNotNone(
            committed_tool_payload,
            "expected a committed change_graph result recorded as role:Tool",
        )
        # The final assistant text is the model's real output, not a
        # synthesis fabricated by the loop.
        self.assertEqual(
            result.get("assistant_text"),
            "I updated the sample rate to 48000.",
        )

    def test_dispatcher_backward_compat_accepts_flat_state_string(self) -> None:
        errors: list[str] = []
        op = _update_state_operation(
            {"instance_name": "blocks_throttle2_0", "state": "disabled"},
            index=0,
            field_name="update_states",
            errors=errors,
        )
        self.assertIsNotNone(op, f"errors={errors}")
        self.assertEqual(op["op_type"], "update_states")
        self.assertEqual(op["state"], "disabled")

    def test_dispatcher_backward_compat_accepts_old_object_format(self) -> None:
        errors: list[str] = []
        op = _update_state_operation(
            {"instance_name": "blocks_throttle2_0", "states": {"state": "disabled"}},
            index=0,
            field_name="update_states",
            errors=errors,
        )
        self.assertIsNotNone(op, f"errors={errors}")
        self.assertEqual(op["op_type"], "update_states")
        self.assertEqual(op["state"], "disabled")

    def test_dispatcher_backward_compat_accepts_boolean_alias(self) -> None:
        errors: list[str] = []
        op = _update_state_operation(
            {"instance_name": "blocks_throttle2_0", "states": {"disabled": True}},
            index=0,
            field_name="update_states",
            errors=errors,
        )
        self.assertIsNotNone(op, f"errors={errors}")
        self.assertEqual(op["state"], "disabled")

    def test_dispatcher_accepts_bypass_state(self) -> None:
        errors: list[str] = []
        op = _update_state_operation(
            {"instance_name": "blocks_throttle2_0", "state": "bypass"},
            index=0,
            field_name="update_states",
            errors=errors,
        )
        self.assertIsNotNone(op, f"errors={errors}")
        self.assertEqual(op["state"], "bypass")

    def test_dispatcher_rejects_invalid_state_value(self) -> None:
        errors: list[str] = []
        op = _update_state_operation(
            {"instance_name": "blocks_throttle2_0", "state": "invisible"},
            index=0,
            field_name="update_states",
            errors=errors,
        )
        self.assertIsNone(op)
        self.assertTrue(any("state expected to be" in e for e in errors), errors)


# ---------------------------------------------------------------------------
# Fix #3: No forced_next_tool_name remnants
# ---------------------------------------------------------------------------


class Fix3NoForcedToolsTests(unittest.TestCase):
    """forced_next_tool_name and related code have been removed."""

    def test_forced_next_tool_name_not_in_module(self) -> None:
        import grc_agent.toolagents_runtime as mod
        self.assertFalse(
            hasattr(mod, "forced_next_tool_name"),
            "forced_next_tool_name should have been removed from the module",
        )

    def test_forced_tool_for_retry_reminder_removed(self) -> None:
        import grc_agent.toolagents_runtime as mod
        self.assertFalse(
            hasattr(mod, "_forced_tool_for_retry_reminder"),
            "_forced_tool_for_retry_reminder should have been removed",
        )

    def test_committed_change_text_removed(self) -> None:
        import grc_agent.toolagents_runtime as mod
        self.assertFalse(
            hasattr(mod, "_committed_change_text"),
            "_committed_change_text should have been removed",
        )

    def test_forced_change_graph_reminder_removed(self) -> None:
        import grc_agent.toolagents_runtime as mod
        self.assertFalse(
            hasattr(mod, "_FORCED_CHANGE_GRAPH_REMINDER"),
            "_FORCED_CHANGE_GRAPH_REMINDER should have been removed",
        )


# ---------------------------------------------------------------------------
# Fix #4: update_states flat enum — schema enforcement
# ---------------------------------------------------------------------------


class Fix4UpdateStatesEnumTests(unittest.TestCase):
    """update_states uses flat state enum; old object rejected at schema."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        src = _fixture_path("random_bit_generator.grc")
        dst = Path(cls._tmp.name) / "test.grc"
        shutil.copy2(src, dst)
        cls._session = FlowgraphSession()
        cls._session.load(dst)
        cls._agent = GrcAgent(cls._session)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_new_flat_enum_accepted(self) -> None:
        result = self._agent.execute_tool(
            "change_graph",
            {
                "update_states": [
                    {
                        "instance_name": "blocks_throttle2_0",
                        "state": "disabled",
                    }
                ],
                "force": True,
            },
            model_tool_call=True,
        )
        self.assertTrue(result["ok"], result)

    def test_old_object_format_rejected_by_schema(self) -> None:
        result = self._agent.execute_tool(
            "change_graph",
            {
                "update_states": [
                    {
                        "instance_name": "blocks_throttle2_0",
                        "states": {"state": "disabled"},
                    }
                ],
            },
            model_tool_call=True,
        )
        self.assertFalse(result["ok"], result)
        error_text = str(result.get("validation_errors", ""))
        has_state_required = any(
            "state" in str(e.get("field", "")) for e in result.get("validation_errors", [])
        )
        has_states_invalid = any(
            "states" in str(e.get("field", "")) for e in result.get("validation_errors", [])
        )
        self.assertTrue(
            has_state_required or has_states_invalid,
            f"Expected schema rejection for old format: {error_text}",
        )

    def test_new_flat_enum_rejects_invalid_value(self) -> None:
        result = self._agent.execute_tool(
            "change_graph",
            {
                "update_states": [
                    {
                        "instance_name": "blocks_throttle2_0",
                        "state": "invisible",
                    }
                ],
            },
            model_tool_call=True,
        )
        self.assertFalse(result["ok"], result)
        self.assertTrue(
            any(
                "state" in str(e.get("field", ""))
                for e in result.get("validation_errors", [])
            ),
            str(result.get("validation_errors", "")),
        )


# ---------------------------------------------------------------------------
# Cache invalidation: _catalog_version_token must reflect on-disk changes
# ---------------------------------------------------------------------------


class CatalogCacheInvalidationTests(unittest.TestCase):
    """The search-blocks cache key embeds ``_catalog_version_token`` to
    detect catalog file changes. If that function is ``lru_cache``d, the
    mtime freezes and the cache never invalidates — search returns stale
    results for the entire process lifetime.
    """

    def test_catalog_version_token_is_not_lru_cached(self) -> None:
        from grc_agent.agent import _catalog_version_token

        # lru_cache-decorated functions expose cache_info/cache_clear.
        self.assertFalse(
            hasattr(_catalog_version_token, "cache_info"),
            "_catalog_version_token must not be lru_cached — freezing the mtime "
            "defeats the cache invalidation it exists to provide.",
        )


if __name__ == "__main__":
    unittest.main()
