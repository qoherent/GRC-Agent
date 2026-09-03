"""Constants shared across more than one chat_sidebar widget-owning module.

A genuine cross-cutting UI value belongs here rather than in whichever
module happens to use it first — ``_SCROLL_STICK_THRESHOLD`` is read by the
streaming flush, the scroll-position tracker, and zoom-projection's
scroll-settle, none of which owns it more than the others.
"""

from __future__ import annotations

from gi.repository import Gtk

# Stick-to-bottom re-engagement distance: once the user scrolls back to
# within this many pixels of the bottom, following resumes. Computed on every
# vadjustment value-changed (wheel, scrollbar drag, keyboard, touch — every
# scroll source changes the value), so a user scrolled up to read earlier
# messages is never yanked back down on new content.
_SCROLL_STICK_THRESHOLD = 80


def _is_near_bottom(adj: Gtk.Adjustment) -> bool:
    """Whether the scrolled view driven by ``adj`` sits within
    ``_SCROLL_STICK_THRESHOLD`` pixels of the bottom.

    The one owner of the near-bottom test: the scroll-position tracker (does
    the user still want stick-to-bottom?), the streaming flush, and
    zoom-projection's anchor snapshot all ask the same question about the
    same adjustment, so the comparison lives beside the threshold constant
    rather than being rewritten at each call site.
    """
    return (
        adj.get_upper() - adj.get_page_size() - adj.get_value()
    ) <= _SCROLL_STICK_THRESHOLD
