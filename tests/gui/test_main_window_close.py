import os
import sys
from unittest.mock import MagicMock, patch

from PySide6.QtCore import QProcess
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication

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


def test_stale_graph_warning_clears_on_re_run(qtbot):
    """M9-06: restarting the flowgraph (on_process_started) must clear the
    _last_applied_revision so the stale-graph warning does not persist
    past a re-run.
    """
    from unittest.mock import MagicMock

    from PySide6.QtCore import QProcess

    mock_agent = MagicMock()
    mock_agent.session.state_revision = 5
    mock_provider = MagicMock()

    window = MainWindow(mock_agent, mock_provider)
    qtbot.addWidget(window)
    window.show()

    mock_run_proc = MagicMock()
    mock_run_proc.state.return_value = QProcess.ProcessState.Running
    window.process_manager.run_process = mock_run_proc

    window.on_turn_finished({"ok": True, "assistant_text": "first"})
    mock_agent.session.state_revision = 6
    window.on_turn_finished({"ok": True, "assistant_text": "second"})
    assert "stale" in window.status_bar.currentMessage().lower()

    window.on_process_started()
    assert window._last_applied_revision is None


def test_console_log_has_maximum_block_count(qtbot):
    """M9-04: console_log must have setMaximumBlockCount configured so that
    long-running flowgraphs do not cause unbounded memory growth in the
    QTextDocument.
    """
    from unittest.mock import MagicMock

    mock_agent = MagicMock()
    mock_provider = MagicMock()

    window = MainWindow(mock_agent, mock_provider)
    qtbot.addWidget(window)

    assert window.console_log.maximumBlockCount() > 0
    assert window.console_log.maximumBlockCount() == 10000




def test_main_window_has_help_menu(qtbot):
    """The File and Help menus must both exist with the expected items."""
    from grc_agent_gui.main_window import MainWindow
    widget = MainWindow(MagicMock(), MagicMock())
    qtbot.addWidget(widget)

    menubar = widget.menuBar()
    titles = [a.text() for a in menubar.actions()]
    assert any("File" in t for t in titles)
    assert any("Help" in t for t in titles)


def test_main_window_help_menu_has_about_and_open_docs(qtbot):
    """The Help menu must contain About and Open Docs Folder actions."""
    from grc_agent_gui.main_window import MainWindow
    widget = MainWindow(MagicMock(), MagicMock())
    qtbot.addWidget(widget)

    help_menu = None
    for menu_action in widget.menuBar().actions():
        if "Help" in menu_action.text():
            help_menu = menu_action.menu()
            break
    assert help_menu is not None, "Help menu not found"
    help_actions = [a.text() for a in help_menu.actions()]
    assert any("About" in a for a in help_actions)
    assert any("Docs" in a for a in help_actions)


def test_main_window_file_menu_has_export_chat_and_output_folder(qtbot):
    """The File menu must contain Export Chat and Open Output Folder actions."""
    from grc_agent_gui.main_window import MainWindow
    widget = MainWindow(MagicMock(), MagicMock())
    qtbot.addWidget(widget)

    file_menu = None
    for menu_action in widget.menuBar().actions():
        if "File" in menu_action.text():
            file_menu = menu_action.menu()
            break
    assert file_menu is not None, "File menu not found"
    file_actions = [a.text() for a in file_menu.actions()]
    assert any("Export Chat" in a for a in file_actions)
    assert any("Output Folder" in a for a in file_actions)


def test_main_window_accepts_file_drops(qtbot):
    """MainWindow must accept drag-and-drops of .grc files."""
    from grc_agent_gui.main_window import MainWindow
    widget = MainWindow(MagicMock(), MagicMock())
    qtbot.addWidget(widget)

    assert widget.acceptDrops() is True


def test_main_window_about_action_does_not_crash(monkeypatch, qtbot):
    """The About dialog must be invokable without crashing even when QMessageBox is monkeypatched."""
    from grc_agent_gui.main_window import MainWindow
    from PySide6.QtWidgets import QMessageBox

    calls = []
    monkeypatch.setattr(
        QMessageBox, "exec", lambda self, *a, **kw: calls.append("exec") or 0
    )
    widget = MainWindow(MagicMock(), MagicMock())
    qtbot.addWidget(widget)
    widget.show_about_dialog()
    assert calls == ["exec"]
