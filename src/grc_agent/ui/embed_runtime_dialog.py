# ruff: noqa: E402
"""Progress dialog for provisioning the local llama.cpp embedding runtime.

This project is GUI-only, so there is no `setup` subcommand — consent, the
byte-size disclosure, and the download itself all live here.

Threading: `embed_runtime.provision` is blocking, so it runs on a worker
thread via ``asyncio.to_thread``. Its progress callback therefore fires on
that thread, where touching GTK is not safe. The counts are stashed in a
plain dict and a ``GLib.timeout_add`` tick reads them on the main loop —
the same shape as ``chat_sidebar._poll_indexing``, which polls RAG build
progress for exactly this reason.
"""

from __future__ import annotations

import asyncio
import logging

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

from .. import embed_runtime

_log = logging.getLogger(__name__)

_STAGE_LABELS = {
    "runtime": "Downloading llama.cpp runtime",
    "model": "Downloading EmbeddingGemma model",
}


class EmbedRuntimeDialog(Gtk.Dialog):
    """Confirm, then download, the local embedding runtime and model."""

    def __init__(self, toplevel: Gtk.Window | None, on_done=None) -> None:
        super().__init__(title="Local Embedding Runtime", transient_for=toplevel, modal=True)
        self.set_default_size(460, -1)
        self._on_done = on_done
        self._cancelled = False
        self._running = False
        self._tick_id: int | None = None
        # Written from the worker thread, read on the main loop. Only ever
        # whole-value assignments of immutable objects, so no lock is needed.
        self._progress: dict = {"stage": "", "done": 0, "total": 0}

        content = self.get_content_area()
        content.set_spacing(10)
        content.set_border_width(12)

        self._status = Gtk.Label()
        self._status.set_xalign(0.0)
        self._status.set_line_wrap(True)
        content.pack_start(self._status, False, False, 0)

        self._bar = Gtk.ProgressBar()
        self._bar.set_show_text(True)
        self._bar.set_no_show_all(True)
        content.pack_start(self._bar, False, False, 0)

        self._install_button = self.add_button("Install", Gtk.ResponseType.APPLY)
        self.add_button("Cancel", Gtk.ResponseType.CANCEL)
        self.connect("response", self._on_response)

        self._describe()
        content.show_all()

    def _describe(self) -> None:
        """State the exact cost before asking for consent, and refuse up front
        when this machine cannot run the result at all."""
        try:
            plan = embed_runtime.download_plan()
        except Exception as exc:  # pragma: no cover - defensive
            self._status.set_text(f"Could not inspect the runtime: {exc}")
            self._install_button.set_sensitive(False)
            return

        if not plan["need_runtime"] and not plan["need_model"]:
            self._status.set_text(
                "The local embedding runtime is already installed.\n"
                f"Location: {embed_runtime.data_dir()}"
            )
            self._install_button.set_label("Reinstall")
            return

        if plan["need_runtime"] and not plan["runtime_available"]:
            self._status.set_text(
                f"No prebuilt runtime for this machine: {plan['reason']}\n\n"
                f"{embed_runtime.manual_instructions()}"
            )
            self._install_button.set_sensitive(False)
            return

        parts = []
        if plan["reusable_server"] is not None:
            parts.append(f"Reusing the llama-server already at {plan['reusable_server']}.")
        elif plan["need_runtime"]:
            parts.append("Downloads the llama.cpp runtime.")
        if plan["need_model"]:
            parts.append("Downloads the EmbeddingGemma model (300M, CPU is fine).")
        parts.append(
            f"Total download: {embed_runtime.fmt_size(plan['download_bytes'])}, "
            f"installed to {embed_runtime.data_dir()}."
        )
        if plan["warn"]:
            parts.append(f"Note: {plan['warn']}")
        self._status.set_text("\n".join(parts))

    def _on_response(self, _dlg: Gtk.Dialog, response: int) -> None:
        if response == Gtk.ResponseType.APPLY and not self._running:
            self._start()
            return
        # Cancel during a download aborts it; the partial file is removed by
        # embed_runtime.download's own cleanup.
        self._cancelled = True
        if not self._running:
            self._teardown()
            self.destroy()

    def _start(self) -> None:
        self._running = True
        self._install_button.set_sensitive(False)
        self._bar.show()
        self._bar.set_fraction(0.0)
        self._bar.set_text("Starting…")
        self._status.set_text("Installing…")
        self._tick_id = GLib.timeout_add(200, self._tick)
        asyncio.ensure_future(self._run())

    def _on_progress(self, stage: str, done: int, total: int) -> None:
        """Called on the worker thread — must not touch GTK."""
        self._progress = {"stage": stage, "done": done, "total": total}

    def _tick(self) -> bool:
        p = self._progress
        total = p["total"]
        if total:
            self._bar.set_fraction(min(1.0, p["done"] / total))
            self._bar.set_text(
                f"{_STAGE_LABELS.get(p['stage'], p['stage'])} — "
                f"{embed_runtime.fmt_size(p['done'])} / {embed_runtime.fmt_size(total)}"
            )
        return True

    async def _run(self) -> None:
        try:
            await asyncio.to_thread(
                embed_runtime.provision,
                progress=self._on_progress,
                should_cancel=lambda: self._cancelled,
            )
        except Exception as exc:
            self._finish(False, str(exc))
            return
        self._finish(True, "")

    def _finish(self, ok: bool, error: str) -> None:
        self._teardown()
        self._running = False
        if self._on_done is not None:
            try:
                self._on_done(ok, error)
            except Exception:  # pragma: no cover - defensive
                _log.exception("embed runtime on_done callback failed")
        if ok:
            self.destroy()
            return
        self._bar.hide()
        self._install_button.set_sensitive(True)
        self._status.set_text(
            "Installation cancelled." if self._cancelled else f"Installation failed:\n{error}"
        )
        self._cancelled = False

    def _teardown(self) -> None:
        if self._tick_id is not None:
            GLib.source_remove(self._tick_id)
            self._tick_id = None
