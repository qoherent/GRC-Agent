"""Tests for grc_agent.chat.format — pure formatting functions extracted from
chat_sidebar.py. No GTK import anywhere in this file: these run under the
fast gate without xvfb, proving R34's "testable without a display" bar for
the one module in the split that has no widget dependency at all.
"""


def test_parse_final_summary_accepts_grc_agent_response_shapes():
    """The model's final structured output (GrcAgentResponse) arrives as a
    final_result tool call; _parse_final_summary must recover (actions,
    explanation) from both the dict form (pydantic-ai ToolCallPart.args) and
    the JSON-string form, and return None for anything else so the caller
    falls back to a normal tool expander."""
    from grc_agent.chat.format import _parse_final_summary

    assert _parse_final_summary(
        {"actions_taken": ["Added x", "Connected y"], "explanation": "Graph valid"}
    ) == (["Added x", "Connected y"], "Graph valid")
    assert _parse_final_summary('{"actions_taken": ["a"], "explanation": "e"}') == (["a"], "e")
    # Missing explanation is tolerated (the field is required by the schema,
    # but a malformed model response must degrade to a card, not a crash).
    assert _parse_final_summary({"actions_taken": ["a"]}) == (["a"], "")

    # Non-GrcAgentResponse shapes -> None (render as a plain tool expander).
    assert _parse_final_summary({"foo": "bar"}) is None
    assert _parse_final_summary({"actions_taken": "not a list"}) is None
    assert _parse_final_summary({"actions_taken": [1, 2]}) is None
    assert _parse_final_summary("not json") is None
    assert _parse_final_summary(None) is None
    assert _parse_final_summary(42) is None
    assert _parse_final_summary("") is None


def test_query_knowledge_label_shows_search_mode():
    from grc_agent.chat.format import _tool_label

    assert (
        _tool_label("query_knowledge", result='{"search_mode": "vector"}')
        == "⚙ query_knowledge (vector) ✓"
    )
    assert (
        _tool_label("query_knowledge", result='{"search_mode": "lexical"}')
        == "⚙ query_knowledge (lexical) ✓"
    )
    assert (
        _tool_label("query_knowledge", result='{"search_mode": "hybrid"}')
        == "⚙ query_knowledge (hybrid) ✓"
    )
    assert (
        _tool_label("query_knowledge", ok=False, result='{"search_mode": "vector"}')
        == "⚙ query_knowledge (vector) ✗"
    )
    assert (
        _tool_label("query_knowledge", retry=True, result='{"search_mode": "lexical"}')
        == "⚠ query_knowledge (lexical) retry"
    )
    assert _tool_label("inspect_graph", result='{"ok": true}') == "⚙ inspect_graph ✓"


def test_tool_label_running_and_default():
    from grc_agent.chat.format import _tool_label, _tool_label_running

    assert _tool_label_running("change_graph") == "⚙ change_graph ..."
    # No result yet, no explicit ok -> defaults to the success glyph. This is
    # the exact default the streaming path's tool-status bug relied on; the
    # fix lives in chat_sidebar's _set_tool_result, not here.
    assert _tool_label("change_graph") == "⚙ change_graph ✓"
    assert _tool_label("change_graph", ok=False) == "⚙ change_graph ✗"


def test_format_tokens():
    from grc_agent.chat.format import format_tokens

    assert format_tokens(500) == "500"
    assert format_tokens(1200) == "1.2k"
    assert format_tokens(14710) == "14.7k"
    assert format_tokens(128000) == "128k"
    assert format_tokens(1_500_000) == "1.5M"


def test_format_tool_display_truncates_with_marker():
    from grc_agent.chat.format import _format_tool_display

    short = "hello"
    assert _format_tool_display(short) == short

    long_text = "x" * 20000
    out = _format_tool_display(long_text, max_chars=100)
    assert len(out) < len(long_text)
    assert "truncated" in out
    assert str(len(long_text) - 100) in out
    # Head and tail both survive, per the no-silent-truncation rule.
    assert out.startswith("x" * 10)
    assert out.endswith("x" * 10)


def test_transcript_fragments_combine_call_and_result():
    from grc_agent.chat.format import (
        _transcript_summary,
        _transcript_tool_call,
        _transcript_tool_result,
    )

    call_only = _transcript_tool_call("change_graph", '{"reason": "x"}')
    assert call_only == '<Tool Call: change_graph>\nArgs: {"reason": "x"}\n'

    combined = _transcript_tool_call("change_graph", '{"reason": "x"}', '{"ok": true}')
    assert combined == (
        '<Tool Call: change_graph>\nArgs: {"reason": "x"}\nResult: {"ok": true}\n'
    )

    assert _transcript_tool_result('{"ok": true}') == '<Tool Result: {"ok": true}>\n'

    summary = _transcript_summary(["Added x"], "Done")
    assert summary == "<Summary>\n['Added x']\nDone\n</Summary>\n"


def test_tool_args_text_uses_json_not_repr():
    from unittest.mock import MagicMock

    from grc_agent.chat.format import _tool_args_text

    part = MagicMock()
    part.args = {"k": "v"}
    part.args_as_json_str.return_value = '{"k":"v"}'
    assert _tool_args_text(part) == '{"k":"v"}'

    empty = MagicMock()
    empty.args = None
    assert _tool_args_text(empty) == ""
