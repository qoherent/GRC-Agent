# ruff: noqa: E402
"""Sign in with ChatGPT (Codex).

GUI-only, so there is no `login` subcommand: this opens the system browser at
the authorization URL and serves the loopback redirect on the unified event
loop. Because a remote or headless session can never receive that redirect,
the dialog also accepts a pasted redirect URL or bare code — the browser race
and the paste box are both live at once, and whichever arrives first wins.
"""

from __future__ import annotations

import asyncio
import logging

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gio, Gtk

from ..providers.openai_codex import auth

_log = logging.getLogger(__name__)


class CodexLoginDialog(Gtk.Dialog):
    def __init__(self, toplevel: Gtk.Window | None, on_done=None) -> None:
        super().__init__(title="Sign in with ChatGPT", transient_for=toplevel, modal=True)
        self.set_default_size(520, -1)
        self._on_done = on_done
        self._flow = auth.start_login()
        self._task: asyncio.Task | None = None

        content = self.get_content_area()
        content.set_spacing(10)
        content.set_border_width(12)

        self._status = Gtk.Label()
        self._status.set_xalign(0.0)
        self._status.set_line_wrap(True)
        self._status.set_selectable(True)
        self._status.set_text(
            "Waiting for you to finish signing in…\n"
            "The browser redirects back here automatically; this dialog closes "
            "on its own when it does.\n"
            "Requires an active ChatGPT Plus or Pro subscription."
        )
        content.pack_start(self._status, False, False, 0)

        link = Gtk.LinkButton(uri=self._flow.url, label="Open the ChatGPT sign-in page")
        link.set_halign(Gtk.Align.START)
        content.pack_start(link, False, False, 0)

        fallback = Gtk.Label()
        fallback.set_xalign(0.0)
        fallback.set_line_wrap(True)
        fallback.get_style_context().add_class("dim-label")
        fallback.set_text(
            "Only if it does not: copy your browser's address bar after signing "
            "in (it starts with http://localhost:1455/) and paste it below."
        )
        content.pack_start(fallback, False, False, 0)

        self._paste = Gtk.Entry()
        self._paste.set_placeholder_text("Paste the redirect URL or authorization code")
        self._paste.set_activates_default(True)
        content.pack_start(self._paste, False, False, 0)

        self._submit = self.add_button("Sign in", Gtk.ResponseType.APPLY)
        self.add_button("Cancel", Gtk.ResponseType.CANCEL)
        self.set_default_response(Gtk.ResponseType.APPLY)
        self.connect("response", self._on_response)

        content.show_all()
        self._open_browser()
        self._task = asyncio.ensure_future(self._await_browser())

    def _open_browser(self) -> None:
        try:
            Gio.AppInfo.launch_default_for_uri(self._flow.url, None)
        except Exception as exc:
            _log.info("could not launch a browser for the ChatGPT sign-in: %s", exc)
            self._status.set_text(
                "Could not open a browser automatically. Use the link below, "
                "then paste the resulting address here."
            )

    async def _await_browser(self) -> None:
        try:
            code = await auth.wait_for_callback(self._flow)
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            self._status.set_text(
                "Timed out waiting for the browser to redirect back. "
                "Paste the redirect URL below, or Cancel and try again."
            )
            return
        except Exception as exc:
            self._fail(str(exc))
            return
        await self._finish(code)

    def _on_response(self, _dlg: Gtk.Dialog, response: int) -> None:
        if response == Gtk.ResponseType.APPLY:
            text = self._paste.get_text().strip()
            if not text:
                self._status.set_text("Paste the redirect URL or code first, or Cancel.")
                return
            self._submit.set_sensitive(False)
            asyncio.ensure_future(self._submit_pasted(text))
            return
        self._cancel_task()
        self.destroy()

    async def _submit_pasted(self, text: str) -> None:
        try:
            code = auth.parse_redirect(text, self._flow.state)
        except Exception as exc:
            self._fail(str(exc))
            return
        await self._finish(code)

    async def _finish(self, code: str) -> None:
        self._cancel_task()
        try:
            await auth.exchange_code(code, self._flow.verifier)
        except Exception as exc:
            self._fail(str(exc))
            return
        if self._on_done is not None:
            self._on_done(True, "")
        self.destroy()

    def _fail(self, error: str) -> None:
        # Never render the pasted text back: it can contain an authorization
        # code, and this label is selectable and screenshot-friendly.
        self._status.set_text(f"Sign-in failed: {error}")
        self._submit.set_sensitive(True)

    def _cancel_task(self) -> None:
        """Stop waiting on the browser callback.

        Never cancels the *current* task. `_finish` runs inside the callback
        waiter itself, so cancelling unconditionally here killed the token
        exchange mid-flight: the CancelledError landed on the `await` inside
        `_finish`, and because it derives from BaseException rather than
        Exception, nothing caught it — the sign-in silently never completed
        and the dialog sat there as if nothing had happened.
        """
        task = self._task
        self._task = None
        if task is not None and not task.done() and task is not asyncio.current_task():
            task.cancel()
