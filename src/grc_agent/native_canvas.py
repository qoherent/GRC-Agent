# ruff: noqa: E402
import fcntl
import hashlib
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk

_log = logging.getLogger(__name__)

# Fit-to-view constants for _fit_to_view. Zoom bounds match GRC's own
# DrawingArea.zoom_in/zoom_out clamps (0.1..5.0) so a fit never sets a zoom
# level the native zoom actions can't reach back to.
_FIT_ZOOM_MIN = 0.1
_FIT_ZOOM_MAX = 5.0
# Multiplicative padding around the graph's bounding box when computing the
# fit zoom, so blocks aren't glued to the viewport edges.
_FIT_PAD = 1.1

# GRC's own undo/redo state_cache (see NativeCanvasManager._state_cache_version)
# is a necessary-but-not-sufficient signal: (a) block-library drag-and-drop
# add, double-click add, and Variable Editor add/remove mutate the flowgraph
# without touching state_cache at all, and (b) an ordinary "undo, then make a
# different edit" sequence provably returns state_cache to the exact same
# (current_state_index, num_prev_states, num_next_states) tuple it had before
# the undo — indistinguishable from "nothing happened" by that tuple alone,
# even though the content differs. Both were confirmed by direct testing
# against the installed gnuradio package. Rather than making the cheap check
# itself airtight (it structurally can't be, from read-only counters alone),
# every Nth tick forces the full check regardless of the cheap comparison —
# bounding the staleness window for those two gaps to a few seconds instead of
# "until the next unrelated state_cache movement, or never."
_POLL_FULL_CHECK_EVERY = 10  # ~15s at the 1.5s poll interval

from grc_agent.adapter import (
    _atomic_write_text,
    _serialize_flow_graph,
    flow_graph_content_hash,
    get_blocks_panel_visibility,
    push_undo_snapshot,
    set_blocks_panel_visibility,
)


def _sha256_file(path) -> str | None:
    try:
        with open(path, "rb") as f:
            return hashlib.file_digest(f, "sha256").hexdigest()
    except OSError:
        return None


class NativeFlowgraphProxy:
    """Transparent proxy for the active flowgraph (agent deps). Resolves
    to ``window.current_page.flow_graph`` on every access — automatically
    follows tab switches and file-open/close in GRC's native UI.

    Also carries an optional ``_exec_monitor`` reference so the
    ``get_run_log`` tool can read the last run's output via
    ``ctx.deps.get_run_log()`` without a separate module-level singleton.
    """

    def __init__(self, canvas_manager: "NativeCanvasManager", exec_monitor: Any = None) -> None:
        object.__setattr__(self, "_canvas_manager", canvas_manager)
        object.__setattr__(self, "_exec_monitor", exec_monitor)

    def _get_target(self) -> Any:
        cm = object.__getattribute__(self, "_canvas_manager")
        fg = cm.current_flow_graph
        if fg is None:
            raise RuntimeError(
                "No flowgraph is open. Open or create a flowgraph in GRC "
                "(File > New / File > Open) before using this tool."
            )
        return fg

    def __getattr__(self, name: str) -> Any:
        return getattr(self._get_target(), name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._get_target(), name, value)

    def get_run_log(self) -> dict | None:
        """Return the last completed run's log via the exec_monitor wired at
        startup, or None if no monitor is wired or no run has completed."""
        monitor = object.__getattribute__(self, "_exec_monitor")
        if monitor is None:
            return None
        return monitor.get_last_run_log()

    async def run_flowgraph(
        self,
        action: Literal["start", "stop"] = "start",
        wait: bool = True,
        timeout_seconds: float = 60.0,
        stop_after_seconds: float | None = None,
    ) -> dict:
        """Control execution of the active flowgraph (start or stop).

        When action='stop', sends SIGTERM to the running process (same as the
        toolbar Stop button).
        When action='start', mirrors the GUI toolbar Run button: GRC generates from
        the live in-memory graph, spawns the process, and streams its output to
        the GRC console (where the user watches it live). This method returns
        status only — the model reads the full output via get_run_log.

        With stop_after_seconds set (and wait=True), the run is stopped
        automatically once it has run that long without finishing on its own —
        one bounded run instead of a start-then-stop pair. The auto-stop sends
        the same SIGTERM the toolbar Stop button does and reports
        status='stopped_after_timeout'.

        Pre-gates mirror GRC's own handler conditions because a disabled
        Gio action is a SILENT no-op, and an unsaved/untitled page would
        route GRC into a modal Save-As dialog that blocks the unified loop.
        """
        if action == "stop":
            return await self.stop_flowgraph()
        if action != "start":
            raise ValueError(f"Invalid action {action!r}: must be 'start' or 'stop'")
        self._validate_bounded_run(wait, stop_after_seconds)

        monitor = object.__getattribute__(self, "_exec_monitor")
        if monitor is None:
            raise ValueError(
                "The run monitor is not wired, so flowgraphs cannot be run from here. "
                "This is an environment fault — do not retry; tell the user to use "
                "GRC's own Execute button."
            )
        cm = object.__getattribute__(self, "_canvas_manager")
        page = cm.current_page
        if page is None:
            raise ValueError(
                "No flowgraph is open. Open or create a flowgraph in GRC before running it."
            )
        if getattr(page, "process", None) is not None:
            raise ValueError(
                "A flowgraph execution is already in progress. Stop it with run_flowgraph(action='stop') "
                "first, or wait for it to finish."
            )
        if not getattr(page, "file_path", None):
            raise ValueError(
                "The flowgraph has never been saved. Save it in GRC first (File > Save) — "
                "execution generates from the saved file."
            )
        fg = page.flow_graph
        # is_valid()/iter_error_messages() only read _error_messages, which is
        # populated by an explicit validate() call (same convention as the
        # turn-end output validator) — without it the check passes vacuously.
        fg.validate()
        if not fg.is_valid():
            errors = "; ".join(list(fg.iter_error_messages())[:5])
            raise ValueError(
                f"The flowgraph is invalid, so GRC will refuse to execute it: {errors}. "
                "Fix the graph with change_graph first."
            )

        from grc_agent.adapter import gui_actions

        actions = gui_actions()
        # Gio actions are silent no-ops when disabled, and GRC's enablement
        # (update_exec_stop) only refreshes on GRC's own actions — agent-side
        # edits can leave EXEC stale-disabled. The gates above re-establish
        # exactly the enabled condition, so enabling here is truthful.
        actions.FLOW_GRAPH_EXEC.set_enabled(True)
        epoch = self._trigger_execute(monitor, actions)

        if not wait:
            return {
                "status": "started",
                "note": (
                    "The flowgraph is running; output streams to GRC's console where the "
                    "user can watch it. GUI flowgraphs run until stopped — call "
                    "run_flowgraph(action='stop') when it should end. Read output with get_run_log "
                    "(its run_in_progress field tells you when the run has finished)."
                ),
            }

        # With stop_after_seconds the wait bound IS the runtime budget — the run
        # cannot exceed it — so timeout_seconds is moot in that mode.
        deadline = stop_after_seconds if stop_after_seconds is not None else timeout_seconds
        outcome = await monitor.wait_for_run_end(deadline, epoch=epoch)
        return await self._build_run_result(
            monitor, outcome, timeout_seconds, stop_after_seconds
        )

    async def _build_run_result(
        self,
        monitor,
        outcome: str,
        timeout_seconds: float,
        stop_after_seconds: float | None,
    ) -> dict:
        """Map the monitor's run outcome to the tool result payload."""
        if outcome == "completed":
            code = monitor.last_run_code
            return {
                "status": "completed",
                "return_code": code,
                "ran_successfully": code == 0,
                "note": (
                    "Read the full console output with the get_run_log tool. An empty log "
                    "with an immediate completion can mean the graph ran in an external "
                    "terminal (no_gui graphs) or failed to spawn — check the log and ask "
                    "the user if in doubt."
                ),
            }
        if outcome == "still_running":
            if stop_after_seconds is not None:
                # Bounded run exhausted its budget: stop it through the same
                # native Stop path the toolbar button takes — no extra machinery.
                return await self._finish_bounded_run(monitor, stop_after_seconds)
            return {
                "status": "still_running",
                "note": (
                    f"The run did not finish within {timeout_seconds}s. GUI flowgraphs run "
                    "until stopped — call run_flowgraph(action='stop') when done, then read get_run_log."
                ),
            }
        return {
            "status": "not_started",
            "note": (
                "GRC did not start an execution (no 'Executing:' marker observed). This "
                "should not happen after the pre-checks — ask the user to check GRC's "
                "console and try the toolbar Run button."
            ),
        }

    @staticmethod
    def _validate_bounded_run(wait: bool, stop_after_seconds: float | None) -> None:
        """Reject a stop_after_seconds request the engine cannot honor."""
        if stop_after_seconds is None:
            return
        if not wait:
            raise ValueError(
                "stop_after_seconds requires wait=True: with wait=False the call "
                "returns immediately and nothing would enforce the deadline. Use "
                "wait=True for a bounded run, or stop the run later with action='stop'."
            )
        if stop_after_seconds <= 0:
            raise ValueError(
                f"Invalid stop_after_seconds {stop_after_seconds!r}: must be a "
                "positive number of seconds of runtime."
            )

    async def _finish_bounded_run(
        self, monitor, stop_after_seconds: float
    ) -> dict:
        """Stop a bounded run whose budget is exhausted, via the same native
        Stop path the toolbar button takes (SIGTERM). If the run finished on
        its own right at the deadline (nothing left to stop), report the real
        outcome instead of a stop that never happened."""
        stop_res = await self.stop_flowgraph()
        if stop_res.get("status") == "not_running":
            code = monitor.last_run_code
            return {
                "status": "completed",
                "return_code": code,
                "ran_successfully": code == 0,
                "note": (
                    f"The run finished on its own right at the {stop_after_seconds}s "
                    "auto-stop deadline. Read the full output with get_run_log."
                ),
            }
        return {
            "status": "stopped_after_timeout",
            "return_code": monitor.last_run_code,
            "note": (
                f"Auto-stopped after {stop_after_seconds}s of runtime (SIGTERM — the "
                "same Stop the toolbar button sends; a deliberate stop, not a failure). "
                "Read the full output with get_run_log."
            ),
        }

    def _trigger_execute(self, monitor, actions) -> int:
        """Flag this run as agent-initiated and trigger GRC's Execute.

        The suppression flag must be set BEFORE the action (the 'Executing:'
        start marker fires synchronously inside it) and dropped if no start
        marker ever fires — otherwise it would wrongly suppress a later
        user-initiated run's failure notification. Returns the pre-action run
        epoch: unchanged after a silent no-op, which makes wait_for_run_end
        report not_started instead of the previous run's stale 'completed'.
        """
        epoch = monitor.run_epoch
        monitor.mark_run_agent_initiated()
        try:
            actions.FLOW_GRAPH_EXEC()
        except Exception:
            monitor.mark_run_agent_initiated_cancelled()
            raise
        if not monitor.is_tracking:
            monitor.mark_run_agent_initiated_cancelled()
        return epoch

    async def stop_flowgraph(self) -> dict:
        """Stop the active flowgraph's run through GRC's native Stop action
        (SIGTERM to the process group — the same thing the toolbar Stop
        button does)."""
        cm = object.__getattribute__(self, "_canvas_manager")
        page = cm.current_page
        if page is None:
            raise ValueError("No flowgraph is open, so nothing is running.")
        if getattr(page, "process", None) is None:
            return {
                "status": "not_running",
                "note": "No flowgraph execution is in progress.",
            }

        from grc_agent.adapter import gui_actions

        actions = gui_actions()
        # Truthful enable: page.process is set, exactly GRC's own condition.
        actions.FLOW_GRAPH_KILL.set_enabled(True)
        actions.FLOW_GRAPH_KILL()

        monitor = object.__getattribute__(self, "_exec_monitor")
        if monitor is None:
            return {"status": "stop_requested"}
        outcome = await monitor.wait_for_run_end(10.0)
        if outcome == "completed":
            return {
                "status": "stopped",
                "note": (
                    "The run was stopped (SIGTERM — a user-requested stop, not a failure). "
                    "Buffered output is still captured: read it with get_run_log."
                ),
            }
        return {
            "status": "stop_requested",
            "note": (
                "SIGTERM sent; the process is still shutting down. Check get_run_log "
                "shortly (its run_in_progress field) to confirm it ended."
            ),
        }

    async def notify_edit(self, relayout: bool = False) -> dict:
        cm = object.__getattribute__(self, "_canvas_manager")
        cm.after_agent_edit(relayout=relayout)
        monitor = object.__getattribute__(self, "_exec_monitor")
        if monitor is not None and hasattr(monitor, "notify_graph_modified"):
            monitor.notify_graph_modified()
        return {"ok": True}

    async def save_block(
        self,
        instance_name: str,
        block_id: str | None = None,
        label: str | None = None,
        category: str | None = None,
        overwrite: bool = False,
    ) -> dict:
        from grc_agent.adapter.block_library import save_block_to_library

        cm = object.__getattribute__(self, "_canvas_manager")
        result = save_block_to_library(
            self._get_target(),
            instance_name,
            block_id=block_id,
            label=label,
            category=category,
            overwrite=overwrite,
        )
        if result.get("ok"):
            cm.reload_block_library()
        return result


class NativeCanvasManager:
    """Manages the flowgraph canvas inside GRC's MainWindow. All
    flowgraph access is resolved dynamically from ``window.current_page``
    so the agent always sees the graph the user is looking at — no
    Browse button, no stale references."""

    def __init__(self, window: Any, platform: Any) -> None:
        self.window = window
        self.platform = platform
        self.app: Any = None
        self.last_disk_hash: str | None = None
        self.last_synced_export_hash: str | None = None
        # Cheap gate for the 1.5s safety-net poll: GRC's own undo/redo ring
        # buffer (page.state_cache) moves on most interactive edit paths that
        # don't fire a trackable GTK signal (properties-dialog OK/Apply,
        # paste, align, rotate, delete, undo/redo) — see
        # _check_for_unsynced_edit. None until the first baseline sync, or if
        # the current page has no state_cache. Not fully sufficient on its
        # own — see _POLL_FULL_CHECK_EVERY above — hence _poll_tick_count.
        self._last_state_cache_version: tuple[int, int, int] | None = None
        # The page.file_path that the current baselines (last_disk_hash,
        # last_synced_export_hash) were derived for. Saving an untitled graph
        # in place — or Save-As-ing to a new path — changes page.file_path
        # without firing switch-page, so the poll must notice the change and
        # re-baseline, or sync_manual_edit's `last_disk_hash is None` early
        # return (native_canvas.py) would silently stop auto-persisting edits.
        self._baseline_path: str | None = None
        self._poll_tick_count = 0
        self._blocks_visible = get_blocks_panel_visibility()
        # Block name the chat sidebar wants outlined on canvas (set/cleared by
        # a chat badge hover) — deliberately independent of GRC's own
        # element.highlighted/selected_elements mechanism, which
        # FlowGraph.update_selected() overwrites on every dispatched action
        # (selection, undo/redo, move, ...), wiping a single named highlight.
        self._highlight_block_name: str | None = None
        self.panning = False
        self.pan_start_x = 0.0
        self.pan_start_y = 0.0
        self.pan_start_hadj = 0.0
        self.pan_start_vadj = 0.0
        # Fired on switch-page (current page actually changed) — wired by
        # desktop_app.py to _sync_sidebar, which cancels any in-flight chat
        # and re-binds the sidebar to the new current page's session.
        self.on_graphs_changed: Callable[[], None] | None = None
        self.on_sync_failed: Callable[[str], None] | None = None
        self.on_graph_modified: Callable[[], None] | None = None

    @property
    def current_page(self) -> Any:
        return self.window.current_page

    @property
    def current_flow_graph(self) -> Any:
        page = self.current_page
        return page.flow_graph if page else None

    @property
    def drawing_area(self) -> Any:
        page = self.current_page
        return page.drawing_area if page else None

    @property
    def path(self) -> str | None:
        page = self.current_page
        if page is None:
            return None
        return page.file_path or None

    @property
    def _lock_path(self) -> Path | None:
        p = self.path
        if not p:
            return None
        return Path(p).parent / ".grc_agent" / (Path(p).name + ".lock")

    def _get_scrolled_window(self, da: Any = None) -> Any:
        if da is None:
            da = self.drawing_area
        if not da:
            return None
        parent = da.get_parent()
        while parent is not None and not isinstance(parent, Gtk.ScrolledWindow):
            parent = parent.get_parent()
        return parent

    def after_agent_edit(self, relayout: bool = False) -> None:
        if not (self.drawing_area and hasattr(self.drawing_area, "_flow_graph")):
            return
        fg = self.drawing_area._flow_graph

        # Update flowgraph elements to draw, labels, and shapes first. A
        # failure here must not skip queue_draw below — the graph is already
        # mutated (and possibly persisted), so a stale canvas is worse than a
        # partially-updated one.
        try:
            if hasattr(fg, "update"):
                fg.update()
            if hasattr(self.window, "vars") and hasattr(self.window.vars, "update_gui"):
                self.window.vars.update_gui(fg.blocks)
        except Exception:
            _log.warning("flowgraph update() raised during after_agent_edit", exc_info=True)

        self.drawing_area._update_after_zoom = True
        self.drawing_area.queue_draw()
        if relayout:
            self._fit_to_view(fg)
        self.last_synced_export_hash = flow_graph_content_hash(fg)
        if self.on_graph_modified:
            self.on_graph_modified()

        # Push to GRC's native undo cache and mark page as modified
        page = self.current_page
        if page:
            page.saved = False
            if hasattr(page, "state_cache"):
                page.state_cache.save_new_state(fg.export_data())
                # save_new_state bumps the version tuple — re-derive the
                # poll's cheap-gate baseline so the next tick doesn't
                # needlessly run a full export-hash comparison.
                self._last_state_cache_version = self._state_cache_version(page)
            if hasattr(self.window, "update"):
                self.window.update()

    def sync_manual_edit(self, current_hash: str | None = None) -> None:  # noqa: C901
        if not (self.drawing_area and hasattr(self.drawing_area, "_flow_graph")):
            return
        fg = self.drawing_area._flow_graph
        current_hash = current_hash or flow_graph_content_hash(fg)
        if (
            self.last_synced_export_hash is not None
            and current_hash == self.last_synced_export_hash
        ):
            return
        if not self.path:
            # Unsaved/untitled graph: nothing to persist to disk, but re-arm the
            # poll baseline so the 1.5s safety-net doesn't keep firing forever.
            self.last_synced_export_hash = flow_graph_content_hash(fg)
            if self.on_graph_modified:
                self.on_graph_modified()
            return
        try:
            lock = self._lock_path
            if lock is None:
                return
            lock.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with lock.open("a", encoding="utf-8") as lock_file:
                try:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    # Lock contended (e.g. the same .grc open in another
                    # instance, or a writer mid-commit). Never block the single
                    # gbulb UI thread — skip; the 1.5s safety-net poll re-arms
                    # and retries this sync on the next tick.
                    _log.debug("Flowgraph lock busy — deferring this sync to the next poll.")
                    return
                try:
                    current_hash = _sha256_file(self.path)
                    if self.last_disk_hash is None:
                        return
                    if current_hash is None:
                        return
                    if current_hash != self.last_disk_hash:
                        _log.debug("Disk changed since last reload — skipping drag-save.")
                        # Unlike the exception branch below, this used to be
                        # silent — the poll would keep re-attempting and
                        # re-skipping this same edit indefinitely with zero
                        # indication anything was wrong.
                        if self.on_sync_failed:
                            self.on_sync_failed(
                                "Your edit wasn't saved — the file changed on disk. "
                                "Reload it before continuing."
                            )
                        return
                    _atomic_write_text(_serialize_flow_graph(fg), Path(self.path))
                    self.last_disk_hash = _sha256_file(self.path)
                    self.last_synced_export_hash = flow_graph_content_hash(fg)
                    push_undo_snapshot(fg, Path(self.path))
                    if self.on_graph_modified:
                        self.on_graph_modified()
                finally:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        except Exception as e:
            _log.warning("Failed to sync manual edit: %s", e)
            # Log-only was a real data-loss risk: a disk-full/unwritable-file
            # edit would silently never persist, with zero user-visible
            # signal. Surface it through the sidebar's status bar.
            if self.on_sync_failed:
                self.on_sync_failed(f"Failed to save your edit: {e}")

    def reload_block_library(self) -> None:
        """Refresh the live block registry + visible block-tree panel after
        save_block writes a new .block.yml/.py into the hier-block library.
        Mirrors GRC's own native "Reload Blocks" action (RELOAD_BLOCKS in
        gnuradio.grc.gui.Application): build_library() then repopulate the
        block-tree widget, then redraw open canvases. Uses self.platform
        (the GUI Platform backing this running app's live MainWindow) —
        NOT the headless get_platform() singleton, which is a separate
        instance and would leave the visible panel stale."""
        try:
            self.platform.build_library()
            if hasattr(self.window, "btwin"):
                self.window.btwin.repopulate()
            if hasattr(self.window, "update_pages"):
                self.window.update_pages()
        except Exception as e:
            _log.warning("Failed to reload block library: %s", e)

    def toggle_blocks_panel(self) -> bool:
        if not self.app:
            return False
        self._blocks_visible = not self._blocks_visible
        return set_blocks_panel_visibility(self.app, self._blocks_visible)

    def set_highlight_block(self, name: str) -> None:
        """Outline the named block on canvas (chat badge hover)."""
        self._highlight_block_name = name
        da = self.drawing_area
        if da:
            da.queue_draw()

    def clear_highlight(self) -> None:
        if self._highlight_block_name is not None:
            self._highlight_block_name = None
            da = self.drawing_area
            if da:
                da.queue_draw()

    def scroll_to_block(self, name: str) -> bool:
        """Scroll the canvas so the named block is centered/visible."""
        fg = self.current_flow_graph
        if fg is None:
            return False
        try:
            block = fg.get_block(name)
            coord = tuple(block.states.get("coordinate", (0, 0)))
        except (KeyError, AttributeError):
            return False
        scrolled_window = self._get_scrolled_window()
        if scrolled_window is None or not self.drawing_area:
            return False
        try:
            zoom = getattr(self.drawing_area, "zoom_factor", 1.0)
            content_w, content_h = (
                fg.get_extents()[2:] if hasattr(fg, "get_extents") else (1000, 1000)
            )
            target_x = coord[0] * zoom
            target_y = coord[1] * zoom
            for adjustment, content_extent, target in (
                (scrolled_window.get_hadjustment(), content_w * zoom + 100, target_x),
                (scrolled_window.get_vadjustment(), content_h * zoom + 100, target_y),
            ):
                if adjustment is None:
                    continue
                adjustment.set_upper(max(adjustment.get_upper(), content_extent))
                upper_bound = max(
                    adjustment.get_lower(), adjustment.get_upper() - adjustment.get_page_size()
                )
                adjustment.set_value(max(adjustment.get_lower(), min(target, upper_bound)))
            return True
        except Exception as e:
            _log.warning("Failed to scroll to block %r: %s", name, e)
            return False

    def _on_draw_highlight_overlay(self, da: Any, cr: Any) -> bool:
        """Second 'draw' handler on the DrawingArea, connected after GRC's own
        (DrawingArea.draw), so it inherits the same cairo context — including
        the cr.scale(zoom_factor, zoom_factor) GRC's handler already applied,
        with no save/restore around it to undo. Drawing directly in
        block.coordinate/width/height (flow-graph/logical units), exactly as
        Block.draw() itself does, lands in the right place with no extra
        transform of our own."""
        name = self._highlight_block_name
        if not name:
            return False
        fg = self.current_flow_graph
        if fg is None:
            return False
        try:
            block = fg.get_block(name)
        except KeyError:
            return False
        if not (block.width and block.height):
            return False

        try:
            w, h = (
                (block.width, block.height)
                if block.is_horizontal()
                else (block.height, block.width)
            )
            zoom = da.zoom_factor
            pad = 4.0

            cr.save()
            try:
                cr.translate(*block.coordinate)
                cr.rectangle(-pad, -pad, w + 2 * pad, h + 2 * pad)
                cr.set_line_width(2.5 / zoom)
                cr.set_source_rgba(0.13, 0.59, 0.95, 0.18)  # #2196F3 fill — distinct
                cr.fill_preserve()  # from GRC's own cyan HIGHLIGHT_COLOR, so a chat
                cr.set_source_rgba(0.13, 0.59, 0.95, 1.0)  # hover reads differently
                cr.stroke()  # than a real canvas selection.
            finally:
                cr.restore()
        except Exception as e:
            _log.warning("Failed to draw block highlight overlay: %s", e)
        return False

    def _fit_to_view(self, flow_graph: Any) -> None:
        """Zoom and scroll so every block fits in the visible viewport.
        Called from after_agent_edit only when the batch actually relaid out
        (change_graph reports relayout=True) — the relayout repositions every
        block, so the only view that always shows "what changed" is the whole
        graph. Param-only edits and manual/GUI edits never trigger it, so a
        user's own zoom level is never touched except right after an
        auto-arrange.

        Computes the fit zoom from the graph's get_extents() bounding box and
        the viewport's allocation (the DrawingArea's parent Viewport), clamps
        it to GRC's native 0.1..5.0 zoom range, sets it via GRC's own
        _set_zoom_factor (which queues the label/shape/size refresh), then
        scrolls so the graph's center lands in the middle of the viewport —
        with the adjustment upper raised to cover the new content size first,
        so the target is reachable before the ScrolledWindow's own recalcu-
        lation catches up."""
        da = self.drawing_area
        if da is None or not flow_graph.blocks:
            return
        scrolled_window = self._get_scrolled_window()
        if scrolled_window is None:
            return
        try:
            x_min, y_min, x_max, y_max = flow_graph.get_extents()
            w = max(x_max - x_min, 1.0)
            h = max(y_max - y_min, 1.0)
            viewport = da.get_parent()
            alloc = (
                viewport.get_allocation()
                if viewport is not None
                else scrolled_window.get_allocation()
            )
            vw = max(alloc.width, 1)
            vh = max(alloc.height, 1)
            zoom = min(vw / (w * _FIT_PAD), vh / (h * _FIT_PAD))
            zoom = max(_FIT_ZOOM_MIN, min(zoom, _FIT_ZOOM_MAX))
            if zoom != da.zoom_factor:
                da._set_zoom_factor(zoom)
            # Content size mirrors GRC's own DrawingArea._update_size()
            # (extents corner * zoom + 100) so the manually-raised adjustment
            # upper always covers what the canvas will actually request.
            content_w = x_max * zoom + 100
            content_h = y_max * zoom + 100
            center_x = ((x_min + x_max) / 2.0) * zoom
            center_y = ((y_min + y_max) / 2.0) * zoom
            for adjustment, content, center, viewport_size in (
                (scrolled_window.get_hadjustment(), content_w, center_x, vw),
                (scrolled_window.get_vadjustment(), content_h, center_y, vh),
            ):
                if adjustment is None:
                    continue
                adjustment.set_upper(max(adjustment.get_upper(), content))
                target = center - viewport_size / 2.0
                upper_bound = max(
                    adjustment.get_lower(),
                    adjustment.get_upper() - adjustment.get_page_size(),
                )
                adjustment.set_value(max(adjustment.get_lower(), min(target, upper_bound)))
        except Exception as e:
            _log.warning("Failed to fit graph into view: %s", e)

    def setup_signal_handlers(self) -> None:
        notebook = self.window.notebook
        notebook.connect("switch-page", self._on_page_switched)
        notebook.connect("page-added", self._on_page_added)
        notebook.connect("page-removed", self._on_page_removed)

        for i in range(notebook.get_n_pages()):
            self._setup_drawing_area(notebook.get_nth_page(i))
        self._sync_page_baselines()

        GLib.timeout_add(1500, self._check_for_unsynced_edit)

    def _setup_drawing_area(self, page: Any = None) -> None:
        da = page.drawing_area if page is not None else self.drawing_area
        if da is None or getattr(da, "_grc_agent_setup", False):
            return
        da._grc_agent_setup = True

        sw = self._get_scrolled_window(da)
        if sw is not None:
            sw.set_size_request(1, 1)

        da.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
        )
        da.connect("button-press-event", self._on_button_press)
        da.connect("button-release-event", self._on_button_release)
        da.connect("motion-notify-event", self._on_motion_notify)
        # Connected after GRC's own DrawingArea.draw (wired during page
        # construction, always before this setup runs) — fires second on the
        # same cr, already zoom-scaled, so we draw straight in block
        # coordinates with no transform of our own.
        da.connect("draw", self._on_draw_highlight_overlay)

    @staticmethod
    def _state_cache_version(page: Any) -> tuple[int, int, int] | None:
        """A cheap, read-only fingerprint of GRC's own undo/redo ring buffer.
        Necessarily changes on every interactive edit path GRC itself tracks
        (see the class-level comment on _last_state_cache_version) — a
        necessary condition for flow_graph_content_hash to have changed too,
        used to skip that far more expensive check when nothing moved."""
        sc = getattr(page, "state_cache", None) if page is not None else None
        if sc is None:
            return None
        return (sc.current_state_index, sc.num_prev_states, sc.num_next_states)

    def _sync_page_baselines(self) -> None:
        try:
            fg = self.current_flow_graph
            if fg is not None:
                page = self.current_page
                if page and page.file_path:
                    fg.grc_file_path = page.file_path
                self.last_synced_export_hash = flow_graph_content_hash(fg)
                self.last_disk_hash = _sha256_file(self.path) if self.path else None
                self._baseline_path = self.path
                self._last_state_cache_version = self._state_cache_version(page)
        except Exception as e:
            # Guard the only signal handlers touching disk hashing: if this
            # raised, last_synced_export_hash would stay at the previous tab's
            # value and the next poll would compare the new page against a
            # stale baseline.
            _log.warning("Failed to sync page baselines on tab switch: %s", e)

    def _on_page_switched(self, _notebook: Any, _page: Any, _page_num: int) -> None:
        self._highlight_block_name = None
        self._setup_drawing_area()
        self._sync_page_baselines()
        if self.on_graphs_changed:
            self.on_graphs_changed()

    def _on_page_added(self, _notebook: Any, child: Any, _page_num: int) -> None:
        # A new tab was appended — could be the foreground OR a background
        # tab. If it's foreground, switch-page will fire next and run the
        # full sync (chat-cancel + rebind). If it's background, the current
        # chat must NOT be cancelled (M1).
        self._setup_drawing_area(child)

    def _on_page_removed(self, *_args: Any) -> None:
        # Closing a background tab does NOT change the current page — chat
        # must keep running. Closing the current tab fires page-removed
        # AND THEN switch-page, so the chat-cancel correctly happens via
        # the switch-page handler. Either way, this handler must NOT
        # itself cancel the chat (M1).
        pass

    def _on_button_press(self, _widget: Any, event: Any) -> bool:
        if event.button == 2:
            sw = self._get_scrolled_window()
            if sw:
                self.panning = True
                self.pan_start_x = event.x_root
                self.pan_start_y = event.y_root
                self.pan_start_hadj = sw.get_hadjustment().get_value()
                self.pan_start_vadj = sw.get_vadjustment().get_value()
                return True
        return False

    def _on_motion_notify(self, _widget: Any, event: Any) -> bool:
        if self.panning:
            if event.state & Gdk.ModifierType.BUTTON2_MASK:
                sw = self._get_scrolled_window()
                if sw:
                    dx = event.x_root - self.pan_start_x
                    dy = event.y_root - self.pan_start_y
                    hadj = sw.get_hadjustment()
                    vadj = sw.get_vadjustment()
                    new_h = max(
                        hadj.get_lower(),
                        min(self.pan_start_hadj - dx, hadj.get_upper() - hadj.get_page_size()),
                    )
                    new_v = max(
                        vadj.get_lower(),
                        min(self.pan_start_vadj - dy, vadj.get_upper() - vadj.get_page_size()),
                    )
                    hadj.set_value(new_h)
                    vadj.set_value(new_v)
                    return True
            else:
                self.panning = False
        return False

    def _on_button_release(self, _widget: Any, event: Any) -> bool:
        if event.button == 2:
            if self.panning:
                self.panning = False
                return True
        else:
            self.sync_manual_edit()
        return False

    def _check_for_unsynced_edit(self) -> bool:
        if self.drawing_area and hasattr(self.drawing_area, "_flow_graph"):
            try:
                self._poll_tick_count += 1
                page = self.current_page
                # A path change with no tab switch (untitled->saved in place,
                # or Save-As to a new file) must re-baseline the hashes —
                # otherwise last_disk_hash stays None and sync_manual_edit
                # would early-return on every later edit, silently killing
                # auto-sync for that tab. One uniform rule: baselines follow
                # the page's path. Cheap string compare per tick.
                if page is not None and page.file_path != self._baseline_path:
                    self._sync_page_baselines()
                version = self._state_cache_version(page)
                state_cache_unchanged = (
                    version is not None and version == self._last_state_cache_version
                )
                due_for_backstop = self._poll_tick_count % _POLL_FULL_CHECK_EVERY == 0
                if state_cache_unchanged and not due_for_backstop:
                    # GRC's own undo/redo cache says nothing has moved since the
                    # last tick — skip the expensive full export+YAML+hash
                    # below, unless this is a periodic backstop tick (see
                    # _POLL_FULL_CHECK_EVERY). Pages with no state_cache
                    # (version is None) always fall through to the full check,
                    # unchanged from before.
                    return True

                current_hash = flow_graph_content_hash(self.drawing_area._flow_graph)
                if (
                    self.last_synced_export_hash is not None
                    and current_hash != self.last_synced_export_hash
                ):
                    self.sync_manual_edit(current_hash)
                self._last_state_cache_version = version
            except Exception as e:
                # Log instead of silently swallowing — a single transient error
                # here would otherwise blind the sole guard against un-synced
                # manual edits for the rest of the session.
                _log.warning("Safety-net poll error: %s", e)
        return True
