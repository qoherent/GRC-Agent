# ruff: noqa: E402
"""Transcript-render mixin for ChatSidebar.

Owns rendering already-completed turns into the chat's GTK ``ListBox``: a
full history rebuild, a single-turn replace after streaming finishes, the
per-message-type rich renderer (text, thinking, tool calls/results, native
tools, a final-summary card), the tool-card and copy-button primitives both
this and the streaming render (``chat.stream_view``) build on, and the
inline user/error message rows. Split out of ``chat_sidebar.py`` by U15 — a
GTK-owning mixin, not a pure-function module, so it still needs a display to
test against.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")

from gi.repository import Gdk, GLib, Gtk, Pango
from pydantic_ai.messages import (
    BinaryContent,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    NativeToolCallPart,
    NativeToolReturnPart,
    RetryPromptPart,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from ..db import prompt_images, user_prompt_text
from .format import (
    _format_tool_display,
    _parse_final_summary,
    _tool_args_text,
    _tool_label,
    _tool_label_running,
    _transcript_summary,
    _transcript_thinking,
    _transcript_tool_call,
)
from .images import _thumbnail
from .stream_view import _StreamCtx


class TranscriptViewMixin:
    """Transcript-render behavior mixed into ``ChatSidebar``.

    Every method here assumes the full ``ChatSidebar`` instance attributes
    (``self._listbox``, ``self._md``, ``self._welcome``, and the turn-driver
    state still living on ``ChatSidebar`` itself) — this is an organizational
    split, not an encapsulation boundary.
    """

    def _render_history(self) -> None:  # noqa: C901
        self._implement_plan_row = None
        self._implement_plan_button = None
        for child in self._listbox.get_children():
            self._listbox.remove(child)

        # A full rebuild destroys any badge pill mid-hover without a
        # leave-notify-event (GTK3 doesn't synthesize one on widget
        # destruction), which could otherwise leave a stale canvas highlight.
        cm = self._get_cm()
        if cm:
            cm.clear_highlight()

        if not self._message_history:
            self._render_welcome_screen()
            self._listbox.show_all()
            self._update_context_label()
            return

        for msg in self._message_history:
            if isinstance(msg, ModelRequest):
                for part in msg.parts:
                    if isinstance(part, UserPromptPart):
                        self._append_user_message(user_prompt_text(part), prompt_images(part))
            elif isinstance(msg, ModelResponse):
                box = self._start_agent_message()
                self._render_last_message_rich(box, msg)
        self._scroll_to_bottom(force=True)
        self._update_context_label()

    def _replace_streaming_turn(
        self, ctx: _StreamCtx, new_messages: list[ModelMessage]
    ) -> None:
        """Replace only this turn's temporary stream row with rich widgets.

        Rebuilding the whole ListBox destroyed selections and focus in every
        earlier message at the end of each turn. Older rows are immutable and
        stay in place; only the one transient aggregate stream bubble is
        replaced by this run's final per-response rendering.
        """
        parent = ctx.box.get_parent()
        row = (
            parent
            if isinstance(parent, Gtk.ListBoxRow)
            else (parent.get_parent() if parent is not None else None)
        )
        if not isinstance(row, Gtk.ListBoxRow) or row.get_parent() is not self._listbox:
            self._render_history()
            return
        self._listbox.remove(row)
        for message in new_messages:
            if isinstance(message, ModelResponse):
                box = self._start_agent_message()
                self._render_last_message_rich(box, message)
        self._update_context_label()
        self._scrolled.check_resize()
        self._scroll_to_bottom()

    def _copy_to_clipboard(self, text: str, btn: Gtk.Button | None = None) -> None:
        if not text:
            return
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        clipboard.set_text(text, -1)
        self.set_status("Copied message to clipboard.")
        if btn is not None:
            btn.set_tooltip_text("Copied!")
            image = btn.get_image()
            if isinstance(image, Gtk.Image):
                image.set_from_icon_name("object-select-symbolic", Gtk.IconSize.MENU)
            if btn.get_label():
                btn.set_label("Copied")

            if hasattr(btn, "_copy_timeout_id") and btn._copy_timeout_id is not None:
                GLib.source_remove(btn._copy_timeout_id)
                btn._copy_timeout_id = None

            def _revert() -> bool:
                btn._copy_timeout_id = None
                try:
                    img = btn.get_image()
                    if isinstance(img, Gtk.Image):
                        img.set_from_icon_name("edit-copy-symbolic", Gtk.IconSize.MENU)
                    btn.set_tooltip_text("Copy message")
                    if btn.get_label():
                        btn.set_label("Copy")
                except Exception:
                    pass
                return False

            btn._copy_timeout_id = GLib.timeout_add(1500, _revert)
            if not getattr(btn, "_destroy_handler_set", False):
                btn._destroy_handler_set = True
                btn.connect(
                    "destroy",
                    lambda b: GLib.source_remove(b._copy_timeout_id)
                    if getattr(b, "_copy_timeout_id", None)
                    else None,
                )

    def _update_copy_text(self, box: Gtk.Box, text: Any) -> None:
        btn = getattr(box, "_grc_copy_btn", None)
        if btn is None:
            parent = box.get_parent()
            if parent and hasattr(parent, "_grc_copy_btn"):
                btn = parent._grc_copy_btn
        if btn is not None:
            btn._grc_copy_text = str(text)

    def _append_user_message(
        self, text: str, images: Sequence[BinaryContent] = ()
    ) -> None:
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        vbox.get_style_context().add_class("chat-user-msg-box")
        vbox.set_halign(Gtk.Align.END)
        vbox.set_margin_start(40)

        if images:
            thumbs = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            for image in images:
                thumb = _thumbnail(image.data, 128)
                if thumb is not None:
                    thumbs.pack_start(thumb, False, False, 0)
            if thumbs.get_children():
                thumbs.get_style_context().add_class("chat-user-msg-images")
                vbox.pack_start(thumbs, False, False, 0)

        lbl = Gtk.Label(label=text)
        lbl.set_line_wrap(True)
        lbl.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        lbl.set_xalign(0.0)
        lbl.set_halign(Gtk.Align.START)
        lbl.set_selectable(True)
        vbox.pack_start(lbl, False, False, 0)

        action_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        action_row.set_halign(Gtk.Align.END)

        copy_btn = Gtk.Button()
        copy_icon = Gtk.Image.new_from_icon_name("edit-copy-symbolic", Gtk.IconSize.MENU)
        copy_btn.set_image(copy_icon)
        copy_btn.set_always_show_image(True)
        copy_btn.set_focus_on_click(False)
        copy_btn.set_tooltip_text("Copy message")
        copy_btn.get_accessible().set_name("Copy message")
        copy_btn.get_style_context().add_class("chat-copy-btn")
        copy_btn.connect("clicked", lambda b: self._copy_to_clipboard(text, b))
        action_row.pack_start(copy_btn, False, False, 0)

        vbox.pack_start(action_row, False, False, 0)
        vbox._grc_copy_btn = copy_btn
        self._add_message_row(vbox)

    def _start_agent_message(self) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.get_style_context().add_class("chat-agent-msg-box")
        box.set_hexpand(True)
        box.set_halign(Gtk.Align.FILL)

        action_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        action_row.set_halign(Gtk.Align.END)
        action_row.get_style_context().add_class("chat-msg-actions")

        copy_btn = Gtk.Button()
        copy_icon = Gtk.Image.new_from_icon_name("edit-copy-symbolic", Gtk.IconSize.MENU)
        copy_btn.set_image(copy_icon)
        copy_btn.set_always_show_image(True)
        copy_btn.set_focus_on_click(False)
        copy_btn.set_tooltip_text("Copy message")
        copy_btn.get_accessible().set_name("Copy message")
        copy_btn.get_style_context().add_class("chat-copy-btn")

        copy_btn._grc_copy_text = ""
        copy_btn.connect(
            "clicked", lambda b: self._copy_to_clipboard(getattr(b, "_grc_copy_text", ""), b)
        )

        action_row.pack_start(copy_btn, False, False, 0)
        box.pack_end(action_row, False, False, 0)
        box._grc_copy_btn = copy_btn
        box._grc_action_row = action_row

        self._add_message_row(box)
        return box

    def _render_markdown_to_box(self, box: Gtk.Box, text: str, clear: bool = True) -> None:
        """Render markdown into ``box``. Delegates to the MarkdownView, which
        does not exist until __init__ has built the message list."""
        if self._md is not None:
            self._md.render(box, text, clear)

    def _function_returns_by_call_id(self) -> dict[str, ToolReturnPart | RetryPromptPart]:
        """Index every function-tool outcome in the history by its call id.

        Built once per render. The previous version re-scanned the whole history
        from scratch inside the per-part loop, making a full re-render
        O(messages x parts) for every tool call it drew. Mirrors the
        `native_returns` pre-scan the render already did for native tools.

        First-wins: pydantic-ai issues one outcome per `tool_call_id` (a retry
        gets a fresh id), so a second entry for the same id would be corruption,
        not a newer result.
        """
        by_id: dict[str, ToolReturnPart | RetryPromptPart] = {}
        for msg in self._message_history:
            if not isinstance(msg, ModelRequest):
                continue
            for part in msg.parts:
                if isinstance(part, ToolReturnPart | RetryPromptPart) and part.tool_call_id:
                    by_id.setdefault(part.tool_call_id, part)
        return by_id

    def _render_last_message_rich(self, box: Gtk.Box, msg: ModelMessage) -> None:  # noqa: C901
        # The copy-button action row is not content: _start_agent_message
        # packed it once, and the button it carries is the box's copy-text
        # accumulator. Wiping it detached the button from the widget tree
        # while its text kept updating on the orphaned object (U3/F-01). The
        # badge-pill hover guard below only concerns content children.
        action_row = getattr(box, "_grc_action_row", None)
        for child in box.get_children():
            if child is action_row:
                continue
            box.remove(child)

        # A re-render destroys any badge pill mid-hover without a
        # leave-notify-event (GTK3 doesn't synthesize one on widget removal),
        # which could leave a stale canvas highlight — same guard as
        # _render_history's full rebuild.
        cm = self._get_cm()
        if cm:
            cm.clear_highlight()

        full_text = ""
        # Native tool call+return live as sibling parts within this same
        # ModelResponse (unlike function tools, whose return is a separate
        # ToolReturnPart in a later ModelRequest) — pre-scan the returns so
        # the call part can be resolved in a single forward pass.
        native_returns = {
            p.tool_call_id: p for p in msg.parts if isinstance(p, NativeToolReturnPart)
        }
        function_returns = self._function_returns_by_call_id()
        for part in msg.parts:
            if isinstance(part, NativeToolReturnPart):
                continue
            if isinstance(part, TextPart):
                self._render_markdown_to_box(box, part.content, clear=False)
                full_text += part.content
            elif isinstance(part, ThinkingPart):
                exp, _tv = self._make_thinking_widget(
                    part.content, label=self._thinking_label(streaming=False)
                )
                box.pack_start(exp, False, False, 0)
                exp.show_all()
                full_text += _transcript_thinking(part.content)
            elif isinstance(part, ToolCallPart | NativeToolCallPart):
                tool_name = part.tool_name or "?"
                if isinstance(part, ToolCallPart):
                    summary = _parse_final_summary(part.args)
                    if tool_name == "final_result" and summary is not None:
                        # Same summary-card treatment as the streaming path — a
                        # re-render (e.g. after a settings live-swap) must not
                        # degrade the final structured output back to raw JSON.
                        widget = self._make_final_summary_widget(*summary)
                        box.pack_start(widget, False, False, 0)
                        widget.show_all()
                        full_text += _transcript_summary(*summary)
                        continue
                    ret_part: ToolReturnPart | RetryPromptPart | NativeToolReturnPart | None = (
                        function_returns.get(part.tool_call_id or "")
                    )
                else:
                    ret_part = native_returns.get(part.tool_call_id)

                exp = self._make_tool_expander(tool_name)
                args_str = _tool_args_text(part)
                self._set_tool_body(exp, args_str)

                if isinstance(ret_part, RetryPromptPart):
                    ret_content, ok, retry = ret_part.model_response(), True, True
                elif ret_part is not None:
                    ret_content = str(ret_part.content)
                    ok, retry = ret_part.outcome != "failed", False
                else:
                    ret_content, ok, retry = "", True, False

                if ret_content:
                    self._set_tool_body(exp, ret_content)
                    exp.set_label(_tool_label(tool_name, ok=ok, retry=retry, result=ret_content))
                    full_text += _transcript_tool_call(tool_name, args_str, ret_content)
                else:
                    exp.set_label(_tool_label(tool_name))
                    full_text += _transcript_tool_call(tool_name, args_str)

                box.pack_start(exp, False, False, 0)
                exp.show_all()

        if hasattr(box, "_grc_copy_btn") and box._grc_copy_btn is not None:
            box._grc_copy_btn._grc_copy_text = full_text
        box.show_all()
        self._scrolled.check_resize()

        parent = box.get_parent()
        if parent and hasattr(parent, "_grc_copy_btn"):
            parent._grc_copy_btn._grc_copy_text = full_text

    def _clear_welcome_screen(self) -> None:
        has_welcome = False
        for c in self._listbox.get_children():
            inner = c.get_child() if isinstance(c, Gtk.ListBoxRow) else c
            if inner:
                ctx = inner.get_style_context()
                if (
                    ctx.has_class("chat-welcome-box")
                    or ctx.has_class("chat-recent-header")
                    or ctx.has_class("chat-recent-item")
                ):
                    has_welcome = True
                    break
        if has_welcome:
            for child in self._listbox.get_children():
                self._listbox.remove(child)

    def _add_message_row(self, child: Gtk.Widget) -> Gtk.ListBoxRow:
        self._clear_welcome_screen()
        row = Gtk.ListBoxRow()
        row.set_activatable(False)
        row.set_selectable(False)
        row.add(child)
        row.set_margin_top(2)
        row.set_margin_bottom(2)
        self._listbox.add(row)
        row.show_all()
        self._scrolled.check_resize()
        # New rows follow only when the user is pinned to the bottom — the
        # same stickiness rule as streaming. Forcing a scroll here yanked a
        # user reading earlier content to the bottom on every tool expander
        # and error label appended mid-turn. Sending a message re-engages
        # _auto_scroll explicitly in send_message() before the row is added.
        self._scroll_to_bottom()
        return row

    def _make_final_summary_widget(self, actions: list[str], explanation: str) -> Gtk.Box:
        """Render the model's final structured output (GrcAgentResponse) as a
        readable summary card instead of a raw `final_result` tool expander:
        a bold Done header, a bulleted list of the actions taken, and the
        explanation. The model already produces this structure — the UI was
        just showing the JSON it arrives in."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_hexpand(True)
        box.set_halign(Gtk.Align.FILL)

        header = Gtk.Label()
        header.set_markup("<b>Done</b>")
        header.set_xalign(0.0)
        box.pack_start(header, False, False, 0)

        for action in actions:
            lbl = Gtk.Label()
            lbl.set_markup(f"\u2022 {GLib.markup_escape_text(action)}")
            lbl.set_line_wrap(True)
            lbl.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
            lbl.set_xalign(0.0)
            lbl.set_halign(Gtk.Align.FILL)
            lbl.set_selectable(True)
            box.pack_start(lbl, False, False, 0)

        if explanation:
            expl = Gtk.Label()
            expl.set_markup(f"<i>{GLib.markup_escape_text(explanation)}</i>")
            expl.set_line_wrap(True)
            expl.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
            expl.set_xalign(0.0)
            expl.set_halign(Gtk.Align.FILL)
            expl.set_selectable(True)
            box.pack_start(expl, False, False, 0)

        return box

    def _make_tool_expander(self, tool_name: str) -> Gtk.Expander:
        exp = Gtk.Expander(label=_tool_label_running(tool_name))
        exp.set_expanded(False)
        exp.get_style_context().add_class("chat-tool-expander")
        exp.set_hexpand(True)
        exp.set_halign(Gtk.Align.FILL)
        exp.connect("notify::expanded", self._on_expander_toggled)

        body = Gtk.Label(label="")
        body.set_line_wrap(True)
        body.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        body.set_xalign(0.0)
        body.set_halign(Gtk.Align.FILL)
        body.set_selectable(True)
        exp.add(body)
        exp._grc_tool_name = tool_name
        exp._grc_tool_body = body
        return exp

    def _set_tool_body(self, exp: Gtk.Expander, text: str) -> None:
        body = getattr(exp, "_grc_tool_body", None)
        if body is not None:
            body.set_text(_format_tool_display(text))

    def _set_tool_status(self, exp: Gtk.Expander) -> None:
        name = getattr(exp, "_grc_tool_name", "?")
        exp.set_label(_tool_label_running(name))

    def _set_tool_result(self, exp: Gtk.Expander, result: str, *, ok: bool = True) -> None:
        self._set_tool_body(exp, result)
        name = getattr(exp, "_grc_tool_name", "?")
        exp.set_label(_tool_label(name, ok=ok, result=result))

    def _append_error(self, message: str, style: str = "error") -> None:
        """Append an inline status label to the chat log.

        ``style="error"`` (the default) renders in the red error styling used
        for genuine failures. ``style="aborted"`` renders in a neutral/muted
        style instead, for user-initiated cancellations (e.g. clicking Stop)
        which are not errors and shouldn't look like one.
        """
        lbl = Gtk.Label(label=message)
        lbl.set_line_wrap(True)
        lbl.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        lbl.set_xalign(0.0)
        lbl.set_selectable(True)
        css_class = "chat-error-label" if style == "error" else "chat-aborted-label"
        lbl.get_style_context().add_class(css_class)
        self._add_message_row(lbl)

