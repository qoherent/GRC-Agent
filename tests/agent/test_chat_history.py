"""Tests for the new ChatHistory-based message-history path.

These tests cover round-trips and resume capabilities.
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
        from grc_agent.chat_roles import (
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
        tool_result = rebuilt.get_messages()[1].get_tool_call_results()[0]
        self.assertEqual(tool_result.tool_call_id, "call-1")
        self.assertEqual(len(tool_result.tool_call_result), 4000)

    def test_resume_skips_corrupt_payload(self) -> None:
        from grc_agent.chat_roles import chat_message_from_payload

        self.assertIsNone(chat_message_from_payload(None))
        self.assertIsNone(chat_message_from_payload({}))
        self.assertIsNone(chat_message_from_payload({"role": "garbage"}))


if __name__ == "__main__":
    unittest.main()
