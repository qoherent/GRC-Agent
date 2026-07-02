import html
import json
import re
from typing import Any

from grc_agent.chat_roles import DISPLAY_ROLES
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name, guess_lexer
from pygments.lexers.special import TextLexer
from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QDesktopServices, QTextBlockFormat, QTextCursor, QTextDocument
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

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
    # Pre-process inline math (LaTeX) to either unicode (common case)
    # or a <code> span (fallback) before handing off to Qt's markdown
    # parser, which has no LaTeX engine of its own. The shim is
    # intentionally narrow — see ``strip_inline_math``.
    markdown_text = strip_inline_math(markdown_text)

    parts = markdown_text.split("```")
    final_html_parts = []

    for idx, part in enumerate(parts):
        if idx % 2 == 0:
            if not part:
                continue
            doc = QTextDocument()
            doc.setMarkdown(part)
            part_html = doc.toHtml()

            # ``QTextDocument.toHtml()`` always emits ``<body style=" font-family:; ...">``
            # with an empty font-family declaration; Qt's HTML renderer falls
            # back to its default (serif) for the body, which made the
            # agent's markdown body render in Times while every other text
            # element stayed sans-serif. Match ``<body>`` with *any* attributes
            # (not just the literal ``<body>``), then strip every font-family
            # declaration so the body inherits from the document default font
            # set via ``setDefaultFont`` in :func:`MainWindow.apply_zoom`.
            body_match = re.search(r"<body[^>]*>(.*?)</body>", part_html, flags=re.DOTALL)
            if body_match:
                part_html = body_match.group(1)
            part_html = re.sub(r"font-family\s*:\s*[^;\"]*;?", "", part_html, flags=re.IGNORECASE)
            final_html_parts.append(part_html)
        else:
            lines = part.split("\n")
            if not lines:
                continue

            lang = lines[0].strip()
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
                cssstyles="font-family: monospace; background-color: #181825; color: #cdd6f4; padding: 8px; border-radius: 4px; border: 1px solid #45475a; line-height: 1.4;",
            )
            highlighted_code = highlight(code, lexer, formatter)
            final_html_parts.append(highlighted_code)

    return sanitize_html("".join(final_html_parts))


# ── Inline-math shim ──────────────────────────────────────────────────────
# QTextBrowser has no LaTeX engine. ``strip_inline_math`` rewrites the
# common patterns the local model emits (greek letters, superscripts,
# subscripts, dots) into unicode so they read as plain text, and falls
# back to a ``<code>`` span for anything it cannot safely handle. The
# function is intentionally narrow — it never tries to be a full TeX
# renderer. Anything ambiguous is preserved verbatim inside the code span
# so the user can read it.
_MATH_FALLBACK = re.compile(r"\$+([^\n]+?)\$+")


def _rewrite_math_segment(body: str) -> str | None:
    """Try to rewrite a single ``$...$`` / ``$$...$$`` body to plain text.

    Returns ``None`` if the body contains anything the shim cannot safely
    rewrite (unsupported macros, multi-line content, mismatched braces,
    etc.) — the caller then falls back to a ``<code>`` span.
    """
    if not body or "\n" in body:
        return None
    s = body

    # Unsupported macros (anything that would need a real TeX engine).
    if re.search(r"\\(frac|sqrt|sum|int|prod|lim|sin|cos|tan|log|ln|exp|alpha|beta|gamma|delta|epsilon|zeta|eta|theta|iota|kappa|lambda|nu|xi|pi|rho|sigma|tau|phi|chi|psi|omega|leq|geq|neq|approx|rightarrow|leftarrow|Rightarrow|Leftarrow|to|infty|partial|nabla|forall|exists|in|notin|subset|supset|cup|cap|emptyset|mathbb|mathrm|mathit|mathbf|mathcal|binom|begin|end)\b", s):
        return None
    # Unbalanced / nested braces are not supported (we only handle
    # \text{...}).
    if s.count("{") != s.count("}"):
        return None
    # Square brackets / pipe / hat in math mode are display-only.
    if re.search(r"[\[\]|<>]", s):
        return None

    # \text{...} -> contents
    def _text_sub(match: re.Match[str]) -> str:
        return match.group(1)

    s = re.sub(r"\\text\{([^{}]*)\}", _text_sub, s)

    # Greek letters (single replacements only; no \foo{bar} patterns
    # left at this point).
    s = s.replace("\\mu", "µ")
    s = s.replace("\\cdot", "·")
    s = s.replace("\\times", "×")
    s = s.replace("\\pm", "±")
    s = s.replace("\\to", "→")
    s = s.replace("\\rightarrow", "→")
    s = s.replace("\\leftarrow", "←")
    s = s.replace("\\infty", "∞")
    s = s.replace("\\approx", "≈")
    s = s.replace("\\neq", "≠")
    s = s.replace("\\leq", "≤")
    s = s.replace("\\geq", "≥")
    s = s.replace("\\deg", "°")

    # Superscripts: only single-char unicode (digit / common letter).
    super_map = {
        "0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴",
        "5": "⁵", "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹",
        "n": "ⁿ", "i": "ⁱ", "T": "ᵀ", "+": "⁺", "-": "⁻",
    }
    s = re.sub(r"\^([0-9A-Za-z+\-])", lambda m: super_map.get(m.group(1), m.group(0)), s)

    # Subscripts.
    sub_map = {
        "0": "₀", "1": "₁", "2": "₂", "3": "₃", "4": "₄",
        "5": "₅", "6": "₆", "7": "₇", "8": "₈", "9": "₉",
        "i": "ᵢ", "j": "ⱼ",
    }
    s = re.sub(r"_([0-9A-Za-z])", lambda m: sub_map.get(m.group(1), m.group(0)), s)

    # LaTeX dashes.
    s = s.replace("---", "—")
    s = s.replace("--", "–")
    s = s.replace("``", "“").replace("''", "”")

    # Stray backslashes that survived the replacements → drop them
    # (they would otherwise render as raw TeX).
    s = s.replace("\\", "")

    # If any backslash-free TeX-ish artifact remains (curly braces, $,
    # a leading backslash we did not recognize), give up.
    if re.search(r"[{}]|\\\w|\$", s):
        return None

    return s


def strip_inline_math(text: str) -> str:
    """Rewrite inline ``$...$`` and display ``$$...$$`` math to plain text.

    Cases the shim can rewrite (``\\mu``, ``\\text{...}``, ``^N``, ``_N``,
    ``\\cdot``, ``\\to``, etc.) are emitted as plain unicode text so the
    QTextBrowser displays them correctly. Anything the shim cannot
    safely rewrite is left in a ``<code>`` span so the original
    notation stays visible to the user.

    Plain prose (no ``$``) is returned unchanged.
    """
    if "$" not in text:
        return text

    def _replace(match: re.Match[str]) -> str:
        delimiters = match.group(0).split(match.group(1))[0]  # "$" or "$$"
        body = match.group(1)
        rewritten = _rewrite_math_segment(body)
        if rewritten is None:
            return f"<code>{html.escape(delimiters + body + delimiters)}</code>"
        return rewritten

    return _MATH_FALLBACK.sub(_replace, text)


def strip_think_blocks(text: str) -> str:
    """Remove all ``<think>...</think>`` blocks from *text* and return the remainder stripped.

    Shared by the chat renderer (``_render_chat``) and the MainWindow
    streaming handler (``on_tool_started``) so the two don't reimplement
    the same regex.
    """
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def extract_thinking_content(text: str) -> str | None:
    """Extract and join all ``<think>...</think>`` blocks from *text*.

    Returns ``None`` if no non-empty thinking block is found.
    """
    think_matches = re.findall(r"<think>(.*?)</think>", text, flags=re.DOTALL)
    if not think_matches:
        return None
    joined = "\n\n".join(m.strip() for m in think_matches if m.strip())
    return joined or None


# ── Accent colors for each message type (dim/muted) ─────────────────────────
# user        #4a5568  dim slate-blue
# agent       #3d5a45  dim sage-green
# tool call   #2e4a52  dim steel-teal
# tool result #5a5080  dim muted indigo
# thinking    #4a4220  dim olive
_COLOR_USER = "#cdd6f4"          # primary text color for the "You:" label
_COLOR_USER_TEXT = "#e8eaf0"     # near-white for the user message body
_COLOR_USER_BG = "#243049"       # distinct blue-tinted panel (lighter for contrast)
_COLOR_USER_BORDER = "#4d6298"   # matching blue border
_COLOR_AGENT = "#a6e3a1"         # sage-green label (success-palette green)
_COLOR_AGENT_BG = "#1b1d22"      # neutral dark grey — distinct from the user blue panel
_COLOR_AGENT_BORDER = "#3a4250"  # neutral border
_COLOR_TOOL_CALL = "#3a6070"     # dim steel-teal label
_COLOR_TOOL_CALL_BG = "#0f1a1e" # near-black w/ teal tint
_COLOR_TOOL_CALL_BORDER = "#1e3540"  # muted teal border
_COLOR_TOOL_NAME = "#7a9cb0"     # teal-leaning name (used in both call + result)
_COLOR_TOOL_RESULT = "#5a5080"   # dim muted indigo label
_COLOR_TOOL_RESULT_BG = "#131018"  # near-black w/ indigo tint
_COLOR_TOOL_RESULT_BORDER = "#2a2440"  # muted indigo border
_COLOR_THINKING = "#6b5f30"      # dim olive-yellow label
_COLOR_THINKING_BG = "#161410"   # near-black w/ olive tint
_COLOR_THINKING_BORDER = "#332e18"   # muted olive border


def render_user_message_html(text: str, *, font_size_px: int | None = None) -> str:
    """Render a user message as a simple, sanitized bold-header HTML block.

    ``font_size_px`` is sourced from the same :func:`ui_font_metrics` the
    chat-display QFont uses, so the user-message body tracks zoom
    uniformly instead of inheriting the markdown path's document default.
    """
    safe = html.escape(text).replace("\n", "<br/>")
    body_style = (
        f"margin-top: 4px; padding-left: 4px; color: {_COLOR_USER_TEXT};"
    )
    if font_size_px is not None and font_size_px > 0:
        body_style += f" font-size: {font_size_px}px;"
    return (
        f'<div style="margin-bottom: 12px; padding: 6px 8px; border-left: 2px solid {_COLOR_USER_BORDER}; background-color: {_COLOR_USER_BG}; border-radius: 3px;">'
        f'<b style="color: {_COLOR_USER};">You:</b>'
        f'<div style="{body_style}">{safe}</div>'
        f'</div>'
    )


class ChatWidget(QWidget):
    """Chat UI widget featuring native Markdown and Pygments code highlighting.

    Implements:
    - Memoized per-message rendered HTML (2.2) so that re-rendering the
      history does not re-parse unchanged messages.
    - Flicker-free streaming via insertPlainText() during the stream and
      a single setHtml() on turn completion.
    """
    stop_clicked = Signal()

    def __init__(self, parent: QWidget = None) -> None:
        super().__init__(parent)
        self._history: list[dict[str, Any]] = []
        self._streaming = False
        self._stream_header_printed = False
        # chat_pt from ui_font_metrics(zoom_factor). Set by
        # MainWindow.apply_zoom so the user-message body HTML tracks
        # the chat-display default font size. None means "use the
        # document default" (matches the pre-zoom-history behavior).
        self._chat_pt: int | None = None
        # user_text_px from ui_font_metrics — the user-message body
        # size, intentionally larger than chat_pt (the agent's
        # markdown body inherits the document default of chat_pt).
        self._user_text_px: int | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.chat_display = QTextBrowser(self)
        self.chat_display.setOpenExternalLinks(False)
        self.chat_display.anchorClicked.connect(self._on_anchor_clicked)
        layout.addWidget(self.chat_display)

        input_row = QHBoxLayout()
        input_row.setContentsMargins(0, 0, 0, 0)

        self.chat_input = QLineEdit(self)
        self.chat_input.setPlaceholderText("Ask the GRC Agent...")
        self.chat_input.setMinimumHeight(32)
        input_row.addWidget(self.chat_input)

        self.stop_btn = QPushButton("■ Stop", self)
        self.stop_btn.setToolTip("Stop the agent's current response")
        self.stop_btn.setMinimumHeight(32)
        self.stop_btn.setVisible(False)
        self.stop_btn.clicked.connect(self.stop_clicked.emit)
        input_row.addWidget(self.stop_btn)

        layout.addLayout(input_row)

    def set_generating(self, is_generating: bool) -> None:
        """Show/enable the Stop button while a turn is in flight."""
        self.stop_btn.setVisible(is_generating)
        self.stop_btn.setEnabled(is_generating)

    def set_chat_pt(self, chat_pt: int, user_text_px: int | None = None) -> None:
        """Update the chat-point size used for user-message body HTML.

        Called from :func:`MainWindow.apply_zoom` so the user-message
        bubble grows / shrinks with the chat display's default font
        rather than inheriting a different (smaller) cascade.

        ``user_text_px`` is the user-message body size. It defaults to
        ``chat_pt`` for backwards compatibility; the MainWindow passes
        the dedicated ``ui_font_metrics(...).user_text_px`` so the
        user text is consistently 1.3x larger than the agent body.
        """
        if chat_pt <= 0:
            return
        new_user_text = user_text_px if (user_text_px and user_text_px > 0) else chat_pt
        if chat_pt == self._chat_pt and new_user_text == self._user_text_px:
            return
        self._chat_pt = chat_pt
        self._user_text_px = new_user_text
        for msg in self._history:
            if msg.get("role") == "user":
                msg["_rendered"] = None
        self._render_chat()

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
        if role not in DISPLAY_ROLES:
            raise ValueError(
                f"unknown display role: {role!r}; expected one of {sorted(DISPLAY_ROLES)}"
            )
        if role == "tool_finished":
            try:
                parsed = json.loads(text)
                text = json.dumps(parsed, indent=2, sort_keys=True)
            except Exception:
                pass
        entry = {"role": role, "text": text, "_rendered": None}
        if payload:
            entry.update(payload)
        self._history.append(entry)
        self._render_chat()

    def append_tool_finished(self, name: str, result: str) -> None:
        """Merge a completed tool output into the most-recent ``tool_started``.

        Each tool call renders as one line in the chat (call args +
        result, with the result expandable). We avoid the historical
        two-row layout ("call X" / "result X") because it doubles
        every tool row in the chat without adding information.
        """
        # Pretty-print JSON results so the (collapsed) preview is
        # readable when the user expands the row.
        pretty = result
        if result:
            try:
                parsed = json.loads(result)
                pretty = json.dumps(parsed, indent=2, sort_keys=True)
            except Exception:
                pass
        # Find the most recent tool_started entry with a matching
        # tool_name and no result yet. Walk back over any assistant /
        # thinking entries that may sit between (the agent's pre-tool
        # text bubble, if any).
        for entry in reversed(self._history):
            role = entry.get("role")
            if role == "tool_started" and entry.get("tool_name") == name and not entry.get(
                "result"
            ):
                entry["result"] = pretty
                entry["_rendered"] = None
                self._render_chat()
                return
            if role == "tool_finished":
                # Past a finished tool — stop searching; the new
                # tool call belongs to a different turn.
                break
        # Fallback: no matching tool_started found. Append a
        # standalone tool_finished row so the result is still visible.
        self.append_message(
            "tool_finished",
            pretty,
            payload={"tool_name": name, "expanded": False},
        )

    def append_status(self, name: str, args: str) -> None:
        """Insert a styled tool-call status block with arguments."""
        self.append_message(
            "tool_started",
            "",
            payload={"tool_name": name, "args": args},
        )

    def append_mutation(self, result: str) -> None:
        """Insert a styled mutation summary line."""
        self.append_message("mutation", result)

    def append_error(self, text: str) -> None:
        """Insert a styled error line."""
        self.append_message("error", text)

    def append_info(self, text: str) -> None:
        """Insert a plain informational line (e.g. graph loaded)."""
        self.append_message("info", text)

    def start_stream(self) -> None:
        """Start a text streaming session, locking updates to plain-text mode.

        If a previous turn's empty assistant placeholder is still in
        the history (typically because the turn began with a tool
        call), reuse it so the agent bubble stays contiguous across
        ``Agent: (empty) → tool call → result → Agent: text``.
        """
        self._streaming = True
        self._stream_header_printed = False
        # Look back for an empty assistant entry we can reuse. We
        # must skip past intervening tool_started/tool_finished
        # entries (the tool call that consumed the placeholder).
        for entry in reversed(self._history):
            role = entry.get("role")
            if role in ("tool_started", "tool_finished"):
                continue
            if role == "assistant" and not entry.get("text", "").strip():
                entry["_rendered"] = None
                break
            # Anything else (a user message, a non-empty assistant)
            # means a new turn.
            self._history.append({"role": "assistant", "text": "", "_rendered": None})
            break
        else:
            # Empty history — first turn.
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
                    f'<div style="margin-top: 12px; margin-bottom: 4px; border-left: 2px solid {_COLOR_AGENT_BORDER}; padding-left: 6px;">'
                    f'<b style="color: {_COLOR_AGENT};">Agent:</b>'
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

    def current_stream_text(self) -> str:
        """Return the text accumulated so far in the most recent assistant row."""
        for msg in reversed(self._history):
            if msg["role"] == "assistant":
                return msg.get("text", "")
        return ""

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
            text = msg.get("text", "")
            cached = msg.get("_rendered")
            if cached is not None:
                if cached:
                    html_contents.append(cached)
                continue

            if role == "user":
                cached = render_user_message_html(text, font_size_px=self._user_text_px)
            elif role == "assistant":
                # Check for thinking block(s). Round-level streaming can
                # attach a separate <think> segment per tool-calling round,
                # so all of them are combined rather than just the first.
                thinking_content = extract_thinking_content(text)
                clean_text = strip_think_blocks(text) if thinking_content else text

                body = markdown_to_highlighted_html(clean_text)

                thinking_html = ""
                if thinking_content:
                    is_thinking_expanded = msg.get("thinking_expanded", False)
                    safe_thinking = html.escape(thinking_content)
                    # Default (collapsed) always renders a one-line
                    # summary so reasoning never "disappears" from
                    # the chat after streaming. The label uses the
                    # tool-call color, capital T, and "expand" /
                    # "collapse" — no char count.
                    toggle_label = "▲ collapse" if is_thinking_expanded else "▼ expand"
                    thinking_toggle = (
                        f'<a href="toggle-thinking:{idx}" '
                        f'style="color: {_COLOR_TOOL_CALL}; text-decoration: none; margin-left: 8px;">'
                        f"{toggle_label}</a>"
                    )
                    thinking_box = ""
                    if is_thinking_expanded:
                        thinking_box = (
                            f'<pre style="margin-top: 6px; padding: 8px; background-color: {_COLOR_THINKING_BG}; '
                            f'color: #8a7c50; font-family: monospace; border-radius: 3px; '
                            f'border: 1px solid {_COLOR_THINKING_BORDER}; overflow-x: auto; white-space: pre-wrap;">{safe_thinking}</pre>'
                        )

                    thinking_html = (
                        f'<div style="margin-top: 4px; margin-bottom: 8px; padding: 4px 8px; '
                        f'border-left: 2px solid {_COLOR_TOOL_CALL_BORDER}; background-color: {_COLOR_TOOL_CALL_BG}; '
                        f'font-family: monospace; border-radius: 3px;">'
                        f'<span style="color: {_COLOR_TOOL_CALL};">Thinking</span>'
                        f"{thinking_toggle}"
                        f"{thinking_box}"
                        f"</div>"
                    )

                cached = (
                    f'<div style="margin-bottom: 10px; padding: 6px 8px; border-left: 2px solid {_COLOR_AGENT_BORDER}; background-color: {_COLOR_AGENT_BG}; border-radius: 3px;">'
                    f'<b style="color: {_COLOR_AGENT};">Agent:</b>'
                    f'<div style="margin-top: 4px; padding-left: 4px; color: #9aabb0;">'
                    f"{thinking_html}"
                    f"{body}"
                    f"</div>"
                    f"</div>"
                )
            elif role == "tool_started":
                name = str(msg.get("tool_name", ""))
                args = str(msg.get("args", ""))
                result = msg.get("result")
                safe_name = html.escape(name)
                safe_args = html.escape(args)
                # Single row per tool call: call + (optional) result.
                # Same color for both halves (no teal/indigo split).
                if result is None:
                    # Still running.
                    cached = (
                        f'<div style="margin-top: 4px; margin-bottom: 4px; padding: 4px 8px; '
                        f'border-left: 2px solid {_COLOR_TOOL_CALL_BORDER}; background-color: {_COLOR_TOOL_CALL_BG}; '
                        f'font-family: monospace; border-radius: 3px;">'
                        f'<span style="color: {_COLOR_TOOL_CALL};">call </span>'
                        f'<span style="color: {_COLOR_TOOL_NAME};">{safe_name}</span>'
                        f' <span style="color: #485a60;">({safe_args})</span>'
                        f' <span style="color: #485a60;">…</span>'
                        f"</div>"
                    )
                else:
                    is_expanded = msg.get("expanded", False)
                    safe_output = html.escape(result)
                    if is_expanded:
                        toggle_link = (
                            f'<a href="toggle:{idx}" '
                            f'style="color: {_COLOR_TOOL_CALL}; text-decoration: none; margin-left: 8px;">▲ collapse</a>'
                        )
                        output_block = (
                            f'<pre style="margin-top: 6px; padding: 8px; background-color: #100e0c; '
                            f'color: #7a7060; font-family: monospace; border-radius: 3px; '
                            f'border: 1px solid {_COLOR_TOOL_CALL_BORDER}; overflow-x: auto; white-space: pre-wrap;">{safe_output}</pre>'
                        )
                    else:
                        toggle_link = (
                            f'<a href="toggle:{idx}" '
                            f'style="color: {_COLOR_TOOL_CALL}; text-decoration: none; margin-left: 8px;">▼ expand</a>'
                        )
                        output_block = ""

                    cached = (
                        f'<div style="margin-top: 4px; margin-bottom: 4px; padding: 4px 8px; '
                        f'border-left: 2px solid {_COLOR_TOOL_CALL_BORDER}; background-color: {_COLOR_TOOL_CALL_BG}; '
                        f'font-family: monospace; border-radius: 3px;">'
                        f'<span style="color: {_COLOR_TOOL_CALL};">call </span>'
                        f'<span style="color: {_COLOR_TOOL_NAME};">{safe_name}</span>'
                        f' <span style="color: #485a60;">({safe_args})</span>'
                        f' <span style="color: {_COLOR_TOOL_CALL};">→ result</span>'
                        f"{toggle_link}"
                        f"{output_block}"
                        f"</div>"
                    )
            elif role == "mutation":
                cached = (
                    '<div style="color: #3d5a45; border-left: 2px solid #1e3028; '
                    'background-color: #0e120f; padding: 4px 8px; margin: 4px 0;  border-radius: 3px;">&#10003; graph updated</div>'
                )
            elif role == "error":
                cached = (
                    f'<div style="color: #7a3535; border-left: 2px solid #3a1a1a; '
                    f'background-color: #130e0e; padding: 4px 8px; margin: 4px 0;  border-radius: 3px;">&#10007; {html.escape(text[:200])}</div>'
                )
            elif role == "info":
                cached = (
                    f'<div style="color: #a6adc8; border-left: 2px solid #313244; '
                    f'background-color: #181825; padding: 4px 8px; margin: 4px 0; '
                    f'border-radius: 3px;">&#8505; {html.escape(text)}</div>'
                )
            elif role == "tool_finished":
                tool_name = msg.get("tool_name") or "Tool"
                is_expanded = msg.get("expanded", False)
                pretty_output = text

                safe_tool_name = html.escape(tool_name)
                safe_output = html.escape(pretty_output)

                if is_expanded:
                    toggle_link = f'<a href="toggle:{idx}" style="color: {_COLOR_TOOL_RESULT}; text-decoration: none;">▲ collapse</a>'
                    output_block = (
                        f'<pre style="margin-top: 4px; padding: 8px; background-color: #100e0c; '
                        f'color: #7a7060; font-family: monospace;  border-radius: 3px; '
                        f'border: 1px solid {_COLOR_TOOL_RESULT_BORDER}; overflow-x: auto; white-space: pre-wrap;">{safe_output}</pre>'
                    )
                else:
                    toggle_link = f'<a href="toggle:{idx}" style="color: {_COLOR_TOOL_RESULT}; text-decoration: none;">▼ expand</a>'
                    output_block = ""

                cached = (
                    f'<div style="margin-top: 2px; margin-bottom: 4px; padding: 4px 8px; '
                    f'border-left: 2px solid {_COLOR_TOOL_RESULT_BORDER}; background-color: {_COLOR_TOOL_RESULT_BG}; '
                    f'font-family: monospace;  border-radius: 3px;">'
                    f'<span style="color: {_COLOR_TOOL_RESULT};">result </span>'
                    f'<span style="color: {_COLOR_TOOL_NAME};">{safe_tool_name} </span>'
                    f"{toggle_link}"
                    f"{output_block}"
                    f"</div>"
                )

            msg["_rendered"] = cached
            if cached:
                html_contents.append(cached)

        scroll_bar = self.chat_display.verticalScrollBar()
        old_val = scroll_bar.value()
        # Stick to the bottom on every re-render (new message, tool call,
        # streamed chunk, ...) as long as the user was already at/near the
        # bottom. If they scrolled up to read history, leave them there
        # instead of yanking the view down on every update.
        was_at_bottom = old_val >= scroll_bar.maximum() - 4

        self.chat_display.setHtml("".join(html_contents))

        if self._streaming or was_at_bottom:
            scroll_bar.setValue(scroll_bar.maximum())
        else:
            scroll_bar.setValue(old_val)
