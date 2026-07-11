# ruff: noqa: E402
import fcntl
import json
import os
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import gi

gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
gi.require_version('PangoCairo', '1.0')
from gi.repository import Gdk, GLib, Gtk
from gnuradio import gr
from gnuradio.grc.gui.Platform import Platform
from gnuradio.grc.gui.DrawingArea import DrawingArea
from gnuradio.grc.gui.Application import Application

# The canvas must show only the flowgraph's scrollable drawing area — no
# menu bar, toolbar, block-library tree, or console/variable-editor pane.
# GRC's MainWindow layout isn't a fixed contract (it varies with the
# variable_editor_sidebar preference), so instead of hardcoding pane
# indices, we walk up from the DrawingArea to the window and hide every
# sibling along the way. Whatever GRC packs around the canvas, this
# isolates it without depending on the exact widget tree shape.
#
# The window starts at this size, but web.py's /grc/canvas/resize forwards
# the dashboard's actual canvas-pane dimensions here as soon as the browser
# reports them (see start_control_server below) — a size mismatch here isn't
# just cosmetic: it clips the flowgraph AND pushes GRC's own scrollbars
# (which live at the window's right/bottom edge) outside the visible
# iframe viewport, making the canvas both cropped and unpannable.
CANVAS_WIDTH = 1200
CANVAS_HEIGHT = 900
CANVAS_MIN_WIDTH = 400
CANVAS_MIN_HEIGHT = 300
CANVAS_CONTROL_PORT = 7933


class CanvasControlContext:
    """Shared, mutable state for the control HTTP server. The server binds
    immediately at startup — before GRC's own Platform.build_library() call,
    which alone can take several hundred ms — so a resize request that
    arrives before the window/flowgraph exist yet is buffered here instead
    of silently lost. Same reasoning covers reload requests: one that
    arrives before anything is loaded is simply a no-op, since the very
    first load will already pick up the current file from disk."""

    def __init__(self, grc_file_path):
        self.grc_file_path = grc_file_path
        self.window = None
        self.drawing_area = None
        self.platform = None
        self.pending_size = None

    def apply_resize(self, width, height):
        if self.window:
            self.window.resize(width, height)
            self.window.move(0, 0)
        else:
            self.pending_size = (width, height)
        return False

    def apply_pending_size(self):
        if self.window and self.pending_size:
            width, height = self.pending_size
            self.window.resize(width, height)
            self.window.move(0, 0)

    def apply_reload(self):
        """Re-read the flowgraph from disk into the live DrawingArea —
        used when an agent tool call (change_graph) edits the file out
        from under this already-running canvas, so the visible GTK canvas
        doesn't silently drift out of sync with what the chat just told
        the user changed."""
        if not (self.window and self.drawing_area and self.platform):
            print("Reload requested before the canvas finished loading — ignoring.")
            return False
        try:
            new_data = self.platform.parse_flow_graph(self.grc_file_path)
            flow_graph = self.drawing_area._flow_graph
            flow_graph.import_data(new_data)
            flow_graph.update()
            # Mirrors what a zoom change does (DrawingArea.draw() only
            # recomputes labels/shapes when this flag is set) — the
            # cheapest way to force a full relayout using GRC's own
            # existing redraw path rather than reaching for a cairo
            # context ourselves outside of a real draw callback.
            self.drawing_area._update_after_zoom = True
            self.drawing_area.queue_draw()
            print("Reloaded flowgraph from disk after an external edit")
        except Exception as e:
            print("Failed to reload flowgraph from disk:", e)
        return False


def start_control_server(ctx):
    """Background HTTP listener so the dashboard page (resize) and the web
    server (reload, after an agent-driven edit) can act on this canvas
    process. GTK calls must happen on the main thread, so handlers only
    schedule work via GLib.idle_add."""

    class ControlHandler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # keep stdout reserved for canvas.log's real diagnostics

        def _read_json(self):
            length = int(self.headers.get("Content-Length", 0))
            return json.loads(self.rfile.read(length)) if length else {}

        def do_POST(self):
            try:
                if self.path == "/resize":
                    data = self._read_json()
                    width = max(CANVAS_MIN_WIDTH, int(data["width"]))
                    height = max(CANVAS_MIN_HEIGHT, int(data["height"]))
                    GLib.idle_add(ctx.apply_resize, width, height)
                    self.send_response(200)
                elif self.path == "/reload":
                    GLib.idle_add(ctx.apply_reload)
                    self.send_response(200)
                else:
                    self.send_response(404)
            except Exception:
                self.send_response(400)
            self.end_headers()

    # A stale canvas_app.py from a killed/crashed previous run (or the test
    # suite — see tests/test_web_app.py) can still be holding this port for
    # a moment after web.py's own cleanup sweep. An uncaught bind failure
    # here used to crash this whole process before Gtk.main() ever ran,
    # leaving the dashboard's canvas permanently blank with zero indication
    # why. Retry briefly instead of taking the whole app down with it.
    server = None
    for attempt in range(10):
        try:
            server = HTTPServer(("127.0.0.1", CANVAS_CONTROL_PORT), ControlHandler)
            break
        except OSError as e:
            print(f"Control server bind attempt {attempt + 1}/10 failed: {e}")
            time.sleep(0.3)
    if server is None:
        print(
            "Could not bind the canvas control server after retries — "
            "live resize and chat-driven reload won't work this session, "
            "but the canvas itself will still render."
        )
        return
    threading.Thread(target=server.serve_forever, daemon=True).start()


def main():
    if len(sys.argv) < 2:
        print("Usage: python canvas_app.py <path_to_grc>")
        sys.exit(1)

    grc_file_path = os.path.abspath(sys.argv[1])
    print(f"Starting canvas app for: {grc_file_path}")

    # Bind the control server immediately — before GRC's own
    # Platform.build_library() call below, which alone can take several
    # hundred ms — so a resize request that arrives while GRC is still
    # loading its block catalog is buffered (see CanvasControlContext)
    # instead of simply being missed.
    ctx = CanvasControlContext(grc_file_path)
    start_control_server(ctx)

    # Set up GRC Platform & Application Context
    p = Platform(
        version=gr.version(),
        version_parts=(gr.major_version(), gr.api_version(), gr.minor_version()),
        prefs=gr.prefs(),
        install_prefix=gr.prefix()
    )
    p.build_library()

    # Pass the flowgraph path directly to the native Application to load it inside the MainWindow
    app = Application([grc_file_path], p)
    app.register(None)
    app.activate()

    # Get the active MainWindow created by the application
    window = Gtk.Application.get_default().get_active_window()
    if not window:
        print("Failed to get GRC active MainWindow")
        sys.exit(1)

    # Strip the native title bar/min/max/close controls — this is meant to
    # render as a canvas embedded in an iframe, not a desktop window.
    window.set_decorated(False)

    # Only a MIN_SIZE floor — no MAX_SIZE. A hard max would fight the
    # dynamic resize-to-pane-size below just as much as no constraint at
    # all would let the window's natural size request (e.g. from a not-yet-
    # hidden console pane) balloon past the visible iframe. The floor just
    # keeps a degenerate 0x0 report from wedging the window unusably small.
    geometry = Gdk.Geometry()
    geometry.min_width = CANVAS_MIN_WIDTH
    geometry.min_height = CANVAS_MIN_HEIGHT
    window.set_geometry_hints(None, geometry, Gdk.WindowHints.MIN_SIZE)
    window.resize(CANVAS_WIDTH, CANVAS_HEIGHT)
    window.move(0, 0)
    window.connect("destroy", Gtk.main_quit)

    # Recursively find the DrawingArea widget to attach auto-save triggers
    def find_drawing_area(widget):
        if widget.__class__.__name__ == 'DrawingArea':
            return widget
        if hasattr(widget, 'get_children'):
            for child in widget.get_children():
                res = find_drawing_area(child)
                if res:
                    return res
        return None

    drawing_area = find_drawing_area(window)
    if not drawing_area:
        print("Failed to locate GRC DrawingArea widget")

    # Make the control server's buffered state usable now that the window
    # actually exists, and apply anything that arrived before it did.
    ctx.window = window
    ctx.drawing_area = drawing_area
    ctx.platform = p
    ctx.apply_pending_size()

    # Hide every sibling on the path from the DrawingArea up to the window
    # (menu bar, toolbar, block-library tree, console/variable-editor pane,
    # notebook tabs bar, ...) so only the flowgraph's scrolled canvas shows,
    # no matter how GRC's MainWindow happens to be laid out.
    def isolate_drawing_area(root_window, target):
        node = target
        parent = node.get_parent()
        while parent is not None and parent is not root_window:
            if isinstance(parent, Gtk.Notebook):
                parent.set_show_tabs(False)
                parent.set_show_border(False)
            for sibling in parent.get_children():
                if sibling is not node:
                    sibling.hide()
            node = parent
            parent = node.get_parent()

    if drawing_area:
        try:
            isolate_drawing_area(window, drawing_area)
        except Exception as e:
            print("Failed to isolate GRC canvas:", e)

    # Broadway sometimes only honors window placement once the client has
    # actually connected, which happens after Gtk.main() starts pumping the
    # loop — re-assert position/size on the first idle iteration as a
    # belt-and-suspenders measure against the "window initialized
    # out-of-bounds" race.
    def _reassert_geometry():
        if ctx.pending_size:
            ctx.apply_pending_size()
        else:
            window.move(0, 0)
            window.resize(CANVAS_WIDTH, CANVAS_HEIGHT)
        return False

    GLib.idle_add(_reassert_geometry)

    # Cross-process lock, shared with adapter.py's own save path
    # (write_flow_graph_atomic uses the exact same `.grc_agent/<name>.lock`
    # convention) — without it, a block drag released here has no
    # coordination with a concurrent agent-driven edit going through the
    # web server, and either write can clobber the other.
    lock_path = Path(grc_file_path).parent / ".grc_agent" / (Path(grc_file_path).name + ".lock")
    lock_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)

    def _do_trigger_reload():
        print("Auto-saving and reloading flowgraph...")
        try:
            if drawing_area and hasattr(drawing_area, "_flow_graph"):
                with lock_path.open("a", encoding="utf-8") as lock_file:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                    try:
                        # save_flow_graph(filename, flow_graph) — filename first.
                        p.save_flow_graph(grc_file_path, drawing_area._flow_graph)
                    finally:
                        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            urllib.request.urlopen("http://localhost:7932/grc/reload")
        except Exception as e:
            print("Failed to trigger reload:", e)
        return False

    def trigger_reload():
        # Deferred to an idle callback so the click/drag that triggered
        # this (a blocking network round-trip plus a full flow-graph
        # re-parse) doesn't freeze the GTK main loop before the
        # selection/move the user just made has had a chance to redraw.
        GLib.idle_add(_do_trigger_reload)

    # 1. Save/reload when dragging/moving blocks (button release)
    def on_button_release(widget, event):
        trigger_reload()
        return False

    if drawing_area:
        drawing_area.connect("button-release-event", on_button_release)

    # 2. Save/reload when properties dialogs are closed (parameter edits)
    def on_window_added(application, win):
        # We only want to listen to properties dialog windows (not the main window itself)
        if win != window:
            print(f"Properties dialog added to context: {win}")
            # GTK positions new dialogs (e.g. PropsDialog) somewhere within
            # the full CANVAS_WIDTH x CANVAS_HEIGHT coordinate space —
            # which can land outside the narrow visible iframe pane in the
            # dashboard, since the pane is almost always smaller than the
            # full canvas. The dialog still opens and works, it's just
            # invisible off past the edge of the pane. Pin it near the
            # origin so it's visible regardless of pane width.
            def _position_dialog():
                win.move(20, 20)
                return False
            win.move(20, 20)
            GLib.idle_add(_position_dialog)

            def on_window_destroy(w, event=None):
                trigger_reload()
                return False
            win.connect("destroy", on_window_destroy)

    app.connect("window-added", on_window_added)

    Gtk.main()


if __name__ == "__main__":
    main()
