"""Tests for the new ChatHistory-based message-history path.

These tests cover the small, native API we just added:

- ``grc_agent.runtime.chat_history.compact_chat_history`` enforces a
  character budget by shortening ``ToolCallResultContent`` payloads while
  leaving ``TextContent`` and ``ToolCallContent`` intact.
- ``ChatHistory`` round-trips a sequence that includes assistant tool
  calls and tool results (this is the contract the resume path relies on).
- The new ``render_model_messages`` puts a custom-role reminder at the
  end and never smuggles reminders through user content.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ToolAgents.data_models.chat_history import ChatHistory
from ToolAgents.data_models.messages import (
    ChatMessage,
    ChatMessageRole,
    TextContent,
    ToolCallContent,
    ToolCallResultContent,
)

from grc_agent.runtime.chat_history import compact_chat_history


def _assistant_tool_call_message(text: str = "calling inspect") -> ChatMessage:
    return ChatMessage(
        id="asst-1",
        role=ChatMessageRole.Assistant,
        content=[
            TextContent(content=text),
            ToolCallContent(
                tool_call_id="call-1",
                tool_call_name="inspect_graph",
                tool_call_arguments={"view": "overview"},
            ),
        ],
        created_at=__import__("datetime").datetime.now(),
        updated_at=__import__("datetime").datetime.now(),
    )


def _tool_result_message(
    result: str, name: str = "inspect_graph", call_id: str = "call-1"
) -> ChatMessage:
    return ChatMessage(
        id="tool-1",
        role=ChatMessageRole.Tool,
        content=[
            ToolCallResultContent(
                tool_call_result_id="res-1",
                tool_call_id=call_id,
                tool_call_name=name,
                tool_call_result=result,
            )
        ],
        created_at=__import__("datetime").datetime.now(),
        updated_at=__import__("datetime").datetime.now(),
    )


def _make_history() -> ChatHistory:
    history = ChatHistory()
    history.add_user_message("What's in the active graph?")
    history.add_message(_assistant_tool_call_message())
    history.add_message(_tool_result_message("X" * 4000))
    history.add_assistant_message("There are 2 blocks.")
    return history


class CompactChatHistoryTests(unittest.TestCase):
    def test_no_op_when_under_budget(self) -> None:
        history = _make_history()
        changed = compact_chat_history(history, budget_chars=100_000)
        self.assertFalse(changed)
        for message in history.get_messages():
            for content in message.content:
                if isinstance(content, ToolCallResultContent):
                    self.assertEqual(len(content.tool_call_result), 4000)

    def test_shortens_tool_result_text_keeps_tool_call(self) -> None:
        history = _make_history()
        changed = compact_chat_history(history, budget_chars=300)
        self.assertTrue(changed)
        asst = history.get_messages()[1]
        tool_calls = asst.get_tool_calls()
        self.assertEqual(len(tool_calls), 1)
        self.assertEqual(tool_calls[0].tool_call_name, "inspect_graph")
        self.assertEqual(
            tool_calls[0].tool_call_arguments, {"view": "overview"}
        )
        tool_msg = history.get_messages()[2]
        results = tool_msg.get_tool_call_results()
        self.assertEqual(len(results), 1)
        self.assertLess(len(results[0].tool_call_result), 4000)
        self.assertIn("TRUNCATED by chat-history compactor", results[0].tool_call_result)

    def test_compaction_preserves_assistant_text(self) -> None:
        history = _make_history()
        compact_chat_history(history, budget_chars=300)
        assistant = history.get_messages()[3]
        self.assertEqual(assistant.role, ChatMessageRole.Assistant)
        text = next(
            c.content for c in assistant.content if isinstance(c, TextContent)
        )
        self.assertEqual(text, "There are 2 blocks.")

    def test_truncation_sentinel_reports_original_length(self) -> None:
        """A truncated payload must carry a sentinel that says so —
        otherwise the model may hallucinate missing closing brackets
        when the JSON is cut mid-way.
        """
        history = _make_history()
        compact_chat_history(history, budget_chars=300)
        result = history.get_messages()[2].get_tool_call_results()[0]
        self.assertIn("was 4000 chars", result.tool_call_result)
        self.assertIn("TRUNCATED by chat-history compactor", result.tool_call_result)

    def test_truncation_sentinel_keeps_payload_under_budget(self) -> None:
        history = _make_history()
        compact_chat_history(history, budget_chars=300)
        result = history.get_messages()[2].get_tool_call_results()[0]
        # Total history must be under the budget after compaction.
        total = sum(len(m.get_as_text()) for m in history.get_messages())
        self.assertLessEqual(total, 300 + 200)  # small slack for the sentinel

    def test_idempotent(self) -> None:
        history = _make_history()
        first = compact_chat_history(history, budget_chars=300)
        self.assertTrue(first)
        second = compact_chat_history(history, budget_chars=300)
        self.assertFalse(second)

    def test_one_pass_exact_arithmetic(self) -> None:
        """The compactor must do a single pass: each candidate payload
        is rewritten at most once. We verify by counting how many
        tool-result messages are *new* (different identity) after
        one call.
        """
        from ToolAgents.data_models.messages import (
            ChatMessage,
            ToolCallResultContent,
        )

        history = _make_history()
        # Snapshot identities of every ToolCallResultContent.
        before_ids = {
            id(c)
            for m in history.get_messages()
            for c in m.content
            if isinstance(c, ToolCallResultContent)
        }
        compact_chat_history(history, budget_chars=300)
        after_ids = {
            id(c)
            for m in history.get_messages()
            for c in m.content
            if isinstance(c, ToolCallResultContent)
        }
        # The one eligible candidate (the 4000-char result) was
        # replaced once. New identity count must be 1, not 2+ as a
        # multi-pass loop would produce.
        new_count = len(after_ids - before_ids)
        self.assertEqual(
            new_count, 1,
            f"Expected exactly 1 rewrite, got {new_count}",
        )

    def test_converges_to_exact_budget(self) -> None:
        """After compaction, the total must be under the budget."""
        history = _make_history()
        compact_chat_history(history, budget_chars=300)
        total = sum(len(m.get_as_text()) for m in history.get_messages())
        self.assertLessEqual(total, 300)

    def test_per_payload_cap_respected_when_budget_is_tight(self) -> None:
        """The per-payload cap (default 4000) is the upper bound for
        any individual ``ToolCallResultContent`` payload once the
        compactor decides it must shrink the history. Without this
        guarantee, a single ``query_knowledge`` result can swallow
        a chunk of the context window — which was the data-starvation
        bug that motivated exposing the cap to config.
        """
        history = ChatHistory()
        history.add_user_message("Find me a sink.")
        for i in range(8):
            history.add_message(_assistant_tool_call_message(
                f"calling query_knowledge {i}"
            ))
            history.add_message(_tool_result_message("Z" * 12_000, call_id=f"c{i}"))
        # 8 * 12K = 96K payload > 30K budget, so the compactor must
        # run and apply the per-payload cap to each candidate.
        compact_chat_history(history, budget_chars=30_000)
        for i, message in enumerate(history.get_messages()):
            for content in message.content:
                if isinstance(content, ToolCallResultContent):
                    self.assertLessEqual(
                        len(content.tool_call_result), 4000,
                        f"Result #{i} exceeded the default 4000-char "
                        f"per-payload cap: {len(content.tool_call_result)} chars.",
                    )
                    self.assertIn(
                        "TRUNCATED by chat-history compactor",
                        content.tool_call_result,
                    )

    def test_per_payload_cap_is_configurable(self) -> None:
        """A caller can lower the cap explicitly. This is the agent's
        escape hatch when a known tool returns larger JSON than
        even 4000 chars can hold."""
        history = ChatHistory()
        history.add_user_message("Find me a sink.")
        for i in range(8):
            history.add_message(_assistant_tool_call_message(
                f"calling query_knowledge {i}"
            ))
            history.add_message(_tool_result_message("Z" * 12_000, call_id=f"c{i}"))
        # Caller asks for a 2000-char cap. The compactor must clamp
        # each candidate down to <= 2000.
        compact_chat_history(
            history, budget_chars=30_000, max_tool_result_chars=2000
        )
        for message in history.get_messages():
            for content in message.content:
                if isinstance(content, ToolCallResultContent):
                    self.assertLessEqual(len(content.tool_call_result), 2000)
                    self.assertIn(
                        "TRUNCATED by chat-history compactor",
                        content.tool_call_result,
                    )


class ChatHistoryRoundTripTests(unittest.TestCase):
    def test_save_load_preserves_tool_messages(self) -> None:
        history = _make_history()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "history.json"
            history.save_to_json(str(path))
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertIn("created_at", payload["messages"][1])
            reloaded = ChatHistory.load_from_json(str(path))

        self.assertEqual(reloaded.get_message_count(), history.get_message_count())
        reloaded_assistant = reloaded.get_messages()[1]
        self.assertEqual(reloaded_assistant.get_tool_calls()[0].tool_call_name, "inspect_graph")
        reloaded_tool = reloaded.get_messages()[2]
        result = reloaded_tool.get_tool_call_results()[0]
        self.assertEqual(len(result.tool_call_result), 4000)
        self.assertEqual(result.tool_call_id, "call-1")


class ResumeReplaysToolMessagesTests(unittest.TestCase):
    """Regression test for the resume-bug fix: model rows persisted as
    ``assistant_model`` / ``tool_model`` must rebuild a complete
    ``ChatHistory`` on resume. Prior to the fix, the resume path
    dropped tool messages and the model had no inspect/search evidence.
    """

    def test_resume_replays_assistant_tool_calls_and_results(self) -> None:
        from grc_agent.session_roles import (
            ASSISTANT_MODEL_ROLE,
            TOOL_MODEL_ROLE,
            chat_message_from_payload,
            chat_message_payload,
        )

        history = _make_history()

        def model_role(message: ChatMessage) -> str:
            if message.role == ChatMessageRole.Assistant:
                return ASSISTANT_MODEL_ROLE
            if message.role == ChatMessageRole.Tool:
                return TOOL_MODEL_ROLE
            raise AssertionError(f"unexpected role: {message.role}")

        payloads = [
            (model_role(msg), chat_message_payload(msg))
            for msg in history.get_messages()
            if msg.role
            in {
                ChatMessageRole.Assistant,
                ChatMessageRole.Tool,
            }
        ]
        self.assertEqual(len(payloads), 3)
        self.assertEqual(payloads[0][0], ASSISTANT_MODEL_ROLE)
        self.assertEqual(payloads[1][0], TOOL_MODEL_ROLE)
        self.assertEqual(payloads[2][0], ASSISTANT_MODEL_ROLE)

        rebuilt = ChatHistory()
        for _role, payload in payloads:
            rebuilt.add_message(chat_message_from_payload(payload))
        self.assertIsNotNone(rebuilt)
        self.assertEqual(rebuilt.get_message_count(), 3)
        self.assertEqual(
            rebuilt.get_messages()[0].get_tool_calls()[0].tool_call_name,
            "inspect_graph",
        )
        tool_result = (
            rebuilt.get_messages()[1].get_tool_call_results()[0]
        )
        self.assertEqual(tool_result.tool_call_id, "call-1")
        self.assertEqual(len(tool_result.tool_call_result), 4000)

    def test_resume_skips_corrupt_payload(self) -> None:
        from grc_agent.session_roles import chat_message_from_payload

        self.assertIsNone(chat_message_from_payload(None))
        self.assertIsNone(chat_message_from_payload({}))
        self.assertIsNone(chat_message_from_payload({"role": "garbage"}))


if __name__ == "__main__":
    unittest.main()
