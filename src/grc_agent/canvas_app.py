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
    hide_panels_by_default,
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
        # A /reload request can arrive via the control server before the GTK
        # window/drawing_area have finished building (the server binds before
        # GRC's Platform.build_library() call). Buffer it here and drain once
        # the window exists, so an early agent edit doesn't get silently lost.
        self.pending_reload = False
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
        self.panning = False
        self.pan_start_x = 0.0
        self.pan_start_y = 0.0
        self.pan_start_hadj = 0.0
        self.pan_start_vadj = 0.0

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
            self.pending_reload = True
            return False
        return self._perform_reload()

    def _perform_reload(self):
        """The actual reload work, separate from the buffering guard above."""
        try:
            flow_graph = self.drawing_area._flow_graph
            # Captured BEFORE import_data overwrites the graph — diffed
            # against the post-reload block set below to find newly-added
            # blocks (most commonly from an agent's change_graph call) and
            # scroll them into view. change_graph's own positioning (see
            # adapter.py's add_blocks phase) places new blocks in a column
            # that grows further right with each edit, so without this
            # there's no cue anything changed until the user happens to
            # notice or scroll there themselves.
            old_names = {b.name for b in flow_graph.blocks}
            new_data = self.platform.parse_flow_graph(self.grc_file_path)
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
            self._scroll_to_new_blocks(flow_graph, old_names)
            print("Reloaded flowgraph from disk after an external edit")
        except Exception as e:
            print("Failed to reload flowgraph from disk:", e)
        return False

    def apply_pending_reload(self):
        if self.window and self.pending_reload:
            self.pending_reload = False
            self._perform_reload()

    def _scroll_to_new_blocks(self, flow_graph, old_names):
        """Best-effort: pan the ScrolledWindow so a block added by this
        reload is actually visible, rather than requiring the user to
        notice something changed and scroll/zoom-out to find it themselves.

        Computes the flowgraph's own extent directly (the same formula
        DrawingArea._update_size uses) and sets the ScrolledWindow's
        adjustment bounds from it explicitly, rather than calling
        _update_size() and waiting for GTK to propagate that size request
        into the adjustment through a real size-allocate cycle — confirmed
        live that this does NOT happen synchronously (nor within a couple
        of idle_add hops afterward): _update_size() correctly computed a
        wider size request, but the adjustment's own `upper` was still
        stuck at the pre-edit value when read immediately after, in both
        cases. Setting the bound directly sidesteps that timing entirely.
        """
        try:
            new_coords = [
                tuple(b.states["coordinate"])
                for b in flow_graph.blocks
                if b.name not in old_names
                and isinstance(b.states.get("coordinate"), (list, tuple))
            ]
            if not new_coords:
                return
            # DrawingArea -> Viewport -> ScrolledWindow (gui/Notebook.py's
            # own construction) — not assumed, walked defensively in case a
            # future GRC version changes this nesting.
            scrolled_window = self.drawing_area.get_parent()
            while scrolled_window is not None and not isinstance(
                scrolled_window, Gtk.ScrolledWindow
            ):
                scrolled_window = scrolled_window.get_parent()
            if scrolled_window is None:
                return
            zoom = self.drawing_area.zoom_factor
            content_w, content_h = flow_graph.get_extents()[2:]
            min_x = min(c[0] for c in new_coords)
            min_y = min(c[1] for c in new_coords)
            for adjustment, content_extent, target in (
                (scrolled_window.get_hadjustment(), content_w * zoom + 100, min_x),
                (scrolled_window.get_vadjustment(), content_h * zoom + 100, min_y),
            ):
                if adjustment is None:
                    continue
                adjustment.set_upper(max(adjustment.get_upper(), content_extent))
                upper_bound = max(
                    adjustment.get_lower(), adjustment.get_upper() - adjustment.get_page_size()
                )
                adjustment.set_value(max(adjustment.get_lower(), min(target, upper_bound)))
        except Exception as e:
            print("Failed to scroll to newly-added blocks:", e)


def start_control_server(ctx, port) -> bool:
    """Background HTTP listener so the dashboard page (resize) and the web
    server (reload, after an agent-driven edit) can act on this canvas
    process. GTK calls must happen on the main thread, so handlers only
    schedule work via GLib.idle_add.

    Returns True if the server bound and started, False if the port remained
    unavailable after all retries. The caller (main()) is responsible for
    deciding whether to continue without a control server or exit so web.py
    can report a real error instead of a misleading "timed out" message.
    """

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
    #
    # Budget sized to comfortably exceed web.py's own worst-case teardown of
    # a prior canvas: _terminate_canvas_proc waits up to 2s for a SIGTERM
    # exit plus up to another 2s for a SIGKILL exit (web.py's
    # _terminate_canvas_proc/_killpg_with_fallback), and a stale orphan from
    # an earlier crashed run goes through a similar wait first
    # (_reclaim_canvas_orphan) — both can precede this process's own spawn.
    # A too-short budget here doesn't just delay the canvas: if it's
    # exhausted, /ready becomes permanently unreachable for this process's
    # entire life, so _wait_for_canvas_ready's 20s poll (web.py) is
    # guaranteed to time out even if the canvas itself renders fine.
    server = None
    attempts = 25
    for attempt in range(attempts):
        try:
            server = HTTPServer(("127.0.0.1", port), ControlHandler)
            break
        except OSError as e:
            print(f"Control server bind attempt {attempt + 1}/{attempts} failed: {e}")
            time.sleep(0.3)
    if server is None:
        print(
            "FATAL: Could not bind the canvas control server after retries — "
            "live resize and chat-driven reload are unavailable. Exiting so the "
            "dashboard can report a real error instead of a generic timeout."
        )
        return False
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return True


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
    if not start_control_server(ctx, control_port):
        sys.exit(1)

    # Set up GRC Platform & Application Context (via adapter — the sole
    # gnuradio importer).
    p = get_gui_platform()

    # Pass the flowgraph path directly to the native Application to load it inside the MainWindow
    Application = gui_application_cls()
    app = Application([grc_file_path], p)
    app.register(None)
    app.activate()

    # Hide the GRC panels (block library, console, variable editor) by default
    hide_panels_by_default(app)

    # Get the active MainWindow created by the application
    window = Gtk.Application.get_default().get_active_window()
    if not window:
        print("Failed to get GRC active MainWindow")
        sys.exit(1)

    # Strip the native title bar/min/max/close controls — this is meant to
    # render as a canvas embedded in an iframe, not a desktop window.
    window.set_decorated(False)

    # Hide the top menu bar and toolbar/icon bar
    if hasattr(window, "menu_bar") and window.menu_bar:
        window.menu_bar.hide()
    if hasattr(window, "tool_bar") and window.tool_bar:
        window.tool_bar.hide()

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
    ctx.apply_pending_reload()

    # Hide every sibling on the path from the DrawingArea up to the window
    # (menu bar, toolbar, block-library tree, console/variable-editor pane,
    # notebook tabs bar, ...) so only the flowgraph's scrolled canvas shows,
    # no matter how GRC's MainWindow happens to be laid out.
    def isolate_drawing_area(root_window, target):
        pass

    if drawing_area:
        try:
            isolate_drawing_area(window, drawing_area)
        except Exception as e:
            print("Failed to isolate GRC canvas:", e)

        # GRC's own Notebook.py hardcodes the ScrolledWindow wrapping the
        # canvas to a 600x400 minimum size request — a real GTK container
        # can never be resized smaller than its child's requested minimum,
        # so apply_resize()'s window.resize() calls below were silently
        # clamped to at least 600x400 regardless of what the dashboard's
        # actual (often narrower) pane asked for. The canvas then always
        # rendered wider/taller than the visible iframe, cropping content
        # — and the scrollbar fixed above — off past its right/bottom edge
        # (live-confirmed: a 260x350 resize request had no visible effect;
        # the graph's own content, known to span to x=1320, showed no
        # scrollbar at that pane size because the real rendered canvas was
        # still 600+ px wide, wider than the 260px-visible crop). Relaxing
        # the requested minimum to (1, 1) lets the window actually shrink
        # to match whatever size is requested.
        try:
            scrolled_window = drawing_area.get_parent()
            while scrolled_window is not None and not isinstance(
                scrolled_window, Gtk.ScrolledWindow
            ):
                scrolled_window = scrolled_window.get_parent()
            if scrolled_window is not None:
                scrolled_window.set_size_request(1, 1)
        except Exception as e:
            print("Failed to relax canvas ScrolledWindow's minimum size:", e)

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
        try:
            if drawing_area and hasattr(drawing_area, "_flow_graph"):
                # No-op guard: on_button_release fires trigger_reload() for
                # EVERY non-middle-click release (including a plain select-
                # click that changed nothing). Skip the disk write + reload
                # ping entirely when the in-memory graph is unchanged since
                # the last sync — the exact same gate _check_for_unsynced_edit
                # (the 1.5s safety-net poll) already uses. Without this, every
                # left-click on the canvas wrote the file and bumped the
                # version, forcing a web-side reload + canvas refresh.
                if (
                    ctx.last_synced_export_hash is not None
                    and flow_graph_content_hash(drawing_area._flow_graph)
                    == ctx.last_synced_export_hash
                ):
                    return False
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
            url = f"http://127.0.0.1:{web_port}/grc/reload"
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

    def get_scrolled_window():
        if not drawing_area:
            return None
        parent = drawing_area.get_parent()
        while parent is not None and not isinstance(parent, Gtk.ScrolledWindow):
            parent = parent.get_parent()
        return parent

    def on_button_press(widget, event):
        if event.button == 2:  # Middle mouse click
            scrolled_window = get_scrolled_window()
            if scrolled_window:
                ctx.panning = True
                ctx.pan_start_x = event.x_root
                ctx.pan_start_y = event.y_root
                ctx.pan_start_hadj = scrolled_window.get_hadjustment().get_value()
                ctx.pan_start_vadj = scrolled_window.get_vadjustment().get_value()
                return True
        return False

    def on_motion_notify(widget, event):
        if ctx.panning:
            if event.state & Gdk.ModifierType.BUTTON2_MASK:
                scrolled_window = get_scrolled_window()
                if scrolled_window:
                    dx = event.x_root - ctx.pan_start_x
                    dy = event.y_root - ctx.pan_start_y
                    hadj = scrolled_window.get_hadjustment()
                    vadj = scrolled_window.get_vadjustment()

                    new_h = ctx.pan_start_hadj - dx
                    new_v = ctx.pan_start_vadj - dy

                    new_h = max(hadj.get_lower(), min(new_h, hadj.get_upper() - hadj.get_page_size()))
                    new_v = max(vadj.get_lower(), min(new_v, vadj.get_upper() - vadj.get_page_size()))

                    hadj.set_value(new_h)
                    vadj.set_value(new_v)
                    return True
            else:
                ctx.panning = False
        return False

    def on_button_release(widget, event):
        if event.button == 2:
            if ctx.panning:
                ctx.panning = False
                return True
        else:
            trigger_reload()
        return False

    if drawing_area:
        drawing_area.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK | Gdk.EventMask.POINTER_MOTION_MASK)
        drawing_area.connect("button-press-event", on_button_press)
        drawing_area.connect("button-release-event", on_button_release)
        drawing_area.connect("motion-notify-event", on_motion_notify)

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
