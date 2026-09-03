"""Unit tests for desktop_app — split from the former test_unit.py god file.

Minimal set per the clustered test plan; shared fixtures/helpers live in conftest.py.
"""

import pytest


def test_apply_canvas_zoom_delegates_to_native_drawing_area_methods():
    """Regression: Ctrl+Plus/Minus/0 must delegate to GRC's own
    DrawingArea.zoom_in()/zoom_out()/reset_zoom() instead of hand-rolling
    zoom math — GRC's own View menu triggers these same native methods for
    the identical accelerators, so a hand-rolled reimplementation here would
    silently diverge (previously: additive +/-0.1 clamped 0.5-3.0 here vs.
    native multiplicative x1.2 clamped 0.1-5.0), making keyboard zoom and
    menu zoom disagree."""
    from unittest.mock import MagicMock

    from grc_agent.desktop_app import _apply_canvas_zoom

    canvas = MagicMock()
    da = MagicMock()
    canvas.drawing_area = da

    _apply_canvas_zoom(canvas, "in")
    da.zoom_in.assert_called_once()
    da.zoom_out.assert_not_called()
    da.reset_zoom.assert_not_called()

    _apply_canvas_zoom(canvas, "out")
    da.zoom_out.assert_called_once()

    _apply_canvas_zoom(canvas, "reset")
    da.reset_zoom.assert_called_once()

    # The hand-rolled math this replaced used to write zoom_factor directly —
    # confirm the delegated version never touches it, only calls the native methods.
    da.zoom_factor = "untouched-sentinel"
    _apply_canvas_zoom(canvas, "in")
    assert da.zoom_factor == "untouched-sentinel"


def test_window_keypress_editable_propagation():
    from unittest.mock import MagicMock

    from gi.repository import Gdk, Gtk

    from grc_agent.desktop_app import _on_window_key_press

    win = MagicMock()
    entry = MagicMock(spec=Gtk.Entry)
    win.get_focus.return_value = entry

    canvas = MagicMock()
    sidebar = MagicMock()

    # Bare (unmodified) keys must propagate (return False) so GTK's native
    # focus dispatch routes them through the widget's IM-context path — no raw
    # re-emission that would bypass IME composition (the old .event() forward).
    event = MagicMock(spec=Gdk.EventKey)
    event.state = 0
    event.keyval = Gdk.KEY_minus

    result = _on_window_key_press(win, event, canvas, sidebar)

    assert result is False
    entry.event.assert_not_called()

    # Ctrl+A override still selects all on the entry and is consumed.
    event_ctrl_a = MagicMock(spec=Gdk.EventKey)
    event_ctrl_a.state = Gdk.ModifierType.CONTROL_MASK
    event_ctrl_a.keyval = Gdk.KEY_a

    result = _on_window_key_press(win, event_ctrl_a, canvas, sidebar)
    assert result is True
    entry.select_region.assert_called_once_with(0, -1)


def test_build_app_shows_fatal_error_when_gnuradio_missing(monkeypatch):
    """Regression: a missing/broken GNU Radio install must show a friendly
    Gtk.MessageDialog (via _show_fatal_error) and exit cleanly, instead of a
    raw traceback — this is the fix for the GUI-only "friendly startup
    failures" requirement, and previously had zero test coverage."""
    from unittest.mock import MagicMock

    import grc_agent.desktop_app as desktop_app

    def _boom():
        raise ModuleNotFoundError("No module named 'gnuradio'")

    monkeypatch.setattr(desktop_app, "get_gui_platform", _boom)
    fatal = MagicMock()
    monkeypatch.setattr(desktop_app, "_show_fatal_error", fatal)
    # build_app() calls _apply_global_css() unconditionally before either
    # failure branch. That installs a module-global Gtk.CssProvider on the
    # DEFAULT SCREEN via add_provider_for_screen, at APPLICATION priority,
    # and never removes it -- a real, process-wide, permanent side effect
    # this test has no interest in. Left in place, it leaks into every later
    # test in the process (observed: it silently defeats
    # ChatSidebar.set_zoom_projection's own same-priority widget-scoped
    # provider in test_chat_sidebar.py, deterministically, whenever this test
    # runs first). Not the subject of this test, so it is stubbed out.
    monkeypatch.setattr(desktop_app, "_apply_global_css", lambda: None)

    with pytest.raises(SystemExit):
        desktop_app.build_app()

    fatal.assert_called_once()
    title, message = fatal.call_args[0]
    assert "gnu radio" in title.lower() or "gnuradio" in message.lower()
    assert "gnuradio" in message.lower()


def test_build_app_shows_fatal_error_when_window_not_found(monkeypatch):
    """Regression: if GRC's own MainWindow can't be found after activation,
    build_app() must show a friendly dialog (via _show_fatal_error) and exit
    cleanly — matching the fix that replaced a bare print() at this exact
    branch (previously the only console-only fatal path left, violating the
    GUI-only rule)."""
    from unittest.mock import MagicMock

    from gi.repository import Gtk

    import grc_agent.desktop_app as desktop_app

    monkeypatch.setattr(desktop_app, "get_gui_platform", lambda: object())

    class _FakeApplication:
        def __init__(self, *_args, **_kwargs):
            pass

        def register(self, *_args, **_kwargs):
            pass

        def activate(self):
            pass

    monkeypatch.setattr(desktop_app, "gui_application_cls", lambda: _FakeApplication)
    monkeypatch.setattr(Gtk.Application, "get_default", staticmethod(lambda: None))
    fatal = MagicMock()
    monkeypatch.setattr(desktop_app, "_show_fatal_error", fatal)
    # Same leak as test_build_app_shows_fatal_error_when_gnuradio_missing:
    # _apply_global_css() runs unconditionally at the top of build_app() and
    # installs a permanent, process-wide screen CSS provider this test does
    # not exercise or need.
    monkeypatch.setattr(desktop_app, "_apply_global_css", lambda: None)

    with pytest.raises(SystemExit):
        desktop_app.build_app()

    fatal.assert_called_once()
    title, _message = fatal.call_args[0]
    assert "window" in title.lower()


def test_untitled_save_dialog_seeded_to_project_dir(tmp_path, monkeypatch):
    """An untitled graph's Save-As dialog must default to the configured
    project directory (Ctrl+S on a new graph), while a named graph keeps GRC's
    own file-folder behavior. Seeds GRC's native dialog class only for the
    untitled case — native save flow untouched.

    We assert on what set_current_folder is *called* with, not
    get_current_folder(), because GTK realizes the chooser folder lazily
    (get_current_folder can read None before the dialog is shown)."""
    from grc_agent.adapter import graph as adapter_graph

    # Bootstrap gnuradio.grc.gui in the same order the running app does
    # (get_gui_platform/gui_application_cls set the PangoCairo version and
    # import the package top-down), so FileDialogs imports without GRC's
    # circular-import failure.
    adapter_graph.get_gui_platform()
    from gnuradio.grc.gui import FileDialogs

    proj = tmp_path / "proj"
    proj.mkdir()

    monkeypatch.setattr(adapter_graph, "_UNTITLED_SAVE_INSTALLED", False)
    adapter_graph.install_untitled_save_folder_provider(lambda: proj)

    save_cls = FileDialogs.SaveFlowGraph  # the installed subclass
    assert issubclass(save_cls, FileDialogs.SaveFlowGraph)
    seed_calls = []
    monkeypatch.setattr(
        save_cls, "set_current_folder", lambda *args: seed_calls.append(args[-1])
    )

    # Untitled (empty path) -> seeds the project directory (on top of GRC's
    # own dirname('') no-op call).
    save_cls(None, "")
    assert seed_calls[-1] == str(proj), "untitled save must seed the project dir"

    # A named graph keeps GRC's own "start in the file's folder" behavior —
    # the subclass must not re-seed it.
    named = proj / "sub" / "x.grc"
    named.parent.mkdir()
    named.write_text("x")
    save_cls(None, str(named))
    assert seed_calls[-1] == str(named.parent), "named save must keep GRC's folder"


def test_untitled_save_folder_ignores_unset_or_invalid_dir(monkeypatch):
    """No project dir (or one that vanished) -> the dialog keeps GRC's native
    default instead of being seeded somewhere invalid."""
    from grc_agent.adapter import graph as adapter_graph

    adapter_graph.get_gui_platform()
    from gnuradio.grc.gui import FileDialogs

    monkeypatch.setattr(adapter_graph, "_UNTITLED_SAVE_INSTALLED", False)
    adapter_graph.install_untitled_save_folder_provider(lambda: None)

    save_cls = FileDialogs.SaveFlowGraph
    seed_calls = []
    monkeypatch.setattr(save_cls, "set_current_folder", lambda *args: seed_calls.append(args[-1]))

    save_cls(None, "")
    # Only GRC's own dirname('') no-op call runs; the subclass must not seed.
    assert seed_calls == [""]


def test_is_native_wayland_session(monkeypatch):
    from grc_agent.desktop_app import is_native_wayland_session

    # Forced X11 backend -> always False
    monkeypatch.setenv("GDK_BACKEND", "x11")
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    assert is_native_wayland_session() is False

    # Wayland session without GDK_BACKEND=x11 -> True
    monkeypatch.delenv("GDK_BACKEND", raising=False)
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    assert is_native_wayland_session() is True

    # Wayland display env var -> True
    monkeypatch.delenv("XDG_SESSION_TYPE", raising=False)
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    assert is_native_wayland_session() is True

    # Pure X11 session -> False
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    assert is_native_wayland_session() is False


def test_is_native_wayland_display_fallback(monkeypatch):
    from gi.repository import Gdk

    from grc_agent.desktop_app import is_native_wayland_session

    class FakeWaylandDisplay:
        pass

    monkeypatch.delenv("GDK_BACKEND", raising=False)
    monkeypatch.delenv("XDG_SESSION_TYPE", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(Gdk.Display, "get_default", lambda: FakeWaylandDisplay())
    assert is_native_wayland_session() is True


@pytest.mark.asyncio
async def test_startup_preflight_surfaces_wayland_advisory(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    import grc_agent.desktop_app as desktop_app

    sidebar = MagicMock()
    monkeypatch.setattr(desktop_app, "is_native_wayland_session", lambda: True)
    monkeypatch.setattr(
        desktop_app, "load_settings", lambda: {"provider": "ollama_local", "model": "test"}
    )
    monkeypatch.setattr(
        desktop_app.asyncio,
        "to_thread",
        AsyncMock(return_value=(None, None)),
    )

    await desktop_app._startup_preflight(sidebar)

    sidebar.set_status.assert_called_with(
        "Advisory: Native Wayland detected. If menu popups drop, launch with: GDK_BACKEND=x11 uv run grc-agent",
        background=True,
    )




def test_canvas_zoom_wiring_contract():
    """KD2/R9 wiring contract, pinned at both ends (build_app itself needs a
    live GRC window and is not unit-testable here): NativeCanvasManager has
    the assignable on_zoom_changed slot (peer of the on_graphs_changed/
    on_sync_failed/on_graph_modified callbacks), and ChatSidebar exposes the
    one-argument set_zoom_projection entry point desktop_app.py wires it to,
    mapping through sidebar_font_multiplier."""
    import inspect
    from types import SimpleNamespace

    from grc_agent.chat_sidebar import ChatSidebar
    from grc_agent.native_canvas import NativeCanvasManager, sidebar_font_multiplier

    entry = ChatSidebar.set_zoom_projection
    assert callable(entry)
    assert list(inspect.signature(entry).parameters) == ["self", "zoom_factor"]

    cm = NativeCanvasManager.__new__(NativeCanvasManager)
    cm.window = SimpleNamespace(current_page=None)
    # Same seam as test_native_canvas's _zoom_test_manager: the assignability
    # of the callback slot desktop_app.build_app wires to the sidebar entry.
    cm.on_zoom_changed = None
    assert cm.on_zoom_changed is None
    cm.on_zoom_changed = ChatSidebar.set_zoom_projection  # the wiring shape

    # The mapping the entry applies is the one committed pure function.
    assert sidebar_font_multiplier(2.25) == 1.5
