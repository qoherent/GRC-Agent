"""Tests for exec_monitor — real buffer behavior, log retention, and the
get_run_log proxy accessor. No mocking of the monitor itself; messages are
fed through the real handle_message path just as GRC's Messages bus delivers
them."""

import json

from grc_agent.exec_monitor import ExecutionErrorMonitor
from grc_agent.native_canvas import NativeFlowgraphProxy


def _noop(_code, _log):
    """No-op callback for tests that don't care about the on_error path."""
    pass


def _feed_run(monitor, start_cmd, output, code):
    """Feed a realistic message sequence: start → char-by-char output → done."""
    monitor.handle_message(f"\nExecuting: {start_cmd}\n")
    for ch in output:
        monitor.handle_message(ch)
    done = "\n>>> Done\n" if code == 0 else f"\n>>> Done (return code {code})\n"
    monitor.handle_message(done)


# --- Callback contract (new 2-arg signature) ---


def test_failure_callback_receives_code_and_log():
    calls = []
    monitor = ExecutionErrorMonitor(on_error=lambda code, log: calls.append((code, log)))
    _feed_run(monitor, "/tmp/flow.py", "RuntimeError: boom\n", code=1)
    assert len(calls) == 1
    code, log = calls[0]
    assert code == 1
    assert "RuntimeError: boom" in log
    assert "Executing:" in log


def test_success_does_not_trigger_callback():
    calls = []
    monitor = ExecutionErrorMonitor(on_error=lambda code, log: calls.append((code, log)))
    _feed_run(monitor, "/tmp/flow.py", "all good\n", code=0)
    assert calls == []


def test_sigterm_does_not_trigger_callback():
    calls = []
    monitor = ExecutionErrorMonitor(on_error=lambda code, log: calls.append((code, log)))
    _feed_run(monitor, "/tmp/flow.py", "running...\n", code=-15)
    assert calls == []


# --- Log retention (new: last run's log is queryable by get_run_log) ---


def test_last_run_log_retained_after_failure():
    monitor = ExecutionErrorMonitor(on_error=_noop)
    _feed_run(monitor, "/tmp/flow.py", "Traceback\nValueError: bad\n", code=1)
    data = monitor.get_last_run_log()
    assert data is not None
    assert data["return_code"] == 1
    assert data["ran_successfully"] is False
    assert "Traceback" in data["log_text"]
    assert "ValueError: bad" in data["log_text"]


def test_last_run_log_retained_after_success():
    monitor = ExecutionErrorMonitor(on_error=_noop)
    _feed_run(monitor, "/tmp/flow.py", "everything ok\n", code=0)
    data = monitor.get_last_run_log()
    assert data is not None
    assert data["return_code"] == 0
    assert data["ran_successfully"] is True
    assert "everything ok" in data["log_text"]


def test_last_run_log_none_before_any_run():
    monitor = ExecutionErrorMonitor(on_error=_noop)
    assert monitor.get_last_run_log() is None


def test_last_run_log_replaced_by_next_run():
    monitor = ExecutionErrorMonitor(on_error=_noop)
    _feed_run(monitor, "/tmp/first.py", "first output\n", code=1)
    _feed_run(monitor, "/tmp/second.py", "second output\n", code=0)
    data = monitor.get_last_run_log()
    assert data is not None
    assert data["return_code"] == 0
    assert "second output" in data["log_text"]
    assert "first output" not in data["log_text"]


# --- Proxy accessor ---


def test_proxy_get_run_log_returns_none_without_monitor():
    """A proxy with no exec_monitor (e.g. scenario harness) returns None
    so the tool surfaces a clear 'no log available' message."""
    proxy = NativeFlowgraphProxy(canvas_manager=None, exec_monitor=None)
    assert proxy.get_run_log() is None


def test_proxy_get_run_log_returns_monitor_data():
    monitor = ExecutionErrorMonitor(on_error=_noop)
    _feed_run(monitor, "/tmp/flow.py", "output here\n", code=1)
    proxy = NativeFlowgraphProxy(canvas_manager=None, exec_monitor=monitor)
    data = proxy.get_run_log()
    assert data is not None
    assert data["return_code"] == 1
    assert "output here" in data["log_text"]


def test_other_negative_return_code_triggers_error():
    # -11 (not the -15 SIGTERM carve-out) must still be reported.
    errors = []
    monitor = ExecutionErrorMonitor(on_error=lambda code, log: errors.append((code, log)))
    _feed_run(monitor, "/tmp/flowgraph.py", "", code=-11)
    assert len(errors) == 1


def test_generate_error_triggers_error_without_exec_start():
    errors = []
    monitor = ExecutionErrorMonitor(on_error=lambda code, log: errors.append((code, log)))
    monitor.handle_message("Generate Error: invalid block parameter\n>>> Failure\n")
    assert len(errors) == 1
    assert "Generate Error" in errors[0][1]


def test_buffer_resets_between_runs():
    errors = []
    monitor = ExecutionErrorMonitor(on_error=lambda code, log: errors.append((code, log)))
    _feed_run(monitor, "/tmp/first.py", "first run output\n", code=0)
    _feed_run(monitor, "/tmp/second.py", "second run output\n", code=1)
    assert len(errors) == 1
    assert "first run" not in errors[0][1]
    assert "second run" in errors[0][1]


def test_buffer_preserves_full_output_no_arbitrary_truncation():
    errors = []
    monitor = ExecutionErrorMonitor(on_error=lambda code, log: errors.append((code, log)))
    monitor.handle_message("\nExecuting: /tmp/flowgraph.py\n")
    monitor.handle_message("START_MARKER\n")
    for _ in range(20000):
        monitor.handle_message("#")
    monitor.handle_message("\n>>> Done (return code 1)\n")
    assert len(errors) == 1
    assert "START_MARKER" in errors[0][1]
    assert errors[0][1].count("#") == 20000


# --- get_run_log tool function ---


def test_get_run_log_tool_returns_monitor_data():
    """The tool function reads from ctx.deps.get_run_log() — verify it
    returns structured JSON with the right fields."""
    import asyncio

    from pydantic_ai import RunContext

    from grc_agent.agent import get_run_log_func

    monitor = ExecutionErrorMonitor(on_error=_noop)
    _feed_run(monitor, "/tmp/test.py", "RuntimeError: No RTL-SDR devices found!\n", code=1)
    proxy = NativeFlowgraphProxy(canvas_manager=None, exec_monitor=monitor)

    ctx = RunContext(
        deps=proxy,
        retry=0,
        messages=[],
        tool_name="get_run_log",
        run_step=0,
        model=None,
        usage=None,
    )
    result = asyncio.run(get_run_log_func(ctx))
    data = json.loads(result)
    assert data["return_code"] == 1
    assert data["ran_successfully"] is False
    assert "RTL-SDR" in data["log_text"]


def test_get_run_log_tool_no_monitor_wired_raises():
    """An unwired monitor is an environment fault, not an empty log.

    It used to be reported as ordinary data ("No execution log available"),
    which the model could not distinguish from the legitimate "no run yet"
    result — so a broken wiring read as a healthy environment. It now raises
    ModelRetry like every other domain-tool failure."""
    import asyncio

    import pytest
    from pydantic_ai import RunContext
    from pydantic_ai.exceptions import ModelRetry

    from grc_agent.agent import get_run_log_func

    # A plain object with no get_run_log — simulates the harness's raw flowgraph deps
    ctx = RunContext(
        deps=object(),
        retry=0,
        messages=[],
        tool_name="get_run_log",
        run_step=0,
        model=None,
        usage=None,
    )
    with pytest.raises(ModelRetry, match="run monitor is not available"):
        asyncio.run(get_run_log_func(ctx))


def test_get_run_log_tool_no_run_yet():
    """When a monitor IS wired but no run has happened yet."""
    import asyncio

    from pydantic_ai import RunContext

    from grc_agent.agent import get_run_log_func

    monitor = ExecutionErrorMonitor(on_error=_noop)
    proxy = NativeFlowgraphProxy(canvas_manager=None, exec_monitor=monitor)

    ctx = RunContext(
        deps=proxy,
        retry=0,
        messages=[],
        tool_name="get_run_log",
        run_step=0,
        model=None,
        usage=None,
    )
    result = asyncio.run(get_run_log_func(ctx))
    data = json.loads(result)
    assert data["log_text"] == ""
    assert "No flowgraph has been run yet" in data["message"]


# --- grc_tools includes get_run_log ---


def test_grc_tools_includes_get_run_log():
    from grc_agent.agent import grc_tools

    names = {t.name for t in grc_tools()}
    assert "get_run_log" in names


# --- Runtime error detection (":error:" in verbose output, code=0) ---


def test_runtime_error_triggers_callback_even_with_zero_code():
    """GNU Radio runtime errors (buffer overflows, rate mismatches) print
    ':error:' to stderr but exit with code 0. The monitor must detect them."""
    errors = []
    monitor = ExecutionErrorMonitor(on_error=lambda code, log: errors.append((code, log)))

    monitor.handle_message("\nExecuting: /tmp/flow.py\n")
    for ch in "ofdm_cp_0 :error: Buffer too small\n":
        monitor.handle_message(ch)
    monitor.handle_message("\n>>> Done\n")

    assert len(errors) == 1
    assert errors[0][0] == 0  # process exited cleanly
    assert "Buffer too small" in errors[0][1]


def test_runtime_error_shows_in_get_last_run_log():
    monitor = ExecutionErrorMonitor(on_error=_noop)

    monitor.handle_message("\nExecuting: /tmp/flow.py\n")
    for (
        ch
    ) in "throttle :info: set_min_output_buffer to 20480\nofdm :error: ERROR Buffer too small\n":
        monitor.handle_message(ch)
    monitor.handle_message("\n>>> Done\n")

    data = monitor.get_last_run_log()
    assert data is not None
    assert data["return_code"] == 0
    assert data["ran_successfully"] is False
    assert "Buffer too small" in data["log_text"]


def test_runtime_error_with_nonzero_code_still_reports():
    """Both a non-zero return code AND a runtime error — reported once."""
    errors = []
    monitor = ExecutionErrorMonitor(on_error=lambda code, log: errors.append((code, log)))

    monitor.handle_message("\nExecuting: /tmp/flow.py\n")
    for ch in "RuntimeError: No RTL-SDR devices found!\n":
        monitor.handle_message(ch)
    monitor.handle_message("\n>>> Done (return code 1)\n")

    assert len(errors) == 1
    assert errors[0][0] == 1


def test_runtime_error_not_triggered_by_info_level():
    """':info:' messages (like set_min_output_buffer) must NOT trigger a
    runtime error — only ':error:' matters."""
    errors = []
    monitor = ExecutionErrorMonitor(on_error=lambda code, log: errors.append((code, log)))

    monitor.handle_message("\nExecuting: /tmp/flow.py\n")
    for ch in "throttle :info: set_min_output_buffer on block 2 to 20480\n":
        monitor.handle_message(ch)
    monitor.handle_message("\n>>> Done\n")

    assert errors == []  # no :error: seen, should not report


def test_runtime_error_resets_between_runs():
    """The _has_runtime_error flag must be cleared on each new Executing: marker."""
    errors = []
    monitor = ExecutionErrorMonitor(on_error=lambda code, log: errors.append((code, log)))

    # First run: has runtime error
    monitor.handle_message("\nExecuting: /tmp/first.py\n")
    for ch in ":error: Broken\n":
        monitor.handle_message(ch)
    monitor.handle_message("\n>>> Done\n")
    assert len(errors) == 1

    # Second run: no runtime error, should NOT report
    monitor.handle_message("\nExecuting: /tmp/second.py\n")
    for ch in "All good here\n":
        monitor.handle_message(ch)
    monitor.handle_message("\n>>> Done\n")
    assert len(errors) == 1  # still only one from the first run


def test_execution_error_monitor_modified_since_last_run_note():
    """When notify_graph_modified is called, get_last_run_log includes a note
    warning the model that the log reflects the run BEFORE its recent edits."""
    from grc_agent.exec_monitor import ExecutionErrorMonitor

    mon = ExecutionErrorMonitor(on_error=lambda *_: None)
    mon.handle_message("Executing: /tmp/test.py\n")
    mon.handle_message("\n>>> Done (return code 1)\n")

    log1 = mon.get_last_run_log()
    assert log1 is not None
    assert log1["return_code"] == 1
    assert "note" not in log1

    mon.notify_graph_modified()
    log2 = mon.get_last_run_log()
    assert log2 is not None
    assert "note" in log2
    assert "modified in memory" in log2["note"]

    # Starting a new run resets the note flag
    mon.handle_message("Executing: /tmp/test.py\n")
    mon.handle_message("\n>>> Done (return code 0)\n")
    log3 = mon.get_last_run_log()
    assert log3 is not None
    assert "note" not in log3


def test_log_truncation_is_disclosed():
    """The 512KB cap drops the FRONT of the log. That reduction must be visible
    to the model — a diagnostic read as complete when it is not is exactly the
    silent transformation the tool contract forbids."""
    from grc_agent.exec_monitor import _MAX_LOG_BYTES, ExecutionErrorMonitor

    monitor = ExecutionErrorMonitor(on_error=_noop)
    # _append evicts back under the cap on every call, so _chunk_bytes never
    # exceeds it — feed a known total past the cap instead of watching the size.
    _feed_run(monitor, "/tmp/flow.py", ["x" * 8192] * ((_MAX_LOG_BYTES // 8192) + 2), 0)

    data = monitor.get_last_run_log()
    assert data is not None
    assert data["log_truncated"] is True
    assert str(_MAX_LOG_BYTES) in data["truncation_note"]
    assert len(data["log_text"]) <= _MAX_LOG_BYTES


def test_log_truncation_flag_absent_for_a_small_run():
    """A run under the cap must not claim truncation."""
    from grc_agent.exec_monitor import ExecutionErrorMonitor

    monitor = ExecutionErrorMonitor(on_error=_noop)
    _feed_run(monitor, "/tmp/flow.py", ["all good\n"], 0)

    data = monitor.get_last_run_log()
    assert data is not None
    assert "log_truncated" not in data
    assert "truncation_note" not in data
    assert data["ran_successfully"] is True


# --- Completion signaling + agent-initiated suppression (run_flowgraph) ---


def test_success_done_has_no_return_code_text():
    """GRC's Messages.send_end_exec omits the '(return code N)' suffix for
    code 0 — the retained log must still record return_code 0 without the
    marker text having contained it (locks the Messages.py conditional)."""
    monitor = ExecutionErrorMonitor(on_error=_noop)
    _feed_run(monitor, "/tmp/flow.py", "hello\n", code=0)
    res = monitor.get_last_run_log()
    assert res is not None
    assert res["return_code"] == 0
    assert "(return code" not in res["log_text"].split(">>> Done")[-1]


def test_wait_for_run_end_completed():
    import asyncio

    async def main():
        monitor = ExecutionErrorMonitor(on_error=_noop)
        monitor.handle_message("\nExecuting: /tmp/flow.py\n")
        task = asyncio.ensure_future(monitor.wait_for_run_end(5.0))
        await asyncio.sleep(0)  # let the waiter park on the event
        assert not task.done()
        monitor.handle_message("\n>>> Done (return code 0)\n")
        return await task

    assert asyncio.run(main()) == "completed"


def test_wait_for_run_end_still_running_on_timeout():
    import asyncio

    async def main():
        monitor = ExecutionErrorMonitor(on_error=_noop)
        monitor.handle_message("\nExecuting: /tmp/flow.py\n")
        return await monitor.wait_for_run_end(0.05)

    assert asyncio.run(main()) == "still_running"


def test_wait_for_run_end_not_started_when_never_ran():
    import asyncio

    async def main():
        monitor = ExecutionErrorMonitor(on_error=_noop)
        return await monitor.wait_for_run_end(0.05)

    assert asyncio.run(main()) == "not_started"


def test_wait_for_run_end_completes_after_synchronous_done():
    """GRC's spawn-failure path emits its Done marker synchronously inside the
    Execute action — before run_flowgraph ever awaits. A run that already
    ended must report completed, not not_started."""
    import asyncio

    async def main():
        monitor = ExecutionErrorMonitor(on_error=_noop)
        monitor.handle_message("\nExecuting: /tmp/flow.py\n")
        monitor.handle_message("\n>>> Done\n")  # synchronous completion
        return await monitor.wait_for_run_end(5.0)

    assert asyncio.run(main()) == "completed"


def test_generate_error_completes_wait_and_records_code_1():
    import asyncio

    async def main():
        calls = []
        monitor = ExecutionErrorMonitor(on_error=lambda code, _log: calls.append(code))
        monitor.handle_message("Generate Error: bad graph\n>>> Failure\n")
        return await monitor.wait_for_run_end(5.0), monitor.last_run_code, list(calls)

    outcome, code, calls = asyncio.run(main())
    assert outcome == "completed"
    assert code == 1
    assert calls == [1]


def test_agent_initiated_failure_suppresses_callback():
    """The run_flowgraph tool reports failures in-turn; the follow-up
    notify_run_failure turn must not also fire for agent-started runs."""
    calls = []
    monitor = ExecutionErrorMonitor(on_error=lambda code, _log: calls.append(code))

    monitor.mark_run_agent_initiated()
    _feed_run(monitor, "/tmp/flow.py", "RuntimeError: boom\n", code=1)
    assert calls == []  # suppressed

    # A subsequent user-initiated failed run still notifies.
    _feed_run(monitor, "/tmp/flow.py", "RuntimeError: boom\n", code=1)
    assert calls == [1]


def test_agent_initiated_success_run_leaves_flag_consumed():
    calls = []
    monitor = ExecutionErrorMonitor(on_error=lambda code, _log: calls.append(code))
    monitor.mark_run_agent_initiated()
    _feed_run(monitor, "/tmp/flow.py", "ok\n", code=0)
    # Flag consumed at the terminal marker: the next (user) failure notifies.
    _feed_run(monitor, "/tmp/flow.py", "RuntimeError: boom\n", code=1)
    assert calls == [1]


def test_get_last_run_log_reports_run_in_progress():
    monitor = ExecutionErrorMonitor(on_error=_noop)
    _feed_run(monitor, "/tmp/flow.py", "previous\n", code=0)
    res = monitor.get_last_run_log()
    assert res["run_in_progress"] is False

    monitor.handle_message("\nExecuting: /tmp/flow.py\n")
    res = monitor.get_last_run_log()
    assert res["run_in_progress"] is True
    assert "previous completed run" in res["in_progress_note"]
    assert "previous" in res["log_text"]  # still the previous run's log


# --- epoch guard: silent no-op vs stale completed (verify round C3) ---


def test_wait_epoch_detects_silent_noop_after_previous_run():
    """After any run has completed, a silent no-op Execute must report
    not_started — NOT the previous run's stale 'completed'."""
    import asyncio

    async def main():
        monitor = ExecutionErrorMonitor(on_error=_noop)
        # A prior real run completes.
        _feed_run(monitor, "/tmp/flow.py", "ok\n", code=0)
        assert monitor.run_epoch == 1
        # run_flowgraph captures the epoch before triggering Execute...
        epoch = monitor.run_epoch
        # ...the action is a silent no-op (no start marker fires)...
        # ...and the wait is called with that epoch.
        return await monitor.wait_for_run_end(5.0, epoch=epoch)

    assert asyncio.run(main()) == "not_started"


def test_wait_epoch_sees_a_real_start():
    import asyncio

    async def main():
        monitor = ExecutionErrorMonitor(on_error=_noop)
        epoch = monitor.run_epoch
        monitor.handle_message("\nExecuting: /tmp/flow.py\n")  # start fires
        task = asyncio.ensure_future(monitor.wait_for_run_end(5.0, epoch=epoch))
        await asyncio.sleep(0)
        monitor.handle_message("\n>>> Done (return code 0)\n")
        return await task

    assert asyncio.run(main()) == "completed"


def test_wait_epoch_sees_synchronous_completion():
    """The spawn-failure path: start AND done both fire synchronously inside
    the action, before the wait is called — with a matching epoch this is a
    legitimately completed (failed) run, not a no-op."""
    import asyncio

    async def main():
        monitor = ExecutionErrorMonitor(on_error=_noop)
        epoch = monitor.run_epoch
        monitor.handle_message("\nExecuting: /tmp/flow.py\n")
        monitor.handle_message("\n>>> Done\n")
        return await monitor.wait_for_run_end(5.0, epoch=epoch)

    assert asyncio.run(main()) == "completed"


def test_run_epoch_counts_starts():
    monitor = ExecutionErrorMonitor(on_error=_noop)
    assert monitor.run_epoch == 0
    _feed_run(monitor, "/tmp/flow.py", "x\n", code=0)
    assert monitor.run_epoch == 1
    _feed_run(monitor, "/tmp/flow.py", "y\n", code=0)
    assert monitor.run_epoch == 2


def test_mark_cancelled_drops_suppression_before_user_run():
    calls = []
    monitor = ExecutionErrorMonitor(on_error=lambda code, _log: calls.append(code))
    monitor.mark_run_agent_initiated()
    monitor.mark_run_agent_initiated_cancelled()  # no-op Execute
    _feed_run(monitor, "/tmp/flow.py", "RuntimeError: boom\n", code=1)
    assert calls == [1]  # user-initiated failure still notifies
