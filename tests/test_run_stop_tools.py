"""Tests for the run_flowgraph / stop_flowgraph agent tools and their
NativeFlowgraphProxy wiring.

Hermetic: no display, no live LLM. The GRC Actions module is monkeypatched
(the proxy imports it lazily), the canvas manager and exec monitor are fakes
built the same way tests/test_native_canvas.py builds them, and the tool
functions are driven through fake deps objects the way
tests/test_exec_monitor.py drives get_run_log.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic_ai.exceptions import ApprovalRequired, ModelRetry

from grc_agent.agent import run_flowgraph_func
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


def _make_proxy(page, monitor=_MONITOR_DEFAULT, outcome="completed", code=0, tracking=True):
    cm = NativeCanvasManager.__new__(NativeCanvasManager)
    cm.window = SimpleNamespace(current_page=page)
    if monitor is _MONITOR_DEFAULT:
        monitor = _FakeExecMonitor(outcome=outcome, code=code, tracking=tracking)
    proxy = NativeFlowgraphProxy(cm, exec_monitor=monitor)
    return proxy, monitor


def _valid_page(process=None, file_path="/tmp/proj/flow.grc"):
    fg = MagicMock()
    fg.validate.return_value = None
    fg.is_valid.return_value = True
    fg.iter_error_messages.return_value = iter([])
    return SimpleNamespace(process=process, file_path=file_path, saved=True, flow_graph=fg)


@pytest.fixture
def fake_actions(monkeypatch):
    """Replace GRC's Actions singletons with recorders.

    The proxy resolves them lazily through adapter.gui_actions(), so
    patching the Actions namespace attributes covers every call site.
    """
    from grc_agent.adapter import gui_actions

    actions = gui_actions()
    exec_action = MagicMock(name="FLOW_GRAPH_EXEC")
    kill_action = MagicMock(name="FLOW_GRAPH_KILL")
    monkeypatch.setattr(actions, "FLOW_GRAPH_EXEC", exec_action, raising=False)
    monkeypatch.setattr(actions, "FLOW_GRAPH_KILL", kill_action, raising=False)
    return SimpleNamespace(exec_action=exec_action, kill_action=kill_action)


# --- proxy gates (each pre-empts a real GRC failure mode) ---


def test_run_gates_no_monitor():
    proxy, _ = _make_proxy(_valid_page(), monitor=None)
    with pytest.raises(ValueError, match="run monitor is not wired"):
        import asyncio

        asyncio.run(proxy.run_flowgraph(action="start"))


def test_run_gates_no_page():
    proxy, _ = _make_proxy(None)
    with pytest.raises(ValueError, match="No flowgraph is open"):
        import asyncio

        asyncio.run(proxy.run_flowgraph(action="start"))


def test_run_gates_already_running(fake_actions):
    proxy, _ = _make_proxy(_valid_page(process=object()))
    with pytest.raises(ValueError, match="already in progress"):
        import asyncio

        asyncio.run(proxy.run_flowgraph(action="start"))
    fake_actions.exec_action.assert_not_called()


def test_run_gates_unsaved_page(fake_actions):
    proxy, _ = _make_proxy(_valid_page(file_path=""))
    with pytest.raises(ValueError, match="never been saved"):
        import asyncio

        asyncio.run(proxy.run_flowgraph(action="start"))
    fake_actions.exec_action.assert_not_called()


def test_run_gates_invalid_graph(fake_actions):
    page = _valid_page()
    page.flow_graph.is_valid.return_value = False
    page.flow_graph.iter_error_messages.return_value = iter(
        ["src_0: Port is not connected", "sink_0: Port is not connected"]
    )
    proxy, _ = _make_proxy(page)
    with pytest.raises(ValueError, match="Port is not connected"):
        import asyncio

        asyncio.run(proxy.run_flowgraph(action="start"))
    # validate() must run before is_valid() is trusted (Elements only populate
    # _error_messages on an explicit validate call).
    page.flow_graph.validate.assert_called_once()
    fake_actions.exec_action.assert_not_called()


# --- proxy happy paths ---


def test_run_no_wait_returns_started_and_marks_agent_initiated(fake_actions):
    proxy, monitor = _make_proxy(_valid_page())

    import asyncio

    res = asyncio.run(proxy.run_flowgraph(action="start", wait=False))

    assert res["status"] == "started"
    assert "get_run_log" in res["note"]
    assert monitor.marked_agent_initiated == 1
    fake_actions.exec_action.set_enabled.assert_called_once_with(True)
    fake_actions.exec_action.assert_called_once_with()


@pytest.mark.usefixtures("fake_actions")
def test_run_wait_completed():
    proxy, _ = _make_proxy(_valid_page(), outcome="completed", code=0)

    import asyncio

    res = asyncio.run(proxy.run_flowgraph(action="start", wait=True, timeout_seconds=5))

    assert res["status"] == "completed"
    assert res["return_code"] == 0
    assert res["ran_successfully"] is True
    assert "get_run_log" in res["note"]


@pytest.mark.usefixtures("fake_actions")
def test_run_wait_still_running():
    proxy, _ = _make_proxy(_valid_page(), outcome="still_running")

    import asyncio

    res = asyncio.run(proxy.run_flowgraph(action="start", wait=True, timeout_seconds=1))

    assert res["status"] == "still_running"
    assert "run_flowgraph" in res["note"]


class _BoundedRunMonitor(_FakeExecMonitor):
    """Serves the killer path: the run is still going at the deadline, then its
    terminal marker lands (after the auto-stop's SIGTERM). On the first wait the
    page's process is set (spawn=True), simulating GRC spawning the run; with
    spawn=False it stays None, simulating a run that finished on its own at the
    deadline."""

    def __init__(self, page, code=-15, spawn=True):
        super().__init__(outcome="completed", code=code, tracking=True)
        self._page = page
        self._spawn = spawn
        self._first = True

    async def wait_for_run_end(self, timeout, *, epoch=None):  # noqa: ARG002
        if self._first:
            self._first = False
            if self._spawn:
                self._page.process = object()
            return "still_running"
        return "completed"


def test_run_stop_after_requires_wait_true():
    """stop_after_seconds is a bounded-run intent: with wait=False the call
    returns before anything could enforce the deadline, so it is rejected
    up front (uniform rule, no background task machinery)."""
    import asyncio

    proxy, _ = _make_proxy(_valid_page())
    with pytest.raises(ValueError, match="requires wait=True"):
        asyncio.run(proxy.run_flowgraph(action="start", wait=False, stop_after_seconds=10))


def test_run_stop_after_requires_positive():
    import asyncio

    proxy, _ = _make_proxy(_valid_page())
    with pytest.raises(ValueError, match="positive"):
        asyncio.run(proxy.run_flowgraph(action="start", wait=True, stop_after_seconds=0))


@pytest.mark.usefixtures("fake_actions")
def test_run_bounded_run_auto_stops_at_deadline(fake_actions):
    """The run exceeds stop_after_seconds: the auto-stop fires the same native
    KILL action the toolbar Stop button takes, and the result reports the
    deliberate stop with the SIGTERM return code."""
    import asyncio

    page = _valid_page()
    proxy, monitor = _make_proxy(page, monitor=_BoundedRunMonitor(page, code=-15))
    res = asyncio.run(proxy.run_flowgraph(action="start", wait=True, stop_after_seconds=5))

    assert res["status"] == "stopped_after_timeout"
    assert res["return_code"] == -15
    assert monitor.marked_agent_initiated == 1
    fake_actions.kill_action.assert_called_once()


@pytest.mark.usefixtures("fake_actions")
def test_run_bounded_run_finished_at_deadline_reports_completed(fake_actions):
    """The run ends on its own right at the deadline (page.process is already
    None), so no stop is performed — the real outcome wins over a stop we never
    made."""
    import asyncio

    page = _valid_page()
    proxy, _ = _make_proxy(page, monitor=_BoundedRunMonitor(page, code=0, spawn=False))
    res = asyncio.run(proxy.run_flowgraph(action="start", wait=True, stop_after_seconds=5))

    assert res["status"] == "completed"
    assert res["return_code"] == 0
    assert res["ran_successfully"] is True
    fake_actions.kill_action.assert_not_called()



@pytest.mark.usefixtures("fake_actions")
def test_run_wait_not_started():
    proxy, _ = _make_proxy(_valid_page(), outcome="not_started")

    import asyncio

    res = asyncio.run(proxy.run_flowgraph(action="start", wait=True))

    assert res["status"] == "not_started"


def test_run_silent_noop_cancels_agent_initiated_flag():
    """If the Execute action returns without the synchronous start marker
    (silent no-op), the suppression flag must not linger — a later
    user-initiated run failure would otherwise never notify."""
    import asyncio

    proxy, monitor = _make_proxy(
        _valid_page(), outcome="not_started", tracking=False
    )
    res = asyncio.run(proxy.run_flowgraph(action="start", wait=True))
    assert res["status"] == "not_started"
    assert monitor.marked_agent_initiated == 1
    assert monitor.cancelled_agent_initiated == 1


def test_run_exception_after_mark_cancels_flag(monkeypatch):
    """If the Execute action raises, the suppression flag is dropped so it
    cannot leak into a later user-initiated run's failure notification."""
    import asyncio

    from grc_agent.adapter import gui_actions

    actions = gui_actions()

    class _Boom:
        def set_enabled(self, _v):
            pass

        def __call__(self):
            raise RuntimeError("boom")

    monkeypatch.setattr(actions, "FLOW_GRAPH_EXEC", _Boom(), raising=False)
    proxy, monitor = _make_proxy(_valid_page(), outcome="completed", tracking=True)
    with pytest.raises(RuntimeError):
        asyncio.run(proxy.run_flowgraph(action="start", wait=True))
    assert monitor.marked_agent_initiated == 1
    assert monitor.cancelled_agent_initiated == 1


def test_proxy_run_flowgraph_action_stop(fake_actions):
    proxy, _ = _make_proxy(_valid_page(process=None))

    import asyncio

    res = asyncio.run(proxy.run_flowgraph(action="stop"))

    assert res["status"] == "not_running"
    fake_actions.kill_action.assert_not_called()


def test_stop_no_process_is_clean_noop(fake_actions):
    proxy, _ = _make_proxy(_valid_page(process=None))

    import asyncio

    res = asyncio.run(proxy.stop_flowgraph())

    assert res["status"] == "not_running"
    fake_actions.kill_action.assert_not_called()


def test_stop_terminates_via_native_action(fake_actions):
    proxy, _ = _make_proxy(_valid_page(process=object()), outcome="completed")

    import asyncio

    res = asyncio.run(proxy.stop_flowgraph())

    assert res["status"] == "stopped"
    fake_actions.kill_action.set_enabled.assert_called_once_with(True)
    fake_actions.kill_action.assert_called_once_with()


@pytest.mark.usefixtures("fake_actions")
def test_stop_pending_when_shutdown_lingers():
    proxy, _ = _make_proxy(_valid_page(process=object()), outcome="still_running")

    import asyncio

    res = asyncio.run(proxy.stop_flowgraph())

    assert res["status"] == "stop_requested"


# --- tool functions ---


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


def _ctx(deps, tool_call_approved=True):
    return SimpleNamespace(deps=deps, tool_call_approved=tool_call_approved)


def test_tool_run_flowgraph_requires_approval_on_start():
    import asyncio

    ctx = _ctx(SimpleNamespace(), tool_call_approved=False)
    with pytest.raises(ApprovalRequired):
        asyncio.run(
            run_flowgraph_func(ctx, action="start", wait=True, timeout_seconds=1)
        )


def test_tool_run_flowgraph_requires_wired_deps():
    import asyncio

    with pytest.raises(ModelRetry, match="wiring"):
        asyncio.run(
            run_flowgraph_func(_ctx(SimpleNamespace(), tool_call_approved=True), action="start", wait=True, timeout_seconds=1)
        )


def test_tool_run_flowgraph_invalid_action():
    import asyncio

    with pytest.raises(ModelRetry, match="Invalid action"):
        asyncio.run(
            run_flowgraph_func(_ctx(SimpleNamespace()), action="invalid")  # type: ignore[arg-type]
        )


@pytest.mark.usefixtures("fake_actions")
def test_tool_run_flowgraph_wraps_gate_errors_as_modelretry():
    import asyncio

    proxy, _ = _make_proxy(_valid_page(file_path=""))
    with pytest.raises(ModelRetry, match="never been saved"):
        asyncio.run(run_flowgraph_func(_ctx(_FakeDeps(proxy), tool_call_approved=True), action="start", wait=True, timeout_seconds=1))


@pytest.mark.usefixtures("fake_actions")
def test_tool_run_flowgraph_returns_json():
    import asyncio
    import json

    proxy, _ = _make_proxy(_valid_page(), outcome="completed", code=0)
    res = json.loads(
        asyncio.run(run_flowgraph_func(_ctx(_FakeDeps(proxy), tool_call_approved=True), action="start", wait=True, timeout_seconds=1))
    )
    assert res["status"] == "completed"


@pytest.mark.usefixtures("fake_actions")
def test_tool_run_flowgraph_bounded_stop_after():
    """stop_after_seconds flows through the tool to the proxy and the killer
    path reports stopped_after_timeout (bounded run in one call)."""
    import asyncio
    import json

    page = _valid_page()
    proxy, _ = _make_proxy(page, monitor=_BoundedRunMonitor(page, code=-15))
    res = json.loads(
        asyncio.run(
            run_flowgraph_func(
                _ctx(_FakeDeps(proxy), tool_call_approved=True),
                action="start",
                wait=True,
                stop_after_seconds=5,
            )
        )
    )
    assert res["status"] == "stopped_after_timeout"
    assert res["return_code"] == -15


def test_tool_run_flowgraph_stop_requires_no_approval_and_stops():
    import asyncio
    import json

    proxy, _ = _make_proxy(_valid_page(process=None))
    # tool_call_approved is False, but action='stop' needs no approval!
    ctx = _ctx(_FakeDeps(proxy), tool_call_approved=False)
    res = json.loads(asyncio.run(run_flowgraph_func(ctx, action="stop")))
    assert res["status"] == "not_running"
