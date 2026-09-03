"""Unit tests for chat_sidebar — split from the former test_unit.py god file.

Minimal set per the clustered test plan; shared fixtures/helpers live in conftest.py.
"""

import asyncio
import os
import time

import pytest
from conftest import _count_sessions_for_path, _seed_session


def _settle_events() -> None:
    """Drain the pending GTK event queue (idles, allocations, repaints) of
    the default display — the same loop pattern every widget test here uses."""
    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk

    # BOUNDED: ChatSidebar.__init__ arms a repeating 500ms poller (and a 60s
    # one) that is never removed, and every sidebar this file constructs stays
    # alive on its own poller source — once a few dozen are armed the default
    # display always has a ready source and an unbounded drain never exits
    # (observed live at this file's tail). 500 iterations settle every
    # layout/idle sequence in this suite with wide margin.
    n = 0
    while n < 500 and Gtk.events_pending():
        Gtk.main_iteration()
        n += 1


def test_change_summary_formatter():
    """The approval card's change_graph-JSON -> Markdown formatter.

    The payloads are built from the tool's own argument models rather than
    hand-written dicts: change_graph dumps BlockAdd/ParamUpdate/StateUpdate
    with model_dump(exclude_none=True) and the card renders that verbatim, so
    a hand-written shape lets the renderer read keys the tool never sends.
    """
    from grc_agent.agent import BlockAdd, ParamUpdate, StateUpdate
    from grc_agent.ui.approval_card import format_change_summary

    text = format_change_summary(
        {
            "add_blocks": [
                BlockAdd(
                    block_id="filter_low_pass_filter_x",
                    instance_name="lpf_0",
                    params={"cutoff": "19e3"},
                ).model_dump(exclude_none=True)
            ],
            "add_connections": ["src:0->lpf_0:0"],
            "force": True,
        }
    )
    assert "**Add blocks:**" in text and "`lpf_0` (`filter_low_pass_filter_x`)" in text
    assert "cutoff=19e3" in text
    assert "src:0 → lpf_0:0" in text  # cosmetic arrow
    assert "force" in text and "bypasses" in text
    assert "?" not in text

    # A block added already disabled must say so — the user is approving that
    # state too, and BlockAdd carries it.
    text = format_change_summary(
        {
            "add_blocks": [
                BlockAdd(
                    block_id="analog_noise_source_x",
                    instance_name="noise_0",
                    state="disabled",
                ).model_dump(exclude_none=True)
            ]
        }
    )
    assert "`noise_0`" in text and "disabled" in text
    # ...and a block added without an explicit state must not invent one.
    text = format_change_summary(
        {
            "add_blocks": [
                BlockAdd(
                    block_id="analog_sig_source_x", instance_name="tone_0"
                ).model_dump(exclude_none=True)
            ]
        }
    )
    assert "`tone_0`" in text
    assert "enabled" not in text and "disabled" not in text

    text = format_change_summary(
        {
            "update_params": [
                ParamUpdate(
                    instance_name="samp_rate", params={"value": "48000"}
                ).model_dump(exclude_none=True)
            ]
        }
    )
    assert "`samp_rate.value` = `48000`" in text
    assert "?" not in text

    # Every parameter in a multi-param update stays legible.
    text = format_change_summary(
        {
            "update_params": [
                ParamUpdate(
                    instance_name="sig_0",
                    params={"freq": "440", "amp": "0.5", "waveform": "analog.GR_SIN_WAVE"},
                ).model_dump(exclude_none=True)
            ]
        }
    )
    for frag in ("`sig_0.freq` = `440`", "`sig_0.amp` = `0.5`",
                 "`sig_0.waveform` = `analog.GR_SIN_WAVE`"):
        assert frag in text

    text = format_change_summary(
        {
            "update_states": [
                StateUpdate(instance_name="noise_0", state="disabled").model_dump()
            ]
        }
    )
    assert "`noise_0`" in text and "disabled" in text
    assert "?" not in text

    # A genuinely absent instance name must not raise.
    assert format_change_summary({"add_blocks": [{"block_id": "x"}]})
    assert format_change_summary({}) == "_No changes in this batch._"


def test_approval_mode_settings_helpers(tmp_path, monkeypatch):
    """The action approval gate persists via .env and supports manual, auto, and yolo."""
    from grc_agent.settings import get_approval_mode, set_approval_mode

    env_file = tmp_path / ".env"
    env_file.write_text("")
    monkeypatch.setenv("GRC_AGENT_ENV", str(env_file))
    monkeypatch.delenv("GRC_AGENT_APPROVE_CHANGES", raising=False)
    assert get_approval_mode() == "manual"  # default: manual
    set_approval_mode("auto")
    assert get_approval_mode() == "auto"
    set_approval_mode("yolo")
    assert get_approval_mode() == "yolo"
    set_approval_mode("bogus")
    assert get_approval_mode() == "yolo"  # invalid values are ignored
    set_approval_mode("manual")
    assert get_approval_mode() == "manual"


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


def test_open_recent_session_cleans_a_dangling_tool_call(tmp_path):
    """A session persisted mid-approval-pause (or by a pre-fix build) can end
    on a ModelResponse with an unfulfilled tool call — pydantic-ai rejects any
    new prompt sent against such a history. Loading it now cleans it at the
    read boundary, so U15's turn-start no longer needs to repair it again on
    every send (AGENTS.md section 1: fix state problems at the source)."""
    from unittest.mock import MagicMock, patch

    from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart, UserPromptPart

    from grc_agent.chat_sidebar import ChatSidebar
    from grc_agent.db import serialize_messages

    sidebar = ChatSidebar()
    proxy = MagicMock()
    cm = MagicMock()
    window = MagicMock()
    notebook = MagicMock()
    proxy._canvas_manager = cm
    cm.window = window
    window.notebook = notebook
    sidebar.set_flowgraph_proxy(proxy)

    file_real = tmp_path / "dangling.grc"
    file_real.touch()
    page = MagicMock()
    page.file_path = str(file_real)
    notebook.get_n_pages.return_value = 1
    notebook.get_nth_page.return_value = page

    dangling_messages = [
        ModelRequest(parts=[UserPromptPart(content="add a throttle block")]),
        ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="change_graph",
                    args={"add": []},
                    tool_call_id="call_1",
                )
            ]
        ),
    ]
    with patch("grc_agent.chat_sidebar.load_session") as mock_load:
        mock_load.return_value = {
            "id": 456,
            "grc_file_path": str(file_real),
            "messages": serialize_messages(dangling_messages),
            "created_at": "...",
            "updated_at": "...",
        }
        sidebar._on_recent_session_clicked(456)

    assert sidebar._message_history == dangling_messages[:1]


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
        "z-ai/glm-5.3-flash",
        is_default=False,
        base_url="https://openrouter.ai/api/v1",
    )
    assert sidebar._provider_label.get_text() == "OpenRouter · glm-5.3-flash"
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

    monkeypatch.setattr("grc_agent.chat.settings_controller.save_settings", _boom)

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

    from grc_agent.chat.stream_view import _ChunkAccumulator
    from grc_agent.chat_sidebar import ChatSidebar, _StreamCtx

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


def test_chunk_accumulator_replace_chunk():
    """replace_chunk patches an unflushed chunk in place without reordering
    -- the mechanism the copy-transcript fix uses to turn a call-only
    fragment into the combined call+result shape once the result arrives."""
    from grc_agent.chat.stream_view import _ChunkAccumulator

    acc = _ChunkAccumulator()
    acc.append("<Tool Call: x>\n")
    acc.append(" some unrelated text in between ")
    assert acc.replace_chunk("<Tool Call: x>\n", "<Tool Call: x>\nResult: y\n") is True
    assert str(acc) == "<Tool Call: x>\nResult: y\n some unrelated text in between "

    # A chunk that was already flushed (sent downstream) cannot be patched --
    # taking it back would silently un-send something the UI already painted.
    acc2 = _ChunkAccumulator()
    acc2.append("call-fragment")
    acc2.drain_new()
    acc2.append("more text")
    assert acc2.replace_chunk("call-fragment", "patched") is False
    assert str(acc2) == "call-fragmentmore text"

    # No match at all -> False, no mutation.
    acc3 = _ChunkAccumulator()
    acc3.append("something else")
    assert acc3.replace_chunk("not present", "x") is False
    assert str(acc3) == "something else"


def test_streaming_thinking_flush_throttled(monkeypatch):
    """Mirror of the text-flush test for the ThinkingPart branch: thinking
    tokens are throttled the same way and force=True flushes them."""
    from gi.repository import Gtk

    from grc_agent.chat.stream_view import _ChunkAccumulator
    from grc_agent.chat_sidebar import ChatSidebar, _StreamCtx

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
    """Thinking expander shows 'Thinking...' and expands during streaming, then collapses to 'Thought' when closed."""
    from gi.repository import Gtk

    from grc_agent.chat_sidebar import ChatSidebar, _StreamCtx

    sidebar = ChatSidebar()
    ctx = _StreamCtx(Gtk.Box())
    sidebar._ensure_thinking(ctx)
    exp = ctx.think_expander
    assert exp is not None
    assert exp.get_label() == "Thinking..."
    assert exp.get_expanded() is True

    sidebar._close_thinking(ctx)
    assert exp.get_label() == "Thought"
    assert exp.get_expanded() is False


def test_thinking_expander_label_codex_summary():
    """When the active provider is openai_codex, the thinking expander shows
    'Thinking (summary)...' while streaming and 'Thought summary (Codex)' when closed/reloaded."""
    from gi.repository import Gtk
    from pydantic_ai.messages import ModelResponse, ThinkingPart

    from grc_agent.chat_sidebar import ChatSidebar, _StreamCtx

    sidebar = ChatSidebar()
    sidebar.set_active_provider("openai_codex", "gpt-5.3-codex")

    # 1. Streaming lifecycle
    ctx = _StreamCtx(Gtk.Box())
    sidebar._ensure_thinking(ctx)
    exp = ctx.think_expander
    assert exp is not None
    assert exp.get_label() == "Thinking..." or exp.get_label() == "Thinking (summary)..."
    assert exp.get_expanded() is True

    sidebar._close_thinking(ctx)
    assert exp.get_label() == "Thought summary (Codex)"
    assert exp.get_expanded() is False

    # 2. Historical transcript render
    box = Gtk.Box()
    msg = ModelResponse(parts=[ThinkingPart(content="High level plan summary")])
    sidebar._render_last_message_rich(box, msg)
    found_exp = None
    for child in box.get_children():
        if isinstance(child, Gtk.Expander):
            found_exp = child
            break
    assert found_exp is not None
    assert found_exp.get_label() == "Thought summary (Codex)"
    assert found_exp.get_expanded() is False


def test_thinking_expander_auto_scroll_and_tall_sizing():
    """Thinking widget is configured with increased height and auto-scrolls during streaming."""
    from gi.repository import Gtk

    from grc_agent.chat.stream_view import _ChunkAccumulator
    from grc_agent.chat_sidebar import ChatSidebar, _StreamCtx

    sidebar = ChatSidebar()
    ctx = _StreamCtx(Gtk.Box())
    sidebar._ensure_thinking(ctx)

    exp = ctx.think_expander
    assert exp is not None
    assert exp.get_expanded() is True

    sw = getattr(exp, "_grc_scrolled", None)
    assert isinstance(sw, Gtk.ScrolledWindow)
    assert sw.get_min_content_height() >= 200
    assert sw.get_max_content_height() >= 750

    # Stream several chunks of thinking and verify delta buffer and mark movement
    ctx.think_acc = _ChunkAccumulator("First thought line\n")
    ctx.think_dirty = True
    sidebar._flush_thinking(ctx)

    buf = ctx.think_body.get_buffer()
    assert "First thought line" in buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True)
    assert buf.get_insert() is not None

    sidebar._close_thinking(ctx)
    assert exp.get_expanded() is False


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

    window = Gtk.OffscreenWindow()
    window.set_default_size(420, 760)
    window.add(sidebar)
    window.show_all()
    _settle_events()

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
    import grc_agent.adapter.rag as rag_mod
    from grc_agent.chat_sidebar import ChatSidebar

    sidebar = ChatSidebar()
    calls: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        sidebar,
        "set_status",
        lambda msg, *, error=False, background=False: calls.append((msg, error)),  # noqa: ARG005
    )

    rag_mod._rag_building.clear()
    try:
        # Idle: no domains -> no status writes.
        sidebar._poll_indexing()
        assert calls == []

        # Building: live progress shows counts.
        rag_mod._rag_building["catalog"] = {
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
        rag_mod._rag_building["catalog"]["current"] = 9
        sidebar._poll_indexing()
        assert calls[-1] == ("Indexing block library for search\u2026 9/10", False)

        # Transition to ready with indexed(8) < total(10): message uses indexed.
        rag_mod._rag_building["catalog"] = {
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
        rag_mod._rag_building["docs"] = {
            "status": "failed",
            "current": 0,
            "total": 0,
            "indexed": 0,
        }
        sidebar._poll_indexing()
        assert calls[-1][1] is True
    finally:
        rag_mod._rag_building.clear()


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
    # When user manually collapses the thinking container:
    ctx.think_expander.set_expanded(False)

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
    _settle_events()

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


def test_set_tool_result_streaming_agrees_with_history_render_on_failure():
    """A failed tool call must render with the failure marker both while
    streaming and after a full history re-render.

    _set_tool_result used to default to ok=True unconditionally, so a failed
    tool showed the success glyph mid-stream and only corrected itself once
    the turn ended and _render_last_message_rich re-derived ok from
    ret_part.outcome. Both paths must now agree on the very payload that
    used to diverge.
    """
    from gi.repository import Gtk
    from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart

    from grc_agent.chat.format import _tool_label
    from grc_agent.chat_sidebar import ChatSidebar

    sidebar = ChatSidebar()

    # The streaming path, for a FAILED result.
    exp = sidebar._make_tool_expander("get_run_log")
    sidebar._set_tool_result(exp, "boom: run monitor unavailable", ok=False)
    streaming_label = exp.get_label()
    expected = _tool_label("get_run_log", ok=False, result="boom: run monitor unavailable")
    assert streaming_label == expected
    assert "\u2717" in streaming_label  # the failure glyph, not success

    # The history path, for the identical call+result, via the real render.
    sidebar._message_history = [
        ModelResponse(
            parts=[ToolCallPart(tool_name="get_run_log", args={}, tool_call_id="c1")]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="get_run_log",
                    content="boom: run monitor unavailable",
                    tool_call_id="c1",
                    outcome="failed",
                )
            ]
        ),
    ]
    sidebar._render_history()

    history_labels = [
        w.get_label()
        for w in _iter_widgets(sidebar._listbox)
        if isinstance(w, Gtk.Expander)
    ]
    assert streaming_label in history_labels, (streaming_label, history_labels)


def test_streaming_copy_transcript_matches_history_render_shape():
    """The copied transcript must be identical whether taken mid-stream or
    after re-render.

    The call fragment and its result used to be two separately-appended,
    differently-tagged blocks in the streaming path (<Tool Call: ...> then a
    LATER, separate <Tool Result: ...>), while the history path always
    produced one combined block (<Tool Call: ...> with its own Result: line)
    -- a divergence the streaming helper's own docstring admitted rather than
    fixed. Both paths must now build the exact same fragment.
    """
    from gi.repository import Gtk
    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        PartStartEvent,
        ToolCallPart,
        ToolReturnPart,
    )

    from grc_agent.chat.format import _transcript_tool_call
    from grc_agent.chat_sidebar import ChatSidebar, _StreamCtx

    sidebar = ChatSidebar()
    ctx = _StreamCtx(Gtk.Box())

    call_part = ToolCallPart(
        tool_name="change_graph", args={"reason": "add lpf"}, tool_call_id="c1"
    )
    sidebar._on_part_start(ctx, PartStartEvent(index=0, part=call_part))

    result_part = ToolReturnPart(
        tool_name="change_graph", content='{"ok": true}', tool_call_id="c1", outcome="success"
    )
    # This is exactly what _stream_tools' FunctionToolResultEvent handler
    # does with event.part -- exercised directly since driving the real
    # handler needs a live node.stream() async generator.
    sidebar._set_tool_result(
        ctx.tools["c1"], str(result_part.content), ok=result_part.outcome != "failed"
    )
    sidebar._record_tool_result_transcript(ctx, "c1", str(result_part.content))

    streaming_transcript = str(ctx.full_raw_text)
    expected = _transcript_tool_call("change_graph", '{"reason":"add lpf"}', '{"ok": true}')
    assert streaming_transcript == expected

    # The history path, for the identical call+result.
    sidebar2 = ChatSidebar()
    sidebar2._message_history = [
        ModelResponse(parts=[call_part]),
        ModelRequest(parts=[result_part]),
    ]
    sidebar2._render_history()
    rows = list(sidebar2._listbox.get_children())
    box = rows[-1].get_children()[0]
    history_copy_text = getattr(box, "_grc_copy_btn", None)
    history_text = getattr(history_copy_text, "_grc_copy_text", None) if history_copy_text else None
    if history_text is None:
        # The copy button lives on the outer row, not the inner box, in some
        # layouts -- fall back to walking for it.
        for w in _iter_widgets(rows[-1]):
            if hasattr(w, "_grc_copy_text"):
                history_text = w._grc_copy_text
                break
    assert history_text == streaming_transcript == expected


def test_tool_expander_toggle_keeps_auto_scroll_intent():
    """Toggling a tool expander must not permanently disable auto-scroll
    follow. Scroll compensation (see the anchor test below) prevents the
    jump, and the value-changed tracker is the only authority on
    stickiness — an expand in an older message must not silently kill
    follow for the rest of the conversation."""
    from grc_agent.chat_sidebar import ChatSidebar

    sidebar = ChatSidebar()
    sidebar._auto_scroll = True
    exp = sidebar._make_tool_expander("inspect_graph")

    # Simulate GTK notify::expanded signal (unmapped sidebar: no value
    # changes fire, so the intent flag must be untouched).
    exp.set_expanded(True)
    assert sidebar._auto_scroll is True
    exp.set_expanded(False)
    assert sidebar._auto_scroll is True


def test_expander_toggle_anchor_compensation():
    """Expanding a container above the viewport must not move the visible
    content: the vadjustment value is compensated by the row's height delta
    (the same anchoring Polari applies to prepended log entries)."""
    from gi.repository import Gtk

    from grc_agent.chat_sidebar import ChatSidebar

    sidebar = ChatSidebar()
    win = Gtk.OffscreenWindow()
    win.set_default_size(400, 300)
    win.add(sidebar)
    # A couple of filler rows, then the expander row ABOVE the parked
    # viewport position, then bulk content below it.
    for i in range(2):
        sidebar._add_message_row(Gtk.Label(label=f"filler {i}\n" * 4))
    exp = sidebar._make_tool_expander("inspect_graph")
    sidebar._set_tool_result(exp, "line of tool output\n" * 80)
    sidebar._add_message_row(exp)
    for i in range(16):
        sidebar._add_message_row(Gtk.Label(label=f"below {i}\n" * 4))
    win.show_all()
    _settle_events()

    adj = sidebar._scrolled.get_vadjustment()
    row = exp.get_ancestor(Gtk.ListBoxRow)
    assert row is not None and row.get_allocated_height() > 0

    # Park mid-view, well away from the bottom, with the row above the fold.
    adj.set_value(300.0)
    value_before = adj.get_value()
    before = row.get_allocation()
    assert before.y + before.height <= value_before

    exp.set_expanded(True)
    _settle_events()

    after = row.get_allocation()
    delta = (after.y + after.height) - (before.y + before.height)
    assert delta > 0  # the row actually grew
    # The same visible content stays in view: value shifted by the delta.
    assert abs(adj.get_value() - (value_before + delta)) <= 1.0
    win.destroy()


def test_auto_scroll_intent_tracks_adjustment_value():
    """_auto_scroll follows the vadjustment value (wheel, drag, keyboard —
    all sources), not scroll-event delivery."""
    from gi.repository import Gtk

    from grc_agent.chat_sidebar import ChatSidebar

    sidebar = ChatSidebar()
    win = Gtk.OffscreenWindow()
    win.set_default_size(400, 300)
    win.add(sidebar)
    for i in range(10):
        sidebar._add_message_row(Gtk.Label(label=f"msg {i}\n" * 5))
    win.show_all()
    _settle_events()

    adj = sidebar._scrolled.get_vadjustment()
    assert adj.get_upper() - adj.get_page_size() > 200  # actually scrollable

    # Simulate a scrollbar drag / keyboard scroll: direct value change,
    # no scroll-event is emitted for these.
    adj.set_value((adj.get_upper() - adj.get_page_size()) / 2)
    assert sidebar._auto_scroll is False
    # Content growth alone (upper change, value unchanged) never flips the
    # intent: streaming appends cannot yank a reader back to the bottom.
    sidebar._add_message_row(Gtk.Label(label="appended\n" * 10))
    _settle_events()
    assert sidebar._auto_scroll is False
    # Scrolling back to the bottom re-engages follow.
    adj.set_value(adj.get_upper() - adj.get_page_size())
    assert sidebar._auto_scroll is True
    win.destroy()


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
    captured["coro"].close()


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
    monkeypatch.setitem(_af._CTX_PROBES, "ollama_cloud", lambda _model: 1024 * 1024)
    assert resolve_model_context_length("ollama_cloud", "glm-5.3-flash:cloud") == 1_048_576
    _context_length_cache.clear()
    _context_negative_cache.clear()

    sidebar = ChatSidebar()
    sidebar.set_active_provider("ollama_cloud", "glm-5.3-flash:cloud")

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

    # The label reads a cache the probe fills off-loop; it never makes the
    # blocking HTTP call itself, because it runs inside the agent.iter() node
    # loop after every node. Before the probe lands there is simply no
    # denominator to show.
    sidebar._update_context_label()
    assert "3.3k tok" in sidebar._context_label.get_label()

    sidebar._context_window_cache[("ollama_cloud", "glm-5.3-flash:cloud")] = 1_048_576
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
    tag = buffer.get_tag_table().lookup("block_badge_test_block_x")
    assert tag is not None
    assert getattr(tag, "grc_block_name", "") == "test_block_x"
    slice_text = buffer.get_slice(buffer.get_start_iter(), buffer.get_end_iter(), True)
    assert "test_block_x" in slice_text


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

    win = Gtk.OffscreenWindow()
    win.set_default_size(700, 400)
    sidebar = ChatSidebar()
    win.add(sidebar)
    win.show_all()
    _settle_events()

    box = sidebar._start_agent_message()
    long_text = (
        "This is a long sentence meant to exercise word wrapping across a "
        "realistically wide chat column so it does not collapse to one word "
        "per line."
    )
    sidebar._render_markdown_to_box(box, long_text, clear=True)
    _settle_events()

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

    win = Gtk.OffscreenWindow()
    win.set_default_size(900, 500)
    win.add(sidebar)
    win.show_all()
    _settle_events()

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
        win = Gtk.OffscreenWindow()
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
        _settle_events()
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
            _settle_events()

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
            _settle_events()
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

    win = Gtk.OffscreenWindow()
    sidebar = ChatSidebar()
    win.add(sidebar)
    win.show_all()
    _settle_events()

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

    win = Gtk.OffscreenWindow()
    win.set_default_size(1000, 800)
    sidebar = ChatSidebar()
    win.add(sidebar)
    win.show_all()
    _settle_events()

    box = sidebar._start_agent_message()
    sidebar._render_markdown_to_box(box, "```\n" + diagram + "\n```\n", clear=True)
    # An offscreen widget is only allocated during the frame-clock-driven
    # pass, which an events-pending drain does not run to completion — the
    # same reason the font-scaling tests use this helper. Without it the
    # TextView reports an allocated height of 1 and the assertion below reads
    # as "diagram clipped" when in fact nothing has been laid out yet.
    _run_settle_frames()

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

    from grc_agent.chat_sidebar import ChatSidebar
    from grc_agent.db import init_db, save_session
    from grc_agent.ui.welcome_view import WelcomeView

    env_file = tmp_path / ".env"
    monkeypatch.setenv("GRC_AGENT_ENV", str(env_file))
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

    def _walk(widget):
        yield widget
        if isinstance(widget, Gtk.Container):
            for child in widget.get_children():
                yield from _walk(child)

    all_widgets = list(_walk(listbox))
    header_labels = [
        w for w in all_widgets if isinstance(w, Gtk.Label) and "Recent" in w.get_text()
    ]
    assert len(header_labels) == 1
    assert "Recent (1)" in header_labels[0].get_text()

    buttons = [
        w
        for w in all_widgets
        if isinstance(w, Gtk.Button) and w.get_label() == "Delete all sessions"
    ]
    assert len(buttons) == 1
    buttons[0].clicked()
    clear_mock.assert_called_once()

    # Also test with real ChatSidebar._on_clear_history_clicked handler (prevent TypeError regression)
    sidebar = ChatSidebar()
    welcome_real = WelcomeView(
        listbox,
        on_quick_prompt=MagicMock(),
        on_open_session=MagicMock(),
        on_delete_session=MagicMock(),
        on_clear_all_sessions=sidebar._on_clear_history_clicked,
    )
    for child in listbox.get_children():
        listbox.remove(child)
    welcome_real.render(current_page=None, active_session_id=None)

    real_btn = [
        w for w in _walk(listbox) if isinstance(w, Gtk.Button) and w.get_label() == "Delete all sessions"
    ]
    assert len(real_btn) == 1
    sidebar._on_clear_history_clicked()  # 0 args
    if sidebar._open_dialog:
        sidebar._open_dialog.destroy()
        sidebar._open_dialog = None


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
        "• time_sink (qtgui_time_sink_x, 2 connections) — shows the input carrier and output.\n"
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

    assert get_approval_mode() in ("manual", "auto", "yolo")  # unchanged by this click


def test_block_badge_prose_text_aligns_with_baseline():
    """Prose block badges rendered with native TextTags are perfectly aligned
    with the surrounding sentence baseline (0px vertical offset). Table cells
    use BlockBadge pills for interactive box containers."""
    from unittest.mock import MagicMock

    from gi.repository import Gtk

    from grc_agent.ui.block_badge import BlockBadge
    from grc_agent.ui.css import apply_css
    from grc_agent.ui.markdown_view import MarkdownView

    apply_css()

    mock_cm = MagicMock()
    mock_fg = MagicMock()
    mock_block = MagicMock()
    mock_block.name = "data_source"
    mock_fg.blocks = [mock_block]
    mock_cm.current_flow_graph = mock_fg

    listbox = Gtk.ListBox()
    md = MarkdownView(listbox, lambda: mock_cm)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    md.render(box, "The `data_source` block generates samples.")

    textviews = [
        c.get_child()
        for c in box.get_children()
        if isinstance(c, Gtk.ScrolledWindow) and isinstance(c.get_child(), Gtk.TextView)
    ]
    assert len(textviews) == 1
    tv = textviews[0]
    buf = tv.get_buffer()

    # Verify block tag was created and applied to data_source
    tag = buf.get_tag_table().lookup("block_badge_data_source")
    assert tag is not None
    assert getattr(tag, "grc_block_name", "") == "data_source"

    win = Gtk.OffscreenWindow()
    win.add(box)
    win.set_default_size(420, 90)
    win.show_all()
    win.realize()
    for _ in range(20):
        Gtk.main_iteration_do(False)

    rect_the = tv.get_iter_location(buf.get_iter_at_offset(0))
    rect_badge = tv.get_iter_location(buf.get_iter_at_offset(5))
    rect_block = tv.get_iter_location(buf.get_iter_at_offset(18))

    win.destroy()

    assert rect_the.y == rect_badge.y == rect_block.y, (
        f"Badge text y={rect_badge.y} not on sentence y={rect_the.y}"
    )

    # Verify BlockBadge widget for table cells
    pill = BlockBadge("data_source", lambda: mock_cm)
    assert pill.name == "data_source"


def test_request_approvals_yolo_mode(tmp_path, monkeypatch):
    """In 'yolo' approval mode, _request_approvals auto-approves all tools immediately without UI."""
    import asyncio
    from unittest.mock import MagicMock

    from pydantic_ai.messages import ToolCallPart
    from pydantic_ai.tools import DeferredToolRequests, ToolApproved

    from grc_agent.chat_sidebar import ChatSidebar

    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))
    monkeypatch.setenv("GRC_AGENT_APPROVE_CHANGES", "yolo")

    async def _test():
        sidebar = ChatSidebar()
        fg_call = ToolCallPart("change_graph", {"reason": "test edit"}, tool_call_id="call_fg")
        sh_call = ToolCallPart("run_command", {"command": "ls -la"}, tool_call_id="call_sh")
        output = DeferredToolRequests(approvals=[fg_call, sh_call])

        ctx = MagicMock()
        results = await sidebar._request_approvals(ctx, output)
        assert "call_fg" in results.approvals
        assert isinstance(results.approvals["call_fg"], ToolApproved)
        assert "call_sh" in results.approvals
        assert isinstance(results.approvals["call_sh"], ToolApproved)
        assert ctx.box.pack_start.call_count == 0  # No UI cards created

    asyncio.run(_test())


def test_request_approvals_auto_mode_auto_approves_flowgraph_only(tmp_path, monkeypatch):
    """In 'auto' mode, flowgraph mutations are auto-approved while shell execution still asks."""
    import asyncio
    import contextlib
    from unittest.mock import MagicMock

    from pydantic_ai.messages import ToolCallPart
    from pydantic_ai.tools import DeferredToolRequests, ToolApproved

    from grc_agent.chat_sidebar import ChatSidebar

    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))
    monkeypatch.setenv("GRC_AGENT_APPROVE_CHANGES", "auto")

    async def _test():
        sidebar = ChatSidebar()
        # 1. Flowgraph-only batch: auto-approves immediately without UI
        fg_call = ToolCallPart("change_graph", {"reason": "test edit"}, tool_call_id="call_fg")
        run_fg_call = ToolCallPart("run_flowgraph", {"action": "start"}, tool_call_id="call_run_fg")
        output_fg = DeferredToolRequests(approvals=[fg_call, run_fg_call])

        ctx_fg = MagicMock()
        results_fg = await sidebar._request_approvals(ctx_fg, output_fg)
        assert isinstance(results_fg.approvals["call_fg"], ToolApproved)
        assert isinstance(results_fg.approvals["call_run_fg"], ToolApproved)
        assert ctx_fg.box.pack_start.call_count == 0

        # 2. Shell call in auto mode: still creates approval card
        sh_call = ToolCallPart("run_command", {"command": "echo test"}, tool_call_id="call_sh")
        output_sh = DeferredToolRequests(approvals=[sh_call])
        ctx_sh = MagicMock()
        # Start _request_approvals task
        task = asyncio.create_task(sidebar._request_approvals(ctx_sh, output_sh))
        for _ in range(5):
            await asyncio.sleep(0.01)
        assert ctx_sh.box.pack_start.call_count == 1  # Card was added to UI
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(_test())


def test_approval_mode_button_cycles_and_persists(tmp_path, monkeypatch):
    """Clicking the mode button cycles Manual -> Auto -> YOLO -> Manual."""
    from grc_agent.chat_sidebar import ChatSidebar
    from grc_agent.settings import get_approval_mode

    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))
    monkeypatch.delenv("GRC_AGENT_APPROVE_CHANGES", raising=False)

    sidebar = ChatSidebar()
    assert get_approval_mode() == "manual"
    assert sidebar._approval_toggle.get_label() == "Mode: Manual"

    # Click 1: Manual -> Auto
    sidebar._approval_toggle.clicked()
    assert get_approval_mode() == "auto"
    assert sidebar._approval_toggle.get_label() == "Mode: Auto"

    # Click 2: Auto -> YOLO
    sidebar._approval_toggle.clicked()
    assert get_approval_mode() == "yolo"
    assert sidebar._approval_toggle.get_label() == "Mode: YOLO"

    # Click 3: YOLO -> Manual
    sidebar._approval_toggle.clicked()
    assert get_approval_mode() == "manual"
    assert sidebar._approval_toggle.get_label() == "Mode: Manual"


def test_confirm_yes_no_non_blocking(tmp_path, monkeypatch):
    """_confirm_yes_no must use non-blocking signals and fire callback on response."""
    from gi.repository import Gtk

    from grc_agent.chat_sidebar import ChatSidebar

    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))
    sidebar = ChatSidebar()

    cb_results = []
    sidebar._confirm_yes_no(
        None,
        title="Test Title",
        body="Test Body",
        on_response=lambda res: cb_results.append(res),
    )
    assert sidebar._open_dialog is not None
    # Simulate user clicking YES
    sidebar._open_dialog.response(Gtk.ResponseType.YES)
    assert cb_results == [True]
    assert sidebar._open_dialog is None


def test_send_fix_when_free_waits_for_idle(tmp_path, monkeypatch):
    """_send_fix_when_free must wait for _idle_event before dispatching fix."""
    import asyncio
    from unittest.mock import MagicMock

    from grc_agent.chat_sidebar import ChatSidebar

    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))

    async def _test_flow():
        sidebar = ChatSidebar()
        proxy = MagicMock()
        proxy._canvas_manager.current_page = "page1"
        sidebar.set_flowgraph_proxy(proxy)
        dispatched = []
        sidebar.send_message = lambda text: dispatched.append(text) or True

        # Mark sidebar busy
        sidebar._set_busy(True)
        assert not sidebar._idle_event.is_set()

        task = asyncio.create_task(sidebar._send_fix_when_free("Fix prompt", "page1"))
        # Yield control so task runs up to await _idle_event.wait()
        for _ in range(5):
            await asyncio.sleep(0.01)
        assert len(dispatched) == 0  # not yet dispatched while busy
        sidebar._set_busy(False)  # set idle
        await task
        assert dispatched == ["Fix prompt"]

    asyncio.run(_test_flow())


def _write_test_png(path) -> None:
    """Write a real 1x1 PNG through GdkPixbuf (same loader the sidebar uses)."""
    import gi

    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf

    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 1, 1)
    pixbuf.fill(0xFF0000FF)
    pixbuf.savev(str(path), "png", [], [])


def _iter_widgets(widget):
    """Yield every widget in a GTK container tree (depth-first)."""
    from gi.repository import Gtk

    stack = [widget]
    while stack:
        w = stack.pop()
        yield w
        if isinstance(w, Gtk.Container):
            stack.extend(w.get_children())


def test_composer_image_attachments(tmp_path):
    """Attach flow: chips appear, sensitivity admits image-only sends, prompt
    is built as a multimodal list, and the composer resets after dispatch."""
    from grc_agent.chat_sidebar import ChatSidebar

    sidebar = ChatSidebar()
    sidebar._entry.set_sensitive(True)

    img_path = tmp_path / "dot.png"
    _write_test_png(img_path)

    # No attachments: blank text keeps Send disabled.
    sidebar._update_send_sensitivity()
    assert not sidebar._send_btn.get_sensitive()

    # Attach button exists and is wired into the composer.
    assert sidebar._attach_btn is not None
    assert sidebar._attach_btn in sidebar._input_box.get_children()

    # Adding an attachment queues it; the caller refreshes chips once per
    # batch (the chooser response handler does the same).
    sidebar._add_attachment(str(img_path))
    sidebar._refresh_attachment_chips()
    assert len(sidebar._attachments) == 1
    assert sidebar._attachments[0].media_type == "image/png"
    assert sidebar._attachments[0].data.startswith(b"\x89PNG")
    assert sidebar._attachment_row.get_children()
    sidebar._update_send_sensitivity()
    assert sidebar._send_btn.get_sensitive()

    # Removing the chip restores the disabled state.
    sidebar._remove_attachment(0)
    assert sidebar._attachments == []
    assert not sidebar._attachment_row.get_children()
    assert not sidebar._attachment_row.get_visible()
    sidebar._update_send_sensitivity()
    assert not sidebar._send_btn.get_sensitive()

    # Dispatch builds [text, *attachments] and clears the composer.
    captured = {}

    async def fake_turn(prompt):
        captured["prompt"] = prompt

    async def flow():
        sidebar._run_agent_turn = fake_turn
        sidebar._add_attachment(str(img_path))
        sidebar._refresh_attachment_chips()
        sidebar._entry.set_text("what is this?")
        sidebar._dispatch_send()
        await asyncio.sleep(0)

    asyncio.run(flow())
    assert isinstance(captured["prompt"], list)
    assert captured["prompt"][0] == "what is this?"
    assert captured["prompt"][1].media_type == "image/png"
    assert sidebar._attachments == []
    assert sidebar._entry.get_text() == ""


def test_send_message_multimodal_and_image_only(tmp_path):
    """send_message accepts a multimodal prompt list, renders the user bubble
    with a thumbnail, and allows an image-only turn with blank text."""
    from collections.abc import Sequence

    from gi.repository import Gtk
    from pydantic_ai.messages import BinaryContent

    from grc_agent.chat_sidebar import ChatSidebar

    sidebar = ChatSidebar()
    img_path = tmp_path / "dot2.png"
    _write_test_png(img_path)
    img = BinaryContent(data=img_path.read_bytes(), media_type="image/png")

    captured = []

    async def fake_turn(prompt):
        captured.append(prompt)

    async def flow():
        sidebar._run_agent_turn = fake_turn
        assert sidebar.send_message(["look", img])
        for _ in range(5):
            await asyncio.sleep(0.01)  # fake turn completes; done-callback clears busy
        assert sidebar.send_message([img])  # image-only, blank text
        await asyncio.sleep(0.01)

    asyncio.run(flow())
    assert len(captured) == 2
    first = captured[0]
    assert isinstance(first, Sequence)
    assert not isinstance(first, str)
    assert first[0] == "look"
    assert isinstance(first[1], BinaryContent)
    assert captured[1] == [img]  # image-only turn, no stray text piece

    # The user bubble for a multimodal turn contains a rendered image.
    assert any(
        isinstance(w, Gtk.Image)
        for row in sidebar._listbox.get_children()
        for w in _iter_widgets(row.get_child())
    )


def test_render_history_multimodal(tmp_path):
    """A reloaded session containing an image-bearing user message renders
    the text plus a thumbnail in the user bubble."""
    from gi.repository import Gtk
    from pydantic_ai.messages import BinaryContent

    from grc_agent.chat_sidebar import ChatSidebar
    from grc_agent.db import user_request

    sidebar = ChatSidebar()
    img_path = tmp_path / "hist.png"
    _write_test_png(img_path)
    img = BinaryContent(data=img_path.read_bytes(), media_type="image/png")

    sidebar._message_history = [user_request(["hello from history", img])]
    sidebar._render_history()
    assert any(
        isinstance(w, Gtk.Image)
        for row in sidebar._listbox.get_children()
        for w in _iter_widgets(row.get_child())
    )


def test_drag_drop_registration_and_batch_attach(tmp_path):
    """The sidebar is a URI-list drop target, and _attach_paths queues a whole
    batch of files with one refresh (the same seam the chooser and drag use)."""
    from gi.repository import Gdk

    from grc_agent.chat_sidebar import ChatSidebar

    sidebar = ChatSidebar()
    targets = sidebar.drag_dest_get_target_list()
    assert targets is not None
    assert targets.find(Gdk.atom_intern("text/uri-list", False))

    img_a, img_b = tmp_path / "a.png", tmp_path / "b.png"
    _write_test_png(img_a)
    _write_test_png(img_b)

    sidebar._entry.set_sensitive(True)
    sidebar._attach_paths([str(img_a), str(img_b)])
    assert len(sidebar._attachments) == 2
    assert len(sidebar._attachment_row.get_children()) == 2
    sidebar._update_send_sensitivity()
    assert sidebar._send_btn.get_sensitive()  # image-only send enabled


# -- chat zoom input + sidebar font projection (R9/R10/R12, KD2) ------------


def _run_settle_frames(ms: int = 400) -> None:
    """Run a bounded real main-loop window so GTK's frame-clock pass settles.

    Cached PangoContexts only adopt a changed CSS font during the
    frame-clock-driven style pass, which a bare events-pending drain does not
    run to completion on offscreen widgets; a short live loop is the same
    settle the running app gets from its next frame.

    This is a wall-clock window, not an iteration count, so it is sensitive
    to system load: under a busier machine (e.g. running after ~100 other
    widget-heavy tests in the same file) fewer real loop iterations fit in
    the window, and a too-tight bound occasionally lets the idle-priority
    repin callback miss its turn — observed as a one-off flake, not an
    order-dependent failure (reproduced clean across 3 repeats at 150ms and
    also failed once at 150ms with identical test order, so it is timing-
    sensitive rather than deterministic). 400ms buys generous headroom for
    ~4 call sites at negligible suite-time cost."""
    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import GLib, Gtk

    def _stop() -> bool:
        Gtk.main_quit()
        return False

    GLib.timeout_add(ms, _stop)
    Gtk.main()


def _wait_until(predicate, timeout_s: float = 3.0) -> bool:
    """Poll a condition by pumping the main loop, instead of a blind wall-clock
    wait. _run_settle_frames' fixed window is sensitive to system load — under
    heavy load from many preceding widget-heavy tests (observed: reproducible
    once test_desktop_app.py's ten window-constructing tests all run first)
    a fixed real-time budget can be too short even after widening it, while a
    condition-based wait succeeds the instant the repin actually lands and
    only fails if it genuinely never does. Uses _settle_events' BOUNDED drain,
    never a bare `while Gtk.events_pending()` — an unbounded drain can spin
    forever once enough repeating sources are armed on the shared context."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        _run_settle_frames(ms=50)
        _settle_events()
    return predicate()


def test_zoom_projection_css_rule_scope_clamp_and_theme_restore():
    """R9/KTD8: the projection is ONE scoped CssProvider on the SIDEBAR's
    style context (never the screen) carrying ONE recalculated absolute
    .chat-sidebar font-size rule; values follow sidebar_font_multiplier
    (clamped 0.7..1.8 x the measured theme base), zoom 1.0 restores the
    measured theme default exactly (base x 1.0), and later projections
    reload the same provider instead of remove/re-add."""
    import re

    from gi.repository import Gtk

    from grc_agent.chat_sidebar import ChatSidebar
    from grc_agent.native_canvas import sidebar_font_multiplier

    sidebar = ChatSidebar()
    win = Gtk.OffscreenWindow()
    win.set_default_size(400, 300)
    win.add(sidebar)
    for i in range(20):
        sidebar._add_message_row(Gtk.Label(label=f"msg {i}\n" * 4))
    win.show_all()
    _settle_events()

    # Capture the theme font BEFORE any projection — zoom 1.0 must land back
    # on exactly this resolved description.
    font_before = sidebar.get_style_context().get_font(Gtk.StateFlags.NORMAL).to_string()
    assert sidebar._zoom_css_provider is None  # nothing projected yet

    screen_adds = []
    widget_adds = []
    orig_screen_add = Gtk.StyleContext.add_provider_for_screen
    orig_widget_add = Gtk.StyleContext.add_provider
    try:
        Gtk.StyleContext.add_provider_for_screen = staticmethod(
            lambda *args: screen_adds.append(args)
        )
        Gtk.StyleContext.add_provider = lambda ctx, provider, prio: widget_adds.append(
            (ctx, provider, prio)
        )

        def css_px() -> float:
            css = sidebar._zoom_css_provider.to_string()
            m = re.search(r"font-size: ([0-9.]+)px", css)
            assert m is not None, css
            assert ".chat-sidebar" in css
            return float(m.group(1))

        sidebar.set_zoom_projection(2.25)  # sqrt -> multiplier 1.5
        assert sidebar._zoom_css_provider is not None
        base = sidebar._zoom_css_base_px
        assert base is not None and base > 0
        assert abs(css_px() - base * sidebar_font_multiplier(2.25)) < 0.01

        # The clamped mapping, exercised across GRC's native range and beyond
        # (tolerance covers the 4-decimal CSS formatting precision).
        for zoom in (0.1, 0.5, 1.0, 2.25, 5.0, 10.0):
            sidebar.set_zoom_projection(zoom)
            assert 0.7 * base - 1e-3 <= css_px() <= 1.8 * base + 1e-3

        # Zoom 1.0: base x 1.0 == the measured theme default, and the sidebar's
        # resolved style font reads back exactly as before any projection.
        sidebar.set_zoom_projection(1.0)
        assert abs(css_px() - base) < 0.005
        assert (
            sidebar.get_style_context().get_font(Gtk.StateFlags.NORMAL).to_string()
            == font_before
        )

        # Scope: attached to THIS sidebar's style context exactly once, at
        # application priority; the screen-level provider list is untouched.
        assert screen_adds == []
        assert len(widget_adds) == 1, widget_adds
        ctx, _provider, prio = widget_adds[0]
        assert ctx is sidebar.get_style_context()
        assert prio == Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    finally:
        # Reassign the saved introspected methods (PyGObject GI methods live
        # in the class __dict__, so deleting the override would drop them).
        Gtk.StyleContext.add_provider_for_screen = orig_screen_add
        Gtk.StyleContext.add_provider = orig_widget_add

    win.destroy()


def test_zoom_projection_repins_code_blocks():
    """R12: after a projected font inflate, an EXISTING code block re-measures
    its height pin with the projected font (rows never clip), a block created
    after the rescale settles to the same projected pin via its one-shot
    first-allocate re-pin,
    and the settled geometry shows min == natural with the TextView fully
    visible."""
    from gi.repository import Gtk

    from grc_agent.chat_sidebar import ChatSidebar
    from grc_agent.ui.code_block import CodeBlock

    code = "\n".join(f"line {i}  ->  step_{i}" for i in range(8))

    win = Gtk.OffscreenWindow()
    win.set_default_size(1000, 800)
    sidebar = ChatSidebar()
    win.add(sidebar)
    win.show_all()
    _settle_events()

    box = sidebar._start_agent_message()
    sidebar._render_markdown_to_box(box, f"```\n{code}\n```\n", clear=True)
    _settle_events()

    def find_code_block(root):
        return next((w for w in _iter_widgets(root) if isinstance(w, CodeBlock)), None)

    cb = find_code_block(sidebar._listbox)
    assert cb is not None
    sw = next(c for c in cb.get_children() if isinstance(c, Gtk.ScrolledWindow))
    tv = sw.get_child()
    pin_before = sw.get_size_request()[1]

    sidebar.set_zoom_projection(2.25)  # sqrt(2.25) = multiplier 1.5
    # Re-pinning an EXISTING block is the same frame-clock-driven style pass
    # the comment below describes for a newly rendered one, and the wait is
    # condition-based rather than a fixed window: under enough system load
    # (observed: reproducible when test_desktop_app.py's ten window-
    # constructing tests all run beforehand) a fixed real-time settle can
    # elapse before the idle-priority repin gets a turn, and the assertion
    # reads as "the projection did nothing" even though it hasn't failed —
    # it just hasn't happened yet.
    _wait_until(lambda: sw.get_size_request()[1] > pin_before)

    pin_after = sw.get_size_request()[1]
    assert pin_after > pin_before, (pin_before, pin_after)
    # The pin scales its line CONTENT with the projected font while the
    # per-line spacing and margins stay fixed px (and hinted line advances
    # quantize), so a short block's height ratio lands somewhat under the
    # 1.5 font multiplier — the exact law is the new-vs-repinned equality.
    assert abs(pin_after / pin_before - 1.5) < 0.3, (pin_before, pin_after)

    # A block rendered AFTER the rescale pins at construction against an
    # unparented style context (theme font); its style-updated font-watch
    # re-pins it once packed into the projected hierarchy and the style pass
    # resolves the projected font. That pass is frame-clock-driven, so this
    # settle needs _run_settle_frames (a bare events drain does not complete
    # the style pass on offscreen widgets).
    box2 = sidebar._start_agent_message()
    sidebar._render_markdown_to_box(box2, f"```python\n{code}\n```\n", clear=True)
    cb2 = find_code_block(box2)
    assert cb2 is not None and cb2 is not cb
    sw2 = next(c for c in cb2.get_children() if isinstance(c, Gtk.ScrolledWindow))
    _wait_until(lambda: sw2.get_size_request()[1] == pin_after)
    assert sw2.get_size_request()[1] == pin_after

    # Settled: below the cap the pin closes the min<natural gap and the
    # TextView's allocation covers its content — no clipped rows.
    assert pin_after < 420
    _wait_until(lambda: sw.get_preferred_height()[0] == sw.get_preferred_height()[1])
    sw_min, sw_nat = sw.get_preferred_height()
    assert sw_min == sw_nat
    assert tv.get_allocated_height() >= tv.get_preferred_height()[1] - 1
    win.destroy()


def test_zoom_projection_preserves_near_bottom_anchor():
    """R9 anchor preservation (plan Approach 2): with the viewport pinned to
    the bottom and simulated streaming rows arriving, a projected font
    inflate re-seats the anchor at the new bottom and keeps stick-to-bottom
    engaged — the re-seat is itself the value change the single authority
    (_on_scroll_value_changed) re-derives _auto_scroll from; nothing here
    writes the intent flag."""
    from gi.repository import Gtk

    from grc_agent.chat_sidebar import ChatSidebar

    win = Gtk.OffscreenWindow()
    win.set_default_size(400, 300)
    sidebar = ChatSidebar()
    win.add(sidebar)
    win.show_all()
    for i in range(10):
        sidebar._add_message_row(Gtk.Label(label=f"msg {i}\n" * 5))
    _settle_events()

    adj = sidebar._scrolled.get_vadjustment()
    adj.set_value(adj.get_upper() - adj.get_page_size())
    _settle_events()
    assert sidebar._auto_scroll is True

    # Streaming rows land while pinned to the bottom.
    sidebar._add_message_row(Gtk.Label(label="streamed chunk\n" * 6))
    sidebar._add_message_row(Gtk.Label(label="more content\n" * 6))
    _settle_events()
    assert sidebar._auto_scroll is True

    sidebar.set_zoom_projection(2.25)
    _settle_events()

    dist = adj.get_upper() - adj.get_page_size() - adj.get_value()
    assert dist <= 1.0, f"viewport drifted {dist}px off the bottom after inflate"
    assert sidebar._auto_scroll is True, "stick-to-bottom intent must stay engaged"
    win.destroy()


def test_zoom_projection_leaves_scrolled_up_reader_and_adjustment_alone():
    """R10/KD2: the projection is one-directional — a canvas zoom change
    updates the sidebar CSS but never scrolls the chat: a reader parked
    mid-history keeps their exact vadjustment value and reader intent (no
    feedback loop from the canvas into the transcript)."""
    from gi.repository import Gtk

    from grc_agent.chat_sidebar import ChatSidebar

    win = Gtk.OffscreenWindow()
    win.set_default_size(400, 300)
    sidebar = ChatSidebar()
    win.add(sidebar)
    win.show_all()
    for i in range(20):
        sidebar._add_message_row(Gtk.Label(label=f"msg {i}\n" * 5))
    _settle_events()

    adj = sidebar._scrolled.get_vadjustment()
    adj.set_value(300.0)
    _settle_events()
    assert sidebar._auto_scroll is False  # reading earlier content
    value_before = adj.get_value()

    sidebar.set_zoom_projection(2.25)
    _settle_events()

    assert adj.get_value() == value_before, "a zoom change must not scroll the chat"
    assert sidebar._auto_scroll is False
    # ... while the CSS did update to the projected size (base x sqrt(2.25)).
    import re

    css = sidebar._zoom_css_provider.to_string()
    px = float(re.search(r"font-size: ([0-9.]+)px", css).group(1))
    assert abs(px - sidebar._zoom_css_base_px * 1.5) < 0.01
    win.destroy()


def test_chat_ctrl_scroll_zooms_canvas_and_consumes_gesture():
    """R10/KTD9: Control+wheel over the transcript drives exactly one native
    canvas zoom step per wheel tick through GRC's own DrawingArea methods,
    never scrolls the chat, never steals focus, and never disturbs
    stick-to-bottom intent. Plain wheel passes through natively (native
    scroll, no zoom, authority recomputes intent from the moved position);
    smooth/horizontal deltas are not zoom gestures."""
    from unittest.mock import MagicMock

    import gi

    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk, Gtk

    from grc_agent.chat.constants import _SCROLL_STICK_THRESHOLD
    from grc_agent.chat_sidebar import ChatSidebar

    sidebar = ChatSidebar()
    proxy = MagicMock()
    cm = MagicMock()
    proxy._canvas_manager = cm
    sidebar.set_flowgraph_proxy(proxy)

    win = Gtk.OffscreenWindow()
    win.set_default_size(400, 300)
    win.add(sidebar)
    for i in range(12):
        sidebar._add_message_row(Gtk.Label(label=f"msg {i}\n" * 5))
    win.show_all()
    _settle_events()

    adj = sidebar._scrolled.get_vadjustment()
    adj.set_value(adj.get_upper() - adj.get_page_size())
    _settle_events()
    assert sidebar._auto_scroll is True
    focus_before = win.get_focus()
    entry = sidebar._entry
    assert not entry.tv.has_focus()

    def scroll_event(direction, state):
        ev = Gdk.Event.new(Gdk.EventType.SCROLL)
        ev.scroll.direction = direction
        ev.scroll.state = state
        return sidebar._scrolled.emit("scroll-event", ev)

    # Ctrl+wheel up: exactly one native zoom-in step, gesture consumed, the
    # chat does not scroll, intent and focus are untouched.
    v_before = adj.get_value()
    assert scroll_event(Gdk.ScrollDirection.UP, Gdk.ModifierType.CONTROL_MASK) is True
    cm.drawing_area.zoom_in.assert_called_once()
    cm.drawing_area.zoom_out.assert_not_called()
    assert adj.get_value() == v_before
    assert sidebar._auto_scroll is True
    assert win.get_focus() is focus_before
    assert not entry.tv.has_focus()

    # Ctrl+wheel down: exactly one zoom-out step.
    assert scroll_event(Gdk.ScrollDirection.DOWN, Gdk.ModifierType.CONTROL_MASK) is True
    cm.drawing_area.zoom_out.assert_called_once()
    cm.drawing_area.zoom_in.assert_called_once()

    # Plain wheel passes through: no extra zoom, native scrolling runs, and
    # the single authority recomputes intent from the moved position. (The
    # emit's boolean comes from the ScrolledWindow's own class handler, so
    # pass-through is asserted by behavior, not by the return value.) One
    # wheel tick stays within the stick threshold, so wheel up repeatedly —
    # each event natively scrolled and unzoomed — until clearly off-bottom,
    # where the authority's verdict is False.
    scroll_event(Gdk.ScrollDirection.UP, 0)
    cm.drawing_area.zoom_in.assert_called_once()  # not stepped again
    cm.drawing_area.zoom_out.assert_called_once()
    _settle_events()
    assert adj.get_value() < v_before, "plain wheel must pass through to native scrolling"
    for _ in range(20):
        if adj.get_upper() - adj.get_page_size() - adj.get_value() > _SCROLL_STICK_THRESHOLD:
            break
        scroll_event(Gdk.ScrollDirection.UP, 0)
        _settle_events()
    assert sidebar._auto_scroll is False

    # Ctrl+smooth is not one of the two zoom directions: falls through.
    assert scroll_event(Gdk.ScrollDirection.SMOOTH, Gdk.ModifierType.CONTROL_MASK) is False
    cm.drawing_area.zoom_in.assert_called_once()
    win.destroy()


def test_zoom_projection_same_multiplier_is_noop():
    """R9 (review finding #8): the canvas clamp band is wider than the
    sidebar's, so real zoom changes at the clamped tails can project an
    IDENTICAL multiplier — the second application must be a pure no-op (no
    CSS reload)."""
    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk

    from grc_agent.chat_sidebar import ChatSidebar

    sidebar = ChatSidebar()
    win = Gtk.OffscreenWindow()
    win.set_default_size(400, 300)
    win.add(sidebar)
    win.show_all()
    _settle_events()

    sidebar.set_zoom_projection(2.25)  # sqrt(2.25) = 1.5 (inside clamp)
    _settle_events()
    assert sidebar._zoom_css_provider is not None

    reloads = []
    orig = sidebar._zoom_css_provider.load_from_data
    sidebar._zoom_css_provider.load_from_data = lambda data: (
        reloads.append(data) or orig(data)
    )
    # The applied multiplier is 1.5 (from zoom 2.25). Repeating 2.25 no-ops;
    # 4.0 clamps to 1.8 (a REAL multiplier change -> exactly one reload);
    # 4.84 also clamps to 1.8 -> no-op again.
    sidebar.set_zoom_projection(2.25)
    sidebar.set_zoom_projection(4.0)
    sidebar.set_zoom_projection(4.84)
    assert len(reloads) == 1
    win.destroy()


def test_attach_button_opens_in_app_file_chooser():
    """The paperclip opens an in-app Gtk.FileChooserDialog (always renders,
    no xdg-desktop-portal round-trip) with multi-select and the image
    filter — the FileChooserNative portal path previously presented
    nothing at all on Wayland sessions."""
    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk

    from grc_agent.chat_sidebar import ChatSidebar

    sidebar = ChatSidebar()
    win = Gtk.OffscreenWindow()
    win.set_default_size(400, 300)
    win.add(sidebar)
    win.show_all()
    _settle_events()

    sidebar._attach_btn.emit("clicked")
    _settle_events()

    # `type(w) is` rather than isinstance: adapter.graph permanently swaps
    # GRC's FileDialogs.SaveFlowGraph for a FileChooserDialog subclass the
    # first time any test installs the untitled-save folder provider, and that
    # swap is process-wide and never undone. isinstance matched that leftover
    # too, so in some orders this picked a flowgraph save dialog and asserted
    # against it.
    dialogs = [w for w in Gtk.Window.list_toplevels() if type(w) is Gtk.FileChooserDialog]
    assert dialogs, "no file chooser appeared after clicking attach"
    dialog = dialogs[-1]
    assert dialog.get_select_multiple()
    filters = dialog.list_filters()
    assert filters and any(f.get_name() == "Images" for f in filters)
    dialog.destroy()
    win.destroy()


def test_ctrl_v_with_clipboard_image_attaches_png():
    """Ctrl+V with a copied IMAGE queues it as a pending attachment (chip
    row visible) and consumes the keystroke; a text clipboard falls through
    (returns False) so normal paste keeps working."""
    import time

    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import Gdk, GdkPixbuf, Gtk

    from grc_agent.chat_sidebar import ChatSidebar

    sidebar = ChatSidebar()
    win = Gtk.OffscreenWindow()
    win.set_default_size(420, 320)
    win.add(sidebar)
    win.show_all()
    _settle_events()

    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 12, 8)
    clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
    # store() ownership negotiation is asynchronous and racy under load —
    # re-assert ownership each round until the content is actually readable.
    for _ in range(150):
        clipboard.set_image(pixbuf)
        clipboard.store()
        _settle_events()
        if clipboard.wait_is_image_available():
            break
        time.sleep(0.02)
    else:
        # 150 re-assertions over ~3s and the X selection owner still never
        # settled. That is an environment limitation, not a defect in the
        # paste handler — reporting it as a failure makes the fast gate go
        # red at random and hides real regressions. Skip with the evidence.
        pytest.skip(
            "X clipboard ownership never settled after 150 re-assertions "
            "(~3s); cannot establish the image-clipboard precondition"
        )

    # Synthesize a Ctrl+V key event: a raw Gdk event with the fields the
    # handler reads.
    ev = Gdk.EventKey()
    ev.type = Gdk.EventType.KEY_PRESS
    ev.keyval = Gdk.KEY_v
    ev.state = Gdk.ModifierType.CONTROL_MASK
    handled = sidebar._on_entry_key_press(sidebar._entry, ev)
    assert handled is True
    assert len(sidebar._attachments) == 1
    assert sidebar._attachments[0].media_type == "image/png"
    assert sidebar._attachment_row.get_visible()

    # Text clipboard: falls through (default text paste), nothing attached.
    for _ in range(150):
        clipboard.set_text("plain note", -1)
        clipboard.store()
        _settle_events()
        if not clipboard.wait_is_image_available():
            break
        time.sleep(0.02)
    else:
        pytest.skip(
            "X clipboard would not release image ownership after 150 "
            "re-assertions (~3s); cannot establish the text-clipboard precondition"
        )
    ev2 = Gdk.EventKey()
    ev2.type = Gdk.EventType.KEY_PRESS
    ev2.keyval = Gdk.KEY_v
    ev2.state = Gdk.ModifierType.CONTROL_MASK
    assert sidebar._on_entry_key_press(sidebar._entry, ev2) is False
    assert len(sidebar._attachments) == 1
    win.destroy()


# ==========================================
# U3 pins — verified behavioral findings from the U2 audit
# ==========================================


def _turn_ready_sidebar(sidebar, canvas_path=None):
    """Minimum attribute setup so a REAL _run_agent_turn can drive a
    TestModel agent (same pattern as the end-to-end persistence tests)."""
    from unittest.mock import MagicMock

    sidebar._agent = None  # replaced by the caller
    sidebar._active_provider = "test-provider"
    sidebar._active_model = "test-model"
    sidebar._active_base_url = "test://base"
    sidebar._flowgraph_proxy = MagicMock()
    if canvas_path is None:
        sidebar._flowgraph_proxy._canvas_manager = None
    else:
        cm = MagicMock()
        cm.path = canvas_path
        sidebar._flowgraph_proxy._canvas_manager = cm
    sidebar._render_history = MagicMock()
    sidebar._scroll_to_bottom = MagicMock()
    sidebar._update_context_label = MagicMock()
    return sidebar


def _blocking_tool_agent():
    """A TestModel agent whose single tool blocks until released — a turn
    that can genuinely be cancelled mid-tool-call."""
    from pydantic_ai import Agent
    from pydantic_ai.models.test import TestModel

    entered = asyncio.Event()
    release = asyncio.Event()

    async def hang_tool() -> str:
        entered.set()
        await release.wait()
        return "released"

    return Agent(TestModel(), tools=[hang_tool], output_type=str), entered, release


def _cancel_mid_tool(sidebar, prompt="cancel me", *, timeout=5.0):
    """Drive a real turn into its blocking tool, then cancel it. Returns
    (entered, release); the turn task is finished (cancelled) on return."""
    agent, entered, release = _blocking_tool_agent()
    sidebar._agent = agent

    async def _scenario():
        turn = asyncio.ensure_future(sidebar._run_agent_turn(prompt))
        for _ in range(int(timeout * 100)):
            await asyncio.sleep(0.01)
            if entered.is_set():
                break
        assert entered.is_set(), "blocking tool never entered"
        turn.cancel()
        import contextlib

        with contextlib.suppress(asyncio.CancelledError):
            await turn

    asyncio.run(_scenario())
    return entered, release


def test_agent_copy_action_row_survives_rich_render(sidebar):
    """U3/F-01 (U2 audit): _render_last_message_rich wiped every child of the
    agent box — including the action row _start_agent_message had just packed
    — leaving the copy button orphaned out of the widget tree while its copy
    text kept updating on the detached object."""
    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk

    window = Gtk.OffscreenWindow()
    window.add(sidebar)
    window.show_all()
    try:
        from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

        sidebar._message_history = [
            ModelRequest(parts=[UserPromptPart(content="hello")]),
            ModelResponse(parts=[TextPart(content="world")]),
        ]
        sidebar._render_history()
        _settle_events()

        boxes = [
            w
            for w in walk(sidebar._listbox)
            if w.get_style_context().has_class("chat-agent-msg-box")
        ]
        assert boxes, "agent message box not rendered"
        rows = [
            w for w in walk(boxes[0]) if w.get_style_context().has_class("chat-msg-actions")
        ]
        assert rows, "agent copy action row missing from the widget tree after render"
        buttons = [
            w
            for w in walk(rows[0])
            if isinstance(w, Gtk.Button) and w.get_style_context().has_class("chat-copy-btn")
        ]
        assert buttons, "agent copy button missing from the action row"
        assert getattr(buttons[0], "_grc_copy_text", "") == "world"
    finally:
        window.destroy()


def test_cancelled_turn_tracks_the_history_save(sidebar):
    """U3/F-04 (U2 audit): the CancelledError path scheduled _save_history
    with a bare asyncio.ensure_future, never registered in _background_tasks
    — a clear/stop racing the save orphaned the handle and could persist
    pre-clear history."""
    _turn_ready_sidebar(sidebar)

    save_started = asyncio.Event()
    release_save = asyncio.Event()
    seen = {}

    async def fake_save():
        seen["task"] = asyncio.current_task()
        save_started.set()
        await release_save.wait()

    sidebar._save_history = fake_save

    agent, entered, release = _blocking_tool_agent()
    sidebar._agent = agent

    async def _scenario():
        turn = asyncio.ensure_future(sidebar._run_agent_turn("cancel me"))
        for _ in range(500):
            await asyncio.sleep(0.01)
            if entered.is_set():
                break
        assert entered.is_set(), "blocking tool never entered"
        turn.cancel()
        import contextlib

        with contextlib.suppress(asyncio.CancelledError):
            await turn
        await asyncio.wait_for(save_started.wait(), 5)
        # The save task the cancel path scheduled must be tracked.
        assert seen["task"] in sidebar._background_tasks
        release_save.set()

    asyncio.run(_scenario())


def test_aborted_turn_persists_history_needing_no_repair(sidebar, tmp_path):
    """Origin U15 scenario 6 end to end: an aborted turn persists a history
    the next send can use without any repair pass. The cleaner runs on the
    recovered history today; this test asserts it has nothing left to do."""
    from grc_agent.chat.history import _clean_message_history_for_new_turn
    from grc_agent.db import deserialize_messages, load_session, save_session

    f = tmp_path / "g.grc"
    f.touch()
    sid = save_session(None, str(f), [])
    _turn_ready_sidebar(sidebar, canvas_path=str(f))
    sidebar._active_session_id = sid

    _cancel_mid_tool(sidebar, "cancel me mid tool")

    history = sidebar._message_history
    assert history, "aborted turn left no history to persist"
    # The persisted history needs no repair: the cleaner is a no-op on it.
    assert _clean_message_history_for_new_turn(list(history)) == history

    # The history actually reached the database (the save is tracked and
    # runs on a worker thread — poll briefly rather than sleep-and-hope).
    deadline = time.monotonic() + 5
    reloaded = None
    while time.monotonic() < deadline:
        row = load_session(sid)
        if row and deserialize_messages(row["messages"]) == history:
            reloaded = row
            break
        time.sleep(0.05)
    assert reloaded is not None, "aborted turn's history never reached the database"

    # The abort rendered as a muted status row, not an error.
    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk

    labels = [w for w in walk(sidebar._listbox) if isinstance(w, Gtk.Label)]
    assert any(lbl.get_text() == "[aborted]" for lbl in labels)


def walk(root):
    """Depth-first walk — the conftest helper, imported locally to keep the
    module header GTK-free like the rest of this file."""
    from conftest import walk_widgets

    return walk_widgets(root)


# ==========================================
# U4 pins — copy-confirmation revert contract (audit F-03/F-07)
# ==========================================


def _copy_button():
    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk

    btn = Gtk.Button()
    btn.set_tooltip_text("Copy message")
    btn.set_label("Copy")
    btn.set_image(Gtk.Image.new_from_icon_name("edit-copy-symbolic", Gtk.IconSize.MENU))
    return btn


def test_copy_confirmation_reverts_after_one_timeout(sidebar, monkeypatch):
    """U4/F-07: the copy confirmation flips the button to its copied state,
    reverts after exactly one timeout, and a re-copy re-arms exactly one new
    timeout — never stacking."""
    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import GLib

    armed = []
    removed = []

    def spy_add(interval, callback, *_data):
        sid = 1000 + len(armed) + 1
        armed.append((interval, callback, sid))
        return sid

    def spy_remove(sid):
        removed.append(sid)
        return True

    monkeypatch.setattr(GLib, "timeout_add", spy_add)
    monkeypatch.setattr(GLib, "source_remove", spy_remove)
    # The status-clear timer shares GLib.timeout_add; silence it so the spy
    # sees only the copy button's own arm/remove calls.
    monkeypatch.setattr(sidebar, "set_status", lambda *_: None)

    def button_arms():
        """The GLib sources armed by the copy confirmation itself, identified
        by the revert closure — foreign GTK timers may share the spy."""
        return [rec for rec in armed if "revert" in getattr(rec[1], "__qualname__", "")]

    btn = _copy_button()
    sidebar._copy_to_clipboard("payload", btn)

    arms = button_arms()
    assert len(arms) == 1, "first copy must arm exactly one timeout"
    assert arms[0][0] == 1500
    assert btn._copy_timeout_id == arms[0][2]
    btn_tooltip = btn.get_tooltip_text()
    assert btn_tooltip == "Copied!"
    assert btn.get_label() == "Copied"
    image = btn.get_image()
    assert image.get_icon_name()[0] == "object-select-symbolic"

    # The revert is the one timeout callback; firing it restores the
    # pre-copy state and disarms itself.
    assert arms[0][1]() is False
    assert btn.get_tooltip_text() == "Copy message"
    assert btn.get_label() == "Copy"
    assert image.get_icon_name()[0] == "edit-copy-symbolic"
    assert btn._copy_timeout_id is None

    # Copying again after a fired revert arms fresh (nothing pending); copying
    # again while one is pending replaces it — the superseded source is
    # removed, one new arm, never stacking.
    sidebar._copy_to_clipboard("again", btn)
    arms = button_arms()
    assert len(arms) == 2
    assert removed == []
    sidebar._copy_to_clipboard("once more", btn)
    arms = button_arms()
    assert len(arms) == 3
    assert all(rec[0] == 1500 for rec in arms)
    assert [sid for sid in removed if sid == arms[1][2]] == [arms[1][2]]
    assert btn._copy_timeout_id == arms[2][2]
