"""save_graph tool + proxy tests (native save parity: agent save == Ctrl+S).

Split out of test_run_stop_tools.py (the run/stop half) so each surface has
its own file. The shared hermetic fakes this suite needs (_FakeDeps, _ctx)
are duplicated here with the same shapes; the save-specific harness
(_save_page, _fake_platform, _patch_save_action, _make_save_proxy) moved
with its tests unchanged.
"""

import stat
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic_ai.exceptions import ModelRetry

from grc_agent.agent import save_graph_func
from grc_agent.native_canvas import NativeCanvasManager, NativeFlowgraphProxy


class _FakeExecMonitor:
    """The slice of ExecutionErrorMonitor the proxy relies on."""

    def __init__(self, outcome="completed", code=0, tracking=True):
        self.marked_agent_initiated = 0
        self.cancelled_agent_initiated = 0
        self._outcome = outcome
        self.last_run_code = code
        self._tracking = tracking
        self.run_epoch = 0

    def mark_run_agent_initiated(self):
        self.marked_agent_initiated += 1

    def mark_run_agent_initiated_cancelled(self):
        self.cancelled_agent_initiated += 1

    @property
    def is_tracking(self):
        return self._tracking

    async def wait_for_run_end(self, timeout, *, epoch=None):  # noqa: ARG002
        return self._outcome


@pytest.fixture(autouse=True)


def _default_loop_policy():
    """Force the plain asyncio policy — test_desktop_app installs the app's
    gbulb/GLib policy process-wide earlier in the full-suite order, and these
    tests call asyncio.run directly (same rationale as test_shell_toolset)."""
    import asyncio as _asyncio

    saved = _asyncio.get_event_loop_policy()
    _asyncio.set_event_loop_policy(_asyncio.DefaultEventLoopPolicy())
    yield
    _asyncio.set_event_loop_policy(saved)


_MONITOR_DEFAULT = object()


_MONITOR_DEFAULT = object()


def _make_proxy(page, monitor=_MONITOR_DEFAULT, outcome="completed", code=0, tracking=True):
    cm = NativeCanvasManager.__new__(NativeCanvasManager)
    cm.window = SimpleNamespace(current_page=page)
    if monitor is _MONITOR_DEFAULT:
        monitor = _FakeExecMonitor(outcome=outcome, code=code, tracking=tracking)
    proxy = NativeFlowgraphProxy(cm, exec_monitor=monitor)
    return proxy, monitor


class _FakeDeps:
    def __init__(self, proxy):
        self._proxy = proxy

    async def run_flowgraph(self, action="start", wait=True, timeout_seconds=60.0, stop_after_seconds=None):
        return await self._proxy.run_flowgraph(
            action=action,
            wait=wait,
            timeout_seconds=timeout_seconds,
            stop_after_seconds=stop_after_seconds,
        )

    async def stop_flowgraph(self):
        return await self._proxy.stop_flowgraph()

    async def save_graph(self):
        return await self._proxy.save_graph()


def _ctx(deps, tool_call_approved=True):
    return SimpleNamespace(deps=deps, tool_call_approved=tool_call_approved)


# --- save_graph (native save parity: agent save == Ctrl+S) ---


def _save_page(file_path="", options_id="default", saved=None):
    """A page shaped like GRC's notebook Page for save tests: a flow_graph
    mock with a working options-id param (GRC's SAVE_AS API surface), a
    file_path, and a saved flag (untitled pages start unsaved)."""
    fg = MagicMock()
    fg.options_block.params["id"].get_value.return_value = options_id
    if saved is None:
        saved = bool(file_path)
    return SimpleNamespace(process=None, file_path=file_path, saved=saved, flow_graph=fg)


def _fake_platform(events, content="options:\n  coordinates: [0, 0]\n"):
    """GRC's platform.save_flow_graph parity (core/platform.py:362): renders
    the flowgraph to the given filename with a plain non-atomic open-w — the
    exact reason the caller must wrap it in temp+fsync+os.replace."""
    seen = []

    def save_flow_graph(filename, flow_graph):  # noqa: ARG001
        seen.append(filename)
        events.append(("render", filename))
        with open(filename, "w", encoding="utf-8") as f:
            f.write(content)

    return SimpleNamespace(save_flow_graph=save_flow_graph), seen


def _patch_save_action(monkeypatch, events):
    """Replace GRC's FLOW_GRAPH_SAVE action with a recorder so the refresh
    tail is observable in call order — and so a synthesized action DISPATCH
    (forbidden: double write) would blow up instead of writing."""
    from grc_agent.adapter import gui_actions

    actions = gui_actions()
    monkeypatch.setattr(
        actions,
        "FLOW_GRAPH_SAVE",
        SimpleNamespace(set_enabled=lambda v: events.append(("save_enabled", v))),
        raising=False,
    )


def _make_save_proxy(page, tmp_path, monkeypatch, events, platform):
    """Proxy + canvas manager for save tests. The project directory is wired
    through fs_tools.active_project_dir — the same source the filesystem
    tools resolve their sandbox root from — and window/app are fakes that
    record the refresh tail into `events` in call order."""
    from grc_agent import fs_tools

    monkeypatch.setattr(fs_tools, "active_project_dir", lambda: tmp_path)

    window = SimpleNamespace(
        current_page=page,
        get_pages=lambda: [page],
        update=lambda: events.append(("window_update", None)),
        tool_bar=SimpleNamespace(refresh_submenus=lambda: events.append(("tool_bar", None))),
        menu=SimpleNamespace(refresh_submenus=lambda: events.append(("menu", None))),
    )
    app = SimpleNamespace(
        config=SimpleNamespace(add_recent_file=lambda p: events.append(("recent", p)))
    )
    cm = NativeCanvasManager.__new__(NativeCanvasManager)
    cm.window = window
    cm.platform = platform
    cm.app = app
    proxy = NativeFlowgraphProxy(cm, exec_monitor=None)
    return proxy, cm, window, app


def test_save_no_page_open():
    import asyncio

    proxy, _ = _make_proxy(None)
    with pytest.raises(ValueError, match="No flowgraph is open"):
        asyncio.run(proxy.save_graph())


def test_save_no_project_dir_gives_fs_tool_directive(tmp_path, monkeypatch):
    """KTD3 guard 1: no project directory -> the fs-tool directive wording,
    reused verbatim from the U1 helper, before anything else happens."""
    import asyncio

    from grc_agent import fs_tools

    events = []
    _patch_save_action(monkeypatch, events)
    page = _save_page(file_path="", options_id="default", saved=False)
    platform, seen = _fake_platform(events)
    proxy, _cm, _window, _app = _make_save_proxy(page, tmp_path, monkeypatch, events, platform)
    monkeypatch.setattr(fs_tools, "active_project_dir", lambda: None)

    with pytest.raises(ValueError, match="Select a Project directory"):
        asyncio.run(proxy.save_graph())
    assert seen == []


def test_save_preserves_existing_target_file_mode(tmp_path, monkeypatch):
    """R2 (review finding #11): mkstemp creates the temp file 0600, but a
    native Ctrl+S open-w write preserves an existing target's mode — the
    agent save must be mode-indistinguishable too."""
    import os

    events = []
    platform, _ = _fake_platform(events)
    target = tmp_path / "untitled.grc"
    target.write_text("previous content")
    os.chmod(target, 0o644)
    page = _save_page(file_path=str(target), options_id="untitled")
    proxy, *_ = _make_save_proxy(page, tmp_path, monkeypatch, events, platform)
    # Not the hash-equal path: force the real write (sibling tests patch the
    # same pair for the same reason — the real serializer imports gnuradio).
    monkeypatch.setattr(
        "grc_agent.native_canvas.flow_graph_content_hash", lambda _fg: "different"
    )
    import asyncio

    result = asyncio.run(proxy.save_graph())
    assert result["path"] == str(target)
    assert (stat.S_IMODE(os.stat(target).st_mode)) == 0o644


def test_save_untitled_happy_path_atomic_write_and_surface_tail(tmp_path, monkeypatch):
    """AE1: an untitled page saves into the project directory with no modal:
    GRC's platform renders a TEMP file in the target directory, fsync +
    os.replace land it at the derived path, and the SAVE_AS-parity surface
    tail fires in GRC's own order — id rename first (KTD1: BEFORE
    serialization), then state, Save enablement, window update, and recents
    + submenu refreshes on first naming."""
    import asyncio
    import hashlib as _hashlib
    import os as _os
    from pathlib import Path as _Path

    events = []
    _patch_save_action(monkeypatch, events)
    monkeypatch.setattr("grc_agent.native_canvas.flow_graph_content_hash", lambda _fg: "HASH")

    real_replace = _os.replace

    def _recording_replace(src, dst):
        events.append(("replace", str(src), str(dst)))
        real_replace(src, dst)

    monkeypatch.setattr("grc_agent.native_canvas.os.replace", _recording_replace)

    page = _save_page(file_path="", options_id="default", saved=False)
    page.flow_graph.options_block.params["id"].set_value.side_effect = lambda v: events.append(
        ("set_id", v)
    )
    page.flow_graph.update.side_effect = lambda: events.append(("fg_update", None))
    platform, _seen = _fake_platform(events)
    proxy, cm, _window, _app = _make_save_proxy(page, tmp_path, monkeypatch, events, platform)

    res = asyncio.run(proxy.save_graph())

    target = tmp_path / "untitled.grc"
    assert res == {"path": str(target), "page": "untitled"}
    assert target.read_text(encoding="utf-8") == "options:\n  coordinates: [0, 0]\n"
    # atomic seam: rendered to a temp sibling, then replaced into place; no
    # temp may survive.
    renders = [e for e in events if e[0] == "render"]
    replaces = [e for e in events if e[0] == "replace"]
    assert len(renders) == 1 and len(replaces) == 1
    tmp_rendered = _Path(renders[0][1])
    assert tmp_rendered.parent == tmp_path and tmp_rendered.name != target.name
    assert replaces[0][1] == str(tmp_rendered) and replaces[0][2] == str(target)
    assert not tmp_rendered.exists()
    leftovers = [
        p.name for p in tmp_path.iterdir() if p.name not in ("untitled.grc", ".grc_agent")
    ]
    assert not leftovers, f"stray files after save: {leftovers}"
    # KTD1: SAVE_AS-parity id rename happened, before serialization.
    page.flow_graph.options_block.params["id"].set_value.assert_called_once_with("untitled")
    # KTD4 page state + R7 baselines.
    assert page.file_path == str(target)
    assert page.flow_graph.grc_file_path == str(target)
    assert page.saved is True
    assert cm.last_synced_export_hash == "HASH"
    assert cm.last_disk_hash == _hashlib.sha256(target.read_bytes()).hexdigest()
    # Full call order: id rename + graph refresh -> render -> replace ->
    # Save enablement -> window update -> recents + submenu refreshes.
    assert [e[0] for e in events] == [
        "set_id",
        "fg_update",
        "render",
        "replace",
        "save_enabled",
        "window_update",
        "recent",
        "tool_bar",
        "menu",
    ]
    assert events[4][1] is False  # page.saved True -> Save action disabled, GRC parity
    assert events[6][1] == str(target)  # add_recent_file on first naming


def test_save_titled_page_saves_in_place_without_rename(tmp_path, monkeypatch):
    """KTD1: a titled page derives nothing and renames nothing — the existing
    file_path is the target; the options id and the graph refresh stay
    untouched, and no recent-file/submenu tail fires (not first naming)."""
    import asyncio
    from pathlib import Path as _Path

    events = []
    _patch_save_action(monkeypatch, events)
    monkeypatch.setattr("grc_agent.native_canvas.flow_graph_content_hash", lambda _fg: "HASH")

    target = tmp_path / "existing.grc"
    target.write_text("OLD CONTENT", encoding="utf-8")
    page = _save_page(file_path=str(target), options_id="my_radio", saved=False)
    platform, seen = _fake_platform(events, content="NEW CONTENT")
    proxy, _cm, _window, _app = _make_save_proxy(page, tmp_path, monkeypatch, events, platform)

    res = asyncio.run(proxy.save_graph())

    assert res == {"path": str(target), "page": "existing"}
    assert target.read_text(encoding="utf-8") == "NEW CONTENT"
    page.flow_graph.options_block.params["id"].set_value.assert_not_called()
    page.flow_graph.update.assert_not_called()
    # rendered via a temp in the same dir, replaced onto the titled path
    assert len(seen) == 1
    assert _Path(seen[0]).parent == tmp_path and _Path(seen[0]).name != target.name
    assert not _Path(seen[0]).exists()
    assert page.saved is True and page.file_path == str(target)
    assert not [e for e in events if e[0] in ("recent", "tool_bar", "menu")]


def test_save_untitled_collision_takes_next_name_and_renames_id(tmp_path, monkeypatch):
    """AE2: untitled.grc already on disk (different content) -> the save lands
    at untitled(1).grc, the options id becomes untitled_1 (SAVE_AS parity,
    applied BEFORE serialization), and the original stays byte-identical."""
    import asyncio

    events = []
    _patch_save_action(monkeypatch, events)
    monkeypatch.setattr("grc_agent.native_canvas.flow_graph_content_hash", lambda _fg: "HASH")
    page = _save_page(file_path="", options_id="default", saved=False)
    page.flow_graph.options_block.params["id"].set_value.side_effect = lambda v: events.append(
        ("set_id", v)
    )

    original = tmp_path / "untitled.grc"
    original.write_text("ORIGINAL", encoding="utf-8")

    platform, _seen = _fake_platform(events, content="NEW")
    proxy, _cm, _window, _app = _make_save_proxy(page, tmp_path, monkeypatch, events, platform)

    res = asyncio.run(proxy.save_graph())

    assert res == {"path": str(tmp_path / "untitled(1).grc"), "page": "untitled(1)"}
    assert original.read_text(encoding="utf-8") == "ORIGINAL"
    assert (tmp_path / "untitled(1).grc").read_text(encoding="utf-8") == "NEW"
    assert [e for e in events if e[0] == "set_id"] == [("set_id", "untitled_1")]
    kinds = [e[0] for e in events]
    assert kinds.index("set_id") < kinds.index("render"), "id rename must precede serialization"


def test_save_untitled_non_default_id_reuses_id_without_rename(tmp_path, monkeypatch):
    """KTD1 edge: an untitled page whose options id already equals the derived
    file stem takes <id>.grc and performs no id rename/refresh."""
    import asyncio

    events = []
    _patch_save_action(monkeypatch, events)
    monkeypatch.setattr("grc_agent.native_canvas.flow_graph_content_hash", lambda _fg: "HASH")
    page = _save_page(file_path="", options_id="my_radio", saved=False)
    platform, _seen = _fake_platform(events, content="X")
    proxy, _cm, _window, _app = _make_save_proxy(page, tmp_path, monkeypatch, events, platform)

    res = asyncio.run(proxy.save_graph())

    assert res == {"path": str(tmp_path / "my_radio.grc"), "page": "my_radio"}
    page.flow_graph.options_block.params["id"].set_value.assert_not_called()
    page.flow_graph.update.assert_not_called()
    assert (tmp_path / "my_radio.grc").exists()


def test_save_hash_equal_target_updates_state_without_writing(tmp_path, monkeypatch):
    """AE3: the export the writer would produce is byte-identical to the file
    on disk (adapter's serialized-export content hash == the canvas's raw-file
    sha256 — the exact pair the manual-edit poll compares): state-only update
    — no platform render, no new file, but the page state and surface tail
    still land."""
    import asyncio
    import hashlib as _hashlib

    target = tmp_path / "flow.grc"
    target.write_text("ON DISK BYTES", encoding="utf-8")
    disk_hash = _hashlib.sha256(target.read_bytes()).hexdigest()
    monkeypatch.setattr("grc_agent.native_canvas.flow_graph_content_hash", lambda _fg: disk_hash)

    events = []
    _patch_save_action(monkeypatch, events)
    page = _save_page(file_path=str(target), options_id="flow", saved=False)
    platform, seen = _fake_platform(events)
    proxy, cm, _window, _app = _make_save_proxy(page, tmp_path, monkeypatch, events, platform)

    res = asyncio.run(proxy.save_graph())

    assert res == {"path": str(target), "page": "flow"}
    assert seen == [], "platform.save_flow_graph must not be called on a byte-equal target"
    assert target.read_text(encoding="utf-8") == "ON DISK BYTES"
    assert [p.name for p in tmp_path.iterdir() if p.is_file()] == ["flow.grc"]
    assert page.saved is True and page.file_path == str(target)
    assert cm.last_synced_export_hash == disk_hash
    assert cm.last_disk_hash == disk_hash
    assert ("window_update", None) in events  # state-only tail still refreshes the surface


def test_save_target_open_in_another_tab_names_it(tmp_path, monkeypatch):
    """AE4: the derived path is already open in another tab -> ValueError
    naming that tab, nothing rendered, nothing written."""
    import asyncio

    events = []
    _patch_save_action(monkeypatch, events)
    monkeypatch.setattr("grc_agent.native_canvas.flow_graph_content_hash", lambda _fg: "HASH")
    page = _save_page(file_path="", options_id="default", saved=False)
    other = SimpleNamespace(file_path=str(tmp_path / "untitled.grc"))
    platform, seen = _fake_platform(events)
    proxy, _cm, window, _app = _make_save_proxy(page, tmp_path, monkeypatch, events, platform)
    window.get_pages = lambda: [other, page]

    with pytest.raises(ValueError, match="another tab") as excinfo:
        asyncio.run(proxy.save_graph())
    assert "untitled" in str(excinfo.value), "the error must name the tab"
    assert seen == []
    assert not (tmp_path / "untitled.grc").exists()


def test_save_unwritable_target_fails_before_write(tmp_path, monkeypatch):
    """KTD3 guard 3: an existing read-only target fails before anything is
    touched — page.saved ends False, GRC's own fail-save console message is
    sent, and the tool layer gets a retryable ValueError."""
    import asyncio

    events = []
    _patch_save_action(monkeypatch, events)
    monkeypatch.setattr("grc_agent.native_canvas.flow_graph_content_hash", lambda _fg: "HASH")
    fail_save = []
    monkeypatch.setattr(
        "gnuradio.grc.core.Messages.send_fail_save", lambda p: fail_save.append(p)
    )

    target = tmp_path / "flow.grc"
    target.write_text("RO", encoding="utf-8")
    monkeypatch.setattr("grc_agent.native_canvas.os.access", lambda *_a, **_k: False)

    page = _save_page(file_path=str(target), options_id="flow", saved=True)
    platform, seen = _fake_platform(events)
    proxy, _cm, _window, _app = _make_save_proxy(page, tmp_path, monkeypatch, events, platform)

    with pytest.raises(ValueError, match="read-only"):
        asyncio.run(proxy.save_graph())
    assert page.saved is False
    assert fail_save == [str(target)]
    assert seen == []
    assert target.read_text(encoding="utf-8") == "RO"


def test_save_ioerror_is_atomic_and_reports_fail_save(tmp_path, monkeypatch):
    """IOError from GRC's platform write: the target is untouched (temp cleaned
    up — the atomic seam), page.saved is False, the fail-save console message
    is sent, and the tool layer gets a ValueError."""
    import asyncio

    events = []
    _patch_save_action(monkeypatch, events)
    monkeypatch.setattr("grc_agent.native_canvas.flow_graph_content_hash", lambda _fg: "HASH")
    fail_save = []
    monkeypatch.setattr(
        "gnuradio.grc.core.Messages.send_fail_save", lambda p: fail_save.append(p)
    )

    target = tmp_path / "flow.grc"
    target.write_text("KEEP", encoding="utf-8")
    page = _save_page(file_path=str(target), options_id="flow", saved=True)

    def _boom(filename, flow_graph):  # noqa: ARG001
        with open(filename, "w", encoding="utf-8") as f:
            f.write("PARTIAL")
        raise OSError("disk on fire")  # IOError alias — what GRC's handler catches

    platform = SimpleNamespace(save_flow_graph=_boom)
    proxy, _cm, _window, _app = _make_save_proxy(page, tmp_path, monkeypatch, events, platform)

    with pytest.raises(ValueError, match="disk on fire"):
        asyncio.run(proxy.save_graph())
    assert target.read_text(encoding="utf-8") == "KEEP"
    assert page.saved is False
    assert fail_save == [str(target)]
    assert [p.name for p in tmp_path.iterdir() if p.is_file()] == ["flow.grc"], (
        "temp file must be cleaned up"
    )


def test_save_refreshes_baselines_so_no_spurious_sync_fires(tmp_path, monkeypatch):
    """R7: immediately after the save the disk/export baselines point at the
    saved path, so one full _check_for_unsynced_edit tick performs no
    sync_manual_edit call (no spurious manual-edit sync)."""
    import asyncio

    events = []
    _patch_save_action(monkeypatch, events)
    monkeypatch.setattr("grc_agent.native_canvas.flow_graph_content_hash", lambda _fg: "HASH")

    page = _save_page(file_path="", options_id="default", saved=False)
    da = MagicMock()
    da._flow_graph = page.flow_graph
    page.drawing_area = da
    page.state_cache = SimpleNamespace(
        current_state_index=2, num_prev_states=2, num_next_states=0
    )

    platform, _seen = _fake_platform(events, content="SAVED")
    proxy, cm, _window, _app = _make_save_proxy(page, tmp_path, monkeypatch, events, platform)
    res = asyncio.run(proxy.save_graph())
    target = tmp_path / "untitled.grc"

    assert res == {"path": str(target), "page": "untitled"}
    assert cm.last_synced_export_hash == "HASH"
    assert cm._baseline_path == str(target)
    assert cm.last_disk_hash
    assert cm._last_state_cache_version == (2, 2, 0)

    sync_calls = []
    monkeypatch.setattr(cm, "sync_manual_edit", lambda *a, **k: sync_calls.append((a, k)))
    cm._poll_tick_count = 0
    assert cm._check_for_unsynced_edit() is True
    assert sync_calls == [], "the tick right after an agent save must not fire a manual-edit sync"


def test_save_lock_contention_defers_truthfully_without_writing(tmp_path, monkeypatch):
    """KTD2/R4: the per-graph lock (target dir/.grc_agent/<name>.lock — the
    sync_manual_edit rule, parameterized for the derived target) is held ->
    a truthful defer ValueError, no render, no partial write."""
    import asyncio
    import fcntl

    events = []
    _patch_save_action(monkeypatch, events)
    monkeypatch.setattr("grc_agent.native_canvas.flow_graph_content_hash", lambda _fg: "HASH")

    page = _save_page(file_path="", options_id="default", saved=False)
    platform, seen = _fake_platform(events)
    proxy, _cm, _window, _app = _make_save_proxy(page, tmp_path, monkeypatch, events, platform)

    lock_path = tmp_path / ".grc_agent" / "untitled.grc.lock"
    lock_path.parent.mkdir(exist_ok=True)
    held = lock_path.open("a", encoding="utf-8")
    fcntl.flock(held.fileno(), fcntl.LOCK_EX)
    try:
        with pytest.raises(ValueError, match="locked by another writer"):
            asyncio.run(proxy.save_graph())
    finally:
        fcntl.flock(held.fileno(), fcntl.LOCK_UN)
        held.close()
    assert seen == []
    assert not (tmp_path / "untitled.grc").exists()
    assert not [p for p in tmp_path.iterdir() if p.is_file()], "no temp files may be left"


# --- save_graph tool (agent-side save exposed to the model) ---


def test_tool_save_graph_returns_path_and_page(tmp_path, monkeypatch):
    """R1: the tool drives the proxy's real save and returns its {"path",
    "page"} payload as JSON — an untitled page lands in the project
    directory, ready for a subsequent run_flowgraph."""
    import asyncio
    import json

    events = []
    _patch_save_action(monkeypatch, events)
    monkeypatch.setattr("grc_agent.native_canvas.flow_graph_content_hash", lambda _fg: "HASH")
    page = _save_page(file_path="", options_id="default", saved=False)
    platform, _seen = _fake_platform(events)
    proxy, _cm, _window, _app = _make_save_proxy(page, tmp_path, monkeypatch, events, platform)

    res = json.loads(asyncio.run(save_graph_func(_ctx(_FakeDeps(proxy)))))

    assert res == {"path": str(tmp_path / "untitled.grc"), "page": "untitled"}
    assert (tmp_path / "untitled.grc").exists()


def test_tool_save_graph_wraps_proxy_valueerror_as_modelretry(tmp_path, monkeypatch):
    """Proxy guard ValueErrors cross the tool boundary as ModelRetry carrying
    the page/path detail — here the derived path is already open in another
    tab, so the model can self-correct from the message alone."""
    import asyncio

    events = []
    _patch_save_action(monkeypatch, events)
    monkeypatch.setattr("grc_agent.native_canvas.flow_graph_content_hash", lambda _fg: "HASH")
    page = _save_page(file_path="", options_id="default", saved=False)
    other = SimpleNamespace(file_path=str(tmp_path / "untitled.grc"))
    platform, seen = _fake_platform(events)
    proxy, _cm, window, _app = _make_save_proxy(page, tmp_path, monkeypatch, events, platform)
    window.get_pages = lambda: [other, page]

    with pytest.raises(ModelRetry) as excinfo:
        asyncio.run(save_graph_func(_ctx(_FakeDeps(proxy))))

    msg = str(excinfo.value)
    assert "another tab" in msg
    assert "untitled.grc" in msg
    assert seen == [], "a failed save must not have written anything"


def test_tool_save_graph_requires_wired_deps():
    """Unwired deps (raw flowgraph in some test contexts) -> the environment-
    fault convention with explicit do-not-retry phrasing: a wiring fault must
    not become a retry loop."""
    import asyncio

    with pytest.raises(ModelRetry, match="wiring") as excinfo:
        asyncio.run(save_graph_func(_ctx(SimpleNamespace())))
    assert "Do not retry" in str(excinfo.value)


def test_grc_tools_save_graph_registration():
    """save_graph is registered next to run_flowgraph with the house style:
    approval-free (a save is local, atomic, never destructive) and
    max_retries=3 like its sibling flowgraph tools."""
    from grc_agent.agent import grc_tools

    tools = grc_tools()
    tool = {t.name: t for t in tools}["save_graph"]
    assert tool.max_retries == 3
    assert tool.requires_approval is False
    names = [t.name for t in tools]
    assert names.index("save_graph") > names.index("run_flowgraph")
