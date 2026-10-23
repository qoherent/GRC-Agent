# ruff: noqa: E402
"""Application-update dialog: check for updates, preview the command, copy it.

Check runs only on the user's click — never on dialog construction — via
``updates.check_for_updates`` on the shared asyncio/GLib main loop (asyncio
subprocesses keep the GTK thread responsive). The Copy button stays
insensitive until a check reports an update is available. Destroying the
dialog cancels a check in flight.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, Gtk

from ..updates import UpdateError, UpdateInfo, check_for_updates
from .copy_confirm import confirm_copy

_log = logging.getLogger(__name__)


class UpdateDialog(Gtk.Dialog):
    """Modal Check-for-updates dialog. Copies a terminal command on demand."""

    def __init__(self, toplevel: Gtk.Window | None) -> None:
        super().__init__(title="Application Update", transient_for=toplevel, modal=True)
        self.set_default_size(560, -1)
        self._check_task: asyncio.Task | None = None
        self._info: UpdateInfo | None = None

        content = self.get_content_area()
        content.set_spacing(10)
        content.set_border_width(12)

        self._status = Gtk.Label()
        self._status.set_xalign(0.0)
        self._status.set_line_wrap(True)
        self._status.get_style_context().add_class("dim-label")
        self._status.set_text(
            "Check whether this installation is behind origin/main. "
            "Nothing is downloaded or changed until you run the copied command "
            "in a terminal with the app closed."
        )
        content.pack_start(self._status, False, False, 0)

        self._cmd_entry = Gtk.Entry()
        self._cmd_entry.set_editable(False)
        self._cmd_entry.set_tooltip_text("The terminal command that applies the update")
        self._cmd_entry.set_no_show_all(True)
        content.pack_start(self._cmd_entry, False, False, 0)

        hint = Gtk.Label()
        hint.set_xalign(0.0)
        hint.set_line_wrap(True)
        hint.get_style_context().add_class("dim-label")
        hint.set_text(
            "After copying: save your work, stop any running flowgraphs, "
            "close the app, then paste the command into a new terminal."
        )
        content.pack_start(hint, False, False, 0)

        self._check_button = self.add_button("Check for updates", Gtk.ResponseType.APPLY)
        self._copy_button = self.add_button("Copy update command", Gtk.ResponseType.OK)
        self._copy_button.set_sensitive(False)
        self._copy_button.set_tooltip_text("Copy update command")
        self.add_button("Close", Gtk.ResponseType.CLOSE)

        self.connect("response", self._on_response)
        self.connect("destroy", self._on_destroy)
        content.show_all()

    def _on_response(self, _dlg: Gtk.Dialog, response: int) -> None:
        if response == Gtk.ResponseType.APPLY:
            self._start_check()
        elif response == Gtk.ResponseType.OK:
            self._copy_command()
        else:
            self._cancel_check()
            self.destroy()

    def _on_destroy(self, _dlg: Gtk.Dialog) -> None:
        self._cancel_check()

    def _start_check(self) -> None:
        if self._check_task is not None and not self._check_task.done():
            return
        self._info = None
        self._copy_button.set_sensitive(False)
        self._cmd_entry.hide()
        self._check_button.set_sensitive(False)
        self._status.set_text("Checking for updates…")
        self._check_task = asyncio.ensure_future(self._run_check())

    def _cancel_check(self) -> None:
        task, self._check_task = self._check_task, None
        if task is not None and not task.done():
            task.cancel()

    async def _run_check(self) -> None:
        try:
            info = await check_for_updates()
        except UpdateError as exc:
            self._finish_check_error(str(exc))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            _log.exception("update check failed")
            self._finish_check_error(f"Update check failed: {exc}")
        else:
            self._finish_check_ok(info)

    def _finish_check_error(self, message: str) -> None:
        self._check_task = None
        self._check_button.set_sensitive(True)
        # Destroyed mid-flight: the widgets are gone, the message is moot.
        with contextlib.suppress(Exception):  # pragma: no cover - destroyed mid-flight
            self._status.set_text(message)

    def _finish_check_ok(self, info: UpdateInfo) -> None:
        self._check_task = None
        self._check_button.set_sensitive(True)
        try:
            if info.available:
                self._info = info
                self._status.set_text(
                    f"Update available on {info.source}: "
                    f"{info.current_commit[:12]} → {info.target_commit[:12]}."
                )
                self._cmd_entry.set_text(info.command)
                self._cmd_entry.show()
                self._copy_button.set_sensitive(True)
            else:
                self._status.set_text(
                    f"Up to date ({info.current_commit[:12]} on {info.source})."
                )
        except Exception:  # pragma: no cover - destroyed mid-flight
            pass

    def _copy_command(self) -> None:
        if self._info is None:
            return
        if confirm_copy(
            self._copy_button,
            self._info.command,
            idle_tooltip="Copy update command",
            revert_label_to="Copy update command",
        ):
            # Persist past application exit: the user closes the app before
            # pasting, and without store() the clipboard contents die with it.
            with contextlib.suppress(Exception):  # pragma: no cover - no clipboard manager
                Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).store()
