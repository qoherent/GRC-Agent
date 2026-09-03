# ruff: noqa: E402
"""Composer mixin for ChatSidebar: the input area, attachments, and send.

Owns building the input area's widgets (``_ChatTextView``, the send/attach
buttons, the attachment-chip strip), the entry's key handling, attaching
files/images (paperclip, drag-and-drop, clipboard paste), and dispatching a
prompt into a new agent turn. Split out of ``chat_sidebar.py`` by U15 — a
GTK-owning mixin, not a pure-function module, so it still needs a display to
test against.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path
from typing import Any, get_args

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")

from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, Pango
from pydantic_ai.messages import BinaryContent, ImageMediaType, UserContent

from ..db import prompt_images, user_prompt_text, user_request
from .images import _thumbnail

# Derived from pydantic-ai's ImageMediaType so the chooser filter and the
# attachment admission gate are the same set by construction (one rule, no
# drift).
_IMAGE_MEDIA_TYPES: tuple[str, ...] = get_args(ImageMediaType)

# Target info id for the sidebar's uri-list drop target (single registered
# target, so the id is a constant, not an index into a list). ChatSidebar's
# own __init__ registers the drop target with this same id.
_DROP_TARGET_INFO = 0


class _ChatTextView(Gtk.ScrolledWindow):
    """Multi-line text input widget using GTK3 TextView and ScrolledWindow."""

    def __init__(self) -> None:
        super().__init__()
        self.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.set_shadow_type(Gtk.ShadowType.NONE)
        self.set_min_content_height(64)
        self.set_max_content_height(160)
        self.set_propagate_natural_height(True)
        self.set_hexpand(True)
        self.get_style_context().add_class("chat-entry-frame")

        self.tv = Gtk.TextView()
        self.tv.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.tv.set_hexpand(True)
        self.tv.get_accessible().set_name("Chat message")
        self.add(self.tv)

    def get_text(self) -> str:
        buf = self.tv.get_buffer()
        return buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True)

    def set_text(self, text: str) -> None:
        self.tv.get_buffer().set_text(text)

    def set_placeholder_text(self, text: str) -> None:
        self.tv.set_tooltip_text(text)

    def grab_focus(self) -> None:
        self.tv.grab_focus()

    def set_sensitive(self, sensitive: bool) -> None:
        super().set_sensitive(sensitive)
        self.tv.set_sensitive(sensitive)

    def get_sensitive(self) -> bool:
        return self.tv.get_sensitive()

    def get_position(self) -> int:
        buf = self.tv.get_buffer()
        mark = buf.get_insert()
        return buf.get_iter_at_mark(mark).get_offset()

    def set_position(self, pos: int) -> None:
        buf = self.tv.get_buffer()
        iter_pos = buf.get_iter_at_offset(pos)
        buf.place_cursor(iter_pos)

    def connect(self, detailed_signal: str, handler: Any, *args: Any) -> int:
        if detailed_signal == "changed":
            return self.tv.get_buffer().connect("changed", handler, *args)
        return self.tv.connect(detailed_signal, handler, *args)


class ComposerMixin:
    """Composer behavior mixed into ``ChatSidebar``.

    Every method here assumes the full ``ChatSidebar`` instance attributes
    (``self._entry``, ``self._send_btn``, ``self._pending_attachments``, and
    the turn-driver/session state still living on ``ChatSidebar`` itself) —
    this is an organizational split, not an encapsulation boundary.
    """

    def _build_input_area(self, content: Gtk.Box) -> None:
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        vbox.get_style_context().add_class("chat-input-area")
        vbox.set_border_width(4)

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

        self._entry = _ChatTextView()
        self._entry.set_placeholder_text("Ask a question or request changes...")
        self._entry.set_hexpand(True)
        self._entry.connect("key-press-event", self._on_entry_key_press)
        self._entry.connect("changed", lambda *_: self._update_send_sensitivity())
        self._entry.set_sensitive(False)

        # Pending image attachments live as removable chips in their own row
        # above the composer, so the entry itself stays a pure text surface.
        self._attachment_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self._attachment_row.set_visible(False)

        self._attach_btn = Gtk.Button.new_from_icon_name(
            "mail-attachment-symbolic", Gtk.IconSize.SMALL_TOOLBAR
        )
        self._attach_btn.set_tooltip_text("Attach image to message")
        self._attach_btn.get_style_context().add_class("chat-attach-btn")
        self._attach_btn.connect("clicked", self._on_attach_clicked)

        self._send_btn = Gtk.Button.new_from_icon_name(
            "media-playback-start-symbolic", Gtk.IconSize.SMALL_TOOLBAR
        )
        self._send_btn.set_tooltip_text("Send message (Enter, Shift+Enter for newline)")
        self._send_btn.get_style_context().add_class("chat-send-btn")
        self._send_btn.connect("clicked", self._on_send_clicked)
        self._send_btn.set_sensitive(False)

        box.pack_start(self._entry, True, True, 0)
        box.pack_start(self._attach_btn, False, False, 0)
        box.pack_start(self._send_btn, False, False, 0)
        self._input_box = box
        vbox.pack_start(self._attachment_row, False, False, 0)
        vbox.pack_start(box, False, False, 0)

        # Context and conversation controls share one compact row under the
        # composer. These are turn/session controls, not global toolbar actions.
        context_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        context_row.get_style_context().add_class("chat-context-controls")

        self._context_label = Gtk.Label()
        self._context_label.set_xalign(0.0)
        self._context_label.set_halign(Gtk.Align.START)
        self._context_label.set_hexpand(True)
        self._context_label.set_ellipsize(Pango.EllipsizeMode.END)
        self._context_label.set_max_width_chars(48)
        self._context_label.get_style_context().add_class("chat-context-label")
        self._context_label.set_margin_start(2)
        self._context_label.set_margin_top(2)
        self._context_label.set_margin_bottom(2)
        context_row.pack_start(self._context_label, True, True, 0)

        self._planner_toggle = Gtk.ToggleButton(label="Active:Agent")
        self._planner_toggle.set_valign(Gtk.Align.CENTER)
        self._planner_toggle.get_style_context().add_class("chat-mode-btn")
        self._planner_toggle.get_style_context().add_class("chat-mode-agent")
        self._planner_toggle.get_accessible().set_name("Planner mode")
        self._planner_toggle.connect("toggled", self._on_planner_toggled)
        self._planner_toggle.get_text = self._planner_toggle.get_label
        self._planner_mode_label = self._planner_toggle
        self._update_agent_mode_label()
        context_row.pack_start(self._planner_toggle, False, False, 0)

        self._compact_btn = Gtk.Button(label="Compact")
        self._compact_btn.set_tooltip_text(
            "Summarize older conversation messages while retaining a full transcript snapshot"
        )
        self._compact_btn.get_style_context().add_class("chat-compact-btn")
        self._compact_btn.connect("clicked", self._on_compact_clicked)
        self._compact_btn.set_sensitive(False)
        context_row.pack_start(self._compact_btn, False, False, 0)

        # Action approval gate: 'manual' (ask for all actions), 'auto'
        # (flowgraph changes auto-applied, shell asks), 'yolo' (all actions auto-applied).
        # Clicking the button cycles through the three modes.
        self._approval_toggle = Gtk.Button()
        self._approval_toggle.set_valign(Gtk.Align.CENTER)
        self._approval_toggle.get_style_context().add_class("chat-mode-btn")
        self._approval_toggle.get_accessible().set_name("Action approval gate")
        self._approval_toggle.connect("clicked", self._on_approval_mode_clicked)
        self._update_approval_toggle()
        context_row.pack_start(self._approval_toggle, False, False, 0)

        vbox.pack_start(context_row, False, False, 0)

        content.pack_start(vbox, False, False, 0)
        self._update_context_label()

    def grab_entry_focus(self) -> bool:
        """Grab keyboard focus for the chat text entry box if sensitive."""
        if self._entry.get_sensitive():
            self._entry.grab_focus()
            return True
        return False

    def set_input_enabled(self, enabled: bool) -> None:
        if not self._busy:
            self._entry.set_sensitive(enabled)
            self._update_send_sensitivity()
        if enabled:
            path = ""
            if self._flowgraph_proxy is not None:
                cm = self._get_cm()
                path = cm.path if cm else ""
            if not path:
                self._entry.set_placeholder_text(
                    "Save the flowgraph to keep this chat. Ask about your flowgraph..."
                )
            else:
                self._entry.set_placeholder_text("Ask about your flowgraph...")
            self.grab_entry_focus()
        else:
            self._entry.set_placeholder_text(
                "Open or create a flowgraph in GRC to start chatting..."
            )

    def _update_send_sensitivity(self) -> None:
        # Gate Send on non-blank input too, on top of the entry's own
        # busy/flowgraph-present sensitivity — otherwise a click on
        # whitespace-only text is a silent no-op (see _dispatch_send). Pending
        # image attachments alone are a valid turn, so they enable Send.
        self._send_btn.set_sensitive(
            self._entry.get_sensitive()
            and (bool(self._entry.get_text().strip()) or bool(self._attachments))
        )

    def _on_entry_key_press(self, _widget: Any, event: Gdk.EventKey) -> bool:
        if event.keyval == Gdk.KEY_Escape:
            if self._entry.get_text():
                self._entry.set_text("")
            toplevel = self.get_toplevel()
            if isinstance(toplevel, Gtk.Window):
                toplevel.set_focus(None)
            return True
        if (
            event.state & Gdk.ModifierType.CONTROL_MASK
            and event.keyval in (Gdk.KEY_v, Gdk.KEY_V)
        ):
            # Clipboard-first paste: a copied IMAGE (screenshot, copy-image)
            # never reaches the TextView's text paste path, so intercept it
            # here and queue it as a pending attachment. Text clipboards fall
            # through to the default paste (return False).
            clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
            if clipboard.wait_is_image_available():
                pixbuf = clipboard.wait_for_image()
                if pixbuf is not None:
                    self._attach_pixbuf(pixbuf)
                    return True
            return False
        if event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            if event.state & Gdk.ModifierType.SHIFT_MASK:
                pos = self._entry.get_position()
                text = self._entry.get_text()
                new_text = text[:pos] + "\n" + text[pos:]
                self._entry.set_text(new_text)
                self._entry.set_position(pos + 1)
                return True
            else:
                self._dispatch_send()
                return True
        return False

    def _on_send_clicked(self, _btn: Gtk.Button) -> None:
        if self._busy:
            self.stop_chat()
            return
        self._dispatch_send()

    def _dispatch_send(self) -> None:
        text = self._entry.get_text()
        if self._busy or (not text.strip() and not self._attachments):
            return
        if self._attachments:
            # Multimodal prompt per pydantic-ai's Sequence[UserContent]
            # contract: text piece (when non-blank) + image parts, in order.
            prompt: str | list[UserContent] = [text] if text.strip() else []
            prompt.extend(self._attachments)
            self._attachments = []
            self._refresh_attachment_chips()
        else:
            prompt = text
        self._entry.set_text("")
        self._remove_implement_plan_action()
        self.send_message(prompt)

    def _on_attach_clicked(self, _btn: Gtk.Button) -> None:
        """In-app file chooser (the same Gtk.FileChooserDialog pattern the
        Project-directory picker uses). FileChooserNative was rejected here:
        under Wayland it round-trips through xdg-desktop-portal, and when
        that fails it presents NOTHING — silently. A plain dialog always
        renders in-app on both X11 and Wayland."""
        top = self.get_toplevel()
        parent_win = top if isinstance(top, Gtk.Window) else None
        dialog = Gtk.FileChooserDialog(
            title="Attach image",
            parent=parent_win,
            action=Gtk.FileChooserAction.OPEN,
        )
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("_Attach", Gtk.ResponseType.ACCEPT)
        dialog.set_default_response(Gtk.ResponseType.ACCEPT)
        dialog.set_select_multiple(True)
        file_filter = Gtk.FileFilter()
        file_filter.set_name("Images")
        for mime in _IMAGE_MEDIA_TYPES:
            file_filter.add_mime_type(mime)
        dialog.add_filter(file_filter)
        dialog.connect("response", self._on_attach_response)
        dialog.show()

    def _on_attach_response(self, dialog: Gtk.Dialog, response: int) -> None:
        if response == Gtk.ResponseType.ACCEPT:
            self._attach_paths(dialog.get_filenames())
        dialog.destroy()

    def _on_drag_data_received(
        self,
        _widget: Any,
        _context: Any,
        _x: int,
        _y: int,
        data: Gtk.SelectionData,
        info: int,
        _time: int,
    ) -> None:
        """Queue dropped files as pending attachments through the same
        admission rule as the attach button. Non-file URIs (http, etc.) are
        skipped — only local files can be attached; non-image files surface
        the standard error bubble via _add_attachment."""
        if info != _DROP_TARGET_INFO:
            return
        paths: list[str] = []
        for uri in data.get_uris():
            try:
                path, _host = GLib.filename_from_uri(uri)
            except (GLib.Error, ValueError):
                continue
            paths.append(path)
        if paths:
            self._attach_paths(paths)

    def _attach_pixbuf(self, pixbuf: GdkPixbuf.Pixbuf) -> None:
        """Queue a clipboard image as a pending attachment.

        The pixbuf is written to a timestamped PNG in the platform temp dir
        (BinaryContent reads from a path), then routed through the same
        admission seam as the paperclip button, and the status bar confirms
        it — paste has no other visible surface."""
        import datetime
        import os
        import tempfile

        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        path = os.path.join(tempfile.gettempdir(), f"chat-paste-{stamp}.png")
        pixbuf.savev(path, "png", [], [])
        self._attach_paths([path])
        self.set_status("Image pasted — attached to the next message.")

    def _attach_paths(self, paths: Sequence[str]) -> None:
        """Queue a batch of image attachments — validation in
        _add_attachment, exactly one chip-row refresh and sensitivity update
        for the whole batch (chooser multi-select and drag-drop share it)."""
        for path in paths:
            self._add_attachment(path)
        self._refresh_attachment_chips()
        self._update_send_sensitivity()

    def _add_attachment(self, path: str) -> None:
        """Queue one image file as a pending attachment (validated, not yet
        rendered — the caller refreshes chips once per batch). Admission rule
        is exact membership in `_IMAGE_MEDIA_TYPES`, the same set the chooser
        filter offers; unreadable files are reported with an explicit error
        bubble instead of failing the callback."""
        try:
            content = BinaryContent.from_path(path)
        except OSError as e:
            self._append_error(f"Cannot read attachment: {Path(path).name} ({e})")
            return
        if content.media_type not in _IMAGE_MEDIA_TYPES:
            self._append_error(f"Unsupported attachment type: {Path(path).name}")
            return
        self._attachments.append(content)

    def _remove_attachment(self, index: int) -> None:
        if 0 <= index < len(self._attachments):
            del self._attachments[index]
            self._refresh_attachment_chips()
            self._update_send_sensitivity()

    def _refresh_attachment_chips(self) -> None:
        for child in self._attachment_row.get_children():
            self._attachment_row.remove(child)
        for index, attachment in enumerate(self._attachments):
            self._attachment_row.pack_start(
                self._build_attachment_chip(index, attachment), False, False, 0
            )
        # show_all() would re-show the row after set_visible(False), so the
        # show is guarded by the same emptiness rule that drives visibility.
        self._attachment_row.set_visible(bool(self._attachments))
        if self._attachments:
            self._attachment_row.show_all()

    def _build_attachment_chip(self, index: int, attachment: BinaryContent) -> Gtk.Widget:
        chip = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        chip.get_style_context().add_class("chat-attachment-chip")
        thumb = _thumbnail(attachment.data, 48)
        if thumb is not None:
            chip.pack_start(thumb, False, False, 0)
        remove_btn = Gtk.Button.new_from_icon_name(
            "window-close-symbolic", Gtk.IconSize.MENU
        )
        remove_btn.set_relief(Gtk.ReliefStyle.NONE)
        remove_btn.set_focus_on_click(False)
        remove_btn.set_tooltip_text("Remove attachment")
        remove_btn.get_accessible().set_name("Remove attachment")
        remove_btn.connect("clicked", lambda _b, i=index: self._remove_attachment(i))
        chip.pack_start(remove_btn, False, False, 0)
        return chip

    def send_message(self, prompt: str | Sequence[UserContent]) -> bool:
        """Send `prompt` as a user turn in the current session, as if it had
        been typed into the entry and submitted. Accepts plain text or a
        multimodal `Sequence[UserContent]` (text + image parts) per
        pydantic-ai's user-prompt contract. Returns False (no-op) if `prompt`
        carries neither text nor images, or a turn is already in flight."""
        part = user_request(prompt).parts[0]
        text = user_prompt_text(part)
        images = prompt_images(part)
        if (not text.strip() and not images) or self._busy:
            return False
        # Sending a message always re-engages auto-scroll — the user wants
        # to see their message and the agent's reply, even if they had
        # scrolled up to read earlier content.
        self._auto_scroll = True
        self._append_user_message(text, images)

        self._set_busy(True)
        self._chat_task = self._track_background_task(
            asyncio.ensure_future(self._run_agent_turn(prompt))
        )
        self._chat_task.add_done_callback(self._on_chat_task_done)
        return True

    def _remember_user_message(self, prompt: str | Sequence[UserContent]) -> None:
        """Record the user's just-sent prompt into the canonical history on a
        failed turn, so it is persisted and survives the next render instead of
        being wiped along with the error bubble. Uses the one canonical
        `user_request` builder so image-bearing turns are remembered whole."""
        self._message_history = [*self._message_history, user_request(prompt)]

