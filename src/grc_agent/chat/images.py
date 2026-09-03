# ruff: noqa: E402
"""Image thumbnail decoding shared by more than one chat_sidebar mixin.

Used by both the transcript renderer (a user message's attached images) and
the composer (attachment chips) — a genuinely shared leaf utility, not owned
by either.
"""

from __future__ import annotations

import contextlib

import gi

gi.require_version("Gtk", "3.0")

from gi.repository import GdkPixbuf, GLib, Gtk


def _pixbuf_from_bytes(data: bytes, max_height: int | None = None) -> GdkPixbuf.Pixbuf | None:
    """Decode image bytes through GdkPixbuf's streaming loader (the standard
    GTK path). When `max_height` is given, the loader's size-prepared hint
    decodes directly at thumbnail scale, so a large photo never allocates a
    full-resolution RGBA buffer just to shrink it. Returns None for
    undecodable bytes so callers can skip the thumbnail rather than fail the
    whole turn."""
    loader = GdkPixbuf.PixbufLoader()
    if max_height is not None:

        def _hint(_loader: GdkPixbuf.PixbufLoader, width: int, height: int) -> None:
            _loader.set_size(max(1, round(width * max_height / height)), max_height)

        loader.connect("size-prepared", _hint)
    try:
        loader.write(data)
        loader.close()
        return loader.get_pixbuf()
    except GLib.Error:
        with contextlib.suppress(GLib.Error):
            loader.close()
        return None


def _thumbnail(data: bytes, height: int) -> Gtk.Image | None:
    """Aspect-preserving thumbnail widget at `height` px, or None for
    undecodable bytes."""
    pixbuf = _pixbuf_from_bytes(data, max_height=height)
    if pixbuf is None:
        return None
    return Gtk.Image.new_from_pixbuf(pixbuf)
