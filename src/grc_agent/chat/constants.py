"""Constants shared across more than one chat_sidebar widget-owning module.

A genuine cross-cutting UI value belongs here rather than in whichever
module happens to use it first — ``_SCROLL_STICK_THRESHOLD`` is read by the
streaming flush, the scroll-position tracker, and zoom-projection's
scroll-settle, none of which owns it more than the others.
"""

from __future__ import annotations

# Stick-to-bottom re-engagement distance: once the user scrolls back to
# within this many pixels of the bottom, following resumes. Computed on every
# vadjustment value-changed (wheel, scrollbar drag, keyboard, touch — every
# scroll source changes the value), so a user scrolled up to read earlier
# messages is never yanked back down on new content.
_SCROLL_STICK_THRESHOLD = 80
