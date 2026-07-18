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
import time
from pathlib import Path
from typing import Any

import gi
from bs4 import BeautifulSoup, NavigableString
from markdown_it import MarkdownIt

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")
from datetime import UTC

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
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    NativeToolCallPart,
    NativeToolReturnPart,
    RetryPromptPart,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_graph import End

from .db import (
    delete_all_sessions,
    delete_session,
    deserialize_messages,
    get_recent_sessions,
    get_session_for_path,
    load_session,
    save_session,
)
from .settings import (
    get_env_value,
    load_settings,
    save_settings,
    upsert_env_key,
)

_log = logging.getLogger(__name__)

# When auto-scrolling incrementally (streaming / appended rows), only stick to
# the bottom if the user is already within this many pixels of it — so a user
# scrolled up to read earlier messages isn't yanked back down on every token.
_SCROLL_STICK_THRESHOLD = 80

# Minimum interval between streamed-text UI flushes (seconds). Without this,
# every token called Gtk.Label.set_text(accumulated_text), re-running Pango's
# line-wrap layout over the ENTIRE growing message each token = O(n^2) and a
# frozen UI on long responses. Flushing at ~30fps keeps streaming smooth while
# the final markdown render (at part/stream close) shows the polished result.
_STREAM_FLUSH_INTERVAL = 0.033


def _esc(text: str) -> str:
    """Escape text for safe interpolation into Pango markup."""
    return GLib.markup_escape_text(text, -1)


def format_relative_time(timestamp_str: str) -> str:
    from datetime import datetime
    try:
        if "T" in timestamp_str:
            dt = datetime.fromisoformat(timestamp_str)
        else:
            dt = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
        now = datetime.now(UTC)
        diff = now - dt
        seconds = diff.total_seconds()
        if seconds < 60:
            return "just now"
        minutes = seconds // 60
        if minutes < 60:
            return f"{int(minutes)}m ago"
        hours = minutes // 60
        if hours < 24:
            return f"{int(hours)}h ago"
        days = hours // 24
        if days < 7:
            return f"{int(days)}d ago"
        return dt.strftime("%b %d, %Y")
    except Exception:
        return timestamp_str


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
    color: #212121;
}
.chat-agent-msg-box {
    background: #f5f5f5;
    border: 1px solid #e0e0e0;
    border-radius: 10px;
    padding: 10px 12px;
}
.chat-code-block {
    background: #ffffff;
    border: 1px solid #e0e0e0;
    border-radius: 6px;
}
.chat-error-label {
    background: #ffebee;
    color: #c62828;
    border: 1px solid #ffcdd2;
    border-radius: 8px;
    padding: 8px 10px;
}
.chat-aborted-label {
    background: #f5f5f5;
    color: #616161;
    border: 1px solid #e0e0e0;
    border-radius: 8px;
    padding: 8px 10px;
}
.chat-confirm-box {
    background: #fff8e1;
    border: 1px solid #ffe082;
    border-radius: 8px;
    padding: 10px 12px;
}
.chat-tool-expander {
    background: #ffffff;
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
.chat-recent-row {
    margin-bottom: 6px;
    margin-left: 12px;
    margin-right: 12px;
}
.chat-recent-item {
    background: #ffffff;
    color: #212121;
    border: 1px solid #e0e0e0;
    border-radius: 6px 0px 0px 6px;
    padding: 8px 12px;
}
.chat-recent-item:hover {
    background: #e3f2fd;
    border-color: #90caf9;
    color: #0d47a1;
}
.chat-recent-delete-btn {
    background: #ffffff;
    color: #c62828;
    border: 1px solid #e0e0e0;
    border-left: none;
    border-radius: 0px 6px 6px 0px;
    padding: 8px;
}
.chat-recent-delete-btn:hover {
    background: #ffebee;
    border-color: #ffcdd2;
    color: #b71c1c;
}
"""

_PROVIDER_LABELS = {
    "ollama": "Ollama (local)",
    "openrouter": "OpenRouter (cloud)",
    "ollama_cloud": "Ollama Cloud (cloud)",
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
# Example text for the Settings dialog's placeholders — mirrors the real
# per-provider defaults in settings.py's _DEFAULT_MODELS.
_PROVIDER_MODEL_PLACEHOLDER = {
    "ollama": "qwen3.6:35b-a3b-q4_K_M",
    "openrouter": "deepseek/deepseek-v4-flash",
    "ollama_cloud": "deepseek-v4-flash:cloud",
}
_PROVIDER_KEY_PLACEHOLDER = {
    "openrouter": "sk-or-v1-...",
    "ollama_cloud": "Paste your API key",
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


class _StreamCtx:
    """Per-call mutable streaming state — held outside ``send_message``
    so the node/event handler helpers can stay small and flat."""

    __slots__ = (
        "box",
        "text_lbl",
        "text_acc",
        "text_dirty",
        "think_body",
        "think_acc",
        "think_dirty",
        "tools",
        "full_raw_text",
        "last_flush",
    )

    def __init__(self, box: Gtk.Box) -> None:
        self.box = box
        self.text_lbl: Gtk.Label | None = None
        self.text_acc = ""
        self.text_dirty = False
        self.think_body: Gtk.Label | None = None
        self.think_acc = ""
        self.think_dirty = False
        self.tools: dict[str, Gtk.Expander] = {}
        self.full_raw_text = ""
        self.last_flush = 0.0


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
        self._active_session_id: int | None = None
        self._loading_session_id: int | None = None
        self._busy = False
        # Bumped on every global Clear History. _save_history captures it before
        # dispatching its (uncancellable) worker-thread save; if a clear lands
        # while that save is in flight, the saved row is removed so a cleared
        # session can't resurrect.
        self._clear_generation: int = 0
        self._chat_task: asyncio.Task | None = None
        # True while an inline Yes/No confirm bubble (prompt_fix_error) is
        # awaiting a response — blocks _refresh_welcome_times from wiping the
        # listbox (and the pending bubble with it) out from under the user.
        self._pending_confirm = False
        # Per-domain last-seen RAG build status, so the poller only writes the
        # status bar on transitions (and while building) — never when idle.
        # Catalog and docs build independently and can run concurrently.
        self._last_index_state: dict[str, str] = {}
        self._last_index_msg: str | None = None
        # Holds the currently-open non-blocking modal dialog so the gbulb loop
        # keeps pumping while it's shown. A non-blocking toplevel shown via
        # .show() would be garbage-collected once the constructing method
        # returns (PyGObject holds no Python-side root ref), so we anchor it
        # here and clear it in the response handler.
        self._open_dialog: Gtk.Dialog | None = None

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

        # Refresh relative timestamps ("2m ago") on the recent-sessions list
        # while the welcome screen is visible. Re-renders only when idle and
        # empty so live-streaming bubbles are never wiped.
        GLib.timeout_add_seconds(60, self._refresh_welcome_times)

        # Poll the RAG index-build status (set by the worker thread that runs
        # ingest) and surface progress in the status bar. Cheap dict reads; the
        # build itself runs off the main loop via asyncio.to_thread.
        GLib.timeout_add(500, self._poll_indexing)

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

        self._new_session_btn = _signal_btn("New Session", "Start a new chat session", "new-session-clicked")

        # Clear History button
        self._clear_hist_btn = Gtk.Button.new_with_label("Clear History")
        self._clear_hist_btn.set_tooltip_text("Delete ALL saved chat sessions")
        self._clear_hist_btn.get_style_context().add_class("chat-toolbar-btn")
        self._clear_hist_btn.connect("clicked", self._on_clear_history_clicked)
        bar.pack_start(self._clear_hist_btn, False, False, 0)

        # Active graph badge
        self._graph_label = Gtk.Label(label="Active Graph: none")
        self._graph_label.get_style_context().add_class("graph-badge")
        bar.pack_start(self._graph_label, False, False, 8)

        # Spacer
        spacer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        bar.pack_start(spacer, True, True, 0)

        # Settings
        self._gear_btn = Gtk.Button.new_with_label("Settings")
        self._gear_btn.set_tooltip_text("Provider / model settings")
        self._gear_btn.get_style_context().add_class("chat-toolbar-btn")
        self._gear_btn.connect("clicked", lambda *_: self._open_settings())
        bar.pack_start(self._gear_btn, False, False, 0)

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
        self._entry.connect("changed", lambda *_: self._update_send_sensitivity())
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
        self._graph_label.set_text(f"Active Graph: {name}" if name else "Active Graph: none")

    def _domain_label(self, domain: str | None) -> str:
        if domain == "catalog":
            return "block library"
        if domain == "docs":
            return "documentation"
        return "index"

    def _poll_indexing(self) -> bool:
        """Surface RAG index-build progress in the status bar.

        Builds run on worker threads (dispatched via ``asyncio.to_thread`` from
        the agent tools) and mutate the per-domain ``_rag_building`` entries in
        place. This polls from the main loop so no cross-thread widget calls are
        needed (CPython per-key dict reads/writes are atomic). Catalog and docs
        builds can run concurrently (pydantic-ai runs tools in parallel), so
        status is tracked per-domain. Only writes the status bar while a build
        is in progress or on a transition — never when idle — so it can't
        clobber other messages.
        """
        from .adapter import _rag_building

        # Snapshot the keys: the worker thread may add a domain entry
        # concurrently, and iterating a dict view during mutation raises.
        building_msg: str | None = None
        for domain in list(_rag_building):
            entry = _rag_building.get(domain)
            if not entry:
                continue
            status = entry.get("status")
            last = self._last_index_state.get(domain)
            label = self._domain_label(domain)
            if status == "building":
                self._last_index_state[domain] = "building"
                # Show progress for the first building domain found; a second
                # concurrent build is rare and its transition is still notified.
                if building_msg is None:
                    current = entry.get("current", 0)
                    total = entry.get("total", 0)
                    if total:
                        building_msg = f"Indexing {label} for search\u2026 {current}/{total}"
                    else:
                        building_msg = f"Indexing {label} for search\u2026"
            elif status in ("ready", "failed") and last != status:
                # Terminal transition for this domain — notify exactly once.
                self._last_index_state[domain] = status
                self._last_index_msg = None
                if status == "ready":
                    # `indexed` is the actually-embedded count (may be < total).
                    n = entry.get("indexed", entry.get("total", 0))
                    self.set_status(
                        f"{label.capitalize()} indexed \u2014 {n} entries ready for search."
                    )
                else:
                    self.set_status(
                        f"{label.capitalize()} indexing failed; search may return no or stale results.",
                        error=True,
                    )
                return True  # re-arm
        if building_msg is not None and building_msg != self._last_index_msg:
            self._last_index_msg = building_msg
            self.set_status(building_msg)
        return True  # re-arm

    def _on_clear_history_clicked(self, _widget: Gtk.Button) -> None:
        _log.info("Clear History: button clicked")
        dialog = Gtk.MessageDialog(
            transient_for=self.get_toplevel() if isinstance(self.get_toplevel(), Gtk.Window) else None,
            flags=Gtk.DialogFlags.MODAL,
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

    def _on_delete_recent_session(self, session_id: int) -> None:
        try:
            delete_session(session_id)
            if self._active_session_id == session_id:
                self._active_session_id = None
                self._message_history = []
                page = self.current_page
                if page:
                    page._grc_agent_session_id = None
        except Exception as e:
            _log.error("Failed to delete session %s: %s", session_id, e)
        self._render_history()

    def set_input_enabled(self, enabled: bool) -> None:
        if not self._busy:
            self._entry.set_sensitive(enabled)
            self._update_send_sensitivity()
        if enabled:
            self._entry.set_placeholder_text("Ask about your flowgraph...")

    def _update_send_sensitivity(self) -> None:
        # Gate Send on non-blank input too, on top of the entry's own
        # busy/flowgraph-present sensitivity — otherwise a click on
        # whitespace-only text is a silent no-op (see _dispatch_send).
        self._send_btn.set_sensitive(self._entry.get_sensitive() and bool(self._entry.get_text().strip()))

    def set_blocks_expanded(self, expanded: bool) -> None:
        self._blocks_expanded = expanded
        icon = "pan-start-symbolic" if expanded else "pan-end-symbolic"
        self._blocks_arrow.set_from_icon_name(icon, Gtk.IconSize.SMALL_TOOLBAR)
        self._blocks_toggle.set_tooltip_text("Hide block library" if expanded else "Show block library")

    def set_agent(self, agent: Agent) -> None:
        self._agent = agent

    def set_flowgraph_proxy(self, proxy: object) -> None:
        self._flowgraph_proxy = proxy
        cm = getattr(proxy, "_canvas_manager", None)
        path = cm.path if cm else None
        self.sync_to_file(path)

    @property
    def current_page(self) -> Any:
        if self._flowgraph_proxy is None:
            return None
        cm = getattr(self._flowgraph_proxy, "_canvas_manager", None)
        return cm.current_page if cm else None

    def sync_to_file(self, path: str | None) -> None:
        page = self.current_page
        if self._loading_session_id is not None:
            if page:
                page._grc_agent_session_id = self._loading_session_id
            return

        if page:
            self._sync_page_session(page, path)
        else:
            self._sync_headless_session(path)

    def _sync_page_session(self, page: Any, path: str | None) -> None:
        session_id = getattr(page, "_grc_agent_session_id", None)
        if session_id is not None and not isinstance(session_id, int):
            session_id = None
            if hasattr(page, "_grc_agent_session_id"):
                import contextlib
                with contextlib.suppress(AttributeError):
                    delattr(page, "_grc_agent_session_id")

        if not hasattr(page, "_grc_agent_session_id"):
            # Page opened for the first time — look up most recently updated session for its path
            if path:
                session = get_session_for_path(path)
                page._grc_agent_session_id = session["id"] if session else None
            else:
                page._grc_agent_session_id = None

        session_id = page._grc_agent_session_id
        if session_id is not None:
            session = load_session(session_id)
            if session:
                self._active_session_id = session["id"]
                self._message_history = deserialize_messages(session["messages"])
                self._render_history()
                return
            else:
                # Session was deleted/not found, reset page association
                page._grc_agent_session_id = None

        self._active_session_id = None
        self._message_history = []
        self._render_history()

    def _sync_headless_session(self, path: str | None) -> None:
        if path:
            session = get_session_for_path(path)
            if session:
                self._active_session_id = session["id"]
                self._message_history = deserialize_messages(session["messages"])
                self._render_history()
                return

        self._active_session_id = None
        self._message_history = []
        self._render_history()

    def clear_messages(self) -> None:
        # Bump the generation first so any in-flight _save_history worker
        # (uncancellable) will undo its own INSERT instead of resurrecting a
        # session the user just cleared (see _save_history), and so any
        # in-flight _run_agent_turn's CancelledError handler recognizes this
        # clear and skips re-populating the listbox it just wiped.
        self._clear_generation += 1
        if self._chat_task and not self._chat_task.done():
            self._chat_task.cancel()
        self._message_history = []
        self._active_session_id = None
        page = self.current_page
        if page:
            page._grc_agent_session_id = None
        self._render_history()

    def _refresh_welcome_times(self) -> bool:
        """Periodically re-render the welcome/recent-sessions list so the
        relative timestamps ("2m ago") stay fresh. Only runs when idle and the
        history is empty (the only state in which the list is visible); never
        disturbs a live chat stream."""
        if not self._busy and not self._message_history and not self._pending_confirm:
            self._render_history()
        return True  # re-arm

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
        self._scroll_to_bottom(force=True)

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

        page = self.current_page

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
        try:
            sessions = get_recent_sessions()
        except Exception as e:
            # A corrupt/unwritable chat_sessions.db must not abort the UI —
            # degrade to an empty recent list rather than crashing launch.
            _log.error("Failed to load recent sessions: %s", e)
            sessions = []

        # Filter out the current active session from the suggestions
        if self._active_session_id is not None:
            sessions = [s for s in sessions if s["id"] != self._active_session_id]

        if sessions:
            self._add_recent_sessions_to_list(sessions)

    def _add_recent_sessions_to_list(self, sessions: list[dict[str, Any]]) -> None:
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
            sid = s["id"]
            grc_path = s["grc_file_path"]
            last_message = s.get("last_message", "")
            updated_at = s.get("updated_at", "")

            row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
            row_box.get_style_context().add_class("chat-recent-row")

            btn = Gtk.Button()
            btn.get_style_context().add_class("chat-recent-item")
            btn.set_relief(Gtk.ReliefStyle.NONE)
            btn.set_hexpand(True)

            # Content layout
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            item_icon = Gtk.Image.new_from_icon_name("text-x-generic-symbolic", Gtk.IconSize.MENU)

            text_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)

            top_hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            name_lbl = Gtk.Label()
            name_lbl.set_markup(f"<b>{_esc(Path(grc_path).name)}</b>")
            name_lbl.set_xalign(0.0)
            top_hbox.pack_start(name_lbl, False, False, 0)

            if updated_at:
                time_str = format_relative_time(updated_at)
                time_lbl = Gtk.Label()
                time_lbl.set_markup(f"<span fgcolor='#888888' size='small'>{_esc(time_str)}</span>")
                time_lbl.set_xalign(1.0)
                top_hbox.pack_end(time_lbl, False, False, 0)

            path_lbl = Gtk.Label()
            path_lbl.set_markup(f"<span fgcolor='#777777' size='small'>{_esc(str(Path(grc_path).parent))}</span>")
            path_lbl.set_xalign(0.0)
            path_lbl.set_ellipsize(Pango.EllipsizeMode.START)

            text_vbox.pack_start(top_hbox, False, False, 0)
            text_vbox.pack_start(path_lbl, False, False, 0)

            if last_message:
                snippet = last_message.replace("\n", " ").strip()
                snippet_lbl = Gtk.Label()
                snippet_lbl.set_markup(f"<span fgcolor='#555555' style='italic' size='small'>{_esc(snippet)}</span>")
                snippet_lbl.set_xalign(0.0)
                snippet_lbl.set_ellipsize(Pango.EllipsizeMode.END)
                text_vbox.pack_start(snippet_lbl, False, False, 0)

            box.pack_start(item_icon, False, False, 0)
            box.pack_start(text_vbox, True, True, 0)

            btn.add(box)
            btn.set_tooltip_text(grc_path)

            # Connect click handler
            btn.connect("clicked", lambda _, session_id=sid: self._on_recent_session_clicked(session_id))

            # Individual delete button next to each previous session
            del_btn = Gtk.Button()
            del_btn.get_style_context().add_class("chat-recent-delete-btn")
            del_btn.set_relief(Gtk.ReliefStyle.NONE)
            del_icon = Gtk.Image.new_from_icon_name("user-trash-symbolic", Gtk.IconSize.MENU)
            del_btn.set_image(del_icon)
            del_btn.set_tooltip_text("Delete this session permanently")
            del_btn.connect("clicked", lambda _, session_id=sid: self._on_delete_recent_session(session_id))

            row_box.pack_start(btn, True, True, 0)
            row_box.pack_start(del_btn, False, False, 0)

            self._listbox.add(row_box)

    def _on_recent_session_clicked(self, session_id: int) -> None:
        if self._busy:
            self.set_status("Stop or wait for the current response before switching sessions.", error=True)
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
        self._message_history = deserialize_messages(session_data["messages"])
        self._render_history()

        self._loading_session_id = session_id
        try:
            self._switch_or_open_file(path)
        finally:
            self._loading_session_id = None

    def _switch_or_open_file(self, path: str) -> None:
        cm = getattr(self._flowgraph_proxy, "_canvas_manager", None) if self._flowgraph_proxy else None
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
                    pass

        if not switched:
            try:
                cm.window.new_page(path)
                self.set_status("Opened session file.")
            except Exception as e:
                _log.error("Failed to open recent session file %s: %s", path, e)
                self.set_status(f"Failed to open session: {e}", error=True)

    async def _save_history(self) -> None:
        if self._active_session_id is None:
            return
        if self._flowgraph_proxy is None:
            return
        cm = getattr(self._flowgraph_proxy, "_canvas_manager", None)
        path = cm.path if cm else None
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
                delete_session(new_id)
            except Exception:
                _log.exception("Failed to remove session resurrected by in-flight save")

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
        # Force a final flush so the last throttled chunk is painted before the
        # node hands control back (and before any markdown re-render).
        self._flush_streaming(ctx, force=True)

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
                        if isinstance(event.part, RetryPromptPart):
                            res_str = event.part.model_response()
                            name = getattr(exp, "_grc_tool_name", "?")
                            self._set_tool_body(exp, res_str)
                            exp.set_label(f"⚠ {name} retry")
                        else:
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
            self._ensure_text(ctx)
            ctx.text_dirty = True
            self._update_copy_text(ctx.box, ctx.full_raw_text)
            self._flush_streaming(ctx, force=True)
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
        elif isinstance(part, NativeToolCallPart):
            # Native tool calls (e.g. provider-native web_search/web_fetch) never
            # fire FunctionToolCallEvent/FunctionToolResultEvent — call and return
            # arrive purely as ordinary response parts, each in its own
            # PartStartEvent (no delta class exists for either).
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
        elif isinstance(part, NativeToolReturnPart):
            tcid = part.tool_call_id or ""
            exp = ctx.tools.get(tcid)
            if exp is not None:
                res_str = str(part.content)
                self._set_tool_result(exp, res_str)
                ctx.full_raw_text += f"<Tool Result: {res_str}>\n"
                self._update_copy_text(ctx.box, ctx.full_raw_text)
        elif isinstance(part, ThinkingPart):
            self._close_text(ctx)
            self._ensure_thinking(ctx)
            ctx.think_acc = part.content or ""
            ctx.full_raw_text += part.content or ""
            ctx.think_dirty = True
            self._update_copy_text(ctx.box, ctx.full_raw_text)
            self._flush_streaming(ctx, force=True)

    def _on_part_delta(self, ctx: _StreamCtx, event: PartDeltaEvent) -> None:
        delta = event.delta
        if isinstance(delta, TextPartDelta):
            self._close_thinking(ctx)
            ctx.text_acc += delta.content_delta
            ctx.full_raw_text += delta.content_delta
            self._ensure_text(ctx)
            ctx.text_dirty = True
            self._update_copy_text(ctx.box, ctx.full_raw_text)
            self._flush_streaming(ctx)
        elif isinstance(delta, ThinkingPartDelta):
            self._close_text(ctx)
            ctx.think_acc += delta.content_delta
            ctx.full_raw_text += delta.content_delta
            self._ensure_thinking(ctx)
            ctx.think_dirty = True
            self._update_copy_text(ctx.box, ctx.full_raw_text)
            self._flush_streaming(ctx)

    def _flush_streaming(self, ctx: _StreamCtx, *, force: bool = False) -> None:
        """Push accumulated streamed text/thinking to their labels at most once
        per ``_STREAM_FLUSH_INTERVAL``. Each ``Gtk.Label.set_text`` re-runs
        Pango's line-wrap layout over the full (growing) text, so calling it
        per token is O(n^2); throttling to ~30fps keeps the UI responsive and
        lets the idle autoscroll handler run between flushes. A forced flush
        (part start/close, stream end) bypasses the interval so transitions
        never show stale text."""
        now = time.monotonic()
        if not force and (now - ctx.last_flush) < _STREAM_FLUSH_INTERVAL:
            return
        flushed = False
        if ctx.text_dirty and ctx.text_lbl is not None:
            ctx.text_lbl.set_text(ctx.text_acc)
            ctx.text_dirty = False
            flushed = True
        if ctx.think_dirty and ctx.think_body is not None:
            ctx.think_body.set_text(ctx.think_acc)
            ctx.think_dirty = False
            flushed = True
        if flushed:
            ctx.last_flush = now
            self._scroll_to_bottom()

    def _close_text(self, ctx: _StreamCtx) -> None:
        self._flush_streaming(ctx, force=True)
        ctx.text_lbl = None
        ctx.text_acc = ""
        ctx.text_dirty = False

    def _close_thinking(self, ctx: _StreamCtx) -> None:
        self._flush_streaming(ctx, force=True)
        ctx.think_body = None
        ctx.think_acc = ""
        ctx.think_dirty = False

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
        copy_btn.set_valign(Gtk.Align.START)
        img = Gtk.Image.new_from_icon_name("edit-copy-symbolic", Gtk.IconSize.MENU)
        copy_btn.set_image(img)
        copy_btn.set_tooltip_text("Copy message")
        copy_btn.connect("clicked", lambda *_: self._copy_to_clipboard(text))

        hbox.pack_start(copy_btn, False, False, 0)
        hbox.pack_start(lbl, True, True, 0)
        self._add_message_row(hbox)

    def _start_agent_message(self) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.get_style_context().add_class("chat-agent-msg-box")

        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        hbox.set_halign(Gtk.Align.START)
        hbox.pack_start(box, True, True, 0)

        copy_btn = Gtk.Button()
        copy_btn.set_relief(Gtk.ReliefStyle.NONE)
        copy_btn.set_focus_on_click(False)
        copy_btn.set_valign(Gtk.Align.START)
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

    def _node_to_pango(self, node, depth: int = 0) -> str:  # noqa: C901
        if isinstance(node, NavigableString):
            return _esc(str(node))

        tag = node.name
        if not tag:
            return ""

        if tag in ("ul", "ol"):
            li_children = [c for c in node.children if getattr(c, "name", None) == "li"]
            lines = [
                f"{'  ' * depth}{f'{i}.' if tag == 'ol' else '•'}  "
                + "".join(self._node_to_pango(child, depth + 1) for child in li.children).strip()
                for i, li in enumerate(li_children, start=1)
            ]
            return "\n".join(lines) + "\n"

        inner_text = "".join(self._node_to_pango(child, depth) for child in node.children)

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
            href_esc = _esc(href)
            return f'<a href="{href_esc}">{inner_text}</a>'
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            return f'<span size="larger" weight="bold">{inner_text}</span>\n'
        elif tag == "li":
            # Defensive fallback for a stray orphaned <li> outside any ul/ol —
            # the normal case is handled above by the ul/ol branch itself.
            return f"  •  {inner_text}\n"
        elif tag == "table":
            table_str = self._format_table(node)
            table_str_esc = _esc(table_str)
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
                        lbl.set_markup(_esc(t))
                        box.pack_start(lbl, False, False, 0)
                    continue

                tag = element.name
                if tag == "table":
                    table_str = self._format_table(element)
                    table_str_esc = _esc(table_str).replace(" ", "\u00A0")

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
                    sw.get_style_context().add_class("chat-code-block")

                    box.pack_start(sw, False, False, 0)
                elif tag == "pre":
                    code_text = element.get_text()
                    code_text_esc = _esc(code_text).replace(" ", "\u00A0")

                    lbl = Gtk.Label()
                    lbl.get_style_context().add_class("chat-code-block")
                    lbl.get_style_context().add_class("chat-monospace")
                    lbl.set_markup(f'<span face="monospace" size="small">{code_text_esc}</span>')
                    lbl.set_line_wrap(True)
                    lbl.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
                    lbl.set_xalign(0.0)
                    lbl.set_selectable(True)
                    lbl.set_margin_start(4)
                    lbl.set_margin_end(4)
                    lbl.set_margin_top(4)
                    lbl.set_margin_bottom(4)

                    box.pack_start(lbl, False, False, 0)
                else:
                    block_markup = self._node_to_pango(element).strip()
                    if block_markup:
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
        # Native tool call+return live as sibling parts within this same
        # ModelResponse (unlike function tools, whose return is a separate
        # ToolReturnPart in a later ModelRequest) — pre-scan the returns so
        # the call part can be resolved in a single forward pass.
        native_returns = {
            p.tool_call_id: p for p in msg.parts if isinstance(p, NativeToolReturnPart)
        }
        for part in msg.parts:
            part_cls = part.__class__.__name__
            if isinstance(part, NativeToolReturnPart):
                continue
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
                ret_content, is_success, is_retry = "", True, False
                if tcid:
                    for m in self._message_history:
                        if m.__class__.__name__ == "ModelRequest":
                            for p in m.parts:
                                if p.__class__.__name__ == "ToolReturnPart" and p.tool_call_id == tcid:
                                    ret_content = str(p.content)
                                    is_success = (p.outcome != "failed")
                                    break
                                if isinstance(p, RetryPromptPart) and p.tool_call_id == tcid:
                                    ret_content = p.model_response()
                                    is_retry = True
                                    break

                if ret_content:
                    self._set_tool_body(exp, ret_content)
                    if is_retry:
                        exp.set_label(f"⚠ {tool_name} retry")
                    elif is_success:
                        exp.set_label(f"\u2699 {tool_name} \u2713")
                    else:
                        exp.set_label(f"\u2699 {tool_name} \u2717")
                    full_text += f"<Tool Call: {tool_name}>\nArgs: {args_str}\nResult: {ret_content}\n"
                else:
                    exp.set_label(f"\u2699 {tool_name} ✓")
                    full_text += f"<Tool Call: {tool_name}>\nArgs: {args_str}\n"

                box.pack_start(exp, False, False, 0)
                exp.show_all()
            elif isinstance(part, NativeToolCallPart):
                tool_name = part.tool_name or "?"
                exp = self._make_tool_expander(tool_name)
                args_str = str(part.args) if part.args else ""
                self._set_tool_body(exp, args_str)

                ret_part = native_returns.get(part.tool_call_id)
                if ret_part is not None:
                    ret_content = str(ret_part.content)
                    is_success = (ret_part.outcome != "failed")
                    self._set_tool_body(exp, ret_content)
                    exp.set_label(f"⚙ {tool_name} {'✓' if is_success else '✗'}")
                    full_text += f"<Tool Call: {tool_name}>\nArgs: {args_str}\nResult: {ret_content}\n"
                else:
                    exp.set_label(f"⚙ {tool_name} ✓")
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

    def prompt_fix_error(self, log_text: str) -> None:
        """Show an inline Yes/No bubble asking whether to auto-fix a failed
        flowgraph run, offering to resend the captured console log as a
        prompt for the agent to diagnose."""
        log_text = log_text.strip()

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.get_style_context().add_class("chat-confirm-box")

        question = Gtk.Label(
            label="The flowgraph run failed. Want me to look at the log and try to fix it?"
        )
        question.set_line_wrap(True)
        question.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        question.set_xalign(0.0)
        box.pack_start(question, False, False, 0)

        expander = Gtk.Expander(label="Show error log")
        expander.set_expanded(False)
        log_lbl = Gtk.Label()
        log_lbl.set_markup(f'<span face="monospace" size="small">{_esc(log_text)}</span>')
        log_lbl.set_line_wrap(True)
        log_lbl.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        log_lbl.set_xalign(0.0)
        log_lbl.set_selectable(True)
        log_lbl.get_style_context().add_class("chat-code-block")
        expander.add(log_lbl)
        box.pack_start(expander, False, False, 0)

        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        yes_btn = Gtk.Button.new_with_label("Yes, fix it")
        no_btn = Gtk.Button.new_with_label("No thanks")
        btn_row.pack_start(yes_btn, False, False, 0)
        btn_row.pack_start(no_btn, False, False, 0)
        box.pack_start(btn_row, False, False, 0)

        def _on_yes(_btn: Gtk.Button) -> None:
            self._pending_confirm = False
            yes_btn.set_sensitive(False)
            no_btn.set_sensitive(False)
            question.set_text("Sending the error log for a fix...")
            prompt = (
                "The flowgraph execution failed. Here is the console log:\n\n"
                f"```\n{log_text}\n```\n\n"
                "Please diagnose the error and fix the flowgraph."
            )
            asyncio.ensure_future(self._send_fix_when_free(prompt))

        def _on_no(_btn: Gtk.Button) -> None:
            self._pending_confirm = False
            yes_btn.set_sensitive(False)
            no_btn.set_sensitive(False)
            question.set_text("Okay, dismissed.")

        yes_btn.connect("clicked", _on_yes)
        no_btn.connect("clicked", _on_no)

        self._pending_confirm = True
        self._add_message_row(box)

    async def _send_fix_when_free(self, text: str) -> None:
        """Wait out any in-flight agent turn, then send `text` as the next
        user message in the current session."""
        if self._chat_task and not self._chat_task.done():
            await asyncio.gather(self._chat_task, return_exceptions=True)
        self.send_message(text)

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
        self.send_message(text)

    def send_message(self, text: str) -> bool:
        """Send `text` as a user turn in the current session, as if it had
        been typed into the entry and submitted. Returns False (no-op) if
        `text` is blank or a turn is already in flight."""
        if not text.strip() or self._busy:
            return False
        self._append_user_message(text)

        if self._active_session_id is None:
            path = None
            if self._flowgraph_proxy is not None:
                cm = getattr(self._flowgraph_proxy, "_canvas_manager", None)
                path = cm.path if cm else None
            if path:
                try:
                    self._active_session_id = save_session(None, path, self._message_history)
                    page = self.current_page
                    if page:
                        page._grc_agent_session_id = self._active_session_id
                except Exception as e:
                    _log.error("Failed to create new session in database: %s", e)

        self._set_busy(True)
        self._chat_task = asyncio.ensure_future(self._run_agent_turn(text))
        self._chat_task.add_done_callback(self._on_chat_task_done)
        return True

    def _remember_user_message(self, text: str) -> None:
        """Record the user's just-sent prompt into the canonical history on a
        failed turn, so it is persisted and survives the next render instead of
        being wiped along with the error bubble."""
        self._message_history = self._message_history + [
            ModelRequest(parts=[UserPromptPart(content=text)])
        ]

    async def _run_agent_turn(self, text: str) -> None:  # noqa: C901
        rich_rendered = False
        origin_page = self.current_page
        origin_gen = self._clear_generation
        ctx: _StreamCtx | None = None
        try:
            if self._agent is None:
                self._append_error("No agent configured.")
                return
            ctx = _StreamCtx(self._start_agent_message())
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
                await self._save_history()
                self._render_history()
                rich_rendered = True
        except asyncio.CancelledError:
            # A tab switch mid-stream cancels this task then synchronously
            # swaps _message_history/_active_session_id to the new page, and a
            # Clear History/New Session bumps _clear_generation and wipes the
            # listbox synchronously too — only touch shared state if neither
            # happened. The eager save in send_message only creates the session
            # row; it does not contain this turn's prompt, so persist it now
            # via a fire-and-forget save (not awaited — this task is already
            # cancelling and must not suspend).
            if self.current_page is origin_page and self._clear_generation == origin_gen:
                self._remember_user_message(text)
                asyncio.ensure_future(self._save_history())
                self._append_error("[aborted]", style="aborted")
                rich_rendered = True
            raise
        except ModelHTTPError as e:
            _log.exception("agent run failed with HTTP error")
            if self.current_page is origin_page:
                msg = f"Model HTTP {e.status_code} Error"
                if e.body:
                    msg += f": {e.body}"
                else:
                    msg += f" from {e.model_name}"
                self._remember_user_message(text)
                await self._save_history()
                self._append_error(msg)
                rich_rendered = True
        except UsageLimitExceeded as e:
            _log.exception("agent run failed due to usage limit")
            if self.current_page is origin_page:
                self._remember_user_message(text)
                await self._save_history()
                self._append_error(f"Usage Limit Exceeded: {e}")
                rich_rendered = True
        except ModelAPIError as e:
            _log.exception("agent run failed with API error")
            if self.current_page is origin_page:
                self._remember_user_message(text)
                await self._save_history()
                self._append_error(f"Model API Error: {e}")
                rich_rendered = True
        except UnexpectedModelBehavior as e:
            _log.exception("agent run failed with unexpected behavior")
            if self.current_page is origin_page:
                self._remember_user_message(text)
                await self._save_history()
                self._append_error(f"Unexpected Model Behavior: {e}")
                rich_rendered = True
        except Exception as e:
            _log.exception("agent run failed")
            if self.current_page is origin_page:
                self._remember_user_message(text)
                await self._save_history()
                self._append_error(f"Agent Error: {e}")
                rich_rendered = True
        finally:
            # Paint any throttled-but-unflushed tail before deciding whether to
            # markdown-render, so an error/cancel mid-part never leaves the live
            # bubble stuck at a ~33ms-stale snapshot (the per-token throttle can
            # hold back the last chunk when the stream raises before a flush).
            if ctx is not None:
                self._flush_streaming(ctx, force=True)
            if ctx is not None and not rich_rendered and ctx.full_raw_text and self.current_page is origin_page:
                self._render_markdown_to_box(ctx.box, ctx.full_raw_text)
            self._set_busy(False)
            self._scroll_to_bottom()

    def _on_chat_task_done(self, task: asyncio.Task) -> None:
        """Defence in depth: log any unhandled exception that escaped the
        _run_agent_turn try/except (e.g. a BaseException), and guarantee the
        busy UI is released. The finally in _run_agent_turn already resets
        busy for normal paths."""
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            _log.error("chat task ended with unhandled exception: %s", exc, exc_info=exc)
        if self._busy:
            self._set_busy(False)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        can_type = self._flowgraph_proxy is not None
        self._gear_btn.set_sensitive(not busy)
        self._new_session_btn.set_sensitive(not busy)
        self._clear_hist_btn.set_sensitive(not busy)
        if busy:
            self._send_btn.set_label("Stop")
            self._send_btn.set_sensitive(True)
            self._entry.set_sensitive(False)
        else:
            self._send_btn.set_label("Send")
            self._entry.set_sensitive(can_type)
            self._update_send_sensitivity()
            if can_type:
                self._entry.grab_focus()

    def _scroll_to_bottom(self, *, force: bool = False) -> None:
        def _do_scroll():
            sw = self._scrolled
            if sw is None:
                return False
            adj = sw.get_vadjustment()
            upper = adj.get_upper()
            page = adj.get_page_size()
            # Don't yank the view down while streaming if the user scrolled up
            # to read (unless explicitly forced, e.g. after a full rebuild).
            if not force and (upper - page - adj.get_value()) > _SCROLL_STICK_THRESHOLD:
                return False
            adj.set_value(upper - page)
            return False

        GLib.idle_add(_do_scroll)

    def _open_settings(self) -> None:  # noqa: C901
        toplevel = self.get_toplevel()
        if not isinstance(toplevel, Gtk.Window):
            toplevel = None
        dlg = Gtk.Dialog(title="Chat Settings", transient_for=toplevel, modal=True)
        dlg.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dlg.add_button("Save", Gtk.ResponseType.APPLY)
        dlg.set_default_response(Gtk.ResponseType.APPLY)
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
        model_entry.set_activates_default(True)
        grid.attach(model_entry, 1, 1, 1, 1)

        grid.attach(Gtk.Label(label="API Key:"), 0, 2, 1, 1)
        key_entry = Gtk.Entry()
        key_entry.set_visibility(False)
        key_entry.set_activates_default(True)
        grid.attach(key_entry, 1, 2, 1, 1)

        info = Gtk.Label(label="Changes take effect after restart.")
        info.get_style_context().add_class("dim-label")

        def _sync_provider_fields(combo: Gtk.ComboBoxText) -> None:
            idx = combo.get_active()
            if idx < 0:
                return
            p = _PROVIDER_ORDER[idx]
            model_entry.set_text(cfg.get(_PROVIDER_MODEL_KEY[p], ""))
            model_entry.set_placeholder_text(f"e.g. {_PROVIDER_MODEL_PLACEHOLDER[p]}")
            key_var = _PROVIDER_API_KEY[p]
            if key_var:
                key_entry.set_text(get_env_value(key_var) or "")
                key_entry.set_sensitive(True)
                key_entry.set_placeholder_text(_PROVIDER_KEY_PLACEHOLDER[p])
            else:
                key_entry.set_text("")
                key_entry.set_sensitive(False)
                key_entry.set_placeholder_text("")

        provider_combo.connect("changed", _sync_provider_fields)
        _sync_provider_fields(provider_combo)

        content.pack_start(grid, False, False, 0)
        content.pack_start(info, False, False, 0)
        content.show_all()

        self._open_dialog = dlg

        def _on_response(_dlg: Gtk.Dialog, response: int) -> None:
            # Read widget values BEFORE destroying the dialog. After
            # gtk_widget_destroy(), Gtk.Entry.get_text() returns "" and
            # ComboBox.get_active() returns -1, so reading them afterwards
            # silently skipped save_settings (empty model) and would wipe the
            # API key with an empty string.
            if response == Gtk.ResponseType.APPLY:
                idx = provider_combo.get_active()
                provider = _PROVIDER_ORDER[idx] if idx >= 0 else "ollama"
                model = model_entry.get_text().strip()
                key_var = _PROVIDER_API_KEY.get(provider)
                key_val = key_entry.get_text().strip()
            else:
                provider = model = key_var = key_val = None
            self._open_dialog = None
            dlg.destroy()
            if response != Gtk.ResponseType.APPLY:
                return
            if model:
                save_settings(provider, model)
            if key_var:
                upsert_env_key(key_var, key_val)
            if model:
                self.set_status("Settings saved.")
            else:
                self.set_status("Settings not saved — model name is required.", error=True)

        dlg.connect("response", _on_response)
        dlg.show()
