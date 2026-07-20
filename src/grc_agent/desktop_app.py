# ruff: noqa: E402
import asyncio
import contextlib
import signal
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")

import gbulb

gbulb.install(gtk=True)

# Patch gbulb to avoid AssertionError in ReadTransport._loop_reading when transports close/change
try:
    import gbulb.transports
    _original_loop_reading = gbulb.transports.ReadTransport._loop_reading

    def _patched_loop_reading(self, fut=None):
        if fut is not None and self._read_fut is not fut and not (self._read_fut is None and self._closing):
            return
        return _original_loop_reading(self, fut)

    gbulb.transports.ReadTransport._loop_reading = _patched_loop_reading
except Exception as e:
    print(f"Warning: Failed to patch gbulb transports: {e}")

from gi.repository import Gdk, GLib, Gtk

from grc_agent.adapter import (
    get_blocks_panel_visibility,
    get_gui_platform,
    gui_application_cls,
    register_execution_messenger,
)
from grc_agent.agent_factory import (
    build_agent_from_cfg,
    build_interactive_agent,
    preflight_from_cfg,
)
from grc_agent.chat_sidebar import ChatSidebar
from grc_agent.exec_monitor import ExecutionErrorMonitor
from grc_agent.native_canvas import NativeCanvasManager, NativeFlowgraphProxy
from grc_agent.settings import load_settings

GRC_EXTENSIONS = (".grc", ".yml", ".yaml")

_GLOBAL_CSS_TEMPLATE = """
* { font-size: %(base)dpx; }
.toolbar-btn { padding: 4px 8px; }
.validation-valid { color: #2e7d32; font-weight: bold; }
.validation-invalid { color: #c62828; font-weight: bold; }
"""

_BASE_FONT_SIZE = 13
_SCALE_MIN = 0.8
_SCALE_MAX = 2.0
_SCALE_STEP = 0.1
_scale_factor = 1.4
_css_provider: Gtk.CssProvider | None = None


def _apply_global_css() -> None:
    global _css_provider
    screen = Gdk.Screen.get_default()
    if screen is None:
        return
    base = int(_BASE_FONT_SIZE * _scale_factor)
    css = _GLOBAL_CSS_TEMPLATE % {"base": base}
    if _css_provider is None:
        _css_provider = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_screen(
            screen, _css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
    _css_provider.load_from_data(css.encode("utf-8"))


def _update_scale(delta: float) -> None:
    global _scale_factor
    _scale_factor = max(_SCALE_MIN, min(_SCALE_MAX, _scale_factor + delta))
    _apply_global_css()


def _reset_scale() -> None:
    global _scale_factor
    _scale_factor = 1.0
    _apply_global_css()


def _apply_canvas_zoom(canvas: NativeCanvasManager, direction: str) -> None:
    """Delegate to GRC's own DrawingArea.zoom_in()/zoom_out()/reset_zoom()
    (native, multiplicative, clamped 0.1-5.0) instead of hand-rolling zoom
    math. GRC's own View menu triggers these exact same methods for the
    identical Ctrl+Plus/Minus/0 accelerators (gnuradio/grc/gui/Actions.py's
    ZOOM_IN/ZOOM_OUT/ZOOM_RESET) — delegating keeps keyboard zoom and menu
    zoom in sync instead of silently diverging (previously additive ±0.1
    clamped 0.5-3.0 here vs. multiplicative x1.2 clamped 0.1-5.0 natively)."""
    da = canvas.drawing_area
    if da is None:
        return
    if direction == "in" and hasattr(da, "zoom_in"):
        da.zoom_in()
    elif direction == "out" and hasattr(da, "zoom_out"):
        da.zoom_out()
    elif direction == "reset" and hasattr(da, "reset_zoom"):
        da.reset_zoom()


def _on_window_key_press(
    _window: Gtk.Window,
    event: Gdk.EventKey,
    canvas: NativeCanvasManager,
    sidebar: ChatSidebar,
) -> bool:
    focus_widget = _window.get_focus()
    accel = event.state & Gdk.ModifierType.CONTROL_MASK
    key = event.keyval

    # Ctrl+A: text selection on chat widgets, overriding GRC's global
    # select-all. Only consumed when a text widget is focused — canvas Ctrl+A
    # falls through (returns False below) and reaches GRC's select-all.
    if accel and key in (Gdk.KEY_a, Gdk.KEY_A) and focus_widget:
        if isinstance(focus_widget, (Gtk.Entry, Gtk.Label)):
            focus_widget.select_region(0, -1)
            return True
        if isinstance(focus_widget, Gtk.TextView):
            buf = focus_widget.get_buffer()
            buf.select_range(buf.get_start_iter(), buf.get_end_iter())
            return True

    # All non-Ctrl keys propagate to GTK's native focus dispatch, which routes
    # them through the widget's IM-context path (so CJK/IME input composes
    # correctly instead of being re-emitted raw).
    if not accel:
        return False

    if key in (Gdk.KEY_plus, Gdk.KEY_KP_Add, Gdk.KEY_equal):
        _update_scale(_SCALE_STEP)
        _apply_canvas_zoom(canvas, "in")
        sidebar.set_status(f"Zoom: {int(_scale_factor * 100)}%")
        return True
    if key in (Gdk.KEY_minus, Gdk.KEY_KP_Subtract):
        _update_scale(-_SCALE_STEP)
        _apply_canvas_zoom(canvas, "out")
        sidebar.set_status(f"Zoom: {int(_scale_factor * 100)}%")
        return True
    if key == Gdk.KEY_0:
        _reset_scale()
        _apply_canvas_zoom(canvas, "reset")
        sidebar.set_status("Zoom reset")
        return True
    return False


def _show_status(sidebar: ChatSidebar, msg: str, *, error: bool = False) -> None:
    sidebar.set_status(msg, error=error)


def _on_new_session(sidebar: ChatSidebar) -> None:
    sidebar.clear_messages()
    _show_status(sidebar, "")


def _sync_sidebar(canvas: NativeCanvasManager, sidebar: ChatSidebar) -> None:
    """Update sidebar to match the current GRC page state."""
    sidebar.stop_chat()
    page = canvas.current_page
    name = None
    if page:
        p = page.file_path
        if p:
            name = Path(p).stem
        elif hasattr(page.flow_graph, "get_option"):
            with contextlib.suppress(Exception):
                name = page.flow_graph.get_option("title") or page.flow_graph.get_option("id")
        if not name:
            name = "untitled"
    sidebar.set_active_graph(name)
    fg = canvas.current_flow_graph
    if fg is not None:
        sidebar.set_input_enabled(True)
    else:
        sidebar.set_input_enabled(False)
    sidebar.sync_to_file(page.file_path if page else None)


def _show_fatal_error(title: str, message: str) -> None:
    """Native GTK error dialog for failures before the main window exists (so
    there's no sidebar/status-bar to report through yet). GTK is already
    confirmed working by the time this can be called (we're well past this
    module's own import-time gi.require_version calls), so a real dialog is
    always safe here, unlike a raw traceback."""
    dialog = Gtk.MessageDialog(
        transient_for=None,
        flags=Gtk.DialogFlags.MODAL,
        message_type=Gtk.MessageType.ERROR,
        buttons=Gtk.ButtonsType.OK,
        text=title,
    )
    dialog.format_secondary_text(message)
    dialog.run()
    dialog.destroy()


def build_app() -> tuple[Gtk.Window, NativeCanvasManager, ChatSidebar, NativeFlowgraphProxy]:  # noqa: C901
    _apply_global_css()
    try:
        platform = get_gui_platform()
        Application = gui_application_cls()
    except Exception as exc:
        _show_fatal_error(
            "GNU Radio not found",
            f"grc-agent couldn't load GNU Radio Companion: {exc}\n\n"
            "Make sure GNU Radio 3.10+ is installed (e.g. `sudo apt install "
            "gnuradio gnuradio-dev`) and that this app's virtual environment "
            "was created with --system-site-packages. See the README's "
            "Installation section for setup instructions.",
        )
        sys.exit(1)
    argv = [a for a in sys.argv[1:] if a.endswith(GRC_EXTENSIONS)]
    grc_app = Application(argv, platform)
    grc_app.register(None)
    grc_app.activate()

    gtk_app = Gtk.Application.get_default()
    window = gtk_app.get_active_window() if gtk_app else None
    if not window:
        _show_fatal_error(
            "GNU Radio Companion window not found",
            "grc-agent activated GNU Radio Companion but could not find its "
            "main window. This usually indicates an incompatible GNU Radio "
            "version — see the README's Installation section.",
        )
        sys.exit(1)

    main_widget = window.main
    parent = main_widget.get_parent()
    outer_paned = Gtk.Paned.new(Gtk.Orientation.HORIZONTAL)
    parent.remove(main_widget)
    outer_paned.pack1(main_widget, resize=True, shrink=False)

    sidebar = ChatSidebar()
    sidebar.set_size_request(1, -1)
    outer_paned.pack2(sidebar, resize=True, shrink=True)
    parent.pack_start(outer_paned, expand=True, fill=True, padding=0)

    agent, model_error = build_interactive_agent()
    sidebar.set_agent(agent)
    # Wire the live-swap entry point. The Settings dialog's Save handler calls
    # this after a successful save to rebuild the Agent in-place from the
    # newly-written .env — eliminating the restart requirement that used to
    # silently keep the running agent on the old provider ("backend still kept
    # calling ollama cloud" after a swap to openrouter).
    sidebar.set_rebuild_agent_callback(lambda: build_agent_from_cfg(load_settings()))
    if model_error:
        sidebar.set_status(f"Model warning: {model_error} (using defaults)", error=True)
    # NOTE: the startup connection preflight is scheduled from main() AFTER
    # window.show_all(), so the window appears immediately instead of being
    # delayed up to 5s by a sync HTTP probe (see _startup_preflight).

    exec_monitor = ExecutionErrorMonitor(on_error=sidebar.notify_run_failure)
    register_execution_messenger(exec_monitor.handle_message)

    canvas = NativeCanvasManager(window, platform)
    canvas.app = grc_app
    sidebar.set_blocks_expanded(canvas._blocks_visible)
    canvas.on_graphs_changed = lambda: _sync_sidebar(canvas, sidebar)
    canvas.on_sync_failed = lambda msg: sidebar.set_status(msg, error=True)
    canvas.setup_signal_handlers()
    proxy = NativeFlowgraphProxy(canvas, exec_monitor=exec_monitor)
    sidebar.set_flowgraph_proxy(proxy)

    _sync_sidebar(canvas, sidebar)

    window.connect("key-press-event", _on_window_key_press, canvas, sidebar)

    sidebar.connect("new-session-clicked", lambda *_: _on_new_session(sidebar))

    def _on_toggle_blocks(*_):
        expanded = canvas.toggle_blocks_panel()
        sidebar.set_blocks_expanded(expanded)
        w_main = main_widget.get_allocated_width()
        if expanded:
            main_widget.set_position(int(w_main * 0.78))
        else:
            main_widget.set_position(w_main)

    sidebar.connect("toggle-blocks-panel", _on_toggle_blocks)

    def _set_pane_positions() -> bool:
        w = window.get_allocated_width()
        h = window.get_allocated_height()
        if w > 100:
            outer_paned.set_position(int(w * 0.70))
            w_main = int(w * 0.70)
            if get_blocks_panel_visibility():
                main_widget.set_position(int(w_main * 0.78))
            else:
                main_widget.set_position(w_main)
        if h > 100 and hasattr(window, "left"):
            window.left.set_position(int(h * 0.78))
        return False

    GLib.idle_add(_set_pane_positions)

    return window, canvas, sidebar, proxy


async def _startup_preflight(sidebar: ChatSidebar) -> None:
    """Run after window.show_all() — surfaces a non-blocking status-bar
    warning if the configured chat backend is unreachable. Bounded at 5s
    inside preflight_from_cfg. Running it via asyncio.to_thread keeps the
    gbulb-unified main loop responsive (chat streaming, indexing polls,
    canvas syncs all keep firing) instead of the old sync call that
    delayed window.show_all() by up to 5s before any window appeared."""
    try:
        cfg = load_settings()
        err = await asyncio.to_thread(preflight_from_cfg, cfg)
    except Exception as exc:
        err = f"preflight raised: {exc}"
    if err:
        provider = cfg.get("provider", "?") if "cfg" in locals() else "?"
        sidebar.set_status(
            f"Cannot reach {provider} backend ({err}). The first message may fail.",
            error=True,
        )


def main() -> None:
    window, canvas, sidebar, proxy = build_app()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def _shutdown() -> None:
        sidebar.shutting_down()
        sidebar.stop_chat()

        async def _async_cleanup():
            tasks = [t for t in asyncio.all_tasks(loop) if t is not asyncio.current_task()]
            for t in tasks:
                t.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            loop.stop()

        asyncio.ensure_future(_async_cleanup())

    window.connect("destroy", lambda *_: _shutdown())
    for sig in (signal.SIGTERM, signal.SIGINT):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, lambda: (_shutdown(), False)[1])

    window.show_all()
    # Schedule the startup backend reachability check AFTER the window is
    # visible — the user sees the app launch immediately, and the (bounded
    # 5s) probe reports to the status bar when it returns.
    asyncio.ensure_future(_startup_preflight(sidebar))
    loop.run_forever()
    loop.close()


if __name__ == "__main__":
    main()
