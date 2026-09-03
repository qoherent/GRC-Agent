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


def test_sanitize_history_for_executor_prunes_tool_debris_and_merges():
    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        RetryPromptPart,
        TextPart,
        ToolCallPart,
        ToolReturnPart,
        UserPromptPart,
    )

    from grc_agent.chat.history import _sanitize_history_for_executor

    # Realistic history: User asks, planner calls inspect_graph, receives return,
    # tries write_plan, receives retry prompt, tries write_plan again, and finally gives text answer.
    raw_history = [
        ModelRequest(parts=[UserPromptPart(content="Build QPSK modulator")]),
        ModelResponse(parts=[TextPart(content="Looking at graph"), ToolCallPart("inspect_graph", {})]),
        ModelRequest(parts=[ToolReturnPart("inspect_graph", "graph json...")]),
        ModelResponse(parts=[ToolCallPart("write_plan", {"items": "bad"})]),
        ModelRequest(parts=[RetryPromptPart(content="list_type error")]),
        ModelResponse(parts=[TextPart(content="Here is the finalized plan: Step 1, Step 2.")]),
    ]

    sanitized = _sanitize_history_for_executor(raw_history)

    # Must have 2 alternating messages: 1 user, 1 assistant
    assert len(sanitized) == 2
    assert isinstance(sanitized[0], ModelRequest)
    assert isinstance(sanitized[1], ModelResponse)

    # User request is untouched
    assert sanitized[0].parts[0].content == "Build QPSK modulator"

    # Assistant has both text parts merged into one ModelResponse
    text_contents = [p.content for p in sanitized[1].parts if isinstance(p, TextPart)]
    assert text_contents == ["Looking at graph", "Here is the finalized plan: Step 1, Step 2."]

    # Zero tool call, tool return, or retry parts survive
    all_parts = [p for msg in sanitized for p in msg.parts]
    assert not any(isinstance(p, (ToolCallPart, ToolReturnPart, RetryPromptPart)) for p in all_parts)


def test_extract_plan_from_text_variants():
    from grc_agent.chat.history import extract_plan_from_text

    # Markdown heading style: '### Step \d+ [-—–:] ...'
    markdown_plan = """
    ## QPSK End-to-End Plan
    ### Step 1 — Global variables
    Add samp_rate and sym_rate.
    ### Step 2 — Modulator chain
    Add psk_mod block.
    ### Step 3: QT GUI sinks
    Show signal at all stages.
    """
    items = extract_plan_from_text(markdown_plan)
    assert len(items) == 3
    assert items[0].content == "Global variables"
    assert items[1].content == "Modulator chain"
    assert items[2].content == "QT GUI sinks"

    # Numbered list style: '1. ...'
    numbered_plan = """
    Here is what we will do:
    1. Setup global variables
    2. Add QPSK constellation modulator
    3. Connect channel model and noise
    4. Demodulate and calculate BER
    """
    items_num = extract_plan_from_text(numbered_plan)
    assert len(items_num) == 4
    assert items_num[0].content == "Setup global variables"
    assert items_num[3].content == "Demodulate and calculate BER"

    # Unstructured text or < 2 steps -> empty list
    assert extract_plan_from_text("Just a single thought without steps.") == []
    assert extract_plan_from_text("1. Only one item") == []
    assert extract_plan_from_text("") == []

