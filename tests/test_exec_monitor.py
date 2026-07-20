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
    assert not monitor.has_last_run


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


# --- Existing tests adapted for the new callback signature ---


def test_nonzero_return_code_triggers_error():
    errors = []
    monitor = ExecutionErrorMonitor(on_error=lambda code, log: errors.append((code, log)))
    _feed_run(monitor, "/tmp/flowgraph.py", "Traceback\nZeroDivisionError\n", code=1)
    assert len(errors) == 1
    assert errors[0][0] == 1
    assert "ZeroDivisionError" in errors[0][1]
    assert "Executing" in errors[0][1]


def test_zero_return_code_does_not_trigger_error():
    errors = []
    monitor = ExecutionErrorMonitor(on_error=lambda code, log: errors.append((code, log)))
    _feed_run(monitor, "/tmp/flowgraph.py", "all good\n", code=0)
    assert errors == []


def test_sigterm_return_code_does_not_trigger_error():
    errors = []
    monitor = ExecutionErrorMonitor(on_error=lambda code, log: errors.append((code, log)))
    _feed_run(monitor, "/tmp/flowgraph.py", "still running...\n", code=-15)
    assert errors == []


def test_other_negative_return_code_triggers_error():
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


def test_get_run_log_tool_no_monitor_wired():
    """When deps has no get_run_log method (scenario harness), the tool
    returns a clear 'no log available' JSON instead of crashing."""
    import asyncio

    from pydantic_ai import RunContext

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
    result = asyncio.run(get_run_log_func(ctx))
    data = json.loads(result)
    assert data["log_text"] == ""
    assert "No execution log available" in data["message"]


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
    for ch in "throttle :info: set_min_output_buffer to 20480\nofdm :error: ERROR Buffer too small\n":
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
