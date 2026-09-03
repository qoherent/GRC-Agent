"""Message-history cleaning for the turn loop.

Pure functions extracted from ``chat_sidebar.py`` — no GTK, no ``self``.
These repair state a prior turn's abort/retry-exhaustion path left behind,
so a new user prompt can be sent without pydantic-ai rejecting the history
as ending on unprocessed tool calls, or the agent choking on a truncated
reasoning tail.
"""

from __future__ import annotations

import logging

from pydantic_ai.messages import ModelMessage, ModelResponse, ThinkingPart, ToolCallPart

_log = logging.getLogger(__name__)

def _clean_message_history_for_new_turn(
    messages: list[ModelMessage],
) -> list[ModelMessage]:
    """Ensure message_history is valid for a new user prompt.

    PydanticAI rejects any run whose message_history ends on a ModelResponse
    with unfulfilled tool_calls (raising UserError: "Cannot provide a new user
    prompt when the message history contains unprocessed tool calls.").

    If an earlier turn aborted, hit max retries, or was persisted with
    trailing unprocessed tool calls, pop trailing ModelResponse messages with
    tool_calls so the next turn can start cleanly.
    """
    cleaned = list(messages)
    while cleaned:
        last = cleaned[-1]
        if isinstance(last, ModelResponse) and last.tool_calls:
            _log.warning(
                "cleaning history for a new turn: dropping a response with %d unprocessed "
                "tool call(s) %s — the calls stay recoverable in the step-store snapshots",
                len(last.tool_calls),
                [tc.tool_name for tc in last.tool_calls],
            )
            cleaned.pop()
            continue
        break
    return cleaned


def _messages_call_tool(messages: list[ModelMessage], tool_name: str) -> bool:
    """Whether this run emitted a call to one exact Pydantic AI function tool."""
    return any(
        isinstance(part, ToolCallPart) and part.tool_name == tool_name
        for message in messages
        for part in getattr(message, "parts", [])
    )


def _without_truncated_thinking_tail(
    messages: list[ModelMessage],
) -> tuple[list[ModelMessage], bool]:
    """Detach a provider-length response that contains reasoning and no output.

    Pydantic AI raises ``UnexpectedModelBehavior`` for this exact structural
    state. Keeping the 65k-token repetition in active history makes the next
    request and every re-render pay for unusable output; callers archive the
    full transcript before accepting this cleaned history.
    """
    if not messages:
        return messages, False
    last = messages[-1]
    if (
        isinstance(last, ModelResponse)
        and last.finish_reason == "length"
        and last.parts
        and all(isinstance(part, ThinkingPart) for part in last.parts)
    ):
        return messages[:-1], True
    return messages, False
