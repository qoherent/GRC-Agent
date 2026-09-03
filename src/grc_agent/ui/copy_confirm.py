# ruff: noqa: E402
"""The one copy-confirmation implementation for every copy button in the app.

Writing text to the clipboard and flipping the button into a transient
"Copied!" state used to be written out twice with divergent timeouts —
the chat transcript reverted after 1500 ms, code blocks after 2 s — so
the confirmation behavior depended on which surface you clicked. Both
call this helper now: one re-arm guard, one revert closure, one destroy
cleanup, one timeout. The canonical revert delay is the chat side's
1500 ms (the high-frequency surface, and the value the transcript tests
pin); the per-context parts that legitimately differ are the idle
tooltip text and whether the button carries a reverting label.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")

from gi.repository import Gdk, GLib, Gtk

# The canonical revert delay in milliseconds. One value, every surface.
COPY_CONFIRM_REVERT_MS = 1500

_ICON_COPIED = "object-select-symbolic"
_ICON_IDLE = "edit-copy-symbolic"


def confirm_copy(
    btn: Gtk.Button | None,
    text: str,
    *,
    idle_tooltip: str,
    revert_label_to: str | None = None,
) -> bool:
    """Write ``text`` to the clipboard and flip ``btn`` into its copied
    state, reverting after one timeout.

    ``btn`` may be ``None`` (clipboard write only). ``idle_tooltip`` is
    the tooltip the button reverts to; ``revert_label_to`` restores a
    text label when the button carries one. Returns ``True`` when the
    clipboard write happened.
    """
    if not text:
        return False
    clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
    clipboard.set_text(text, -1)
    if btn is None:
        return True
    btn.set_tooltip_text("Copied!")
    image = btn.get_image()
    if isinstance(image, Gtk.Image):
        image.set_from_icon_name(_ICON_COPIED, Gtk.IconSize.MENU)
    if revert_label_to is not None and btn.get_label():
        btn.set_label("Copied")

    # Re-arm guard: copying again while a confirmation is pending replaces
    # the pending timeout rather than stacking a second one.
    if getattr(btn, "_copy_timeout_id", None) is not None:
        GLib.source_remove(btn._copy_timeout_id)
        btn._copy_timeout_id = None

    def _revert() -> bool:
        btn._copy_timeout_id = None
        try:
            img = btn.get_image()
            if isinstance(img, Gtk.Image):
                img.set_from_icon_name(_ICON_IDLE, Gtk.IconSize.MENU)
            btn.set_tooltip_text(idle_tooltip)
            if revert_label_to is not None and btn.get_label():
                btn.set_label(revert_label_to)
        except Exception:
            pass
        return False

    btn._copy_timeout_id = GLib.timeout_add(COPY_CONFIRM_REVERT_MS, _revert)
    if not getattr(btn, "_destroy_handler_set", False):
        btn._destroy_handler_set = True
        btn.connect(
            "destroy",
            lambda b: GLib.source_remove(b._copy_timeout_id)
            if getattr(b, "_copy_timeout_id", None)
            else None,
        )
    return True
