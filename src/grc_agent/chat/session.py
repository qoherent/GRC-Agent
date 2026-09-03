# ruff: noqa: E402
"""The sidebar's session lifecycle: open, save, clear, delete, compact, and
the planner-to-executor handoff.

Owns: the Clear-History and per-session delete confirmations, the compaction
confirm-and-run flow, ``clear_messages``, ``_save_history`` (with the
clear-generation resurrection guard), recent-session open
(``_on_recent_session_clicked`` / ``_switch_or_open_file``), and the
implement-plan handoff block (``_append/_remove_implement_plan_action``,
``_show_implement_plan_if_ready``, ``_on_implement_plan_clicked``,
``_implement_durable_plan``).

Host-attribute contract — every method assumes the full ChatSidebar
instance and reads/edits these host attributes (created in ``__init__`` or
``_build_*``, which stay in the composition root):

- state: ``_active_session_id``, ``_message_history``, ``_clear_generation``,
  ``_loading_session_id``, ``_busy``, ``_agent``, ``_agent_mode``,
  ``_implement_plan_row``, ``_implement_plan_button``, ``_implement_plan_task``,
  ``_open_dialog``, ``_compact_task``, ``_flowgraph_proxy``
- widgets: ``_listbox``, ``_compact_btn``
- sibling methods called through ``self``: ``_get_effective_path``,
  ``_get_cm``, ``_select_executor``, ``_render_history``,
  ``_cancel_background_tasks``, ``_track_background_task``, ``_set_busy``,
  ``_update_context_label``, ``_archive_agent_name``, ``set_status``,
  ``send_message``, ``grab_entry_focus``, ``_remove_timers``
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")

from gi.repository import Gtk
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai_harness.planning import PlanItem, SqlitePlanStore

from ..db import (
    archive_transcript,
    conversation_id_for_session,
    delete_all_sessions,
    delete_session,
    deserialize_messages,
    get_db_path,
    load_plan_items,
    load_session,
    save_session,
)
from .history import (
    _clean_message_history_for_new_turn,
    _sanitize_history_for_executor,
    _without_truncated_thinking_tail,
    extract_plan_from_text,
)

_log = logging.getLogger(__name__)


class SessionMixin:
    """Session lifecycle; see module docstring."""

    def _on_clear_history_clicked(self, _widget: Gtk.Button | None = None) -> None:
        _log.info("Clear History: button clicked")
        dialog = Gtk.MessageDialog(
            transient_for=self.get_toplevel()
            if isinstance(self.get_toplevel(), Gtk.Window)
            else None,
            modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            text="Clear ALL Chat History",
        )
        dialog.format_secondary_text(
            "This will permanently delete EVERY saved chat session for all flowgraphs. "
            "This cannot be undone."
        )
        self._open_dialog = dialog

        def _on_response(_dlg: Gtk.Dialog, response: int) -> None:
            _log.info("Clear History: dialog response=%s (YES=%s)", response, Gtk.ResponseType.YES)
            self._open_dialog = None
            dialog.destroy()
            if response != Gtk.ResponseType.YES:
                return
            # Global clear: delete every saved session. The toolbar button is not
            # tied to a specific flowgraph, and the welcome screen lists sessions
            # across all files — so scoping the delete to "the active flowgraph's
            # path" (the old behavior) silently did nothing when no flowgraph was
            # saved/active (path=None, sid=None), which is exactly the case where
            # the user is staring at the recent-sessions list. Per-session
            # deletion stays available via the per-row delete buttons.
            try:
                delete_all_sessions()
                _log.info("Clear History: deleted all sessions")
            except Exception as e:
                _log.exception("Failed to delete all sessions")
                self.clear_messages()
                self.set_status(f"Failed to clear history ({e})", error=True)
                return
            self.clear_messages()
            self.set_status("All chat history cleared.")

        dialog.connect("response", _on_response)
        dialog.show()
        _log.info("Clear History: dialog shown, awaiting response")

    def _on_compact_clicked(self, _btn: Gtk.Button) -> None:
        """Confirm before manual compaction to prevent accidental summaries."""
        if self._busy or self._agent is None or not self._message_history:
            return
        if self._active_session_id is None:
            # No session row = no conversation id: the pre-compact snapshot
            # cannot be registered, so compacting would destroy the only
            # (in-memory) copy of the summarized turns. Refuse.
            self.set_status("Cannot compact — history is not saved to a session yet.", error=True)
            return

        dialog = Gtk.MessageDialog(
            transient_for=self.get_toplevel()
            if isinstance(self.get_toplevel(), Gtk.Window)
            else None,
            modal=True,
            destroy_with_parent=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            text="Compact Conversation?",
        )
        dialog.format_secondary_text(
            "Older messages in the active context will be summarized using the current model. "
            "The complete pre-compaction transcript remains saved for history and dataset collection."
        )
        dialog.set_default_response(Gtk.ResponseType.NO)
        self._open_dialog = dialog

        def _on_response(_dlg: Gtk.Dialog, response: int) -> None:
            self._open_dialog = None
            dialog.destroy()
            if response != Gtk.ResponseType.YES:
                return
            if self._busy or self._agent is None or not self._message_history:
                return
            if self._active_session_id is None:
                self.set_status(
                    "Cannot compact — history is not saved to a session yet.", error=True
                )
                return
            self._set_busy(True)
            self._compact_task = self._track_background_task(
                asyncio.ensure_future(self._run_compact_now())
            )

        dialog.connect("response", _on_response)
        dialog.show()

    async def _run_compact_now(self) -> None:
        try:
            from pydantic_ai_harness.compaction import compact_now

            from ..agent_factory import make_summarizing_strategy

            # _on_compact_clicked guarantees an agent before spawning; derive
            # the model here (inside the try, so no early return can skip the
            # finally that clears busy).
            agent = self._agent
            model = agent.model if agent is not None else None

            # D3: snapshot the pre-compact history first so ConversationSearch
            # can still recall what the summary drops.
            sid = self._active_session_id
            if sid is not None:
                await archive_transcript(
                    self._message_history,
                    conversation_id=conversation_id_for_session(sid),
                    agent_name=self._archive_agent_name(),
                    kind="manual_compaction_transcript",
                )

            strategy = make_summarizing_strategy()
            compacted = await compact_now(
                strategy,
                self._message_history,
                model=model,  # D1: model=None inherits this
            )
            strategy_keep = strategy.keep_messages
            had_work = len(self._message_history) > strategy_keep
            if compacted is not self._message_history and compacted != self._message_history:
                self._message_history = compacted
                await self._save_history()
                self._render_history()
                self.set_status("History compacted — older messages summarized.")
            elif had_work:
                # More than keep_messages messages and STILL unchanged: the
                # summary call itself failed (D2 kept the history — e.g. Codex,
                # whose transport rejects the non-streaming summarizer).
                self.set_status(
                    "Compaction failed — summary unavailable, history unchanged.",
                    error=True,
                )
            else:
                self.set_status("History is already compact — nothing to summarize.")
        except Exception as e:
            _log.warning("compact_now failed: %s", e, exc_info=True)
            self.set_status("Compaction failed — history unchanged.", error=True)
        finally:
            self._set_busy(False)
            self._update_context_label()

    def _on_delete_recent_session(self, session_id: int) -> None:
        """Delete a saved conversation after a confirmation dialog — mirrors the
        per-row delete-with-confirm of the reference web UI sidebar. The dialog
        is non-blocking (signal-based under gbulb) and anchored on `self` so
        PyGObject doesn't GC it mid-response (same pattern as Clear History)."""
        toplevel = self.get_toplevel()
        if not isinstance(toplevel, Gtk.Window):
            toplevel = None
        dialog = Gtk.MessageDialog(
            transient_for=toplevel,
            modal=True,
            destroy_with_parent=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            text="Delete this conversation?",
        )
        dialog.format_secondary_text(
            "This will permanently delete the conversation and cannot be undone."
        )
        self._open_dialog = dialog

        def _on_response(_dlg: Gtk.Dialog, response: int) -> None:
            self._open_dialog = None
            dialog.destroy()
            if response != Gtk.ResponseType.YES:
                return
            try:
                delete_session(session_id)
                if self._active_session_id == session_id:
                    self._active_session_id = None
                    self._message_history = []
            except Exception as e:
                _log.error("Failed to delete session %s: %s", session_id, e)
                self.set_status(f"Failed to delete session: {e}", error=True)
            self._render_history()

        dialog.connect("response", _on_response)
        dialog.show()

    def clear_messages(self) -> None:
        # Bump the generation first so any in-flight _save_history worker
        # (uncancellable) will undo its own INSERT instead of resurrecting a
        # session the user just cleared (see _save_history), and so any
        # in-flight _run_agent_turn's CancelledError handler recognizes this
        # clear and skips re-populating the listbox it just wiped.
        self._clear_generation += 1
        self._cancel_background_tasks()
        self._implement_plan_task = None
        self._remove_implement_plan_action()
        self._message_history = []
        self._active_session_id = None
        self._select_executor()
        self._compact_btn.set_sensitive(False)
        self._render_history()

    def _on_recent_session_clicked(self, session_id: int) -> None:
        if self._busy:
            self.set_status(
                "Stop or wait for the current response before switching sessions.", error=True
            )
            return
        session_data = load_session(session_id)
        if not session_data:
            self.set_status("Session not found in database.", error=True)
            return

        path = session_data["grc_file_path"]
        if not path or not Path(path).exists():
            self.set_status("Associated file not found on disk.", error=True)
            return

        self._active_session_id = session_id
        loaded = _clean_message_history_for_new_turn(
            deserialize_messages(session_data["messages"])
        )
        loaded, had_truncated_thinking = _without_truncated_thinking_tail(loaded)
        if had_truncated_thinking:
            _log.warning(
                "Dropped an unarchived truncated-thinking tail while loading session %d",
                session_id,
            )
        self._message_history = loaded
        self._select_executor()
        self._render_history()

        self._loading_session_id = session_id
        try:
            self._switch_or_open_file(path)
        finally:
            self._loading_session_id = None

    def _switch_or_open_file(self, path: str) -> None:
        cm = self._get_cm()
        if not cm or not cm.window:
            self.set_status("GRC window not available.", error=True)
            return

        notebook = getattr(cm.window, "notebook", None)
        if not notebook:
            self.set_status("GRC notebook not available.", error=True)
            return

        target_path = Path(path).resolve()
        switched = False
        for i in range(notebook.get_n_pages()):
            p = notebook.get_nth_page(i)
            p_path = getattr(p, "file_path", None)
            if p_path:
                try:
                    if Path(p_path).resolve() == target_path:
                        notebook.set_current_page(i)
                        self.set_status("Switched to active tab.")
                        switched = True
                        break
                except Exception:
                    _log.debug(
                        "recent-session: skipping page %r during resolve", p_path, exc_info=True
                    )

        if not switched:
            try:
                cm.window.new_page(path, show=True)
                self.set_status("Opened session file.")
            except Exception as e:
                _log.error("Failed to open recent session file %s: %s", path, e)
                self.set_status(f"Failed to open session: {e}", error=True)

    async def _save_history(self) -> None:
        if self._active_session_id is None:
            return
        path = self._get_effective_path()
        if not path:
            return
        # Capture the clear-generation BEFORE dispatching. The save runs on a
        # worker thread that can't be cancelled; if a global Clear History runs
        # while it's in flight, the worker's save_session can INSERT a row that
        # resurrects a session the user just deleted. After the await, if the
        # generation changed, undo that resurrection. (Both reads of
        # _clear_generation happen on the main loop — no cross-thread access.)
        gen = self._clear_generation
        try:
            new_id = await asyncio.to_thread(
                save_session, self._active_session_id, path, self._message_history
            )
        except Exception as e:
            _log.error("Failed to save chat history to database: %s", e)
            return
        if new_id is not None and gen != self._clear_generation:
            try:
                # Off-thread like the save two lines above: this is the undo for
                # that same write, and it was the one SQLite call in this async
                # function still running on the GLib loop.
                await asyncio.to_thread(delete_session, new_id)
            except Exception:
                _log.exception("Failed to remove session resurrected by in-flight save")

    def _remove_implement_plan_action(self) -> None:
        row = self._implement_plan_row
        if row is not None and row.get_parent() is self._listbox:
            self._listbox.remove(row)
        self._implement_plan_row = None
        self._implement_plan_button = None

    def _append_implement_plan_action(self, session_id: int) -> None:
        """Render the user-controlled planner → executor handoff in chat."""
        self._remove_implement_plan_action()

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_hexpand(True)
        box.get_style_context().add_class("chat-plan-action-box")

        label = Gtk.Label(label="Plan ready for the GRC agent.")
        label.set_xalign(0.0)
        label.set_halign(Gtk.Align.FILL)
        box.pack_start(label, False, False, 0)

        button = Gtk.Button(label="Implement the Plan")
        button.set_hexpand(True)
        button.set_halign(Gtk.Align.FILL)
        button.set_tooltip_text(
            "Switch to GRC-Agent and begin implementing the durable plan"
        )
        button.get_accessible().set_name("Implement the Plan")
        button.get_style_context().add_class("chat-implement-plan-btn")
        button.set_sensitive(not self._busy)
        button.connect(
            "clicked",
            lambda clicked: self._on_implement_plan_clicked(clicked, session_id),
        )
        box.pack_start(button, False, False, 0)

        self._implement_plan_button = button
        self._implement_plan_row = self._add_message_row(box)

    async def _show_implement_plan_if_ready(self, session_id: int) -> None:
        """Show the handoff only when the planner left a durable plan."""
        try:
            items = await load_plan_items(session_id)
            if not items and self._message_history:
                items = await self._recover_plan_from_last_message(session_id)
        except Exception:
            _log.exception("Failed to read durable plan for implementation action")
            self.set_status("Plan saved, but its implementation action could not be loaded.", error=True)
            return
        if (
            items
            and self._active_session_id == session_id
            and self._agent_mode == "planner"
        ):
            self._append_implement_plan_action(session_id)

    async def _recover_plan_from_last_message(self, session_id: int) -> list[PlanItem]:
        """Recover structured plan steps from the planner's last assistant message if write_plan was not called."""
        for msg in reversed(self._message_history):
            if isinstance(msg, ModelResponse):
                text = " ".join(
                    part.content
                    for part in msg.parts
                    if isinstance(part, TextPart) and part.content
                )
                items = extract_plan_from_text(text)
                if len(items) >= 2:
                    conversation_id = f"session-{session_id}"
                    store = SqlitePlanStore(str(get_db_path()), session=conversation_id)
                    await store.set_items(items)
                    _log.info(
                        "Recovered %d plan steps from planner text response for session %d",
                        len(items),
                        session_id,
                    )
                    return items
                break
        return []

    def _on_implement_plan_clicked(self, button: Gtk.Button, session_id: int) -> None:
        if self._busy or self._implement_plan_task is not None:
            return
        button.set_sensitive(False)
        self._implement_plan_task = self._track_background_task(
            asyncio.ensure_future(self._implement_durable_plan(session_id))
        )

    async def _implement_durable_plan(self, session_id: int) -> None:
        try:
            if self._active_session_id != session_id:
                self.set_status("The plan belongs to a different chat session.", error=True)
                return
            items = await load_plan_items(session_id)
            if not items:
                self._remove_implement_plan_action()
                self.set_status("The durable plan is empty. Ask Planner to create it again.", error=True)
                return

            try:
                await archive_transcript(
                    self._message_history,
                    conversation_id=f"session-{session_id}",
                    agent_name="grc_planner",
                    kind="handoff",
                )
            except Exception:
                _log.warning("Failed to archive planner transcript before handoff", exc_info=True)

            self._message_history = _sanitize_history_for_executor(self._message_history)
            await self._save_history()

            self._select_executor()
            self._remove_implement_plan_action()
            sent = self.send_message(
                "Implement the approved plan now. Re-inspect the live graph before editing, "
                "follow the durable plan, and report the completed changes."
            )
            if not sent and self._implement_plan_button is not None:
                self._implement_plan_button.set_sensitive(True)
        except Exception:
            _log.exception("Failed to start durable plan implementation")
            if self._implement_plan_button is not None:
                self._implement_plan_button.set_sensitive(True)
            self.set_status("Could not start plan implementation. Try again.", error=True)
        finally:
            self._implement_plan_task = None


