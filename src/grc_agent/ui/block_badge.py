# ruff: noqa: E402
"""Inline block-name badge ("pill") rendered inside agent prose and table cells.

Hovering a pill highlights the corresponding block on the GRC canvas; clicking
scrolls the canvas to it. The pill is an ``EventBox`` so it can be embedded both
as a ``Gtk.TextBuffer`` child anchor (inside prose TextViews) and as a plain
box child (inside table cells).

One uniform whole-word regex is built from the live flowgraph's block names —
no per-scenario heuristics, no allowlists. ``build_badge_regex`` is stateless;
callers cache it by the block-name ``frozenset``.
"""

from __future__ import annotations

import re

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gtk


def build_badge_regex(names: set[str]) -> re.Pattern | None:
    """Whole-word alternation regex matching any of ``names``. None if empty.

    Longest-name-first so a name that is a prefix of another matches the longer
    one. ``\\w`` (Unicode) boundaries match GRC's block-id validation
    ``^[a-z|A-Z]\\w*$`` — an ASCII block name immediately followed by a Unicode
    letter (e.g. "data" inside "dataéx") must not false-badge the substring.
    """
    if not names:
        return None
    alternation = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
    return re.compile(r"(?<!\w)(" + alternation + r")(?!\w)")


def badge_enter(cm, name: str) -> None:
    if cm is not None:
        cm.set_highlight_block(name)


def badge_leave(cm) -> None:
    if cm is not None:
        cm.clear_highlight()


def badge_click(cm, event, name: str) -> bool:
    if event.type == Gdk.EventType.BUTTON_PRESS and getattr(event, "button", 1) == 1:
        if cm is not None:
            cm.scroll_to_block(name)
        return True
    return False


class BlockBadge(Gtk.EventBox):
    """A single clickable block-name pill. ``cm_getter`` resolves the live
    canvas manager on each event (it can change across tab switches).

    ``anchored`` selects the prose-TextView embedding mode: the label is
    wrapped in a vertically-padded ``Gtk.Box`` so its text aligns with the
    surrounding sentence's baseline. GTK3 child-anchor widgets are top-aligned
    and STRETCH to the full line box (verified by measurement: a 23px badge
    sits at line_y=0 with its bottom 5px below the baseline), and the label
    text is centered inside that stretched box — so without the padding the
    badge text rides ~4px above the surrounding text and reads as a
    superscript. CSS padding on the EventBox itself does nothing (GtkBin
    ignores padding for child allocation), and a Pango ``rise`` tag on the
    anchor character does not move child-anchor widgets at all — both
    measured. Table cells (``anchored=False``) keep the plain centered label.
    """

    def __init__(self, name: str, cm_getter, anchored: bool = False) -> None:
        super().__init__()
        self.name = name
        self._cm_getter = cm_getter
        self.get_style_context().add_class("chat-block-badge")
        self.set_above_child(True)  # events route to the EventBox, not the child label
        self.add_events(
            Gdk.EventMask.ENTER_NOTIFY_MASK
            | Gdk.EventMask.LEAVE_NOTIFY_MASK
            | Gdk.EventMask.BUTTON_PRESS_MASK
        )

        lbl = Gtk.Label(label=name)
        lbl.set_selectable(False)
        if anchored:
            # GtkBox respects CSS padding (GtkBin does not): padding-top: 4px
            # on this box lowers the label text onto the prose baseline.
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            box.get_style_context().add_class("chat-block-badge-anchored")
            box.pack_start(lbl, False, False, 0)
            self.add(box)
        else:
            self.add(lbl)

        self.connect("enter-notify-event", self._on_enter)
        self.connect("leave-notify-event", self._on_leave)
        self.connect("button-press-event", self._on_click)

    def _on_enter(self, _widget, _event) -> bool:
        badge_enter(self._cm_getter(), self.name)
        return False

    def _on_leave(self, _widget, _event) -> bool:
        badge_leave(self._cm_getter())
        return False

    def _on_click(self, _widget, event) -> bool:
        return badge_click(self._cm_getter(), event, self.name)
