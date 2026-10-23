"""GTK tests for the application-update dialog (grc_agent.ui.update_dialog).

Runs under xvfb (the `gui` marker): dialogs are toplevels, so they are
shown directly rather than packed into an OffscreenWindow. Network/Git are
never touched — ``check_for_updates`` is stubbed at the dialog-module seam.
"""

import asyncio
import contextlib

import pytest
from gi.repository import Gdk, Gtk

from grc_agent.ui import update_dialog as update_dialog_module
from grc_agent.ui.update_dialog import UpdateDialog
from grc_agent.updates import UpdateError, UpdateInfo

pytestmark = pytest.mark.gui


def _settle_events() -> None:
    n = 0
    while n < 500 and Gtk.events_pending():
        Gtk.main_iteration()
        n += 1


async def _pump_until(pred, limit: int = 500) -> bool:
    """Pump GTK + asyncio until ``pred`` holds or ``limit`` rounds pass."""
    for _ in range(limit):
        _settle_events()
        await asyncio.sleep(0)
        try:
            if pred():
                return True
        except Exception:
            pass
    _settle_events()
    try:
        return bool(pred())
    except Exception:
        return False


async def _wait_clipboard_text(expected: str, rounds: int = 100) -> str | None:
    """Poll the clipboard: X selection transfer settles asynchronously and a
    busy suite can delay ownership handoff past a single wait_for_text()."""
    clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
    for _ in range(rounds):
        text = clipboard.wait_for_text()
        if text == expected:
            return text
        await asyncio.sleep(0.02)
    return clipboard.wait_for_text()


def _available_info() -> UpdateInfo:
    return UpdateInfo(
        root="/tmp/fake-root",
        current_commit="a" * 40,
        target_commit="b" * 40,
        source="origin/main",
        command="cd /tmp/fake-root && .venv/bin/python -c \"apply\"",
    )


def test_construction_triggers_no_check(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list = []

    async def _boom() -> UpdateInfo:
        calls.append(1)
        raise AssertionError("must not check on construction")

    monkeypatch.setattr(update_dialog_module, "check_for_updates", _boom)
    dlg = UpdateDialog(None)
    try:
        dlg.show()
        _settle_events()
        assert calls == []
        assert not dlg._copy_button.get_sensitive()
        assert not dlg._cmd_entry.get_visible()
    finally:
        dlg.destroy()
        _settle_events()


@pytest.mark.asyncio
async def test_available_enables_copy_and_copies_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_check() -> UpdateInfo:
        return _available_info()

    monkeypatch.setattr(update_dialog_module, "check_for_updates", _fake_check)
    dlg = UpdateDialog(None)
    try:
        dlg.show()
        dlg._check_button.clicked()
        assert await _pump_until(lambda: dlg._copy_button.get_sensitive())
        assert "b" * 12 in dlg._status.get_text()
        assert dlg._cmd_entry.get_text() == _available_info().command
        dlg._copy_button.clicked()
        _settle_events()
        assert await _wait_clipboard_text(_available_info().command) == (
            _available_info().command
        )
    finally:
        dlg.destroy()
        _settle_events()


@pytest.mark.asyncio
async def test_up_to_date_keeps_copy_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_check() -> UpdateInfo:
        return UpdateInfo(
            root="/tmp/fake-root",
            current_commit="c" * 40,
            target_commit="c" * 40,
            source="origin/main",
            command="cmd",
        )

    monkeypatch.setattr(update_dialog_module, "check_for_updates", _fake_check)
    dlg = UpdateDialog(None)
    try:
        dlg.show()
        dlg._check_button.clicked()
        assert await _pump_until(lambda: "Up to date" in dlg._status.get_text())
        assert not dlg._copy_button.get_sensitive()
        assert dlg._check_button.get_sensitive()
    finally:
        dlg.destroy()
        _settle_events()


@pytest.mark.asyncio
async def test_check_error_surfaces_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_check() -> UpdateInfo:
        raise UpdateError("Could not reach origin: boom")

    monkeypatch.setattr(update_dialog_module, "check_for_updates", _fake_check)
    dlg = UpdateDialog(None)
    try:
        dlg.show()
        dlg._check_button.clicked()
        assert await _pump_until(
            lambda: "Could not reach origin" in dlg._status.get_text()
        )
        assert not dlg._copy_button.get_sensitive()
        assert dlg._check_button.get_sensitive()
    finally:
        dlg.destroy()
        _settle_events()


@pytest.mark.asyncio
async def test_destroy_cancels_in_flight_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()

    async def _slow_check() -> UpdateInfo:
        started.set()
        await asyncio.sleep(30)
        return _available_info()

    monkeypatch.setattr(update_dialog_module, "check_for_updates", _slow_check)
    dlg = UpdateDialog(None)
    dlg.show()
    dlg._check_button.clicked()
    await asyncio.wait_for(started.wait(), timeout=5)
    task = dlg._check_task
    assert task is not None and not task.done()
    dlg.destroy()
    _settle_events()
    await asyncio.sleep(0)
    assert task.cancelled() or task.done()


def test_settings_dialog_update_button_opens_dialog_without_saving(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The update entry point stays separate from Save (no on_save call)."""
    from grc_agent.settings import default_settings
    from grc_agent.ui import settings_dialog as settings_module
    from grc_agent.ui.settings_dialog import SettingsDialog

    created: list = []
    saved: list = []

    class _FakeUpdateDialog:
        def __init__(self, *args: object, **kwargs: object) -> None:
            created.append((args, kwargs))

        def show(self) -> None:
            pass

    monkeypatch.setattr(
        update_dialog_module, "UpdateDialog", _FakeUpdateDialog
    )
    dlg = SettingsDialog(
        toplevel=None, cfg=default_settings(), on_save=lambda *a: saved.append(a)
    )
    try:
        dlg.show()
        _settle_events()
        target = None
        # A GTK container tree cannot cycle (each widget has one parent),
        # so this is a plain stack walk with no visited set — id()-based
        # dedup would be wrong here: transient wrapper objects can be
        # garbage-collected and their ids reused by later widgets.
        stack = [dlg]
        while stack:
            w = stack.pop()
            if isinstance(w, Gtk.Button) and w.get_label() == "Check for updates…":
                target = w
                break
            if hasattr(w, "get_children"):
                with contextlib.suppress(Exception):
                    stack.extend(w.get_children())
        assert target is not None, "update button missing from settings dialog"
        target.clicked()
        _settle_events()
        assert len(created) == 1
        assert saved == []
    finally:
        dlg.destroy()
        _settle_events()
    # Silence unused-import lint if refactored: module seam stays patched above.
    assert settings_module is not None
