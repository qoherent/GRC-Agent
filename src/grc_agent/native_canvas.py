# ruff: noqa: E402
import fcntl
import hashlib
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk

_log = logging.getLogger(__name__)

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
    flow_graph_content_hash,
    get_blocks_panel_visibility,
    push_undo_snapshot,
    set_blocks_panel_visibility,
    write_flow_graph_atomic,
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

    def get_state_lock(self) -> None:
        return None

    def get_run_log(self) -> dict | None:
        """Return the last completed run's log via the exec_monitor wired at
        startup, or None if no monitor is wired or no run has completed."""
        monitor = object.__getattribute__(self, "_exec_monitor")
        if monitor is None:
            return None
        return monitor.get_last_run_log()

    async def notify_edit(self) -> dict:
        cm = object.__getattribute__(self, "_canvas_manager")
        cm.after_agent_edit()
        return {"ok": True}


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
        self._last_block_names: set[str] = set()
        # Cheap gate for the 1.5s safety-net poll: GRC's own undo/redo ring
        # buffer (page.state_cache) moves on most interactive edit paths that
        # don't fire a trackable GTK signal (properties-dialog OK/Apply,
        # paste, align, rotate, delete, undo/redo) — see
        # _check_for_unsynced_edit. None until the first baseline sync, or if
        # the current page has no state_cache. Not fully sufficient on its
        # own — see _POLL_FULL_CHECK_EVERY above — hence _poll_tick_count.
        self._last_state_cache_version: tuple[int, int, int] | None = None
        self._poll_tick_count = 0
        self._blocks_visible = get_blocks_panel_visibility()
        self.panning = False
        self.pan_start_x = 0.0
        self.pan_start_y = 0.0
        self.pan_start_hadj = 0.0
        self.pan_start_vadj = 0.0
        self._connected_drawing_areas: set[int] = set()
        # Fired on switch-page (current page actually changed) — wired by
        # desktop_app.py to _sync_sidebar, which cancels any in-flight chat
        # and re-binds the sidebar to the new current page's session.
        self.on_graphs_changed: Callable[[], None] | None = None
        # Fired on page-added / page-removed (the set of open tabs changed,
        # but the current tab hasn't necessarily changed). Distinct from
        # on_graphs_changed because background tab add/remove must NOT
        # cancel the current chat (M1) — only switch-page (and
        # page-removed-of-the-current-page, which GTK follows with a
        # switch-page) should. Left as None (no-op) — the current page
        # doesn't change on background tab events, so no badge/sidebar
        # refresh is needed.
        self.on_graph_list_changed: Callable[[], None] | None = None
        self.on_sync_failed: Callable[[str], None] | None = None

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

    def after_agent_edit(self) -> None:
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

        old_names = self._last_block_names
        self.drawing_area._update_after_zoom = True
        self.drawing_area.queue_draw()
        self._scroll_to_new_blocks(fg, old_names)
        self.last_synced_export_hash = flow_graph_content_hash(fg)
        if self.path:
            self.last_disk_hash = _sha256_file(self.path)
        self._last_block_names = {b.name for b in fg.blocks}

        # Push to GRC's native undo cache and mark page as modified
        page = self.current_page
        if page:
            page.saved = False
            if hasattr(page, "state_cache"):
                page.state_cache.save_new_state(fg.export_data())
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
            self._last_block_names = {b.name for b in fg.blocks}
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
                    write_flow_graph_atomic(fg, Path(self.path))
                    self.last_disk_hash = _sha256_file(self.path)
                    self.last_synced_export_hash = flow_graph_content_hash(fg)
                    push_undo_snapshot(fg, Path(self.path))
                    self._last_block_names = {b.name for b in fg.blocks}
                finally:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        except Exception as e:
            _log.warning("Failed to sync manual edit: %s", e)
            # Log-only was a real data-loss risk: a disk-full/unwritable-file
            # edit would silently never persist, with zero user-visible
            # signal. Surface it through the sidebar's status bar.
            if self.on_sync_failed:
                self.on_sync_failed(f"Failed to save your edit: {e}")

    def toggle_blocks_panel(self) -> bool:
        if not self.app:
            return False
        self._blocks_visible = not self._blocks_visible
        return set_blocks_panel_visibility(self.app, self._blocks_visible)

    def _scroll_to_new_blocks(self, flow_graph: Any, old_names: set[str]) -> None:
        try:
            new_coords = [
                tuple(b.states["coordinate"])
                for b in flow_graph.blocks
                if b.name not in old_names
                and isinstance(b.states.get("coordinate"), (list, tuple))
            ]
            if not new_coords:
                return
            scrolled_window = self._get_scrolled_window()
            if scrolled_window is None:
                return
            zoom = self.drawing_area.zoom_factor
            content_w, content_h = flow_graph.get_extents()[2:]
            min_x = min(c[0] for c in new_coords) * zoom
            min_y = min(c[1] for c in new_coords) * zoom
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
            _log.warning("Failed to scroll to newly-added blocks: %s", e)

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
                self._last_block_names = {b.name for b in fg.blocks}
                self._last_state_cache_version = self._state_cache_version(page)
        except Exception as e:
            # Guard the only signal handlers touching disk hashing: if this
            # raised, last_synced_export_hash would stay at the previous tab's
            # value and the next poll would compare the new page against a
            # stale baseline.
            _log.warning("Failed to sync page baselines on tab switch: %s", e)

    def _on_page_switched(self, _notebook: Any, _page: Any, _page_num: int) -> None:
        self._setup_drawing_area()
        self._sync_page_baselines()
        if self.on_graphs_changed:
            self.on_graphs_changed()

    def _on_page_added(self, _notebook: Any, child: Any, _page_num: int) -> None:
        # A new tab was appended — could be the foreground OR a background
        # tab. If it's foreground, switch-page will fire next and run the
        # full sync (chat-cancel + rebind). If it's background, the current
        # chat must NOT be cancelled — call the light list-change callback
        # instead of on_graphs_changed (M1).
        self._setup_drawing_area(child)
        if self.on_graph_list_changed:
            self.on_graph_list_changed()

    def _on_page_removed(self, *_args: Any) -> None:
        # Closing a background tab does NOT change the current page — chat
        # must keep running. Closing the current tab fires page-removed
        # AND THEN switch-page, so the chat-cancel correctly happens via
        # the switch-page handler. Either way, this handler must NOT
        # itself cancel the chat (M1).
        if self.on_graph_list_changed:
            self.on_graph_list_changed()

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
