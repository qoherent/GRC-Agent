import html
import logging
import re
from PySide6.QtGui import QTextDocument, QTextCursor
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLineEdit, QTextBrowser

from pygments import highlight
from pygments.lexers import get_lexer_by_name, guess_lexer
from pygments.lexers.special import TextLexer
from pygments.formatters import HtmlFormatter

logger = logging.getLogger(__name__)


def sanitize_html(html_str: str) -> str:
    """Sanitize HTML by stripping out script and iframe tags for security."""
    html_str = re.sub(r"<script\b[^<]*(?:(?!<\/script>)<[^<]*)*<\/script>", "", html_str, flags=re.IGNORECASE)
    html_str = re.sub(r"<iframe\b[^<]*(?:(?!<\/iframe>)<[^<]*)*<\/iframe>", "", html_str, flags=re.IGNORECASE)
    return html_str


def markdown_to_highlighted_html(markdown_text: str) -> str:
    """Convert markdown text to HTML, using Pygments for inline-styled code blocks."""
    # Split text into code blocks and normal paragraphs
    parts = markdown_text.split("```")
    final_html_parts = []
    
    for idx, part in enumerate(parts):
        if idx % 2 == 0:
            # Even index: standard markdown text.
            if not part:
                continue
            # Render using a temporary QTextDocument's setMarkdown
            doc = QTextDocument()
            doc.setMarkdown(part)
            part_html = doc.toHtml()
            
            # Extract only the body content to keep output layout clean
            body_start = part_html.find("<body>")
            body_end = part_html.find("</body>")
            if body_start != -1 and body_end != -1:
                part_html = part_html[body_start + 6 : body_end]
            final_html_parts.append(part_html)
        else:
            # Odd index: code block.
            # Format: [optional language]\n[code content]
            lines = part.split("\n")
            if not lines:
                continue
                
            lang = lines[0].strip()
            # Standard GRC/Python block languages
            common_langs = {"python", "py", "cpp", "c++", "c", "bash", "sh", "yaml", "yml", "json", "xml", "html"}
            if lang.lower() in common_langs:
                code = "\n".join(lines[1:])
            else:
                lang = "text"
                code = "\n".join(lines[1:])
                
            # Perform syntax highlighting using Pygments
            try:
                lexer = get_lexer_by_name(lang)
            except Exception:
                try:
                    lexer = guess_lexer(code)
                except Exception:
                    lexer = TextLexer()
            
            # Generate inline-styled HTML blocks to preserve text selection highlight in QTextBrowser
            formatter = HtmlFormatter(noclasses=True, cssstyles="font-family: monospace; font-size: 10pt; background-color: #f5f5f5; padding: 5px;")
            highlighted_code = highlight(code, lexer, formatter)
            final_html_parts.append(highlighted_code)
            
    return sanitize_html("".join(final_html_parts))


class ChatWidget(QWidget):
    """Chat UI widget featuring native Markdown and Pygments code highlighting.
    
    Implements a stream throttling strategy to prevent display flicker.
    """
    
    def __init__(self, parent: QWidget = None) -> None:
        super().__init__(parent)
        self._history: list[dict[str, str]] = []  # list of {"role": "user"|"assistant", "text": str}
        self._streaming = False
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        # Chat display area using native QTextBrowser
        self.chat_display = QTextBrowser(self)
        self.chat_display.setOpenExternalLinks(True)
        layout.addWidget(self.chat_display)
        
        # Chat text input field
        self.chat_input = QLineEdit(self)
        self.chat_input.setPlaceholderText("Ask the GRC Agent...")
        layout.addWidget(self.chat_input)

    def append_message(self, role: str, text: str) -> None:
        """Append a standard completed message, parsing it as markdown/HTML."""
        self._history.append({"role": role, "text": text})
        self._render_chat()

    def start_stream(self) -> None:
        """Start a text streaming session, locking updates to plain-text mode."""
        self._streaming = True
        self._history.append({"role": "assistant", "text": ""})
        self.chat_display.append("<b>Agent:</b> ")
        self.chat_display.moveCursor(QTextCursor.MoveOperation.End)

    def append_stream_chunk(self, text: str) -> None:
        """Append raw stream text incrementally to prevent UI flicker."""
        if self._streaming:
            self._history[-1]["text"] += text
            self.chat_display.insertPlainText(text)
            # Ensure the scrollbar follows the text insertion
            self.chat_display.ensureCursorVisible()

    def finalize_stream(self, final_text: str) -> None:
        """Finalize the stream and apply the definitive highlighted markdown HTML."""
        self._streaming = False
        if self._history and self._history[-1]["role"] == "assistant":
            self._history[-1]["text"] = final_text
        self._render_chat()

    def _render_chat(self) -> None:
        """Render all messages in the chat history, applying markdown and code styling."""
        html_contents = []
        for msg in self._history:
            role = msg["role"]
            text = msg["text"]
            
            if role == "user":
                # User messages are formatted cleanly as bold headers
                header = f"<p><b>You:</b> {html.escape(text) if hasattr(html, 'escape') else text}</p>"
                html_contents.append(header)
            else:
                # Assistant messages are fully parsed into rich HTML
                header = "<b>Agent:</b> "
                body = markdown_to_highlighted_html(text)
                html_contents.append(f"<p>{header}{body}</p>")
                
        # Preserve scrollbar position if needed, then update
        scroll_bar = self.chat_display.verticalScrollBar()
        old_val = scroll_bar.value()
        
        # Set raw sanitised HTML
        self.chat_display.setHtml("".join(html_contents))
        
        # Restore scrollbar position or scroll to the bottom
        if self._streaming:
            scroll_bar.setValue(scroll_bar.maximum())
        else:
            scroll_bar.setValue(min(old_val, scroll_bar.maximum()))
