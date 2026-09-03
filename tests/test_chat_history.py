"""Tests for grc_agent.chat.history — message-history cleaning for the turn
loop. No GTK import: runs under the fast gate without xvfb.
"""


def test_clean_message_history_for_new_turn():
    """_clean_message_history_for_new_turn must pop trailing ModelResponses
    that contain unprocessed tool calls so PydanticAI accepts a subsequent
    user prompt without raising UserError."""
    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        RetryPromptPart,
        TextPart,
        ToolCallPart,
        UserPromptPart,
    )

    from grc_agent.chat.history import _clean_message_history_for_new_turn

    # Case 1: Trailing ModelResponse with ToolCallPart is trimmed
    msgs = [
        ModelRequest(parts=[UserPromptPart(content="turn 1")]),
        ModelResponse(parts=[ToolCallPart("change_graph", {})]),
    ]
    cleaned = _clean_message_history_for_new_turn(msgs)
    assert len(cleaned) == 1
    assert isinstance(cleaned[0].parts[0], UserPromptPart)

    # Case 2: Multi-retry failure ending in ToolCallPart is trimmed
    msgs = [
        ModelRequest(parts=[UserPromptPart(content="turn 1")]),
        ModelResponse(parts=[ToolCallPart("change_graph", {})]),
        ModelRequest(parts=[RetryPromptPart(content="retry 1")]),
        ModelResponse(parts=[ToolCallPart("change_graph", {})]),
    ]
    cleaned = _clean_message_history_for_new_turn(msgs)
    assert len(cleaned) == 3
    assert isinstance(cleaned[-1].parts[0], RetryPromptPart)

    # Case 3: Completed response with TextPart is preserved intact
    msgs = [
        ModelRequest(parts=[UserPromptPart(content="turn 1")]),
        ModelResponse(parts=[TextPart("all done")]),
    ]
    cleaned = _clean_message_history_for_new_turn(msgs)
    assert len(cleaned) == 2

    # Empty history is a no-op, not an IndexError.
    assert _clean_message_history_for_new_turn([]) == []


def test_without_truncated_thinking_tail():
    """A finish_reason='length' response containing ONLY ThinkingPart(s) is
    detached; anything else (a real answer, a partial finish reason, mixed
    content) is left alone."""
    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        TextPart,
        ThinkingPart,
        UserPromptPart,
    )

    from grc_agent.chat.history import _without_truncated_thinking_tail

    base = ModelRequest(parts=[UserPromptPart(content="continue")])

    truncated = [
        base,
        ModelResponse(parts=[ThinkingPart(content="reasoning")], finish_reason="length"),
    ]
    cleaned, removed = _without_truncated_thinking_tail(truncated)
    assert removed is True
    assert cleaned == [base]

    # A real answer at finish_reason='length' is NOT reasoning-only -- kept.
    has_text = [
        base,
        ModelResponse(
            parts=[ThinkingPart(content="reasoning"), TextPart(content="answer")],
            finish_reason="length",
        ),
    ]
    cleaned, removed = _without_truncated_thinking_tail(has_text)
    assert removed is False
    assert cleaned == has_text

    # Reasoning-only but NOT length-truncated -- kept.
    normal_finish = [
        base,
        ModelResponse(parts=[ThinkingPart(content="reasoning")], finish_reason="stop"),
    ]
    cleaned, removed = _without_truncated_thinking_tail(normal_finish)
    assert removed is False

    assert _without_truncated_thinking_tail([]) == ([], False)


def test_messages_call_tool():
    from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart, UserPromptPart

    from grc_agent.chat.history import _messages_call_tool

    msgs = [
        ModelRequest(parts=[UserPromptPart(content="x")]),
        ModelResponse(parts=[ToolCallPart("write_plan", {})]),
    ]
    assert _messages_call_tool(msgs, "write_plan") is True
    assert _messages_call_tool(msgs, "change_graph") is False
    assert _messages_call_tool([], "write_plan") is False

