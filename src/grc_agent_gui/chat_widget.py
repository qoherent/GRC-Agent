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

# Allow-list of LaTeX macros we can safely rewrite to unicode. Anything
# not in this list fails closed (becomes a <code> span in strip_inline_math).
# Deny-lists leak (we never know what new macros the model might emit);
# allow-lists fail closed — every new macro defaults to "show as code".
_MATH_ALLOW_LIST = frozenset({
    "text", "mu", "cdot", "times", "pm", "to", "rightarrow",
    "leftarrow", "infty", "approx", "neq", "leq", "geq", "deg",
})
# Single-character super/subscript allow-lists.
_SUPER_MAP = {
    "0": "\u2070", "1": "\u00b9", "2": "\u00b2", "3": "\u00b3", "4": "\u2074",
    "5": "\u2075", "6": "\u2076", "7": "\u2077", "8": "\u2078", "9": "\u2079",
    "n": "\u207f", "i": "\u2071", "T": "\u1d40", "+": "\u207a", "-": "\u207b",
}
_SUB_MAP = {
    "0": "\u2080", "1": "\u2081", "2": "\u2082", "3": "\u2083", "4": "\u2084",
    "5": "\u2085", "6": "\u2086", "7": "\u2087", "8": "\u2088", "9": "\u2089",
    "i": "\u1d62", "j": "\u2c7c",
}


def _rewrite_math_segment(body: str) -> str | None:
    """Try to rewrite a single ``$...$`` / ``$$...$$`` body to plain text.

    Returns ``None`` if the body contains anything the shim cannot safely
    rewrite (unsupported macros, multi-line content, mismatched braces,
    etc.) — the caller then falls back to a ``<code>`` span.

    Policy is allow-list-based: every ``\\name`` token must appear in
    ``_MATH_ALLOW_LIST``. Anything else → refuse to render.
    """
    if not body or "\n" in body:
        return None
    s = body
    # Single uniform rule: any macro outside _MATH_ALLOW_LIST blocks the rewrite.
    for match in re.finditer(r"\\([A-Za-z]+)", s):
        if match.group(1) not in _MATH_ALLOW_LIST:
            return None
    # Unbalanced / nested braces are not supported (we only handle \text{...}).
    if s.count("{") != s.count("}"):
        return None
    # Square brackets / pipe / hat in math mode are display-only.
    if re.search(r"[\[\]|<>]", s):
        return None

    # \text{...} -> contents
    s = re.sub(r"\\text\{([^{}]*)\}", lambda m: m.group(1), s)

    # Greek letters + symbols (allow-list only — see _MATH_ALLOW_LIST).
    for name, char in (
        ("text", None),  # handled above
        ("mu", "\u00b5"), ("cdot", "\u00b7"), ("times", "\u00d7"), ("pm", "\u00b1"),
        ("to", "\u2192"), ("rightarrow", "\u2192"), ("leftarrow", "\u2190"),
        ("infty", "\u221e"), ("approx", "\u2248"), ("neq", "\u2260"),
        ("leq", "\u2264"), ("geq", "\u2265"), ("deg", "\u00b0"),
    ):
        if char is not None:
            s = s.replace("\\" + name, char)

    # Superscript / subscript — reject any char not in the map.
    s = re.sub(r"\^([0-9A-Za-z+\-])",
               lambda m: _SUPER_MAP.get(m.group(1), "\x00"), s)
    if "\x00" in s:
        return None
    s = re.sub(r"_([0-9A-Za-z])",
               lambda m: _SUB_MAP.get(m.group(1), "\x00"), s)
    if "\x00" in s:
        return None

    # LaTeX dashes + quotes.
    s = s.replace("---", "\u2014").replace("--", "\u2013")
    s = s.replace("``", "\u201c").replace("''", "\u201d")

    # Stray backslashes that survived the replacements → drop them.
    s = s.replace("\\", "")

    # If any backslash-free TeX-ish artifact remains, give up.
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


_THINK_BLOCK_RE = re.compile(r"<think>(.*?)</think>", flags=re.DOTALL)


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
    think_matches = _THINK_BLOCK_RE.findall(text)
    if not think_matches:
        return None
    joined = "\n\n".join(m.strip() for m in think_matches if m.strip())
    return joined or None


# ── Accent colors for each message type (dim/muted) ─────────────────────────
# Color palette for the chat. Each role gets a distinct accent so
# the user can scan long conversations at a glance:
#   user      dim blue panel + near-white text
#   agent     neutral dark grey panel + sage label
#   tool call dim teal row (call + result share one row)
#   thinking  olive panel (lives inside the agent bubble)
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
_COLOR_THINKING = "#6b5f30"      # dim olive-yellow label
_COLOR_THINKING_BG = "#161410"   # near-black w/ olive tint
_COLOR_THINKING_BORDER = "#332e18"   # muted olive border

# Inline style constants shared by the small status rows. Each row
# has a left accent bar (border-left), a tinted background, a
# subtle border-radius, and identical monospace font. The status
# rows (mutation, error, info) use the same panel layout so the
# chat reads as a single column.
_INFO_PANEL = (
    "margin: 4px 0; padding: 4px 8px; border-left: 2px solid #313244; "
    "background-color: #181825; border-radius: 3px; color: #a6adc8;"
)
_MUTATION_PANEL = (
    "color: #3d5a45; border-left: 2px solid #1e3028; background-color: #0e120f; "
    "padding: 4px 8px; margin: 4px 0; border-radius: 3px;"
)
_ERROR_PANEL = (
    "color: #7a3535; border-left: 2px solid #3a1a1a; background-color: #130e0e; "
    "padding: 4px 8px; margin: 4px 0; border-radius: 3px;"
)
# Style helpers for the monospace rows (tool call, thinking).
# Both share the same panel shape (left accent bar, monospace
# font, tinted background) and the same expanded-body pre
# (dark pre, monospace, scrollable).
_MONO_PANEL = (
    "margin-top: 4px; margin-bottom: 4px; padding: 4px 8px; "
    "border-left: 2px solid {border}; background-color: {bg}; "
    "font-family: monospace; border-radius: 3px;"
)
_BODY_PRE = (
    "margin-top: 6px; padding: 8px; background-color: #100e0c; "
    "color: #7a7060; font-family: monospace; border-radius: 3px; "
    "border: 1px solid {border}; overflow-x: auto; white-space: pre-wrap;"
)


def _mono_panel(*, border: str, bg: str) -> str:
    return _MONO_PANEL.format(border=border, bg=bg)


def _body_pre(*, border: str) -> str:
    return _BODY_PRE.format(border=border)


def _render_mutation_html() -> str:
    """Render the static "graph updated" status row."""
    return f'<div style="{_MUTATION_PANEL}">&#10003; graph updated</div>'


def _render_error_html(text: str) -> str:
    """Render an inline error row. ``text`` is truncated to 200 chars."""
    return f'<div style="{_ERROR_PANEL}">&#10007; {html.escape(text[:200])}</div>'


def _render_info_html(text: str) -> str:
    """Render an inline informational row (e.g. "Loaded graph: …")."""
    return f'<div style="{_INFO_PANEL}">&#8505; {html.escape(text)}</div>'


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
        if url_str.startswith("toggle-tool:"):
            # Tool result expand/collapse on a fragment inside an
            # assistant turn. URL format: assistant_idx:tool_idx.
            try:
                payload = url_str.removeprefix("toggle-tool:")
                a_idx, t_idx = (int(x) for x in payload.split(":", 1))
                entry = self._history[a_idx]
                fragments = entry.get("fragments", [])
                if 0 <= t_idx < len(fragments):
                    fragments[t_idx]["expanded"] = not fragments[t_idx].get("expanded", False)
                    entry["_rendered"] = None
                    self._render_chat()
            except (ValueError, IndexError):
                pass
        elif url_str.startswith("toggle-thinking:"):
            # Thinking-block expand/collapse on an assistant turn.
            try:
                idx = int(url_str.removeprefix("toggle-thinking:"))
                self._history[idx]["thinking_expanded"] = not self._history[idx].get(
                    "thinking_expanded", False
                )
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
        """Append a standard completed message, parsing it as markdown/HTML.

        Assistant messages are stored in the fragment model: a
        single text fragment in the ``fragments`` list. The
        ``fragments`` list is the single source of truth for
        assistant text — no parallel ``text`` field is kept
        (the legacy flat field is gone).
        """
        if role not in DISPLAY_ROLES:
            raise ValueError(
                f"unknown display role: {role!r}; expected one of {sorted(DISPLAY_ROLES)}"
            )
        if role == "assistant":
            entry = {
                "role": role,
                "fragments": [{"type": "text", "text": text}],
                "_rendered": None,
                "thinking_expanded": False,
            }
        else:
            entry = {"role": role, "text": text, "_rendered": None}
        if payload:
            entry.update(payload)
        self._history.append(entry)
        self._render_chat()

    def append_tool_finished(self, name: str, result: str) -> None:
        """Merge a completed tool output into the matching tool
        fragment of the active agent turn.

        Each agent turn is one assistant entry whose ``fragments``
        list is walked in temporal order when the row is rendered.
        A tool call sits in the fragment stream at the exact point
        the model issued it — not at the bottom of the turn — so
        the chat shows the pre-tool text, the tool row, and any
        post-tool text in the order they happened.
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
        # Walk back to the active assistant turn and look for the
        # latest unfilled tool fragment with a matching name.
        for entry in reversed(self._history):
            if entry.get("role") != "assistant":
                continue
            for frag in reversed(entry.get("fragments", [])):
                if (
                    frag.get("type") == "tool"
                    and frag.get("name") == name
                    and frag.get("result") is None
                ):
                    frag["result"] = pretty
                    entry["_rendered"] = None
                    self._render_chat()
                    return
            break
        # No matching fragment — the caller invoked a tool result
        # without a preceding call. Surface it as an error so the
        # user (and the test suite) notices; the silent fallback
        # paths from the old data model are gone.
        self.append_error(f"tool result without prior call: {name}")

    def append_status(self, name: str, args: str) -> None:
        """Add a tool call to the active agent turn.

        Closes the current text fragment and appends a tool fragment
        at the exact point in the turn where the model issued the
        call. The tool row renders inline with the surrounding text
        fragments (pre-tool text above, post-tool text below).
        """
        self._streaming = False
        self._stream_header_printed = False
        assistant = self._current_assistant_entry()
        # If the caller hasn't opened a turn yet, create one. This
        # is the only legal way to start an agent turn (no legacy
        # "standalone tool row" fallback path).
        if assistant is None:
            assistant = {
                "role": "assistant",
                "fragments": [],
                "text": "",
                "_rendered": None,
                "thinking_expanded": False,
            }
            self._history.append(assistant)
        fragments = assistant.setdefault("fragments", [])
        fragments.append(
            {"type": "tool", "name": name, "args": args, "result": None, "expanded": False}
        )
        assistant["_rendered"] = None
        self._render_chat()

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

        Each agent turn is one assistant entry whose ``fragments``
        list is walked in temporal order. A turn is "open" between
        the first response chunk and the next tool call, turn end,
        or new user message. If the most recent history entry is
        the same agent turn (no user/tool boundary in between),
        reuse it so text chunks append to the current text fragment.
        Otherwise start a new turn.
        """
        self._streaming = True
        self._stream_header_printed = False
        # Reuse the most recent assistant entry if it is part of the
        # current turn. The boundary condition is a user message,
        # a fresh assistant entry, or no history at all.
        if not (self._history and self._history[-1].get("role") == "assistant"):
            self._history.append(
                {
                    "role": "assistant",
                    "fragments": [],
                    "text": "",
                    "_rendered": None,
                    "thinking_expanded": False,
                }
            )
        cursor = self.chat_display.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.chat_display.setTextCursor(cursor)

    def _current_assistant_entry(self) -> dict[str, Any] | None:
        """Return the active assistant turn (most recent entry), or None."""
        if self._history and self._history[-1].get("role") == "assistant":
            return self._history[-1]
        return None

    def append_stream_chunk(self, text: str) -> None:
        """Append raw stream text incrementally to prevent UI flicker.

        Text is appended to the current text fragment of the active
        assistant turn. If the last fragment is not a text fragment
        (e.g. a tool row), a new text fragment is opened so the
        post-tool text sits in the right position in the turn.
        """
        if not self._streaming:
            self.start_stream()
        assistant = self._current_assistant_entry()
        if assistant is None:
            return

        # Update the fragment model first, so any re-render that
        # fires from another handler sees the latest text. The
        # fragment list is the single source of truth — the flat
        # ``text`` field that earlier drafts maintained is gone.
        fragments = assistant.setdefault("fragments", [])
        if not fragments or fragments[-1].get("type") != "text":
            fragments.append({"type": "text", "text": text})
        else:
            fragments[-1]["text"] += text
        assistant["_rendered"] = None

        cursor = self.chat_display.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)

        if not self._stream_header_printed:
            # Break out of any previous block (user msg, tool row).
            cursor.insertBlock(QTextBlockFormat())
            cursor.insertHtml(
                f'<div style="margin-top: 12px; margin-bottom: 4px; border-left: 2px solid {_COLOR_AGENT_BORDER}; padding-left: 6px;">'
                f'<b style="color: {_COLOR_AGENT};">Agent:</b>'
                "</div>"
            )
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
        """Finalize the stream and apply the definitive highlighted markdown HTML.

        The final text replaces the current text fragment (it is the
        authoritative post-stream text for the chunk that just
        finished). The full turn re-renders from the fragment
        model.
        """
        self._streaming = False
        self._stream_header_printed = False
        for msg in reversed(self._history):
            if msg["role"] == "assistant":
                fragments = msg.get("fragments", [])
                if fragments and fragments[-1].get("type") == "text":
                    fragments[-1]["text"] = final_text
                else:
                    fragments.append({"type": "text", "text": final_text})
                msg["_rendered"] = None
                break
        self._render_chat()

    def current_stream_text(self) -> str:
        """Return the text of the in-flight text fragment.

        The in-flight fragment is the LAST text fragment of the
        active assistant turn — the one currently being typed
        into. Earlier text fragments (pre-tool text, etc.) are
        NOT included: ``finalize_stream`` only updates the last
        text fragment, so returning the joined whole-turn text
        would cause the pre-tool text to be duplicated in the
        last fragment on finalize.
        """
        assistant = self._current_assistant_entry()
        if assistant is None:
            return ""
        for frag in reversed(assistant.get("fragments", [])):
            if frag.get("type") == "text":
                return frag.get("text", "")
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
            if role == "user":
                lines.append("## You")
                lines.append("")
                lines.append(entry.get("text", ""))
                lines.append("")
            elif role == "assistant":
                # Walk fragments so the export preserves the temporal
                # order of text and tool calls.
                lines.append("## Agent")
                lines.append("")
                for frag in entry.get("fragments", []):
                    if frag.get("type") == "text":
                        lines.append(frag.get("text", ""))
                        lines.append("")
                    elif frag.get("type") == "tool":
                        lines.append(
                            f"```\n{frag.get('name', '')}({frag.get('args', '')})\n```"
                        )
                        if frag.get("result") is not None:
                            lines.append("")
                            lines.append("Result:")
                            lines.append("")
                            lines.append(f"```\n{frag['result']}\n```")
                            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    def _render_assistant_entry(self, msg: dict[str, Any], idx: int) -> str:
        """Render one assistant turn by walking its fragment list once.

        The order of fragments is the temporal order in which the
        model emitted them — text chunks, tool calls, and
        reasoning blocks all live in the same list. This is what
        fixes the previous bug where the tool call was rendered
        at the bottom of the turn even when the model issued it
        in the middle of its response.

        Each text fragment is rendered through
        :func:`markdown_to_highlighted_html` (with the
        ``strip_inline_math`` shim applied upstream). The
        ``<think>…</think>`` content is stripped from each text
        fragment and accumulated into a single Thinking panel at
        the top of the agent bubble. Multiple rounds' thinking
        is concatenated because round-level streaming can attach
        a separate <think> segment per tool-calling round.
        """
        fragments = msg.get("fragments", []) or []
        body_parts: list[str] = []
        thinking_text = ""

        for f_idx, frag in enumerate(fragments):
            ftype = frag.get("type")
            if ftype == "text":
                text = frag.get("text", "")
                extracted = extract_thinking_content(text)
                if extracted:
                    thinking_text += extracted + "\n\n"
                visible = strip_think_blocks(text) if extracted else text
                if visible:
                    body_parts.append(markdown_to_highlighted_html(visible))
            elif ftype == "tool":
                body_parts.append(self._render_tool_fragment(frag, idx, f_idx))

        thinking_text = thinking_text.strip()
        thinking_html = (
            self._render_thinking_panel(
                thinking_text,
                assistant_idx=idx,
                expanded=msg.get("thinking_expanded", False),
            )
            if thinking_text
            else ""
        )

        return self._wrap_agent_bubble(thinking_html + "".join(body_parts))

    def _render_thinking_panel(
        self, thinking_text: str, *, assistant_idx: int, expanded: bool
    ) -> str:
        """Render the collapsible Thinking panel.

        No char count, capital T, "▼ expand" / "▲ collapse" toggle,
        tool-call color. The thinking body is only emitted when
        expanded. ``assistant_idx`` is the history-row index of
        the parent assistant entry (used in the toggle URL).
        """
        if not thinking_text:
            return ""
        toggle_label = "▲ collapse" if expanded else "▼ expand"
        body_html = ""
        if expanded:
            body_html = (
                f'<pre style="{_body_pre(border=_COLOR_THINKING_BORDER)}; color: #8a7c50;">'
                f"{html.escape(thinking_text)}</pre>"
            )
        panel = _mono_panel(border=_COLOR_TOOL_CALL_BORDER, bg=_COLOR_TOOL_CALL_BG)
        return (
            f'<div style="margin-bottom: 8px; {panel}">'
            f'<span style="color: {_COLOR_TOOL_CALL};">Thinking</span>'
            f'<a href="toggle-thinking:{assistant_idx}" '
            f'style="color: {_COLOR_TOOL_CALL}; text-decoration: none; margin-left: 8px;">'
            f"{toggle_label}</a>"
            f"{body_html}"
            f"</div>"
        )

    def _render_tool_fragment(self, frag: dict[str, Any], assistant_idx: int, tool_idx: int) -> str:
        """Render a single tool call fragment as ``call name (args) → result ▼ expand``.

        The toggle URL is ``toggle-tool:assistant_idx:tool_idx`` so
        multiple tools in one turn each get their own expand state.
        """
        name = html.escape(str(frag.get("name", "")))
        args = html.escape(str(frag.get("args", "")))
        result = frag.get("result")
        panel = _mono_panel(border=_COLOR_TOOL_CALL_BORDER, bg=_COLOR_TOOL_CALL_BG)
        if result is None:
            return (
                f'<div style="{panel}">'
                f'<span style="color: {_COLOR_TOOL_CALL};">call </span>'
                f'<span style="color: {_COLOR_TOOL_NAME};">{name}</span>'
                f' <span style="color: #485a60;">({args})</span>'
                f' <span style="color: #485a60;">…</span>'
                f"</div>"
            )
        is_expanded = frag.get("expanded", False)
        safe_output = html.escape(result)
        toggle = (
            f'<a href="toggle-tool:{assistant_idx}:{tool_idx}" '
            f'style="color: {_COLOR_TOOL_CALL}; text-decoration: none; margin-left: 8px;">'
            f"{'▲ collapse' if is_expanded else '▼ expand'}"
            f"</a>"
        )
        body = (
            f'<pre style="{_body_pre(border=_COLOR_TOOL_CALL_BORDER)}">{safe_output}</pre>'
            if is_expanded
            else ""
        )
        return (
            f'<div style="{panel}">'
            f'<span style="color: {_COLOR_TOOL_CALL};">call </span>'
            f'<span style="color: {_COLOR_TOOL_NAME};">{name}</span>'
            f' <span style="color: #485a60;">({args})</span>'
            f' <span style="color: {_COLOR_TOOL_CALL};">→ result</span>'
            f"{toggle}"
            f"{body}"
            f"</div>"
        )

    def _wrap_agent_bubble(self, body_html: str) -> str:
        """Wrap a rendered agent body in the standard Agent: bubble.

        Body text uses the primary text color (#cdd6f4) so the
        final-rendered chat matches the streaming phase (which
        inherits the same QTextBrowser default). The previous
        #9aabb0 (dim teal) was readable but dulled the chat
        relative to the streaming text.
        """
        return (
            f'<div style="margin-bottom: 10px; padding: 6px 8px; border-left: 2px solid {_COLOR_AGENT_BORDER}; background-color: {_COLOR_AGENT_BG}; border-radius: 3px;">'
            f'<b style="color: {_COLOR_AGENT};">Agent:</b>'
            f'<div style="margin-top: 4px; padding-left: 4px; color: #cdd6f4;">'
            f"{body_html}"
            f"</div>"
            f"</div>"
        )

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
                cached = self._render_assistant_entry(msg, idx)
            elif role == "mutation":
                cached = _render_mutation_html()
            elif role == "error":
                cached = _render_error_html(text)
            elif role == "info":
                cached = _render_info_html(text)
            else:
                # Unknown / legacy role. The data model no longer
                # creates tool_started or tool_finished rows. If
                # somehow one slipped in (e.g. an older session that
                # has not been re-saved), the safest behavior is to
                # drop it from the visible chat rather than render
                # it in a half-supported legacy format.
                continue

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
