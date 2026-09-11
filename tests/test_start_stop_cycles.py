"""Hermetic three-cycle start/stop integration test (plan unit U4, R10).

Zero-spend guardian for the live smoke's "three clean start/stop cycles" pass
criterion: three consecutive start -> bounded run -> stop -> assert-clean cycles
through the REAL NativeFlowgraphProxy / ExecutionErrorMonitor seam. No LLM, no
display, no external network; GRC's Actions singletons are monkeypatched with
Executor-faithful fakes that speak the console message protocol ("Executing:"
start marker, ">>> Done" terminal marker) the real monitor parses.

Home decision: NEW file rather than extending tests/test_run_stop_tools.py.
That battery drives the proxy through _FakeExecMonitor (scripted outcomes, no
message protocol, no epoch/run_id); this guardian needs the opposite seam (a
real ExecutionErrorMonitor plus action fakes driving handle_message). Mixing
both fixture families in one module would couple the fake-based escalation
unit tests to the real-monitor lifecycle, so each file keeps one uniform
fixture family. Style (loop-policy fixture, verbatim assertion idioms for
grace/settle patching, injected-clock wall bounds) is reused exactly.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from grc_agent.exec_monitor import ExecutionErrorMonitor
from grc_agent.native_canvas import (
    _RUN_STATUS_VOCABULARY,
    NativeCanvasManager,
    NativeFlowgraphProxy,
)


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


def _valid_page(process=None, file_path="/tmp/proj/flow.grc"):
    fg = MagicMock()
    fg.validate.return_value = None
    fg.is_valid.return_value = True
    fg.iter_error_messages.return_value = iter([])
    # qt_gui is the direct-child spawn: escalation must SIGKILL the child PID
    # alone, never walk a terminal-wrapper process tree.
    fg.get_option.return_value = "qt_gui"
    return SimpleNamespace(process=process, file_path=file_path, saved=True, flow_graph=fg)


class _CycleHarness:
    """Executor-faithful GRC action fakes driving the REAL monitor.

    on_exec delivers the synchronous "Executing:" start marker plus one output
    line, then assigns page.process (Executor._popen assigns only on a
    successful spawn, after the marker). on_kill in "natural" mode delivers the
    SIGTERM Done marker first and clears page.process after it (done() clears
    strictly after send_end_exec); in "stall" mode it records the SIGTERM
    attempt and delivers nothing, modelling a wedged shutdown. No process
    provider is wired, so code-carrying markers take the return-code path and
    the spawn-failure rule never fires.
    """

    def __init__(self, page, monitor):
        self.page = page
        self.monitor = monitor
        self.kill_mode = "natural"
        self.kill_calls = []  # page.process values the KILL action saw
        self.exec_mock = None
        self.kill_mock = None
        self._next_pid = 10000

    def on_exec(self):
        self.monitor.handle_message("\nExecuting: /tmp/cycle_flow.py\n")
        self.monitor.handle_message(f"cycle output {self.monitor.run_epoch}\n")
        self._next_pid += 1
        self.page.process = SimpleNamespace(pid=self._next_pid, poll=lambda: None)

    def on_kill(self):
        self.kill_calls.append(self.page.process)
        if self.kill_mode == "stall":
            return
        self.monitor.handle_message("\n>>> Done (return code -15)\n")
        self.page.process = None


def _wire(page, monkeypatch):
    """Build the real proxy+monitor seam with the harness fakes as Actions."""
    errors = []
    monitor = ExecutionErrorMonitor(
        on_error=lambda code, log, _spawn_failed=False: errors.append((code, log))
    )
    cm = NativeCanvasManager.__new__(NativeCanvasManager)
    cm.window = SimpleNamespace(current_page=page)
    proxy = NativeFlowgraphProxy(cm, exec_monitor=monitor)
    harness = _CycleHarness(page, monitor)
    from grc_agent.adapter import gui_actions

    actions = gui_actions()
    harness.exec_mock = MagicMock(name="FLOW_GRAPH_EXEC")
    harness.exec_mock.side_effect = harness.on_exec
    harness.kill_mock = MagicMock(name="FLOW_GRAPH_KILL")
    harness.kill_mock.side_effect = harness.on_kill
    monkeypatch.setattr(actions, "FLOW_GRAPH_EXEC", harness.exec_mock, raising=False)
    monkeypatch.setattr(actions, "FLOW_GRAPH_KILL", harness.kill_mock, raising=False)
    return proxy, monitor, errors, harness


def _assert_vocab(statuses):
    outside = [s for s in statuses if s not in _RUN_STATUS_VOCABULARY]
    assert outside == [], f"statuses outside the frozen vocabulary: {outside}"


def test_three_clean_start_stop_cycles(monkeypatch):
    """Happy: three start -> SIGTERM-stop cycles, each terminal natural.

    Per cycle: start returns started with the run tracking, stop returns
    stopped (plain SIGTERM account, never SIGKILL), the retained log is the
    natural -15 terminal with that cycle's output, page.process is cleared so
    the next start's already-in-progress pre-gate passes on the real page
    state, and run_id advances 1 -> 2 -> 3.
    """
    import asyncio

    page = _valid_page()
    proxy, monitor, errors, harness = _wire(page, monkeypatch)
    statuses = []
    run_ids = []
    for i in (1, 2, 3):
        start = asyncio.run(proxy.run_flowgraph(action="start", wait=False))
        statuses.append(start["status"])
        assert start["status"] == "started"
        assert monitor.is_tracking is True
        assert monitor.run_epoch == i
        stop = asyncio.run(proxy.stop_flowgraph())
        statuses.append(stop["status"])
        assert stop["status"] == "stopped"
        assert "SIGKILL" not in stop["note"]
        assert monitor.is_tracking is False
        assert page.process is None
        log = proxy.get_run_log()
        assert log["run_id"] == i
        # Truthful SIGTERM shape: the run died to the stop signal, so the
        # code is -15 (no callback fires for it); "clean" is the tool-level
        # stopped verdict, not a fabricated exit 0.
        assert log["return_code"] == -15
        assert f"cycle output {i}" in log["log_text"]
        assert log["run_in_progress"] is False
        assert "terminated_by_kill" not in log
        assert "spawn_failed" not in log
        run_ids.append(log["run_id"])
    assert run_ids == [1, 2, 3]
    assert monitor.run_epoch == 3
    assert harness.exec_mock.call_count == 3
    assert harness.kill_mock.call_count == 3
    assert errors == []  # deliberate stops never fire the failure callback
    _assert_vocab(statuses)


def test_middle_cycle_escalation_recovers(monkeypatch):
    """Edge: cycle 2's SIGTERM shutdown lingers past grace -> SIGKILL.

    The kill lands on the direct child PID only (qt_gui: the descendant walk
    must not even be consulted), the synthesized terminal retains cycle 2's
    output with kill disclosure, page.process is cleared by the escalation,
    and cycle 3 still starts through the real pre-gate and stops clean with
    run_id 3.
    """
    import asyncio
    import signal as _signal

    import grc_agent.native_canvas as _nc

    # NOTE: stop_flowgraph calls _escalate_stop with its definition-time default
    # grace/settle (10s/5s), so this full-path escalation costs ~15s of wall
    # time — the same inherent cost the two existing full-path escalation tests
    # pay. Patching the module constants cannot compress it (defaults bind at
    # def time); the injected-clock compression of the same seam lives in the
    # error test below and test_escalation_uses_injected_clock_without_blocking.
    page = _valid_page()
    proxy, monitor, errors, harness = _wire(page, monkeypatch)
    kills = []
    monkeypatch.setattr(_nc.os, "kill", lambda pid, sig: kills.append((pid, sig)))
    # Tripwire: qt_gui escalation must kill the direct child PID alone and never
    # consult the terminal-wrapper descendant walk (no_gui-only branch).
    monkeypatch.setattr(
        _nc,
        "_descendant_pids",
        lambda pid: (_ for _ in ()).throw(  # noqa: ARG005
            AssertionError("qt_gui escalation must not walk the process tree")
        ),
    )
    # Compress stop's 10s initial wait through the real event-based wait —
    # same timeout path, shorter clock; synchronous Done markers (cycles 1/3)
    # still return immediately regardless of the bound.
    orig_wait = monitor.wait_for_run_end

    async def _fast_wait(timeout, *, epoch=None):
        return await orig_wait(min(timeout, 0.3), epoch=epoch)

    monkeypatch.setattr(monitor, "wait_for_run_end", _fast_wait)
    statuses = []

    # Cycle 1: natural, run_id 1.
    statuses.append(asyncio.run(proxy.run_flowgraph(action="start", wait=False))["status"])
    statuses.append(asyncio.run(proxy.stop_flowgraph())["status"])
    assert proxy.get_run_log()["run_id"] == 1

    # Cycle 2: the SIGTERM shutdown stalls; escalation must fire.
    statuses.append(asyncio.run(proxy.run_flowgraph(action="start", wait=False))["status"])
    proc = page.process
    assert proc is not None
    harness.kill_mode = "stall"
    stop2 = asyncio.run(proxy.stop_flowgraph())
    statuses.append(stop2["status"])
    assert stop2["status"] == "stopped"
    assert "SIGKILL" in stop2["note"]
    assert kills == [(proc.pid, _signal.SIGKILL)]
    assert page.process is None
    log2 = proxy.get_run_log()
    assert log2["run_id"] == 2
    assert log2["return_code"] is None  # no exit code observed, never fabricated
    assert log2["terminated_by_kill"] is True
    assert "signal 9" in log2["termination_note"]
    assert "cycle output 2" in log2["log_text"]
    assert log2["run_in_progress"] is False
    assert monitor.is_tracking is False

    # Cycle 3: starts (pre-gate passes on the cleared page) and stops clean.
    harness.kill_mode = "natural"
    statuses.append(asyncio.run(proxy.run_flowgraph(action="start", wait=False))["status"])
    assert monitor.run_epoch == 3
    stop3 = asyncio.run(proxy.stop_flowgraph())
    statuses.append(stop3["status"])
    assert stop3["status"] == "stopped"
    assert "SIGKILL" not in stop3["note"]
    log3 = proxy.get_run_log()
    assert log3["run_id"] == 3
    assert log3["return_code"] == -15
    assert "terminated_by_kill" not in log3

    assert monitor.run_epoch == 3
    assert harness.exec_mock.call_count == 3
    assert harness.kill_mock.call_count == 3
    assert errors == []
    _assert_vocab(statuses)


def test_vanished_process_heals_and_restarts_without_blocking(monkeypatch):
    """Error: the run's process vanishes between cycles with no Done marker.

    stop_flowgraph heals the wedge synchronously (not_running, no signal sent
    — there is nothing to signal), the synthesized terminal discloses the
    vanished process, and the next cycle restarts: its start marker is
    accepted (epoch advances — the swallowed-start wedge is gone). The
    restarted run stays manageable: a stalled stop escalates on the injected
    clock, so 30 virtual grace seconds cost milliseconds of wall time.
    """
    import asyncio
    import signal as _signal
    import time as _time

    import grc_agent.native_canvas as _nc

    page = _valid_page()
    proxy, monitor, errors, harness = _wire(page, monkeypatch)
    kills = []
    monkeypatch.setattr(_nc.os, "kill", lambda pid, sig: kills.append((pid, sig)))
    statuses = []

    # Cycle 1: natural, run_id 1.
    statuses.append(asyncio.run(proxy.run_flowgraph(action="start", wait=False))["status"])
    statuses.append(asyncio.run(proxy.stop_flowgraph())["status"])
    assert proxy.get_run_log()["run_id"] == 1

    # Cycle 2: starts, then the process vanishes (crash / external reap) with
    # no terminal marker — the monitor still believes a run is live.
    statuses.append(asyncio.run(proxy.run_flowgraph(action="start", wait=False))["status"])
    assert monitor.is_tracking is True
    page.process = None
    assert monitor.is_tracking is True
    kills_before_heal = len(kills)
    kill_calls_before_heal = len(harness.kill_calls)
    t0 = _time.monotonic()
    heal = asyncio.run(proxy.stop_flowgraph())
    heal_wall = _time.monotonic() - t0
    statuses.append(heal["status"])
    assert heal["status"] == "not_running"
    # Non-blocking heal: this branch performs zero awaits, so no real timeout
    # may elapse — the same wall bound the injected-clock test asserts.
    assert heal_wall < 5.0, f"vanish heal blocked the loop for {heal_wall:.1f}s"
    assert len(kills) == kills_before_heal  # nothing to signal, none sent
    assert len(harness.kill_calls) == kill_calls_before_heal
    assert monitor.is_tracking is False
    log2 = proxy.get_run_log()
    assert log2["run_id"] == 2
    assert log2["return_code"] is None
    assert log2["terminated_by_kill"] is False
    assert "without delivering its terminal marker" in log2["termination_note"]
    assert log2["run_in_progress"] is False

    # Cycle 3: restarts on the healed monitor, then its stop stalls and the
    # escalation runs on the injected clock (wall-clock independence).
    statuses.append(asyncio.run(proxy.run_flowgraph(action="start", wait=False))["status"])
    assert monitor.run_epoch == 3
    assert monitor.is_tracking is True
    proc3 = page.process
    assert proc3 is not None
    harness.kill_mode = "stall"
    harness.kill_mock()  # the SIGTERM attempt stalls: no marker follows
    state = {"t": 0.0}
    clock_calls = []

    async def fake_clock(dt):
        clock_calls.append(dt)
        state["t"] += dt

    t1 = _time.monotonic()
    killed = asyncio.run(
        proxy._escalate_stop(
            monitor, page, "qt_gui", grace_seconds=30.0, clock=fake_clock, now=lambda: state["t"]
        )
    )
    esc_wall = _time.monotonic() - t1
    assert killed is True
    assert clock_calls, "escalation must await the clock (yield to the loop)"
    assert esc_wall < 5.0, f"escalation blocked the loop for {esc_wall:.1f}s"
    assert state["t"] >= 30.0, "the virtual grace window must actually elapse"
    assert kills == [(proc3.pid, _signal.SIGKILL)]
    assert page.process is None
    log3 = proxy.get_run_log()
    assert log3["run_id"] == 3
    assert log3["terminated_by_kill"] is True
    assert "signal 9" in log3["termination_note"]

    assert monitor.run_epoch == 3
    assert errors == []
    _assert_vocab(statuses)
