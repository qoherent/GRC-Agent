import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

from PySide6.QtCore import Qt

# Add src to system path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../src")))

from grc_agent.sessions_store import SessionRecord, SessionStore
from grc_agent_gui.main_window import MainWindow
from grc_agent_gui.sidebar_widget import SidebarWidget


def _make_session(id: int = 1, **overrides) -> SessionRecord:
    base = dict(
        id=id,
        graph_path="/tmp/example.grc",
        graph_hash="grc:abc",
        started_at="2026-06-01T00:00:00.000000Z",
        ended_at=None,
        model_alias="test-model",
        backend="ollama",
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


def test_sidebar_item_text_omits_msgs_count(qtbot):
    """Recent-chats row must show only title + date — the ``msgs=N`` count
    was a noise token (it duplicated the chat widget's own indicator and
    pushed the date off-screen in long lists)."""
    widget = SidebarWidget()
    qtbot.addWidget(widget)

    sessions = [_make_session(id=1, title="Alpha", message_count=3)]
    widget.populate_sessions(sessions)
    text = widget.list_widget.item(0).text()
    assert "msgs=" not in text
    assert "Alpha" in text
    # The date still appears, just without the msgs=N suffix.
    assert "2026-06-01" in text


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


def test_sidebar_clear_all_emits_signal(qtbot):
    """The "Clear all history" button must emit ``clear_all_requested``
    so the main window can wipe the sessions DB and confirm with the
    user."""
    widget = SidebarWidget()
    qtbot.addWidget(widget)
    assert hasattr(widget, "clear_all_btn"), "SidebarWidget must expose a clear_all button"
    assert hasattr(widget, "clear_all_requested"), "SidebarWidget must expose the clear_all_requested signal"

    emitted = False

    def on_clear():
        nonlocal emitted
        emitted = True

    widget.clear_all_requested.connect(on_clear)
    widget.clear_all_btn.click()
    assert emitted


def test_main_window_clear_all_wipes_sessions_db(qtbot, tmp_path, monkeypatch):
    """Clicking the sidebar's Clear all button must (a) prompt the
    user, (b) wipe every row from the sessions DB, and (c) refresh
    the sidebar list."""
    db_path = tmp_path / "sessions.db"
    import grc_agent_gui.main_window

    monkeypatch.setattr(grc_agent_gui.main_window, "_default_sessions_db", lambda: db_path)

    SessionStore._instance = None

    mock_agent = MagicMock()
    mock_agent.session = None
    mock_provider = MagicMock()

    window = MainWindow(mock_agent, mock_provider)
    qtbot.addWidget(window)

    # Seed two sessions so the list is non-empty.
    for i in range(2):
        sid = window.sessions_store.open_session(
            graph_path="",
            graph_hash="",
            model_alias="m",
            backend="ollama",
            title=f"S{i}",
        )
        window.sessions_store.append(sid, "user", "hi")
    window.sessions_store.flush(timeout=2.0)

    # Bypass the user-confirmation dialog.
    monkeypatch.setattr(
        "grc_agent_gui.main_window.QMessageBox.question",
        lambda *a, **_kw: __import__("PySide6").QtWidgets.QMessageBox.StandardButton.Yes,
    )

    # Trigger the same handler the button click would.
    window._on_clear_all_history()

    # The DB is empty.
    rows = window.sessions_store._writer_conn.execute(
        "SELECT count(*) FROM sessions"
    ).fetchone()[0]
    assert rows == 0
    # The sidebar list is empty.
    assert window.sidebar_widget.list_widget.count() == 0
    # The active session is reset.
    assert window.active_session_id is None

    window.sessions_store.close()
    SessionStore._instance = None


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
    sessions = window.sessions_store._writer_conn.execute(
        "SELECT id, title FROM sessions"
    ).fetchall()
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
        backend="ollama",
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
    """Resume replays the typed ``assistant_model`` / ``tool_model``
    rows into ``agent.chat_history`` and the display rows into the
    chat widget. After resume, the next user prompt appends to the
    same session.
    """
    import datetime

    db_path = tmp_path / "sessions.db"
    import grc_agent_gui.main_window

    monkeypatch.setattr(grc_agent_gui.main_window, "_default_sessions_db", lambda: db_path)

    SessionStore._instance = None

    mock_agent = MagicMock()
    mock_agent.session = None
    from ToolAgents.data_models.chat_history import ChatHistory
    from ToolAgents.data_models.messages import (
        ChatMessage,
        ChatMessageRole,
        TextContent,
        ToolCallContent,
        ToolCallResultContent,
    )

    mock_agent.chat_history = ChatHistory()
    mock_provider = MagicMock()

    window = MainWindow(mock_agent, mock_provider)
    qtbot.addWidget(window)

    monkeypatch.setattr(window, "open_file", MagicMock())
    monkeypatch.setattr(window, "start_generation", MagicMock())

    # Create a session and write only typed *_model rows (SSOT).
    session_id = window.sessions_store.open_session(
        graph_path="",
        graph_hash="",
        model_alias="modelA",
        backend="ollama",
        title="Session Resumption Test",
    )
    now = datetime.datetime.now()
    user_msg = ChatMessage(
        id="u-1",
        role=ChatMessageRole.User,
        content=[TextContent(content="Hello agent")],
        created_at=now,
        updated_at=now,
    )
    asst_msg = ChatMessage(
        id="a-1",
        role=ChatMessageRole.Assistant,
        content=[
            ToolCallContent(
                tool_call_id="c-1",
                tool_call_name="inspect_graph",
                tool_call_arguments={"view": "overview"},
            )
        ],
        created_at=now,
        updated_at=now,
    )
    tool_msg = ChatMessage(
        id="t-1",
        role=ChatMessageRole.Tool,
        content=[
            ToolCallResultContent(
                tool_call_result_id="r-1",
                tool_call_id="c-1",
                tool_call_name="inspect_graph",
                tool_call_result='{"ok": true, "blocks": 3}',
            )
        ],
        created_at=now,
        updated_at=now,
    )
    window.sessions_store.append(
        session_id, "user_model", "", payload=user_msg.model_dump(mode="json")
    )
    window.sessions_store.append(
        session_id, "assistant_model", "", payload=asst_msg.model_dump(mode="json")
    )
    window.sessions_store.append(
        session_id, "tool_model", "", payload=tool_msg.model_dump(mode="json")
    )
    window.sessions_store.flush(timeout=2.0)

    # Open/resume the past session
    window._open_past_session(session_id)

    # 1. active_session_id is set to the resumed session.
    assert window.active_session_id == session_id

    # 2. The typed chat history holds the 3 model rows in order.
    assert mock_agent.chat_history.get_message_count() == 3
    roles = [m.role for m in mock_agent.chat_history.get_messages()]
    assert roles == [
        ChatMessageRole.User,
        ChatMessageRole.Assistant,
        ChatMessageRole.Tool,
    ]
    # The tool call survives round-trip.
    assert (
        mock_agent.chat_history.get_messages()[1].get_tool_calls()[0].tool_call_name
        == "inspect_graph"
    )

    # 3. The chat widget derives display from model rows — user and
    #    assistant entries present, AND tool-call fragment with result.
    widget_history = window.chat_widget.get_history()
    widget_roles = [row["role"] for row in widget_history]
    assert "user" in widget_roles
    assert "assistant" in widget_roles
    # The assistant entry must contain a tool fragment whose result is filled.
    assistant_entries = [r for r in widget_history if r["role"] == "assistant"]
    tool_frags = [
        f for entry in assistant_entries
        for f in entry.get("fragments", [])
        if f.get("type") == "tool"
    ]
    assert len(tool_frags) == 1, "tool fragment must be visible after resume"
    assert tool_frags[0]["name"] == "inspect_graph"
    assert tool_frags[0]["result"] is not None, "tool result must be filled"

    # 4. Send a new message and verify the session is still active.
    #    (start_generation is mocked, so no runtime user_model event
    #    fires; the display write was removed by the SSOT consolidation.)
    window.chat_input.setText("How are you?")
    window.send_prompt()

    assert window.active_session_id == session_id

    # 5. Database is unchanged — the 3 original model rows.
    messages = window.sessions_store._writer_conn.execute(
        "SELECT role FROM messages WHERE session_id = ? ORDER BY sequence",
        (session_id,),
    ).fetchall()
    assert len(messages) == 3

    # Explicit teardown
    window.sessions_store.close()
    SessionStore._instance = None


def test_open_legacy_session_refuses_to_resume(qtbot, tmp_path, monkeypatch):
    """Sessions written before ``assistant_model`` / ``tool_model``
    rows existed have no typed history to resume. The resume path
    must refuse, not synthesize a legacy fallback. AGENTS.md
    forbids backward-compat shims.
    """
    db_path = tmp_path / "sessions.db"
    import grc_agent_gui.main_window

    monkeypatch.setattr(grc_agent_gui.main_window, "_default_sessions_db", lambda: db_path)

    SessionStore._instance = None

    from ToolAgents.data_models.chat_history import ChatHistory

    mock_agent = MagicMock()
    mock_agent.session = None
    mock_agent.chat_history = ChatHistory()
    mock_provider = MagicMock()

    window = MainWindow(mock_agent, mock_provider)
    qtbot.addWidget(window)
    monkeypatch.setattr(window, "open_file", MagicMock())

    # A "legacy" session: only display rows, no model rows.
    session_id = window.sessions_store.open_session(
        graph_path="",
        graph_hash="",
        model_alias="modelA",
        backend="ollama",
        title="Legacy Session",
    )
    window.sessions_store.append(session_id, "user", "Old prompt")
    window.sessions_store.append(session_id, "assistant", "Old reply")
    window.sessions_store.flush(timeout=2.0)

    window._open_past_session(session_id)

    # The resume path refused — no fallback synthesis.
    assert window.active_session_id is None
    assert mock_agent.chat_history.get_message_count() == 0

    # Explicit teardown
    window.sessions_store.close()
    SessionStore._instance = None
