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
from pydantic_ai.exceptions import ApprovalRequired, ModelRetry, ToolFailed

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
        self._tracking_seq = None
        self._tracking_latch = None
        self._tracking_reads = 0
        self.synthesized = []

    def bind_run_process(self, process):
        self.bound_process = process

    def mark_run_agent_initiated(self):
        self.marked_agent_initiated += 1

    def mark_run_agent_initiated_cancelled(self):
        self.cancelled_agent_initiated += 1

    @property
    def is_tracking(self):
        if self._tracking_latch is not None:
            self._tracking_reads += 1
            return self._tracking_reads <= self._tracking_latch
        if self._tracking_seq:
            return self._tracking_seq.pop(0)
        return self._tracking

    def synthesize_terminal_state(self, *, signal=None):
        self.synthesized.append(signal)
        if not self._tracking:
            return False
        self._tracking = False
        return True

    def get_last_run_log(self):
        return {
            "return_code": self.last_run_code,
            "run_id": self.run_epoch + 1,
            "log_text": "",
            "ran_successfully": self.last_run_code == 0,
            "run_in_progress": False,
        }

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
    """An unwired monitor is terminal, so the proxy raises ToolFailed.

    ValueError is the proxy's channel for a *recoverable* gate refusal (no
    graph, invalid graph, unsaved) which run_flowgraph_func turns into a
    ModelRetry. A missing monitor is not recoverable, and routing it through
    ValueError told the model to retry something no retry can fix.
    """
    proxy, _ = _make_proxy(_valid_page(), monitor=None)
    with pytest.raises(ToolFailed, match="run monitor is not wired"):
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
    """R6: the unsaved-run gate is self-serve — it names the save tool and
    demands a retry, and it states the true invariant (GRC generates into the
    saved graph's directory and executes from there). The old false claim
    that execution generates from the saved file must be gone."""
    proxy, _ = _make_proxy(_valid_page(file_path=""))
    with pytest.raises(ValueError) as excinfo:
        import asyncio

        asyncio.run(proxy.run_flowgraph(action="start"))
    msg = str(excinfo.value)
    assert "never been saved" in msg
    assert "save_graph" in msg
    assert "retry grc_interaction(mode='start')" in msg
    assert "directory" in msg
    assert "generates from the saved file" not in msg
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
    assert "grc_interaction(mode='log')" in res["note"]
    assert monitor.marked_agent_initiated == 1
    fake_actions.exec_action.set_enabled.assert_called_once_with(True)
    fake_actions.exec_action.assert_called_once_with()


@pytest.mark.usefixtures("fake_actions")
def test_run_wait_completed():
    proxy, _ = _make_proxy(_valid_page(), outcome="completed", code=0)

    import asyncio

    res = asyncio.run(proxy.run_flowgraph(action="start", wait=True, timeout_seconds=5))

    assert res["status"] == "completed"
    assert res["run_id"] == 1
    assert res["return_code"] == 0
    assert res["ran_successfully"] is True
    assert "grc_interaction(mode='log')" in res["note"]


@pytest.mark.usefixtures("fake_actions")
def test_run_wait_still_running():
    proxy, _ = _make_proxy(_valid_page(), outcome="still_running")

    import asyncio

    res = asyncio.run(proxy.run_flowgraph(action="start", wait=True, timeout_seconds=1))

    assert res["status"] == "still_running"
    assert "grc_interaction(mode='stop')" in res["note"]


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
    assert res["run_id"] == 1
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
def test_stop_escalates_when_shutdown_lingers(monkeypatch):
    """A stalled SIGTERM shutdown escalates to SIGKILL instead of wedging.

    This replaces the old stop_pending expectation: reporting
    'stop_requested' forever left the run monitor stuck and poisoned every
    later start. The escalated stop reports 'stopped', retains the terminal
    state, and clears the dead process so the next start works."""
    import asyncio
    import signal as _signal

    import grc_agent.native_canvas as _nc

    monkeypatch.setattr(_nc, "_KILL_GRACE_SECONDS", 0.2)
    monkeypatch.setattr(_nc, "_KILL_SETTLE_SECONDS", 0.1)
    proc = SimpleNamespace(pid=12345, poll=lambda: None)
    proxy, monitor = _make_proxy(
        _valid_page(process=proc), outcome="still_running", tracking=True
    )
    kills = []
    monkeypatch.setattr(
        _nc.os, "kill", lambda pid, sig: kills.append((pid, sig))
    )
    res = asyncio.run(proxy.stop_flowgraph())

    assert res["status"] == "stopped"
    assert "SIGKILL" in res["note"]
    assert kills == [(12345, _signal.SIGKILL)]
    assert monitor.synthesized == [_signal.SIGKILL]
    assert proxy._canvas_manager.window.current_page.process is None


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

    async def save_graph(self):
        return await self._proxy.save_graph()


def _ctx(deps, tool_call_approved=True):
    return SimpleNamespace(deps=deps, tool_call_approved=tool_call_approved)


def test_tool_grc_interaction_requires_approval_on_start():
    """Only the start mode asks for approval; stop remains available to the user."""
    from grc_agent.agent import _validate_interaction

    with pytest.raises(ApprovalRequired):
        _validate_interaction(_ctx(SimpleNamespace(), tool_call_approved=False), mode="start")

    _validate_interaction(_ctx(SimpleNamespace(), tool_call_approved=False), mode="stop")
    _validate_interaction(_ctx(SimpleNamespace(), tool_call_approved=True), mode="start")


def test_tool_run_flowgraph_requires_wired_deps():
    """Unwired execution is terminal, so it is a failed result, not a retry."""
    import asyncio

    with pytest.raises(ToolFailed, match="not wired up"):
        asyncio.run(
            run_flowgraph_func(_ctx(SimpleNamespace(), tool_call_approved=True), action="start", wait=True, timeout_seconds=1)
        )


def test_tool_grc_interaction_invalid_mode():
    """The wrapper's Literal mode schema rejects a bad operation before dispatch."""
    import pydantic

    from grc_agent.agent import grc_tools

    tool = [t for t in grc_tools() if t.name == "grc_interaction"][0]
    modes = tool.function_schema.json_schema["properties"]["mode"]["enum"]
    assert modes == ["start", "stop", "log", "measure", "plot", "canvas"]
    with pytest.raises(pydantic.ValidationError):
        tool.function_schema.validator.validate_python({"mode": "invalid"})


@pytest.mark.usefixtures("fake_actions")
def test_tool_run_flowgraph_wraps_gate_errors_as_modelretry():
    import asyncio

    proxy, _ = _make_proxy(_valid_page(file_path=""))
    with pytest.raises(ModelRetry) as excinfo:
        asyncio.run(
            run_flowgraph_func(
                _ctx(_FakeDeps(proxy), tool_call_approved=True), action="start", wait=True, timeout_seconds=1
            )
        )
    msg = str(excinfo.value)
    # The gate's self-serve wording crosses the tool boundary intact.
    assert "never been saved" in msg
    assert "save_graph" in msg
    assert "retry grc_interaction(mode='start')" in msg


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


def test_get_run_log_annotates_no_gui():
    page = _valid_page()
    page.flow_graph.get_option.return_value = "no_gui"
    monitor = MagicMock()
    monitor.get_last_run_log.return_value = {
        "return_code": 0,
        "log_text": "",
        "ran_successfully": True,
        "run_in_progress": False,
    }
    proxy, _ = _make_proxy(page, monitor=monitor)
    res = proxy.get_run_log()
    assert res is not None
    assert res["generate_options"] == "no_gui"
    assert "external_terminal_note" in res
    assert "no_gui" in res["external_terminal_note"]


@pytest.mark.usefixtures("fake_actions")
def test_run_flowgraph_annotates_no_gui():
    import asyncio

    page = _valid_page()
    page.flow_graph.get_option.return_value = "no_gui"
    proxy, _ = _make_proxy(page, outcome="completed", code=0)
    res = asyncio.run(proxy.run_flowgraph(action="start", wait=True))
    assert res["status"] == "completed"
    assert res["run_id"] == 1
    assert res["generate_options"] == "no_gui"
    assert "external terminal wrapper" in res["note"]


# --- stop escalation (SIGTERM grace, then SIGKILL, then terminal synthesis) ---


def test_stop_natural_completion_during_grace_skips_kill(monkeypatch, fake_actions):  # noqa: ARG001
    """A Done marker arriving during the grace window cancels the escalation."""
    import asyncio

    import grc_agent.native_canvas as _nc

    monkeypatch.setattr(_nc, "_KILL_GRACE_SECONDS", 5.0)
    monkeypatch.setattr(_nc, "_KILL_SETTLE_SECONDS", 0.1)
    proc = SimpleNamespace(pid=12345, poll=lambda: None)
    proxy, monitor = _make_proxy(
        _valid_page(process=proc), outcome="still_running", tracking=True
    )
    # Tracking clears early: the natural terminal won the race.
    monitor._tracking_latch = 2
    kills = []
    monkeypatch.setattr(_nc.os, "kill", lambda pid, sig: kills.append((pid, sig)))
    res = asyncio.run(proxy.stop_flowgraph())

    assert res["status"] == "stopped"
    assert "SIGKILL" not in res["note"]
    assert kills == []
    assert monitor.synthesized == []


def test_escalation_uses_injected_clock_without_blocking(monkeypatch):
    """The grace window awaits the injected clock — never a blocking sleep."""
    import asyncio
    import signal as _signal
    import time as _time

    import grc_agent.native_canvas as _nc

    state = {"t": 0.0}
    calls = []

    async def fake_clock(dt):
        calls.append(dt)
        state["t"] += dt

    proc = SimpleNamespace(pid=12345, poll=lambda: None)
    proxy, monitor = _make_proxy(
        _valid_page(process=proc), outcome="still_running", tracking=True
    )
    kills = []
    monkeypatch.setattr(_nc.os, "kill", lambda pid, sig: kills.append((pid, sig)))
    page = proxy._canvas_manager.window.current_page
    start = _time.monotonic()
    killed = asyncio.run(
        proxy._escalate_stop(
            monitor, page, "qt_gui", grace_seconds=30.0, clock=fake_clock, now=lambda: state["t"]
        )
    )
    wall = _time.monotonic() - start

    assert killed is True
    assert calls, "escalation must await the clock (yield to the loop)"
    assert wall < 5.0, f"escalation blocked the loop for {wall:.1f}s"
    assert kills == [(12345, _signal.SIGKILL)]
    assert monitor.synthesized == [_signal.SIGKILL]
    assert page.process is None


def test_escalation_does_not_clear_replaced_process(monkeypatch):
    """Only the exact killed process object is cleared — never a successor."""
    import asyncio

    import grc_agent.native_canvas as _nc

    monkeypatch.setattr(_nc, "_KILL_GRACE_SECONDS", 5.0)
    monkeypatch.setattr(_nc, "_KILL_SETTLE_SECONDS", 0.1)
    proc = SimpleNamespace(pid=12345, poll=lambda: None)
    successor = SimpleNamespace(pid=54321, poll=lambda: None)
    proxy, monitor = _make_proxy(
        _valid_page(process=proc), outcome="still_running", tracking=True
    )
    page = proxy._canvas_manager.window.current_page
    state = {"t": 0.0}

    async def swapping_clock(dt):
        state["t"] += dt
        page.process = successor

    monkeypatch.setattr(_nc.os, "kill", lambda pid, sig: None)  # noqa: ARG005
    asyncio.run(
        proxy._escalate_stop(
            monitor, page, "qt_gui", clock=swapping_clock, now=lambda: state["t"]
        )
    )
    assert page.process is successor


def test_stop_heals_vanished_process_tracking():
    """Process gone with the monitor still tracking: synthesize, report not_running."""
    import asyncio

    proxy, monitor = _make_proxy(_valid_page(process=None), tracking=True)
    res = asyncio.run(proxy.stop_flowgraph())

    assert res["status"] == "not_running"
    assert monitor.synthesized == [None]
    assert monitor.is_tracking is False


def test_stop_not_running_without_tracking_synthesizes_nothing():
    import asyncio

    proxy, monitor = _make_proxy(_valid_page(process=None), tracking=False)
    res = asyncio.run(proxy.stop_flowgraph())

    assert res["status"] == "not_running"
    assert monitor.synthesized == []


def test_escalation_kills_descendants_first_for_terminal_wrapper(monkeypatch):
    """no_gui spawns through xterm: grandchildren hold the pipe, so they die first."""
    import asyncio
    import signal as _signal

    import grc_agent.native_canvas as _nc

    monkeypatch.setattr(_nc, "_KILL_GRACE_SECONDS", 0.2)
    monkeypatch.setattr(_nc, "_KILL_SETTLE_SECONDS", 0.1)
    monkeypatch.setattr(_nc, "_descendant_pids", lambda pid: [111, 222] if pid == 12345 else [])
    proc = SimpleNamespace(pid=12345, poll=lambda: None)
    page = _valid_page(process=proc)
    page.flow_graph.get_option.return_value = "no_gui"
    proxy, monitor = _make_proxy(page, outcome="still_running", tracking=True)
    kills = []
    monkeypatch.setattr(_nc.os, "kill", lambda pid, sig: kills.append((pid, sig)))
    res = asyncio.run(proxy.stop_flowgraph())

    assert res["status"] == "stopped"
    assert [pid for pid, _ in kills] == [111, 222, 12345]
    assert all(sig == _signal.SIGKILL for _, sig in kills)


def test_tool_status_vocabulary_conformance(fake_actions):  # noqa: ARG001
    """Every status the tools can return belongs to the frozen vocabulary.

    The vocabulary lives once in native_canvas._RUN_STATUS_VOCABULARY; the
    collection harness allowlists against the same import. Any new status
    must extend the constant first — a hand-copied list here would let the
    two sides drift silently.
    """
    import asyncio

    from grc_agent.native_canvas import _RUN_STATUS_VOCABULARY

    observed = []

    def collect(res):
        observed.append(res["status"])
        return res

    # stop paths
    collect(asyncio.run(_make_proxy(_valid_page(process=None), tracking=False)[0].stop_flowgraph()))
    proc = SimpleNamespace(pid=99999, poll=lambda: 0)
    collect(
        asyncio.run(
            _make_proxy(_valid_page(process=proc), outcome="completed", tracking=False)[0].stop_flowgraph()
        )
    )
    # start paths (actions faked; monitor epoch never advances so wait is faked)
    start_proxy, _ = _make_proxy(_valid_page(process=None), outcome="completed", tracking=False)
    collect(asyncio.run(start_proxy.run_flowgraph(action="start", wait=False)))
    collect(asyncio.run(start_proxy.run_flowgraph(action="start", wait=True)))
    collect(asyncio.run(start_proxy.run_flowgraph(action="start", wait=True, timeout_seconds=1.0)))
    bounded_proxy, _ = _make_proxy(_valid_page(process=None), outcome="still_running", tracking=False)
    collect(asyncio.run(bounded_proxy.run_flowgraph(action="start", wait=True, stop_after_seconds=5)))

    assert observed, "conformance battery produced no statuses"
    outside = [s for s in observed if s not in _RUN_STATUS_VOCABULARY]
    assert outside == [], f"statuses outside the frozen vocabulary: {outside}"


def test_bounded_stop_targets_originating_page_after_tab_switch(fake_actions):
    import asyncio

    original = _valid_page(process=object())
    selected = _valid_page(process=object())
    proxy, monitor = _make_proxy(selected, outcome="completed", tracking=False)
    targets = []
    cm = object.__getattribute__(proxy, "_canvas_manager")
    fake_actions.kill_action.side_effect = lambda: targets.append(cm.window.current_page)
    result = asyncio.run(proxy._finish_bounded_run(monitor, 5, expected_run_id=1, page=original))
    assert result["status"] == "stopped_after_timeout"
    assert targets == [original]
    assert cm.window.current_page is selected
