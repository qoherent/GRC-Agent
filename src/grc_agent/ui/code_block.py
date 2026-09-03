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

from gi.repository import GLib, Gtk, Pango

from .copy_confirm import confirm_copy
from .css import get_code_style

# Height cap for the code body: below it the full content shows, above it an
# exactly-420px viewport with a vertical scrollbar. Shared by the
# min/max-content-height setup and the size-request pin so the two can never
# drift apart.
_HEIGHT_CAP = 420


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

    def __init__(self, lang: str, code: str) -> None:
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

        self._copy_btn = Gtk.Button()
        copy_icon = Gtk.Image.new_from_icon_name("edit-copy-symbolic", Gtk.IconSize.MENU)
        self._copy_btn.set_image(copy_icon)
        self._copy_btn.set_always_show_image(True)
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
        sw.set_max_content_height(_HEIGHT_CAP)
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
        # Inter-line breathing room (the intern's "lack of spacing between
        # the lines" complaint). GTK3-native TextView property — no CSS
        # line-height exists in GTK3 (verified by introspection); the
        # default is 0px on every side.
        tv.set_pixels_above_lines(3)
        tv.set_pixels_below_lines(3)
        self._highlight(tv.get_buffer(), lang, code)

        # Height pin — same uniform rule as the prose width pin: a row child
        # whose minimum < natural gets allocated its MINIMUM by the ListBox
        # (verified live: a 13-line diagram rendered in a 46px porthole),
        # and an AUTOMATIC-vpolicy ScrolledWindow always has min < natural.
        # Pinning the request to min(natural, cap) makes min == natural below
        # the cap (no scrollbar; ASCII diagrams and short snippets show in
        # full) and exactly the cap above it (the existing 420px viewport +
        # vscroll).
        #
        # Measured via a Pango layout over the buffer text: an unrealized
        # TextView reports preferred height 0/1 (no font metrics yet), but
        # its style-context font IS already resolved (monospace 11 pre- and
        # post-realize) — layout height + top/bottom margins equals the
        # realized preferred height exactly (verified: 322+12 == 334).
        # WrapMode.NONE means the height is width-independent, so this never
        # fights the horizontal scrollbar.
        self._sw = sw
        self._tv = tv
        self._pinned_font_str = self.get_style_context().get_font(
            Gtk.StateFlags.NORMAL
        ).to_string()
        self._repin_idle_id = 0
        self._dead = False
        self.connect("destroy", lambda *_: setattr(self, "_dead", True))
        sw.set_size_request(-1, self._measure_body_height())
        # The construction pin is best-effort: this block is constructed
        # UNPARENTED by MarkdownView.render and packed only afterwards, so
        # the construction-time style context cannot see hierarchy-scoped
        # CSS (the sidebar's zoom projection), and the first size-allocate
        # may still run before the style pass resolves the packed font
        # (verified live: pin 273 vs true content 363 at 1.5x). So the block
        # watches its own style-updated: whenever the RESOLVED font changes
        # (packing under an active projection, a later projection step, a
        # theme change), it re-measures and re-pins — one uniform rule, no
        # one-shot windows to miss.
        self.connect("style-updated", self._on_style_updated)

        sw.add(tv)
        self.pack_start(header, False, False, 0)
        self.pack_start(sw, True, True, 0)

    def _measure_body_height(self) -> int:
        """Measure the pin height from the CURRENT buffer + CURRENT style font.

        One measurement law used at construction and at re-pin (``repin_height``).
        The resolved style font is set explicitly on the layout instead of
        leaning on ``create_pango_layout``'s inheritance: the widget's cached
        PangoContext only adopts a changed CSS font on the next
        frame-clock-driven style pass (verified live — right after a provider
        reload the style font reads 13.2pt while the PangoContext still holds
        11pt), so an explicit description is what makes the re-pin measure the
        PROJECTED font instead of a stale one. At construction the style font
        and the PangoContext font are the same, so the inherited-layout result
        is unchanged (verified: 302 == 302).
        """
        buf = self._tv.get_buffer()
        buf_text = buf.get_slice(buf.get_start_iter(), buf.get_end_iter(), True)
        layout = self._tv.create_pango_layout(buf_text)
        layout.set_font_description(
            self._tv.get_style_context().get_font(Gtk.StateFlags.NORMAL)
        )
        _lw, content_h = layout.get_pixel_size()
        # The per-line pixel spacing set above (3px above + 3px below) is not
        # part of the Pango measurement — the TextView adds one above/below
        # pair per line, so the pin must too (verified: 10-line diagram +
        # trailing newline = 11 lines, spacing 66px exactly accounts for the
        # measured natural-vs-pango delta).
        spacing = (
            self._tv.get_pixels_above_lines() + self._tv.get_pixels_below_lines()
        ) * layout.get_line_count()
        return min(
            content_h + spacing + self._tv.get_top_margin() + self._tv.get_bottom_margin(),
            _HEIGHT_CAP,
        )

    def repin_height(self) -> int:
        """R12 re-pin entry point: re-measure from the current buffer and the
        current style font, then re-apply the height pin.

        The construction-time pin was measured with the then-current font (an
        unparented style context at that, so it is best-effort only); a
        mid-session rescale of the projected chat font (sidebar
        ``set_zoom_projection``) leaves it stale, which clips rows. The
        style-updated font-watch and the sidebar's projection sweeps both
        route through here, so every rendered block
        measures the font its packed style context actually resolves to."""
        height = self._measure_body_height()
        self._sw.set_size_request(-1, height)
        self._pinned_font_str = self.get_style_context().get_font(
            Gtk.StateFlags.NORMAL
        ).to_string()
        return height

    def _on_style_updated(self, *_args) -> None:
        """Deferred font-watch: by idle time the full style pass (including
        this block's revalidated context) has settled, so the comparison and
        the re-measure both see the font the row will actually render with.
        Deferring also avoids re-pinning reentrantly inside the
        style-updated emission."""
        if not self._dead and not self._repin_idle_id:
            self._repin_idle_id = GLib.idle_add(self._repin_if_font_changed)

    def _repin_if_font_changed(self) -> bool:
        self._repin_idle_id = 0
        if self._dead:
            return False
        current = self.get_style_context().get_font(Gtk.StateFlags.NORMAL).to_string()
        if current != self._pinned_font_str:
            self.repin_height()
        return False  # one-shot

    def _on_copy(self, btn: Gtk.Button) -> None:
        # The shared copy confirmation (one implementation for every copy
        # button); the code block's old 2 s revert collapsed to the
        # canonical 1500 ms.
        confirm_copy(btn, self._code, idle_tooltip="Copy code to clipboard")

    def _highlight(self, buffer: Gtk.TextBuffer, lang: str, code: str) -> None:
        try:
            from pygments import lex
            from pygments.lexers import get_lexer_by_name, guess_lexer
            from pygments.styles import get_style_by_name
        except Exception:
            buffer.set_text(code)
            return

        style_name = get_code_style(self)
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
