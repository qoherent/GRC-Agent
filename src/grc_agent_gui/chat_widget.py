import html
import json
import logging
import re
from typing import Any

from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name, guess_lexer
from pygments.lexers.special import TextLexer
from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices, QTextBlockFormat, QTextCursor, QTextDocument
from PySide6.QtWidgets import QLineEdit, QTextBrowser, QVBoxLayout, QWidget

logger = logging.getLogger(__name__)


# Layered defense against XSS / event-handler injection. QTextBrowser does
# not execute JavaScript or load remote resources, but we still strip
# dangerous tags and on*-event attributes so that an HTML engine swap
# later (or a paste-to-pdf path) cannot become a vector.
_DANGEROUS_TAGS = (
    "script",
    "iframe",
    "object",
    "embed",
    "style",
    "link",
    "meta",
    "form",
    "input",
    "button",
    "select",
    "textarea",
    "base",
    "frame",
    "frameset",
    "applet",
    "svg",
    "math",
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
            doc.deleteLater()

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
            common_langs = {
                "python",
                "py",
                "cpp",
                "c++",
                "c",
                "bash",
                "sh",
                "yaml",
                "yml",
                "json",
                "xml",
                "html",
            }
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
                style="monokai",
                noclasses=True,
                cssstyles="font-family: monospace; font-size: 10pt; background-color: #181825; color: #cdd6f4; padding: 8px; border-radius: 4px; border: 1px solid #45475a; line-height: 1.4;",
            )
            highlighted_code = highlight(code, lexer, formatter)
            final_html_parts.append(highlighted_code)

    return sanitize_html("".join(final_html_parts))


def render_user_message_html(text: str) -> str:
    """Render a user message as a simple, sanitized bold-header HTML block."""
    safe = html.escape(text).replace("\n", "<br/>")
    return f'<div style="margin-bottom: 12px;"><b style="color: #89b4fa;">You:</b><div style="margin-top: 4px; padding-left: 8px;">{safe}</div></div>'


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
        self._history: list[dict[str, Any]] = []
        self._streaming = False
        self._stream_header_printed = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.chat_display = QTextBrowser(self)
        self.chat_display.setOpenExternalLinks(False)
        self.chat_display.anchorClicked.connect(self._on_anchor_clicked)
        layout.addWidget(self.chat_display)

        self.chat_input = QLineEdit(self)
        self.chat_input.setPlaceholderText("Ask the GRC Agent...")
        self.chat_input.setMinimumHeight(32)
        layout.addWidget(self.chat_input)

    def _on_anchor_clicked(self, url: QUrl) -> None:
        url_str = url.toString()
        if url_str.startswith("toggle:"):
            try:
                idx = int(url_str.removeprefix("toggle:"))
                self._history[idx]["expanded"] = not self._history[idx].get("expanded", False)
                self._history[idx]["_rendered"] = None
                self._render_chat()
            except (ValueError, IndexError):
                pass
        elif url_str.startswith("toggle-thinking:"):
            try:
                idx = int(url_str.removeprefix("toggle-thinking:"))
                self._history[idx]["thinking_expanded"] = not self._history[idx].get("thinking_expanded", False)
                self._history[idx]["_rendered"] = None
                self._render_chat()
            except (ValueError, IndexError):
                pass
        else:
            QDesktopServices.openUrl(url)

    def clear(self) -> None:
        """Clear all messages and reset chat display."""
        self._history.clear()
        self.chat_display.clear()
        self._streaming = False
        self._stream_header_printed = False

    def append_message(self, role: str, text: str, payload: dict | None = None) -> None:
        """Append a standard completed message, parsing it as markdown/HTML."""
        entry = {"role": role, "text": text, "_rendered": None}
        if payload:
            entry.update(payload)
        self._history.append(entry)
        self._render_chat()

    def append_tool_finished(self, name: str, result: str) -> None:
        """Append a completed tool output block."""
        self._history.append(
            {
                "role": "tool_finished",
                "tool_name": name,
                "text": result,
                "expanded": False,
                "_rendered": None,
            }
        )
        self._render_chat()

    def append_status(self, name: str, args: str) -> None:
        """Insert a styled tool-call status block with arguments."""
        self._history.append(
            {"role": "tool_started", "text": f"Tool: {name}\nArgs: {args}", "_rendered": None}
        )
        self._render_chat()

    def append_mutation(self, result: str) -> None:
        """Insert a styled mutation summary line."""
        self._history.append({"role": "mutation", "text": result, "_rendered": None})
        self._render_chat()

    def append_error(self, text: str) -> None:
        """Insert a styled error line."""
        self._history.append({"role": "error", "text": text, "_rendered": None})
        self._render_chat()

    def start_stream(self) -> None:
        """Start a text streaming session, locking updates to plain-text mode."""
        self._streaming = True
        self._stream_header_printed = False
        self._history.append({"role": "assistant", "text": "", "_rendered": None})
        cursor = self.chat_display.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.chat_display.setTextCursor(cursor)

    def append_stream_chunk(self, text: str) -> None:
        """Append raw stream text incrementally to prevent UI flicker."""
        if self._streaming:
            # Find the active assistant message to append the stream chunk to
            assistant_msg = None
            for msg in reversed(self._history):
                if msg["role"] == "assistant":
                    assistant_msg = msg
                    break

            if assistant_msg is not None:
                assistant_msg["text"] += text
                assistant_msg["_rendered"] = None

            cursor = self.chat_display.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)

            if not self._stream_header_printed:
                # Break out of any previous block (like a user message or a tool status block)
                cursor.insertBlock(QTextBlockFormat())

                # Insert the styled "Agent:" header
                cursor.insertHtml(
                    '<div style="margin-top: 12px; margin-bottom: 4px;">'
                    '<b style="color: #a6e3a1;">Agent:</b>'
                    "</div>"
                )

                # Move to the end of the header and start the indented block for streaming
                cursor.movePosition(QTextCursor.MoveOperation.End)
                block_format = QTextBlockFormat()
                block_format.setLeftMargin(8)
                block_format.setTopMargin(4)
                cursor.insertBlock(block_format)

                self._stream_header_printed = True

            self.chat_display.setTextCursor(cursor)
            self.chat_display.insertPlainText(text)
            self.chat_display.ensureCursorVisible()

    def finalize_stream(self, final_text: str) -> None:
        """Finalize the stream and apply the definitive highlighted markdown HTML."""
        self._streaming = False
        self._stream_header_printed = False
        for msg in reversed(self._history):
            if msg["role"] == "assistant":
                msg["text"] = final_text
                msg["_rendered"] = None
                break
        self._render_chat()

    def drop_last_assistant(self) -> None:

        """Remove the most recent ``assistant`` row from the visible log.

        Used when a turn ends with an empty assistant text (the model
        only issued tool calls). The display row would otherwise show
        an empty "Agent:" bubble.
        """
        self._streaming = False
        self._stream_header_printed = False
        if self._history and self._history[-1]["role"] == "assistant":
            self._history.pop()
        self._render_chat()

    def get_history(self) -> list[dict[str, str]]:
        """Return a copy of the in-memory chat history for export."""
        return [dict(entry) for entry in self._history]

    def export_markdown(self) -> str:
        """Render the chat history as a single Markdown document.

        The export is intentionally plain (no Pygments / sanitization layer)
        so the output round-trips cleanly into any Markdown reader.
        """
        lines: list[str] = ["# GRC Agent chat export", ""]
        for entry in self._history:
            role = entry.get("role", "")
            text = entry.get("text", "")
            if role == "user":
                lines.append("## You")
                lines.append("")
                lines.append(text)
                lines.append("")
            elif role == "assistant":
                lines.append("## Agent")
                lines.append("")
                lines.append(text)
                lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    def _render_chat(self) -> None:
        """Render all messages in the chat history, applying markdown and code styling.

        Per-message HTML is memoized on the message dict itself. A
        re-render of the same text content is a cheap join() instead of
        a full markdown / pygments parse pass.
        """
        html_contents: list[str] = []
        for idx, msg in enumerate(self._history):
            role = msg["role"]
            text = msg["text"]
            cached = msg.get("_rendered")
            if cached is not None:
                if cached:
                    html_contents.append(cached)
                continue

            if role == "user":
                cached = render_user_message_html(text)
            elif role == "assistant":
                # Check for thinking block
                match = re.search(r"<think>(.*?)</think>", text, flags=re.DOTALL)
                if match:
                    thinking_content = match.group(1).strip()
                    clean_text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
                else:
                    thinking_content = None
                    clean_text = text

                body = markdown_to_highlighted_html(clean_text)

                thinking_html = ""
                if thinking_content:
                    is_thinking_expanded = msg.get("thinking_expanded", False)
                    safe_thinking = html.escape(thinking_content)
                    if is_thinking_expanded:
                        thinking_toggle = f'<a href="toggle-thinking:{idx}" style="color: #f9e2af; text-decoration: none; font-weight: bold;">▲ collapse</a>'
                        thinking_box = (
                            f'<pre style="margin-top: 4px; padding: 8px; background-color: #181825; '
                            f"color: #f9e2af; font-family: monospace; font-size: 11px; border-radius: 4px; "
                            f'border: 1px solid #f9e2af; overflow-x: auto; white-space: pre-wrap;">{safe_thinking}</pre>'
                        )
                    else:
                        thinking_toggle = f'<a href="toggle-thinking:{idx}" style="color: #f9e2af; text-decoration: none; font-weight: bold;">▼ expand</a>'
                        thinking_box = ""

                    thinking_html = (
                        f'<div style="margin-top: 4px; margin-bottom: 8px; padding: 4px 8px; '
                        f"border-left: 2px solid #f9e2af; background-color: #1e1e2e; "
                        f'font-family: monospace; font-size: 12px; border-radius: 4px;">'
                        f'<span style="color: #f9e2af;">Thinking Process </span>'
                        f"{thinking_toggle}"
                        f"{thinking_box}"
                        f"</div>"
                    )

                cached = (
                    f'<div style="margin-bottom: 12px;">'
                    f'<b style="color: #a6e3a1;">Agent:</b>'
                    f'<div style="margin-top: 4px; padding-left: 8px;">'
                    f"{thinking_html}"
                    f"{body}"
                    f"</div>"
                    f"</div>"
                )
            elif role == "tool_started":
                lines = text.split("\n", 1)
                name = lines[0].removeprefix("Tool: ").strip() if len(lines) > 0 else ""
                args = lines[1].removeprefix("Args: ").strip() if len(lines) > 1 else ""
                safe_name = html.escape(name)
                safe_args = html.escape(args)
                cached = (
                    f'<div style="margin-top: 6px; margin-bottom: 2px; padding: 4px 8px; '
                    f"border-left: 2px solid #89b4fa; background-color: #1e1e2e; "
                    f'font-family: monospace; font-size: 12px; border-radius: 4px;">'
                    f'<span style="color: #89b4fa; font-weight: bold;">⚡ {safe_name}</span>'
                    f' <span style="color: #a6adc8; font-size: 11px;">({safe_args})</span>'
                    f"</div>"
                )
            elif role == "mutation":
                cached = (
                    '<div style="color: #a6e3a1; border-left: 3px solid #a6e3a1; '
                    'padding-left: 8px; margin: 6px 0; font-size: 13px;">&#10003; Graph updated</div>'
                )
            elif role == "error":
                cached = (
                    f'<div style="color: #f38ba8; border-left: 3px solid #f38ba8; '
                    f'padding-left: 8px; margin: 6px 0; font-size: 13px;">&#10007; {html.escape(text[:200])}</div>'
                )
            elif role == "tool_finished":
                tool_name = msg.get("tool_name") or msg.get("name") or "Tool"
                is_expanded = msg.get("expanded", False)
                raw_output = text
                try:
                    parsed = json.loads(raw_output)
                    pretty_output = json.dumps(parsed, indent=2, sort_keys=True)
                except Exception:
                    pretty_output = raw_output

                safe_tool_name = html.escape(tool_name)
                safe_output = html.escape(pretty_output)

                if is_expanded:
                    toggle_link = f'<a href="toggle:{idx}" style="color: #89b4fa; text-decoration: none; font-weight: bold;">▲ collapse</a>'
                    output_block = (
                        f'<pre style="margin-top: 4px; padding: 8px; background-color: #181825; '
                        f"color: #cdd6f4; font-family: monospace; font-size: 11px; border-radius: 4px; "
                        f'border: 1px solid #45475a; overflow-x: auto; white-space: pre-wrap;">{safe_output}</pre>'
                    )
                else:
                    toggle_link = f'<a href="toggle:{idx}" style="color: #89b4fa; text-decoration: none; font-weight: bold;">▼ expand</a>'
                    output_block = ""

                cached = (
                    f'<div style="margin-top: 2px; margin-bottom: 6px; padding: 4px 8px; '
                    f"border-left: 2px solid #a6e3a1; background-color: #1e1e2e; "
                    f'font-family: monospace; font-size: 12px; border-radius: 4px;">'
                    f'<span style="color: #a6adc8;">{safe_tool_name} output </span>'
                    f"{toggle_link}"
                    f"{output_block}"
                    f"</div>"
                )
            else:
                body = markdown_to_highlighted_html(text)
                cached = f'<div style="margin-bottom: 12px;"><b style="color: #a6e3a1;">Agent:</b><div style="margin-top: 4px; padding-left: 8px;">{body}</div></div>'

            msg["_rendered"] = cached
            if cached:
                html_contents.append(cached)

        scroll_bar = self.chat_display.verticalScrollBar()
        old_val = scroll_bar.value()

        self.chat_display.setHtml("".join(html_contents))

        if self._streaming:
            scroll_bar.setValue(scroll_bar.maximum())
        else:
            scroll_bar.setValue(max(scroll_bar.minimum(), min(old_val, scroll_bar.maximum())))
