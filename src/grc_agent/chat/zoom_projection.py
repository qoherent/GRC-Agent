# ruff: noqa: E402
"""Zoom-projection mixin for ChatSidebar.

Owns projecting the GRC canvas zoom onto the sidebar's own font size as one
absolute, widget-scoped CSS rule (never the screen), and restoring the
scroll anchor and every descendant's cached text metrics after that CSS
change lands. Split out of ``chat_sidebar.py`` by U15 — a GTK-owning mixin,
not a pure-function module, so it still needs a display to test against.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")

from gi.repository import Gdk, GLib, Gtk, Pango

from ..native_canvas import sidebar_font_multiplier
from ..ui.code_block import CodeBlock
from .constants import _SCROLL_STICK_THRESHOLD


class ZoomProjectionMixin:
    """Zoom-projection behavior mixed into ``ChatSidebar``.

    Every method here assumes the full ``ChatSidebar`` instance attributes
    (``self._zoom_css_provider``, ``self._scrolled``, ``self._listbox``, and
    the rest of the widget tree still built on ``ChatSidebar`` itself) —
    this is an organizational split, not an encapsulation boundary.
    """

    def set_zoom_projection(self, zoom_factor: float) -> None:
        """KD2/R9 entry point, wired by desktop_app.py to
        ``NativeCanvasManager.on_zoom_changed``: the chat sidebar's text/UI
        scale is a PURE projection of the canvas zoom via the one committed
        mapping (``sidebar_font_multiplier``). Session-only, never persisted.
        There is no inverse — nothing here ever writes the canvas zoom — so
        the projection cannot loop back on itself."""
        self._apply_sidebar_font_projection(sidebar_font_multiplier(zoom_factor))

    def _apply_sidebar_font_projection(self, multiplier: float) -> None:
        """Apply the projected sidebar font as ONE absolute CSS rule (KTD8).

        The rule lives on a CssProvider scoped to THIS widget's style context
        (never the screen), mirrored on the app-wide single-provider build in
        ui/css.py, so the rescale propagates only through .chat-sidebar's
        inherited font — every sidebar rule in ui/css.py is em-relative, and
        GRC's panels sit outside this style context and are untouched. The
        provider is created once and afterwards only reloaded via
        load_from_data (no remove/re-add), always on the unified main loop —
        this runs on GTK's default GMainContext, exactly where
        on_zoom_changed fires."""
        # Same-value early return (mirrors GRC's own _set_zoom_factor
        # discipline): the projection is a pure function of the multiplier,
        # so re-applying an identical one is a no-op — skip the reload, the
        # metric flush, the re-pin sweep, and the anchor idle entirely.
        if multiplier == self._zoom_css_last_multiplier:
            return
        # Snapshot the near-bottom anchor BEFORE the CSS lands (plan
        # Approach 2): a font inflate changes the list geometry mid-stream,
        # and the snapshot has to describe the pre-relayout viewport.
        adj = self._scrolled.get_vadjustment()
        was_near_bottom = (
            adj.get_upper() - adj.get_page_size() - adj.get_value()
        ) <= _SCROLL_STICK_THRESHOLD
        if self._zoom_css_provider is None:
            # Base measured ONCE at provider creation, before this sidebar's
            # own rule exists, so the measurement reflects exactly the size
            # the sidebar had at rest — including any app-wide font CSS —
            # and multiplier 1.0 restores it (see _measure_theme_font_px).
            self._zoom_css_base_px = self._measure_theme_font_px()
            self._zoom_css_provider = Gtk.CssProvider()
            self.get_style_context().add_provider(
                self._zoom_css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )
        css = (
            f".chat-sidebar {{ font-size: {self._zoom_css_base_px * multiplier:.4f}px; }}"
        )
        self._zoom_css_provider.load_from_data(css.encode("utf-8"))
        self._zoom_css_last_multiplier = multiplier
        # Anchor-preserving re-pin lands after the reload, on one idle.
        GLib.idle_add(self._settle_projection_layout, was_near_bottom)

    def _measure_theme_font_px(self) -> float:
        """The sidebar's effective theme font size, as a CSS px value.

        GTK3 resolves every font to Pango points in the style description
        (verified: a screen-level ``font-size: 18px`` rule reads back as
        10.8pt on a 120 dpi screen), so the resolved size is converted back
        to CSS px with the screen resolution — writing that px value back
        restores the measured font exactly (verified round-trip delta: 0.0).
        A value that is already absolute is device px by definition."""
        font_desc = self.get_style_context().get_font(Gtk.StateFlags.NORMAL)
        size = font_desc.get_size()
        if font_desc.get_size_is_absolute():
            return size / Pango.SCALE
        screen = self.get_screen() or Gdk.Screen.get_default()
        resolution = float(screen.get_resolution()) if screen is not None else -1.0
        # -1 == "unset": GTK applies the same 96 dpi fallback for its own
        # pt/px conversion, so the round-trip stays exact.
        if resolution <= 0:
            resolution = 96.0
        return size / Pango.SCALE * resolution / 72.0

    def _on_zoom_projection_destroy(self, *_args) -> None:
        self._zoom_projection_dead = True

    def _settle_projection_layout(self, was_near_bottom: bool) -> bool:
        """Restore the scroll anchor after a projected font change (R9/R12).

        One-shot idle: re-pin every rendered CodeBlock (their construction
        pins measured the then-current font — stale after the inflate, which
        would clip rows), refresh each widget's cached Pango metrics from its
        settled style, force one synchronous re-allocation of the scrollable
        child (the same mechanism ``_on_expander_toggled`` uses), and then —
        ONLY when the viewport was pinned to the bottom — re-seat the anchor
        at the new bottom.

        This works WITH the single authority: the re-seat is itself a value
        change, so ``_on_scroll_value_changed`` re-derives ``_auto_scroll``
        from geometry (True at the bottom), and the flag is never written
        here directly.

        A silent no-op once destroyed (see _zoom_projection_dead): the idle
        may still be pending when a test tears its window down.
        """
        if self._zoom_projection_dead:
            return False  # one-shot
        self._refresh_descendant_text_metrics()
        self._repin_code_blocks()
        self._scrolled.check_resize()
        if was_near_bottom:
            adj = self._scrolled.get_vadjustment()
            bottom = adj.get_upper() - adj.get_page_size()
            if bottom > 0 and adj.get_value() != bottom:
                adj.set_value(bottom)
        return False  # one-shot

    def _refresh_descendant_text_metrics(self) -> None:
        """Flush each descendant widget's cached Pango metrics to its settled
        style.

        A scoped CSS reload revalidates style contexts lazily, but a widget's
        cached PangoContext (which drives its text layout and preferred size)
        only adopts the new font when GTK runs the style-updated default
        handler during a frame-clock pass — verified live: right after the
        reload the style font is the projected size while the PangoContext
        still measures the old one. Running that same default handler
        (Gtk.Widget.do_style_updated) here makes row requisitions settle
        synchronously, so the anchor restore below computes against final
        geometry instead of a one-frame-old one. GTK runs this identical work
        on every real style change; no widget state is invented here."""
        stack = [self]
        while stack:
            widget = stack.pop()
            if isinstance(widget, Gtk.Container):
                stack.extend(widget.get_children())
            Gtk.Widget.do_style_updated(widget)

    def _repin_code_blocks(self) -> None:
        """Re-pin every CodeBlock rendered under the message list (R12).

        The listbox is the one bookkeeping structure that owns every rendered
        row, so the sweep is the same uniform subtree walk MarkdownView uses
        for re-wrapping prose — no per-widget registries. Blocks created
        after a rescale self-correct via their one-shot first-allocate re-pin
        (see CodeBlock._on_style_updated)."""
        stack = [self._listbox]
        while stack:
            widget = stack.pop()
            if isinstance(widget, CodeBlock):
                widget.repin_height()
            elif isinstance(widget, Gtk.Container):
                stack.extend(widget.get_children())

