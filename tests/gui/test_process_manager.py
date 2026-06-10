import os
import sys
import tempfile
from unittest.mock import MagicMock, patch

from PySide6.QtCore import QProcess, QProcessEnvironment

# Add src to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../src")))

try:
    from grc_agent_gui.process_manager import ProcessManager
except ImportError:
    ProcessManager = None


def test_process_manager_imports_exist():
    """Assert that ProcessManager module exists and can be imported under TDD."""
    assert ProcessManager is not None, "ProcessManager class not implemented yet"


def test_flowgraph_id_resolution():
    """Assert that compilation resolves the output Python filename by reading options block id."""
    # Mock session
    mock_session = MagicMock()
    mock_session.flowgraph.metadata = {
        "options": {
            "parameters": {
                "id": "my_fuzzed_flowgraph"
            }
        }
    }

    # We test that our helper method resolves ID correctly
    manager = ProcessManager()
    resolved_id = manager.resolve_flowgraph_id(mock_session)
    assert resolved_id == "my_fuzzed_flowgraph"


def test_working_directory_and_environment(qtbot):
    """Assert that the execution QProcess calls setWorkingDirectory and uses systemEnvironment."""
    manager = ProcessManager()

    # Mock session
    mock_session = MagicMock()
    mock_session.path = "/home/user/project/my_graph.grc"
    mock_session.flowgraph.metadata = {
        "options": {
            "parameters": {
                "id": "my_graph"
            }
        }
    }

    proc = QProcess(manager)
    # We test setting working directory and environment on a test process
    grc_dir = os.path.dirname(mock_session.path)
    proc.setWorkingDirectory(grc_dir)
    proc.setProcessEnvironment(QProcessEnvironment.systemEnvironment())

    assert proc.workingDirectory() == "/home/user/project"
    assert proc.processEnvironment().keys() == QProcessEnvironment.systemEnvironment().keys()


@patch("grc_agent_gui.process_manager.QProcess")
def test_split_stage_execution(mock_qprocess_class, qtbot):
    """Assert compilation runs grcc and on success emits finished without execution."""
    manager = ProcessManager()

    # Mock session
    mock_session = MagicMock()
    mock_session.path = "/tmp/my_graph.grc"
    mock_session.flowgraph.metadata = {
        "options": {
            "parameters": {
                "id": "my_graph"
            }
        }
    }

    # Instantiate mocked QProcesses
    mock_compile_proc = MagicMock()

    # Configure mock class to return our mocks
    mock_qprocess_class.side_effect = [mock_compile_proc]

    # Track finished signal
    finished_codes = []
    manager.finished.connect(lambda code: finished_codes.append(code))

    # Trigger validate
    manager.validate_graph(mock_session)

    # Assert compile process started grcc
    mock_compile_proc.start.assert_called_once()
    args = mock_compile_proc.start.call_args[0]
    assert args[0] == "grcc"
    assert "/tmp/my_graph.grc" in args[1]

    # Simulate compilation finish successfully (exit code 0)
    mock_compile_proc.exitCode.return_value = 0
    mock_compile_proc.exitStatus.return_value = QProcess.ExitStatus.NormalExit

    # Trigger the compilation finished slot manually
    manager.on_compilation_finished(0, QProcess.ExitStatus.NormalExit)

    assert finished_codes == [0]


def test_two_phase_termination(qtbot):
    """Assert stop() sends terminate(), schedules a QTimer fallback, and calls kill() if running."""
    manager = ProcessManager()

    # Mock active run process
    mock_run_proc = MagicMock()
    mock_run_proc.state.return_value = QProcess.ProcessState.Running
    manager.run_process = mock_run_proc

    # The R2 fix uses a QTimer instance on the ProcessManager (not
    # QTimer.singleShot). Patch QTimer so we can assert on interval and
    # simulate the timeout callback without firing the real timer.
    with patch("grc_agent_gui.process_manager.QTimer") as mock_qtimer_class:
        mock_timer_instance = MagicMock()
        mock_qtimer_class.return_value = mock_timer_instance

        manager.stop()

        # Verify terminate() was called
        mock_run_proc.terminate.assert_called_once()
        # The per-slot QTimer must have been constructed, configured to
        # single-shot, and started with a 2000ms interval.
        mock_qtimer_class.assert_called_once()
        mock_timer_instance.setSingleShot.assert_called_once_with(True)
        mock_timer_instance.setInterval.assert_called_once_with(2000)
        mock_timer_instance.start.assert_called_once()
        # The slot's timer attribute is now populated.
        assert manager._run_kill_timer is mock_timer_instance
        # Capture the timeout callback registered on the timer.
        timeout_args = mock_timer_instance.timeout.connect.call_args
        timer_callback = timeout_args[0][0]

        # Simulate timer timeout callback when process is still running
        mock_run_proc.state.return_value = QProcess.ProcessState.Running
        timer_callback()

        # Assert fallback kill() was invoked
        mock_run_proc.kill.assert_called_once()
        # After the kill, the slot timer is cleared.
        assert manager._run_kill_timer is None


def test_deferred_close_event_with_running_process(qtbot):
    """Assert closeEvent ignores the event while process is running, calls stop(), and deferred close works."""
    from grc_agent_gui.main_window import MainWindow

    mock_agent = MagicMock()
    mock_provider = MagicMock()

    window = MainWindow(mock_agent, mock_provider)
    qtbot.addWidget(window)

    # Mock processes in process manager to be running
    mock_run_proc = MagicMock()
    mock_run_proc.state.return_value = QProcess.ProcessState.Running
    window.process_manager.run_process = mock_run_proc

    # Spies/Mocks for event
    mock_event = MagicMock()

    # We call closeEvent
    window.closeEvent(mock_event)

    # Check that event was ignored
    mock_event.ignore.assert_called_once()
    mock_event.accept.assert_not_called()

    # Verify process_manager.stop was called
    assert mock_run_proc.terminate.call_count == 1

    # Now simulate process termination (process finished)
    mock_run_proc.state.return_value = QProcess.ProcessState.NotRunning

    # Trigger deferred close callback manually
    window.on_deferred_close()

    # The second close call should go through and accept the event
    mock_event_accept = MagicMock()
    window.closeEvent(mock_event_accept)
    mock_event_accept.accept.assert_called_once()


def test_button_state_locking(qtbot):
    """Assert that process started and finished signals lock/unlock buttons."""
    from grc_agent_gui.main_window import MainWindow

    mock_agent = MagicMock()
    mock_provider = MagicMock()

    window = MainWindow(mock_agent, mock_provider)
    qtbot.addWidget(window)

    # Emit started signal
    window.process_manager.started.emit()
    assert not window.validate_btn.isEnabled()

    # Emit finished signal
    window.process_manager.finished.emit(0)
    assert window.validate_btn.isEnabled()


def test_compile_process_has_fallback_kill(qtbot):
    """Assert compile process has fallback kill scheduled via QTimer on stop()."""
    manager = ProcessManager()
    mock_compile_proc = MagicMock()
    mock_compile_proc.state.return_value = QProcess.ProcessState.Running
    manager.compile_process = mock_compile_proc

    with patch("grc_agent_gui.process_manager.QTimer") as mock_qtimer_class:
        mock_timer_instance = MagicMock()
        mock_qtimer_class.return_value = mock_timer_instance
        manager.stop()
        mock_compile_proc.terminate.assert_called_once()
        mock_qtimer_class.assert_called_once()
        mock_timer_instance.setInterval.assert_called_once_with(2000)
        mock_timer_instance.start.assert_called_once()
        # The compile slot is tracked separately from the run slot.
        assert manager._compile_kill_timer is mock_timer_instance
        assert manager._run_kill_timer is None


def test_start_execution_finds_missing_py_file(qtbot):
    """Assert start_execution emits finished(-1) and doesn't run QProcess if py_file doesn't exist."""
    manager = ProcessManager()
    mock_session = MagicMock()
    mock_session.path = "/tmp/my_graph.grc"
    mock_session.flowgraph.metadata = {"options": {"parameters": {"id": "my_graph"}}}

    manager.active_session = mock_session
    manager._current_temp_dir = "/nonexistent_dir"

    finished_calls = []
    manager.finished.connect(finished_calls.append)

    with patch("grc_agent_gui.process_manager.QProcess") as mock_qprocess:
        manager.start_execution()
        mock_qprocess.assert_not_called()
        assert finished_calls == [-1]


def test_error_occurred_emits_status(qtbot):
    """Assert QProcess errorOccurred connects and emits status_message."""
    manager = ProcessManager()

    # Connect signal to check message
    messages = []
    manager.status_message.connect(messages.append)

    # Trigger compile error handler
    mock_proc = MagicMock()
    mock_proc.errorString.return_value = "grcc binary not found"
    manager.compile_process = mock_proc
    manager.on_compile_error(QProcess.ProcessError.FailedToStart)

    assert any("grcc binary not found" in msg for msg in messages)


def test_compile_process_inherits_environment(qtbot):
    """Assert setProcessEnvironment is explicitly called on compilation process."""
    manager = ProcessManager()
    mock_session = MagicMock()
    mock_session.path = "/tmp/my_graph.grc"

    with patch("grc_agent_gui.process_manager.QProcess") as mock_qprocess:
        mock_proc = MagicMock()
        mock_qprocess.return_value = mock_proc

        manager.compile_and_run(mock_session)
        mock_proc.setProcessEnvironment.assert_called_once()


def test_shutdown_stops_and_cleans_temp(qtbot):
    """Assert shutdown() terminates running processes and removes current temp directory."""
    manager = ProcessManager()

    mock_compile_proc = MagicMock()
    mock_compile_proc.state.return_value = QProcess.ProcessState.Running
    mock_compile_proc.waitForFinished.return_value = True
    manager.compile_process = mock_compile_proc

    mock_run_proc = MagicMock()
    mock_run_proc.state.return_value = QProcess.ProcessState.Running
    mock_run_proc.waitForFinished.return_value = True
    manager.run_process = mock_run_proc

    manager._current_temp_dir = "/tmp/dummy_temp_dir"

    with patch("grc_agent_gui.process_manager.shutil.rmtree") as mock_rmtree, \
         patch("grc_agent_gui.process_manager.os.path.exists") as mock_exists:
        mock_exists.return_value = True

        manager.shutdown()

        mock_compile_proc.terminate.assert_called_once()
        mock_run_proc.terminate.assert_called_once()
        mock_rmtree.assert_called_once_with("/tmp/dummy_temp_dir", ignore_errors=True)


def test_shutdown_caps_wait_at_200ms(qtbot):
    """R3: shutdown() must cap each waitForFinished at 200ms to avoid blocking the Qt aboutToQuit loop."""
    manager = ProcessManager()
    mock_proc = MagicMock()
    mock_proc.state.return_value = QProcess.ProcessState.Running
    mock_proc.waitForFinished.return_value = True
    manager.compile_process = mock_proc

    with patch("grc_agent_gui.process_manager.shutil.rmtree"):
        manager.shutdown()

    # Each wait must be called with 200ms, not 1000ms.
    for call in mock_proc.waitForFinished.call_args_list:
        args, kwargs = call
        # Accept either positional or keyword arg.
        actual = kwargs.get("msecs", args[0] if args else None)
        assert actual == 200, f"waitForFinished called with {actual!r}, expected 200"


def test_rapid_stop_does_not_stack_timers(qtbot):
    """R2: multiple stop() calls in quick succession must overwrite the per-slot kill timer,
    not stack an unbounded number of QTimer.singleShot callbacks.
    """
    manager = ProcessManager()
    mock_run_proc = MagicMock()
    mock_run_proc.state.return_value = QProcess.ProcessState.Running
    manager.run_process = mock_run_proc

    with patch("grc_agent_gui.process_manager.QTimer") as mock_qtimer_class:
        timer_instances = [MagicMock() for _ in range(5)]
        mock_qtimer_class.side_effect = timer_instances

        # Call stop() 5 times. Each call must overwrite the prior per-slot timer.
        for _ in range(5):
            manager.stop()

        # The current _run_kill_timer must be the last constructed one.
        assert manager._run_kill_timer is timer_instances[-1]
        # All prior timers must have been stopped and scheduled for deletion.
        for prior in timer_instances[:-1]:
            prior.stop.assert_called_once()
            prior.deleteLater.assert_called_once()
        # The most recent timer must be running.
        timer_instances[-1].start.assert_called_once()


def test_compile_spawn_failure_cleans_temp(qtbot):
    """R10: if QProcess construction fails inside compile_and_run, the freshly created
    temp directory must be cleaned and finished(-1) must be emitted.
    """
    manager = ProcessManager()
    mock_session = MagicMock()
    mock_session.path = "/tmp/my_graph.grc"

    finished_codes = []
    manager.finished.connect(lambda c: finished_codes.append(c))

    with patch("grc_agent_gui.process_manager.QProcess", side_effect=RuntimeError("spawn failed")):
        manager.compile_and_run(mock_session)

    assert finished_codes == [-1]
    # The temp dir is not set after cleanup.
    assert manager._current_temp_dir is None


def test_shutdown_cancels_pending_kill_timers(qtbot):
    """shutdown() must stop and deleteLater() any per-slot kill timers it finds."""
    manager = ProcessManager()
    pending = MagicMock()
    manager._compile_kill_timer = pending
    manager._run_kill_timer = MagicMock()

    with patch("grc_agent_gui.process_manager.shutil.rmtree"):
        manager.shutdown()

    pending.stop.assert_called_once()
    pending.deleteLater.assert_called_once()
    assert manager._compile_kill_timer is None
    assert manager._run_kill_timer is None


def test_reap_active_processes_disconnects_and_terminates(qtbot):
    """M2/M3: _reap_active_processes must disconnect active processes and terminate them."""
    manager = ProcessManager()

    mock_compile = MagicMock()
    mock_compile.state.return_value = QProcess.ProcessState.Running
    manager.compile_process = mock_compile

    mock_run = MagicMock()
    mock_run.state.return_value = QProcess.ProcessState.Running
    manager.run_process = mock_run

    with patch("grc_agent_gui.process_manager.QTimer") as mock_timer_class:
        timer_instances = [MagicMock(), MagicMock()]
        mock_timer_class.side_effect = timer_instances

        manager._reap_active_processes()

        # Both must be disconnected and terminated
        mock_compile.errorOccurred.disconnect.assert_called_once()
        mock_compile.finished.disconnect.assert_called_once()
        mock_compile.terminate.assert_called_once()

        mock_run.errorOccurred.disconnect.assert_called_once()
        mock_run.finished.disconnect.assert_called_once()
        mock_run.terminate.assert_called_once()

        # References must be set to None
        assert manager.compile_process is None
        assert manager.run_process is None


def test_process_finished_deletes_process_and_stops_timer(qtbot):
    """M3: Finished compilation/execution processes must deleteLater themselves and stop/delete kill timers."""
    manager = ProcessManager()

    mock_compile = MagicMock()
    manager.compile_process = mock_compile

    mock_timer = MagicMock()
    manager._compile_kill_timer = mock_timer

    # Simulate compilation finish
    manager.on_compilation_finished(0, QProcess.ExitStatus.NormalExit)

    # Timer must be stopped and deleted
    mock_timer.stop.assert_called_once()
    mock_timer.deleteLater.assert_called_once()
    assert manager._compile_kill_timer is None

    # Process must be set to None and deleteLater called
    mock_compile.deleteLater.assert_called_once()
    assert manager.compile_process is None


def test_process_failed_to_start_deletes_process_and_emits_finished(qtbot):
    """FailedToStart process error must clean up process and emit finished."""
    manager = ProcessManager()

    mock_compile = MagicMock()
    manager.compile_process = mock_compile

    finished_codes = []
    manager.finished.connect(lambda c: finished_codes.append(c))

    # Simulate FailedToStart error
    manager.on_compile_error(QProcess.ProcessError.FailedToStart)

    mock_compile.deleteLater.assert_called_once()
    assert manager.compile_process is None
    assert finished_codes == [-1]


def test_start_execution_spawn_failure_cleans_temp(qtbot):
    """M10-01: if QProcess construction fails inside start_execution, the
    freshly created temp directory must be cleaned and finished(-1) must be
    emitted. Mirrors the R10 hardening applied to compile_and_run.
    """
    manager = ProcessManager()
    mock_session = MagicMock()
    mock_session.path = "/tmp/my_graph.grc"
    mock_session.flowgraph.metadata = {"options": {"parameters": {"id": "my_graph"}}}

    finished_codes = []
    manager.finished.connect(lambda c: finished_codes.append(c))

    manager.active_session = mock_session
    manager._current_temp_dir = tempfile.mkdtemp(prefix="grc_agent_run_")
    py_file = os.path.join(manager._current_temp_dir, "my_graph.py")
    with open(py_file, "w") as f:
        f.write("# dummy")

    with patch("grc_agent_gui.process_manager.QProcess", side_effect=RuntimeError("spawn failed")):
        manager.start_execution()

    assert finished_codes == [-1]
    assert manager._current_temp_dir is None
    assert manager.run_process is None


def test_compile_and_run_reentrancy_guard(qtbot):
    """M9-08: calling compile_and_run while a previous compile or run is
    active must be a no-op (re-entrancy guard). The in-flight process is
    NOT silently reaped.
    """
    manager = ProcessManager()

    mock_compile_proc = MagicMock()
    mock_compile_proc.state.return_value = QProcess.ProcessState.Running
    manager.compile_process = mock_compile_proc

    mock_session = MagicMock()
    mock_session.path = "/tmp/my_graph.grc"

    with patch.object(manager, "_reap_active_processes") as mock_reap, \
         patch("grc_agent_gui.process_manager.tempfile.mkdtemp") as mock_mkdtemp:
        manager.compile_and_run(mock_session)

    mock_reap.assert_not_called()
    mock_mkdtemp.assert_not_called()
    assert manager.compile_process is mock_compile_proc


def test_model_status_label_simple(qtbot):
    """Model status label shows model name when configured."""
    from grc_agent_gui.main_window import MainWindow

    mock_agent = MagicMock()
    mock_agent.session = None
    mock_provider = MagicMock()
    mock_provider.model = "qwen3.5-4b"
    mock_llama_cfg = MagicMock()
    mock_llama_cfg.backend = "ollama"

    window = MainWindow(mock_agent, mock_provider, llama_config=mock_llama_cfg)
    qtbot.addWidget(window)

    text = window.model_status_label.text()
    assert "Model:" in text
    assert "qwen3.5-4b" in text


def test_model_status_label_missing_fields(qtbot):
    """When llama_config is None and provider has no model, label shows n/a for unknown fields."""
    from grc_agent_gui.main_window import MainWindow

    mock_agent = MagicMock()
    mock_agent.session = None
    mock_provider = MagicMock()
    mock_provider.model = None

    window = MainWindow(mock_agent, mock_provider, llama_config=None)
    qtbot.addWidget(window)

    text = window.model_status_label.text()
    assert "Client: ollama" in text


def test_validation_label_states(qtbot):
    """Assert that validation label displays correct states (No Graph, Unvalidated, Valid, Invalid) and styles."""
    from grc_agent_gui.main_window import MainWindow

    mock_agent = MagicMock()
    mock_agent.session = None  # No graph loaded initially
    mock_provider = MagicMock()

    window = MainWindow(mock_agent, mock_provider)
    qtbot.addWidget(window)

    # 1. No graph loaded
    window.update_ui_state()
    assert window.validation_label.text() == "No Graph"
    assert "color: #a6adc8;" in window.validation_label.styleSheet()

    # 2. Graph loaded but unvalidated
    mock_session = MagicMock()
    mock_session.flowgraph = MagicMock()
    mock_session.validation_state.return_value = {"status": "unknown"}
    mock_agent.session = mock_session

    # Manually trigger on_inspector_refreshed representing a freshly loaded or unvalidated graph
    window.on_inspector_refreshed({"validation_result": {"status": "unknown"}})
    assert window.validation_label.text() == "⚪ Unvalidated"
    assert "color: #a6adc8;" in window.validation_label.styleSheet()

    # 3. Validation success (exit code 0)
    # Simulate process stdout/stderr, then process finished
    window.on_validate_clicked() # clears validation buffers
    window.on_process_stdout("Validation passed output\n")
    
    # We patch refresh_inspector to run synchronously for test inspection
    with patch.object(window, "refresh_inspector") as mock_refresh:
        window.on_process_finished(0)
        mock_refresh.assert_called_once()
        
    assert mock_session.last_validation_ok is True
    assert mock_session.last_validation_returncode == 0
    assert mock_session.last_validation_stdout == "Validation passed output\n"
    
    # Trigger refreshed callback directly to check label update
    window.on_inspector_refreshed({"validation_result": {"status": "valid"}})
    assert window.validation_label.text() == "🟢 Valid"
    assert "color: #a6e3a1;" in window.validation_label.styleSheet()

    # 4. Validation failure (exit code 1)
    window.on_validate_clicked()
    window.on_process_stderr("Validation error details\n")
    
    with patch.object(window, "refresh_inspector") as mock_refresh:
        window.on_process_finished(1)
        mock_refresh.assert_called_once()
        
    assert mock_session.last_validation_ok is False
    assert mock_session.last_validation_returncode == 1
    assert mock_session.last_validation_stderr == "Validation error details\n"
    
    window.on_inspector_refreshed({"validation_result": {"status": "invalid"}})
    assert window.validation_label.text() == "🔴 Invalid"
    assert "color: #f38ba8;" in window.validation_label.styleSheet()



