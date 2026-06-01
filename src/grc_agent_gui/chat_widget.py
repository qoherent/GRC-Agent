import html
import logging
import re
from PySide6.QtGui import QTextDocument
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLineEdit, QTextBrowser

from pygments import highlight
from pygments.lexers import get_lexer_by_name, guess_lexer
from pygments.lexers.special import TextLexer
from pygments.formatters import HtmlFormatter

logger = logging.getLogger(__name__)


# Layered defense against XSS / event-handler injection. QTextBrowser does
# not execute JavaScript or load remote resources, but we still strip
# dangerous tags and on*-event attributes so that an HTML engine swap
# later (or a paste-to-pdf path) cannot become a vector.
_DANGEROUS_TAGS = (
    "script", "iframe", "object", "embed", "style", "link", "meta",
    "form", "input", "button", "select", "textarea", "base", "frame",
    "frameset", "applet", "svg", "math",
)
_DANGEROUS_TAG_RE = re.compile(
    r"<\s*(?P<tag>" + "|".join(_DANGEROUS_TAGS) + r")\b[^>]*?(/?)>",
    flags=re.IGNORECASE,
)
_DANGEROUS_TAG_PAIR_RE = re.compile(
    r"<\s*(?P<tag>" + "|".join(_DANGEROUS_TAGS) + r")\b[^>]*?>.*?<\s*/\s*(?P=tag)\s*>",
    flags=re.IGNORECASE | re.DOTALL,
)
_EVENT_ATTR_RE = re.compile(
    r"""\s+on[a-zA-Z]+\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)""",
    flags=re.IGNORECASE,
)
_JAVASCRIPT_URI_RE = re.compile(
    r"""(?:javascript|vbscript|livescript|mocha|data\s*:\s*text/html)\s*:""",
    flags=re.IGNORECASE,
)


def sanitize_html(html_str: str) -> str:
    """Strip dangerous HTML constructs before they reach QTextBrowser.

    The sanitization is intentionally strict because QTextBrowser is
    not a security boundary on its own; the cost of a missed event
    handler is much higher than the cost of a stripped tag.
    """
    # Strip pair-wise dangerous tags first (covers <script>...</script>).
    html_str = _DANGEROUS_TAG_PAIR_RE.sub("", html_str)
    # Strip self-closing dangerous tags.
    html_str = _DANGEROUS_TAG_RE.sub("", html_str)
    # Strip on*-event attributes (onclick, onerror, onload, ...).
    html_str = _EVENT_ATTR_RE.sub("", html_str)
    # Strip dangerous URI schemes (javascript:, vbscript:, ...).
    html_str = _JAVASCRIPT_URI_RE.sub("", html_str)
    return html_str


def markdown_to_highlighted_html(markdown_text: str) -> str:
    """Convert markdown text to HTML, using Pygments for inline-styled code blocks."""
    parts = markdown_text.split("```")
    final_html_parts = []

    for idx, part in enumerate(parts):
        if idx % 2 == 0:
            if not part:
                continue
            doc = QTextDocument()
            doc.setMarkdown(part)
            part_html = doc.toHtml()

            body_start = part_html.find("<body>")
            body_end = part_html.find("</body>")
            if body_start != -1 and body_end != -1:
                part_html = part_html[body_start + 6 : body_end]
            final_html_parts.append(part_html)
        else:
            lines = part.split("\n")
            if not lines:
                continue

            lang = lines[0].strip()
            common_langs = {"python", "py", "cpp", "c++", "c", "bash", "sh", "yaml", "yml", "json", "xml", "html"}
            if lang.lower() in common_langs:
                code = "\n".join(lines[1:])
            else:
                lang = "text"
                code = "\n".join(lines[1:])

            try:
                lexer = get_lexer_by_name(lang)
            except Exception:
                try:
                    lexer = guess_lexer(code)
                except Exception:
                    lexer = TextLexer()

            formatter = HtmlFormatter(
                noclasses=True,
                cssstyles="font-family: monospace; font-size: 10pt; background-color: #f5f5f5; padding: 5px;",
            )
            highlighted_code = highlight(code, lexer, formatter)
            final_html_parts.append(highlighted_code)

    return sanitize_html("".join(final_html_parts))


def render_user_message_html(text: str) -> str:
    """Render a user message as a simple, sanitized bold-header HTML block."""
    safe = html.escape(text)
    return f"<p><b>You:</b> {safe}</p>"


class ChatWidget(QWidget):
    """Chat UI widget featuring native Markdown and Pygments code highlighting.

    Implements:
    - Memoized per-message rendered HTML (2.2) so that re-rendering the
      history does not re-parse unchanged messages.
    - Flicker-free streaming via insertPlainText() during the stream and
      a single setHtml() on turn completion.
    """

    def __init__(self, parent: QWidget = None) -> None:
        super().__init__(parent)
        # Each entry: {"role": str, "text": str, "_rendered": str | None}
        self._history: list[dict[str, str]] = []
        self._streaming = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.chat_display = QTextBrowser(self)
        self.chat_display.setOpenExternalLinks(True)
        layout.addWidget(self.chat_display)

        self.chat_input = QLineEdit(self)
        self.chat_input.setPlaceholderText("Ask the GRC Agent...")
        layout.addWidget(self.chat_input)

    def append_message(self, role: str, text: str) -> None:
        """Append a standard completed message, parsing it as markdown/HTML."""
        self._history.append({"role": role, "text": text, "_rendered": None})
        self._render_chat()

    def start_stream(self) -> None:
        """Start a text streaming session, locking updates to plain-text mode.

        The bold "Agent:" prefix is no longer injected here; it is added
        exclusively by ``_render_chat`` on the final render pass. This
        eliminates the duplicate-prefix artifact visible during the
        transition from stream to finalized render (audit 2.4).
        """
        self._streaming = True
        self._history.append({"role": "assistant", "text": "", "_rendered": None})
        # No chat_display.append("<b>Agent:</b> ") here on purpose. The
        # streaming chunks are appended via insertPlainText; the prefix
        # is added by the final _render_chat() call.

    def append_stream_chunk(self, text: str) -> None:
        """Append raw stream text incrementally to prevent UI flicker."""
        if self._streaming:
            self._history[-1]["text"] += text
            # Invalidate the memoized render for the streaming entry;
            # the final render will rebuild it.
            self._history[-1]["_rendered"] = None
            self.chat_display.insertPlainText(text)
            self.chat_display.ensureCursorVisible()

    def finalize_stream(self, final_text: str) -> None:
        """Finalize the stream and apply the definitive highlighted markdown HTML."""
        self._streaming = False
        if self._history and self._history[-1]["role"] == "assistant":
            self._history[-1]["text"] = final_text
            self._history[-1]["_rendered"] = None
        self._render_chat()

    def _render_chat(self) -> None:
        """Render all messages in the chat history, applying markdown and code styling.

        Per-message HTML is memoized on the message dict itself. A
        re-render of the same text content is a cheap join() instead of
        a full markdown / pygments parse pass.
        """
        html_contents: list[str] = []
        for msg in self._history:
            role = msg["role"]
            text = msg["text"]
            cached = msg.get("_rendered")
            if role == "user":
                if cached is None:
                    cached = render_user_message_html(text)
                    msg["_rendered"] = cached
                html_contents.append(cached)
            else:
                if cached is None:
                    header = "<b>Agent:</b> "
                    body = markdown_to_highlighted_html(text)
                    cached = f"<p>{header}{body}</p>"
                    msg["_rendered"] = cached
                html_contents.append(cached)

        scroll_bar = self.chat_display.verticalScrollBar()
        old_val = scroll_bar.value()

        self.chat_display.setHtml("".join(html_contents))

        if self._streaming:
            scroll_bar.setValue(scroll_bar.maximum())
        else:
            # Explicit clamp to be safe even though QScrollBar.setValue
            # is internally clamped.
            scroll_bar.setValue(max(scroll_bar.minimum(), min(old_val, scroll_bar.maximum())))
