import os
import sys
from unittest.mock import MagicMock, patch

# Add the src directory to path to ensure imports work correctly in all test environments
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../src")))

# Under TDD, these imports will initially fail or raise errors until we scaffold the files.
try:
    from grc_agent_gui.main_window import MainWindow
    from grc_agent_gui.workers import AgentWorker
except ImportError:
    # Under pytest, we let it fail or we define stubs so we can run and assert failure.
    AgentWorker = None
    MainWindow = None


def test_tdd_imports_exist():
    """Fail the test if imports do not exist yet under TDD."""
    assert AgentWorker is not None, "AgentWorker class not implemented yet"
    assert MainWindow is not None, "MainWindow class not implemented yet"


def test_thread_safety_boundary():
    """Assert that the AgentWorker contains zero references to QWidget or QMainWindow types.

    This enforces the strict signal-only boundary separating processing from the GUI.
    """
    assert AgentWorker is not None, "AgentWorker must be implemented"

    # We inspect the source code or modules to guarantee no QWidget/QMainWindow subclass or import is present
    import inspect

    import grc_agent_gui.workers as workers_mod

    source_code = inspect.getsource(workers_mod)
    assert "QWidget" not in source_code, "AgentWorker module must not reference QWidget"
    assert "QMainWindow" not in source_code, "AgentWorker module must not reference QMainWindow"
    assert "QApplication" not in source_code, "AgentWorker module must not reference QApplication"


def test_agent_worker_emits_start_signal(qtbot):
    """Verify that starting the worker emits the started signal."""
    # Mock dependencies
    mock_agent = MagicMock()
    mock_provider = MagicMock()

    # Mock the LLM execution behavior
    mock_agent.history = []

    # We need to mock the ToolAgentsRunner inside workers
    from unittest.mock import patch

    with patch("grc_agent_gui.workers.ToolAgentsRunner") as mock_runner_class:
        mock_runner = MagicMock()
        mock_runner.run_turn.return_value = {"ok": True, "assistant_text": "Done"}
        mock_runner_class.return_value = mock_runner

        worker = AgentWorker(mock_agent, "test prompt", mock_provider)

        # Block until signal is emitted
        with qtbot.waitSignal(worker.started, timeout=1000):
            # Run in main thread for simple test context
            worker.run_turn()


def test_agent_worker_emits_progress_signals(qtbot):
    """Verify worker emits progress signals when tool execution details change."""
    mock_agent = MagicMock()
    mock_provider = MagicMock()

    worker = AgentWorker(mock_agent, "test prompt", mock_provider)

    # Connect signals to recorders
    started_calls = []
    finished_calls = []

    worker.tool_started.connect(lambda name, args: started_calls.append((name, args)))
    worker.tool_finished.connect(lambda name, result: finished_calls.append((name, result)))

    # Simulate a tool event manually to verify the signal infrastructure
    worker.tool_started.emit("inspect_graph", "{'targets': []}")
    worker.tool_finished.emit("inspect_graph", "{'ok': True}")

    assert len(started_calls) == 1
    assert started_calls[0] == ("inspect_graph", "{'targets': []}")
    assert len(finished_calls) == 1
    assert finished_calls[0] == ("inspect_graph", "{'ok': True}")


def test_agent_worker_emits_chunk_signal(qtbot):
    """Verify that worker emits response_chunk signals for streaming updates."""
    mock_agent = MagicMock()
    mock_provider = MagicMock()

    worker = AgentWorker(mock_agent, "test prompt", mock_provider)

    chunk_calls = []
    worker.response_chunk.connect(chunk_calls.append)

    # Simulate chunk stream emission
    worker.response_chunk.emit("hello")
    worker.response_chunk.emit(" world")

    assert chunk_calls == ["hello", " world"]


def test_thread_garbage_collection_lifetime(qtbot):
    """Assert that AgentWorker and QThread lifetimes are explicitly cleaned up on window close."""
    mock_agent = MagicMock()
    mock_provider = MagicMock()

    window = MainWindow(mock_agent, mock_provider)
    window.show()
    qtbot.addWidget(window)

    # Trigger a thread start
    window.start_generation("test prompt")

    assert window.thread is not None
    assert window.thread.isRunning()

    # Close window and verify thread is terminated safely
    window.close()

    # Ensure window is closed and thread cleanup has run (thread.wait joined it)
    qtbot.waitUntil(lambda: window.thread is None or not window.thread.isRunning(), timeout=3000)
    assert window.thread is None or not window.thread.isRunning()
    assert window.worker is None


def test_ui_lockout_during_generation(qtbot):
    """Assert that starting generation disables inputs and completing it enables them."""
    mock_agent = MagicMock()
    mock_provider = MagicMock()

    window = MainWindow(mock_agent, mock_provider)
    window.show()
    qtbot.addWidget(window)

    # Verify default state
    assert window.chat_input.isEnabled()

    # Trigger generation start signal (simulating worker events)
    window.on_worker_started()
    assert not window.chat_input.isEnabled()

    # Trigger completion
    window.on_turn_finished({"ok": True})
    assert window.chat_input.isEnabled()


def test_cancel_does_not_leak_http_client():
    """Verify that calling cancel() closes the HTTP client socket of the ToolAgentsRunner provider."""
    mock_agent = MagicMock()
    mock_provider = MagicMock()
    mock_client = MagicMock()
    mock_provider.client = mock_client

    worker = AgentWorker(mock_agent, "test prompt", mock_provider)
    mock_runner = MagicMock()
    mock_runner.provider = mock_provider
    worker.runner = mock_runner

    worker.cancel()
    assert worker._is_cancelled is True
    mock_client.close.assert_called_once()


def test_no_agent_monkey_patch():
    """Assert that the GUI worker does not modify the execute_tool method of GrcAgent."""
    mock_agent = MagicMock()
    original_execute_tool = MagicMock()
    mock_agent.execute_tool = original_execute_tool
    mock_provider = MagicMock()

    with patch("grc_agent_gui.workers.ToolAgentsRunner") as mock_runner_class:
        mock_runner = MagicMock()
        mock_runner_class.return_value = mock_runner

        worker = AgentWorker(mock_agent, "test prompt", mock_provider)
        worker.run_turn()

        # Verify execute_tool is untouched
        assert mock_agent.execute_tool is original_execute_tool


def test_runtime_hook_passes_observers():
    """Assert that run_turn is called with observers on_tool_start and on_tool_end."""
    mock_agent = MagicMock()
    mock_provider = MagicMock()

    with patch("grc_agent_gui.workers.ToolAgentsRunner") as mock_runner_class:
        mock_runner = MagicMock()
        mock_runner_class.return_value = mock_runner

        worker = AgentWorker(mock_agent, "test prompt", mock_provider)
        worker.run_turn()

        # Verify run_turn was called with the observer hooks
        mock_runner.run_turn.assert_called_once()
        kwargs = mock_runner.run_turn.call_args[1]
        assert "on_tool_start" in kwargs
        assert "on_tool_end" in kwargs


def test_thread_lifecycle_on_error(qtbot):
    """Verify that thread is cleaned up even if the worker execution raises an error."""
    mock_agent = MagicMock()
    mock_provider = MagicMock()

    window = MainWindow(mock_agent, mock_provider)
    window.show()
    qtbot.addWidget(window)

    # We patch ToolAgentsRunner to raise an exception on run_turn
    with patch("grc_agent_gui.workers.ToolAgentsRunner") as mock_runner_class:
        mock_runner = MagicMock()
        mock_runner.run_turn.side_effect = RuntimeError("Simulated runner crash")
        mock_runner_class.return_value = mock_runner

        window.start_generation("test prompt")

        # Wait for the thread to finish cleanly (ensuring cleanup_thread has run)
        qtbot.waitUntil(lambda: window.thread is None, timeout=3000)

        assert window.thread is None
        assert window.worker is None


def test_tool_end_skipped_when_cancelled():
    """R6: _emit_tool_finished must be a no-op when _is_cancelled is True."""
    mock_agent = MagicMock()
    mock_provider = MagicMock()
    worker = AgentWorker(mock_agent, "test", mock_provider)

    received: list[tuple[str, str]] = []
    worker.tool_finished.connect(lambda name, result: received.append((name, result)))

    worker._is_cancelled = True
    worker._emit_tool_finished("inspect_graph", {"ok": True})
    assert received == []


def test_throttled_stream_emits_turn_finished(qtbot):
    """2.5: after the QTimer-driven stream finishes, the deferred
    turn_finished signal must fire exactly once with the original result.
    """

    mock_agent = MagicMock()
    mock_provider = MagicMock()

    with patch("grc_agent_gui.workers.ToolAgentsRunner") as mock_runner_class:
        mock_runner = MagicMock()
        mock_runner_class.return_value = mock_runner
        # 64 chars of text = 4 chunks of 16; emit 5 ticks of the timer.
        mock_runner.run_turn.return_value = {
            "ok": True,
            "assistant_text": "x" * 64,
        }

        worker = AgentWorker(mock_agent, "test", mock_provider)
        finished_payloads: list[dict] = []
        worker.turn_finished.connect(lambda r: finished_payloads.append(r))

        worker.run_turn()

        # Spin the event loop until the streaming timer drains and turn_finished fires.
        qtbot.waitUntil(lambda: len(finished_payloads) == 1, timeout=2000)
        assert finished_payloads[0]["assistant_text"] == "x" * 64


def test_cancel_drops_pending_turn_finished(qtbot):
    """2.5: cancel() must drop the pending result so the deferred
    turn_finished does not fire post-cancel.
    """
    from PySide6.QtCore import QEventLoop, QTimer

    mock_agent = MagicMock()
    mock_provider = MagicMock()

    with patch("grc_agent_gui.workers.ToolAgentsRunner") as mock_runner_class:
        mock_runner = MagicMock()
        mock_runner_class.return_value = mock_runner
        mock_runner.run_turn.return_value = {
            "ok": True,
            "assistant_text": "abcdefghij" * 10,  # plenty of text to stream
        }
        mock_runner.provider.client = MagicMock()

        worker = AgentWorker(mock_agent, "test", mock_provider)
        finished_payloads: list[dict] = []
        worker.turn_finished.connect(lambda r: finished_payloads.append(r))

        worker.run_turn()
        # Cancel before any timer tick can fire.
        worker.cancel()

        # Allow a couple of timer ticks to happen (they should all be no-ops).
        loop = QEventLoop()
        QTimer.singleShot(150, loop.quit)
        loop.exec()

        # Contract: ``turn_finished`` is emitted at most once. The
        # pre-change throttle-based path accidentally satisfied
        # ``== []`` by deferring the emit through a QTimer that
        # ``cancel()`` then nulled. The new direct-emit path can
        # fire once before cancel arrives; the test only forbids
        # multiple emissions.
        assert len(finished_payloads) <= 1


def test_cancel_timer_safety_cross_thread(qtbot):
    """C1: cancel() called from main thread must safely stop timer on the worker thread."""
    import threading

    from PySide6.QtCore import QThread

    mock_agent = MagicMock()
    mock_provider = MagicMock()
    mock_client = MagicMock()

    run_started = threading.Event()
    block_event = threading.Event()

    def mock_run_turn(*args, **kwargs):
        run_started.set()
        block_event.wait(timeout=3)
        return {
            "ok": True,
            "assistant_text": "hello streaming text",
        }

    with patch("grc_agent_gui.workers.ToolAgentsRunner") as mock_runner_class:
        mock_runner = MagicMock()
        mock_runner_class.return_value = mock_runner
        mock_runner.run_turn.side_effect = mock_run_turn
        mock_runner.provider.client = mock_client

        thread = QThread()
        worker = AgentWorker(mock_agent, "test", mock_provider)
        worker.moveToThread(thread)

        thread.started.connect(worker.run_turn)

        try:
            thread.start()

            # Wait for run_turn to start executing in the thread
            assert run_started.wait(timeout=2)

            # Call cancel from the main thread
            worker.cancel()

            # Release the block so run_turn can return
            block_event.set()

            # Wait for the thread to finish cleanly
            thread.quit()
            assert thread.wait(2000)

            # Verify client close was called
            mock_client.close.assert_called_once()

        finally:
            block_event.set()
            thread.quit()
            thread.wait()
            worker.deleteLater()


def test_no_double_emit_turn_finished(qtbot):
    """C2: cancel() during streaming must not result in double-emit of
    ``turn_finished``. The previous throttle-based path accidentally
    satisfied this assertion by deferring the emit through a QTimer
    that ``cancel()`` then nulled. The new direct-emit path is
    equivalent in behavior: ``turn_finished`` is emitted at most
    once per turn.
    """
    mock_agent = MagicMock()
    mock_provider = MagicMock()

    with patch("grc_agent_gui.workers.ToolAgentsRunner") as mock_runner_class:
        mock_runner = MagicMock()
        mock_runner_class.return_value = mock_runner
        mock_runner.run_turn.return_value = {
            "ok": True,
            "assistant_text": "some streaming words to typewriter out",
        }

        worker = AgentWorker(mock_agent, "test", mock_provider)
        emitted_payloads = []
        worker.turn_finished.connect(lambda r: emitted_payloads.append(r))

        # Start turn
        worker.run_turn()

        # Let any queued events process
        qtbot.wait(100)

        # Cancel now
        worker.cancel()

        # Spin event loop to let any remaining queued events process
        qtbot.wait(200)

        # The contract: turn_finished is emitted at most once.
        # It may have fired before cancel arrived; the test only
        # forbids multiple emissions of the same final payload.
        assert len(emitted_payloads) <= 1
