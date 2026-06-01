import os
import sys
from unittest.mock import MagicMock, patch
from PySide6.QtCore import QProcess
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QCloseEvent

# Add src to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../src")))

from grc_agent_gui.main_window import MainWindow


def test_close_during_compile_defers(qtbot):
    """Assert that closing during compilation defers close and stops the processes."""
    mock_agent = MagicMock()
    mock_provider = MagicMock()
    
    window = MainWindow(mock_agent, mock_provider)
    qtbot.addWidget(window)
    window.show()
    
    # Set compilation process to running
    mock_compile_proc = MagicMock()
    mock_compile_proc.state.return_value = QProcess.ProcessState.Running
    window.process_manager.compile_process = mock_compile_proc
    window.process_manager.run_process = None
    
    with patch.object(window.process_manager, "stop") as mock_stop:
        # Create close event
        event = QCloseEvent()
        window.closeEvent(event)
        
        # Verify close was deferred
        assert not event.isAccepted()
        assert window._pending_close is True
        mock_stop.assert_called_once()
        
        # Verify we connected the deferred handler and can trigger it
        with patch.object(window, "close") as mock_close:
            # Simulate compile process finished (state transitions to NotRunning)
            mock_compile_proc.state.return_value = QProcess.ProcessState.NotRunning
            window.process_manager.finished.emit(0)
            mock_close.assert_called_once()


def test_close_event_connected_once(qtbot):
    """Verify that multiple close attempts do not result in multiple on_deferred_close connections."""
    mock_agent = MagicMock()
    mock_provider = MagicMock()
    
    window = MainWindow(mock_agent, mock_provider)
    qtbot.addWidget(window)
    window.show()
    
    # Mock compile process to be running
    mock_compile_proc = MagicMock()
    mock_compile_proc.state.return_value = QProcess.ProcessState.Running
    window.process_manager.compile_process = mock_compile_proc
    
    # Track call count of on_deferred_close
    call_count = 0
    original_handler = window.on_deferred_close
    
    def dummy_handler(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        original_handler(*args, **kwargs)
        
    window.on_deferred_close = dummy_handler
    
    # Trigger closeEvent multiple times
    for _ in range(3):
        event = QCloseEvent()
        window.closeEvent(event)
        
    # Simulate finished signal
    mock_compile_proc.state.return_value = QProcess.ProcessState.NotRunning
    window.process_manager.finished.emit(0)
    
    # It should have fired exactly once
    assert call_count == 1


def test_pending_close_flag_reset(qtbot):
    """Assert the full deferred close lifecycle resets the _pending_close flag and accepts close."""
    mock_agent = MagicMock()
    mock_provider = MagicMock()
    
    window = MainWindow(mock_agent, mock_provider)
    qtbot.addWidget(window)
    window.show()
    
    # Mock run process running
    mock_run_proc = MagicMock()
    mock_run_proc.state.return_value = QProcess.ProcessState.Running
    window.process_manager.run_process = mock_run_proc
    
    # Close once
    event1 = QCloseEvent()
    window.closeEvent(event1)
    assert not event1.isAccepted()
    assert window._pending_close is True
    
    # Simulate process finished
    mock_run_proc.state.return_value = QProcess.ProcessState.NotRunning
    window.process_manager.finished.emit(0)
    
    assert window._pending_close is False
    assert window._safe_to_close is True


def test_about_to_quit_triggers_shutdown(qtbot):
    """Verify that QApplication.aboutToQuit signal triggers ProcessManager.shutdown."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])

    mock_agent = MagicMock()
    mock_provider = MagicMock()

    window = MainWindow(mock_agent, mock_provider)
    qtbot.addWidget(window)
    window.show()

    # Connect signal just like in app.py
    app.aboutToQuit.connect(window.process_manager.shutdown)

    with patch.object(window.process_manager, "shutdown") as mock_shutdown:
        # Emit aboutToQuit
        app.aboutToQuit.emit()
        mock_shutdown.assert_called_once()


def test_thread_finished_disconnected_after_cleanup(qtbot):
    """R4: cleanup_thread must disconnect the thread.finished binding
    so that on_deferred_close is not invoked against a destroyed QThread.
    """
    from unittest.mock import MagicMock

    mock_agent = MagicMock()
    mock_provider = MagicMock()

    window = MainWindow(mock_agent, mock_provider)
    qtbot.addWidget(window)
    window.show()

    window.start_generation("test prompt")
    assert window.thread is not None
    assert window.thread.isRunning()

    # Drive a thread that completes its run quickly.
    qtbot.waitUntil(lambda: window.thread is None or not window.thread.isRunning(), timeout=3000)

    # After cleanup_thread ran, the receiver count for finished -> on_deferred_close
    # must be zero. Qt raises TypeError if the connection still exists.
    assert window.thread is None or window.thread.receivers(window.thread.finished) == 0


def test_inspector_runnable_does_not_import_unittest_mock():
    """R5: production code in main_window.py must not import unittest.mock."""
    import inspect
    from grc_agent_gui import main_window

    source = inspect.getsource(main_window)
    assert "unittest.mock" not in source
    assert "Mock" not in source or "Mock(" not in source


def test_stale_graph_warning_fires_on_revision_change(qtbot):
    """4.6: when a mutation lands while a flowgraph is running, the
    status bar must surface a stale-graph warning.
    """
    from unittest.mock import MagicMock
    from PySide6.QtCore import QProcess

    mock_agent = MagicMock()
    mock_agent.session.state_revision = 5
    mock_provider = MagicMock()

    window = MainWindow(mock_agent, mock_provider)
    qtbot.addWidget(window)
    window.show()

    # Mark the run process as active.
    mock_run_proc = MagicMock()
    mock_run_proc.state.return_value = QProcess.ProcessState.Running
    window.process_manager.run_process = mock_run_proc

    # First turn establishes the baseline revision.
    window.on_turn_finished({"ok": True, "assistant_text": "first"})
    assert "stale" not in window.status_bar.currentMessage().lower()

    # Second turn advances the on-disk revision while the flowgraph is still running.
    mock_agent.session.state_revision = 6
    window.on_turn_finished({"ok": True, "assistant_text": "second"})
    assert "stale" in window.status_bar.currentMessage().lower()
