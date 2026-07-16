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
from pathlib import Path

import gi
from bs4 import BeautifulSoup, NavigableString
from markdown_it import MarkdownIt

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

from .settings import (
    get_env_value,
    load_recent_sessions,
    load_settings,
    save_recent_session,
    save_settings,
    upsert_env_key,
)

_log = logging.getLogger(__name__)

_CHAT_CSS = b"""
.chat-sidebar {
    background: #ffffff;
    border-left: 1px solid #d0d0d0;
}
.chat-user-label {
    background: #e1f5fe;
    color: #0d47a1;
    border: 1px solid #b3e5fc;
    border-radius: 8px;
    padding: 8px 10px;
}
.chat-agent-label {
    background: #f5f5f5;
    color: #212121;
    border: 1px solid #e0e0e0;
    border-radius: 8px;
    padding: 8px 10px;
}
.chat-error-label {
    background: #ffebee;
    color: #c62828;
    border: 1px solid #ffcdd2;
    border-radius: 8px;
    padding: 8px 10px;
}
.chat-tool-expander {
    background: #fafafa;
    color: #424242;
    border: 1px solid #e0e0e0;
    border-radius: 4px;
    padding: 2px 6px;
    margin-top: 4px;
}
.chat-thinking-expander {
    margin-top: 4px;
}
.chat-thinking-expander > label {
    color: #666666;
    font-style: italic;
    font-size: 0.9em;
}
.chat-toolbar {
    background: #f5f5f5;
    border-bottom: 1px solid #e0e0e0;
}
.chat-toolbar-btn {
    background: #ffffff;
    color: #333333;
    border: 1px solid #cccccc;
    border-radius: 4px;
    padding: 4px 10px;
}
.chat-toolbar-btn:hover {
    background: #f0f0f0;
}
.chat-toolbar-btn:active {
    background: #e0e0e0;
}
.chat-toolbar-sep {
    color: #cccccc;
}
.graph-badge {
    background: #e8f5e9;
    color: #2e7d32;
    border: 1px solid #c8e6c9;
    border-radius: 10px;
    padding: 2px 12px;
    font-size: 0.85em;
    font-weight: bold;
}
.chat-side-toggle {
    background: #f5f5f5;
    color: #333333;
    border-right: 1px solid #d0d0d0;
    padding: 4px 2px;
    min-width: 18px;
}
.chat-side-toggle:hover {
    background: #e0e0e0;
}
.chat-entry {
    background: #ffffff;
    color: #000000;
    border: 1px solid #cccccc;
    border-radius: 6px;
    padding: 10px 8px;
    min-height: 42px;
}
.chat-entry placeholder {
    color: #888888;
}
.chat-send-btn {
    background: #1976d2;
    color: #ffffff;
    border: none;
    border-radius: 6px;
    padding: 8px 16px;
    font-weight: bold;
}
.chat-send-btn:hover {
    background: #1565c0;
}
.chat-send-btn:active {
    background: #0d47a1;
}
.chat-msg-list {
    background: #ffffff;
}
.chat-status-bar {
    background: #f5f5f5;
    color: #333333;
    border-top: 1px solid #e0e0e0;
    padding: 3px 8px;
    font-size: 0.9em;
}
.chat-monospace {
    font-family: monospace;
}
.chat-welcome-box {
    background: #fafafa;
    border: 1px solid #e0e0e0;
    border-radius: 8px;
    padding: 16px;
    margin: 12px;
}
.chat-recent-header {
    font-size: 1.05em;
    font-weight: bold;
    color: #424242;
    margin-top: 16px;
    margin-bottom: 8px;
    margin-left: 12px;
}
.chat-recent-item {
    background: #ffffff;
    color: #212121;
    border: 1px solid #e0e0e0;
    border-radius: 6px;
    padding: 8px 12px;
    margin-bottom: 6px;
    margin-left: 12px;
    margin-right: 12px;
}
.chat-recent-item:hover {
    background: #e3f2fd;
    border-color: #90caf9;
    color: #0d47a1;
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


def _markdown_to_pango(text: str) -> str:  # noqa: C901
    """Convert basic markdown to Pango markup for Gtk.Label.set_markup().

    Supports bold, italic, inline/block code, links, headings, lists,
    and formats markdown tables as clean monospace tables.
    """
    def _format_table(table_soup) -> str:
        headers = []
        thead = table_soup.find("thead")
        if thead:
            headers = [th.get_text().strip() for th in thead.find_all("th")]

        tbody = table_soup.find("tbody")
        rows = []
        if tbody:
            for tr in tbody.find_all("tr"):
                rows.append([td.get_text().strip() for td in tr.find_all(["td", "th"])])
        else:
            for tr in table_soup.find_all("tr"):
                rows.append([td.get_text().strip() for td in tr.find_all(["td", "th"])])

        if not headers and rows:
            headers = rows[0]
            rows = rows[1:]

        num_cols = max(len(headers), max((len(r) for r in rows), default=0))
        if num_cols == 0:
            return ""

        headers += [""] * (num_cols - len(headers))
        for r in rows:
            r += [""] * (num_cols - len(r))

        col_widths = [0] * num_cols
        for i in range(num_cols):
            col_widths[i] = max(
                len(headers[i]),
                max((len(r[i]) for r in rows), default=0)
            )

        lines = []
        header_line = " | ".join(f"{h:<{col_widths[i]}}" for i, h in enumerate(headers))
        lines.append("| " + header_line + " |")

        sep_line = "-+-".join("-" * col_widths[i] for i in range(num_cols))
        lines.append("+" + sep_line + "+")

        for r in rows:
            row_line = " | ".join(f"{val:<{col_widths[i]}}" for i, val in enumerate(r))
            lines.append("| " + row_line + " |")

        return "\n" + "\n".join(lines) + "\n"

    def _node_to_pango(node) -> str:  # noqa: C901
        if isinstance(node, NavigableString):
            return str(node).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        tag = node.name
        if not tag:
            return ""

        inner_text = "".join(_node_to_pango(child) for child in node.children)

        if tag in ("p", "div"):
            return f"{inner_text}\n"
        elif tag in ("strong", "b"):
            return f"<b>{inner_text}</b>"
        elif tag in ("em", "i"):
            return f"<i>{inner_text}</i>"
        elif tag in ("code", "tt") or tag == "pre":
            return f"<tt>{inner_text.replace(' ', '\u00A0')}</tt>"
        elif tag == "a":
            href = node.get("href", "")
            href_esc = href.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
            return f'<a href="{href_esc}">{inner_text}</a>'
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            return f"\n<b>{inner_text}</b>\n"
        elif tag == "ul" or tag == "ol":
            return f"{inner_text}\n"
        elif tag == "li":
            return f" • {inner_text}\n"
        elif tag == "table":
            table_str = _format_table(node)
            table_str_esc = table_str.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            return f"<tt>{table_str_esc.replace(' ', '\u00A0')}</tt>"
        elif tag in ("thead", "tbody", "tr", "td", "th"):
            return ""
        else:
            return inner_text

    try:
        md = MarkdownIt("commonmark").enable("table")
        html = md.render(text)
        soup = BeautifulSoup(html, "html.parser")
        result = "".join(_node_to_pango(child) for child in soup.contents)
        return result.strip()
    except Exception as e:
        _log.warning("Failed to render markdown via markdown-it: %s", e)
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


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
        "full_raw_text",
    )

    def __init__(self, box: Gtk.Box) -> None:
        self.box = box
        self.text_lbl: Gtk.Label | None = None
        self.text_acc = ""
        self.think_body: Gtk.Label | None = None
        self.think_acc = ""
        self.tools: dict[str, Gtk.Expander] = {}
        self.full_raw_text = ""


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

        _signal_btn("New Session", "Clear chat history", "new-session-clicked")

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
        cm = getattr(proxy, "_canvas_manager", None)
        path = cm.path if cm else None
        self.load_history_for_path(path)

    def clear_messages(self) -> None:
        if self._chat_task and not self._chat_task.done():
            self._chat_task.cancel()
        self._message_history = []
        self._save_history()
        self._render_history()

    def _render_history(self) -> None:  # noqa: C901
        for child in self._listbox.get_children():
            self._listbox.remove(child)

        if not self._message_history:
            self._render_welcome_screen()
            self._listbox.show_all()
            return

        for msg in self._message_history:
            cls_name = msg.__class__.__name__
            if cls_name == "ModelRequest":
                for part in msg.parts:
                    if part.__class__.__name__ == "UserPromptPart":
                        content = part.content
                        if not isinstance(content, str):
                            parts = []
                            for item in content:
                                if hasattr(item, "text"):
                                    parts.append(item.text)
                                elif isinstance(item, str):
                                    parts.append(item)
                            content = "".join(parts)
                        self._append_user_message(content)
            elif cls_name == "ModelResponse":
                box = self._start_agent_message()
                self._render_last_message_rich(box, msg)
        self._scroll_to_bottom()

    def _render_welcome_screen(self) -> None:
        # 1. Welcome Card
        welcome_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        welcome_box.get_style_context().add_class("chat-welcome-box")

        # Title
        title_lbl = Gtk.Label()
        title_lbl.set_markup("<span size='large' weight='bold'>GRC Agent Chat</span>")
        title_lbl.set_xalign(0.0)
        welcome_box.pack_start(title_lbl, False, False, 0)

        # Subtitle
        sub_lbl = Gtk.Label()
        sub_lbl.set_line_wrap(True)
        sub_lbl.set_xalign(0.0)

        path = None
        page = None
        if self._flowgraph_proxy is not None:
            cm = getattr(self._flowgraph_proxy, "_canvas_manager", None)
            if cm:
                page = cm.current_page
                path = cm.path

        if page is not None:
            sub_lbl.set_markup(
                "<span fgcolor='#666666' size='small'>Ask a question or request a modification for this flowgraph.</span>"
            )
        else:
            sub_lbl.set_markup(
                "No active flowgraph file open.\n"
                "Open a saved flowgraph to start chatting, or load a recent session below:"
            )
        welcome_box.pack_start(sub_lbl, False, False, 0)
        self._listbox.add(welcome_box)

        # 2. Recent Sessions List
        sessions = load_recent_sessions()

        # Filter out current active path from the suggestions if we are already inside it
        if path:
            abs_path = str(Path(path).resolve())
            sessions = [s for s in sessions if str(Path(s).resolve()) != abs_path]

        if sessions:
            # Header
            hdr_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            hdr_box.get_style_context().add_class("chat-recent-header")

            icon = Gtk.Image.new_from_icon_name("document-open-recent-symbolic", Gtk.IconSize.MENU)
            lbl = Gtk.Label()
            lbl.set_markup("<b>Recent Sessions</b>")

            hdr_box.pack_start(icon, False, False, 0)
            hdr_box.pack_start(lbl, False, False, 0)
            self._listbox.add(hdr_box)

            # List items
            for s in sessions:
                btn = Gtk.Button()
                btn.get_style_context().add_class("chat-recent-item")
                btn.set_relief(Gtk.ReliefStyle.NONE)

                # Content layout
                box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                item_icon = Gtk.Image.new_from_icon_name("text-x-generic-symbolic", Gtk.IconSize.MENU)

                text_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)

                name_lbl = Gtk.Label()
                name_lbl.set_markup(f"<b>{Path(s).name}</b>")
                name_lbl.set_xalign(0.0)

                path_lbl = Gtk.Label()
                path_lbl.set_markup(f"<span fgcolor='#777777' size='small'>{Path(s).parent}</span>")
                path_lbl.set_xalign(0.0)
                path_lbl.set_ellipsize(Pango.EllipsizeMode.START)

                text_vbox.pack_start(name_lbl, False, False, 0)
                text_vbox.pack_start(path_lbl, False, False, 0)

                box.pack_start(item_icon, False, False, 0)
                box.pack_start(text_vbox, True, True, 0)

                btn.add(box)
                btn.set_tooltip_text(s)

                # Connect click handler
                btn.connect("clicked", lambda _, p=s: self._open_recent_session(p))
                self._listbox.add(btn)

    def _open_recent_session(self, path: str) -> None:
        if not path or not Path(path).exists():
            self.set_status("File not found on disk.", error=True)
            return

        cm = getattr(self._flowgraph_proxy, "_canvas_manager", None) if self._flowgraph_proxy else None
        if not cm or not cm.window:
            self.set_status("GRC window not available.", error=True)
            return

        notebook = getattr(cm.window, "notebook", None)
        if not notebook:
            self.set_status("GRC notebook not available.", error=True)
            return

        target_path = Path(path).resolve()
        for i in range(notebook.get_n_pages()):
            p = notebook.get_nth_page(i)
            p_path = getattr(p, "file_path", None)
            if p_path:
                try:
                    if Path(p_path).resolve() == target_path:
                        notebook.set_current_page(i)
                        self.set_status("Switched to active tab.")
                        return
                except Exception:
                    pass

        try:
            cm.window.new_page(path)
            self.set_status("Opened session file.")
        except Exception as e:
            _log.error("Failed to open recent session %s: %s", path, e)
            self.set_status(f"Failed to open session: {e}", error=True)

    def _save_history(self) -> None:
        if self._flowgraph_proxy is None:
            return
        cm = getattr(self._flowgraph_proxy, "_canvas_manager", None)
        path = cm.path if cm else None
        if not path:
            return
        try:
            import json

            from pydantic_core import to_jsonable_python
            hist_path = Path(path).parent / ".grc_agent" / (Path(path).name + ".chat.json")
            hist_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)

            data = to_jsonable_python(self._message_history)

            import os
            import tempfile
            fd, tmp_file = tempfile.mkstemp(dir=str(hist_path.parent))
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_file, hist_path)
                dir_fd = os.open(str(hist_path.parent), os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except Exception:
                if os.path.exists(tmp_file):
                    os.unlink(tmp_file)
                raise
        except Exception as e:
            _log.error("Failed to save chat history: %s", e)

    def load_history_for_path(self, path: str | None) -> None:
        self._message_history = []
        if path:
            save_recent_session(path)
            hist_path = Path(path).parent / ".grc_agent" / (Path(path).name + ".chat.json")
            if hist_path.exists():
                try:
                    import json

                    from pydantic_ai import ModelMessagesTypeAdapter
                    content = hist_path.read_text(encoding="utf-8")
                    if content.strip():
                        data = json.loads(content)
                        self._message_history = ModelMessagesTypeAdapter.validate_python(data)
                except Exception as e:
                    _log.error("Failed to load chat history from %s: %s", hist_path, e)
        self._render_history()

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
                        res_str = str(event.part.content)
                        self._set_tool_result(exp, res_str)
                        ctx.full_raw_text += f"<Tool Result: {res_str}>\n"
                        self._update_copy_text(ctx.box, ctx.full_raw_text)

    def _on_part_start(self, ctx: _StreamCtx, event: PartStartEvent) -> None:
        part = event.part
        if isinstance(part, TextPart):
            self._close_thinking(ctx)
            self._close_text(ctx)
            ctx.text_acc = part.content or ""
            ctx.full_raw_text += part.content or ""
            lbl = self._ensure_text(ctx)
            lbl.set_text(ctx.text_acc)
            self._update_copy_text(ctx.box, ctx.full_raw_text)
        elif isinstance(part, ToolCallPart):
            self._close_text(ctx)
            self._close_thinking(ctx)
            tcid = part.tool_call_id or ""
            exp = self._make_tool_expander(part.tool_name or "?")
            args_str = str(part.args) if part.args else ""
            if args_str:
                self._set_tool_body(exp, args_str)
            ctx.box.pack_start(exp, False, False, 0)
            exp.show_all()
            ctx.tools[tcid] = exp
            ctx.full_raw_text += f"<Tool Call: {part.tool_name}>\nArgs: {args_str}\n"
            self._update_copy_text(ctx.box, ctx.full_raw_text)
        elif isinstance(part, ThinkingPart):
            self._close_text(ctx)
            body = self._ensure_thinking(ctx)
            ctx.think_acc = part.content or ""
            ctx.full_raw_text += part.content or ""
            body.set_text(ctx.think_acc)
            self._update_copy_text(ctx.box, ctx.full_raw_text)

    def _on_part_delta(self, ctx: _StreamCtx, event: PartDeltaEvent) -> None:
        delta = event.delta
        if isinstance(delta, TextPartDelta):
            self._close_thinking(ctx)
            ctx.text_acc += delta.content_delta
            ctx.full_raw_text += delta.content_delta
            lbl = self._ensure_text(ctx)
            lbl.set_text(ctx.text_acc)
            self._update_copy_text(ctx.box, ctx.full_raw_text)
        elif isinstance(delta, ThinkingPartDelta):
            self._close_text(ctx)
            ctx.think_acc += delta.content_delta
            ctx.full_raw_text += delta.content_delta
            self._ensure_thinking(ctx).set_text(ctx.think_acc)
            self._update_copy_text(ctx.box, ctx.full_raw_text)

    def _close_text(self, ctx: _StreamCtx) -> None:
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

    def _copy_to_clipboard(self, text: str) -> None:
        if not text:
            return
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        clipboard.set_text(text, -1)
        self.set_status("Copied message to clipboard.")

    def _update_copy_text(self, box: Gtk.Box, text: str) -> None:
        parent = box.get_parent()
        if parent and hasattr(parent, "_grc_copy_btn"):
            parent._grc_copy_btn._grc_copy_text = text

    def _append_user_message(self, text: str) -> None:
        lbl = Gtk.Label(label=text)
        lbl.set_line_wrap(True)
        lbl.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        lbl.set_xalign(1.0)
        lbl.set_halign(Gtk.Align.END)
        lbl.set_selectable(True)
        lbl.get_style_context().add_class("chat-user-label")
        lbl.set_margin_start(40)

        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        hbox.set_halign(Gtk.Align.END)

        copy_btn = Gtk.Button()
        copy_btn.set_relief(Gtk.ReliefStyle.NONE)
        copy_btn.set_focus_on_click(False)
        img = Gtk.Image.new_from_icon_name("edit-copy-symbolic", Gtk.IconSize.MENU)
        copy_btn.set_image(img)
        copy_btn.set_tooltip_text("Copy message")
        copy_btn.connect("clicked", lambda *_: self._copy_to_clipboard(text))

        hbox.pack_start(copy_btn, False, False, 0)
        hbox.pack_start(lbl, True, True, 0)
        self._add_message_row(hbox)

    def _start_agent_message(self) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)

        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        hbox.set_halign(Gtk.Align.START)
        hbox.pack_start(box, True, True, 0)

        copy_btn = Gtk.Button()
        copy_btn.set_relief(Gtk.ReliefStyle.NONE)
        copy_btn.set_focus_on_click(False)
        img = Gtk.Image.new_from_icon_name("edit-copy-symbolic", Gtk.IconSize.MENU)
        copy_btn.set_image(img)
        copy_btn.set_tooltip_text("Copy message")

        copy_btn._grc_copy_text = ""
        copy_btn.connect("clicked", lambda b: self._copy_to_clipboard(b._grc_copy_text))

        hbox.pack_start(copy_btn, False, False, 0)
        hbox._grc_copy_btn = copy_btn

        self._add_message_row(hbox)
        return box

    def _format_table(self, table_soup) -> str:
        headers = []
        thead = table_soup.find("thead")
        if thead:
            headers = [th.get_text().strip() for th in thead.find_all("th")]

        tbody = table_soup.find("tbody")
        rows = []
        if tbody:
            for tr in tbody.find_all("tr"):
                rows.append([td.get_text().strip() for td in tr.find_all(["td", "th"])])
        else:
            for tr in table_soup.find_all("tr"):
                rows.append([td.get_text().strip() for td in tr.find_all(["td", "th"])])

        if not headers and rows:
            headers = rows[0]
            rows = rows[1:]

        num_cols = max(len(headers), max((len(r) for r in rows), default=0))
        if num_cols == 0:
            return ""

        headers += [""] * (num_cols - len(headers))
        for r in rows:
            r += [""] * (num_cols - len(r))

        col_widths = [0] * num_cols
        for i in range(num_cols):
            col_widths[i] = max(
                len(headers[i]),
                max((len(r[i]) for r in rows), default=0)
            )

        lines = []
        header_line = " | ".join(f"{h:<{col_widths[i]}}" for i, h in enumerate(headers))
        lines.append("| " + header_line + " |")

        sep_line = "-+-".join("-" * col_widths[i] for i in range(num_cols))
        lines.append("+" + sep_line + "+")

        for r in rows:
            row_line = " | ".join(f"{val:<{col_widths[i]}}" for i, val in enumerate(r))
            lines.append("| " + row_line + " |")

        return "\n" + "\n".join(lines) + "\n"

    def _node_to_pango(self, node) -> str:  # noqa: C901
        if isinstance(node, NavigableString):
            return str(node).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        tag = node.name
        if not tag:
            return ""

        inner_text = "".join(self._node_to_pango(child) for child in node.children)

        if tag in ("p", "div"):
            return f"{inner_text}\n"
        elif tag in ("strong", "b"):
            return f"<b>{inner_text}</b>"
        elif tag in ("em", "i"):
            return f"<i>{inner_text}</i>"
        elif tag in ("code", "tt") or tag == "pre":
            return f'<span face="monospace">{inner_text.replace(" ", "\u00A0")}</span>'
        elif tag == "a":
            href = node.get("href", "")
            href_esc = href.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
            return f'<a href="{href_esc}">{inner_text}</a>'
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            return f"\n<b>{inner_text}</b>\n"
        elif tag == "ul" or tag == "ol":
            return f"{inner_text}\n"
        elif tag == "li":
            return f" • {inner_text}\n"
        elif tag == "table":
            table_str = self._format_table(node)
            table_str_esc = table_str.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            return f'<span face="monospace" size="small">{table_str_esc.replace(" ", "\u00A0")}</span>'
        elif tag in ("thead", "tbody", "tr", "td", "th"):
            return ""
        else:
            return inner_text

    def _render_markdown_to_box(self, box: Gtk.Box, text: str, clear: bool = True) -> None:
        if clear:
            for child in box.get_children():
                box.remove(child)

        try:
            md = MarkdownIt("commonmark").enable("table")
            html = md.render(text)
            soup = BeautifulSoup(html, "html.parser")

            for element in soup.contents:
                if not element.name:
                    t = str(element).strip()
                    if t:
                        lbl = self._make_text_label()
                        lbl.set_markup(t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
                        box.pack_start(lbl, False, False, 0)
                    continue

                tag = element.name
                if tag == "table":
                    table_str = self._format_table(element)
                    table_str_esc = table_str.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace(" ", "\u00A0")

                    lbl = Gtk.Label()
                    lbl.get_style_context().add_class("chat-monospace")
                    lbl.set_markup(f'<span face="monospace" size="small">{table_str_esc}</span>')
                    lbl.set_line_wrap(False)
                    lbl.set_xalign(0.0)
                    lbl.set_selectable(True)
                    lbl.set_margin_start(4)
                    lbl.set_margin_end(4)
                    lbl.set_margin_top(4)
                    lbl.set_margin_bottom(4)

                    sw = Gtk.ScrolledWindow()
                    sw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
                    sw.add(lbl)
                    sw.set_min_content_height(100)
                    sw.get_style_context().add_class("chat-agent-label")

                    box.pack_start(sw, False, False, 0)
                elif tag == "pre":
                    code_text = element.get_text()
                    code_text_esc = code_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace(" ", "\u00A0")

                    lbl = Gtk.Label()
                    lbl.get_style_context().add_class("chat-agent-label")
                    lbl.get_style_context().add_class("chat-monospace")
                    lbl.set_markup(f'<span face="monospace" size="small">{code_text_esc}</span>')
                    lbl.set_line_wrap(True)
                    lbl.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
                    lbl.set_xalign(0.0)
                    lbl.set_selectable(True)

                    box.pack_start(lbl, False, False, 0)
                else:
                    block_markup = self._node_to_pango(element)
                    if block_markup.strip():
                        lbl = self._make_text_label()
                        lbl.set_markup(block_markup)
                        box.pack_start(lbl, False, False, 0)

            box.show_all()
        except Exception as e:
            _log.warning("Failed to render markdown to box: %s", e)
            lbl = self._make_text_label()
            lbl.set_text(text)
            box.pack_start(lbl, False, False, 0)
            box.show_all()

    def _render_last_message_rich(self, box: Gtk.Box, msg: ModelMessage) -> None:  # noqa: C901
        for child in box.get_children():
            box.remove(child)

        full_text = ""
        for part in msg.parts:
            part_cls = part.__class__.__name__
            if part_cls == "TextPart":
                self._render_markdown_to_box(box, part.content, clear=False)
                full_text += part.content
            elif part_cls == "ThinkingPart":
                exp = Gtk.Expander(label="Thinking...")
                exp.set_expanded(False)
                exp.get_style_context().add_class("chat-thinking-expander")
                body = Gtk.Label(label=part.content)
                body.set_line_wrap(True)
                body.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
                body.set_xalign(0.0)
                body.set_selectable(True)
                exp.add(body)
                box.pack_start(exp, False, False, 0)
                exp.show_all()
                full_text += f"<Thinking>\n{part.content}\n</Thinking>\n"
            elif part_cls == "ToolCallPart":
                tool_name = part.tool_name or "?"
                exp = self._make_tool_expander(tool_name)
                args_str = str(part.args) if part.args else ""
                self._set_tool_body(exp, args_str)

                tcid = part.tool_call_id
                ret_content, is_success = "", True
                if tcid:
                    for m in self._message_history:
                        if m.__class__.__name__ == "ModelRequest":
                            for p in m.parts:
                                if p.__class__.__name__ == "ToolReturnPart" and p.tool_call_id == tcid:
                                    ret_content = str(p.content)
                                    is_success = (p.outcome != "failed")
                                    break

                if ret_content:
                    self._set_tool_body(exp, ret_content)
                    if is_success:
                        exp.set_label(f"\u2699 {tool_name} \u2713")
                    else:
                        exp.set_label(f"\u2699 {tool_name} \u2717")
                    full_text += f"<Tool Call: {tool_name}>\nArgs: {args_str}\nResult: {ret_content}\n"
                else:
                    exp.set_label(f"\u2699 {tool_name} ✓")
                    full_text += f"<Tool Call: {tool_name}>\nArgs: {args_str}\n"

                box.pack_start(exp, False, False, 0)
                exp.show_all()

        parent = box.get_parent()
        if parent and hasattr(parent, "_grc_copy_btn"):
            parent._grc_copy_btn._grc_copy_text = full_text

    def _clear_welcome_screen(self) -> None:
        has_welcome = False
        for c in self._listbox.get_children():
            inner = c.get_child() if isinstance(c, Gtk.ListBoxRow) else c
            if inner:
                ctx = inner.get_style_context()
                if ctx.has_class("chat-welcome-box") or ctx.has_class("chat-recent-header") or ctx.has_class("chat-recent-item"):
                    has_welcome = True
                    break
        if has_welcome:
            for child in self._listbox.get_children():
                self._listbox.remove(child)

    def _add_message_row(self, child: Gtk.Widget) -> None:
        self._clear_welcome_screen()
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
        rich_rendered = False
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
                self._save_history()
                if self._message_history:
                    self._render_last_message_rich(ctx.box, self._message_history[-1])
                    rich_rendered = True
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
            if not rich_rendered and ctx.full_raw_text:
                self._render_markdown_to_box(ctx.box, ctx.full_raw_text)
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
