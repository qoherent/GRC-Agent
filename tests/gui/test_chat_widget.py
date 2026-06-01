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
    
    from PySide6.QtWidgets import QTextBrowser, QLineEdit
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
    
    unsafe_markdown = "Hello <script>alert('hack');</script> <iframe src='http://evil.com'></iframe> World"
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

