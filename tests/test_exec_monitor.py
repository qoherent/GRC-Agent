from grc_agent.exec_monitor import ExecutionErrorMonitor


def test_nonzero_return_code_triggers_error():
    errors = []
    monitor = ExecutionErrorMonitor(on_error=errors.append)

    monitor.handle_message("\nExecuting: /tmp/flowgraph.py\n")
    for ch in "Traceback (most recent call last):\nZeroDivisionError\n":
        monitor.handle_message(ch)
    monitor.handle_message("\n>>> Done (return code 1)\n")

    assert len(errors) == 1
    assert "ZeroDivisionError" in errors[0]
    assert "Executing: /tmp/flowgraph.py" in errors[0]


def test_zero_return_code_does_not_trigger_error():
    errors = []
    monitor = ExecutionErrorMonitor(on_error=errors.append)

    monitor.handle_message("\nExecuting: /tmp/flowgraph.py\n")
    monitor.handle_message("all good\n")
    monitor.handle_message("\n>>> Done\n")

    assert errors == []


def test_sigterm_return_code_does_not_trigger_error():
    """GRC's own Kill button sends SIGTERM (-15) -- a user-requested stop,
    not a crash, so it must not prompt a fix."""
    errors = []
    monitor = ExecutionErrorMonitor(on_error=errors.append)

    monitor.handle_message("\nExecuting: /tmp/flowgraph.py\n")
    monitor.handle_message("still running...\n")
    monitor.handle_message("\n>>> Done (return code -15)\n")

    assert errors == []


def test_other_negative_return_code_triggers_error():
    """A crash from an uncaught signal (e.g. SIGSEGV, -11) is not the Kill
    button's SIGTERM and should still be reported."""
    errors = []
    monitor = ExecutionErrorMonitor(on_error=errors.append)

    monitor.handle_message("\nExecuting: /tmp/flowgraph.py\n")
    monitor.handle_message("\n>>> Done (return code -11)\n")

    assert len(errors) == 1


def test_generate_error_triggers_error_without_exec_start():
    """Pressing Execute auto-generates first; if generation fails the
    subprocess never starts, so there's no Executing:/Done pair at all."""
    errors = []
    monitor = ExecutionErrorMonitor(on_error=errors.append)

    monitor.handle_message("Generate Error: invalid block parameter\n>>> Failure\n")

    assert len(errors) == 1
    assert "Generate Error" in errors[0]


def test_buffer_resets_between_runs():
    errors = []
    monitor = ExecutionErrorMonitor(on_error=errors.append)

    monitor.handle_message("\nExecuting: /tmp/first.py\n")
    monitor.handle_message("first run output\n")
    monitor.handle_message("\n>>> Done\n")

    monitor.handle_message("\nExecuting: /tmp/second.py\n")
    monitor.handle_message("second run output\n")
    monitor.handle_message("\n>>> Done (return code 1)\n")

    assert len(errors) == 1
    assert "first run" not in errors[0]
    assert "second run" in errors[0]


def test_buffer_preserves_full_output_no_arbitrary_truncation():
    """The buffer no longer enforces an arbitrary character cap -- an earlier
    version silently dropped the beginning of a failing run's output (often
    the actual traceback) once it exceeded a fixed size. The full captured
    output for a run must now be preserved verbatim."""
    errors = []
    monitor = ExecutionErrorMonitor(on_error=errors.append)

    monitor.handle_message("\nExecuting: /tmp/flowgraph.py\n")
    monitor.handle_message("START_MARKER\n")
    for _ in range(20000):
        monitor.handle_message("#")
    monitor.handle_message("\n>>> Done (return code 1)\n")

    assert len(errors) == 1
    assert "START_MARKER" in errors[0]
    assert errors[0].count("#") == 20000
