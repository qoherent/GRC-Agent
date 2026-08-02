# ruff: noqa: E402
"""Markdown → widget renderer for agent messages.

Owns the prose ``TextView`` path (MarkdownIt → BeautifulSoup → ``Gtk.TextBuffer``
with named tags), inline block-name badges (child anchors), code blocks
(``CodeBlock`` with Pygments), tables (``TableBlock``), and the prose
width/rewrap subsystem. ``ChatSidebar`` holds one instance and delegates
``_render_markdown_to_box`` to it.

The rewrap machinery exists because ``Gtk.TextView`` (unlike ``Gtk.Label``)
cannot self-measure a word-wrap width, so prose bubbles that hug their content
would collapse one-word-per-line without an explicit size request, and must be
re-clamped when the sidebar is resized. That logic lives here, isolated, rather
than scattered across the sidebar.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import gi
from bs4 import BeautifulSoup, NavigableString
from markdown_it import MarkdownIt

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gdk, GLib, Gtk, Pango

from .block_badge import BlockBadge, build_badge_regex
from .code_block import CodeBlock
from .table_block import TableBlock, parse_table

_log = logging.getLogger(__name__)


def _esc(text: str) -> str:
    return GLib.markup_escape_text(text, -1)


class MarkdownView:
    """Renders markdown text into a vertical ``Gtk.Box`` of prose/code/table
    widgets. Construct once with the chat listbox and a canvas-manager getter."""

    def __init__(self, listbox: Gtk.ListBox, get_cm) -> None:
        self._listbox = listbox
        self._get_cm = get_cm
        self._badge_regex_cache: tuple[frozenset, re.Pattern] | None = None
        self._last_listbox_width = 0
        self._rewrap_idle_id: int | None = None
        self._shutting_down = False
        listbox.connect("size-allocate", self._on_listbox_size_allocate)

    def set_shutting_down(self, value: bool) -> None:
        self._shutting_down = value

    # -- badge regex --------------------------------------------------------
    def _get_active_block_names(self) -> set[str]:
        cm = self._get_cm()
        fg = cm.current_flow_graph if cm else None
        return {b.name for b in fg.blocks} if fg else set()

    def compile_badge_regex(self) -> re.Pattern | None:
        """Whole-word regex over the live flowgraph's block names, cached by the
        block-name set so it's rebuilt only when blocks are added/removed/renamed."""
        names = self._get_active_block_names()
        if not names:
            self._badge_regex_cache = None
            return None
        key = frozenset(names)
        if self._badge_regex_cache and self._badge_regex_cache[0] == key:
            return self._badge_regex_cache[1]
        pattern = build_badge_regex(names)
        self._badge_regex_cache = (key, pattern) if pattern else None
        return pattern

    def _make_block_badge_widget(self, name: str) -> Gtk.EventBox:
        return BlockBadge(name, self._get_cm)

    # -- table cell renderer (badge-aware) ---------------------------------
    def render_inline(self, text: str, bold: bool = False) -> Gtk.Box:
        """Render a single-line cell's text into a badge-aware horizontal Box
        (labels + BlockBadge pills). Passed to TableBlock for each cell."""
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        rx = self.compile_badge_regex()
        if rx is None or not text:
            box.pack_start(self._inline_label(text, bold), False, False, 0)
            return box
        last = 0
        for m in rx.finditer(text):
            if m.start() > last:
                box.pack_start(self._inline_label(text[last : m.start()], bold), False, False, 0)
            box.pack_start(BlockBadge(m.group(1), self._get_cm), False, False, 0)
            last = m.end()
        if text[last:]:
            box.pack_start(self._inline_label(text[last:], bold), False, False, 0)
        if not box.get_children():
            box.pack_start(self._inline_label("", bold), False, False, 0)
        return box

    @staticmethod
    def _inline_label(text: str, bold: bool) -> Gtk.Label:
        lbl = Gtk.Label()
        if bold:
            lbl.set_markup(f"<b>{_esc(text)}</b>")
        else:
            lbl.set_text(text)
        lbl.set_xalign(0.0)
        return lbl

    # -- prose TextBuffer building -----------------------------------------
    def _ensure_buffer_tags(self, buffer: Gtk.TextBuffer) -> None:
        tag_table = buffer.get_tag_table()
        if tag_table.lookup("bold") is None:
            buffer.create_tag("bold", weight=Pango.Weight.BOLD)
        if tag_table.lookup("italic") is None:
            buffer.create_tag("italic", style=Pango.Style.ITALIC)
        if tag_table.lookup("code") is None:
            # Monospace only — inline code inherits the theme's fg, which is
            # readable on every theme. (A hardcoded light bg like #f0f0f0 is
            # invisible-on-dark; deriving the theme color per-tag is unreliable
            # before the widget is realized, so we rely on the theme itself.)
            buffer.create_tag("code", family="monospace")
        if tag_table.lookup("heading") is None:
            buffer.create_tag("heading", weight=Pango.Weight.BOLD, scale=1.15)

    def _insert_plain_tagged(self, buffer: Gtk.TextBuffer, text: str, tags: list) -> None:
        if not text:
            return
        start_offset = buffer.get_end_iter().get_offset()
        buffer.insert(buffer.get_end_iter(), text)
        if tags:
            # get_iter_at_offset (not the iter passed to insert()) — GTK
            # revalidates that iter to the END of the inserted text, so
            # start/end would otherwise both land on the same position.
            start = buffer.get_iter_at_offset(start_offset)
            end = buffer.get_end_iter()
            for t in tags:
                if isinstance(t, str):
                    buffer.apply_tag_by_name(t, start, end)
                else:
                    buffer.apply_tag(t, start, end)

    def _insert_prose_text_with_badges(
        self, buffer: Gtk.TextBuffer, text: str, tags: list, tv: Gtk.TextView
    ) -> None:
        rx = self.compile_badge_regex()
        if rx is None:
            self._insert_plain_tagged(buffer, text, tags)
            return

        last_end = 0
        for m in rx.finditer(text):
            self._insert_plain_tagged(buffer, text[last_end : m.start()], tags)

            name = m.group(1)
            anchor = buffer.create_child_anchor(buffer.get_end_iter())
            pill = self._make_block_badge_widget(name)
            tv.add_child_at_anchor(pill, anchor)
            pill.show_all()

            last_end = m.end()

        self._insert_plain_tagged(buffer, text[last_end:], tags)

    def _on_link_tag_event(self, _tag: Any, _widget: Any, event: Any, _iter: Any, href: str) -> bool:
        """Mirrors Gtk.Label's built-in activate-link default handler."""
        if href and event.type == Gdk.EventType.BUTTON_RELEASE:
            Gtk.show_uri_on_window(None, href, event.time)
            return True
        return False

    def _on_prose_motion_notify(self, tv: Gtk.TextView, event: Any) -> bool:
        """Pointer cursor over link text; reset once it leaves the link."""
        bx, by = tv.window_to_buffer_coords(Gtk.TextWindowType.TEXT, int(event.x), int(event.y))
        _found, it = tv.get_iter_at_location(bx, by)
        hovering_link = any(getattr(t, "grc_href", None) for t in it.get_tags())
        window = tv.get_window(Gtk.TextWindowType.TEXT)
        if window is not None:
            cursor = Gdk.Cursor.new_from_name(window.get_display(), "pointer") if hovering_link else None
            window.set_cursor(cursor)
        return False

    def _element_to_buffer(  # noqa: C901
        self, element: Any, buffer: Gtk.TextBuffer, tv: Gtk.TextView, active_tags: list
    ) -> None:
        """Recursive MarkdownIt/BS4 element → TextBuffer walk with tag dispatch,
        badge-aware leaf text and real per-link click handling."""
        if isinstance(element, NavigableString):
            self._insert_prose_text_with_badges(buffer, str(element), active_tags, tv)
            return

        tag = element.name
        if not tag:
            return

        if tag in ("ul", "ol"):
            li_children = [c for c in element.children if getattr(c, "name", None) == "li"]
            for i, li in enumerate(li_children, start=1):
                prefix = f"{i}." if tag == "ol" else "\u2022"
                self._insert_plain_tagged(buffer, f"  {prefix}  ", active_tags)
                for child in li.children:
                    self._element_to_buffer(child, buffer, tv, active_tags)
                self._insert_plain_tagged(buffer, "\n", active_tags)
            return

        if tag in ("p", "div"):
            for child in element.children:
                self._element_to_buffer(child, buffer, tv, active_tags)
            self._insert_plain_tagged(buffer, "\n", active_tags)
        elif tag in ("strong", "b"):
            for child in element.children:
                self._element_to_buffer(child, buffer, tv, active_tags + ["bold"])
        elif tag in ("em", "i"):
            for child in element.children:
                self._element_to_buffer(child, buffer, tv, active_tags + ["italic"])
        elif tag in ("code", "tt"):
            for child in element.children:
                self._element_to_buffer(child, buffer, tv, active_tags + ["code"])
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            for child in element.children:
                self._element_to_buffer(child, buffer, tv, active_tags + ["heading"])
            self._insert_plain_tagged(buffer, "\n", active_tags)
        elif tag == "a":
            href = element.get("href", "")
            # Theme fg + underline + hover pointer (see _on_prose_motion_notify).
            # GTK3's own GtkLinkButton uses plain theme fg here too; a hardcoded
            # blue (e.g. #1565c0) is unreadable on dark themes.
            link_tag = buffer.create_tag(None, underline=Pango.Underline.SINGLE)
            link_tag.grc_href = href
            link_tag.connect("event", self._on_link_tag_event, href)
            for child in element.children:
                self._element_to_buffer(child, buffer, tv, active_tags + [link_tag])
        elif tag == "li":
            # Defensive fallback for a stray orphaned <li> outside any ul/ol —
            # the normal case is handled above by the ul/ol branch itself.
            self._insert_plain_tagged(buffer, "  \u2022  ", active_tags)
            for child in element.children:
                self._element_to_buffer(child, buffer, tv, active_tags)
            self._insert_plain_tagged(buffer, "\n", active_tags)
        elif tag in ("table", "thead", "tbody", "tr", "td", "th", "pre"):
            # Never reached — render() intercepts <table>/<pre> at the top level.
            return
        else:
            for child in element.children:
                self._element_to_buffer(child, buffer, tv, active_tags)

    # -- prose TextView + width sizing -------------------------------------
    def _make_prose_textview(self) -> Gtk.TextView:
        tv = Gtk.TextView()
        tv.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        tv.set_editable(False)
        tv.set_cursor_visible(False)
        tv.get_style_context().add_class("chat-agent-label")
        tv.grc_is_prose = True  # marks it for _rewrap_prose_textviews, distinct
        # from the unrelated "Thinking" expander's fixed-height textview.
        tv.set_left_margin(0)
        tv.set_right_margin(0)
        tv.set_top_margin(0)
        tv.set_bottom_margin(0)
        tv.set_hexpand(True)
        tv.set_halign(Gtk.Align.FILL)
        tv.add_events(Gdk.EventMask.POINTER_MOTION_MASK)
        tv.connect("motion-notify-event", self._on_prose_motion_notify)
        return tv

    def _make_plain_label(self, text: str) -> Gtk.Label:
        lbl = Gtk.Label(label=text)
        lbl.set_line_wrap(True)
        lbl.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        lbl.set_xalign(0.0)
        lbl.set_halign(Gtk.Align.START)
        lbl.set_selectable(True)
        lbl.get_style_context().add_class("chat-agent-label")
        return lbl

    def _size_prose_textview_to_content(self, tv: Gtk.TextView, plain_text: str) -> None:
        """Gtk.TextView reports only a minimal preferred width for word-wrapped
        content — left alone, a content-hugging bubble collapses to that minimal
        width and wraps one word per line. Measure the text's unwrapped Pango
        extent, cap it at the available column width, and floor it at the badges'
        own widths so a badge-heavy bubble still fits its pills."""
        layout = tv.create_pango_layout(plain_text)
        _ink, logical = layout.get_pixel_extents()
        available = self._listbox.get_allocated_width() or 320
        max_width = max(160, available - 90)
        width = min(logical.width, max_width)
        badges = [c for c in tv.get_children() if getattr(c, "grc_is_badge", False)]
        if badges:
            min_for_pills = min(sum(c.get_preferred_width()[1] for c in badges), max_width)
            width = max(width, min_for_pills)
        tv.set_size_request(width, -1)

    def _on_listbox_size_allocate(self, _listbox: Gtk.ListBox, allocation: Any) -> None:
        width = allocation.width
        if width == self._last_listbox_width:
            return
        self._last_listbox_width = width
        # Defer — multiple allocates during a drag schedule one idle source,
        # which reads the live listbox width when it runs.
        if self._rewrap_idle_id is None:
            self._rewrap_idle_id = GLib.idle_add(self._do_idle_rewrap)

    def _do_idle_rewrap(self) -> bool:
        self._rewrap_idle_id = None
        if not self._shutting_down:
            self._rewrap_prose_textviews(self._listbox)
        return False  # one-shot

    def _rewrap_prose_textviews(self, container: Gtk.Widget) -> None:
        """Re-clamp every rendered prose bubble to the current width — needed for
        history loaded before first size-allocate and for dragging the divider.

        Reuses the sizing text stored on the textview (grc_plain_for_size)
        rather than buffer.get_slice(): the slice carries a \uFFFC placeholder
        per pill badge (~1 char to Pango), which would collapse a badge-heavy
        bubble on every resize."""
        for child in container.get_children():
            if getattr(child, "grc_is_prose", False):
                plain = getattr(child, "grc_plain_for_size", None)
                if plain is None:
                    buffer = child.get_buffer()
                    plain = buffer.get_slice(buffer.get_start_iter(), buffer.get_end_iter(), True)
                self._size_prose_textview_to_content(child, plain)
            elif isinstance(child, Gtk.Container):
                self._rewrap_prose_textviews(child)

    # -- top-level render ---------------------------------------------------
    def render(self, box: Gtk.Box, text: str, clear: bool = True) -> None:  # noqa: C901
        if clear:
            for child in box.get_children():
                box.remove(child)

        try:
            md = MarkdownIt("commonmark").enable("table")
            html = md.render(text)
            soup = BeautifulSoup(html, "html.parser")

            for element in soup.contents:
                if not element.name:
                    t = str(element).strip()
                    if t:
                        tv = self._make_prose_textview()
                        buffer = tv.get_buffer()
                        self._ensure_buffer_tags(buffer)
                        self._insert_prose_text_with_badges(buffer, t, [], tv)
                        tv.grc_plain_for_size = t
                        self._size_prose_textview_to_content(tv, t)
                        box.pack_start(tv, False, False, 0)
                    continue

                tag = element.name
                if tag == "table":
                    headers, rows = parse_table(element)
                    if headers or rows:
                        box.pack_start(TableBlock(headers, rows, self.render_inline), False, False, 0)
                elif tag == "pre":
                    code_text = element.get_text().replace("\u00a0", " ").replace("\xa0", " ")
                    lang = ""
                    code_child = element.find("code")
                    if code_child and code_child.has_attr("class"):
                        for c in code_child["class"]:
                            if c.startswith("language-"):
                                lang = c[9:]
                                break
                    box.pack_start(CodeBlock(lang, code_text), False, False, 0)
                else:
                    tv = self._make_prose_textview()
                    buffer = tv.get_buffer()
                    self._ensure_buffer_tags(buffer)
                    self._element_to_buffer(element, buffer, tv, active_tags=[])
                    # get_slice (not get_text) — get_text() drops the U+FFFC
                    # child-anchor placeholder, so a badge-only paragraph would
                    # look "empty" and get silently dropped.
                    content = buffer.get_slice(
                        buffer.get_start_iter(), buffer.get_end_iter(), True
                    ).strip()
                    if content:
                        tv.grc_plain_for_size = element.get_text()
                        self._size_prose_textview_to_content(tv, element.get_text())
                        box.pack_start(tv, False, False, 0)

            box.show_all()
        except Exception as e:
            _log.warning("MarkdownView.render failed: %s", e)
            lbl = self._make_plain_label(text)
            box.pack_start(lbl, False, False, 0)
            box.show_all()
