import os
import sys
from unittest.mock import MagicMock

from pathlib import Path
from PySide6.QtCore import Qt

# Add src to system path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../src")))

from grc_agent_gui.sidebar_widget import SidebarWidget
from grc_agent.sessions_store import SessionRecord, SessionStore
from grc_agent_gui.main_window import MainWindow


def _make_session(id: int = 1, **overrides) -> SessionRecord:
    base = dict(
        id=id,
        graph_path="/tmp/example.grc",
        graph_hash="grc:abc",
        started_at="2026-06-01T00:00:00.000000Z",
        ended_at=None,
        model_alias="test-model",
        backend="llama_cpp",
        title=f"Session {id}",
        message_count=0,
        graph_exists=True,
    )
    base.update(overrides)
    return SessionRecord(**base)


def test_sidebar_widget_builds_and_populates(qtbot):
    widget = SidebarWidget()
    qtbot.addWidget(widget)
    
    sessions = [
        _make_session(id=1, title="Alpha", message_count=3),
        _make_session(id=2, title="Beta", message_count=1),
    ]
    widget.populate_sessions(sessions)
    assert widget.list_widget.count() == 2

    # Inspect the first item text
    item = widget.list_widget.item(0)
    assert "Alpha" in item.text()
    assert item.data(Qt.ItemDataRole.UserRole) == 1


def test_sidebar_signals_emitted(qtbot):
    widget = SidebarWidget()
    qtbot.addWidget(widget)
    
    sessions = [_make_session(id=42, title="Target")]
    widget.populate_sessions(sessions)

    # Test collapse requested signal
    collapse_emitted = False
    def on_collapse():
        nonlocal collapse_emitted
        collapse_emitted = True
    widget.collapse_requested.connect(on_collapse)
    widget.collapse_btn.click()
    assert collapse_emitted

    # Test new chat requested signal
    new_chat_emitted = False
    def on_new_chat():
        nonlocal new_chat_emitted
        new_chat_emitted = True
    widget.new_chat_requested.connect(on_new_chat)
    widget.new_chat_btn.click()
    assert new_chat_emitted

    # Test session selection double click
    selected_session_id = None
    def on_session_selected(sid):
        nonlocal selected_session_id
        selected_session_id = sid
    widget.session_selected.connect(on_session_selected)
    widget._on_item_double_clicked(widget.list_widget.item(0))
    assert selected_session_id == 42


def test_main_window_sidebar_integration(qtbot, tmp_path, monkeypatch):
    db_path = tmp_path / "sessions.db"
    import grc_agent_gui.main_window
    monkeypatch.setattr(grc_agent_gui.main_window, "_default_sessions_db", lambda: db_path)

    # Clean up any leftover singleton before starting
    SessionStore._instance = None

    mock_agent = MagicMock()
    mock_agent.session = None
    mock_provider = MagicMock()
    
    window = MainWindow(mock_agent, mock_provider)
    qtbot.addWidget(window)
    
    assert window.sidebar_widget is not None
    assert not window.sidebar_widget.isHidden()
    
    # Test toggle sidebar
    window.toggle_sidebar()
    assert window.sidebar_widget.isHidden()
    
    window.toggle_sidebar()
    assert not window.sidebar_widget.isHidden()

    # Mock start_generation to prevent spinning up background thread during test
    monkeypatch.setattr(window, "start_generation", MagicMock())

    # Test new session creation on user prompt
    window.chat_input.setText("Testing sidebar integration")
    window.send_prompt()
    
    assert window.active_session_id is not None
    
    # Flush SQLite operations to guarantee it is written
    window.sessions_store.flush(timeout=2.0)
    
    # Checking if the database has it
    sessions = window.sessions_store._writer_conn.execute("SELECT id, title FROM sessions").fetchall()
    assert len(sessions) == 1
    assert sessions[0][1] == "Testing sidebar integration"

    # Explicit teardown of the thread and DB connection to prevent segmentation fault
    window.sessions_store.close()
    SessionStore._instance = None


def test_open_past_session_autoloads_graph(qtbot, tmp_path, monkeypatch):
    db_path = tmp_path / "sessions.db"
    import grc_agent_gui.main_window
    monkeypatch.setattr(grc_agent_gui.main_window, "_default_sessions_db", lambda: db_path)

    SessionStore._instance = None

    mock_agent = MagicMock()
    mock_agent.session = None
    mock_provider = MagicMock()
    
    window = MainWindow(mock_agent, mock_provider)
    qtbot.addWidget(window)

    # Mock open_file on the window
    mock_open_file = MagicMock()
    monkeypatch.setattr(window, "open_file", mock_open_file)

    # Write a dummy grc file to tmp_path
    grc_file = tmp_path / "dummy.grc"
    grc_file.write_text("dummy content")

    # Create a session in the DB
    session_id = window.sessions_store.open_session(
        graph_path=str(grc_file),
        graph_hash="hash123",
        model_alias="modelA",
        backend="llama_cpp",
        title="Session Graph Load",
    )
    window.sessions_store.flush(timeout=2.0)

    # Open the past session
    window._open_past_session(session_id)

    # Verify that open_file was called with the correct path
    mock_open_file.assert_called_once_with(Path(grc_file))

    # Explicit teardown
    window.sessions_store.close()
    SessionStore._instance = None


def test_open_past_session_resumes_context(qtbot, tmp_path, monkeypatch):
    db_path = tmp_path / "sessions.db"
    import grc_agent_gui.main_window
    monkeypatch.setattr(grc_agent_gui.main_window, "_default_sessions_db", lambda: db_path)

    SessionStore._instance = None

    mock_agent = MagicMock()
    mock_agent.session = None
    mock_agent.history = []
    mock_provider = MagicMock()
    
    window = MainWindow(mock_agent, mock_provider)
    qtbot.addWidget(window)

    monkeypatch.setattr(window, "open_file", MagicMock())
    monkeypatch.setattr(window, "start_generation", MagicMock())

    # Create a session and write messages
    session_id = window.sessions_store.open_session(
        graph_path="",
        graph_hash="",
        model_alias="modelA",
        backend="llama_cpp",
        title="Session Resumption Test",
    )
    window.sessions_store.append(session_id, "user", "Hello agent")
    window.sessions_store.append(session_id, "assistant", "Hello user")
    window.sessions_store.flush(timeout=2.0)

    # Open/resume the past session
    window._open_past_session(session_id)

    # 1. Verify that active_session_id is set correctly (resumed)
    assert window.active_session_id == session_id

    # 2. Verify agent history is reconstructed
    assert len(window.agent.history) == 2
    assert window.agent.history[0] == {"role": "user", "content": "Hello agent"}
    assert window.agent.history[1] == {"role": "assistant", "content": "Hello user"}

    # 3. Send a new message and verify it appends to the same session
    window.chat_input.setText("How are you?")
    window.send_prompt()
    window.sessions_store.flush(timeout=2.0)

    # Confirm active_session_id did not change
    assert window.active_session_id == session_id

    # Check database messages count is 3 now
    messages = window.sessions_store._writer_conn.execute(
        "SELECT role, text FROM messages WHERE session_id = ?", (session_id,)
    ).fetchall()
    assert len(messages) == 3
    assert messages[2][0] == "user"
    assert messages[2][1] == "How are you?"

    # Explicit teardown
    window.sessions_store.close()
    SessionStore._instance = None
