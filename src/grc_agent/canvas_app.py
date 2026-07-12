# ruff: noqa: E402
import fcntl
import hashlib
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

# adapter is the sole importer of gnuradio (core *and* gui); this subprocess
# only needs gi/GTK directly. Lazy accessors keep the import side-effect-free.
from grc_agent.adapter import (
    disable_native_undo_redo,
    flow_graph_content_hash,
    get_gui_platform,
    gui_application_cls,
    push_undo_snapshot,
    write_flow_graph_atomic,
)

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


def _sha256_file(path):
    """Best-effort hash of a file's current bytes (None if unreadable)."""
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


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
        # Flipped to True once Gtk.main() is pumping and the GTK client has
        # connected to the Broadway display — grc_open polls GET /ready so it
        # doesn't point the dashboard iframe at Broadway during the multi-
        # second platform build, which would otherwise make broadway.js fire
        # an unrecoverable alert("disconnected").
        self.ready = False
        # SHA-256 of the on-disk file as this process last saw it. A drag-save
        # compares the current disk content to this: if they differ, the agent
        # (or another writer) changed the file since this canvas last reloaded,
        # so writing our now-stale in-memory graph would clobber that edit.
        self.last_disk_hash = None
        # Hash of flow_graph_content_hash(the in-memory graph) as of the last
        # time it was known to match disk (startup, or a successful
        # reload/drag-save) — deliberately a SEPARATE value from
        # last_disk_hash (which hashes raw file bytes): GRC's own
        # parse->import_data->update() round-trip can fill in a default
        # field that wasn't explicitly present before (live-confirmed: a
        # freshly-added block gained an explicit `rotation: 0` after a
        # reload it didn't have right after creation), so comparing a
        # re-exported in-memory hash against a raw-bytes hash produces
        # false-positive "unsynced edit" hits on every such reload. Using
        # the SAME serialization on both sides of this comparison (see
        # _check_for_unsynced_edit below) avoids that.
        self.last_synced_export_hash = None

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
            self.last_disk_hash = _sha256_file(self.grc_file_path)
            self.last_synced_export_hash = flow_graph_content_hash(flow_graph)
            print("Reloaded flowgraph from disk after an external edit")
        except Exception as e:
            print("Failed to reload flowgraph from disk:", e)
        return False


def start_control_server(ctx, port):
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

        def do_GET(self):
            # Readiness probe: 200 once Gtk.main() is pumping (so Broadway has
            # a GTK client connected), 503 while still building the platform.
            if self.path == "/ready":
                self.send_response(200 if ctx.ready else 503)
                self.end_headers()
                return
            self.send_response(404)
            self.end_headers()

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
            server = HTTPServer(("127.0.0.1", port), ControlHandler)
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
        print("Usage: python canvas_app.py <path_to_grc> [control_port] [web_port]")
        sys.exit(1)

    grc_file_path = os.path.abspath(sys.argv[1])
    control_port = int(sys.argv[2]) if len(sys.argv) > 2 else CANVAS_CONTROL_PORT
    # The web server's port, so the canvas autosave can ping /grc/reload and
    # the web process picks up the new in-memory copy. Passed explicitly by
    # web.py (no hardcoded port coupling).
    web_port = int(sys.argv[3]) if len(sys.argv) > 3 else 7932
    print(f"Starting canvas app for: {grc_file_path}")

    # Bind the control server immediately — before GRC's own
    # Platform.build_library() call below, which alone can take several
    # hundred ms — so a resize request that arrives while GRC is still
    # loading its block catalog is buffered (see CanvasControlContext)
    # instead of simply being missed.
    ctx = CanvasControlContext(grc_file_path)
    start_control_server(ctx, control_port)

    # Set up GRC Platform & Application Context (via adapter — the sole
    # gnuradio importer).
    p = get_gui_platform()

    # Pass the flowgraph path directly to the native Application to load it inside the MainWindow
    Application = gui_application_cls()
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

    # GRC's native Ctrl+Z/Ctrl+Y stays reachable even with the chrome hidden
    # (its accelerators are wired at the Gtk.Application level, live-
    # confirmed) but never touches disk — disable it so there's exactly one
    # undo/redo history (the shared, disk-synced one in adapter.py), not two
    # silently-diverging ones. See disable_native_undo_redo's own docstring.
    try:
        disable_native_undo_redo()
    except Exception as e:
        print("Failed to disable native undo/redo:", e)

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
                        # Lost-update guard: if the on-disk file changed since
                        # this canvas last reloaded it (an agent change_graph
                        # wrote in between), our in-memory graph is stale and
                        # writing it would silently clobber that edit. Drop the
                        # drag-save; the queued apply_reload from the agent's
                        # notify will bring this canvas current instead.
                        #
                        # Fail CLOSED: only proceed when we can positively
                        # confirm the disk still matches our last-known state.
                        # Any case where that can't be confirmed (no baseline
                        # yet, or a transient read failure) is treated the same
                        # as a confirmed mismatch — skip this save attempt.
                        # This is a one-shot skip, not a deadlock: the next
                        # drag or the next successful reload will get a fresh
                        # hash pair and can proceed normally.
                        current_hash = _sha256_file(grc_file_path)
                        if ctx.last_disk_hash is None:
                            print(
                                "No baseline disk hash recorded yet — "
                                "skipping drag-save until a reload establishes one."
                            )
                            return False
                        if current_hash is None:
                            print(
                                "Could not read current disk state (transient I/O "
                                "error?) — skipping drag-save rather than risk a "
                                "blind clobber."
                            )
                            return False
                        if current_hash != ctx.last_disk_hash:
                            print(
                                "Disk changed since last reload (agent edit?) — "
                                "skipping drag-save to avoid clobbering it."
                            )
                            return False
                        # Atomic write (temp + fsync + os.replace), the same
                        # path adapter.change_graph uses — GRC's native
                        # save_flow_graph is a plain truncate+write, which a
                        # concurrent reader (load_flow_graph) could observe
                        # torn mid-write.
                        write_flow_graph_atomic(
                            drawing_area._flow_graph, Path(grc_file_path)
                        )
                        ctx.last_disk_hash = _sha256_file(grc_file_path)
                        ctx.last_synced_export_hash = flow_graph_content_hash(
                            drawing_area._flow_graph
                        )
                        # Shares one undo/redo history with change_graph —
                        # no initial_data here (unlike change_graph's own
                        # call): a manual save relies on change_graph (or an
                        # earlier manual save) having already seeded the
                        # baseline snapshot.
                        push_undo_snapshot(drawing_area._flow_graph, Path(grc_file_path))
                    finally:
                        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            # Bounded retry: a single failed /grc/reload ping would otherwise
            # leave the web server's in-memory graph stale indefinitely. The
            # web reload is the only thing keeping inspect_graph in sync with
            # manual edits, so a transient hiccup shouldn't silently break it.
            url = f"http://localhost:{web_port}/grc/reload"
            for attempt in range(3):
                try:
                    urllib.request.urlopen(url, timeout=2)
                    break
                except Exception:
                    if attempt < 2:
                        time.sleep(0.5)
                    else:
                        raise
        except Exception as e:
            print("Failed to trigger reload:", e)
        return False

    def trigger_reload():
        # Deferred to an idle callback so the click/drag that triggered
        # this (a blocking network round-trip plus a full flow-graph
        # re-parse) doesn't freeze the GTK main loop before the
        # selection/move the user just made has had a chance to redraw.
        GLib.idle_add(_do_trigger_reload)

    # 0. Safety net for edits that don't go through hooks #1/#2 below.
    # Live-confirmed gap: a param edit via the properties dialog (double-
    # click a block) never fires GTK's "window-added" signal at all — GRC's
    # PropsDialog is a plain Gtk.Dialog, never registered with the
    # Gtk.Application (transient_for=parent is not the same as
    # add_window()) — so hook #2 silently never sees it, regardless of
    # OK/Apply/Cancel. The same is likely true of context-menu actions
    # (Delete/Cut/Paste/Rotate/Enable/Disable/Bypass), which activate
    # directly rather than via a button-release on the DrawingArea. Rather
    # than enumerating more individual GTK signals (fragile, "hand-picked"),
    # periodically compare what the in-memory graph would currently
    # serialize to against last_disk_hash — the exact same comparison
    # _do_trigger_reload's own lost-update guard already makes — and let
    # trigger_reload() run through that existing guard: if disk has ALSO
    # changed since (e.g. a concurrent agent edit), it correctly skips the
    # write rather than clobber it, same as it does for a normal drag.
    def _check_for_unsynced_edit():
        if drawing_area and hasattr(drawing_area, "_flow_graph"):
            try:
                current_hash = flow_graph_content_hash(drawing_area._flow_graph)
                if (
                    ctx.last_synced_export_hash is not None
                    and current_hash != ctx.last_synced_export_hash
                ):
                    trigger_reload()
            except Exception:
                pass
        return True  # keep firing

    GLib.timeout_add(1500, _check_for_unsynced_edit)

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

    # Record the initial on-disk hash so the first drag-save's lost-update
    # guard has a correct baseline, then signal readiness. The readiness flag
    # is set after a few idle iterations of Gtk.main()'s loop — i.e. once the
    # GTK client has (probably) connected to the Broadway display — so
    # grc_open's /ready probe doesn't release the dashboard iframe to
    # Broadway during the platform-build window that would trigger
    # alert("disconnected").
    ctx.last_disk_hash = _sha256_file(grc_file_path)
    if drawing_area and hasattr(drawing_area, "_flow_graph"):
        ctx.last_synced_export_hash = flow_graph_content_hash(drawing_area._flow_graph)

    def _mark_ready(remaining_idles=2):
        # Best-effort only: waiting a few extra idle-loop round-trips (instead
        # of just the first one) gives more of the GTK/Broadway connection
        # handshake a chance to complete before external callers see /ready
        # return 200. This is NOT a verified signal — pure Python has no
        # hook into Broadway's actual client-connect event — it's just a
        # smaller race window than a single iteration, not a guarantee.
        if remaining_idles > 0:
            GLib.idle_add(_mark_ready, remaining_idles - 1)
            return False
        ctx.ready = True
        return False

    GLib.idle_add(_mark_ready)
    Gtk.main()


if __name__ == "__main__":
    main()
