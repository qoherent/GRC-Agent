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
