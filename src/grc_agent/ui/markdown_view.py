# ruff: noqa: E402
"""Markdown → widget renderer for agent messages.

Owns the prose ``TextView`` path (markdown-it AST → ``Gtk.TextBuffer``
with named tags), inline block-name badges (child anchors), code blocks
(``CodeBlock`` with Pygments), tables (``TableBlock``), and the column-width
pin/rewrap subsystem. ``ChatSidebar`` holds one instance and delegates
``_render_markdown_to_box`` to it.

Column-width pinning + horizontal-scroll isolation exist because a bare
``Gtk.TextView``'s preferred width follows its unwrapped buffer content: a
long unbroken token (code line, URL, path) grows the chat row's minimum
width, which propagates through the NEVER-hscrollbar list into the outer
``Gtk.HPaned`` and shoves the divider aside while tokens stream — and once
allocated, the TextView's minimum sticks at that allocation. Every chat
TextView — streamed, thinking, and rendered prose — therefore lives inside
an AUTOMATIC-hscrollbar ``Gtk.ScrolledWindow`` (which, like ``CodeBlock`` and
``TableBlock``, never propagates the child's minimum upward) and is pinned to
the current column width for wrapping, re-pinned on resize. No content
measurement, no per-widget heuristics.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import gi
from markdown_it import MarkdownIt
from markdown_it.tree import SyntaxTreeNode

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gdk, GLib, Gtk, Pango

from .block_badge import BlockBadge, build_badge_regex
from .code_block import CodeBlock
from .css import is_dark_theme
from .table_block import TableBlock, parse_table

_log = logging.getLogger(__name__)


def _esc(text: str) -> str:
    return GLib.markup_escape_text(text, -1)


# Centralized typography metrics for native GTK3 text tags (no ad-hoc \n\n)
_LIST_MARGIN_BASE = 24
_LIST_MARGIN_PER_LEVEL = 16
_LIST_HANGING_INDENT = -16
_PARAGRAPH_PIXELS_BELOW = 6
_HEADING_PIXELS_ABOVE = 10
_HEADING_PIXELS_BELOW = 4
_BLOCKQUOTE_LEFT_MARGIN = 16
_BLOCKQUOTE_PIXELS_BELOW = 4


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
        self._badge_regex_cache = (key, pattern)
        return pattern

    def _get_or_create_block_tag(self, buffer: Gtk.TextBuffer, name: str) -> Gtk.TextTag:
        tag_name = f"block_badge_{name}"
        tag = buffer.get_tag_table().lookup(tag_name)
        if tag is None:
            tag = buffer.create_tag(
                tag_name,
                family="monospace",
                weight=Pango.Weight.BOLD,
                background="rgba(255, 255, 255, 0.12)" if is_dark_theme() else "rgba(0, 0, 0, 0.08)",
            )
            tag.grc_block_name = name
            tag.connect("event", self._on_block_tag_event)
        return tag

    def _on_block_tag_event(self, tag: Any, _widget: Any, event: Any, _iter: Any) -> bool:
        if event.type == Gdk.EventType.BUTTON_PRESS and getattr(event, "button", 1) == 1:
            cm = self._get_cm()
            name = getattr(tag, "grc_block_name", "")
            if cm is not None and name:
                cm.scroll_to_block(name)
            return True
        return False

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
        return box

    @staticmethod
    def _inline_label(text: str, bold: bool) -> Gtk.Label:
        lbl = Gtk.Label()
        if bold:
            lbl.set_markup(f"<b>{_esc(text)}</b>")
        else:
            lbl.set_text(text)
        lbl.set_xalign(0.0)
        lbl.set_selectable(True)
        return lbl

    # -- prose TextBuffer building -----------------------------------------
    def _ensure_buffer_tags(self, buffer: Gtk.TextBuffer) -> None:
        tag_table = buffer.get_tag_table()
        if tag_table.lookup("bold") is None:
            buffer.create_tag("bold", weight=Pango.Weight.BOLD)
        if tag_table.lookup("italic") is None:
            buffer.create_tag("italic", style=Pango.Style.ITALIC)
        if tag_table.lookup("strikethrough") is None:
            buffer.create_tag("strikethrough", strikethrough=True)
        if tag_table.lookup("code") is None:
            buffer.create_tag(
                "code",
                family="monospace",
                weight=Pango.Weight.BOLD,
            )
        if tag_table.lookup("heading") is None:
            buffer.create_tag(
                "heading",
                weight=Pango.Weight.BOLD,
                scale=1.16,
                pixels_above_lines=_HEADING_PIXELS_ABOVE,
                pixels_below_lines=_HEADING_PIXELS_BELOW,
            )
        if tag_table.lookup("paragraph") is None:
            buffer.create_tag("paragraph", pixels_below_lines=_PARAGRAPH_PIXELS_BELOW)
        if tag_table.lookup("blockquote") is None:
            buffer.create_tag(
                "blockquote",
                style=Pango.Style.ITALIC,
                left_margin=_BLOCKQUOTE_LEFT_MARGIN,
                pixels_below_lines=_BLOCKQUOTE_PIXELS_BELOW,
            )

    def _get_or_create_list_tag(self, buffer: Gtk.TextBuffer, list_depth: int) -> Gtk.TextTag:
        tag_name = f"list_depth_{list_depth}"
        tag = buffer.get_tag_table().lookup(tag_name)
        if tag is None:
            left_m = _LIST_MARGIN_BASE + list_depth * _LIST_MARGIN_PER_LEVEL
            tag = buffer.create_tag(
                tag_name,
                left_margin=left_m,
                indent=_LIST_HANGING_INDENT,
                pixels_below_lines=2,
            )
        return tag

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
        self, buffer: Gtk.TextBuffer, text: str, tags: list, _tv: Gtk.TextView
    ) -> None:
        rx = self.compile_badge_regex()
        if rx is None:
            self._insert_plain_tagged(buffer, text, tags)
            return

        last_end = 0
        for m in rx.finditer(text):
            self._insert_plain_tagged(buffer, text[last_end : m.start()], tags)

            name = m.group(1)
            block_tag = self._get_or_create_block_tag(buffer, name)
            self._insert_plain_tagged(buffer, name, tags + [block_tag])

            last_end = m.end()

        self._insert_plain_tagged(buffer, text[last_end:], tags)

    def _on_link_tag_event(self, tag: Any, widget: Any, event: Any, _iter: Any, href: str) -> bool:
        """Open a link on click, never when release completes text selection."""
        if event.type == Gdk.EventType.BUTTON_PRESS:
            tag.grc_press_xy = (int(event.x), int(event.y))
            return False
        if href and event.type == Gdk.EventType.BUTTON_RELEASE:
            start = getattr(tag, "grc_press_xy", None)
            tag.grc_press_xy = None
            if start is not None and not widget.drag_check_threshold(
                start[0],
                start[1],
                int(event.x),
                int(event.y),
            ):
                Gtk.show_uri_on_window(None, href, event.time)
                return True
        return False

    def _on_prose_motion_notify(self, tv: Gtk.TextView, event: Any) -> bool:
        """Pointer cursor over links and block badges; canvas highlight on badge hover."""
        bx, by = tv.window_to_buffer_coords(Gtk.TextWindowType.TEXT, int(event.x), int(event.y))
        _found, it = tv.get_iter_at_location(bx, by)
        tags = it.get_tags()
        hovering_link = any(getattr(t, "grc_href", None) for t in tags)
        block_tag = next((t for t in tags if hasattr(t, "grc_block_name")), None)
        hovered_name = getattr(block_tag, "grc_block_name", None)

        prev_hovered = getattr(tv, "_grc_hovered_block", None)
        if hovered_name != prev_hovered:
            tv._grc_hovered_block = hovered_name
            cm = self._get_cm()
            if cm is not None:
                if hovered_name:
                    cm.set_highlight_block(hovered_name)
                else:
                    cm.clear_highlight()

        window = tv.get_window(Gtk.TextWindowType.TEXT)
        if window is not None:
            cursor = (
                Gdk.Cursor.new_from_name(window.get_display(), "pointer")
                if (hovering_link or hovered_name)
                else None
            )
            window.set_cursor(cursor)
        return False

    def _on_prose_leave_notify(self, tv: Gtk.TextView, _event: Any) -> bool:
        """Clear canvas highlight and pointer cursor when mouse leaves TextView."""
        if getattr(tv, "_grc_hovered_block", None):
            tv._grc_hovered_block = None
            cm = self._get_cm()
            if cm is not None:
                cm.clear_highlight()
        window = tv.get_window(Gtk.TextWindowType.TEXT)
        if window is not None:
            window.set_cursor(None)
        return False

    def _render_node(  # noqa: C901
        self,
        node: SyntaxTreeNode,
        buffer: Gtk.TextBuffer,
        tv: Gtk.TextView,
        active_tags: list,
        in_list: bool = False,
        list_depth: int = 0,
    ) -> None:
        """Recursive markdown-it SyntaxTreeNode -> TextBuffer walk with tag dispatch,
        badge-aware leaf text, clickable links, and list/blockquote formatting."""
        ntype = node.type
        if ntype == "text":
            self._insert_prose_text_with_badges(buffer, node.content, active_tags, tv)
        elif ntype == "code_inline":
            self._insert_prose_text_with_badges(buffer, node.content, active_tags + ["code"], tv)
        elif ntype in ("softbreak", "hardbreak"):
            self._insert_plain_tagged(buffer, "\n", active_tags)
        elif ntype == "paragraph":
            if in_list:
                for child in node.children:
                    self._render_node(
                        child, buffer, tv, active_tags, in_list=True, list_depth=list_depth
                    )
            else:
                for child in node.children:
                    self._render_node(
                        child, buffer, tv, active_tags + ["paragraph"], in_list=False, list_depth=list_depth
                    )
                self._insert_plain_tagged(buffer, "\n", active_tags + ["paragraph"])
        elif ntype == "heading":
            for child in node.children:
                self._render_node(
                    child, buffer, tv, active_tags + ["heading"], in_list=False, list_depth=list_depth
                )
            self._insert_plain_tagged(buffer, "\n", active_tags + ["heading"])
        elif ntype in ("strong", "b"):
            for child in node.children:
                self._render_node(
                    child, buffer, tv, active_tags + ["bold"], in_list=in_list, list_depth=list_depth
                )
        elif ntype in ("em", "i"):
            for child in node.children:
                self._render_node(
                    child, buffer, tv, active_tags + ["italic"], in_list=in_list, list_depth=list_depth
                )
        elif ntype == "s":
            for child in node.children:
                self._render_node(
                    child, buffer, tv, active_tags + ["strikethrough"], in_list=in_list, list_depth=list_depth
                )
        elif ntype == "link":
            attrs = node.attrs or {}
            href = attrs.get("href", "")
            # Theme fg + underline + hover pointer (see _on_prose_motion_notify).
            # GTK3's own GtkLinkButton uses plain theme fg here too; a hardcoded
            # blue (e.g. #1565c0) is unreadable on dark themes.
            link_tag = buffer.create_tag(None, underline=Pango.Underline.SINGLE)
            link_tag.grc_href = href
            link_tag.connect("event", self._on_link_tag_event, href)
            for child in node.children:
                self._render_node(
                    child, buffer, tv, active_tags + [link_tag], in_list=in_list, list_depth=list_depth
                )
        elif ntype == "inline":
            for child in node.children:
                self._render_node(
                    child, buffer, tv, active_tags, in_list=in_list, list_depth=list_depth
                )
        elif ntype in ("bullet_list", "ordered_list"):
            list_tag = self._get_or_create_list_tag(buffer, list_depth)
            for i, item in enumerate(node.children, start=1):
                num = item.info if item.info else str(i)
                prefix = f"{num}. " if ntype == "ordered_list" else "\u2022 "
                self._insert_plain_tagged(buffer, prefix, active_tags + [list_tag])
                for c_idx, child in enumerate(item.children):
                    if child.type in ("bullet_list", "ordered_list"):
                        self._insert_plain_tagged(buffer, "\n", active_tags + [list_tag])
                        self._render_node(
                            child, buffer, tv, active_tags, in_list=True, list_depth=list_depth + 1
                        )
                    else:
                        if c_idx > 0:
                            self._insert_plain_tagged(buffer, "\n", active_tags + [list_tag])
                        self._render_node(
                            child, buffer, tv, active_tags + [list_tag], in_list=True, list_depth=list_depth
                        )
                self._insert_plain_tagged(buffer, "\n", active_tags + [list_tag])
        elif ntype == "list_item":
            list_tag = self._get_or_create_list_tag(buffer, list_depth)
            self._insert_plain_tagged(buffer, "\u2022 ", active_tags + [list_tag])
            for c_idx, child in enumerate(node.children):
                if c_idx > 0:
                    self._insert_plain_tagged(buffer, "\n", active_tags + [list_tag])
                self._render_node(
                    child, buffer, tv, active_tags + [list_tag], in_list=True, list_depth=list_depth
                )
            self._insert_plain_tagged(buffer, "\n", active_tags + [list_tag])
        elif ntype == "blockquote":
            for child in node.children:
                self._insert_plain_tagged(buffer, "│ ", active_tags + ["blockquote"])
                self._render_node(
                    child, buffer, tv, active_tags + ["blockquote"], in_list=False, list_depth=list_depth
                )
                self._insert_plain_tagged(buffer, "\n", active_tags + ["blockquote"])
        elif ntype in ("fence", "code_block"):
            self._insert_plain_tagged(buffer, node.content, active_tags + ["code"])
        elif ntype in ("table", "thead", "tbody", "tr", "td", "th"):
            for child in node.children:
                self._render_node(
                    child, buffer, tv, active_tags, in_list=in_list, list_depth=list_depth
                )
        elif ntype == "hr":
            self._insert_plain_tagged(buffer, "\n───\n", active_tags)
        else:
            if node.content and not node.children:
                self._insert_prose_text_with_badges(buffer, node.content, active_tags, tv)
            else:
                for child in node.children:
                    self._render_node(
                        child, buffer, tv, active_tags, in_list=in_list, list_depth=list_depth
                    )

    # -- prose TextView building + column sizing --------------------------
    def _make_prose_textview(self) -> Gtk.TextView:
        tv = Gtk.TextView()
        tv.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        tv.set_editable(False)
        tv.set_cursor_visible(False)
        tv.get_style_context().add_class("chat-agent-label")
        tv.set_left_margin(0)
        tv.set_right_margin(0)
        tv.set_top_margin(0)
        tv.set_bottom_margin(0)
        tv.set_pixels_above_lines(2)
        tv.set_pixels_below_lines(2)
        tv.set_pixels_inside_wrap(2)
        tv.set_hexpand(True)
        tv.set_halign(Gtk.Align.FILL)
        tv.add_events(Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
        tv.connect("motion-notify-event", self._on_prose_motion_notify)
        tv.connect("leave-notify-event", self._on_prose_leave_notify)
        return tv

    @staticmethod
    def wrap_hscrollable(tv: Gtk.TextView) -> Gtk.ScrolledWindow:
        """Isolate ``tv``'s content-driven minimum from the chat column.

        A ScrolledWindow with an AUTOMATIC hscrollbar reports a tiny minimum
        regardless of its child (same mechanism as CodeBlock/TableBlock), so
        a long unbroken token can never propagate a minimum into the row →
        list → HPaned chain and shove the divider; the pin above still sets
        the wrap width. NEVER vpolicy + propagate_natural_height lets prose
        grow downward unbounded without a vertical scrollbar.
        """
        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        sw.set_shadow_type(Gtk.ShadowType.NONE)
        sw.set_propagate_natural_height(True)
        sw.set_hexpand(True)
        sw.set_halign(Gtk.Align.FILL)
        sw.add(tv)
        return sw

    def _make_plain_label(self, text: str) -> Gtk.Label:
        lbl = Gtk.Label(label=text)
        lbl.set_line_wrap(True)
        lbl.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        lbl.set_xalign(0.0)
        lbl.set_halign(Gtk.Align.START)
        lbl.set_selectable(True)
        lbl.get_style_context().add_class("chat-agent-label")
        return lbl

    # Horizontal chrome between the ListBox's allocated width and a message
    # TextView's content area: box padding (8px left + 8px right = 16px),
    # listbox slack, and border — measured ≈24-32px. Reduced from 140px since
    # the Copy button is now embedded at the message bottom rather than in a
    # wide side-by-side column.
    _COLUMN_CHROME = 36

    def pin_to_column(self, tv: Gtk.TextView, extra: int = 0) -> None:
        """Pin ``tv``'s width request to the current chat column width.

        This sets the WRAP width (a bare Gtk.TextView's preferred width is
        content-driven, so without a request a wide column wraps one word per
        line and an unallocated one collapses). It deliberately does NOT rely
        on the request as a layout cap: once allocated, a TextView's minimum
        sticks at that allocation, so capping minimums is the enclosing
        AUTOMATIC-hscrollbar ScrolledWindow's job (min ~0, never propagated).
        ``extra`` widens the subtraction for deeper-nested TextViews (e.g.
        the Thinking expander's arrow/spacing).
        """
        tv.grc_is_pinned = True
        tv.grc_pin_extra = extra
        allocated = self._listbox.get_allocated_width()
        available = (
            allocated
            if allocated > 160
            else (self._last_listbox_width if self._last_listbox_width > 160 else 320)
        )
        tv.set_size_request(max(160, available - self._COLUMN_CHROME - extra), -1)

    def _on_listbox_size_allocate(self, _listbox: Gtk.ListBox, allocation: Any) -> None:
        width = allocation.width
        if width <= 160 or width == self._last_listbox_width:
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
            # Re-allocate rows whose content was added after their first
            # allocation (code blocks, tables — see CodeBlock's height pin):
            # the ListBox's row-geometry cache doesn't re-run on height-only
            # size-request changes, and an explicit check_resize does.
            self._listbox.check_resize()
        return False  # one-shot

    def _rewrap_prose_textviews(self, container: Gtk.Widget) -> None:
        """Re-pin every pinned TextView to the current column width — needed for
        history loaded before first size-allocate and for dragging the divider.
        One uniform rule: full column width, no content measuring."""
        for child in container.get_children():
            if getattr(child, "grc_is_pinned", False):
                self.pin_to_column(child, getattr(child, "grc_pin_extra", 0))
            elif isinstance(child, Gtk.Container):
                self._rewrap_prose_textviews(child)

    # -- top-level render ---------------------------------------------------
    def render(self, box: Gtk.Box, text: str, clear: bool = True) -> None:  # noqa: C901
        if clear:
            for child in box.get_children():
                box.remove(child)

        try:
            md = MarkdownIt("commonmark", {"html": False}).enable("table").enable("strikethrough")
            tree = SyntaxTreeNode(md.parse(text))

            current_tv: Gtk.TextView | None = None
            current_buffer: Gtk.TextBuffer | None = None

            def _flush_prose():
                nonlocal current_tv, current_buffer
                if current_tv is None or current_buffer is None:
                    return
                content = current_buffer.get_slice(
                    current_buffer.get_start_iter(), current_buffer.get_end_iter(), True
                ).strip()
                if content:
                    self.pin_to_column(current_tv)
                    box.pack_start(self.wrap_hscrollable(current_tv), False, False, 0)
                current_tv = None
                current_buffer = None

            for node in tree.children:
                if node.type in ("fence", "code_block"):
                    _flush_prose()
                    code_text = node.content.replace("\u00a0", " ")
                    lang = node.info.strip().split()[0] if node.info and node.info.strip() else ""
                    box.pack_start(CodeBlock(lang, code_text), False, False, 0)
                elif node.type == "table":
                    _flush_prose()
                    headers, rows = parse_table(node)
                    if headers or rows:
                        box.pack_start(
                            TableBlock(headers, rows, self.render_inline), False, False, 0
                        )
                else:
                    if current_tv is None:
                        current_tv = self._make_prose_textview()
                        current_buffer = current_tv.get_buffer()
                        self._ensure_buffer_tags(current_buffer)
                    self._render_node(node, current_buffer, current_tv, active_tags=[])

            _flush_prose()
            box.show_all()
            if self._rewrap_idle_id is None and not self._shutting_down:
                self._rewrap_idle_id = GLib.idle_add(self._do_idle_rewrap)
        except Exception as e:
            _log.warning("MarkdownView.render failed: %s", e)
            lbl = self._make_plain_label(text)
            box.pack_start(lbl, False, False, 0)
            box.show_all()
