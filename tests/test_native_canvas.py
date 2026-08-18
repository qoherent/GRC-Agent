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

        def run() -> None:
            try:
                cm.sync_manual_edit()
            except Exception:  # noqa: BLE001
                pass
            finally:
                done.set()

        t = threading.Thread(target=run, daemon=True)
        t.start()
        finished = done.wait(timeout=1.5)
    finally:
        fcntl.flock(held.fileno(), fcntl.LOCK_UN)
        held.close()

    assert finished, "sync_manual_edit blocked waiting for a held lock"


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
    cm.last_synced_export_hash = "X"
    # __new__ bypasses __init__, so the state-cache-version poll gate's
    # baseline must be set explicitly. None here means the cheap gate always
    # falls through to the full hash path below (the MagicMock page's
    # state_cache attributes never equal None), preserving this test's
    # original intent of exercising the full-hash error path.
    cm._last_state_cache_version = None
    cm._poll_tick_count = 0

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


def test_scroll_to_relaid_out_graph():
    """All three branches of _scroll_to_relaid_out_graph: the new blocks'
    POST-relayout corner is the reframe target (compute_full_layout can move
    every block — the new blocks' corner is where the action is, not the
    whole-graph top-left which is often empty header-band space); the whole
    bbox top-left when the new blocks carry no coordinates; and a no-op when
    there are no new blocks at all (update_params/remove-only edits never
    run the relayout)."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from grc_agent.native_canvas import NativeCanvasManager

    def _canvas(fg):
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
        da = SimpleNamespace(zoom_factor=1.0)
        cm = NativeCanvasManager.__new__(NativeCanvasManager)
        page = SimpleNamespace(flow_graph=fg, drawing_area=da)
        cm.window = SimpleNamespace(current_page=page)
        cm._get_scrolled_window = lambda *_a: sw
        return cm, sw

    # Branch 1: new block carries post-relayout coords -> reframe to its corner.
    old_block = SimpleNamespace(name="old_1", states={"coordinate": [900, 900]})
    new_block = SimpleNamespace(name="new_1", states={"coordinate": [50, 50]})
    fg = SimpleNamespace(blocks=[old_block, new_block], get_extents=lambda: (0, 0, 1000, 1000))
    cm, _ = _canvas(fg)
    cm._scroll_to_relaid_out_graph(fg, old_names={"old_1"})
    sw = cm._get_scrolled_window()
    adj_h, adj_v = sw.get_hadjustment(), sw.get_vadjustment()
    # New block's post-relayout corner (50, 50) -- NOT the whole-graph top-left
    # (0, 0), nor the old block's corner (900, 900).
    adj_h.set_value.assert_called_once_with(50.0)
    adj_v.set_value.assert_called_once_with(50.0)

    # Branch 2: new blocks without coordinates -> whole-graph bbox top-left.
    new_block = SimpleNamespace(name="new_1", states={})
    fg = SimpleNamespace(blocks=[old_block, new_block], get_extents=lambda: (0, 0, 1000, 1000))
    cm, _ = _canvas(fg)
    cm._scroll_to_relaid_out_graph(fg, old_names={"old_1"})
    sw = cm._get_scrolled_window()
    sw.get_hadjustment().set_value.assert_called_once_with(0.0)
    sw.get_vadjustment().set_value.assert_called_once_with(0.0)

    # Branch 3: no new blocks -> strictly a no-op (no adjustment access).
    block = SimpleNamespace(name="only_1", states={"coordinate": [50, 50]})
    fg = SimpleNamespace(blocks=[block], get_extents=lambda: (0, 0, 1000, 1000))
    cm, _ = _canvas(fg)
    cm._scroll_to_relaid_out_graph(fg, old_names={"only_1"})
    sw = cm._get_scrolled_window()
    sw.get_hadjustment.assert_not_called()
    sw.get_vadjustment.assert_not_called()

    sw.get_vadjustment.assert_not_called()
