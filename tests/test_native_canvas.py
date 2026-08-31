"""Unit tests for native_canvas — split from the former test_unit.py god file.

Minimal set per the clustered test plan; shared fixtures/helpers live in conftest.py.
"""


def test_sync_manual_edit_failure_paths_report_via_on_sync_failed(tmp_path, monkeypatch):
    """Both auto-save failure sources surface via the on_sync_failed callback
    (wired to the sidebar's status bar in desktop_app.py), never log-only:
    an exception (disk full) and a stale-disk conflict (another program
    changed the file underneath us — must NOT overwrite it)."""
    from unittest.mock import MagicMock

    from grc_agent.native_canvas import NativeCanvasManager

    grc = tmp_path / "f.grc"
    grc.write_text("data")
    (tmp_path / ".grc_agent").mkdir()

    fg = MagicMock()
    da = MagicMock()
    da._flow_graph = fg
    page = MagicMock()
    page.file_path = str(grc)
    page.drawing_area = da
    window = MagicMock()
    window.current_page = page

    monkeypatch.setattr("grc_agent.native_canvas.flow_graph_content_hash", lambda _: "CURRENT")

    # --- Branch 1: exception during save -> on_sync_failed with the cause.
    cm = NativeCanvasManager.__new__(NativeCanvasManager)
    cm.window = window
    cm.last_synced_export_hash = "PREVIOUS"
    cm.last_disk_hash = "SAME"

    monkeypatch.setattr("grc_agent.native_canvas._sha256_file", lambda _: "SAME")

    def _boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("grc_agent.native_canvas._serialize_flow_graph", lambda *_a, **_k: "dummy")
    monkeypatch.setattr("grc_agent.native_canvas._atomic_write_text", _boom)

    failures = []
    cm.on_sync_failed = lambda msg: failures.append(msg)

    cm.sync_manual_edit()

    assert failures, "on_sync_failed was not called on a failed auto-save"
    assert "disk full" in failures[0]

    # --- Branch 2: stale-disk conflict -> on_sync_failed, no overwrite.
    cm = NativeCanvasManager.__new__(NativeCanvasManager)
    cm.window = window
    cm.last_synced_export_hash = "PREVIOUS"
    cm.last_disk_hash = "ORIGINAL_ON_DISK"

    # Simulate another program having changed the file on disk since we last read it.
    monkeypatch.setattr("grc_agent.native_canvas._sha256_file", lambda _: "CHANGED_BY_SOMEONE_ELSE")

    write_calls = []
    monkeypatch.setattr(
        "grc_agent.native_canvas._atomic_write_text",
        lambda *a, **k: write_calls.append((a, k)),
    )

    failures = []
    cm.on_sync_failed = lambda msg: failures.append(msg)

    cm.sync_manual_edit()

    assert not write_calls, "must not overwrite a file that changed on disk underneath us"
    assert failures, "on_sync_failed was not called on a stale-disk conflict"
    assert "changed on disk" in failures[0]


def test_sync_manual_edit_does_not_block_when_lock_held(tmp_path, monkeypatch):
    """CANVAS-1 regression: sync_manual_edit must not block the single UI
    thread when the .grc lock is already held — LOCK_NB + skip (the 1.5s poll
    retries later) instead of a blocking flock."""
    import fcntl
    import threading
    from unittest.mock import MagicMock

    from grc_agent.native_canvas import NativeCanvasManager

    grc = tmp_path / "f.grc"
    grc.write_text("data")
    (tmp_path / ".grc_agent").mkdir()

    fg = MagicMock()
    da = MagicMock()
    da._flow_graph = fg
    page = MagicMock()
    page.file_path = str(grc)
    page.drawing_area = da
    window = MagicMock()
    window.current_page = page

    cm = NativeCanvasManager.__new__(NativeCanvasManager)
    cm.window = window
    cm.last_synced_export_hash = "PREVIOUS"
    cm.last_disk_hash = None

    # Make the content-hash check differ so sync proceeds to the flock.
    monkeypatch.setattr("grc_agent.native_canvas.flow_graph_content_hash", lambda _: "CURRENT")
    # Neutralize side effects if a deferred writer ever runs after lock release.
    monkeypatch.setattr(
        "grc_agent.native_canvas._atomic_write_text", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        "grc_agent.native_canvas.push_undo_snapshot", lambda *_args, **_kwargs: None
    )

    lock_path = tmp_path / ".grc_agent" / (grc.name + ".lock")
    held = lock_path.open("a", encoding="utf-8")
    fcntl.flock(held.fileno(), fcntl.LOCK_EX)
    try:
        done = threading.Event()

        errors: list[BaseException] = []

        def run() -> None:
            try:
                cm.sync_manual_edit()
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
            finally:
                done.set()

        t = threading.Thread(target=run, daemon=True)
        t.start()
        finished = done.wait(timeout=1.5)
    finally:
        fcntl.flock(held.fileno(), fcntl.LOCK_UN)
        held.close()

    assert finished, "sync_manual_edit blocked waiting for a held lock"
    assert not errors, f"sync_manual_edit raised instead of skipping: {errors!r}"


def test_check_for_unsynced_edit_logs_and_rearms(monkeypatch, caplog):
    """CANVAS-3 regression: a transient error in the safety-net poll must be
    logged (not silently swallowed) and the poll must still re-arm."""
    import logging
    from unittest.mock import MagicMock

    from grc_agent.native_canvas import NativeCanvasManager

    cm = NativeCanvasManager.__new__(NativeCanvasManager)
    da = MagicMock()
    da._flow_graph = MagicMock()
    cm.window = MagicMock()
    cm.window.current_page.drawing_area = da
    cm.window.current_page.file_path = ""
    cm.last_synced_export_hash = "X"
    # __new__ bypasses __init__, so the state-cache-version poll gate's
    # baseline must be set explicitly. None here means the cheap gate always
    # falls through to the full hash path below (the MagicMock page's
    # state_cache attributes never equal None), preserving this test's
    # original intent of exercising the full-hash error path.
    cm._last_state_cache_version = None
    cm._poll_tick_count = 0
    cm._baseline_path = ""  # matches page.file_path so the path gate stays closed

    def boom(_):
        raise RuntimeError("hash failed")

    monkeypatch.setattr("grc_agent.native_canvas.flow_graph_content_hash", boom)

    with caplog.at_level(logging.WARNING, logger="grc_agent.native_canvas"):
        assert cm._check_for_unsynced_edit() is True
    assert "hash failed" in caplog.text


def test_check_for_unsynced_edit_skips_hash_when_state_cache_unchanged(monkeypatch):
    """Efficiency fix: when GRC's own undo/redo state_cache hasn't moved since
    the last poll tick, _check_for_unsynced_edit must skip the expensive full
    flow_graph_content_hash (export+YAML+hash) entirely — that full check is
    the single biggest always-on cost this 1.5s poll incurs."""
    from unittest.mock import MagicMock

    from grc_agent.native_canvas import NativeCanvasManager

    cm = NativeCanvasManager.__new__(NativeCanvasManager)
    da = MagicMock()
    da._flow_graph = MagicMock()
    page = MagicMock()
    page.drawing_area = da
    page.state_cache.current_state_index = 3
    page.state_cache.num_prev_states = 3
    page.state_cache.num_next_states = 0
    cm.window = MagicMock()
    cm.window.current_page = page
    cm.last_synced_export_hash = "X"
    cm._last_state_cache_version = (3, 3, 0)  # matches page.state_cache exactly
    cm._poll_tick_count = 0  # ticks 1-2 below stay well short of the periodic backstop
    page.file_path = ""
    cm._baseline_path = ""  # matches page.file_path so the path gate stays closed

    call_count = 0

    def counting_hash(_):
        nonlocal call_count
        call_count += 1
        return "X"

    monkeypatch.setattr("grc_agent.native_canvas.flow_graph_content_hash", counting_hash)

    assert cm._check_for_unsynced_edit() is True
    assert call_count == 0, "unchanged state_cache must skip the full hash check"

    # A GRC-tracked edit (e.g. properties-dialog OK/Apply) bumps the state
    # cache — the next tick must fall through to the full hash check again.
    page.state_cache.current_state_index = 4
    assert cm._check_for_unsynced_edit() is True
    assert call_count == 1, "a moved state_cache must trigger the full hash check"


def test_check_for_unsynced_edit_periodic_backstop_catches_undo_then_edit_collision(monkeypatch):
    """Regression for a real gap found in adversarial testing: GRC's
    state_cache can return to the EXACT SAME (current_state_index,
    num_prev_states, num_next_states) tuple after an ordinary "undo, then make
    a different edit" sequence — the cheap gate alone would then miss that
    edit forever. A periodic backstop (_POLL_FULL_CHECK_EVERY) must still
    force the full hash check within a bounded number of ticks even when the
    state_cache tuple never appears to change."""
    from unittest.mock import MagicMock

    from grc_agent.native_canvas import _POLL_FULL_CHECK_EVERY, NativeCanvasManager

    cm = NativeCanvasManager.__new__(NativeCanvasManager)
    da = MagicMock()
    da._flow_graph = MagicMock()
    page = MagicMock()
    page.drawing_area = da
    page.state_cache.current_state_index = 5
    page.state_cache.num_prev_states = 5
    page.state_cache.num_next_states = 0
    cm.window = MagicMock()
    cm.window.current_page = page
    cm.last_synced_export_hash = "stale-hash-from-before-the-undo"
    # Baseline matches the (unchanged-looking) state_cache tuple exactly, as it
    # would after the undo+edit collision — the cheap gate alone sees no change.
    cm._last_state_cache_version = (5, 5, 0)
    cm._poll_tick_count = 0
    page.file_path = ""
    cm._baseline_path = ""  # matches page.file_path so the path gate stays closed

    call_count = 0

    def counting_hash(_):
        nonlocal call_count
        call_count += 1
        return "new-hash-after-the-collision"

    monkeypatch.setattr("grc_agent.native_canvas.flow_graph_content_hash", counting_hash)

    synced = []
    monkeypatch.setattr(cm, "sync_manual_edit", lambda h=None: synced.append(h))

    for _ in range(_POLL_FULL_CHECK_EVERY - 1):
        assert cm._check_for_unsynced_edit() is True
    assert call_count == 0, (
        "state_cache tuple never moved, so no tick before the backstop should hash"
    )

    # The Nth tick is the periodic backstop — it must force the full check
    # regardless of the (unchanged-looking) state_cache tuple.
    assert cm._check_for_unsynced_edit() is True
    assert call_count == 1, "the periodic backstop tick must run the full hash check"
    assert synced == ["new-hash-after-the-collision"], (
        "the backstop must detect and sync the content the state_cache tuple alone missed"
    )


def test_check_for_unsynced_edit_rebaselines_on_path_change(monkeypatch):
    """Saving an untitled graph in place (or Save-As to a new path) changes
    page.file_path without firing switch-page, so the safety-net poll must
    detect the path change and re-baseline. Without this, last_disk_hash stays
    None and sync_manual_edit's early return (native_canvas.py) would silently
    stop auto-persisting every later manual edit on that tab."""
    from unittest.mock import MagicMock

    from grc_agent.native_canvas import NativeCanvasManager

    cm = NativeCanvasManager.__new__(NativeCanvasManager)
    da = MagicMock()
    da._flow_graph = MagicMock()
    page = MagicMock()
    page.drawing_area = da
    page.file_path = ""  # untitled
    page.state_cache.current_state_index = 3
    page.state_cache.num_prev_states = 3
    page.state_cache.num_next_states = 0
    cm.window = MagicMock()
    cm.window.current_page = page
    cm.last_synced_export_hash = "X"
    cm._last_state_cache_version = (3, 3, 0)
    cm._poll_tick_count = 0
    cm._baseline_path = ""  # baselined against the untitled path

    synced = []

    def fake_sync_page_baselines():
        synced.append(1)
        cm._baseline_path = page.file_path  # mirror the real method's bookkeeping

    monkeypatch.setattr(cm, "_sync_page_baselines", fake_sync_page_baselines)

    # Untitled, path unchanged -> no re-baseline.
    assert cm._check_for_unsynced_edit() is True
    assert synced == []

    # User saves untitled via native Ctrl+S -> file_path becomes real, same page.
    page.file_path = "/proj/flow.grc"
    assert cm._check_for_unsynced_edit() is True
    assert len(synced) == 1, "a path change between ticks must re-baseline"

    # Baseline updated to the new path -> no repeat re-baseline on the next tick.
    assert cm._check_for_unsynced_edit() is True
    assert len(synced) == 1, "re-baseline must not fire again for the same path"


def test_sync_page_baselines_swallows_hash_error(monkeypatch):
    """CANVAS-4 regression: a hashing error during a tab switch must not
    propagate (which would leave the sidebar's active-graph label stale and
    bias the next poll against a stale baseline)."""
    from unittest.mock import MagicMock

    from grc_agent.native_canvas import NativeCanvasManager

    cm = NativeCanvasManager.__new__(NativeCanvasManager)
    fg = MagicMock()
    page = MagicMock()
    page.file_path = "/tmp/x.grc"
    page.flow_graph = fg
    cm.window = MagicMock()
    cm.window.current_page = page

    def boom(_):
        raise RuntimeError("hash failed")

    monkeypatch.setattr("grc_agent.native_canvas.flow_graph_content_hash", boom)

    cm._sync_page_baselines()  # must not raise


def test_highlight_overlay_state():
    """NativeCanvasManager.set_highlight_block sets _highlight_block_name
    and queues a redraw; clear_highlight resets it (and is a no-op, with no
    extra redraw, if nothing was highlighted)."""
    from unittest.mock import MagicMock

    from grc_agent.native_canvas import NativeCanvasManager

    da = MagicMock()
    page = MagicMock()
    page.drawing_area = da
    window = MagicMock()
    window.current_page = page

    cm = NativeCanvasManager.__new__(NativeCanvasManager)
    cm.window = window
    cm._highlight_block_name = None

    cm.set_highlight_block("test_block")
    assert cm._highlight_block_name == "test_block"
    da.queue_draw.assert_called_once()

    cm.clear_highlight()
    assert cm._highlight_block_name is None
    assert da.queue_draw.call_count == 2

    da.queue_draw.reset_mock()
    cm.clear_highlight()
    da.queue_draw.assert_not_called()


def test_highlight_overlay_draw_geometry():
    """Drives _on_draw_highlight_overlay against a REAL cairo context so the
    geometry (is_horizontal() w/h swap, translate to block.coordinate, the
    +/-pad rectangle) and the stale-block KeyError safe-fail are actually
    exercised — the field-set/queue_draw test above mocks the DrawingArea and
    never runs this path."""
    from types import SimpleNamespace

    import cairo

    from grc_agent.native_canvas import NativeCanvasManager

    class _Block:
        def __init__(self, coordinate, w, h, horizontal=True):
            self.coordinate = coordinate
            self.width = w
            self.height = h
            self._horizontal = horizontal

        def is_horizontal(self):
            return self._horizontal

    class _FG:
        def __init__(self, block):
            self._block = block
            self.blocks = [block]

        def get_block(self, name):
            if self._block.name != name:
                raise KeyError(name)
            return self._block

    block = _Block((30, 40), 100, 60, horizontal=True)
    block.name = "b0"
    fg = _FG(block)
    page = SimpleNamespace(flow_graph=fg, drawing_area=None)
    cm = NativeCanvasManager.__new__(NativeCanvasManager)
    cm.window = SimpleNamespace(current_page=page)
    cm._highlight_block_name = None
    da = SimpleNamespace(zoom_factor=1.0)

    def _render():
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 240, 200)
        cr = cairo.Context(surf)
        return surf, cr

    def _ink(surf):
        data = surf.get_data()
        return sum(1 for i in range(3, len(data), 4) if data[i] > 0)

    # No highlight set -> no draw, blank surface.
    surf, cr = _render()
    assert cm._on_draw_highlight_overlay(da, cr) is False
    assert _ink(surf) == 0

    # Real horizontal block -> draws (ink appears), returns False (chain continues).
    cm.set_highlight_block("b0")
    surf, cr = _render()
    assert cm._on_draw_highlight_overlay(da, cr) is False
    assert _ink(surf) > 0

    # Vertical block -> is_horizontal()==False swaps w/h; still draws, no raise.
    block._horizontal = False
    surf, cr = _render()
    assert cm._on_draw_highlight_overlay(da, cr) is False
    assert _ink(surf) > 0

    # Stale/renamed block -> fg.get_block raises KeyError -> safe no-op, blank.
    cm._highlight_block_name = "gone"
    surf, cr = _render()
    assert cm._on_draw_highlight_overlay(da, cr) is False
    assert _ink(surf) == 0


def test_highlight_cleared_on_tab_switch():
    """Tab switch must clear _highlight_block_name so a stale chat-badge
    hover doesn't draw an outline against the newly-switched-to flowgraph."""
    from unittest.mock import MagicMock

    from grc_agent.native_canvas import NativeCanvasManager

    cm = NativeCanvasManager.__new__(NativeCanvasManager)
    cm._highlight_block_name = "some_block"
    cm.on_graphs_changed = None
    cm._setup_drawing_area = MagicMock()
    cm._sync_page_baselines = MagicMock()

    cm._on_page_switched(MagicMock(), MagicMock(), 0)

    assert cm._highlight_block_name is None
    cm._setup_drawing_area.assert_called_once()
    cm._sync_page_baselines.assert_called_once()


def test_scroll_to_block():
    """verify NativeCanvasManager.scroll_to_block safely handles valid and invalid block lookups."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from grc_agent.native_canvas import NativeCanvasManager

    block = SimpleNamespace(states={"coordinate": [150, 300]})
    fg = SimpleNamespace(
        get_block=lambda name: block if name == "b0" else (_ for _ in ()).throw(KeyError(name)),
        get_extents=lambda: (0, 0, 1000, 1000),
    )
    adj_h = MagicMock()
    adj_h.get_upper.return_value = 500
    adj_h.get_lower.return_value = 0
    adj_h.get_page_size.return_value = 200

    adj_v = MagicMock()
    adj_v.get_upper.return_value = 800
    adj_v.get_lower.return_value = 0
    adj_v.get_page_size.return_value = 200

    sw = MagicMock()
    sw.get_hadjustment.return_value = adj_h
    sw.get_vadjustment.return_value = adj_v

    da = MagicMock()
    da.zoom_factor = 1.5

    cm = NativeCanvasManager.__new__(NativeCanvasManager)
    page = SimpleNamespace(flow_graph=fg, drawing_area=da)
    cm.window = SimpleNamespace(current_page=page)
    cm._get_scrolled_window = lambda *_a: sw

    # Valid block -> calculates target and updates adjustments
    assert cm.scroll_to_block("b0") is True
    adj_h.set_value.assert_called_once_with(225.0)  # 150 * 1.5
    adj_v.set_value.assert_called_once_with(450.0)  # 300 * 1.5

    # Missing block -> returns False
    assert cm.scroll_to_block("missing") is False


def test_fit_to_view():
    """_fit_to_view computes a zoom that fits the whole graph's extents into
    the viewport (with FIT_PAD padding), sets it through GRC's own
    _set_zoom_factor, and scrolls so the graph's center lands mid-viewport —
    with the adjustment upper raised to cover the new content size first. The
    no-blocks branch is a strict no-op (never touches zoom or adjustments)."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from grc_agent.native_canvas import NativeCanvasManager

    def _canvas(fg, da_zoom=1.0):
        adj_h = MagicMock()
        adj_h.get_upper.return_value = 500
        adj_h.get_lower.return_value = 0
        adj_h.get_page_size.return_value = 200
        adj_v = MagicMock()
        adj_v.get_upper.return_value = 500
        adj_v.get_lower.return_value = 0
        adj_v.get_page_size.return_value = 200
        sw = MagicMock()
        sw.get_hadjustment.return_value = adj_h
        sw.get_vadjustment.return_value = adj_v
        viewport = SimpleNamespace(
            get_allocation=lambda: SimpleNamespace(width=1100, height=660)
        )
        da = SimpleNamespace(
            zoom_factor=da_zoom,
            _set_zoom_factor=MagicMock(),
            get_parent=lambda: viewport,
        )
        cm = NativeCanvasManager.__new__(NativeCanvasManager)
        cm.window = SimpleNamespace(current_page=SimpleNamespace(flow_graph=fg, drawing_area=da))
        cm._get_scrolled_window = lambda *_a: sw
        return cm, da, sw

    block = SimpleNamespace(name="b0", states={"coordinate": [0, 0]})
    fg = SimpleNamespace(blocks=[block], get_extents=lambda: (0, 0, 1000, 1000))
    cm, da, sw = _canvas(fg)
    cm._fit_to_view(fg)
    # fit zoom: min(1100/(1000*1.1), 660/(1000*1.1)) = min(1.0, 0.6) = 0.6
    da._set_zoom_factor.assert_called_once_with(0.6)
    adj_h, adj_v = sw.get_hadjustment(), sw.get_vadjustment()
    # upper raised to content (1000*0.6 + 100 = 700) so the target is reachable
    adj_h.set_upper.assert_called_once_with(700.0)
    adj_v.set_upper.assert_called_once_with(700.0)
    # graph center (500*0.6 = 300) minus half the viewport clamps to 0 on both
    adj_h.set_value.assert_called_once_with(0.0)
    adj_v.set_value.assert_called_once_with(0.0)

    # Already at the fit zoom -> zoom untouched, still recentered.
    cm2, da2, sw2 = _canvas(fg, da_zoom=0.6)
    cm2._fit_to_view(fg)
    da2._set_zoom_factor.assert_not_called()
    sw2.get_hadjustment().set_value.assert_called_once()

    # No blocks -> strict no-op.
    empty = SimpleNamespace(blocks=[], get_extents=lambda: (0, 0, 1000, 1000))
    cm3, da3, sw3 = _canvas(empty)
    cm3._fit_to_view(empty)
    da3._set_zoom_factor.assert_not_called()
    sw3.get_hadjustment.assert_not_called()
    sw3.get_vadjustment.assert_not_called()


# --- Zoom observation seam + sidebar font mapping (R8/R9/R11) ---


class _FakeDrawingArea:
    """Minimal fake of GRC's DrawingArea zoom surface, mirroring the
    installed gnuradio grc/gui/DrawingArea.py exactly where it matters:
    the ``zoom_factor`` attribute, ``_set_zoom_factor`` with its same-value
    early-return (it only mutates state on a real change), and the
    ``zoom_in``/``zoom_out``/``reset_zoom`` helpers that route through it
    (Ctrl+scroll and the ZOOM_IN/OUT/RESET actions all land here).
    ``grc_zoom_sets`` counts real mutations so tests can prove the native
    method actually ran underneath the wrapper."""

    def __init__(self, zoom_factor=1.0):
        self.zoom_factor = zoom_factor
        self.grc_zoom_sets = 0

    def _set_zoom_factor(self, zoom_factor):
        if zoom_factor != self.zoom_factor:
            self.zoom_factor = zoom_factor
            self.grc_zoom_sets += 1

    def zoom_in(self):
        self._set_zoom_factor(min(self.zoom_factor * 1.2, 5.0))

    def zoom_out(self):
        self._set_zoom_factor(max(self.zoom_factor / 1.2, 0.1))

    def reset_zoom(self):
        self._set_zoom_factor(1.0)

    # No-op widget plumbing so NativeCanvasManager._setup_drawing_area runs.
    def get_parent(self):
        return None

    def add_events(self, _mask):
        pass

    def connect(self, *_args):
        pass


def _zoom_test_manager(da):
    """A NativeCanvasManager (via __new__, like every test in this file)
    whose current page holds ``da``, ready for _setup_drawing_area."""
    from types import SimpleNamespace

    from grc_agent.native_canvas import NativeCanvasManager

    page = SimpleNamespace(drawing_area=da)
    cm = NativeCanvasManager.__new__(NativeCanvasManager)
    cm.window = SimpleNamespace(current_page=page)
    cm.on_zoom_changed = None
    return cm


def test_sidebar_font_multiplier_mapping_law():
    """R9: the chat sidebar font multiplier is ONE pure, monotonic function
    of the canvas zoom — sqrt(zoom_factor) clamped to [0.7, 1.8] — exactly
    1.0 at zoom 1.0, with no per-surface branches."""
    import math

    import pytest

    from grc_agent.native_canvas import sidebar_font_multiplier as m

    # Default zoom -> exactly the theme size (no multiplier drift).
    assert m(1.0) == 1.0
    # The sqrt law at a non-trivial point: sqrt(1.44) = 1.2.
    assert m(1.44) == pytest.approx(1.2)
    # Monotonic across GRC's whole native zoom range.
    zooms = [0.1 * (5.0 / 0.1) ** (i / 50) for i in range(51)]
    values = [m(z) for z in zooms]
    assert all(b >= a for a, b in zip(values, values[1:], strict=False)), (
        "the mapping must be monotonic non-decreasing in zoom"
    )
    # In-range points follow sqrt exactly (zoom 0.64 -> 0.8, 2.25 -> 1.5:
    # both sqarts lie inside the clamp window).
    assert m(0.64) == pytest.approx(0.8)
    assert m(2.25) == pytest.approx(1.5)
    # Clamped at both ends — including beyond GRC's 0.1..5.0 zoom range.
    # GRC's 0.1 zoom floor is already below the clamp window (sqrt(0.1) ≈ 0.32
    # < 0.7), so the whole native low end clamps to 0.7; zoom 0.5 is in-range.
    assert m(0.5) == pytest.approx(math.sqrt(0.5))
    assert m(0.2) == 0.7  # sqrt(0.2) < 0.7 -> clamped
    assert m(0.1) == 0.7
    assert m(0.0) == 0.7
    assert m(3.24) == pytest.approx(1.8)  # sqrt(3.24) = 1.8 boundary
    assert m(4.0) == 1.8  # sqrt(4.0) = 2.0 > 1.8 -> clamped
    assert m(10.0) == 1.8


def test_zoom_wrapper_fires_on_real_change_only():
    """R8: the per-page _set_zoom_factor wrapper installed at setup calls
    GRC's native method and fires on_zoom_changed exactly once per REAL
    change; a same-value set (GRC's early-return path) stays silent. A second
    _setup_drawing_area on the same page (tab re-switch) must not double-wrap."""
    from types import SimpleNamespace

    da = _FakeDrawingArea(zoom_factor=1.0)
    cm = _zoom_test_manager(da)
    fired = []
    cm.on_zoom_changed = fired.append
    page = SimpleNamespace(drawing_area=da)

    cm._setup_drawing_area(page)
    cm._setup_drawing_area(page)  # _grc_agent_setup guard: no double wrap

    da._set_zoom_factor(1.5)
    assert da.zoom_factor == 1.5
    assert da.grc_zoom_sets == 1, "GRC's native _set_zoom_factor must actually run"
    assert fired == [1.5]

    da._set_zoom_factor(1.5)  # same value -> GRC early-returns -> silent
    assert da.grc_zoom_sets == 1
    assert fired == [1.5], "a same-value zoom set must not fire the callback"

    da._set_zoom_factor(0.5)
    assert fired == [1.5, 0.5]


def test_zoom_wrapper_covers_all_zoom_paths():
    """Every canvas zoom mutation path flows through the one choke point:
    GRC's zoom_in/zoom_out/reset_zoom (Ctrl+scroll and the ZOOM_IN/OUT/RESET
    actions land in these) each produce exactly one callback per real change,
    and the clamp no-ops at GRC's 0.1..5.0 extremes stay silent."""
    da = _FakeDrawingArea(zoom_factor=1.0)
    cm = _zoom_test_manager(da)
    fired = []
    cm.on_zoom_changed = fired.append

    from types import SimpleNamespace

    cm._setup_drawing_area(SimpleNamespace(drawing_area=da))

    da.zoom_in()  # 1.0 -> 1.2
    da.zoom_in()  # 1.2 -> 1.44
    da.reset_zoom()  # 1.44 -> 1.0
    da.reset_zoom()  # 1.0 -> 1.0: GRC early-return -> silent
    assert da.zoom_factor == 1.0
    assert fired == [1.2, 1.44, 1.0]

    da.zoom_out()  # 1.0 -> 1/1.2
    assert len(fired) == 4
    assert fired[-1] == da.zoom_factor

    # Zoom out to GRC's native 0.1 floor, then keep scrolling: the clamp makes
    # the set same-value, so GRC early-returns and the callback stays silent.
    while da.zoom_factor > 0.1:
        da.zoom_out()
    assert da.zoom_factor == 0.1
    clamped = len(fired)
    da.zoom_out()
    assert da.zoom_factor == 0.1
    assert len(fired) == clamped, "a clamped no-op zoom must not fire the callback"

    # Same at the 5.0 ceiling.
    while da.zoom_factor < 5.0:
        da.zoom_in()
    assert da.zoom_factor == 5.0
    clamped = len(fired)
    da.zoom_in()
    assert da.zoom_factor == 5.0
    assert len(fired) == clamped, "a clamped no-op zoom must not fire the callback"


def test_zoom_callback_unwired_real_change_does_not_crash():
    """on_zoom_changed is a peer of on_graphs_changed/on_sync_failed: None by
    default (no sidebar wired — desktop_app.py assigns it after construction),
    and a real zoom change with no callback wired must not crash."""
    from types import SimpleNamespace

    da = _FakeDrawingArea(zoom_factor=1.0)
    cm = _zoom_test_manager(da)
    assert cm.on_zoom_changed is None

    cm._setup_drawing_area(SimpleNamespace(drawing_area=da))
    da._set_zoom_factor(2.0)  # must not raise despite no callback
    assert da.zoom_factor == 2.0

    # Wiring after construction (the desktop_app.py pattern) works and fires.
    fired = []
    cm.on_zoom_changed = fired.append
    da._set_zoom_factor(1.0)
    assert fired == [1.0]


def _fit_to_view_fixtures(da):
    """Shared fake graph/viewport/scroll-window for _fit_to_view tests —
    same shapes as test_fit_to_view above: 1000x1000 extents in a
    1100x660 viewport -> fit zoom min(1.0, 0.6) = 0.6."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    adj_h = MagicMock()
    adj_h.get_upper.return_value = 500
    adj_h.get_lower.return_value = 0
    adj_h.get_page_size.return_value = 200
    adj_v = MagicMock()
    adj_v.get_upper.return_value = 500
    adj_v.get_lower.return_value = 0
    adj_v.get_page_size.return_value = 200
    sw = MagicMock()
    sw.get_hadjustment.return_value = adj_h
    sw.get_vadjustment.return_value = adj_v
    viewport = SimpleNamespace(get_allocation=lambda: SimpleNamespace(width=1100, height=660))
    da.get_parent = lambda: viewport
    block = SimpleNamespace(name="b0", states={"coordinate": [0, 0]})
    fg = SimpleNamespace(blocks=[block], get_extents=lambda: (0, 0, 1000, 1000))
    return fg, sw


def test_fit_to_view_suppresses_zoom_callback():
    """R11: the fit-to-view auto-zoom after an agent relayout is a view
    convenience, not a user zoom gesture — the canvas zoom really changes but
    on_zoom_changed must NOT fire (the chat projection must not rescale). The
    transient _zoom_is_autofit flag must be cleared afterwards, and a user
    zoom gesture right after the fit fires normally."""
    from types import SimpleNamespace

    da = _FakeDrawingArea(zoom_factor=1.0)
    cm = _zoom_test_manager(da)
    fired = []
    cm.on_zoom_changed = fired.append
    fg, sw = _fit_to_view_fixtures(da)
    cm._get_scrolled_window = lambda *_a: sw

    cm._setup_drawing_area(SimpleNamespace(drawing_area=da))
    cm._fit_to_view(fg)

    assert da.zoom_factor == 0.6, "the fit must still zoom the canvas itself"
    assert da.grc_zoom_sets == 1
    assert fired == [], "fit-to-view auto-zoom must not rescale the chat projection"
    assert cm._zoom_is_autofit is False, "the suppression flag must never stick"

    # A user zoom gesture right after the autofit fires exactly once.
    da.zoom_in()
    assert fired == [da.zoom_factor]
    assert len(fired) == 1


def test_zoom_autofit_flag_cleared_when_zoom_set_raises():
    """The _zoom_is_autofit suppression can never stick: even when the zoom
    set inside _fit_to_view raises mid-flight, the finally-style clear runs."""
    from unittest.mock import MagicMock

    da = _FakeDrawingArea(zoom_factor=1.0)
    da._set_zoom_factor = MagicMock(side_effect=RuntimeError("cairo exploded"))
    cm = _zoom_test_manager(da)
    fired = []
    cm.on_zoom_changed = fired.append
    fg, sw = _fit_to_view_fixtures(da)
    cm._get_scrolled_window = lambda *_a: sw

    cm._fit_to_view(fg)  # _fit_to_view's own handler catches the raise

    assert cm._zoom_is_autofit is False, "suppression must be cleared even on a raise"
    assert fired == []
    assert da.zoom_factor == 1.0  # the raise aborted the zoom set


def test_zoom_wrapper_after_fit_and_raised_flag_clears_before_user_zoom():
    """R11/R8 integration: the suppression window is exactly the autofit zoom
    set — a same-value autofit attempt leaves the callback armed, and the
    very next user zoom fires."""
    from types import SimpleNamespace

    da = _FakeDrawingArea(zoom_factor=0.6)  # already at the fit zoom
    cm = _zoom_test_manager(da)
    fired = []
    cm.on_zoom_changed = fired.append
    fg, sw = _fit_to_view_fixtures(da)
    cm._get_scrolled_window = lambda *_a: sw

    cm._setup_drawing_area(SimpleNamespace(drawing_area=da))
    cm._fit_to_view(fg)  # same-value set: GRC early-returns, no callback anyway
    assert da.zoom_factor == 0.6
    assert fired == []
    assert cm._zoom_is_autofit is False

    da.zoom_in()
    assert len(fired) == 1 and fired[0] == da.zoom_factor
