# ruff: noqa: E402
"""A single Markdown code block widget.

Replaces the old ``<span face="monospace">`` Pango-label hack (which mangled
indentation with NBSP) with a real ``Gtk.TextView`` whose buffer is tokenized
by Pygments and colored via one ``Gtk.TextTag`` per token style. The raw source
is stored verbatim and copied as-is, so the clipboard never depends on how the
buffer rendered.

No background or theme color is forced — the TextView uses the native GTK
theme background. ``CODE_STYLE`` picks Pygments' token palette; it is a
dark-friendly default because the host desktop is dark, and is a one-line
change.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gdk, GLib, Gtk, Pango

CODE_STYLE = "monokai"


def _parse_rule(rule: str) -> tuple[str | None, bool, bool]:
    """Pygments style rule string -> (color_hex, bold, italic)."""
    color: str | None = None
    bold = italic = False
    if rule:
        for part in rule.split():
            if part == "bold":
                bold = True
            elif part == "nobold":
                bold = False
            elif part == "italic":
                italic = True
            elif part == "noitalic":
                italic = False
            elif part.startswith("#"):
                color = part
    return color, bold, italic


def _lookup_rule(tok, styles: dict) -> str:
    """Walk the Pygments token hierarchy (Token.Keyword.Pseudo -> Keyword ->
    Token) and return the first matching style rule string."""
    t = tok
    while t is not None:
        if t in styles:
            return styles[t] or ""
        t = t.parent
    return ""


class _BufferStyler:
    """Inserts tokenized code into a TextBuffer, applying one cached TextTag per
    distinct Pygments style. Tokens with no style rule get the native fg color."""

    def __init__(self, buffer: Gtk.TextBuffer, styles: dict) -> None:
        self._buffer = buffer
        self._styles = styles
        self._cache: dict[tuple, Gtk.TextTag] = {}

    def emit(self, tok, val: str) -> None:
        if not val:
            return
        offset = self._buffer.get_end_iter().get_offset()
        self._buffer.insert(self._buffer.get_end_iter(), val)
        rule = _lookup_rule(tok, self._styles)
        if not rule.strip():
            return
        tag = self._tag_for(rule)
        start = self._buffer.get_iter_at_offset(offset)
        end = self._buffer.get_end_iter()
        self._buffer.apply_tag(tag, start, end)

    def _tag_for(self, rule: str) -> Gtk.TextTag:
        key = _parse_rule(rule)
        tag = self._cache.get(key)
        if tag is None:
            color, bold, italic = key
            props: dict[str, object] = {}
            if color:
                props["foreground"] = color
            if bold:
                props["weight"] = Pango.Weight.BOLD
            if italic:
                props["style"] = Pango.Style.ITALIC
            tag = self._buffer.create_tag(None, **props)
            self._cache[key] = tag
        return tag


class CodeBlock(Gtk.Box):
    """``[ header: language label | Copy ]`` over a highlighted, scrollable body."""

    def __init__(self, lang: str, code: str, style_name: str = CODE_STYLE) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._code = code
        self.get_style_context().add_class("chat-code-block")
        for prop in ("margin_start", "margin_end", "margin_top", "margin_bottom"):
            getattr(self, "set_" + prop)(4)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        header.get_style_context().add_class("chat-code-header")

        lang_lbl = Gtk.Label(label=lang or "")
        lang_lbl.set_xalign(0.0)
        lang_lbl.get_style_context().add_class("dim-label")

        self._copy_btn = Gtk.Button(label="Copy")
        self._copy_btn.get_style_context().add_class("chat-copy-btn")
        self._copy_btn.set_halign(Gtk.Align.END)
        self._copy_btn.set_valign(Gtk.Align.CENTER)
        self._copy_btn.set_tooltip_text("Copy code to clipboard")
        self._copy_btn.connect("clicked", self._on_copy)

        header.pack_start(lang_lbl, True, True, 4)
        header.pack_end(self._copy_btn, False, False, 4)

        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        sw.set_min_content_height(28)
        sw.set_max_content_height(420)
        sw.set_propagate_natural_height(True)

        tv = Gtk.TextView()
        tv.set_monospace(True)
        tv.set_wrap_mode(Gtk.WrapMode.NONE)
        tv.set_editable(False)
        tv.set_cursor_visible(False)
        tv.get_style_context().add_class("chat-monospace")
        tv.set_left_margin(6)
        tv.set_right_margin(6)
        tv.set_top_margin(6)
        tv.set_bottom_margin(6)
        self._highlight(tv.get_buffer(), lang, code, style_name)

        sw.add(tv)
        self.pack_start(header, False, False, 0)
        self.pack_start(sw, True, True, 0)

    def _on_copy(self, btn: Gtk.Button) -> None:
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        clipboard.set_text(self._code, -1)
        btn.set_label("\u2713 Copied!")

        def _reset() -> bool:
            btn.set_label("Copy")
            return False

        GLib.timeout_add_seconds(2, _reset)

    def _highlight(self, buffer: Gtk.TextBuffer, lang: str, code: str, style_name: str) -> None:
        try:
            from pygments import lex
            from pygments.lexers import get_lexer_by_name, guess_lexer
            from pygments.styles import get_style_by_name
        except Exception:
            buffer.set_text(code)
            return

        try:
            styles = get_style_by_name(style_name).styles
        except Exception:
            styles = {}

        try:
            lexer = get_lexer_by_name(lang, stripnl=False) if lang else guess_lexer(code)
        except Exception:
            buffer.set_text(code)
            return

        styler = _BufferStyler(buffer, styles)
        for tok, val in lex(code, lexer):
            styler.emit(tok, val)
