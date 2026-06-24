import datetime
import unittest
import uuid

from grc_agent.runtime.model_context import _prune_completed_episodes
from ToolAgents.data_models.messages import (
    ChatMessage,
    ChatMessageRole,
    TextContent,
    ToolCallContent,
    ToolCallResultContent,
)


def _msg(role: ChatMessageRole, content: list) -> ChatMessage:
    now = datetime.datetime.now()
    return ChatMessage(
        id=str(uuid.uuid4()),
        role=role,
        content=content,
        created_at=now,
        updated_at=now,
    )


class EpisodePruneTests(unittest.TestCase):
    def test_prior_tool_result_is_retained_across_episodes(self) -> None:
        messages = [
            _msg(ChatMessageRole.User, [TextContent(content="what blocks?")]),
            _msg(
                ChatMessageRole.Assistant,
                [
                    TextContent(content="checking"),
                    ToolCallContent(
                        tool_call_id="c1",
                        tool_call_name="inspect_graph",
                        tool_call_arguments={},
                    ),
                ],
            ),
            _msg(
                ChatMessageRole.Tool,
                [
                    ToolCallResultContent(
                        tool_call_result_id="r1",
                        tool_call_id="c1",
                        tool_call_name="inspect_graph",
                        tool_call_result='{"ok": true, "blocks": ["samp_rate"]}',
                    )
                ],
            ),
            _msg(ChatMessageRole.Assistant, [TextContent(content="found samp_rate")]),
            _msg(ChatMessageRole.User, [TextContent(content="now change it")]),
        ]

        pruned = _prune_completed_episodes(messages)

        roles = [message.role for message in pruned]
        self.assertIn(ChatMessageRole.Tool, roles)

    def test_old_runtime_directives_still_dropped(self) -> None:
        messages = [
            _msg(
                ChatMessageRole.User,
                [TextContent(content="<runtime_directive>old nudge</runtime_directive>")],
            ),
            _msg(ChatMessageRole.User, [TextContent(content="real question")]),
        ]

        pruned = _prune_completed_episodes(messages)

        self.assertEqual(len(pruned), 1)
        self.assertEqual(pruned[0].role, ChatMessageRole.User)


if __name__ == "__main__":
    unittest.main()
