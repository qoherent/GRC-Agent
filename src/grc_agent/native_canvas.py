# ruff: noqa: E402
import fcntl
import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk

from grc_agent.adapter import (
    flow_graph_content_hash,
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
    follows tab switches and file-open/close in GRC's native UI."""

    def __init__(self, canvas_manager: "NativeCanvasManager") -> None:
        object.__setattr__(self, "_canvas_manager", canvas_manager)

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

    def swap(self, flowgraph: Any) -> None:
        pass

    def bump_version(self) -> None:
        pass

    def get_version(self) -> int:
        return 0

    def is_loaded(self) -> bool:
        cm = object.__getattribute__(self, "_canvas_manager")
        return cm.current_flow_graph is not None

    def get_state_lock(self) -> None:
        return None

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
        from gnuradio.grc.gui import Actions
        self._blocks_visible = bool(Actions.TOGGLE_BLOCKS_WINDOW.get_active())
        self.panning = False
        self.pan_start_x = 0.0
        self.pan_start_y = 0.0
        self.pan_start_hadj = 0.0
        self.pan_start_vadj = 0.0
        self._connected_drawing_areas: set[int] = set()
        self.on_graphs_changed: Callable[[], None] | None = None

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

    @property
    def graph_count(self) -> int:
        return self.window.notebook.get_n_pages()

    def get_drawing_area(self) -> Any:
        return self.drawing_area

    def _get_scrolled_window(self) -> Any:
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
        old_names = self._last_block_names
        self.drawing_area._update_after_zoom = True
        self.drawing_area.queue_draw()
        self._scroll_to_new_blocks(fg, old_names)
        self.last_synced_export_hash = flow_graph_content_hash(fg)
        if self.path:
            self.last_disk_hash = _sha256_file(self.path)
        self._last_block_names = {b.name for b in fg.blocks}

    def reload_from_disk(self) -> None:
        if not (self.drawing_area and hasattr(self.drawing_area, "_flow_graph") and self.path):
            return
        try:
            fg = self.drawing_area._flow_graph
            old_names = {b.name for b in fg.blocks}
            new_data = self.platform.parse_flow_graph(self.path)
            fg.import_data(new_data)
            fg.update()
            self.drawing_area._update_after_zoom = True
            self.drawing_area.queue_draw()
            self.last_disk_hash = _sha256_file(self.path)
            self.last_synced_export_hash = flow_graph_content_hash(fg)
            self._scroll_to_new_blocks(fg, old_names)
            self._last_block_names = {b.name for b in fg.blocks}
        except Exception as e:
            print("Failed to reload flowgraph from disk:", e)

    def sync_manual_edit(self) -> bool:
        if not (self.drawing_area and hasattr(self.drawing_area, "_flow_graph") and self.path):
            return False
        try:
            fg = self.drawing_area._flow_graph
            if (
                self.last_synced_export_hash is not None
                and flow_graph_content_hash(fg) == self.last_synced_export_hash
            ):
                return False
            lock = self._lock_path
            if lock is None:
                return False
            lock.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with lock.open("a", encoding="utf-8") as lock_file:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                try:
                    current_hash = _sha256_file(self.path)
                    if self.last_disk_hash is None:
                        return False
                    if current_hash is None:
                        return False
                    if current_hash != self.last_disk_hash:
                        print("Disk changed since last reload — skipping drag-save.")
                        return False
                    write_flow_graph_atomic(fg, Path(self.path))
                    self.last_disk_hash = _sha256_file(self.path)
                    self.last_synced_export_hash = flow_graph_content_hash(fg)
                    push_undo_snapshot(fg, Path(self.path))
                    self._last_block_names = {b.name for b in fg.blocks}
                finally:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        except Exception as e:
            print("Failed to sync manual edit:", e)
        return False

    def toggle_blocks_panel(self) -> bool:
        if not self.app:
            return False
        self._blocks_visible = not self._blocks_visible
        return set_blocks_panel_visibility(self.app, self._blocks_visible)

    def validate(self) -> tuple[bool, list[str]]:
        if not (self.drawing_area and hasattr(self.drawing_area, "_flow_graph")):
            return False, ["No flowgraph loaded"]
        fg = self.drawing_area._flow_graph
        fg.validate()
        if fg.is_valid():
            return True, []
        errors = []
        for elem, msg in fg.iter_error_messages():
            parent = getattr(elem, "parent_block", None)
            if parent is not None and parent is not elem:
                errors.append(f"{parent.name}: {elem}: {msg}")
            else:
                errors.append(f"{elem}: {msg}")
        return False, errors

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
            print("Failed to scroll to newly-added blocks:", e)

    def setup_signal_handlers(self) -> None:
        notebook = self.window.notebook
        notebook.connect("switch-page", self._on_page_switched)
        notebook.connect("page-added", self._on_page_added)
        notebook.connect("page-removed", self._on_page_removed)

        self._setup_drawing_area()

        GLib.timeout_add(1500, self._check_for_unsynced_edit)

    def _setup_drawing_area(self) -> None:
        da = self.drawing_area
        if da is None or getattr(da, "_grc_agent_setup", False):
            return
        da._grc_agent_setup = True

        sw = self._get_scrolled_window()
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

    def _sync_page_baselines(self) -> None:
        fg = self.current_flow_graph
        if fg is not None:
            page = self.current_page
            if page and page.file_path:
                fg.grc_file_path = page.file_path
            self.last_synced_export_hash = flow_graph_content_hash(fg)
            self.last_disk_hash = _sha256_file(self.path) if self.path else None
            self._last_block_names = {b.name for b in fg.blocks}

    def _on_page_switched(self, _notebook: Any, _page: Any, _page_num: int) -> None:
        self._setup_drawing_area()
        self._sync_page_baselines()
        if self.on_graphs_changed:
            self.on_graphs_changed()

    def _on_page_added(self, *_args: Any) -> None:
        if self.on_graphs_changed:
            self.on_graphs_changed()

    def _on_page_removed(self, *_args: Any) -> None:
        if self.on_graphs_changed:
            self.on_graphs_changed()

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
                current_hash = flow_graph_content_hash(self.drawing_area._flow_graph)
                if (
                    self.last_synced_export_hash is not None
                    and current_hash != self.last_synced_export_hash
                ):
                    self.sync_manual_edit()
            except Exception:
                pass
        return True
