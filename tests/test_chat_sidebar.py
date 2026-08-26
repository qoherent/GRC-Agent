"""Unit tests for chat_sidebar — split from the former test_unit.py god file.

Minimal set per the clustered test plan; shared fixtures/helpers live in conftest.py.
"""

import asyncio
import os

from conftest import _count_sessions_for_path, _seed_session


def test_change_summary_formatter():
    """The approval card's uniform change_graph-JSON → Markdown formatter."""
    from grc_agent.ui.approval_card import format_change_summary

    text = format_change_summary(
        {
            "add_blocks": [
                {
                    "name": "lpf_0",
                    "block_id": "filter_low_pass_filter_x",
                    "params": {"cutoff": "19e3"},
                }
            ],
            "add_connections": ["src:0->lpf_0:0"],
            "force": True,
        }
    )
    assert "**Add blocks:**" in text and "`lpf_0` (`filter_low_pass_filter_x`)" in text
    assert "src:0 → lpf_0:0" in text  # cosmetic arrow
    assert "force" in text and "bypasses" in text

    text = format_change_summary(
        {"update_params": [{"name": "samp_rate", "param": "value", "value": "48000"}]}
    )
    assert "`samp_rate.value` = `48000`" in text
    assert format_change_summary({}) == "_No changes in this batch._"


def test_approval_mode_settings_helpers(tmp_path, monkeypatch):
    """The flowgraph-change gate persists via .env like the other settings."""
    from grc_agent.settings import get_approval_mode, set_approval_mode

    env_file = tmp_path / ".env"
    env_file.write_text("")
    monkeypatch.setenv("GRC_AGENT_ENV", str(env_file))
    assert get_approval_mode() == "ask"  # default: gate on
    set_approval_mode("always")
    assert get_approval_mode() == "always"
    set_approval_mode("bogus")
    assert get_approval_mode() == "always"  # invalid values are ignored
    set_approval_mode("ask")
    assert get_approval_mode() == "ask"


def test_chat_sidebar_copy_and_rich_rendering():
    from gi.repository import Gdk, Gtk
    from pydantic_ai.messages import ModelResponse, TextPart, ThinkingPart, ToolCallPart

    from grc_agent.chat_sidebar import ChatSidebar

    sidebar = ChatSidebar()

    # 1. Test copy button text update during streaming
    box = sidebar._start_agent_message()
    sidebar._update_copy_text(box, "test copy text")
    parent = box.get_parent()
    assert parent is not None
    copy_btn = getattr(box, "_grc_copy_btn", getattr(parent, "_grc_copy_btn", None))
    assert copy_btn is not None
    assert copy_btn._grc_copy_text == "test copy text"
    assert copy_btn.get_tooltip_text() == "Copy message"
    copy_btn.clicked()
    assert Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).wait_for_text() == "test copy text"
    assert copy_btn.get_tooltip_text() == "Copied!"

    # 2. Test horizontal-scrolling table rendering
    sidebar._render_markdown_to_box(box, "| Head |\n|---|\n| cell |")
    children = box.get_children()
    assert any(isinstance(c, Gtk.ScrolledWindow) for c in children)

    # 3. Test last message rich rendering maps thinking, text, and tools
    msg = ModelResponse(
        parts=[
            ThinkingPart(content="think progress"),
            TextPart(content="here is a table:\n| A | B |\n|---|---|\n| 1 | 2 |"),
            ToolCallPart(tool_name="inspect_graph", args={}, tool_call_id="call_test"),
        ]
    )
    sidebar._render_last_message_rich(box, msg)
    new_children = box.get_children()

    # Verify we have Gtk.Expander for thinking/tools and a table (TableBlock, a
    # Gtk.ScrolledWindow subclass) for the markdown table.
    exp_classes = [c.__class__.__name__ for c in new_children]
    assert "Expander" in exp_classes
    assert any(isinstance(c, Gtk.ScrolledWindow) for c in new_children)


def test_open_recent_session_tab_switching(tmp_path):
    from unittest.mock import MagicMock, patch

    from grc_agent.chat_sidebar import ChatSidebar

    sidebar = ChatSidebar()

    # Mock flowgraph proxy, canvas manager, and GRC window/notebook
    proxy = MagicMock()
    cm = MagicMock()
    window = MagicMock()
    notebook = MagicMock()

    proxy._canvas_manager = cm
    cm.window = window
    window.notebook = notebook

    sidebar.set_flowgraph_proxy(proxy)

    # Prepare files
    file_real = tmp_path / "target.grc"
    file_real.touch()

    # Case 1: Page has relative file path, target is absolute
    page1 = MagicMock()
    page1.file_path = "target.grc"
    notebook.get_n_pages.return_value = 1
    notebook.get_nth_page.return_value = page1

    orig_cwd = os.getcwd()
    os.chdir(str(tmp_path))
    try:
        with patch("grc_agent.chat_sidebar.load_session") as mock_load:
            mock_load.return_value = {
                "id": 123,
                "grc_file_path": str(file_real.resolve()),
                "messages": "[]",
                "created_at": "...",
                "updated_at": "...",
            }
            sidebar._on_recent_session_clicked(123)
    finally:
        os.chdir(orig_cwd)

    notebook.set_current_page.assert_called_once_with(0)


def test_sidebar_session_no_autoload_on_graph_open(tmp_path, monkeypatch):
    """Graphs never auto-load chats. Opening/switching a graph tab clears the
    chat area to a fresh welcome screen — no session lookup by path. The only
    way to load a saved conversation is explicitly from the recent-sessions
    list (which opens the graph AND loads the session via _loading_session_id)."""
    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))

    from grc_agent.chat_sidebar import ChatSidebar
    from grc_agent.db import save_session

    sidebar = ChatSidebar()

    # Prepare files and sessions
    f1 = tmp_path / "flow1.grc"
    f1.touch()
    f2 = tmp_path / "flow2.grc"
    f2.touch()

    # Even though sessions exist for these paths, sync_to_file must NOT load them
    save_session(None, str(f1), [])
    save_session(None, str(f2), [])

    class DummyPage:
        def __init__(self, file_path):
            self.file_path = file_path

    sidebar._flowgraph_proxy = object()
    current_page_val = DummyPage(str(f1))
    monkeypatch.setattr(ChatSidebar, "current_page", property(lambda _self: current_page_val))

    # Switching to flow1.grc — no session loads, chat is blank
    sidebar.sync_to_file()
    assert sidebar._active_session_id is None
    assert sidebar._message_history == []

    # Switching to flow2.grc — same: no autoload
    current_page_val = DummyPage(str(f2))
    sidebar.sync_to_file()
    assert sidebar._active_session_id is None
    assert sidebar._message_history == []

    # Switching back to flow1.grc — still blank, no per-page binding
    current_page_val = DummyPage(str(f1))
    sidebar.sync_to_file()
    assert sidebar._active_session_id is None
    assert sidebar._message_history == []


def test_active_graph_tracking():
    from grc_agent.chat_sidebar import ChatSidebar

    sidebar = ChatSidebar()

    # Initial state
    assert sidebar._active_graph_name is None
    assert sidebar._active_graph_path is None

    # Set active graph with path
    sidebar.set_active_graph("my_cool_flowgraph", "/path/to/my_cool_flowgraph.grc")
    assert sidebar._active_graph_name == "my_cool_flowgraph"
    assert sidebar._active_graph_path == "/path/to/my_cool_flowgraph.grc"

    # Clear active graph
    sidebar.set_active_graph(None)
    assert sidebar._active_graph_name is None
    assert sidebar._active_graph_path is None


def test_ui_micro_interactions_and_shortcuts():
    from gi.repository import Gdk

    from grc_agent.chat_sidebar import ChatSidebar

    sidebar = ChatSidebar()

    # 1. Focus management & Sensitivity
    assert not sidebar.grab_entry_focus()
    sidebar.set_input_enabled(True)
    assert sidebar._entry.get_sensitive()
    assert sidebar._entry.get_min_content_height() == 64

    # 2. Active Provider badge tooltips
    sidebar.set_active_provider(
        "openrouter",
        "deepseek/deepseek-v4-flash",
        is_default=False,
        base_url="https://openrouter.ai/api/v1",
    )
    assert sidebar._provider_label.get_text() == "OpenRouter · deepseek-v4-flash"
    tooltip = sidebar._provider_label.get_tooltip_text()
    assert "OpenRouter" in tooltip
    assert "https://openrouter.ai/api/v1" in tooltip
    assert "Configured provider active" in tooltip

    # 3. Esc key handling (clears text)
    sidebar._entry.set_text("Hello world")
    esc_event = Gdk.EventKey()
    esc_event.keyval = Gdk.KEY_Escape
    handled = sidebar._on_entry_key_press(sidebar._entry, esc_event)
    assert handled
    assert sidebar._entry.get_text() == ""

    # 4. Shift+Enter handling (inserts newline)
    sidebar._entry.set_text("Line 1")
    sidebar._entry.set_position(6)
    shift_enter_event = Gdk.EventKey()
    shift_enter_event.keyval = Gdk.KEY_Return
    shift_enter_event.state = Gdk.ModifierType.SHIFT_MASK
    handled = sidebar._on_entry_key_press(sidebar._entry, shift_enter_event)
    assert handled
    assert sidebar._entry.get_text() == "Line 1\n"

    # 5. Ctrl+, opens settings (regression: the handler used the nonexistent
    # Gdk.KEY_Comma constant — AttributeError on EVERY keypress). The tuple
    # is evaluated per event, so any key must reach the handler unharmed.
    any_event = Gdk.EventKey()
    any_event.keyval = Gdk.KEY_t
    assert sidebar._on_key_press_event(sidebar, any_event) is False

    sidebar._open_dialog = None
    settings_event = Gdk.EventKey()
    settings_event.keyval = Gdk.KEY_comma
    settings_event.state = Gdk.ModifierType.CONTROL_MASK
    assert sidebar._on_key_press_event(sidebar, settings_event) is True
    assert sidebar._open_dialog is not None
    sidebar._open_dialog.destroy()
    sidebar._open_dialog = None


def test_delete_recent_session_ui(monkeypatch):
    """Per-row conversation delete requires confirmation (web-UI sidebar
    parity): YES deletes + re-renders; CANCEL does nothing."""
    from unittest.mock import MagicMock

    from gi.repository import Gtk

    from grc_agent.chat_sidebar import ChatSidebar

    sidebar = ChatSidebar()
    sidebar._render_history = MagicMock()

    mock_delete = MagicMock()
    monkeypatch.setattr("grc_agent.chat_sidebar.delete_session", mock_delete)

    mock_dialog = MagicMock()
    monkeypatch.setattr(Gtk, "MessageDialog", MagicMock(return_value=mock_dialog))

    def confirm(response: int) -> None:
        mock_dialog.connect.reset_mock()
        sidebar._on_delete_recent_session(123)
        handler = mock_dialog.connect.call_args.args[1]
        handler(mock_dialog, response)

    # CANCEL: no delete, no re-render
    confirm(Gtk.ResponseType.CANCEL)
    mock_delete.assert_not_called()
    sidebar._render_history.assert_not_called()

    # YES: delete once + re-render
    confirm(Gtk.ResponseType.YES)
    mock_delete.assert_called_once_with(123)
    sidebar._render_history.assert_called_once()


def test_clear_history_deletes_active_session_real_db(tmp_path, monkeypatch):
    """UI-2 regression: 'Clear History' must actually DELETE the persisted DB
    row for the active session, not just blank in-memory state. Uses a real
    temp SQLite DB (via GRC_AGENT_ENV isolation) instead of mocking
    delete_session — the original mocked test could not catch this bug."""
    from gi.repository import Gtk

    from grc_agent.chat_sidebar import ChatSidebar

    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))
    grc = tmp_path / "flow.grc"
    grc.write_text("# grc")

    sid = _seed_session(str(grc))
    assert _count_sessions_for_path(str(grc)) == 1

    sidebar = ChatSidebar()
    sidebar._active_session_id = sid
    # No flowgraph proxy → path is None → must fall back to deleting by sid.
    sidebar._flowgraph_proxy = None

    sidebar._on_clear_history_clicked(None)
    assert sidebar._open_dialog is not None
    sidebar._open_dialog.emit("response", Gtk.ResponseType.YES)

    assert _count_sessions_for_path(str(grc)) == 0
    assert sidebar._active_session_id is None


def test_clear_history_deletes_all_sessions_for_path_real_db(tmp_path, monkeypatch):
    """Regression for the exact bug seen in production: multiple sessions
    accumulated for the SAME flowgraph path despite repeated 'Clear History'
    clicks. Deleting by path must remove every row for that file."""
    from unittest.mock import MagicMock

    from gi.repository import Gtk

    from grc_agent.chat_sidebar import ChatSidebar

    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))
    grc = tmp_path / "fm_rx.grc"
    grc.write_text("# grc")

    # Seed THREE sessions for the same path — mirrors the real DB state.
    ids = [_seed_session(str(grc)) for _ in range(3)]
    assert _count_sessions_for_path(str(grc)) == 3

    sidebar = ChatSidebar()
    sidebar._active_session_id = ids[-1]
    proxy = MagicMock()
    cm = MagicMock()
    cm.path = str(grc)
    proxy._canvas_manager = cm
    sidebar._flowgraph_proxy = proxy

    sidebar._on_clear_history_clicked(None)
    sidebar._open_dialog.emit("response", Gtk.ResponseType.YES)

    assert _count_sessions_for_path(str(grc)) == 0
    assert sidebar._active_session_id is None


def test_clear_history_deletes_all_sessions_no_active_flowgraph(tmp_path, monkeypatch):
    """Regression for the user-reported bug: with NO flowgraph open (path=None)
    and NO active session (sid=None) — i.e. sitting on the welcome screen
    looking at the recent-sessions list — Clear History must still delete every
    visible session. The old per-flowgraph logic (delete-by-path elif
    delete-by-sid) deleted NOTHING in this case, which is exactly when the user
    is staring at the list. Clear History is now global."""
    from gi.repository import Gtk

    from grc_agent.chat_sidebar import ChatSidebar

    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))
    grc_a = tmp_path / "fm_rx.grc"
    grc_b = tmp_path / "demo_qam.grc"
    grc_a.write_text("#")
    grc_b.write_text("#")

    # Sessions across MULTIPLE flowgraphs (the visible recent-sessions list).
    _seed_session(str(grc_a))
    _seed_session(str(grc_a))
    _seed_session(str(grc_b))

    def total():
        return _count_sessions_for_path(str(grc_a)) + _count_sessions_for_path(str(grc_b))

    assert total() == 3

    sidebar = ChatSidebar()
    # No proxy (path will be None) and no active session (sid None) — the exact
    # state where the old per-flowgraph logic deleted nothing.
    sidebar._flowgraph_proxy = None
    sidebar._active_session_id = None

    sidebar._on_clear_history_clicked(None)
    sidebar._open_dialog.emit("response", Gtk.ResponseType.YES)

    assert total() == 0
    assert sidebar._active_session_id is None


def test_clear_history_dialog_survives_gc_and_responds(tmp_path, monkeypatch):
    """Regression: a non-blocking dialog shown via .show() must be anchored on
    self, otherwise PyGObject garbage-collects the toplevel once the
    constructing method returns and the 'response' signal never fires."""
    import gc
    from unittest.mock import MagicMock

    from gi.repository import Gtk

    from grc_agent.chat_sidebar import ChatSidebar

    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))

    sidebar = ChatSidebar()
    sidebar.clear_messages = MagicMock()
    sidebar.set_status = MagicMock()

    sidebar._on_clear_history_clicked(None)

    assert sidebar._open_dialog is not None
    gc.collect()
    assert sidebar._open_dialog is not None

    sidebar._open_dialog.emit("response", Gtk.ResponseType.YES)

    sidebar.clear_messages.assert_called_once()
    assert sidebar._open_dialog is None


def test_settings_dialog_persists_model_name(tmp_path, monkeypatch):
    """Regression: the Settings dialog must read widget values BEFORE
    gtk_widget_destroy(). Reading after destroy returns '' / -1, which silently
    skipped save_settings so the model name never persisted (and would have
    wiped the API key). Exercises the real _open_settings() dialog tree, not a
    mock."""
    from gi.repository import Gtk

    from grc_agent.chat_sidebar import ChatSidebar
    from grc_agent.settings import load_settings, save_settings

    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))
    save_settings("ollama_local", "old-model-name")  # known starting model

    sidebar = ChatSidebar()
    sidebar._open_settings()
    dlg = sidebar._open_dialog
    assert dlg is not None

    # Locate the Model entry. Gtk.Grid.get_children() order isn't row order, so
    # identify it by content: on dialog open it holds the current model name
    # (the API-key entry is empty/insensitive for the keyless ollama provider).
    entries: list[Gtk.Entry] = []

    def walk(w):
        if isinstance(w, Gtk.Entry):
            entries.append(w)
        if isinstance(w, Gtk.Container):
            for c in w.get_children():
                walk(c)

    walk(dlg)
    assert entries, "no Gtk.Entry found in settings dialog"
    model_entry = next(e for e in entries if e.get_text() == "old-model-name")
    model_entry.set_text("brand-new-model-name")

    monkeypatch.setattr("grc_agent.agent_factory.probe_backend", lambda *_a, **_kw: (None, None))
    dlg.emit("response", Gtk.ResponseType.APPLY)

    # With the read-after-destroy bug this stayed "old-model-name".
    assert load_settings()["model"] == "brand-new-model-name"


def test_settings_dialog_persists_api_key(tmp_path, monkeypatch):
    """The API-key field must also be read BEFORE destroy() (the read-after-
    destroy bug would have wiped it with ''). Covers the keyless-provider gap
    left by test_settings_dialog_persists_model_name (which uses ollama)."""
    from gi.repository import Gtk

    from grc_agent.chat_sidebar import ChatSidebar
    from grc_agent.settings import get_env_value, save_settings

    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))
    save_settings("openai_compatible", "liquid/lfm-2.5-2.6b:free")

    sidebar = ChatSidebar()
    sidebar._open_settings()
    dlg = sidebar._open_dialog
    assert dlg is not None

    # Find the API-key Gtk.Entry (visibility=False distinguishes it from the
    # model Entry, which is the only other Entry and has default visibility).
    entries: list[Gtk.Entry] = []

    def walk(w):
        if isinstance(w, Gtk.Entry):
            entries.append(w)
        if isinstance(w, Gtk.Container):
            for c in w.get_children():
                walk(c)

    walk(dlg)
    key_entry = next(e for e in entries if e.get_visibility() is False)
    key_entry.set_text("sk-test-persists-123")

    monkeypatch.setattr("grc_agent.agent_factory.probe_backend", lambda *_a, **_kw: (None, None))
    dlg.emit("response", Gtk.ResponseType.APPLY)

    assert get_env_value("OPENAI_COMPATIBLE_API_KEY") == "sk-test-persists-123"


def test_settings_dialog_reports_save_failure(tmp_path, monkeypatch):
    """Regression: a failed save_settings()/upsert_env_key() call (e.g. a
    read-only home dir, disk full) must be caught and reported via the status
    bar, matching the existing pattern in _on_clear_history_clicked — not
    close the dialog and silently pretend nothing happened."""
    from gi.repository import Gtk

    from grc_agent.chat_sidebar import ChatSidebar
    from grc_agent.settings import save_settings

    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))
    save_settings("ollama_local", "old-model-name")

    sidebar = ChatSidebar()
    sidebar._open_settings()
    dlg = sidebar._open_dialog
    assert dlg is not None

    def _boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("grc_agent.chat_sidebar.save_settings", _boom)

    dlg.emit("response", Gtk.ResponseType.APPLY)

    assert "not saved" in sidebar._status_label.get_text().lower()
    assert "validation-invalid" in sidebar._status_label.get_style_context().list_classes()


def test_settings_dialog_extended_fields(tmp_path, monkeypatch):
    """Test Settings Dialog includes Base URL entry, saving it properly."""
    from gi.repository import Gtk

    from grc_agent.chat_sidebar import ChatSidebar
    from grc_agent.settings import load_settings, save_settings

    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))
    save_settings("ollama_local", "old-model", ollama_base_url="http://localhost:11434")

    sidebar = ChatSidebar()
    sidebar._open_settings()
    dlg = sidebar._open_dialog
    assert dlg is not None

    entries: list[Gtk.Entry] = []

    def walk(w):
        if isinstance(w, Gtk.Entry):
            entries.append(w)
        if isinstance(w, Gtk.Container):
            for c in w.get_children():
                walk(c)

    walk(dlg)

    url_entry = next(e for e in entries if e.get_text() == "http://localhost:11434")
    url_entry.set_text("http://10.0.0.5:11434")

    # Bypass preflight reachability check for 10.0.0.5
    monkeypatch.setattr("grc_agent.agent_factory.probe_backend", lambda *_a, **_kw: (None, None))

    dlg.emit("response", Gtk.ResponseType.APPLY)

    persisted = load_settings()
    assert persisted["ollama_base_url"] == "http://10.0.0.5:11434"


def test_settings_dialog_switch_to_ollama_cloud(tmp_path, monkeypatch):
    """Regression (reported): switching to Ollama Cloud and saving must
    persist the concrete ollama_cloud provider. With the split, selecting
    the provider shows the fixed https://ollama.com/v1 endpoint read-only —
    there is no cloud checkbox to misconfigure and no editable URL to
    silently keep pointing at localhost."""
    from gi.repository import Gtk

    from grc_agent.chat_sidebar import ChatSidebar
    from grc_agent.settings import load_settings, save_settings
    from grc_agent.ui.providers import PROVIDER_ORDER

    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))
    save_settings(
        "ollama_local", "qwen3.6:35b-a3b-q4_K_M", ollama_base_url="http://localhost:11434"
    )

    sidebar = ChatSidebar()
    sidebar._open_settings()
    dlg = sidebar._open_dialog
    assert dlg is not None

    # Select "Ollama Cloud" in the provider dropdown.
    dlg.provider_combo.set_active(PROVIDER_ORDER.index("ollama_cloud"))
    assert dlg.url_entry.get_text() == "https://ollama.com/v1"
    assert dlg.url_entry.get_sensitive() is False, "fixed endpoint must be read-only"

    # Bypass preflight reachability check
    monkeypatch.setattr("grc_agent.agent_factory.probe_backend", lambda *_a, **_kw: (None, None))

    dlg.emit("response", Gtk.ResponseType.APPLY)

    persisted = load_settings()
    assert persisted["provider"] == "ollama_cloud"
    # The fixed endpoint is canonical — no OLLAMA_BASE_URL line is written.
    assert persisted["ollama_base_url"] == "http://localhost:11434"


def test_settings_dialog_save_warns_on_unserved_model(tmp_path, monkeypatch):
    """Save-path guard: a model the backend does not list is a status-bar
    warning, never a blocking popup — the save proceeds and the warning text
    is visible in the sidebar's status bar."""

    from grc_agent.chat_sidebar import ChatSidebar
    from grc_agent.settings import load_settings, save_settings

    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))
    save_settings(
        "ollama_local", "qwen3.6:35b-a3b-q4_K_M", ollama_base_url="http://localhost:11434"
    )

    monkeypatch.setattr(
        "grc_agent.agent_factory.probe_backend",
        lambda *_a, **_k: (
            None,
            "Model 'typo-model' is not served by this backend (it lists 2 models).",
        ),
    )

    sidebar = ChatSidebar()
    sidebar._apply_settings_save(
        "ollama_local", "typo-model", "OLLAMA_API_KEY", "", "http://localhost:11434", "lexical"
    )
    # No popup: the save went through and the warning reached the status bar.
    assert load_settings()["model"] == "typo-model"
    assert "not served" in (sidebar._status_label.get_text() or "")


def test_streaming_text_flush_is_throttled(monkeypatch):
    """Streaming must NOT call Gtk.Label.set_text on every token (that re-runs
    Pango line-wrap layout over the whole growing message = O(n^2) and freezes
    the UI). _flush_streaming throttles to _STREAM_FLUSH_INTERVAL; force=True
    bypasses it. Time is mocked for determinism."""
    from gi.repository import Gtk

    from grc_agent.chat_sidebar import ChatSidebar, _ChunkAccumulator, _StreamCtx

    sidebar = ChatSidebar()
    ctx = _StreamCtx(Gtk.Box())
    sidebar._ensure_text(ctx)
    assert ctx.text_lbl is not None

    t = [0.0]
    monkeypatch.setattr("grc_agent.chat_sidebar.time.monotonic", lambda: t[0])

    # First flush at t=0 with last_flush=0.0 -> (0 - 0.0) < interval -> skip.
    ctx.text_acc = _ChunkAccumulator("chunk1")
    ctx.text_dirty = True
    sidebar._flush_streaming(ctx)
    assert (
        ctx.text_lbl.get_buffer().get_text(
            ctx.text_lbl.get_buffer().get_start_iter(),
            ctx.text_lbl.get_buffer().get_end_iter(),
            True,
        )
        == ""
    )  # throttled, not painted

    # Advance past the interval -> flush fires.
    t[0] = 0.05
    sidebar._flush_streaming(ctx)
    assert (
        ctx.text_lbl.get_buffer().get_text(
            ctx.text_lbl.get_buffer().get_start_iter(),
            ctx.text_lbl.get_buffer().get_end_iter(),
            True,
        )
        == "chunk1"
    )
    assert ctx.text_dirty is False

    # A second chunk in the same text part is append-only and throttled again.
    ctx.text_acc += "chunk2"
    ctx.text_dirty = True
    sidebar._flush_streaming(ctx)  # t=0.05, last_flush=0.05 -> skip
    assert (
        ctx.text_lbl.get_buffer().get_text(
            ctx.text_lbl.get_buffer().get_start_iter(),
            ctx.text_lbl.get_buffer().get_end_iter(),
            True,
        )
        == "chunk1"
    )

    # force=True bypasses the interval (used on part start/close/stream end).
    sidebar._flush_streaming(ctx, force=True)
    assert (
        ctx.text_lbl.get_buffer().get_text(
            ctx.text_lbl.get_buffer().get_start_iter(),
            ctx.text_lbl.get_buffer().get_end_iter(),
            True,
        )
        == "chunk1chunk2"
    )


def test_streaming_thinking_flush_throttled(monkeypatch):
    """Mirror of the text-flush test for the ThinkingPart branch: thinking
    tokens are throttled the same way and force=True flushes them."""
    from gi.repository import Gtk

    from grc_agent.chat_sidebar import ChatSidebar, _ChunkAccumulator, _StreamCtx

    sidebar = ChatSidebar()
    ctx = _StreamCtx(Gtk.Box())
    sidebar._ensure_thinking(ctx)
    assert ctx.think_body is not None
    ctx.think_expander.set_expanded(True)

    t = [0.0]
    monkeypatch.setattr("grc_agent.chat_sidebar.time.monotonic", lambda: t[0])

    ctx.think_acc = _ChunkAccumulator("thought1")
    ctx.think_dirty = True
    sidebar._flush_streaming(ctx)  # t=0, last_flush=0.0 -> throttled
    assert (
        ctx.think_body.get_buffer().get_text(
            ctx.think_body.get_buffer().get_start_iter(),
            ctx.think_body.get_buffer().get_end_iter(),
            True,
        )
        == ""
    )

    t[0] = 0.30
    sidebar._flush_streaming(ctx)
    assert (
        ctx.think_body.get_buffer().get_text(
            ctx.think_body.get_buffer().get_start_iter(),
            ctx.think_body.get_buffer().get_end_iter(),
            True,
        )
        == "thought1"
    )

    ctx.think_acc += "thought2"  # real streaming appends deltas, never replaces
    ctx.think_dirty = True
    sidebar._flush_streaming(ctx)  # immediately after -> throttled
    assert (
        ctx.think_body.get_buffer().get_text(
            ctx.think_body.get_buffer().get_start_iter(),
            ctx.think_body.get_buffer().get_end_iter(),
            True,
        )
        == "thought1"
    )
    sidebar._flush_streaming(ctx, force=True)
    # Buffers accumulate (delta-append, never full replace) — replacing the
    # whole buffer per flush would reset the thinking scroller's scroll
    # position mid-stream.
    assert (
        ctx.think_body.get_buffer().get_text(
            ctx.think_body.get_buffer().get_start_iter(),
            ctx.think_body.get_buffer().get_end_iter(),
            True,
        )
        == "thought1thought2"
    )


def test_thinking_expander_label_changes_on_close():
    """Thinking expander shows 'Thinking...' during streaming and changes to 'Thinked' when closed."""
    from gi.repository import Gtk

    from grc_agent.chat_sidebar import ChatSidebar, _StreamCtx

    sidebar = ChatSidebar()
    ctx = _StreamCtx(Gtk.Box())
    sidebar._ensure_thinking(ctx)
    exp = ctx.think_expander
    assert exp is not None
    assert exp.get_label() == "Thinking..."

    sidebar._close_thinking(ctx)
    assert exp.get_label() == "Thought"


def test_send_quick_prompt():
    """Quick action prompt chips call _send_quick_prompt which delegates to send_message."""
    from unittest.mock import MagicMock

    from grc_agent.chat_sidebar import ChatSidebar

    sidebar = ChatSidebar()
    sidebar.send_message = MagicMock()
    sidebar._flowgraph_proxy = MagicMock()
    sidebar._busy = False

    sidebar._send_quick_prompt("Inspect this graph")
    sidebar.send_message.assert_called_once_with("Inspect this graph")


def test_welcome_ui_stays_compact_with_long_recent_sessions(monkeypatch):
    """Quick prompts and long recent-session labels must not force a wide,
    sparse sidebar; metadata stays on one compact ellipsized line."""
    from unittest.mock import MagicMock

    from gi.repository import Gtk

    from grc_agent.chat_sidebar import ChatSidebar

    sessions = [
        {
            "id": i,
            "grc_file_path": f"/tmp/project/wideband_receiver_experiment_{i}.grc",
            "first_message": "Inspect this receiver and explain every stage in its RF pipeline",
            "updated_at": "2026-08-20 12:00:00",
        }
        for i in range(5)
    ]
    monkeypatch.setattr("grc_agent.ui.welcome_view.get_recent_sessions", lambda: sessions)

    sidebar = ChatSidebar()
    cm = MagicMock()
    cm.path = "/tmp/project/active.grc"
    cm.current_page = object()
    sidebar.set_flowgraph_proxy(MagicMock(_canvas_manager=cm))

    window = Gtk.Window()
    window.set_default_size(420, 760)
    window.add(sidebar)
    window.show_all()
    while Gtk.events_pending():
        Gtk.main_iteration()

    def descendants(widget):
        yield widget
        if isinstance(widget, Gtk.Container):
            for child in widget.get_children():
                yield from descendants(child)

    widgets = list(descendants(sidebar))
    quick_buttons = [
        widget
        for widget in widgets
        if isinstance(widget, Gtk.Button)
        and widget.get_style_context().has_class("chat-quick-prompt-btn")
    ]
    metadata_labels = [
        widget
        for widget in widgets
        if isinstance(widget, Gtk.Label)
        and widget.get_style_context().has_class("chat-recent-meta")
    ]

    assert [button.get_label() for button in quick_buttons] == [
        "🔍 Inspect",
        "⚡ Validate",
        "❓ Explain",
    ]
    assert len(metadata_labels) == 5
    assert all(label.get_ellipsize().value_nick == "end" for label in metadata_labels)
    assert window.get_allocated_width() <= 500
    window.destroy()


def test_poll_indexing_building_ready_failed_idle(monkeypatch):
    """_poll_indexing drives the status bar across the full state machine:
    idle (no-op), building (live progress, content-guarded), ready transition
    (notifies once, using the embedded `indexed` count not `total`), and failed
    (error). Per-domain so concurrent builds don't tangle."""
    import grc_agent.adapter as adapter_mod
    from grc_agent.chat_sidebar import ChatSidebar

    sidebar = ChatSidebar()
    calls: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        sidebar,
        "set_status",
        lambda msg, *, error=False, background=False: calls.append((msg, error)),  # noqa: ARG005
    )

    adapter_mod._rag_building.clear()
    try:
        # Idle: no domains -> no status writes.
        sidebar._poll_indexing()
        assert calls == []

        # Building: live progress shows counts.
        adapter_mod._rag_building["catalog"] = {
            "status": "building",
            "current": 3,
            "total": 10,
            "indexed": 0,
        }
        sidebar._poll_indexing()
        assert calls[-1] == ("Indexing block library for search\u2026 3/10", False)
        # Same progress -> suppressed (content guard).
        n = len(calls)
        sidebar._poll_indexing()
        assert len(calls) == n
        # Progress advances -> new message.
        adapter_mod._rag_building["catalog"]["current"] = 9
        sidebar._poll_indexing()
        assert calls[-1] == ("Indexing block library for search\u2026 9/10", False)

        # Transition to ready with indexed(8) < total(10): message uses indexed.
        adapter_mod._rag_building["catalog"] = {
            "status": "ready",
            "current": 10,
            "total": 10,
            "indexed": 8,
        }
        sidebar._poll_indexing()
        assert calls[-1] == ("Block library indexed \u2014 8 entries ready for search.", False)
        n = len(calls)
        sidebar._poll_indexing()  # still ready -> no re-notify
        assert len(calls) == n

        # A docs failure surfaces as an error status.
        adapter_mod._rag_building["docs"] = {
            "status": "failed",
            "current": 0,
            "total": 0,
            "indexed": 0,
        }
        sidebar._poll_indexing()
        assert calls[-1][1] is True
    finally:
        adapter_mod._rag_building.clear()


def test_run_agent_turn_error_preserves_user_message(tmp_path, monkeypatch):
    """UI-1 regression: an error mid-turn must NOT wipe the user's just-sent
    message (nor rebuild the widget, which would discard any partial reply)."""
    from unittest.mock import AsyncMock, MagicMock

    from grc_agent.chat_sidebar import ChatSidebar

    # Deterministic settings (a keyed provider in the real .env would
    # early-return before the error path this test exercises).
    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))
    sidebar = ChatSidebar()
    sidebar._render_history = MagicMock()
    sidebar._append_error = MagicMock()
    sidebar._set_busy = MagicMock()
    sidebar._scroll_to_bottom = MagicMock()
    sidebar._save_history = AsyncMock()
    sidebar._flowgraph_proxy = MagicMock()

    agent = MagicMock()
    agent.iter.side_effect = RuntimeError("boom")
    sidebar._agent = agent

    asyncio.run(sidebar._run_agent_turn("my question"))

    user_texts = [
        part.content
        for m in sidebar._message_history
        if m.__class__.__name__ == "ModelRequest"
        for part in m.parts
        if part.__class__.__name__ == "UserPromptPart"
    ]
    assert "my question" in user_texts
    sidebar._render_history.assert_not_called()


def test_append_error_aborted_style_uses_neutral_css_class():
    """A user-initiated Stop ("[aborted]") is not an error and must not be
    styled like one. _append_error's style="aborted" must apply the neutral
    chat-aborted-label CSS class instead of chat-error-label; the default
    (style="error", used by every other caller) must be unaffected."""
    from grc_agent.chat_sidebar import ChatSidebar

    sidebar = ChatSidebar()

    sidebar._append_error("Agent Error: boom")
    error_row = sidebar._listbox.get_children()[-1]
    error_lbl = error_row.get_child() if hasattr(error_row, "get_child") else error_row
    assert "chat-error-label" in error_lbl.get_style_context().list_classes()

    sidebar._append_error("[aborted]", style="aborted")
    aborted_row = sidebar._listbox.get_children()[-1]
    aborted_lbl = aborted_row.get_child() if hasattr(aborted_row, "get_child") else aborted_row
    classes = aborted_lbl.get_style_context().list_classes()
    assert "chat-aborted-label" in classes
    assert "chat-error-label" not in classes


def test_format_turn_error_covers_each_exception_type():
    """_run_agent_turn collapsed 4 near-duplicate except blocks (ModelHTTPError,
    UsageLimitExceeded, ModelAPIError, UnexpectedModelBehavior) plus the
    generic Exception fallback into one handler backed by this message
    builder. Each branch's message shape must survive the refactor exactly,
    including ModelHTTPError's extra status/body-vs-model_name distinction."""
    from pydantic_ai.exceptions import (
        ModelAPIError,
        ModelHTTPError,
        UnexpectedModelBehavior,
        UsageLimitExceeded,
    )

    from grc_agent.chat_sidebar import _format_turn_error

    assert (
        _format_turn_error(ModelHTTPError(500, "gpt-x", body="server exploded"))
        == "Model HTTP 500 Error: server exploded"
    )
    assert (
        _format_turn_error(
            ModelHTTPError(403, "gpt-x", body={"message": "Key limit exceeded", "code": 403})
        )
        == "Model HTTP 403 Error: Key limit exceeded"
    )
    assert _format_turn_error(ModelHTTPError(503, "gpt-x")) == "Model HTTP 503 Error from gpt-x"
    assert _format_turn_error(UsageLimitExceeded("too many tokens")).startswith(
        "Usage Limit Exceeded: too many tokens"
    )
    assert (
        _format_turn_error(ModelAPIError("gpt-x", "bad request")) == "Model API Error: bad request"
    )
    assert (
        _format_turn_error(UnexpectedModelBehavior("no tool call"))
        == "Unexpected Model Behavior: no tool call"
    )
    assert _format_turn_error(RuntimeError("boom")) == "Agent Error: boom"

    # Deep cause extraction from HTTPStatusError
    import httpx

    req = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    resp_json = httpx.Response(
        401, request=req, json={"error": {"message": "Invalid API key provided"}}
    )
    try:
        resp_json.raise_for_status()
    except Exception as c:
        try:
            raise ModelAPIError("gpt-5.6-sol", "Connection error.") from c
        except Exception as exc:
            assert (
                _format_turn_error(exc)
                == "Model API Error: Connection error. (Cause: Invalid API key provided)"
            )


def test_run_agent_turn_missing_api_key_shows_error(tmp_path, monkeypatch):
    """Missing API key for a configured cloud provider surfaces a clear chat error."""
    import asyncio

    from pydantic_ai import Agent
    from pydantic_ai.models.test import TestModel

    from grc_agent.chat_sidebar import ChatSidebar
    from grc_agent.settings import save_settings

    env_file = tmp_path / ".env"
    monkeypatch.setenv("GRC_AGENT_ENV", str(env_file))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    save_settings("anthropic", "claude-sonnet-5")

    sidebar = ChatSidebar()
    sidebar._agent = Agent(TestModel())
    sidebar._active_provider = "anthropic"

    asyncio.run(sidebar._run_agent_turn("hello"))

    # Verify error label added
    rows = sidebar._listbox.get_children()
    assert len(rows) >= 1
    row_text = rows[-1].get_child().get_text()
    assert "API key for Anthropic (Claude) (ANTHROPIC_API_KEY) is not set" in row_text


def test_send_message_guards_and_creates_session(tmp_path, monkeypatch):
    """M14 regression: send_message's blank-text/busy no-op guards and its
    session-creation branch had zero direct coverage — every other test that
    touches send_message replaces it with a MagicMock. This calls the real
    method (only the agent itself is stubbed, so no live model is needed)."""
    from unittest.mock import AsyncMock, MagicMock

    from grc_agent.chat_sidebar import ChatSidebar

    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))
    grc = tmp_path / "flow.grc"
    grc.write_text("# grc")

    sidebar = ChatSidebar()

    # (a) Blank/whitespace-only text is a no-op: no task started.
    assert sidebar.send_message("   ") is False
    assert sidebar._chat_task is None

    # (b) A call while a turn is already in flight (_busy) is a no-op.
    sidebar._busy = True
    assert sidebar.send_message("hello") is False
    assert sidebar._chat_task is None
    sidebar._busy = False

    # (c) A real call on a sidebar with a flowgraph_proxy set creates a new DB
    # session row on first send — inside the turn (off the unified loop), not
    # synchronously in send_message. A fast-failing agent stands in for the
    # live model.
    proxy = MagicMock()
    cm = MagicMock()
    cm.path = str(grc)
    proxy._canvas_manager = cm
    sidebar._flowgraph_proxy = proxy

    agent = MagicMock()
    agent.iter.side_effect = RuntimeError("boom")
    sidebar._agent = agent
    sidebar._save_history = AsyncMock()
    sidebar._render_history = MagicMock()
    sidebar._scroll_to_bottom = MagicMock()
    sidebar._update_context_label = MagicMock()

    assert sidebar._active_session_id is None

    async def _run():
        result = sidebar.send_message("hello agent")
        assert result is True
        await sidebar._chat_task

    asyncio.run(_run())

    assert sidebar._active_session_id is not None

    from grc_agent.db import get_recent_sessions

    sessions = get_recent_sessions()
    assert any(s["id"] == sidebar._active_session_id for s in sessions)


def test_planner_toggle_is_manual_and_reuses_current_session():
    """An empty chat only changes mode; a mid-session toggle visibly dispatches
    the planner handoff through the same send/history path."""
    from unittest.mock import MagicMock

    from pydantic_ai import Agent
    from pydantic_ai.messages import ModelRequest, UserPromptPart
    from pydantic_ai.models.test import TestModel

    from grc_agent.chat_sidebar import ChatSidebar

    sidebar = ChatSidebar()
    executor = Agent(TestModel(custom_output_text="executor"), output_type=str)
    planner = Agent(TestModel(custom_output_text="planner"), output_type=str)
    sidebar.set_agents(executor, planner)
    sidebar.send_message = MagicMock(return_value=True)

    sidebar._planner_toggle.set_active(True)
    assert sidebar._agent_mode == "planner"
    assert sidebar._agent is planner
    assert sidebar._planner_mode_label.get_text() == "Active:Planner"
    assert sidebar._planner_toggle.get_parent() is sidebar._context_label.get_parent()
    assert sidebar._compact_btn.get_parent() is sidebar._context_label.get_parent()
    sidebar.send_message.assert_not_called()

    sidebar._planner_toggle.set_active(False)
    assert sidebar._planner_mode_label.get_text() == "Active:Agent"
    sidebar._message_history = [ModelRequest(parts=[UserPromptPart(content="Build a receiver")])]
    sidebar._active_session_id = 17
    sidebar._planner_toggle.set_active(True)

    sidebar.send_message.assert_called_once_with(
        "Create or revise a complete plan for the current request. Do not execute it."
    )
    assert sidebar._active_session_id == 17


def test_planner_toggle_cannot_switch_while_busy():
    from pydantic_ai import Agent
    from pydantic_ai.models.test import TestModel

    from grc_agent.chat_sidebar import ChatSidebar

    sidebar = ChatSidebar()
    executor = Agent(TestModel(custom_output_text="executor"), output_type=str)
    planner = Agent(TestModel(custom_output_text="planner"), output_type=str)
    sidebar.set_agents(executor, planner)

    sidebar._set_busy(True)
    assert sidebar._planner_toggle.get_sensitive() is False
    sidebar._planner_toggle.set_active(True)

    assert sidebar._agent_mode == "executor"
    assert sidebar._agent is executor
    assert sidebar._planner_mode_label.get_text() == "Active:Agent"


def test_planner_write_shows_implement_action_and_click_runs_executor(tmp_path, monkeypatch):
    """A successful durable write_plan is the UI trigger; the user click then
    flips modes and dispatches one visible executor turn with shared history."""
    from unittest.mock import AsyncMock, MagicMock

    from pydantic_ai import Agent
    from pydantic_ai.models.test import TestModel
    from pydantic_ai_harness.planning import Planning

    from grc_agent.agent_factory import _plan_store_resolver
    from grc_agent.chat_sidebar import ChatSidebar
    from grc_agent.db import load_plan_items, save_session

    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))
    graph = tmp_path / "implement-plan.grc"
    graph.touch()
    session_id = save_session(None, str(graph), [])
    assert session_id is not None

    planner = Agent(
        TestModel(call_tools=["write_plan"]),
        output_type=str,
        capabilities=[
            Planning(
                store_resolver=_plan_store_resolver,
                tools=["write_plan", "read_plan"],
            )
        ],
    )
    executor = Agent(TestModel(custom_output_text="implemented"), output_type=str)
    sidebar = ChatSidebar()
    sidebar.set_agents(executor, planner)
    sidebar._agent_mode = "planner"
    sidebar._agent = planner
    sidebar._update_agent_mode_label()
    sidebar._active_session_id = session_id
    sidebar._flowgraph_proxy = MagicMock()
    sidebar._flowgraph_proxy._canvas_manager = None
    sidebar._save_history = AsyncMock()

    async def _run():
        await sidebar._run_agent_turn("Create the plan")
        assert await load_plan_items(session_id)
        button = sidebar._implement_plan_button
        assert button is not None
        assert button.get_label() == "Implement the Plan"
        assert button.get_sensitive() is True

        button.clicked()
        handoff_task = sidebar._implement_plan_task
        assert handoff_task is not None
        await handoff_task
        assert sidebar._chat_task is not None
        await sidebar._chat_task

    asyncio.run(_run())

    assert sidebar._agent_mode == "executor"
    assert sidebar._agent is executor
    assert sidebar._planner_toggle.get_active() is False
    assert sidebar._planner_mode_label.get_text() == "Active:Agent"
    assert sidebar._implement_plan_row is None
    assert any(
        part.__class__.__name__ == "UserPromptPart"
        and "Implement the approved plan now" in str(part.content)
        for message in sidebar._message_history
        for part in getattr(message, "parts", [])
    )


def test_planner_turn_persists_thinking_in_shared_session_history(tmp_path, monkeypatch):
    """Planner reasoning and reply use the same canonical session payload the
    executor uses, so dataset export does not lose the role's pre-compaction trace."""
    from pydantic_ai import Agent
    from pydantic_ai.messages import ModelResponse, TextPart, ThinkingPart
    from pydantic_ai.models.function import FunctionModel

    from grc_agent.chat_sidebar import ChatSidebar
    from grc_agent.db import deserialize_messages, load_session, save_session

    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))
    graph = tmp_path / "planner-history.grc"
    graph.touch()
    session_id = save_session(None, str(graph), [])
    assert session_id is not None

    planner = Agent(
        FunctionModel(
            lambda _messages, _info: ModelResponse(
                parts=[
                    ThinkingPart(content="planner-private-trace"),
                    TextPart(content="visible planner plan"),
                ]
            )
        ),
        output_type=str,
    )
    sidebar = ChatSidebar()
    sidebar._agent_mode = "planner"
    sidebar._agent = planner
    sidebar._active_session_id = session_id

    result = asyncio.run(planner.run("Make a plan"))
    sidebar._message_history = result.all_messages()
    monkeypatch.setattr(sidebar, "_get_effective_path", lambda: str(graph))
    asyncio.run(sidebar._save_history())

    row = load_session(session_id)
    assert row is not None
    history = deserialize_messages(row["messages"])
    assert any(
        isinstance(part, ThinkingPart) and part.content == "planner-private-trace"
        for message in history
        for part in getattr(message, "parts", [])
    )
    assert any(
        isinstance(part, TextPart) and part.content == "visible planner plan"
        for message in history
        for part in getattr(message, "parts", [])
    )


def test_collapsed_thinking_stream_does_not_force_flush_every_delta():
    """A thinking delta must not close/force-flush a nonexistent text part.

    This exact cross-part flush made a 65k-token reasoning loop consume one CPU
    core by inserting and laying out the growing thought on every delta.
    """
    from pydantic_ai.messages import (
        PartDeltaEvent,
        PartStartEvent,
        ThinkingPart,
        ThinkingPartDelta,
    )

    from grc_agent.chat_sidebar import ChatSidebar, _StreamCtx

    sidebar = ChatSidebar()
    sidebar._scroll_to_bottom = lambda *_args, **_kwargs: None
    ctx = _StreamCtx(sidebar._start_agent_message())
    sidebar._on_part_start(ctx, PartStartEvent(index=0, part=ThinkingPart(content="")))

    event = PartDeltaEvent(index=0, delta=ThinkingPartDelta(content_delta="abc"))
    for _ in range(10_000):
        sidebar._on_part_delta(ctx, event)

    buffer = ctx.think_body.get_buffer()
    assert buffer.get_char_count() == 0  # collapsed: no hidden GTK layout work
    assert len(ctx.think_acc) == 30_000

    sidebar._flush_streaming(ctx, force=True)
    assert buffer.get_char_count() == 30_000


def test_turn_completion_preserves_selection_in_older_message():
    """Finalizing one stream replaces only its temporary row, never older widgets."""
    from pydantic_ai.messages import ModelResponse, TextPart

    from grc_agent.chat_sidebar import ChatSidebar, _StreamCtx

    sidebar = ChatSidebar()
    old_box = sidebar._start_agent_message()
    sidebar._render_markdown_to_box(old_box, "older selectable text")
    old_tv = _unwrap_textviews(old_box)[0]
    old_buffer = old_tv.get_buffer()
    old_buffer.select_range(old_buffer.get_iter_at_offset(0), old_buffer.get_iter_at_offset(5))

    stream_ctx = _StreamCtx(sidebar._start_agent_message())
    sidebar._replace_streaming_turn(
        stream_ctx,
        [ModelResponse(parts=[TextPart(content="new response")])],
    )

    assert old_tv.get_parent().get_parent() is old_box  # tv -> its hscroll sw -> box
    start, end = old_buffer.get_selection_bounds()
    assert old_buffer.get_text(start, end, True) == "older"


def test_busy_release_does_not_steal_focus_from_transcript():
    from gi.repository import Gtk

    from grc_agent.chat_sidebar import ChatSidebar

    window = Gtk.OffscreenWindow()
    sidebar = ChatSidebar()
    window.add(sidebar)
    sidebar._flowgraph_proxy = object()
    box = sidebar._start_agent_message()
    sidebar._render_markdown_to_box(box, "keep transcript focus")
    tv = _unwrap_textviews(box)[0]
    window.show_all()
    tv.grab_focus()
    while Gtk.events_pending():
        Gtk.main_iteration()

    sidebar._set_busy(False)
    assert window.get_focus() is tv
    window.destroy()


def test_truncated_thinking_is_archived_before_active_history_cleanup(tmp_path, monkeypatch):
    from pydantic_ai.messages import ModelRequest, ModelResponse, ThinkingPart, UserPromptPart
    from pydantic_ai_harness.step_persistence import continue_run

    from grc_agent.chat_sidebar import ChatSidebar, _without_truncated_thinking_tail
    from grc_agent.db import conversation_id_for_session, get_step_store, save_session

    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))
    graph = tmp_path / "truncated.grc"
    graph.touch()
    session_id = save_session(None, str(graph), [])
    assert session_id is not None

    messages = [
        ModelRequest(parts=[UserPromptPart(content="continue")]),
        ModelResponse(
            parts=[ThinkingPart(content="repeated reasoning")],
            finish_reason="length",
        ),
    ]
    cleaned, removed = _without_truncated_thinking_tail(messages)
    assert removed is True
    assert cleaned == messages[:1]

    sidebar = ChatSidebar()
    assert asyncio.run(sidebar._archive_truncated_thinking(messages, session_id, "executor"))
    runs = asyncio.run(
        get_step_store().list_runs(conversation_id=conversation_id_for_session(session_id))
    )
    archived = next(
        run for run in runs if run.metadata.get("kind") == "truncated_thinking_transcript"
    )
    snapshot_messages = asyncio.run(continue_run(get_step_store(), run_id=archived.run_id))
    assert snapshot_messages == messages


def test_save_history_is_async_and_offloads_to_thread(monkeypatch):
    """DB-1 regression: _save_history must be async and dispatch save_session via
    asyncio.to_thread so it never blocks the gbulb event loop."""
    from unittest.mock import MagicMock

    from grc_agent.chat_sidebar import ChatSidebar

    sidebar = ChatSidebar()
    sidebar._active_session_id = 7
    proxy = MagicMock()
    cm = MagicMock()
    cm.path = "/tmp/x.grc"
    proxy._canvas_manager = cm
    sidebar._flowgraph_proxy = proxy

    used = {"to_thread": False}

    def fake_to_thread(fn, *a, **k):
        used["to_thread"] = True
        return asyncio.to_thread(fn, *a, **k)

    monkeypatch.setattr("grc_agent.chat_sidebar.asyncio.to_thread", fake_to_thread)
    monkeypatch.setattr("grc_agent.chat_sidebar.save_session", MagicMock(return_value=7))

    asyncio.run(sidebar._save_history())
    assert used["to_thread"] is True


def test_effective_path_unsaved_tab_fallback():
    """Unsaved flowgraph tabs fallback to untitled:<page_title> for session persistence."""
    from unittest.mock import MagicMock

    from grc_agent.chat_sidebar import ChatSidebar

    sidebar = ChatSidebar()
    proxy = MagicMock()
    cm = MagicMock()
    cm.path = ""
    cm.page_title = "MyUnsavedTab"
    proxy._canvas_manager = cm
    sidebar._flowgraph_proxy = proxy

    assert sidebar._get_effective_path() == "untitled:MyUnsavedTab"


def test_tool_expander_disables_auto_scroll():
    """Toggling a tool expander pauses _auto_scroll to prevent jump-scrolling."""
    from grc_agent.chat_sidebar import ChatSidebar

    sidebar = ChatSidebar()
    sidebar._auto_scroll = True
    exp = sidebar._make_tool_expander("inspect_graph")

    # Simulate GTK notify::expanded signal
    exp.set_expanded(True)
    assert sidebar._auto_scroll is False


def test_save_history_deletes_session_resurrected_by_concurrent_clear(monkeypatch):
    """M13 regression: _save_history captures _clear_generation BEFORE the
    asyncio.to_thread(save_session, ...) await. If a global Clear History bumps
    _clear_generation while that save is still in flight, the worker's INSERT
    can resurrect a row Clear History just deleted — this must be undone by
    calling delete_session on the resurrected id once the await returns."""
    from unittest.mock import MagicMock

    from grc_agent.chat_sidebar import ChatSidebar

    sidebar = ChatSidebar()
    sidebar._active_session_id = 42
    proxy = MagicMock()
    cm = MagicMock()
    cm.path = "/tmp/race.grc"
    proxy._canvas_manager = cm
    sidebar._flowgraph_proxy = proxy

    resumed = asyncio.Event()

    def fake_to_thread(fn, *a, **k):
        async def _runner():
            # Block until the test bumps _clear_generation, simulating a
            # concurrent Clear History completing while save_session is still
            # running on its worker thread.
            await resumed.wait()
            return fn(*a, **k)

        return _runner()

    monkeypatch.setattr("grc_agent.chat_sidebar.asyncio.to_thread", fake_to_thread)
    monkeypatch.setattr("grc_agent.chat_sidebar.save_session", MagicMock(return_value=42))
    mock_delete = MagicMock()
    monkeypatch.setattr("grc_agent.chat_sidebar.delete_session", mock_delete)

    async def _run():
        task = asyncio.ensure_future(sidebar._save_history())
        await asyncio.sleep(0)  # let _save_history capture gen and start the await
        sidebar._clear_generation += 1  # simulate the concurrent Clear History
        resumed.set()
        await task

    asyncio.run(_run())
    mock_delete.assert_called_once_with(42)


def test_clean_message_history_for_new_turn():
    """_clean_message_history_for_new_turn must pop trailing ModelResponses
    that contain unprocessed tool calls so PydanticAI accepts a subsequent
    user prompt without raising UserError."""
    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        RetryPromptPart,
        TextPart,
        ToolCallPart,
        UserPromptPart,
    )

    from grc_agent.chat_sidebar import _clean_message_history_for_new_turn

    # Case 1: Trailing ModelResponse with ToolCallPart is trimmed
    msgs = [
        ModelRequest(parts=[UserPromptPart(content="turn 1")]),
        ModelResponse(parts=[ToolCallPart("change_graph", {})]),
    ]
    cleaned = _clean_message_history_for_new_turn(msgs)
    assert len(cleaned) == 1
    assert isinstance(cleaned[0].parts[0], UserPromptPart)

    # Case 2: Multi-retry failure ending in ToolCallPart is trimmed
    msgs = [
        ModelRequest(parts=[UserPromptPart(content="turn 1")]),
        ModelResponse(parts=[ToolCallPart("change_graph", {})]),
        ModelRequest(parts=[RetryPromptPart(content="retry 1")]),
        ModelResponse(parts=[ToolCallPart("change_graph", {})]),
    ]
    cleaned = _clean_message_history_for_new_turn(msgs)
    assert len(cleaned) == 3
    assert isinstance(cleaned[-1].parts[0], RetryPromptPart)

    # Case 3: Completed response with TextPart is preserved intact
    msgs = [
        ModelRequest(parts=[UserPromptPart(content="turn 1")]),
        ModelResponse(parts=[TextPart("all done")]),
    ]
    cleaned = _clean_message_history_for_new_turn(msgs)
    assert len(cleaned) == 2


def test_parse_final_summary_accepts_grc_agent_response_shapes():
    """The model's final structured output (GrcAgentResponse) arrives as a
    final_result tool call; _parse_final_summary must recover (actions,
    explanation) from both the dict form (pydantic-ai ToolCallPart.args) and
    the JSON-string form, and return None for anything else so the caller
    falls back to a normal tool expander."""
    from grc_agent.chat_sidebar import _parse_final_summary

    assert _parse_final_summary(
        {"actions_taken": ["Added x", "Connected y"], "explanation": "Graph valid"}
    ) == (["Added x", "Connected y"], "Graph valid")
    assert _parse_final_summary('{"actions_taken": ["a"], "explanation": "e"}') == (["a"], "e")
    # Missing explanation is tolerated (the field is required by the schema,
    # but a malformed model response must degrade to a card, not a crash).
    assert _parse_final_summary({"actions_taken": ["a"]}) == (["a"], "")

    # Non-GrcAgentResponse shapes -> None (render as a plain tool expander).
    assert _parse_final_summary({"foo": "bar"}) is None
    assert _parse_final_summary({"actions_taken": "not a list"}) is None
    assert _parse_final_summary({"actions_taken": [1, 2]}) is None
    assert _parse_final_summary("not json") is None
    assert _parse_final_summary(None) is None
    assert _parse_final_summary(42) is None
    assert _parse_final_summary("") is None


def test_render_last_message_rich_shows_summary_card_for_final_result():
    """A final_result tool call carrying a GrcAgentResponse must render as a
    readable summary card (Done header + action bullets + explanation), not a
    raw-JSON tool expander."""
    from gi.repository import Gtk
    from pydantic_ai.messages import ModelResponse, ToolCallPart

    from grc_agent.chat_sidebar import ChatSidebar

    sidebar = ChatSidebar()
    msg = ModelResponse(
        parts=[
            ToolCallPart(
                tool_name="final_result",
                args={
                    "actions_taken": ["Added block", "Wired it up"],
                    "explanation": "The chain is valid.",
                },
                tool_call_id="fr1",
            )
        ]
    )
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    sidebar._render_last_message_rich(box, msg)

    def walk(w):
        yield w
        if hasattr(w, "get_children"):
            for c in w.get_children():
                yield from walk(c)

    widgets = list(walk(box))
    labels = [w for w in widgets if isinstance(w, Gtk.Label)]
    assert any("Done" in (lb.get_text() or "") for lb in labels), "summary card header missing"
    assert not any(isinstance(w, Gtk.Expander) for w in widgets), (
        "final_result must not render as a tool expander"
    )
    texts = " ".join(lb.get_text() or "" for lb in labels)
    assert "Added block" in texts and "Wired it up" in texts
    assert "The chain is valid." in texts


def test_render_last_message_rich_keeps_expander_for_other_tools():
    """Ordinary tool calls must keep the expander rendering — only the
    final_result structured output becomes a summary card."""
    from gi.repository import Gtk
    from pydantic_ai.messages import ModelResponse, ToolCallPart

    from grc_agent.chat_sidebar import ChatSidebar

    sidebar = ChatSidebar()
    msg = ModelResponse(
        parts=[ToolCallPart(tool_name="inspect_graph", args={"detail": "all"}, tool_call_id="c1")]
    )
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    sidebar._render_last_message_rich(box, msg)
    assert any(isinstance(c, Gtk.Expander) for c in box.get_children())


def test_notify_run_failure_dispatches_short_notification(monkeypatch):
    """notify_run_failure sends a SHORT notification (return code + tool hint)
    — NOT the full log. The full log is read on demand via get_run_log."""
    from unittest.mock import MagicMock

    from grc_agent.chat_sidebar import ChatSidebar

    sidebar = ChatSidebar()
    sidebar.send_message = MagicMock()

    captured = {}

    def fake_ensure_future(coro):
        captured["coro"] = coro
        return MagicMock()

    monkeypatch.setattr("grc_agent.chat_sidebar.asyncio.ensure_future", fake_ensure_future)

    sidebar.notify_run_failure(1, "RuntimeError: No RTL-SDR devices found!\n" * 100)

    assert "coro" in captured
    # _chat_task is None on a fresh sidebar, so the coroutine sends right away
    asyncio.run(captured["coro"])
    sidebar.send_message.assert_called_once()
    sent_text = sidebar.send_message.call_args.args[0]
    # The notification must contain the return code
    assert "return code 1" in sent_text
    # The notification must NOT contain the full log — the agent reads it
    # via get_run_log tool
    assert "RTL-SDR" not in sent_text
    # Must mention get_run_log so the agent knows what tool to call
    assert "get_run_log" in sent_text


def test_notify_run_failure_does_not_send_when_busy(monkeypatch):
    """If the agent is mid-turn, the notification queues behind it
    via _send_fix_when_free (which awaits the in-flight task)."""
    from unittest.mock import MagicMock

    from grc_agent.chat_sidebar import ChatSidebar

    sidebar = ChatSidebar()
    sidebar.send_message = MagicMock()

    captured = {}

    def fake_ensure_future(coro):
        captured["coro"] = coro
        return MagicMock()

    monkeypatch.setattr("grc_agent.chat_sidebar.asyncio.ensure_future", fake_ensure_future)

    # Simulate a busy agent
    sidebar._busy = True
    sidebar._chat_task = MagicMock()
    sidebar._chat_task.done.return_value = False

    sidebar.notify_run_failure(1, "some error")
    # The coroutine was dispatched but won't call send_message until
    # _chat_task is done — verify it was captured but not yet run
    assert "coro" in captured
    sidebar.send_message.assert_not_called()


def test_send_fix_when_free_waits_for_in_flight_turn():
    from unittest.mock import MagicMock

    from grc_agent.chat_sidebar import ChatSidebar

    sidebar = ChatSidebar()
    sidebar.send_message = MagicMock()

    async def _run():
        async def _pending():
            await asyncio.sleep(0.01)

        sidebar._chat_task = asyncio.ensure_future(_pending())
        # origin_page = current_page (None without a flowgraph proxy) so the
        # post-await "did the user switch tabs?" guard passes through to
        # send_message. H2 regression coverage lives in a separate test.
        await sidebar._send_fix_when_free("fix prompt", sidebar.current_page)

    asyncio.run(_run())
    sidebar.send_message.assert_called_once_with("fix prompt")


def test_send_fix_when_free_aborts_when_user_switched_tabs():
    """H2 regression: if the user switches flowgraph tabs while the fix
    bubble is awaiting an in-flight chat task, _send_fix_when_free must NOT
    dispatch the fix prompt to the now-current (different) flowgraph."""
    from unittest.mock import MagicMock

    from grc_agent.chat_sidebar import ChatSidebar

    sidebar = ChatSidebar()
    sidebar.send_message = MagicMock()

    async def _run():
        async def _pending():
            await asyncio.sleep(0.01)

        sidebar._chat_task = asyncio.ensure_future(_pending())
        # Capture a "page A" identity; simulate the user switching to a
        # different page during the await by having current_page return a
        # distinct object afterwards.
        origin_page = object()
        sidebar._flowgraph_proxy = MagicMock()
        sidebar._flowgraph_proxy._canvas_manager.current_page = object()
        await sidebar._send_fix_when_free("fix prompt", origin_page)

    asyncio.run(_run())
    sidebar.send_message.assert_not_called()


def test_context_label_updates_with_pydantic_ai_usage(tmp_path, monkeypatch):
    """Context label must extract token usage natively from Pydantic AI ModelResponse.usage."""
    from decimal import Decimal

    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        RequestUsage,
        TextPart,
        UserPromptPart,
    )

    import grc_agent.agent_factory as _af
    from grc_agent.agent_factory import (
        _context_length_cache,
        _context_negative_cache,
        resolve_model_context_length,
    )
    from grc_agent.chat_sidebar import ChatSidebar, format_tokens
    from grc_agent.db import deserialize_messages, serialize_messages

    assert format_tokens(1200) == "1.2k"
    assert format_tokens(14710) == "14.7k"
    assert format_tokens(128000) == "128k"

    # Test dynamic context length API resolution (1024 * 1024 = 1,048,576 = 1M).
    # The HTTP call itself is stubbed: this test exercises the resolution →
    # cache → label pipeline, not ollama.com's availability, and a live call
    # made it machine/network dependent.

    _context_length_cache.clear()
    _context_negative_cache.clear()
    # Hermetic: patch the probe dispatch seam (the dict captured at import
    # time — rebinding _af._ollama_context_length is inert and lets the real
    # HTTP probe hit the developer's local daemon) and redirect settings to a
    # tmp .env so load_settings() can't read the real repo .env.
    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))
    monkeypatch.setitem(_af._CTX_PROBES, "ollama", lambda _model: 1024 * 1024)
    monkeypatch.setitem(_af._CTX_PROBES, "ollama_cloud", lambda _model: 1024 * 1024)
    assert resolve_model_context_length("ollama", "deepseek-v4-flash:cloud") == 1_048_576
    _context_length_cache.clear()
    _context_negative_cache.clear()

    sidebar = ChatSidebar()
    sidebar.set_active_provider("ollama_cloud", "deepseek-v4-flash:cloud")

    sidebar._message_history = deserialize_messages(
        serialize_messages(
            [
                ModelRequest(parts=[UserPromptPart(content="Hello")]),
                ModelResponse(
                    parts=[TextPart(content="Hi")],
                    usage=RequestUsage(
                        input_tokens=3300,
                        output_tokens=300,
                        cost=Decimal("0.0012345"),
                    ),
                ),
            ]
        )
    )

    sidebar._update_context_label()
    text = sidebar._context_label.get_label()
    assert "3.3k / 1M tok" in text
    assert "0%" in text
    assert "Cost: $0.0012345" in text
    assert "Native Pydantic AI last-turn cost: $0.0012345" in (
        sidebar._context_label.get_tooltip_text()
    )

    # Never present a partial cross-provider sum as the session total.
    sidebar._message_history.append(
        ModelResponse(
            parts=[TextPart(content="Unpriced")],
            usage=RequestUsage(input_tokens=20, output_tokens=5),
        )
    )
    sidebar._update_context_label()
    assert "Cost: N/A" in sidebar._context_label.get_label()

    # A new user prompt starts a new native RunUsage total; prior-turn cost
    # must not be added to it or lost-history compaction would skew the label.
    sidebar._message_history.extend(
        [
            ModelRequest(parts=[UserPromptPart(content="Next turn")]),
            ModelResponse(
                parts=[TextPart(content="Priced again")],
                usage=RequestUsage(
                    input_tokens=50,
                    output_tokens=10,
                    cost=Decimal("0.0002"),
                ),
            ),
        ]
    )
    sidebar._update_context_label()
    assert "Cost: $0.0002" in sidebar._context_label.get_label()


def test_badge_regex_matching():
    """Block-name badge regex (MarkdownView.compile_badge_regex, the real
    production path — the ChatSidebar pass-through shim was deleted): whole-
    word match built from the live flowgraph's block names, no substring
    false positives, longest-name-first precedence, cached by block-name set."""
    from unittest.mock import MagicMock

    from grc_agent.chat_sidebar import ChatSidebar

    sidebar = ChatSidebar()
    proxy = MagicMock()
    cm = MagicMock()
    fg = MagicMock()

    def _mock_blocks(names):
        blocks = []
        for n in names:
            b = MagicMock()
            b.name = n
            blocks.append(b)
        return blocks

    fg.blocks = _mock_blocks({"test_block_x", "x", "samp_rate", "other"})
    cm.current_flow_graph = fg
    proxy._canvas_manager = cm
    sidebar._flowgraph_proxy = proxy

    rx = sidebar._md.compile_badge_regex()
    assert rx is not None
    assert [m.group(1) for m in rx.finditer("test_block_x")] == ["test_block_x"]
    assert [m.group(1) for m in rx.finditer("x")] == ["x"]
    assert [m.group(1) for m in rx.finditer("test_block_x and x")] == ["test_block_x", "x"]
    # samp_rate_2 must not match samp_rate as a substring — _ is a word char.
    assert [m.group(1) for m in rx.finditer("samp_rate_2")] == []
    assert [m.group(1) for m in rx.finditer("the rate of")] == []

    # Same block-name set -> identical cached pattern object.
    assert sidebar._md.compile_badge_regex() is rx

    # No active blocks -> None, and the cache is cleared.
    cm.current_flow_graph = None
    assert sidebar._md.compile_badge_regex() is None
    assert sidebar._md._badge_regex_cache is None


def test_markdown_link_drag_selects_without_opening(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from gi.repository import Gdk, Gtk

    from grc_agent.chat_sidebar import ChatSidebar

    sidebar = ChatSidebar()
    tag = SimpleNamespace()
    widget = MagicMock()
    show_uri = MagicMock()
    monkeypatch.setattr(Gtk, "show_uri_on_window", show_uri)

    press = SimpleNamespace(type=Gdk.EventType.BUTTON_PRESS, x=10, y=10, time=1)
    release = SimpleNamespace(type=Gdk.EventType.BUTTON_RELEASE, x=40, y=10, time=2)
    assert sidebar._md._on_link_tag_event(tag, widget, press, None, "https://example.com") is False
    widget.drag_check_threshold.return_value = True
    assert (
        sidebar._md._on_link_tag_event(tag, widget, release, None, "https://example.com") is False
    )
    show_uri.assert_not_called()

    assert sidebar._md._on_link_tag_event(tag, widget, press, None, "https://example.com") is False
    widget.drag_check_threshold.return_value = False
    assert sidebar._md._on_link_tag_event(tag, widget, release, None, "https://example.com") is True
    show_uri.assert_called_once()


def test_markdown_table_inline_text_is_selectable():
    from grc_agent.chat_sidebar import ChatSidebar

    label = ChatSidebar()._md._inline_label("cell text", bold=False)
    assert label.get_selectable() is True


def test_badge_render_prose_textview():
    """Prose markdown blocks render into a Gtk.TextView; a mentioned block
    name becomes a GtkTextChildAnchor-embedded pill badge, not plain text."""
    from unittest.mock import MagicMock

    from grc_agent.chat_sidebar import ChatSidebar

    sidebar = ChatSidebar()
    proxy = MagicMock()
    cm = MagicMock()
    block = MagicMock()
    block.name = "test_block_x"
    fg = MagicMock()
    fg.blocks = [block]
    cm.current_flow_graph = fg
    proxy._canvas_manager = cm
    sidebar._flowgraph_proxy = proxy

    box = sidebar._start_agent_message()
    sidebar._render_markdown_to_box(box, "This refers to test_block_x in text.", clear=True)

    textviews = _unwrap_textviews(box)
    assert len(textviews) == 1
    tv = textviews[0]

    buffer = tv.get_buffer()
    # get_text() excludes the child-anchor placeholder entirely — get_slice()
    # is the one that includes it.
    slice_text = buffer.get_slice(buffer.get_start_iter(), buffer.get_end_iter(), True)
    assert "￼" in slice_text

    start, end = buffer.get_start_iter(), buffer.get_end_iter()
    it = start.copy()
    anchors = []
    while it.compare(end) < 0:
        anchor = it.get_child_anchor()
        if anchor:
            anchors.append(anchor)
        if not it.forward_char():
            break
    assert len(anchors) == 1


def test_badge_only_paragraph_not_dropped():
    """A message that is nothing but a block name must still render — the
    emptiness check in _render_markdown_to_box uses get_slice (which counts
    the badge's anchor placeholder), not get_text (which would see nothing
    but a trailing newline and silently drop the whole paragraph)."""
    from unittest.mock import MagicMock

    from grc_agent.chat_sidebar import ChatSidebar

    sidebar = ChatSidebar()
    proxy = MagicMock()
    cm = MagicMock()
    block = MagicMock()
    block.name = "test_block_x"
    fg = MagicMock()
    fg.blocks = [block]
    cm.current_flow_graph = fg
    proxy._canvas_manager = cm
    sidebar._flowgraph_proxy = proxy

    box = sidebar._start_agent_message()
    sidebar._render_markdown_to_box(box, "test_block_x", clear=True)

    textviews = _unwrap_textviews(box)
    assert len(textviews) == 1


def test_contiguous_prose_paragraphs_rendered_without_excess_widgets():
    """Contiguous markdown paragraphs and headings group into a single TextView
    with natural paragraph breaks rather than fragmenting into a separate widget
    per paragraph (which stacked box spacing on top of line margins and caused
    excessive height allocations)."""

    from grc_agent.chat_sidebar import ChatSidebar

    sidebar = ChatSidebar()
    box = sidebar._start_agent_message()
    md_text = (
        "### Overview\n\n"
        "Here is the first paragraph.\n\n"
        "Here is the second paragraph with **bold** text.\n\n"
        "```python\nprint('code')\n```\n\n"
        "Here is the concluding paragraph."
    )
    sidebar._render_markdown_to_box(box, md_text, clear=True)

    children = box.get_children()
    # Should have: prose TextView (heading + 2 paragraphs), CodeBlock (pre),
    # prose TextView (concluding paragraph) — prose TextViews are wrapped in
    # AUTOMATIC-hscrollbar ScrolledWindows.
    assert len(children) == 3
    textviews = _unwrap_textviews(box)
    assert len(textviews) == 2
    buf = textviews[0].get_buffer()
    text = buf.get_slice(buf.get_start_iter(), buf.get_end_iter(), True)
    assert "Overview" in text
    assert "first paragraph" in text
    assert "second paragraph" in text


def test_badge_hover_calls_canvas_highlight():
    """Hovering a chat badge pill calls canvas_manager.set_highlight_block;
    un-hovering calls clear_highlight. Bound methods (not lambdas) are used
    for enter/leave so this can be verified without a real Gdk.Event."""
    from unittest.mock import MagicMock

    from grc_agent.chat_sidebar import ChatSidebar

    sidebar = ChatSidebar()
    proxy = MagicMock()
    cm = MagicMock()
    proxy._canvas_manager = cm
    sidebar._flowgraph_proxy = proxy

    from grc_agent.ui.block_badge import badge_enter, badge_leave

    badge_enter(cm, "analog_sig_source_x_0")
    cm.set_highlight_block.assert_called_once_with("analog_sig_source_x_0")

    badge_leave(cm)
    cm.clear_highlight.assert_called_once()


def test_badge_click_scrolls_canvas():
    """Clicking a block pill badge in the chat sidebar invokes scroll_to_block on canvas."""
    from unittest.mock import MagicMock

    from gi.repository import Gdk

    from grc_agent.chat_sidebar import ChatSidebar

    sidebar = ChatSidebar()
    proxy = MagicMock()
    cm = MagicMock()
    proxy._canvas_manager = cm
    sidebar._flowgraph_proxy = proxy

    from grc_agent.ui.block_badge import badge_click

    event = MagicMock()
    event.type = Gdk.EventType.BUTTON_PRESS
    event.button = 1

    result = badge_click(cm, event, "block_0")
    assert result is True
    cm.scroll_to_block.assert_called_once_with("block_0")


def test_link_click_opens_uri(monkeypatch):
    """A markdown link in a chat message must still open a browser on click.
    Gtk.Label's built-in activate-link handling did this for free before the
    prose renderer moved to Gtk.TextView, which has no equivalent default —
    this is the explicit replacement, not a new feature."""
    from unittest.mock import MagicMock

    from gi.repository import Gdk

    from grc_agent.chat_sidebar import ChatSidebar

    sidebar = ChatSidebar()
    box = sidebar._start_agent_message()
    sidebar._render_markdown_to_box(box, "Check [this link](https://example.com) out.", clear=True)

    textviews = _unwrap_textviews(box)
    assert len(textviews) == 1
    buffer = textviews[0].get_buffer()

    found_tags = []
    buffer.get_tag_table().foreach(lambda t: found_tags.append(t))
    link_tags = [t for t in found_tags if getattr(t, "grc_href", None)]
    assert len(link_tags) == 1
    assert link_tags[0].grc_href == "https://example.com"

    opened = MagicMock()
    monkeypatch.setattr("grc_agent.ui.markdown_view.Gtk.show_uri_on_window", opened)

    widget = MagicMock()
    widget.drag_check_threshold.return_value = False
    press = MagicMock(type=Gdk.EventType.BUTTON_PRESS, x=10, y=10, time=998)
    release = MagicMock(type=Gdk.EventType.BUTTON_RELEASE, x=10, y=10, time=999)
    sidebar._md._on_link_tag_event(link_tags[0], widget, press, None, "https://example.com")
    handled = sidebar._md._on_link_tag_event(
        link_tags[0], widget, release, None, "https://example.com"
    )

    assert handled is True
    opened.assert_called_once_with(None, "https://example.com", 999)


def test_table_renders_block_badges():
    """Block names inside a Markdown table render as a real Gtk.Grid (TableBlock)
    whose cells contain interactive pill badges — not ASCII art in a TextView."""
    from unittest.mock import MagicMock

    from gi.repository import Gtk

    from grc_agent.chat_sidebar import ChatSidebar
    from grc_agent.ui.block_badge import BlockBadge
    from grc_agent.ui.table_block import TableBlock

    sidebar = ChatSidebar()
    proxy = MagicMock()
    cm = MagicMock()
    block1 = MagicMock()
    block1.name = "tone1"
    block2 = MagicMock()
    block2.name = "mixer"
    fg = MagicMock()
    fg.blocks = [block1, block2]
    cm.current_flow_graph = fg
    proxy._canvas_manager = cm
    sidebar._flowgraph_proxy = proxy

    box = sidebar._start_agent_message()
    table_md = "| Block | Type |\n|---|---|\n| tone1 | Source |\n| mixer | Adder |"
    sidebar._render_markdown_to_box(box, table_md, clear=True)

    tables = [c for c in box.get_children() if isinstance(c, TableBlock)]
    assert len(tables) == 1, "expected exactly one TableBlock"

    # Count BlockBadge pills anywhere under the table (tone1 + mixer).
    pills = []

    def walk(w):
        if isinstance(w, BlockBadge):
            pills.append(w)
        if isinstance(w, Gtk.Container):
            for ch in w.get_children():
                walk(ch)

    walk(tables[0])
    assert len(pills) == 2, "expected tone1 + mixer pills in the table"
    badge_names = sorted(p.get_child().get_text() for p in pills)
    assert badge_names == ["mixer", "tone1"]


def test_copy_code_block_to_clipboard():
    """Test markdown pre element rendering creates a Copy button and copies code text to clipboard."""
    from gi.repository import Gdk, Gtk

    from grc_agent.chat_sidebar import ChatSidebar

    sidebar = ChatSidebar()
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

    code_snippet = "def hello_world():\n    print('Antigravity')"
    md_text = f"```python\n{code_snippet}\n```"

    sidebar._render_markdown_to_box(box, md_text)

    # Locate code_box container, header_box, and copy_btn
    widgets: list[Gtk.Widget] = []

    def walk(w):
        widgets.append(w)
        if isinstance(w, Gtk.Container):
            for c in w.get_children():
                walk(c)

    walk(box)

    buttons = [
        w
        for w in widgets
        if isinstance(w, Gtk.Button)
        and (
            w.get_style_context().has_class("chat-copy-btn")
            or w.get_tooltip_text() in ("Copy code to clipboard", "Copied!")
        )
    ]
    assert len(buttons) >= 1, "Expected Copy button in rendered code block"

    copy_btn = buttons[0]

    # Trigger click on Copy button
    copy_btn.emit("clicked")
    assert copy_btn.get_tooltip_text() == "Copied!"

    # Verify text was written to system clipboard
    clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
    text = clipboard.wait_for_text()
    assert text is not None
    assert "def hello_world():" in text


def _unwrap_textviews(box):
    """Collect every Gtk.TextView under ``box`` — prose TextViews live inside
    AUTOMATIC-hscrollbar ScrolledWindows, so descend one container level."""
    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk

    views = []
    for child in box.get_children():
        if isinstance(child, Gtk.TextView):
            views.append(child)
        elif isinstance(child, Gtk.ScrolledWindow):
            inner = child.get_child()
            if isinstance(inner, Gtk.TextView):
                views.append(inner)
    return views


def test_prose_textview_wraps_at_available_width():
    """Gtk.TextView doesn't self-report a usable natural width for
    word-wrapped content (unlike Gtk.Label, which measures via Pango
    internally) — left unpinned, the agent message bubble collapses to a
    tiny width and wraps one word per line. The rendered bubble must be
    pinned to most of the available column width instead (the enclosing
    AUTOMATIC-hscrollbar ScrolledWindow isolates the column from the
    TextView's content-driven minimum)."""
    from gi.repository import Gtk

    from grc_agent.chat_sidebar import ChatSidebar

    win = Gtk.Window()
    win.set_default_size(700, 400)
    sidebar = ChatSidebar()
    win.add(sidebar)
    win.show_all()
    while Gtk.events_pending():
        Gtk.main_iteration()

    box = sidebar._start_agent_message()
    long_text = (
        "This is a long sentence meant to exercise word wrapping across a "
        "realistically wide chat column so it does not collapse to one word "
        "per line."
    )
    sidebar._render_markdown_to_box(box, long_text, clear=True)
    while Gtk.events_pending():
        Gtk.main_iteration()

    textviews = _unwrap_textviews(box)
    assert len(textviews) == 1
    width, _height = textviews[0].get_size_request()
    assert width > 400

    win.destroy()


def test_prose_textview_rewraps_on_listbox_resize():
    """A prose bubble rendered before the window's first size-allocate
    (get_allocated_width() reads 0, so the narrow fallback width is used —
    e.g. session history loaded at startup before window.show_all()) must
    widen once the sidebar is actually laid out, via the listbox's
    size-allocate handler re-pinning every rendered bubble to the column."""
    from gi.repository import Gtk

    from grc_agent.chat_sidebar import ChatSidebar

    sidebar = ChatSidebar()
    long_text = (
        "This is a long sentence meant to exercise word wrapping across a "
        "realistically wide chat column so it does not collapse to one word "
        "per line."
    )
    box = sidebar._start_agent_message()
    sidebar._render_markdown_to_box(box, long_text, clear=True)
    tv = _unwrap_textviews(box)[0]
    narrow_width, _height = tv.get_size_request()

    win = Gtk.Window()
    win.set_default_size(900, 500)
    win.add(sidebar)
    win.show_all()
    while Gtk.events_pending():
        Gtk.main_iteration()

    wide_width, _height = tv.get_size_request()
    assert wide_width > narrow_width

    win.destroy()


def test_streaming_never_shoves_paned_divider():
    """End-to-end regression for the streaming width bug, at BOTH a wide and a
    narrow starting geometry (the shove reproduced on the old code only from
    a narrow sidebar): a bare stream Gtk.TextView's preferred width follows
    its (unwrapped) buffer content and then sticks at its last allocation, so
    long unbroken tokens (code lines, URLs) used to grow the chat column's
    minimum width and shove the outer HPaned's divider aside mid-stream —
    the sidebar visibly 'ate' the canvas as tokens arrived. With
    AUTOMATIC-hscrollbar isolation + the column pin, neither the divider nor
    the chat column may move while tokens stream, and the chat column must
    stay freely shrinkable afterwards (divider drag)."""
    from gi.repository import Gtk

    from grc_agent.chat_sidebar import ChatSidebar, _StreamCtx

    for start_pos, narrow in ((500, False), (782, True)):
        win = Gtk.Window()
        win.set_default_size(1000, 500)
        canvas = Gtk.Label(label="canvas")
        sidebar = ChatSidebar()  # loads the real CSS in its constructor
        paned = Gtk.HPaned()
        # Same packing as desktop_app.py: shrink=False lets pack2's minimum
        # dictate the divider's leftmost reachable position.
        paned.pack1(canvas, resize=True, shrink=False)
        paned.pack2(sidebar, resize=True, shrink=False)
        win.add(paned)
        paned.set_position(start_pos)
        win.show_all()
        while Gtk.events_pending():
            Gtk.main_iteration()
        pos_before = paned.get_position()
        col_before = sidebar._listbox.get_allocated_width()
        if not narrow:
            assert col_before > 300  # sane wide starting column

        box = sidebar._start_agent_message()
        ctx = _StreamCtx(box)
        tv = sidebar._ensure_text(ctx)
        pin, _h = tv.get_size_request()
        assert 160 <= pin <= col_before  # pinned, not (-1, -1), not beyond column
        buf = tv.get_buffer()
        for _ in range(20):  # 20 long unbreakable lines stream in
            buf.insert(buf.get_end_iter(), "x" * 400 + "\n")
            while Gtk.events_pending():
                Gtk.main_iteration()

        assert paned.get_position() == pos_before
        assert sidebar._listbox.get_allocated_width() == col_before

        # Divider drag (user narrows the sidebar): the chat column must
        # follow — the old allocation-sticky minimum used to block this at
        # the high-water mark reached while streaming. (Only meaningful from
        # the wide start; the narrow start already sits at the sidebar's
        # minimum chrome width.)
        if not narrow:
            paned.set_position(850)
            paned.check_resize()  # set_position alone doesn't trigger reallocation
            while Gtk.events_pending():
                Gtk.main_iteration()
            assert sidebar._listbox.get_allocated_width() < col_before

        win.destroy()


def test_model_wait_indicator_lifecycle():
    """The status bar's elapsed-time label is visible exactly while a model
    request is in flight: start shows it with an immediate 0s tick and arms a
    1s GLib source; a tick updates the text in place; stop hides the label and
    removes the source. Belt-and-braces: the chat-task-done callback also stops
    it (guards a future path that ends a task without unwinding the loop)."""
    from gi.repository import GLib, Gtk

    from grc_agent.chat_sidebar import ChatSidebar

    win = Gtk.Window()
    sidebar = ChatSidebar()
    win.add(sidebar)
    win.show_all()
    while Gtk.events_pending():
        Gtk.main_iteration()

    assert not sidebar._wait_label.get_visible()

    sidebar._model_wait_start()
    assert sidebar._wait_label.get_visible()
    assert sidebar._wait_label.get_text() == "Waiting for model\u2026 0s"
    assert sidebar._wait_timer_id is not None

    # Simulate the 1s tick twice: text updates in place, source stays armed.
    sidebar._wait_started -= 63  # pretend 63s elapsed
    assert sidebar._on_wait_tick() is GLib.SOURCE_CONTINUE
    assert sidebar._wait_label.get_text() == "Waiting for model\u2026 1m03s"

    sidebar._model_wait_stop()
    assert not sidebar._wait_label.get_visible()
    assert sidebar._wait_timer_id is None

    # Task-done belt-and-braces: stop() is idempotent (no GLib warning).
    sidebar._model_wait_stop()

    win.destroy()


def test_code_block_height_pin_shows_full_content():
    """Regression: a code block containing an ASCII diagram (13 lines)
    rendered in a ~46px porthole — the ListBox allocates a row child its
    MINIMUM, and the code ScrolledWindow's AUTOMATIC vpolicy made its
    minimum tiny. The height pin (request = min(Pango-measured content
    height, 420)) makes min == natural below the cap, so the diagram shows
    in full with no scrollbars; content taller than the cap gets exactly
    the 420px viewport. Measured at construction via create_pango_layout
    over the buffer text — an unrealized TextView's own preferred height is
    0/1 (no font metrics), but its style font is already resolved."""
    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk

    from grc_agent.chat_sidebar import ChatSidebar
    from grc_agent.ui.code_block import CodeBlock

    diagram = "\n".join(
        [
            "vec_mu0 -+   vec_mu1 -+   vec_local -+",
            "        mul_mu0       mul_mu1      mul_local",
            "        ^ bpf_mu0     ^ bpf_mu1    ^ sig_local",
            "      route_a       route_b      route_c",
            "        +------+-------+             |",
            "               +----> add_0 <-------+",
            "                       |",
            "                   throttle_0",
            "                       |",
            "               unified_spectrogram",
        ]
    )

    win = Gtk.Window()
    win.set_default_size(1000, 800)
    sidebar = ChatSidebar()
    win.add(sidebar)
    win.show_all()
    while Gtk.events_pending():
        Gtk.main_iteration()

    box = sidebar._start_agent_message()
    sidebar._render_markdown_to_box(box, "```\n" + diagram + "\n```\n", clear=True)
    while Gtk.events_pending():
        Gtk.main_iteration()

    def find_cb(w):
        if isinstance(w, CodeBlock):
            return w
        if isinstance(w, Gtk.Container):
            for c in w.get_children():
                r = find_cb(c)
                if r:
                    return r
        return None

    cb = find_cb(box)
    sw = next(c for c in cb.get_children() if isinstance(c, Gtk.ScrolledWindow))
    tv = sw.get_child()

    _tvmn, tv_nat = tv.get_preferred_height()
    sw_mn, sw_nat = sw.get_preferred_height()
    assert sw_mn == sw_nat  # pin closed the min<natural gap below the cap
    assert tv.get_allocated_height() >= tv_nat, "diagram clipped"
    vs = sw.get_vscrollbar()
    assert not (vs is not None and vs.get_child_visible()), "unneeded vscrollbar"

    # Above the cap: request is exactly 420 and the viewport scrolls.
    tall = CodeBlock("", "\n".join(f"line {i}" for i in range(80)))
    tsw = next(c for c in tall.get_children() if isinstance(c, Gtk.ScrolledWindow))
    assert tsw.get_size_request()[1] == 420

    win.destroy()


def test_highlight_cleared_on_history_rebuild(tmp_path, monkeypatch):
    """A full message-list rebuild (_render_history) must clear the canvas
    highlight — GTK3 doesn't synthesize leave-notify-event on a destroyed
    widget, so a badge pill removed mid-hover could otherwise leave a stale
    highlight stuck on canvas."""
    from unittest.mock import MagicMock

    # Redirect the DB: an empty history renders the welcome screen, whose
    # recent-sessions list runs init_db() against the real chat DB otherwise.
    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))

    from grc_agent.chat_sidebar import ChatSidebar

    sidebar = ChatSidebar()
    proxy = MagicMock()
    cm = MagicMock()
    proxy._canvas_manager = cm
    sidebar._flowgraph_proxy = proxy

    sidebar._render_history()

    cm.clear_highlight.assert_called_once()


def test_highlight_cleared_on_partial_rerender():
    """A single-message re-render (_render_last_message_rich, fired per
    streaming chunk) must also clear the canvas highlight — same reason as the
    full-rebuild test above: a pill hovered mid-stream is destroyed without a
    leave-notify-event, which would otherwise leave a stale outline."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from grc_agent.chat_sidebar import ChatSidebar

    sidebar = ChatSidebar()
    proxy = MagicMock()
    cm = MagicMock()
    proxy._canvas_manager = cm
    sidebar._flowgraph_proxy = proxy

    box = sidebar._start_agent_message()
    # Empty-parts ModelResponse is enough to reach the clear at the top of
    # _render_last_message_rich (it runs before the parts loop).
    sidebar._render_last_message_rich(box, SimpleNamespace(parts=[]))

    cm.clear_highlight.assert_called_once()


def test_compact_now_button_compacts_history_and_snapshots_first(tmp_path, monkeypatch):
    """The Compact action: runs on the unified loop, snapshots the
    pre-compact history into the step store FIRST (D3 — ConversationSearch
    recall), replaces _message_history with the compacted list, saves +
    re-renders, and returns to the not-busy state. Uses the real
    make_summarizing_strategy + harness compact_now with a TestModel agent
    (the summary call is a real nested run against the inherited model)."""
    from unittest.mock import AsyncMock, MagicMock

    from pydantic_ai import Agent
    from pydantic_ai.messages import (
        ModelMessage,
        ModelRequest,
        ModelResponse,
        TextPart,
        UserPromptPart,
    )
    from pydantic_ai.models.test import TestModel

    from grc_agent.chat_sidebar import ChatSidebar
    from grc_agent.db import conversation_id_for_session, get_step_store, save_session

    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))

    # A real session row so the pre-compact snapshot seam actually runs.
    f = tmp_path / "g.grc"
    f.touch()
    sid = save_session(None, str(f), [])

    def _turn(i: int) -> list[ModelMessage]:
        return [
            ModelRequest(parts=[UserPromptPart(content=f"Turn {i}: " + ("y" * 200))]),
            ModelResponse(parts=[TextPart(content=f"Turn {i} done.")]),
        ]

    history: list[ModelMessage] = []
    for i in range(1, 30):
        history.extend(_turn(i))

    agent = Agent(TestModel(), capabilities=[])
    sidebar = ChatSidebar()
    sidebar._agent = agent
    sidebar._active_session_id = sid
    sidebar._message_history = history
    sidebar._save_history = AsyncMock()
    sidebar._render_history = MagicMock()
    sidebar._update_context_label = MagicMock()

    asyncio.run(sidebar._run_compact_now())

    # The history was actually compacted: starts with the summary part.
    from pydantic_ai.messages import SystemPromptPart

    first = sidebar._message_history[0]
    assert any(
        isinstance(p, SystemPromptPart) and "Summary" in str(p.content) for p in first.parts
    ), "compacted history must start with the summary"
    assert len(sidebar._message_history) < len(history), "history must shrink"
    sidebar._save_history.assert_awaited_once()
    sidebar._render_history.assert_called_once()
    assert sidebar._busy is False

    # D3: the pre-compact snapshot row exists under the conversation id — the
    # ORIGINAL (unsummarized) history is what was snapshotted, so
    # ConversationSearch can still recall what the summary drops.
    conv = conversation_id_for_session(sid)
    store = get_step_store()
    runs = asyncio.run(store.list_runs(conversation_id=conv))
    assert runs, "pre-compact snapshot run never registered"
    snaps = asyncio.run(store.list_snapshots(run_id=runs[-1].run_id))
    assert snaps, "pre-compact snapshot unreadable via the store seam"
    snap_msgs = snaps[-1].messages
    assert len(snap_msgs) == len(history), "snapshot must hold the FULL pre-compact history"


def test_compact_now_refuses_without_an_active_session(tmp_path, monkeypatch):
    """The no-session guard (audit fix): with no session row there is no
    conversation id, so the pre-compact snapshot cannot be registered and
    compacting would destroy the only in-memory copy of the history. The
    button must refuse with a status message and never touch the history."""
    from unittest.mock import MagicMock

    from pydantic_ai import Agent
    from pydantic_ai.messages import (
        ModelMessage,
        ModelRequest,
        ModelResponse,
        TextPart,
        UserPromptPart,
    )
    from pydantic_ai.models.test import TestModel

    from grc_agent.chat_sidebar import ChatSidebar

    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))

    history: list[ModelMessage] = [
        ModelRequest(parts=[UserPromptPart(content="u1")]),
        ModelResponse(parts=[TextPart(content="r1")]),
    ]
    sidebar = ChatSidebar()
    sidebar._agent = Agent(TestModel(), capabilities=[])
    sidebar._message_history = history
    sidebar._active_session_id = None
    sidebar._render_history = MagicMock()

    sidebar._on_compact_clicked(MagicMock())

    assert sidebar._message_history is history, "history must be untouched"
    assert sidebar._busy is False, "must not enter the busy state"
    assert "not saved to a session" in sidebar._status_label.get_text()


def test_compact_button_requires_explicit_confirmation(tmp_path, monkeypatch):
    """The text Compact action defaults to No and starts no work until the
    user explicitly confirms the summary operation."""
    from unittest.mock import AsyncMock

    from gi.repository import Gtk
    from pydantic_ai import Agent
    from pydantic_ai.messages import ModelRequest, UserPromptPart
    from pydantic_ai.models.test import TestModel

    from grc_agent.chat_sidebar import ChatSidebar
    from grc_agent.db import save_session

    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))
    graph = tmp_path / "compact-confirm.grc"
    graph.touch()
    session_id = save_session(None, str(graph), [])
    assert session_id is not None

    sidebar = ChatSidebar()
    sidebar._agent = Agent(TestModel(custom_output_text="summary"), output_type=str)
    sidebar._active_session_id = session_id
    sidebar._message_history = [ModelRequest(parts=[UserPromptPart(content="Keep me")])]
    sidebar._run_compact_now = AsyncMock()

    sidebar._on_compact_clicked(sidebar._compact_btn)
    first_dialog = sidebar._open_dialog
    assert first_dialog is not None
    no_button = first_dialog.get_widget_for_response(Gtk.ResponseType.NO)
    assert no_button is not None and no_button.has_default()
    assert sidebar._busy is False
    first_dialog.response(Gtk.ResponseType.NO)
    sidebar._run_compact_now.assert_not_awaited()

    async def _confirm():
        sidebar._on_compact_clicked(sidebar._compact_btn)
        second_dialog = sidebar._open_dialog
        assert second_dialog is not None
        second_dialog.response(Gtk.ResponseType.YES)
        assert sidebar._compact_task is not None
        await sidebar._compact_task

    asyncio.run(_confirm())
    sidebar._run_compact_now.assert_awaited_once()


def test_project_directory_selector(tmp_path, monkeypatch):
    from grc_agent.chat_sidebar import ChatSidebar
    from grc_agent.settings import get_env_value

    env_file = tmp_path / ".env"
    monkeypatch.setenv("GRC_AGENT_ENV", str(env_file))

    proj_dir = tmp_path / "my_project"
    proj_dir.mkdir()

    sidebar = ChatSidebar()
    sidebar.set_project_directory(proj_dir)
    assert sidebar.get_project_directory() == proj_dir
    assert get_env_value("GRC_PROJECT_DIR") == str(proj_dir)


def test_welcome_view_clear_all_sessions_button(tmp_path, monkeypatch):
    from unittest.mock import MagicMock

    from gi.repository import Gtk
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    from grc_agent.db import init_db, save_session
    from grc_agent.ui.welcome_view import WelcomeView

    db_file = tmp_path / "chat_sessions.db"
    monkeypatch.setenv("GRC_AGENT_DB", str(db_file))
    init_db()
    grc_file = tmp_path / "test.grc"
    grc_file.write_text("options:\n  parameters:\n    id: test\n")
    save_session(
        None,
        str(grc_file),
        [ModelRequest(parts=[UserPromptPart(content="Test message")])],
    )

    listbox = Gtk.ListBox()
    clear_mock = MagicMock()
    welcome = WelcomeView(
        listbox,
        on_quick_prompt=MagicMock(),
        on_open_session=MagicMock(),
        on_delete_session=MagicMock(),
        on_clear_all_sessions=clear_mock,
    )
    welcome.render(current_page=None, active_session_id=None)

    # Find the Delete all sessions button in listbox children
    buttons = []

    def _find_buttons(widget):
        if isinstance(widget, Gtk.Button) and widget.get_label() == "Delete all sessions":
            buttons.append(widget)
        if isinstance(widget, Gtk.Container):
            for child in widget.get_children():
                _find_buttons(child)

    for row in listbox.get_children():
        _find_buttons(row)

    assert len(buttons) == 1
    buttons[0].clicked()
    clear_mock.assert_called_once()


def test_theme_toggle_and_persistence(tmp_path, monkeypatch):
    from gi.repository import Gtk

    from grc_agent.chat_sidebar import ChatSidebar
    from grc_agent.settings import get_theme_mode, load_settings, set_theme_mode
    from grc_agent.ui.code_block import CodeBlock
    from grc_agent.ui.css import apply_theme, get_code_style, is_dark_theme

    env_file = tmp_path / ".env"
    monkeypatch.setenv("GRC_AGENT_ENV", str(env_file))

    # Test settings persistence
    assert get_theme_mode() == "system"
    set_theme_mode("dark")
    assert get_theme_mode() == "dark"
    assert load_settings()["theme"] == "dark"

    # Test apply_theme
    apply_theme("dark")
    settings = Gtk.Settings.get_default()
    if settings:
        assert settings.get_property("gtk-application-prefer-dark-theme") is True
        assert is_dark_theme() is True
        assert get_code_style() == "monokai"

    apply_theme("light")
    if settings:
        assert settings.get_property("gtk-application-prefer-dark-theme") is False
        assert get_code_style() == "friendly"

    # Test sidebar theme button click
    sidebar = ChatSidebar()
    assert hasattr(sidebar, "_theme_btn")
    sidebar._on_theme_toggle_clicked()
    assert get_theme_mode() in ("dark", "light")

    # Test CodeBlock renders without crashing in either theme
    cb = CodeBlock("python", "import sys\nprint('hello')")
    assert cb is not None


def test_friendly_exhaustion_message():
    """Retry-budget turn deaths render a continuation message, not pydantic-
    ai's developer-aimed "Consider raising the max retry limit" text."""
    from pydantic_ai.exceptions import UnexpectedModelBehavior

    from grc_agent.chat_sidebar import _friendly_exhaustion_message

    tool_msg = _friendly_exhaustion_message(
        UnexpectedModelBehavior(
            "Tool 'change_graph' exceeded max retries count of 3. Consider raising the max retry limit."
        )
    )
    assert tool_msg is not None
    assert "change_graph" in tool_msg and "safe" in tool_msg and "Continue" in tool_msg

    out_msg = _friendly_exhaustion_message(
        UnexpectedModelBehavior("Exceeded maximum output retries (3).")
    )
    assert out_msg is not None and "validation" in out_msg

    assert _friendly_exhaustion_message(UnexpectedModelBehavior("other")) is None
    assert _friendly_exhaustion_message(ValueError("other")) is None


def test_chat_textviews_have_line_spacing():
    """The intern's "lack of spacing between the lines" — both CodeBlock and
    prose TextViews must carry GTK3's native pixel line spacing."""
    from gi.repository import Gtk

    from grc_agent.ui.code_block import CodeBlock
    from grc_agent.ui.markdown_view import MarkdownView

    cb = CodeBlock("python", "print('x')")

    def _find_textview(w):
        if isinstance(w, Gtk.TextView):
            return w
        if isinstance(w, Gtk.Container):
            for c in w.get_children():
                found = _find_textview(c)
                if found is not None:
                    return found
        return None

    tv = _find_textview(cb)
    assert tv is not None
    assert tv.get_pixels_above_lines() == 3
    assert tv.get_pixels_below_lines() == 3

    md = MarkdownView(Gtk.ListBox(), lambda: None)
    prose = md._make_prose_textview()
    assert prose.get_pixels_above_lines() == 2
    assert prose.get_pixels_below_lines() == 2
    assert prose.get_pixels_inside_wrap() == 2


def test_query_knowledge_label_shows_search_mode():
    from grc_agent.chat_sidebar import _tool_label

    assert (
        _tool_label("query_knowledge", result='{"search_mode": "vector"}')
        == "\u2699 query_knowledge (vector) \u2713"
    )
    assert (
        _tool_label("query_knowledge", result='{"search_mode": "lexical"}')
        == "\u2699 query_knowledge (lexical) \u2713"
    )
    assert (
        _tool_label("query_knowledge", ok=False, result='{"search_mode": "vector"}')
        == "\u2699 query_knowledge (vector) \u2717"
    )
    assert (
        _tool_label("query_knowledge", retry=True, result='{"search_mode": "lexical"}')
        == "\u26a0 query_knowledge (lexical) retry"
    )
    assert _tool_label("inspect_graph", result='{"ok": true}') == "\u2699 inspect_graph \u2713"


def test_markdown_ast_formatting_and_tags():
    """Verify AST node walker applies correct Gtk.TextBuffer tags and structure."""
    from gi.repository import Gtk

    from grc_agent.chat_sidebar import ChatSidebar

    sidebar = ChatSidebar()
    box = sidebar._start_agent_message()
    md_text = (
        "# Title Heading\n\n"
        "Normal text with **bold**, *italic*, ~~strike~~, and `inline_code`.\n\n"
        "> Blockquote line 1\n"
        "> Blockquote line 2\n\n"
        "* Bullet 1\n"
        "  * Sub-bullet A\n"
        "* Bullet 2\n\n"
        "1. Step 1\n"
        "2. Step 2\n"
        "   1. Substep 2.1"
    )
    sidebar._render_markdown_to_box(box, md_text, clear=True)

    textviews = [
        c.get_child()
        for c in box.get_children()
        if isinstance(c, Gtk.ScrolledWindow) and isinstance(c.get_child(), Gtk.TextView)
    ]
    assert len(textviews) == 1
    buf = textviews[0].get_buffer()
    content = buf.get_slice(buf.get_start_iter(), buf.get_end_iter(), True)

    assert "Title Heading" in content
    assert "bold" in content
    assert "italic" in content
    assert "strike" in content
    assert "inline_code" in content
    assert "│" in content or "Blockquote" in content
    assert "• Bullet 1" in content
    assert "• Sub-bullet A" in content
    assert "1. Step 1" in content
    assert "1. Substep 2.1" in content


def test_table_block_parse_table_ast():
    """Verify parse_table on SyntaxTreeNode handles standard and edge-case tables."""
    from markdown_it import MarkdownIt
    from markdown_it.tree import SyntaxTreeNode

    from grc_agent.ui.table_block import parse_table

    md = MarkdownIt("commonmark", {"html": False}).enable("table")

    # 1. Standard table with thead & tbody
    t1 = SyntaxTreeNode(md.parse("| Col A | Col B |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |"))
    headers, rows = parse_table(t1.children[0])
    assert headers == ["Col A", "Col B"]
    assert rows == [["1", "2"], ["3", "4"]]

    # 2. Ragged rows padded to uniform column count
    t2 = SyntaxTreeNode(md.parse("| H1 | H2 | H3 |\n|---|---|---|\n| val1 |\n| a | b | c |"))
    headers, rows = parse_table(t2.children[0])
    assert headers == ["H1", "H2", "H3"]
    assert rows == [["val1", "", ""], ["a", "b", "c"]]

    # 3. Header only
    t3 = SyntaxTreeNode(md.parse("| Only Header |\n|---|"))
    headers, rows = parse_table(t3.children[0])
    assert headers == ["Only Header"]
    assert rows == []


def test_markdown_list_hanging_indent_and_anchor_coverage():
    """Verify list items have contiguous tag coverage including child anchors,
    correct hanging indent properties, and single structural newlines."""
    from unittest.mock import MagicMock

    from gi.repository import Gtk

    from grc_agent.chat_sidebar import ChatSidebar

    sidebar = ChatSidebar()
    # Mock canvas manager with a live block name to trigger BlockBadge embedding
    mock_block = MagicMock()
    mock_block.name = "time_sink"
    mock_fg = MagicMock()
    mock_fg.blocks = [mock_block]
    mock_cm = MagicMock()
    mock_cm.current_flow_graph = mock_fg
    sidebar._get_cm = lambda: mock_cm
    sidebar._md._get_cm = lambda: mock_cm

    box = sidebar._start_agent_message()
    md_text = (
        "### Sinks\n"
        "* `time_sink` (`qtgui_time_sink_x`, 2 connections) — shows the **input carrier** and output.\n"
        "* `const_sink` — constellation display.\n\n"
        "### Notes\n"
        "* The PLL's lock range comfortably brackets the carrier, so it should lock cleanly."
    )
    sidebar._render_markdown_to_box(box, md_text, clear=True)

    textviews = [
        c.get_child()
        for c in box.get_children()
        if isinstance(c, Gtk.ScrolledWindow) and isinstance(c.get_child(), Gtk.TextView)
    ]
    assert len(textviews) == 1
    tv = textviews[0]
    buf = tv.get_buffer()

    # Verify list_depth_0 tag properties
    list_tag = buf.get_tag_table().lookup("list_depth_0")
    assert list_tag is not None
    assert list_tag.get_property("left-margin") == 24
    assert list_tag.get_property("indent") == -16
    assert list_tag.get_property("pixels-below-lines") == 2

    # Verify exact transitions: no unwanted blank lines between list items or before headings
    content = buf.get_slice(buf.get_start_iter(), buf.get_end_iter(), True)
    expected = (
        "Sinks\n"
        "• \ufffc (qtgui_time_sink_x, 2 connections) — shows the input carrier and output.\n"
        "• const_sink — constellation display.\n"
        "Notes\n"
        "• The PLL's lock range comfortably brackets the carrier, so it should lock cleanly.\n"
    )
    assert content == expected

    # Verify every character in the list lines (including anchor \ufffc) has list_tag applied
    it = buf.get_start_iter()
    while not it.is_end():
        char = it.get_char()
        if char == "•":
            line_end = it.copy()
            line_end.forward_to_line_end()
            check_it = it.copy()
            while check_it.compare(line_end) < 0:
                tags = check_it.get_tags()
                assert list_tag in tags, f"Missing list_tag at offset {check_it.get_offset()}"
                check_it.forward_char()
        it.forward_char()


def test_markdown_nested_list_ordered_markers_and_loose_lists():
    """Verify nested lists with wrapping, ordered markers (1, 9, 10, 100), and loose lists."""
    from gi.repository import Gtk

    from grc_agent.chat_sidebar import ChatSidebar

    sidebar = ChatSidebar()
    box = sidebar._start_agent_message()
    md_text = (
        "### Nested and Ordered\n"
        "* Top level bullet item with long text\n"
        "  * Nested bullet item depth 1\n"
        "    * Nested bullet item depth 2\n"
        "1. First step\n"
        "9. Ninth step\n"
        "10. Tenth step\n"
        "100. Hundredth step\n\n"
        "### Loose List\n"
        "* Item 1, paragraph A\n\n"
        "  Item 1, paragraph B\n"
        "* Item 2\n\n"
        "Follow-up standalone paragraph."
    )
    sidebar._render_markdown_to_box(box, md_text, clear=True)

    textviews = [
        c.get_child()
        for c in box.get_children()
        if isinstance(c, Gtk.ScrolledWindow) and isinstance(c.get_child(), Gtk.TextView)
    ]
    assert len(textviews) == 1
    tv = textviews[0]
    buf = tv.get_buffer()

    # Verify depth tags exist and scale margins
    tag0 = buf.get_tag_table().lookup("list_depth_0")
    tag1 = buf.get_tag_table().lookup("list_depth_1")
    tag2 = buf.get_tag_table().lookup("list_depth_2")
    assert tag0 is not None and tag0.get_property("left-margin") == 24
    assert tag1 is not None and tag1.get_property("left-margin") == 40
    assert tag2 is not None and tag2.get_property("left-margin") == 56

    content = buf.get_slice(buf.get_start_iter(), buf.get_end_iter(), True)
    assert "• Top level bullet item with long text\n" in content
    assert "• Nested bullet item depth 1\n" in content
    assert "• Nested bullet item depth 2\n" in content
    assert "1. First step\n" in content
    assert "9. Ninth step\n" in content
    assert "10. Tenth step\n" in content
    assert "100. Hundredth step\n" in content
    # Verify loose list separates paragraphs within item
    assert "• Item 1, paragraph A\nItem 1, paragraph B\n• Item 2\n" in content
    # Verify clean list -> paragraph transition without double blank line
    assert "• Item 2\nFollow-up standalone paragraph.\n" in content


def test_format_tool_summary_dispatch():
    """The approval card's per-tool summary renderer: change_graph keeps its
    dedicated formatter; run/shell tools get literal renderings; unknown
    tools fall back to one uniform bullet per argument."""
    from grc_agent.ui.approval_card import format_tool_summary

    # change_graph unchanged
    assert "**Add blocks:**" in format_tool_summary(
        "change_graph", {"add_blocks": [{"name": "lpf_0", "block_id": "x"}]}
    )
    # shell: the literal command in a fence (the command IS the reason)
    assert "cmake --build build" in format_tool_summary("run_command", {"command": "cmake --build build"})
    assert "background" in format_tool_summary("start_command", {"command": "uhd_usrp_probe"})
    # run_flowgraph: intent lines
    text = format_tool_summary("run_flowgraph", {"wait": True, "timeout_seconds": 30})
    assert "native Execute" in text and "30" in text
    text = format_tool_summary("run_flowgraph", {"wait": False})
    assert "until stopped" in text
    # uniform fallback for any other approval-gated tool
    assert "`key`: `value`" in format_tool_summary("some_future_tool", {"key": "value"})
    assert format_tool_summary("some_future_tool", {}) == "_No arguments._"


def test_approval_card_titles_and_summary_per_tool():
    """Widget construction of ApprovalCard for non-change_graph tools: the
    title and summary must describe the actual proposed action, not the
    change_graph fallback text."""
    from pydantic_ai.messages import ToolCallPart

    from grc_agent.ui.approval_card import ApprovalCard

    fired = []
    call = ToolCallPart(
        tool_name="run_command",
        args='{"command": "cmake --build build"}',
        tool_call_id="c1",
    )
    card = ApprovalCard(
        None,
        call,
        on_approve=lambda: fired.append("approve"),
        on_deny=lambda: fired.append("deny"),
        on_always_accept=lambda: fired.append("always"),
    )
    title = card.get_children()[0]
    assert "Proposed command" in title.get_text()
    # The command appears verbatim in the card's widget tree.
    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk

    found = []

    def _walk(widget):
        if isinstance(widget, Gtk.Label):
            if "cmake --build build" in (widget.get_text() or ""):
                found.append(True)
        elif hasattr(widget, "get_children"):
            for child in widget.get_children():
                _walk(child)

    _walk(card)
    assert found, "literal command not rendered anywhere in the card"

    # Buttons fire their callbacks.
    buttons = card.get_children()[-1]
    for button in buttons.get_children():
        button.emit("clicked")
    assert sorted(fired) == ["always", "approve", "deny"]


def test_shell_prefix_allow_is_session_scoped():
    """'Always allow <tok>' on a shell approval card approves that command's
    first token for the CURRENT session only — a different session id makes
    the granted set inert, without any reset wiring."""
    from pydantic_ai.messages import ToolCallPart

    from grc_agent.chat_sidebar import ChatSidebar

    sidebar = ChatSidebar()
    call = ToolCallPart(
        tool_name="run_command",
        args='{"command": "cmake --build build"}',
        tool_call_id="c1",
    )

    # Not granted yet.
    assert sidebar._shell_prefix_allowed(call) is False

    # Grant with session 7 active.
    sidebar._active_session_id = 7
    sidebar._always_allow_command({}, [], call)
    assert "cmake" in sidebar._shell_allowed_prefixes
    assert sidebar._shell_prefix_allowed(call) is True
    # A different command on the same token is covered; another token is not.
    same_token = ToolCallPart(
        tool_name="run_command", args='{"command": "cmake --install build"}', tool_call_id="c2"
    )
    other_token = ToolCallPart(
        tool_name="run_command", args='{"command": "make -j4"}', tool_call_id="c3"
    )
    assert sidebar._shell_prefix_allowed(same_token) is True
    assert sidebar._shell_prefix_allowed(other_token) is False
    # Background commands share the mechanism.
    bg = ToolCallPart(
        tool_name="start_command", args='{"command": "cmake --watch build"}', tool_call_id="c4"
    )
    assert sidebar._shell_prefix_allowed(bg) is True

    # Switching sessions (load/new/clear) makes the set inert.
    sidebar._active_session_id = 8
    assert sidebar._shell_prefix_allowed(call) is False

    # Non-shell tools are never prefix-allowed through this path.
    sidebar._active_session_id = 7
    graph_call = ToolCallPart(
        tool_name="change_graph", args='{"reason": "x"}', tool_call_id="c5"
    )
    assert sidebar._shell_prefix_allowed(graph_call) is False


def test_always_allow_command_resolves_matching_pending_futures():
    """The prefix-allow click approves the pending future(s) whose command
    shares the token and destroys exactly those cards."""
    import asyncio

    from pydantic_ai.messages import ToolCallPart

    from grc_agent.chat_sidebar import ChatSidebar
    from grc_agent.ui.approval_card import ApprovalCard

    sidebar = ChatSidebar()
    sidebar._active_session_id = 3
    cmake_call = ToolCallPart(
        tool_name="run_command", args='{"command": "cmake --build build"}', tool_call_id="c1"
    )
    make_call = ToolCallPart(
        tool_name="run_command", args='{"command": "make -j4"}', tool_call_id="c2"
    )
    pending = {
        "c1": asyncio.new_event_loop().create_future(),
        "c2": asyncio.new_event_loop().create_future(),
    }
    # Build real cards so the destroy path runs against real widgets.
    cards = [
        ApprovalCard(None, cmake_call, on_approve=lambda: None, on_deny=lambda: None, on_always_accept=lambda: None),
        ApprovalCard(None, make_call, on_approve=lambda: None, on_deny=lambda: None, on_always_accept=lambda: None),
    ]
    sidebar._always_allow_command(pending, cards, cmake_call)
    assert pending["c1"].done()  # same token: approved
    assert not pending["c2"].done()  # other token: still waiting
    # The persisted global gate is untouched.
    from grc_agent.settings import get_approval_mode

    assert get_approval_mode() in ("ask", "always")  # unchanged by this click


def test_block_badge_anchored_text_aligns_with_prose_baseline():
    """GTK3 child-anchor widgets stretch to the full line box and center
    their child, so an un-padded badge's label text rides ~4px above the
    sentence baseline — the 'superscript' look. The anchored badge's padded
    inner box must bring the label text onto the baseline; the table-cell
    badge (anchored=False) keeps the old centered look. Numeric regression
    test, not an eyeball check: the label center is measured against the
    TextView's own font baseline."""
    from gi.repository import Gtk

    from grc_agent.ui.block_badge import BlockBadge
    from grc_agent.ui.css import apply_css

    apply_css()  # the app's real rules (incl. .chat-block-badge-anchored)

    def measure(anchored):
        win = Gtk.Window()
        tv = Gtk.TextView()
        buf = tv.get_buffer()
        buf.set_text("The ")
        anchor = buf.create_child_anchor(buf.get_end_iter())
        buf.insert(buf.get_end_iter(), " variable")
        pill = BlockBadge("data_source", lambda: None, anchored=anchored)
        tv.add_child_at_anchor(pill, anchor)
        pill.show_all()
        win.add(tv)
        win.set_default_size(420, 90)
        win.show_all()
        win.realize()
        for _ in range(20):
            Gtk.main_iteration_do(False)
        rect = tv.get_iter_location(buf.get_iter_at_offset(4))
        pctx = tv.get_pango_context()
        font = tv.get_style_context().get_font(Gtk.StateFlags.NORMAL)
        metrics = pctx.get_metrics(font)
        baseline = rect.y + metrics.get_ascent() / 1024.0
        lbl = pill.get_child().get_children()[0] if anchored else pill.get_child()
        la = lbl.get_allocation()
        center = la.y + la.height / 2.0
        font_size = font.get_size() / 1024.0
        text_center = baseline - font_size * 0.25  # ~x-height/2 above baseline
        win.destroy()
        return center - text_center

    anchored_delta = measure(True)
    plain_delta = measure(False)
    assert abs(anchored_delta) <= 2.0, (
        f"anchored badge text off the prose baseline by {anchored_delta:+.1f}px"
    )
    assert plain_delta <= -2.0, (
        f"plain badge no longer superscript ({plain_delta:+.1f}px) — "
        "the anchor-stretch mechanism changed?"
    )
