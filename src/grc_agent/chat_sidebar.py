# ruff: noqa: E402
"""Native GTK3 ChatSidebar widget for the grc-agent desktop app.

Streams agent responses via ``agent.iter()``'s node-by-node iteration:
``ModelRequestNode`` yields ``PartStartEvent`` / ``PartDeltaEvent`` (text,
tool calls, reasoning in strict arrival order), ``CallToolsNode`` yields
``FunctionToolCallEvent`` / ``FunctionToolResultEvent``.

Message history is stored as pydantic-ai's native ``ModelMessage`` objects.
"""

import asyncio
import logging
import re

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gdk, GLib, GObject, Gtk, Pango
from pydantic_ai import (
    Agent,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartDeltaEvent,
    PartStartEvent,
    TextPartDelta,
    ThinkingPartDelta,
)
from pydantic_ai.exceptions import (
    ModelAPIError,
    ModelHTTPError,
    UnexpectedModelBehavior,
    UsageLimitExceeded,
)
from pydantic_ai.messages import ModelMessage, TextPart, ThinkingPart, ToolCallPart
from pydantic_graph import End

from .settings import get_env_value, load_settings, save_settings, upsert_env_key

_log = logging.getLogger(__name__)

_CHAT_CSS = b"""
.chat-sidebar {
    background: #1e1e1e;
}
.chat-user-label {
    background: #1a3a5c;
    color: #ffffff;
    border-radius: 8px;
    padding: 8px 10px;
}
.chat-agent-label {
    background: #2d2d2d;
    color: #e0e0e0;
    border-radius: 8px;
    padding: 8px 10px;
}
.chat-error-label {
    background: #4a1c1c;
    color: #ff8a80;
    border-radius: 8px;
    padding: 8px 10px;
}
.chat-tool-expander {
    background: #2a2a2a;
    color: #e0e0e0;
    border-radius: 4px;
    padding: 2px 6px;
    margin-top: 4px;
}
.chat-thinking-expander {
    margin-top: 4px;
}
.chat-thinking-expander > label {
    color: #999;
    font-style: italic;
    font-size: 0.9em;
}
.chat-toolbar {
    background: #252526;
    border-bottom: 1px solid #3c3c3c;
}
.chat-toolbar-btn {
    background: #333334;
    color: #e0e0e0;
    border: 1px solid #3c3c3c;
    border-radius: 4px;
    padding: 4px 10px;
}
.chat-toolbar-btn:hover {
    background: #3e3e40;
}
.chat-toolbar-btn:active {
    background: #4e4e50;
}
.chat-toolbar-sep {
    color: #3c3c3c;
}
.graph-badge {
    background: #1a3a5c;
    color: #9cdcfe;
    border-radius: 10px;
    padding: 2px 12px;
    font-size: 0.85em;
    font-weight: bold;
}
.chat-side-toggle {
    background: #252526;
    color: #e0e0e0;
    border-right: 1px solid #3c3c3c;
    padding: 4px 2px;
    min-width: 18px;
}
.chat-side-toggle:hover {
    background: #333334;
}
.chat-entry {
    background: #2d2d2d;
    color: #e0e0e0;
    border: 1px solid #3c3c3c;
    border-radius: 6px;
    padding: 10px 8px;
    min-height: 42px;
}
.chat-entry placeholder {
    color: #777;
}
.chat-send-btn {
    background: #0e639c;
    color: #ffffff;
    border: none;
    border-radius: 6px;
    padding: 8px 16px;
    font-weight: bold;
}
.chat-send-btn:hover {
    background: #1177bb;
}
.chat-send-btn:active {
    background: #094771;
}
.chat-msg-list {
    background: #1e1e1e;
}
.chat-status-bar {
    background: #252526;
    color: #e0e0e0;
    border-top: 1px solid #3c3c3c;
    padding: 3px 8px;
    font-size: 0.9em;
}
"""

_PROVIDER_LABELS = {
    "ollama": "Ollama (local)",
    "openrouter": "OpenRouter (cloud)",
    "ollama_cloud": "Ollama Cloud",
}
_PROVIDER_MODEL_KEY = {
    "ollama": "ollama_model",
    "openrouter": "openrouter_model",
    "ollama_cloud": "ollama_cloud_model",
}
_PROVIDER_API_KEY = {
    "ollama": None,
    "openrouter": "OPENROUTER_API_KEY",
    "ollama_cloud": "OLLAMA_CLOUD_API_KEY",
}
_PROVIDER_ORDER = ("ollama", "openrouter", "ollama_cloud")

_css_applied = False


def _apply_css() -> None:
    global _css_applied
    if _css_applied:
        return
    screen = Gdk.Screen.get_default()
    if screen is None:
        return
    provider = Gtk.CssProvider()
    provider.load_from_data(_CHAT_CSS)
    Gtk.StyleContext.add_provider_for_screen(
        screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )
    _css_applied = True


def _markdown_to_pango(text: str) -> str:
    """Convert basic markdown to Pango markup for Gtk.Label.set_markup().

    Supports: fenced code blocks, headings, **bold**, *italic*, `inline
    code`, [link](url). XML special chars are escaped before tags are
    applied so model output containing ``<``, ``>``, ``&`` is safe.
    """
    code_blocks: list[str] = []

    def _stash(m: re.Match) -> str:
        code_blocks.append(m.group(1))
        return f"\x00B{len(code_blocks) - 1}\x00"

    work = re.sub(r"```(?:\w*)\n?(.*?)```", _stash, text, flags=re.DOTALL)
    work = work.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    work = re.sub(r"^#{1,6}\s+(.+)$", r"<b>\1</b>", work, flags=re.MULTILINE)
    work = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", work)
    work = re.sub(r"(^|[^*])\*([^*\n]+)\*", r"\1<i>\2</i>", work)
    work = re.sub(r"`([^`\n]+)`", r"<tt>\1</tt>", work)
    work = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', work)

    def _restore(m: re.Match) -> str:
        esc = (
            code_blocks[int(m.group(1))]
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        return f"<tt>{esc}</tt>"

    return re.sub(r"\x00B(\d+)\x00", _restore, work)


def _safe_set_markup(label: Gtk.Label, text: str) -> None:
    try:
        label.set_markup(_markdown_to_pango(text))
    except Exception:
        # Fall back to raw text if markup fails (e.g. malformed markdown from the model)
        label.set_text(text)


class _StreamCtx:
    """Per-call mutable streaming state — held outside ``send_message``
    so the node/event handler helpers can stay small and flat."""

    __slots__ = (
        "box",
        "text_lbl",
        "text_acc",
        "think_body",
        "think_acc",
        "tools",
    )

    def __init__(self, box: Gtk.Box) -> None:
        self.box = box
        self.text_lbl: Gtk.Label | None = None
        self.text_acc = ""
        self.think_body: Gtk.Label | None = None
        self.think_acc = ""
        self.tools: dict[str, Gtk.Expander] = {}


class ChatSidebar(Gtk.Box):
    """Complete chat sidebar: toolbar, streaming message list, input area.

    Toolbar buttons emit GObject signals for ``desktop_app.py`` to connect.
    The Send button doubles as a Stop/abort button while a request is running.
    """

    __gsignals__ = {
        "new-session-clicked": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "toggle-blocks-panel": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        _apply_css()
        self.get_style_context().add_class("chat-sidebar")
        self._agent: Agent | None = None
        self._flowgraph_proxy: object | None = None
        self._message_history: list[ModelMessage] = []
        self._busy = False
        self._chat_task: asyncio.Task | None = None
        self._chat_abort: asyncio.Future | None = None

        # Slim side toggle for GRC block library
        self._blocks_toggle = Gtk.Button()
        self._blocks_toggle.set_tooltip_text("Toggle block library")
        self._blocks_toggle.get_style_context().add_class("chat-side-toggle")
        self._blocks_toggle.set_valign(Gtk.Align.FILL)
        self._blocks_arrow = Gtk.Image.new_from_icon_name("pan-end-symbolic", Gtk.IconSize.SMALL_TOOLBAR)
        self._blocks_toggle.set_image(self._blocks_arrow)
        self._blocks_toggle.connect("clicked", lambda *_: self.emit("toggle-blocks-panel"))
        self._blocks_expanded = False
        self.pack_start(self._blocks_toggle, False, False, 0)

        # Vertical content area
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._content = content
        self._build_toolbar(content)
        self._build_message_list(content)
        self._build_input_area(content)
        self._build_status_bar(content)
        self.pack_start(content, True, True, 0)

    def _build_toolbar(self, content: Gtk.Box) -> None:
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        bar.set_border_width(4)

        def _signal_btn(label: str, tooltip: str, signal: str) -> Gtk.Button:
            b = Gtk.Button.new_with_label(label)
            b.set_tooltip_text(tooltip)
            b.get_style_context().add_class("chat-toolbar-btn")
            b.connect("clicked", lambda *_: self.emit(signal))
            bar.pack_start(b, False, False, 0)
            return b

        _signal_btn("New", "Clear chat history", "new-session-clicked")

        # Active graph badge
        self._graph_label = Gtk.Label(label="no graph")
        self._graph_label.get_style_context().add_class("graph-badge")
        bar.pack_start(self._graph_label, False, False, 8)

        # Spacer
        spacer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        bar.pack_start(spacer, True, True, 0)

        # Settings
        gear = Gtk.Button.new_with_label("Settings")
        gear.set_tooltip_text("Provider / model settings")
        gear.get_style_context().add_class("chat-toolbar-btn")
        gear.connect("clicked", lambda *_: self._open_settings())
        bar.pack_start(gear, False, False, 0)

        bar.get_style_context().add_class("chat-toolbar")
        content.pack_start(bar, False, False, 0)

    def _build_message_list(self, content: Gtk.Box) -> None:
        self._listbox = Gtk.ListBox()
        self._listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self._listbox.set_activate_on_single_click(False)
        self._listbox.set_border_width(4)
        self._listbox.get_style_context().add_class("chat-msg-list")

        self._scrolled = Gtk.ScrolledWindow()
        self._scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._scrolled.set_vexpand(True)
        self._scrolled.add(self._listbox)

        content.pack_start(self._scrolled, True, True, 0)

    def _build_input_area(self, content: Gtk.Box) -> None:
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        box.set_border_width(6)

        self._entry = Gtk.Entry()
        self._entry.set_placeholder_text("Open a flowgraph in GRC to start chatting...")
        self._entry.set_hexpand(True)
        self._entry.get_style_context().add_class("chat-entry")
        self._entry.connect("activate", self._on_entry_activate)
        self._entry.set_sensitive(False)

        self._send_btn = Gtk.Button.new_with_label("Send")
        self._send_btn.get_style_context().add_class("chat-send-btn")
        self._send_btn.connect("clicked", self._on_send_clicked)
        self._send_btn.set_sensitive(False)

        box.pack_start(self._entry, True, True, 0)
        box.pack_start(self._send_btn, False, False, 0)
        content.pack_start(box, False, False, 0)

    def _build_status_bar(self, content: Gtk.Box) -> None:
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        bar.get_style_context().add_class("chat-status-bar")

        self._status_label = Gtk.Label(label="")
        self._status_label.set_halign(Gtk.Align.START)
        self._status_label.set_xalign(0.0)
        self._status_label.set_hexpand(True)
        self._status_label.set_max_width_chars(60)
        self._status_label.set_ellipsize(Pango.EllipsizeMode.END)
        bar.pack_start(self._status_label, True, True, 0)

        content.pack_start(bar, False, False, 0)

    def set_status(self, msg: str, *, error: bool = False) -> None:
        self._status_label.set_text(msg)
        if error:
            self._status_label.get_style_context().remove_class("validation-valid")
            self._status_label.get_style_context().add_class("validation-invalid")
        else:
            self._status_label.get_style_context().remove_class("validation-invalid")
            self._status_label.get_style_context().remove_class("validation-valid")

    def set_active_graph(self, name: str | None) -> None:
        self._graph_label.set_text(f"{name} active" if name else "no graph")

    def set_input_enabled(self, enabled: bool) -> None:
        if not self._busy:
            self._entry.set_sensitive(enabled)
            self._send_btn.set_sensitive(enabled)
        if enabled:
            self._entry.set_placeholder_text("Ask about your flowgraph...")

    def set_blocks_expanded(self, expanded: bool) -> None:
        self._blocks_expanded = expanded
        icon = "pan-start-symbolic" if expanded else "pan-end-symbolic"
        self._blocks_arrow.set_from_icon_name(icon, Gtk.IconSize.SMALL_TOOLBAR)

    def set_agent(self, agent: Agent) -> None:
        self._agent = agent

    def set_flowgraph_proxy(self, proxy: object) -> None:
        self._flowgraph_proxy = proxy

    def clear_messages(self) -> None:
        if self._chat_task and not self._chat_task.done():
            self._chat_task.cancel()
        for child in self._listbox.get_children():
            self._listbox.remove(child)
        self._message_history = []

    def stop_chat(self) -> None:
        if self._chat_task and not self._chat_task.done():
            self._chat_task.cancel()

    async def _stream_request(self, ctx: _StreamCtx, node, run) -> None:
        async with node.stream(run.ctx) as stream:
            async for event in stream:
                if isinstance(event, PartStartEvent):
                    self._on_part_start(ctx, event)
                elif isinstance(event, PartDeltaEvent):
                    self._on_part_delta(ctx, event)

    async def _stream_tools(self, ctx: _StreamCtx, node, run) -> None:
        async with node.stream(run.ctx) as stream:
            async for event in stream:
                if isinstance(event, FunctionToolCallEvent):
                    tcid = event.part.tool_call_id or ""
                    exp = ctx.tools.get(tcid)
                    if exp is not None:
                        self._set_tool_status(exp, "running")
                elif isinstance(event, FunctionToolResultEvent):
                    tcid = event.tool_call_id or ""
                    exp = ctx.tools.get(tcid)
                    if exp is not None:
                        self._set_tool_result(exp, str(event.part.content))

    def _on_part_start(self, ctx: _StreamCtx, event: PartStartEvent) -> None:
        part = event.part
        if isinstance(part, TextPart):
            self._close_thinking(ctx)
            self._close_text(ctx)
            ctx.text_acc = part.content or ""
            _safe_set_markup(self._ensure_text(ctx), ctx.text_acc)
        elif isinstance(part, ToolCallPart):
            self._close_text(ctx)
            self._close_thinking(ctx)
            tcid = part.tool_call_id or ""
            exp = self._make_tool_expander(part.tool_name or "?")
            if part.args:
                self._set_tool_body(exp, str(part.args))
            ctx.box.pack_start(exp, False, False, 0)
            exp.show_all()
            ctx.tools[tcid] = exp
        elif isinstance(part, ThinkingPart):
            self._close_text(ctx)
            body = self._ensure_thinking(ctx)
            ctx.think_acc = part.content or ""
            body.set_text(ctx.think_acc)

    def _on_part_delta(self, ctx: _StreamCtx, event: PartDeltaEvent) -> None:
        delta = event.delta
        if isinstance(delta, TextPartDelta):
            self._close_thinking(ctx)
            ctx.text_acc += delta.content_delta
            lbl = self._ensure_text(ctx)
            lbl.set_text(ctx.text_acc)
        elif isinstance(delta, ThinkingPartDelta):
            self._close_text(ctx)
            ctx.think_acc += delta.content_delta
            self._ensure_thinking(ctx).set_text(ctx.think_acc)

    def _close_text(self, ctx: _StreamCtx) -> None:
        if ctx.text_lbl is not None and ctx.text_acc:
            _safe_set_markup(ctx.text_lbl, ctx.text_acc)
        ctx.text_lbl = None
        ctx.text_acc = ""

    def _close_thinking(self, ctx: _StreamCtx) -> None:
        ctx.think_body = None
        ctx.think_acc = ""

    def _ensure_text(self, ctx: _StreamCtx) -> Gtk.Label:
        if ctx.text_lbl is None:
            ctx.text_lbl = self._make_text_label()
            ctx.box.pack_start(ctx.text_lbl, False, False, 0)
            ctx.text_lbl.show_all()
        return ctx.text_lbl

    def _ensure_thinking(self, ctx: _StreamCtx) -> Gtk.Label:
        if ctx.think_body is None:
            exp = Gtk.Expander(label="Thinking...")
            exp.set_expanded(False)
            exp.get_style_context().add_class("chat-thinking-expander")
            body = Gtk.Label(label="")
            body.set_line_wrap(True)
            body.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
            body.set_xalign(0.0)
            body.set_selectable(True)
            exp.add(body)
            ctx.box.pack_start(exp, False, False, 0)
            exp.show_all()
            ctx.think_body = body
        return ctx.think_body

    def _make_text_label(self) -> Gtk.Label:
        lbl = Gtk.Label()
        lbl.set_line_wrap(True)
        lbl.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        lbl.set_xalign(0.0)
        lbl.set_halign(Gtk.Align.START)
        lbl.set_selectable(True)
        lbl.get_style_context().add_class("chat-agent-label")
        return lbl

    def _append_user_message(self, text: str) -> None:
        lbl = Gtk.Label(label=text)
        lbl.set_line_wrap(True)
        lbl.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        lbl.set_xalign(1.0)
        lbl.set_halign(Gtk.Align.END)
        lbl.set_selectable(True)
        lbl.get_style_context().add_class("chat-user-label")
        lbl.set_margin_start(40)
        self._add_message_row(lbl)

    def _start_agent_message(self) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._add_message_row(box)
        return box

    def _add_message_row(self, child: Gtk.Widget) -> None:
        row = Gtk.ListBoxRow()
        row.set_activatable(False)
        row.set_selectable(False)
        row.add(child)
        row.set_margin_top(2)
        row.set_margin_bottom(2)
        self._listbox.add(row)
        row.show_all()
        self._scroll_to_bottom()

    def _make_tool_expander(self, tool_name: str) -> Gtk.Expander:
        exp = Gtk.Expander(label=f"\u2699 {tool_name} ...")
        exp.set_expanded(False)
        exp.get_style_context().add_class("chat-tool-expander")
        body = Gtk.Label(label="")
        body.set_line_wrap(True)
        body.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        body.set_xalign(0.0)
        body.set_selectable(True)
        exp.add(body)
        exp._grc_tool_name = tool_name
        exp._grc_tool_body = body
        return exp

    def _set_tool_body(self, exp: Gtk.Expander, text: str) -> None:
        body = getattr(exp, "_grc_tool_body", None)
        if body is not None:
            body.set_text(text)

    def _set_tool_status(self, exp: Gtk.Expander, status: str) -> None:
        name = getattr(exp, "_grc_tool_name", "?")
        if status == "running":
            exp.set_label(f"\u2699 {name} ...")

    def _set_tool_result(self, exp: Gtk.Expander, result: str) -> None:
        self._set_tool_body(exp, result)
        name = getattr(exp, "_grc_tool_name", "?")
        exp.set_label(f"\u2699 {name} \u2713")

    def _append_error(self, message: str) -> None:
        lbl = Gtk.Label(label=message)
        lbl.set_line_wrap(True)
        lbl.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        lbl.set_xalign(0.0)
        lbl.set_selectable(True)
        lbl.get_style_context().add_class("chat-error-label")
        self._add_message_row(lbl)

    def _on_entry_activate(self, _entry: Gtk.Entry) -> None:
        self._dispatch_send()

    def _on_send_clicked(self, _btn: Gtk.Button) -> None:
        if self._busy:
            self.stop_chat()
            return
        self._dispatch_send()

    def _dispatch_send(self) -> None:
        text = self._entry.get_text()
        if not text.strip() or self._busy:
            return
        self._entry.set_text("")
        self._append_user_message(text)
        self._set_busy(True)
        self._chat_task = asyncio.ensure_future(self._run_agent_turn(text))

    async def _run_agent_turn(self, text: str) -> None:  # noqa: C901
        if self._agent is None:
            self._append_error("No agent configured.")
            return
        ctx = _StreamCtx(self._start_agent_message())
        try:
            async with self._agent.iter(
                text,
                message_history=self._message_history,
                deps=self._flowgraph_proxy,
            ) as run:
                node = run.next_node
                while node is not None and not isinstance(node, End):
                    if Agent.is_model_request_node(node):
                        await self._stream_request(ctx, node, run)
                    elif Agent.is_call_tools_node(node):
                        self._close_text(ctx)
                        self._close_thinking(ctx)
                        await self._stream_tools(ctx, node, run)
                    self._scroll_to_bottom()
                    node = await run.next(node)

            if run.result is not None:
                self._message_history = run.result.all_messages()
        except asyncio.CancelledError:
            self._append_error("[aborted]")
            raise
        except ModelHTTPError as e:
            _log.exception("agent run failed with HTTP error")
            msg = f"Model HTTP {e.status_code} Error"
            if e.body:
                msg += f": {e.body}"
            else:
                msg += f" from {e.model_name}"
            self._append_error(msg)
        except UsageLimitExceeded as e:
            _log.exception("agent run failed due to usage limit")
            self._append_error(f"Usage Limit Exceeded: {e}")
        except ModelAPIError as e:
            _log.exception("agent run failed with API error")
            self._append_error(f"Model API Error: {e}")
        except UnexpectedModelBehavior as e:
            _log.exception("agent run failed with unexpected behavior")
            self._append_error(f"Unexpected Model Behavior: {e}")
        except Exception as e:
            _log.exception("agent run failed")
            self._append_error(f"Agent error: {e}")
        finally:
            if ctx.text_lbl is not None and ctx.text_acc:
                _safe_set_markup(ctx.text_lbl, ctx.text_acc)
                ctx.text_lbl = None
                ctx.text_acc = ""
            self._set_busy(False)
            self._scroll_to_bottom()

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        can_type = self._flowgraph_proxy is not None
        if busy:
            self._send_btn.set_label("Stop")
            self._send_btn.set_sensitive(True)
            self._entry.set_sensitive(False)
        else:
            self._send_btn.set_label("Send")
            self._send_btn.set_sensitive(can_type)
            self._entry.set_sensitive(can_type)
            if can_type:
                self._entry.grab_focus()

    def _scroll_to_bottom(self) -> None:
        def _do_scroll():
            adj = self._scrolled.get_vadjustment()
            adj.set_value(adj.get_upper() - adj.get_page_size())
            return False
        GLib.idle_add(_do_scroll)

    def _open_settings(self) -> None:  # noqa: C901
        toplevel = self.get_toplevel()
        if not isinstance(toplevel, Gtk.Window):
            toplevel = None
        dlg = Gtk.Dialog(title="Chat Settings", transient_for=toplevel, modal=True)
        dlg.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dlg.add_button("Save", Gtk.ResponseType.APPLY)
        content = dlg.get_content_area()
        content.set_spacing(8)
        content.set_border_width(12)

        cfg = load_settings()
        grid = Gtk.Grid(column_spacing=8, row_spacing=8)

        grid.attach(Gtk.Label(label="Provider:"), 0, 0, 1, 1)
        provider_combo = Gtk.ComboBoxText()
        for p in _PROVIDER_ORDER:
            provider_combo.append_text(_PROVIDER_LABELS[p])
        provider_combo.set_active(_PROVIDER_ORDER.index(cfg["provider"]))
        grid.attach(provider_combo, 1, 0, 1, 1)

        grid.attach(Gtk.Label(label="Model:"), 0, 1, 1, 1)
        model_entry = Gtk.Entry()
        model_entry.set_text(cfg["model"])
        model_entry.set_hexpand(True)
        grid.attach(model_entry, 1, 1, 1, 1)

        grid.attach(Gtk.Label(label="API Key:"), 0, 2, 1, 1)
        key_entry = Gtk.Entry()
        key_entry.set_visibility(False)
        grid.attach(key_entry, 1, 2, 1, 1)

        info = Gtk.Label(label="Changes take effect after restart.")
        info.get_style_context().add_class("dim-label")

        def _sync_provider_fields(combo: Gtk.ComboBoxText) -> None:
            idx = combo.get_active()
            if idx < 0:
                return
            p = _PROVIDER_ORDER[idx]
            model_entry.set_text(cfg.get(_PROVIDER_MODEL_KEY[p], ""))
            key_var = _PROVIDER_API_KEY[p]
            if key_var:
                key_entry.set_text(get_env_value(key_var) or "")
                key_entry.set_sensitive(True)
            else:
                key_entry.set_text("")
                key_entry.set_sensitive(False)

        provider_combo.connect("changed", _sync_provider_fields)
        _sync_provider_fields(provider_combo)

        content.pack_start(grid, False, False, 0)
        content.pack_start(info, False, False, 0)
        content.show_all()

        if dlg.run() == Gtk.ResponseType.APPLY:
            idx = provider_combo.get_active()
            provider = _PROVIDER_ORDER[idx] if idx >= 0 else "ollama"
            model = model_entry.get_text().strip()
            if model:
                save_settings(provider, model)
            key_var = _PROVIDER_API_KEY.get(provider)
            key_val = key_entry.get_text().strip()
            if key_var:
                upsert_env_key(key_var, key_val)



        dlg.destroy()
