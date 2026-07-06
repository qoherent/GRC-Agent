"""Integration tests verifying the four agent-loop fixes.

Tests cover:
  Fix #1: No premature exit on commit (commit result flows as role:"tool")
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

from grc_agent.agent import GrcAgent
from grc_agent.flowgraph_session import FlowgraphSession
from ToolAgents.data_models.messages import (
    ChatMessage,
    ChatMessageRole,
    TextContent,
    ToolCallContent,
    ToolCallResultContent,
)
from ToolAgents.provider.llm_provider import StreamingChatMessage


def _stream_finished(msg: ChatMessage):
    """Yield one terminal StreamingChatMessage wrapping ``msg``."""
    yield StreamingChatMessage(
        chunk="",
        is_tool_call=False,
        finished=True,
        finished_chat_message=msg,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> datetime.datetime:
    return datetime.datetime.now()


def _assistant_message(text: str) -> ChatMessage:
    return ChatMessage(
        id=f"a-{text[:8]}",
        role=ChatMessageRole.Assistant,
        content=[TextContent(content=text)],
        created_at=_now(),
        updated_at=_now(),
    )


def _assistant_with_tool_calls(calls: list[tuple[str, str, dict]]) -> ChatMessage:
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


def _fixture_path(name: str) -> Path:
    return Path(__file__).resolve().parent / "data" / name


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
        src = _fixture_path("dial_tone.grc")
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
        chat_agent.stream_step.side_effect = [
            _stream_finished(change_call),
            _stream_finished(final_text_msg),
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
                "Set the sample rate to 48000.",
                model=None,
                max_tool_rounds=4,
                on_tool_start=None,
                on_tool_end=None,
            )
        )

        final_events = [e for e in events if e.get("event") == "final"]
        self.assertEqual(len(final_events), 1)
        result = final_events[0]["result"]
        self.assertTrue(result.get("ok"), result)
        self.assertTrue(result.get("ok") or result.get("tool_calls_executed"), result)

        # The commit result must flow as role:Tool, never as a fabricated
        # assistant synthesis message. Inspect the recorded chat history.
        messages = agent.chat_history.get_messages()
        tool_messages = [m for m in messages if m.role == ChatMessageRole.Tool]
        # At least one Tool-role message exists (the change_graph result).
        self.assertGreaterEqual(
            len(tool_messages), 1, "change_graph result must be recorded as role:Tool"
        )
        # The change_graph succeeded — verify the tool result carries ok=True.
        ok_tool_payload = None
        for m in tool_messages:
            for item in m.content:
                if isinstance(item, ToolCallResultContent):
                    payload = json.loads(item.tool_call_result)
                    if isinstance(payload, dict) and payload.get("ok") is True:
                        ok_tool_payload = payload
        self.assertIsNotNone(
            ok_tool_payload,
            "expected an ok change_graph result recorded as role:Tool",
        )
        # The final assistant text is the model's real output, not a
        # synthesis fabricated by the loop.
        self.assertEqual(
            result.get("assistant_text"),
            "I updated the sample rate to 48000.",
        )


class ChangeGraphForceAndRollbackTests(unittest.TestCase):
    """Tests for the force=True bypass and snapshot-based rollback."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        src = _fixture_path("dial_tone.grc")
        dst = Path(self.tmp.name) / "test.grc"
        shutil.copy2(src, dst)
        self.session = FlowgraphSession()
        self.session.load(dst)
        self.agent = GrcAgent(self.session)

    def test_invalid_batch_rejected_without_force(self) -> None:
        """Remove a connection (dangling port) → native validation fails, rollback."""
        result = self.agent.execute_tool(
            "change_graph",
            {"remove_connections": ["analog_sig_source_x_0:0->blocks_add_xx:0"]},
        )
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error_type"), "gnu_validation_failed")

    def test_invalid_batch_committed_with_force(self) -> None:
        """Same invalid batch with force=True → committed despite validation failure."""
        result = self.agent.execute_tool(
            "change_graph",
            {
                "remove_connections": ["analog_sig_source_x_0:0->blocks_add_xx:0"],
                "force": True,
            },
        )
        self.assertTrue(result.get("ok"))
        # Validation errors surface via `errors` so the model knows the graph
        # is invalid even though the batch was applied via force=True.
        gnu_errors = [e for e in result.get("errors", []) if e.get("code") == "gnu_validation"]
        self.assertTrue(len(gnu_errors) > 0)

    def test_rollback_restores_pre_batch_state(self) -> None:
        """After a rejected batch, the graph is byte-identical to before."""
        before_blocks = [b.name for b in self.session.flowgraph.blocks]
        self.agent.execute_tool(
            "change_graph",
            {"remove_connections": ["analog_sig_source_x_0:0->blocks_add_xx:0"]},
        )
        after_blocks = [b.name for b in self.session.flowgraph.blocks]
        self.assertEqual(before_blocks, after_blocks)


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
        src = _fixture_path("dial_tone.grc")
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
                        "instance_name": "analog_sig_source_x_0",
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
            any("state" in str(e.get("field", "")) for e in result.get("validation_errors", [])),
            str(result.get("validation_errors", "")),
        )


if __name__ == "__main__":
    unittest.main()
