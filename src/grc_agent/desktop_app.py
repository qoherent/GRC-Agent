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

from gi.repository import Gdk, GLib, Gtk


def _apply_dark_theme_patches() -> None:
    pass


from grc_agent.adapter import get_gui_platform, gui_application_cls
from grc_agent.agent_factory import build_interactive_agent
from grc_agent.chat_sidebar import ChatSidebar
from grc_agent.native_canvas import NativeCanvasManager, NativeFlowgraphProxy

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


def _apply_canvas_zoom(canvas: NativeCanvasManager, delta: float) -> None:
    if not (canvas.drawing_area and hasattr(canvas.drawing_area, "zoom_factor")):
        return
    current = canvas.drawing_area.zoom_factor
    canvas.drawing_area.zoom_factor = max(0.5, min(3.0, current + delta))
    canvas.drawing_area._update_after_zoom = True
    canvas.drawing_area.queue_draw()


def _on_window_key_press(
    _window: Gtk.Window,
    event: Gdk.EventKey,
    canvas: NativeCanvasManager,
    sidebar: ChatSidebar,
) -> bool:
    accel = event.state & Gdk.ModifierType.CONTROL_MASK
    if not accel:
        return False
    key = event.keyval
    if key in (Gdk.KEY_plus, Gdk.KEY_KP_Add, Gdk.KEY_equal):
        _update_scale(_SCALE_STEP)
        _apply_canvas_zoom(canvas, _SCALE_STEP)
        sidebar.set_status(f"Zoom: {int(_scale_factor * 100)}%")
        return True
    if key in (Gdk.KEY_minus, Gdk.KEY_KP_Subtract):
        _update_scale(-_SCALE_STEP)
        _apply_canvas_zoom(canvas, -_SCALE_STEP)
        sidebar.set_status(f"Zoom: {int(_scale_factor * 100)}%")
        return True
    if key == Gdk.KEY_0:
        _reset_scale()
        if canvas.drawing_area and hasattr(canvas.drawing_area, "zoom_factor"):
            canvas.drawing_area.zoom_factor = 1.0
            canvas.drawing_area._update_after_zoom = True
            canvas.drawing_area.queue_draw()
        sidebar.set_status("Zoom reset")
        return True
    return False


def _show_status(sidebar: ChatSidebar, msg: str, *, error: bool = False) -> None:
    sidebar.set_status(msg, error=error)


def _on_new_session(sidebar: ChatSidebar) -> None:
    sidebar.clear_messages()
    _show_status(sidebar, "Chat history cleared.")


def _sync_sidebar(canvas: NativeCanvasManager, sidebar: ChatSidebar) -> None:
    """Update sidebar to match the current GRC page state."""
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


def build_app() -> tuple[Gtk.Window, NativeCanvasManager, ChatSidebar, NativeFlowgraphProxy]:  # noqa: C901
    _apply_global_css()
    platform = get_gui_platform()
    Application = gui_application_cls()
    _apply_dark_theme_patches()
    argv = [a for a in sys.argv[1:] if a.endswith(GRC_EXTENSIONS)]
    grc_app = Application(argv, platform)
    grc_app.register(None)
    grc_app.activate()

    gtk_app = Gtk.Application.get_default()
    window = gtk_app.get_active_window() if gtk_app else None
    if not window:
        print("Failed to get GRC active MainWindow")
        sys.exit(1)

    main_widget = window.main
    parent = main_widget.get_parent()
    outer_paned = Gtk.Paned.new(Gtk.Orientation.HORIZONTAL)
    parent.remove(main_widget)
    outer_paned.pack1(main_widget, resize=True, shrink=False)

    sidebar = ChatSidebar()
    sidebar.set_size_request(350, -1)
    outer_paned.pack2(sidebar, resize=True, shrink=False)
    parent.pack_start(outer_paned, expand=True, fill=True, padding=0)

    agent, model_error = build_interactive_agent()
    sidebar.set_agent(agent)
    if model_error:
        sidebar.set_status(f"Model warning: {model_error} (using defaults)", error=True)

    canvas = NativeCanvasManager(window, platform)
    canvas.app = grc_app
    sidebar.set_blocks_expanded(canvas._blocks_visible)
    canvas.on_graphs_changed = lambda: _sync_sidebar(canvas, sidebar)
    canvas.setup_signal_handlers()
    proxy = NativeFlowgraphProxy(canvas)
    sidebar.set_flowgraph_proxy(proxy)

    _sync_sidebar(canvas, sidebar)

    window.connect("key-press-event", _on_window_key_press, canvas, sidebar)

    sidebar.connect("new-session-clicked", lambda *_: _on_new_session(sidebar))
    sidebar.connect("toggle-blocks-panel", lambda *_: sidebar.set_blocks_expanded(canvas.toggle_blocks_panel()))

    def _set_pane_positions() -> bool:
        w = window.get_allocated_width()
        h = window.get_allocated_height()
        if w > 100:
            outer_paned.set_position(int(w * 0.70))
            main_widget.set_position(int(w * 0.70 * 0.86))
        if h > 100 and hasattr(window, "left"):
            window.left.set_position(int(h * 0.78))
        return False

    GLib.idle_add(_set_pane_positions)

    return window, canvas, sidebar, proxy


def main() -> None:
    window, canvas, sidebar, proxy = build_app()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def _shutdown() -> None:
        loop.stop()

    window.connect("destroy", lambda *_: _shutdown())
    for sig in (signal.SIGTERM, signal.SIGINT):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, lambda: (_shutdown(), False)[1])

    window.show_all()
    loop.run_forever()
    loop.close()


if __name__ == "__main__":
    main()
