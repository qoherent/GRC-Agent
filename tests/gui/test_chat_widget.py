import os
import sys

# Add src to system path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../src")))

try:
    from grc_agent_gui.chat_widget import ChatWidget
except ImportError:
    ChatWidget = None


def test_chat_widget_imports_exist():
    """Assert that ChatWidget module exists and can be imported under TDD."""
    assert ChatWidget is not None, "ChatWidget class not implemented yet"


def test_chat_widget_uses_qtextbrowser(qtbot):
    """Verify that ChatWidget uses QTextBrowser internally and has an input field."""
    widget = ChatWidget()
    qtbot.addWidget(widget)

    assert hasattr(widget, "chat_display"), "ChatWidget must have chat_display"
    assert hasattr(widget, "chat_input"), "ChatWidget must have chat_input"

    from PySide6.QtWidgets import QLineEdit, QTextBrowser

    assert isinstance(widget.chat_display, QTextBrowser)
    assert isinstance(widget.chat_input, QLineEdit)


def test_native_markdown_rendering(qtbot):
    """Assert that passing standard markdown updates the QTextBrowser document structure correctly."""
    widget = ChatWidget()
    qtbot.addWidget(widget)

    markdown_text = "# Header 1\n* Item 1\n* Item 2\n\n**Bold Text**"
    widget.append_message("assistant", markdown_text)

    html = widget.chat_display.toHtml()
    assert "Header 1" in html or "h1" in html.lower()
    assert "Item 1" in html
    assert "Bold Text" in html or "strong" in html.lower() or "b" in html.lower()


def test_html_safety_handling(qtbot):
    """Assert that unsafe HTML structures are ignored or safely stripped."""
    widget = ChatWidget()
    qtbot.addWidget(widget)

    unsafe_markdown = (
        "Hello <script>alert('hack');</script> <iframe src='http://evil.com'></iframe> World"
    )
    widget.append_message("assistant", unsafe_markdown)

    html = widget.chat_display.toHtml()
    assert "script" not in html.lower()
    assert "iframe" not in html.lower()
    assert "evil.com" not in html.lower()


def test_chat_widget_appends_text(qtbot):
    """Assert that appending user/assistant messages updates display."""
    widget = ChatWidget()
    qtbot.addWidget(widget)

    widget.append_message("user", "Hello agent")
    assert "Hello agent" in widget.chat_display.toPlainText()

    widget.append_message("assistant", "Hello user")
    assert "Hello user" in widget.chat_display.toPlainText()


def test_markdown_stream_throttling(qtbot):
    """Assert that chunk emissions append plain text and do not invoke full markdown rerenders."""
    widget = ChatWidget()
    qtbot.addWidget(widget)

    # Start a stream
    widget.start_stream()

    # Emit token chunks
    widget.append_stream_chunk("def ")
    widget.append_stream_chunk("foo():")

    # During stream, it should be appended as plain text in the display
    plain_text = widget.chat_display.toPlainText()
    assert "def foo():" in plain_text

    # End the stream with definitive text
    widget.finalize_stream("```python\ndef foo():\n    pass\n```")

    # Verify final markdown is set
    html = widget.chat_display.toHtml()
    assert "def" in html
    assert "foo" in html


def test_pygments_syntax_highlighting(qtbot):
    """Assert that final markdown code blocks are styled using Pygments (inline styles)."""
    widget = ChatWidget()
    qtbot.addWidget(widget)

    code_markdown = "Here is Python code:\n```python\nimport sys\nprint(sys.version)\n```"
    widget.start_stream()
    widget.finalize_stream(code_markdown)

    html = widget.chat_display.toHtml()
    # Check for pygments output signature: inline styling on tokens
    assert "style=" in html
    assert "color:" in html
    # Keywords like 'import' should be styled
    assert "import" in html


def test_unknown_language_does_not_eat_code_line(qtbot):
    """Verify that using an unknown language code block tag (like gnu) does not eat/include the tag line as code."""
    from grc_agent_gui.chat_widget import markdown_to_highlighted_html

    markdown_text = "Here is some code:\n```gnu\nprint('hi')\n```"
    html_result = markdown_to_highlighted_html(markdown_text)

    # The output should contain print('hi')
    assert "print('hi')" in html_result or "print(&#39;hi&#39;)" in html_result

    # The output should NOT contain the literal "gnu" as text inside the code block
    assert "gnu" not in html_result


def test_markdown_to_highlighted_html_strips_serif_font_family(qtbot):
    """Regression: ``QTextDocument.toHtml()`` emits ``<body style=" font-family:; ...">``
    with an empty font-family declaration. Qt's HTML renderer falls back to
    its default serif font for the body, which used to make the agent's
    markdown body render in Times while headers / user messages / tool
    output stayed sans-serif. The markdown helper must strip font-family
    so the body inherits from the document default font set via
    ``setDefaultFont`` in :func:`MainWindow.apply_zoom`.
    """
    from grc_agent_gui.chat_widget import markdown_to_highlighted_html

    out = markdown_to_highlighted_html("Hello *world* — **bold** text.")

    # The function should extract the body content (paragraphs / spans),
    # NOT the entire <html> wrapper with Qt's inline font-family on <body>.
    assert "<html>" not in out, (
        "markdown_to_highlighted_html should extract body content, not "
        f"leak the full <html> wrapper:\n{out}"
    )
    assert "</html>" not in out
    # Critically: no inline font-family anywhere — body must inherit.
    assert "font-family" not in out.lower(), (
        f"markdown_to_highlighted_html leaked an inline font-family "
        f"declaration that breaks font inheritance:\n{out}"
    )
    # And the actual rendered text is still there.
    assert "Hello" in out
    assert "world" in out


def test_assistant_message_uses_unified_font(qtbot):
    """Regression: assistant message body (markdown-rendered) must inherit
    the document font — no inline font-family declarations on the body
    or its descendants. Mirrors ``test_markdown_to_highlighted_html_strips_serif_font_family``
    at the message-render layer.
    """
    widget = ChatWidget()
    qtbot.addWidget(widget)
    widget.append_message("assistant", "Hello *world* — **bold** text.")
    rendered = widget._history[0]["_rendered"]
    assert rendered is not None
    assert "font-family" not in rendered.lower()


def test_tool_name_color_parity_between_call_and_result(qtbot):
    """The tool NAME (e.g. ``inspect_graph``) must use the same color in
    both ``call inspect_graph ({})`` and ``result inspect_graph`` rows —
    only the role label (``call``/``result``) and the row chrome differ.
    Regression: the result row used to color the name in a one-off rust
    hex (``#6a5040``) while the call row used a different teal.
    """
    from grc_agent_gui.chat_widget import _COLOR_TOOL_NAME

    widget = ChatWidget()
    qtbot.addWidget(widget)
    widget.append_message(
        "tool_started", "", payload={"tool_name": "inspect_graph", "args": "{}"}
    )
    widget.append_message(
        "tool_finished", "{}", payload={"tool_name": "inspect_graph", "expanded": False}
    )

    call_rendered = widget._history[0]["_rendered"]
    result_rendered = widget._history[1]["_rendered"]

    expected_color = f"color: {_COLOR_TOOL_NAME};"
    assert expected_color in call_rendered, (
        f"call row missing unified tool name color {expected_color}:\n{call_rendered}"
    )
    assert expected_color in result_rendered, (
        f"result row missing unified tool name color {expected_color}:\n{result_rendered}"
    )
    # The legacy one-off rust color must be gone.
    assert "color: #6a5040;" not in result_rendered, (
        f"result row still uses legacy rust color:\n{result_rendered}"
    )
    assert "color: #6a8a96;" not in call_rendered, (
        f"call row still uses legacy teal color:\n{call_rendered}"
    )


def test_user_message_background_is_visually_distinct(qtbot):
    """User message row must use a background color that stands out from
    the main chat background so the user can scan the conversation flow
    at a glance. Regression: the previous ``_COLOR_USER_BG`` was nearly
    identical to the main ``#1e1e2e`` QWidget background.
    """
    from grc_agent_gui.chat_widget import _COLOR_USER_BG

    main_bg = "#1e1e2e"
    assert _COLOR_USER_BG.lower() != main_bg.lower(), (
        "_COLOR_USER_BG must differ from the main QWidget background"
    )

    widget = ChatWidget()
    qtbot.addWidget(widget)
    widget.append_message("user", "Hello")
    rendered = widget._history[0]["_rendered"]
    assert f"background-color: {_COLOR_USER_BG}" in rendered, (
        f"user row missing its distinct background:\n{rendered}"
    )


def test_thinking_persistence_for_thinking_capable_models(qtbot):
    """End-to-end demo of the thinking flow as it would render for a
    thinking-capable model (e.g. qwen3, deepseek-r1). The current
    default model (gemma4:e4b-it-qat-120k) is non-reasoning so no
    `` tags ever reach the chat — this test pins the UI behavior
    on the surface so any regression is caught by CI rather than by
    the user trying it with a thinking model manually.
    """
    widget = ChatWidget()
    qtbot.addWidget(widget)

    # Simulate what a thinking model streams: a final assistant message
    # whose text contains both the <think>…</think> reasoning block and
    # the visible response. Round-level streaming can attach multiple
    # <think> segments — they should all collapse into one summary row.
    thinking_a = "Plan: inspect the graph first"
    thinking_b = "Now I have enough to answer"
    widget.append_message(
        "assistant",
        f"<think>{thinking_a}</think>"
        f"<think>{thinking_b}</think>"
        f"The current graph is a simple audio playback system.",
    )

    rendered = widget._history[0]["_rendered"]
    # The header line is always visible.
    assert "thinking" in rendered.lower()
    # The combined char count is shown.
    expected_count = len(thinking_a) + 1 + len(thinking_b) + 1
    assert f"{expected_count} chars" in rendered
    # The visible response survived the regex strip.
    assert "audio playback system" in rendered
    # The reasoning text is hidden in the default (collapsed) state.
    assert thinking_a not in rendered
    assert thinking_b not in rendered

    # Toggle open — the user clicks ▼ show thinking.
    from PySide6.QtCore import QUrl

    widget._on_anchor_clicked(QUrl("toggle-thinking:0"))
    expanded = widget._history[0]["_rendered"]
    assert thinking_a in expanded
    assert thinking_b in expanded
    # Header still persists.
    assert f"{expected_count} chars" in expanded


def test_streaming_does_not_prepend_agent_bold(qtbot):
    """2.4: start_stream() must NOT inject the bold 'Agent:' prefix into the live
    QTextBrowser. The prefix is added by the final _render_chat() call only.
    """
    widget = ChatWidget()
    qtbot.addWidget(widget)

    widget.start_stream()
    # During streaming, there should be no bold "Agent:" prefix in the
    # QTextBrowser HTML yet — the prefix comes from the final render.
    html = widget.chat_display.toHtml().lower()
    assert "<b>agent:</b>" not in html

    widget.finalize_stream("hello world")
    # After finalization, the rendered body must include the bold prefix.
    # QTextBrowser may normalize whitespace and may collapse nested <p>s,
    # so we search for the "Agent:" text fragment rather than the exact
    # tag form.
    html = widget.chat_display.toHtml()
    plain = widget.chat_display.toPlainText()
    assert "Agent:" in html
    assert "Agent:" in plain
    assert "hello world" in plain


def test_memoized_rendering_preserves_history(qtbot):
    """2.2: the memoized per-message rendered HTML must be reused on re-render."""
    widget = ChatWidget()
    qtbot.addWidget(widget)

    widget.append_message("user", "Hello")
    msg = widget._history[0]
    first_render = msg.get("_rendered")
    assert first_render is not None
    assert "Hello" in first_render

    # Call _render_chat() a second time without mutating the message.
    widget._render_chat()
    # The memoized HTML is the SAME object (cached, not re-parsed).
    assert widget._history[0].get("_rendered") is first_render


def test_stream_chunk_invalidates_memo(qtbot):
    """2.2: streaming chunks must invalidate the memoized render for the streaming entry."""
    widget = ChatWidget()
    qtbot.addWidget(widget)

    widget.start_stream()
    widget.append_stream_chunk("hi")
    # The streaming entry's _rendered must be None (invalidated).
    assert widget._history[-1].get("_rendered") is None


def test_sanitizer_strips_event_handlers(qtbot):
    """2.3: the hardened sanitizer must strip on*-event attributes."""
    from grc_agent_gui.chat_widget import sanitize_html

    unsafe = '<a href="x" onclick="alert(1)" onerror="bad()">link</a>'
    safe = sanitize_html(unsafe).lower()
    assert "onclick" not in safe
    assert "onerror" not in safe


def test_sanitizer_strips_dangerous_tags(qtbot):
    """2.3: the hardened sanitizer must strip <svg>, <object>, <style>, etc."""
    from grc_agent_gui.chat_widget import sanitize_html

    unsafe = "<p>hi</p><svg><script>bad()</script></svg><object data='x'></object>"
    safe = sanitize_html(unsafe).lower()
    assert "<svg" not in safe
    assert "<script" not in safe
    assert "<object" not in safe
    assert "bad()" not in safe


def test_sanitizer_strips_javascript_uri(qtbot):
    """2.3: the hardened sanitizer must strip javascript:/vbscript: URIs."""
    from grc_agent_gui.chat_widget import sanitize_html

    unsafe = '<a href="javascript:alert(1)">x</a><a href="vbscript:msgbox(1)">y</a>'
    safe = sanitize_html(unsafe).lower()
    assert "javascript:" not in safe
    assert "vbscript:" not in safe


def test_get_history_returns_independent_copy(qtbot):
    """get_history() must return a copy, so callers can mutate without affecting the widget."""
    widget = ChatWidget()
    qtbot.addWidget(widget)

    widget.append_message("user", "hello")
    widget.append_message("assistant", "world")

    history = widget.get_history()
    history.clear()
    assert len(widget.get_history()) == 2


def test_export_markdown_renders_user_and_assistant_messages(qtbot):
    """export_markdown() must produce a Markdown doc with both roles."""
    widget = ChatWidget()
    qtbot.addWidget(widget)

    widget.append_message("user", "Summarize this graph.")
    widget.append_message("assistant", "The graph has 3 blocks.")

    md = widget.export_markdown()
    assert "# GRC Agent chat export" in md
    assert "## You" in md
    assert "Summarize this graph." in md
    assert "## Agent" in md
    assert "The graph has 3 blocks." in md


def test_export_markdown_empty_chat_is_minimal(qtbot):
    """An empty chat must still produce a well-formed (but empty) Markdown doc."""
    widget = ChatWidget()
    qtbot.addWidget(widget)

    md = widget.export_markdown()
    assert md.startswith("# GRC Agent chat export")
    assert md.rstrip().endswith("GRC Agent chat export")


def test_block_nesting_prevention(qtbot):
    """Verify that appending status blocks, errors, mutations, and stream chunks does not nest them in previous HTML blocks."""
    widget = ChatWidget()
    qtbot.addWidget(widget)

    widget.append_message("user", "Hello")
    widget.append_status("my_tool", "my_args")
    widget.append_mutation("ok")
    widget.append_error("some error")

    # Check that they exist in the display before finalization
    plain_text_before = widget.chat_display.toPlainText()
    lines_before = [line.strip() for line in plain_text_before.split("\n") if line.strip()]

    assert "You:" in lines_before
    assert "Hello" in lines_before
    assert any("call my_tool" in line for line in lines_before)
    assert any("graph updated" in line.lower() for line in lines_before)
    assert any("some error" in line.lower() for line in lines_before)

    # Stream some chunks
    widget.start_stream()
    widget.append_stream_chunk("response text")

    plain_text_during = widget.chat_display.toPlainText()
    lines_during = [line.strip() for line in plain_text_during.split("\n") if line.strip()]
    assert "Agent:" in lines_during
    assert "response text" in lines_during

    # Finalize
    widget.finalize_stream("response text")

    plain_text_after = widget.chat_display.toPlainText()
    lines_after = [line.strip() for line in plain_text_after.split("\n") if line.strip()]
    assert "You:" in lines_after
    assert "Hello" in lines_after
    assert "Agent:" in lines_after
    assert "response text" in lines_after


def test_drop_last_assistant_removes_empty_placeholder(qtbot):
    """When a turn ends with an empty assistant text (the model
    only issued tool calls), the chat widget should drop the
    streaming placeholder so the user does not see an empty
    "Agent:" bubble.
    """
    widget = ChatWidget()
    qtbot.addWidget(widget)

    widget.append_message("user", "Inspect the graph.")
    widget.start_stream()
    # The model never produced text; the streaming placeholder is
    # an empty assistant row.
    assert widget._history[-1]["role"] == "assistant"
    assert widget._history[-1]["text"] == ""

    widget.drop_last_assistant()

    # The empty assistant row is gone.
    roles = [row["role"] for row in widget._history]
    assert "assistant" not in roles
    # The user row is still there.
    assert "user" in roles

    plain = widget.chat_display.toPlainText()
    assert "Inspect the graph." in plain
    # No "Agent:" header since the only assistant row was dropped.
    assert "Agent:" not in plain

def test_finalize_stream_keeps_final_text_when_present(qtbot):
    """Sanity: ``finalize_stream`` is the path used when the model
    *did* produce text. Empty-text case is covered above.
    """
    widget = ChatWidget()
    qtbot.addWidget(widget)

    widget.append_message("user", "hi")
    widget.start_stream()
    widget.append_stream_chunk("partial")
    widget.finalize_stream("final answer")

    plain = widget.chat_display.toPlainText()
    assert "final answer" in plain


def test_tool_call_preserves_temporal_order(qtbot):
    """Regression: when a tool call happens mid-stream, the pre-tool
    assistant text must close its own bubble BEFORE the tool rows are
    appended. Without this fix, ``append_stream_chunk`` keeps appending
    post-tool reply text to the pre-tool assistant bubble (it searches
    reversed history for the last assistant msg, which sits before the
    tool rows), producing [agent reply] [tool call] instead of the
    correct [pre-tool text] [tool call] [tool result] [post-tool reply].
    """
    widget = ChatWidget()
    qtbot.addWidget(widget)

    # --- Simulate the multi-round agent flow ---

    # 1. User sends a message
    widget.append_message("user", "What is this graph?")

    # 2. Model starts streaming (pre-tool text or thinking)
    widget.start_stream()
    widget.append_stream_chunk("<think>Let me inspect first.</think>")
    widget.append_stream_chunk("Let me check the graph.")

    # 3. Tool call starts — this is where the fix applies.
    #    The MainWindow handler finalizes/drops the stream before
    #    appending the tool status. We simulate that here.
    if widget._streaming:
        final = widget.current_stream_text()
        import re

        visible = re.sub(r"<think>.*?</think>", "", final, flags=re.DOTALL).strip()
        if visible:
            widget.finalize_stream(final)
        else:
            widget.drop_last_assistant()

    widget.append_status("inspect_graph", "{}")

    # 4. Tool finishes
    widget.append_tool_finished("inspect_graph", '{"ok": true}')

    # 5. Model streams its final reply (post-tool)
    widget.start_stream()
    widget.append_stream_chunk("This is a signal source.")
    widget.finalize_stream("This is a signal source.")

    # --- Verify temporal ordering ---
    roles = [msg["role"] for msg in widget._history]
    # Expected: user, assistant(pre-tool), tool_started, tool_finished, assistant(post-tool)
    assert roles == ["user", "assistant", "tool_started", "tool_finished", "assistant"], (
        f"Temporal order broken: {roles}"
    )

    # The pre-tool assistant text must NOT contain the post-tool reply.
    pre_tool = widget._history[1]["text"]
    assert "Let me check" in pre_tool
    assert "signal source" not in pre_tool

    # The post-tool assistant text must contain the final reply.
    post_tool = widget._history[4]["text"]
    assert "signal source" in post_tool
    assert "Let me check" not in post_tool


def test_tool_finished_expand_collapse(qtbot):
    """Verify that tool_finished outputs are rendered, collapsed by default, and can be toggled via link clicks."""
    widget = ChatWidget()
    qtbot.addWidget(widget)

    # Append tool output
    widget.append_tool_finished("inspect_graph", '{"nodes": [], "edges": []}')

    # Collapsed by default: verify text contains "inspect_graph output" and "expand" but NOT the JSON text
    html_before = widget.chat_display.toHtml()
    assert "inspect_graph" in html_before
    assert "expand" in html_before
    assert "nodes" not in html_before

    # Click the toggle link (by manually triggering the anchor clicked logic)
    # The toggle URL is "toggle:0" because it's the first message in history (index 0)
    from PySide6.QtCore import QUrl
    widget._on_anchor_clicked(QUrl("toggle:0"))

    # Expanded: verify text now contains "collapse" and the JSON text
    html_after = widget.chat_display.toHtml()
    assert "collapse" in html_after
    assert "nodes" in html_after


def test_thinking_expand_collapse(qtbot):
    """Verify that thinking blocks are extracted, collapsed by default, and can be toggled.

    Default (collapsed) state MUST keep a one-line summary header visible
    so reasoning does not "disappear" from the chat after streaming ends.
    """
    widget = ChatWidget()
    qtbot.addWidget(widget)

    # Append assistant message with think tags
    thinking_text = "I am analyzing the graph structure"
    widget.append_message(
        "assistant",
        f"<think>{thinking_text}.</think>Done analyzing.",
    )

    # Collapsed by default: the header line with char count and the
    # "show thinking" toggle must persist in the rendered HTML.
    html_before = widget.chat_display.toHtml()
    assert "thinking" in html_before.lower()
    assert "show thinking" in html_before
    assert "Done analyzing" in html_before
    assert thinking_text not in html_before, (
        "thinking content must be hidden in the collapsed default state"
    )
    # char count is shown so the user can tell at a glance that
    # reasoning was emitted.
    assert f"{len(thinking_text) + 1} chars" in html_before

    # Toggle the thinking block: URL is "toggle-thinking:0" (index 0)
    from PySide6.QtCore import QUrl

    widget._on_anchor_clicked(QUrl("toggle-thinking:0"))

    # Expanded: verify "collapse" and the thinking text exist, and the
    # header (with char count) is still present.
    html_after = widget.chat_display.toHtml()
    assert "collapse" in html_after
    assert thinking_text in html_after
    # The header persists in the expanded state too.
    assert f"{len(thinking_text) + 1} chars" in html_after


def test_main_window_zoom_actions(qtbot, monkeypatch, tmp_path):
    """Verify that zoom in, zoom out, and reset zoom actions modify the zoom factor correctly.

    QSettings caches its file path on first use within a QApplication
    session, so subsequent tests can see whatever value prior tests
    wrote to the persisted settings file. We force a known starting
    point via :func:`zoom_reset` instead of asserting the default.
    """
    from unittest.mock import MagicMock

    from grc_agent_gui.main_window import MainWindow

    db_path = tmp_path / "sessions.db"
    import grc_agent_gui.main_window
    monkeypatch.setattr(grc_agent_gui.main_window, "_default_sessions_db", lambda: db_path)

    mock_agent = MagicMock()
    mock_agent.session = None
    from ToolAgents.data_models.chat_history import ChatHistory
    mock_agent.chat_history = ChatHistory()
    mock_provider = MagicMock()

    window = MainWindow(mock_agent, mock_provider)
    qtbot.addWidget(window)

    # Anchor at a known starting point.
    window.zoom_reset()
    assert window._zoom_factor == 1.0

    # Zoom in: add 0.1.
    window.zoom_in()
    assert abs(window._zoom_factor - 1.1) < 1e-5

    # Zoom out twice: subtract 0.2.
    window.zoom_out()
    window.zoom_out()
    assert abs(window._zoom_factor - 0.9) < 1e-5

    # Zoom reset always snaps to 1.0 regardless of where we are.
    window.zoom_reset()
    assert window._zoom_factor == 1.0


def test_ui_font_metrics_is_single_source_of_truth():
    """ui_font_metrics is the only place body / mono / small / chat sizes
    are defined for a given zoom. ``get_stylesheet`` and the chat document
    default font must both consume it — no per-callsite scaling rules."""
    from grc_agent_gui.styles import get_stylesheet, ui_font_metrics

    for zoom in (0.5, 1.0, 1.5, 2.0, 3.0):
        f = ui_font_metrics(zoom)
        # every metric strictly scales up with zoom — no max() clamps
        # quietly absorbing the difference.
        assert f.body_px == max(12, int(15 * zoom))
        assert f.mono_px == max(11, int(14 * zoom))
        assert f.small_px == max(10, int(12 * zoom))
        # chat_pt should track body_px (point→px is ~0.8).
        assert f.chat_pt == max(9, round(f.body_px * 0.8))

        # The stylesheet must embed exactly the same body_px.
        body_rule = f"font-size: {f.body_px}px;"
        assert body_rule in get_stylesheet(zoom)


def test_chat_html_does_not_contain_legacy_em_overrides():
    """Inline chat HTML should inherit the document font (1em) rather
    than carrying hand-picked em ratios like ``0.85em`` that fight the
    unified font cascade."""
    widget = ChatWidget()

    # Render every role we ship in chat.
    widget.append_message("user", "hi")
    widget.append_message("assistant", "hello back")
    widget.append_message("assistant", "<thinkreasoning here.</think>response")
    widget.append_message("tool_started", "", payload={"tool_name": "x", "args": "y"})
    widget.append_message("tool_finished", "{}", payload={"tool_name": "x", "expanded": False})
    widget.append_message("mutation", "")
    widget.append_message("error", "boom")

    for entry in widget._history:
        rendered = entry.get("_rendered") or ""
        assert "0.85em" not in rendered, f"legacy 0.85em override in {entry['role']}: {rendered}"
        assert "0.9em" not in rendered, f"legacy 0.9em override in {entry['role']}: {rendered}"


def test_user_message_text_color_is_near_white(qtbot):
    """The user message body text must render in a near-white color so
    it stays legible at every zoom level. The previous ``#a0a8b8`` (dim
    blue-gray) was hard to read against the dark background."""
    from grc_agent_gui.chat_widget import _COLOR_USER_TEXT

    widget = ChatWidget()
    qtbot.addWidget(widget)
    widget.append_message("user", "hello there")
    rendered = widget._history[0]["_rendered"]
    assert _COLOR_USER_TEXT in rendered


def test_user_message_html_uses_zoomable_font_size(qtbot):
    """The user message body div must carry an explicit ``font-size`` so
    the text grows with zoom instead of inheriting the document default
    (which the markdown path doesn't apply uniformly)."""
    widget = ChatWidget()
    qtbot.addWidget(widget)
    widget.set_chat_pt(28)  # simulates the value applied by MainWindow.apply_zoom
    widget.append_message("user", "hi")
    rendered = widget._history[0]["_rendered"]
    assert "font-size: 28px" in rendered


def test_agent_and_user_panels_are_visually_distinct():
    """User and agent bubbles must use clearly different background
    colors — the previous near-identical dark tints made scanning
    long chats hard. Single source of truth: the two color tokens
    in chat_widget.py."""
    from grc_agent_gui import chat_widget

    assert chat_widget._COLOR_USER_BG != chat_widget._COLOR_AGENT_BG
    # And neither one is a near-identical dim tint of the other —
    # the hex pairs must be visibly different (delta >= 0x10 per
    # channel on at least one component).
    def _hex(c: str) -> tuple[int, int, int]:
        c = c.lstrip("#")
        return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16))

    ur, ug, ub = _hex(chat_widget._COLOR_USER_BG)
    ar, ag, ab = _hex(chat_widget._COLOR_AGENT_BG)
    channel_diff = max(abs(ur - ar), abs(ug - ag), abs(ub - ab))
    assert channel_diff >= 0x10, (
        f"agent/user bg too similar: user={chat_widget._COLOR_USER_BG} agent={chat_widget._COLOR_AGENT_BG}"
    )


# ── strip_inline_math shim ─────────────────────────────────────────────────
# The chat renderer has no LaTeX engine. The shim turns the common
# inline-math patterns (µ, ², ₙ, ·) into unicode, and falls back to a
# <code> span for anything it cannot safely rewrite.

def test_strip_inline_math_rewrites_simple_greek_and_superscripts():
    from grc_agent_gui.chat_widget import strip_inline_math

    out = strip_inline_math("The bandwidth is $350\\text{--}\\mu\\text{Hz}$.")
    assert "350" in out
    assert "Hz" in out
    # \\mu -> µ, \\text{...} -> contents unwrapped
    assert "µ" in out
    # No raw TeX survives in the simple case.
    assert "\\text{" not in out
    assert "\\mu" not in out


def test_strip_inline_math_rewrites_superscripts_and_subscripts():
    from grc_agent_gui.chat_widget import strip_inline_math

    assert "f²" in strip_inline_math("$f^2$")
    assert "x₁" in strip_inline_math("$x_1$")
    # \cdot → ·, with whatever surrounding whitespace the input had.
    assert "·" in strip_inline_math("$a \\cdot b$")


def test_strip_inline_math_falls_back_to_code_for_unsupported():
    """Anything containing unsupported macros or multi-line math is
    rendered as a `<code>` span so the original is at least visible."""
    from grc_agent_gui.chat_widget import strip_inline_math

    out = strip_inline_math("Result: $\\frac{1}{2}$ and $E = mc^{2}$.")
    # At least one segment is replaced with a code span.
    assert "<code>" in out
    # The code span contains the original LaTeX so the user can still read it.
    assert "\\frac" in out or "mc^{2}" in out


def test_strip_inline_math_leaves_plain_text_alone():
    from grc_agent_gui.chat_widget import strip_inline_math

    # The shim must not touch prose that contains no TeX-flavored math.
    # Note: bare $5 / $currency style substrings ARE ambiguous with
    # math in LaTeX; we only require the shim to leave well-formed
    # prose alone. The dollar sign is rare in our domain prose.
    text = "Plain text without math. The answer is 5 dollars, not 4."
    assert strip_inline_math(text) == text


def test_strip_inline_math_handles_display_math():
    from grc_agent_gui.chat_widget import strip_inline_math

    out = strip_inline_math("Display: $$x = 1$$ end.")
    # Display math at minimum loses the `$$` markers.
    assert "$$" not in out
    assert "x = 1" in out
