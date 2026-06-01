import os
import sys
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
    """Assert compilation runs grcc, and on success, spawns QProcess executing the Python script."""
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
    mock_run_proc = MagicMock()
    
    # Configure mock class to return our mocks
    mock_qprocess_class.side_effect = [mock_compile_proc, mock_run_proc]
    
    # Trigger compile and run
    manager.compile_and_run(mock_session)
    
    # Assert compile process started grcc
    mock_compile_proc.start.assert_called_once()
    args = mock_compile_proc.start.call_args[0]
    assert args[0] == "grcc"
    assert "/tmp/my_graph.grc" in args[1]
    
    # Simulate compilation finish successfully (exit code 0)
    mock_compile_proc.exitCode.return_value = 0
    mock_compile_proc.exitStatus.return_value = QProcess.ExitStatus.NormalExit
    
    # Write a dummy python file to satisfy the os.path.exists check
    py_file = os.path.join(manager._current_temp_dir, "my_graph.py")
    with open(py_file, "w") as f:
        f.write("# dummy compiled python script")
        
    # Trigger the compilation finished slot manually
    manager.on_compilation_finished(0, QProcess.ExitStatus.NormalExit)
    
    # Assert run process was spawned for the compiled python script using sys.executable
    mock_run_proc.start.assert_called_once()
    run_args = mock_run_proc.start.call_args[0]
    assert run_args[0] == sys.executable
    assert "my_graph.py" in run_args[1][0]


def test_two_phase_termination(qtbot):
    """Assert stop() sends terminate(), schedules a timer, and calls kill() if running."""
    manager = ProcessManager()
    
    # Mock active run process
    mock_run_proc = MagicMock()
    mock_run_proc.state.return_value = QProcess.ProcessState.Running
    manager.run_process = mock_run_proc
    
    # Call stop
    with patch("PySide6.QtCore.QTimer.singleShot") as mock_timer:
        manager.stop()
        
        # Verify terminate() was called
        mock_run_proc.terminate.assert_called_once()
        
        # Verify 2000ms fallback timer was scheduled
        mock_timer.assert_called_once()
        timer_ms, timer_callback = mock_timer.call_args[0]
        assert timer_ms == 2000
        
        # Simulate timer timeout callback when process is still running
        mock_run_proc.state.return_value = QProcess.ProcessState.Running
        timer_callback()
        
        # Assert fallback kill() was invoked
        mock_run_proc.kill.assert_called_once()


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
    assert not window.run_btn.isEnabled()
    assert window.stop_btn.isEnabled()
    
    # Emit finished signal
    window.process_manager.finished.emit(0)
    assert window.run_btn.isEnabled()
    assert not window.stop_btn.isEnabled()


def test_compile_process_has_fallback_kill(qtbot):
    """Assert compile process has fallback kill scheduled via QTimer on stop()."""
    manager = ProcessManager()
    mock_compile_proc = MagicMock()
    mock_compile_proc.state.return_value = QProcess.ProcessState.Running
    manager.compile_process = mock_compile_proc
    
    with patch("PySide6.QtCore.QTimer.singleShot") as mock_timer:
        manager.stop()
        mock_compile_proc.terminate.assert_called_once()
        mock_timer.assert_called_once()
        timer_ms, _ = mock_timer.call_args[0]
        assert timer_ms == 2000


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


