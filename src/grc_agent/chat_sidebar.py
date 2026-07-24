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
import time
from collections.abc import Callable
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


_MAX_THINKING_DISPLAY_CHARS = 4000
_MAX_TOOL_DISPLAY_CHARS = 8000


def _format_thinking_display(text: str, max_chars: int = _MAX_THINKING_DISPLAY_CHARS) -> str:
    """Format thinking text for Gtk.Label display. Massive thinking output (e.g. 50k+
    chars from deep reasoning models) forces Pango line wrapping to recalculate over the
    entire string, freezing GTK layout and spiking CPU. Truncating display text to a
    bounded window keeps Pango line-wrapping sub-millisecond without affecting full raw text."""
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    return f"{text[:half]}\n\n... [thinking truncated for display ({len(text)} chars total)] ...\n\n{text[-half:]}"


def _format_tool_display(text: str, max_chars: int = _MAX_TOOL_DISPLAY_CHARS) -> str:
    """Format tool argument/result text for Gtk.Expander display labels, keeping Pango bounded."""
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    return f"{text[:half]}\n\n... [truncated {len(text) - max_chars} chars] ...\n\n{text[-half:]}"


_context_length_cache: dict[tuple[str, str], int] = {}


def format_tokens(n: int) -> str:
    """Format token count for display (e.g. 1.2k, 14.7k, 128k, 1M)."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M".replace(".0M", "M")
    if n >= 1_000:
        return f"{n / 1_000:.1f}k".replace(".0k", "k")
    return str(n)


def resolve_model_context_length(provider: str, model: str) -> int | None:
    """Dynamically query the active provider's API for the model's exact context length.

    Queries:
    - Ollama / Ollama Cloud: POST {base_url}/api/show with {"name": model} -> reads model_info context_length
    - OpenRouter: GET https://openrouter.ai/api/v1/models -> reads context_length for the model

    Cached in-memory per (provider, model) pair. Returns None if unresolvable,
    so callers render exact token count without hardcoded guesses.
    """
    key = (provider or "", model or "")
    if key in _context_length_cache:
        return _context_length_cache[key]

    if not provider or not model:
        return None

    import httpx
    from grc_agent.settings import get_env_value

    try:
        if provider in ("ollama", "ollama_cloud"):
            base_url = "https://ollama.com" if provider == "ollama_cloud" else (get_env_value("OLLAMA_BASE_URL") or "http://localhost:11434").rstrip("/")
            api_key = get_env_value("OLLAMA_CLOUD_API_KEY") if provider == "ollama_cloud" else ""
            headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
            url = f"{base_url}/api/show"
            with httpx.Client(timeout=3.0) as client:
                r = client.post(url, json={"name": model}, headers=headers)
                if r.status_code == 200:
                    data = r.json()
                    for k, v in data.get("model_info", {}).items():
                        if "context_length" in k and isinstance(v, (int, float)):
                            ctx_len = int(v)
                            _context_length_cache[key] = ctx_len
                            return ctx_len
                    params = str(data.get("parameters", ""))
                    for line in params.splitlines():
                        if "num_ctx" in line:
                            parts = line.split()
                            if len(parts) >= 2 and parts[1].isdigit():
                                ctx_len = int(parts[1])
                                _context_length_cache[key] = ctx_len
                                return ctx_len

        elif provider == "openrouter":
            api_key = get_env_value("OPENROUTER_API_KEY") or ""
            headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
            with httpx.Client(timeout=3.0) as client:
                r = client.get("https://openrouter.ai/api/v1/models", headers=headers)
                if r.status_code == 200:
                    for m in r.json().get("data", []):
                        m_id = m.get("id", "")
                        if m_id == model or m_id.endswith(model):
                            ctx_len = m.get("context_length")
                            if isinstance(ctx_len, (int, float)):
                                res = int(ctx_len)
                                _context_length_cache[key] = res
                                return res
    except Exception as e:
        _log.debug("Failed to resolve dynamic context length for provider=%s model=%s: %s", provider, model, e)

    return None


def _format_turn_error(e: Exception) -> str:
    """User-facing message for a failed agent turn (_run_agent_turn's catch-all).
    ModelHTTPError carries a status code and optional body/model_name that no
    other exception type has, so it gets its own message shape; everything
    else is a plain `{Label}: {e}`."""
    if isinstance(e, ModelHTTPError):
        msg = f"Model HTTP {e.status_code} Error"
        return f"{msg}: {e.body}" if e.body else f"{msg} from {e.model_name}"
    if isinstance(e, UsageLimitExceeded):
        return f"Usage Limit Exceeded: {e}"
    if isinstance(e, ModelAPIError):
        return f"Model API Error: {e}"
    if isinstance(e, UnexpectedModelBehavior):
        return f"Unexpected Model Behavior: {e}"
    return f"Agent Error: {e}"


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
textview.chat-agent-label,
textview.chat-agent-label text {
    background: transparent;
}
.chat-block-badge {
    background: #e3f2fd;
    color: #0d47a1;
    border: 1px solid #90caf9;
    border-radius: 10px;
    padding: 1px 8px;
    margin: 0px 2px;
    font-weight: bold;
}
.chat-block-badge:hover {
    background: #bbdefb;
    border-color: #42a5f5;
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
.chat-code-header {
    background: #f5f5f5;
    border-bottom: 1px solid #e0e0e0;
    border-radius: 6px 6px 0px 0px;
    padding: 2px 6px;
}
.chat-copy-btn {
    background: #ffffff;
    color: #333333;
    border: 1px solid #cccccc;
    border-radius: 4px;
    padding: 1px 6px;
    font-size: 0.8em;
}
.chat-copy-btn:hover {
    background: #e0e0e0;
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
    padding: 4px 6px;
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
.provider-badge {
    background: #e3f2fd;
    color: #0d47a1;
    border: 1px solid #90caf9;
    border-radius: 10px;
    padding: 2px 12px;
    font-size: 0.85em;
    font-weight: bold;
}
.provider-badge.is-default {
    background: #fff3e0;
    color: #e65100;
    border-color: #ffcc80;
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
    min-height: 32px;
}
.chat-entry placeholder {
    color: #888888;
}
.chat-send-btn {
    background: #1976d2;
    color: #ffffff;
    border: none;
    border-radius: 4px;
    padding: 6px 8px;
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
    "openai_compatible": "OpenAI Compatible / llama.cpp (local)",
    "openrouter": "OpenRouter (cloud)",
    "ollama_cloud": "Ollama Cloud (cloud)",
}
_PROVIDER_MODEL_KEY = {
    "ollama": "ollama_model",
    "openai_compatible": "openai_compatible_model",
    "openrouter": "openrouter_model",
    "ollama_cloud": "ollama_cloud_model",
}
_PROVIDER_API_KEY = {
    "ollama": None,
    "openai_compatible": "OPENAI_COMPATIBLE_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "ollama_cloud": "OLLAMA_CLOUD_API_KEY",
}
# Example text for the Settings dialog's placeholders — derives from
# settings.py's _DEFAULT_MODELS rather than duplicating them.
_PROVIDER_MODEL_PLACEHOLDER = {
    "ollama": "qwen3.6:35b-a3b-q4_K_M",
    "openai_compatible": "local-model",
    "openrouter": "deepseek/deepseek-v4-flash",
    "ollama_cloud": "deepseek-v4-flash:cloud",
}
_PROVIDER_KEY_PLACEHOLDER = {
    "openai_compatible": "Optional (e.g. not-required)",
    "openrouter": "sk-or-v1-...",
    "ollama_cloud": "Paste your API key",
}
_PROVIDER_ORDER = ("ollama", "openai_compatible", "openrouter", "ollama_cloud")

# Map the live model's base_url back to a canonical provider key. Used by
# set_agent to resolve which provider is *actually* running — required
# because OllamaProvider reports .name == "ollama" for both local Ollama
# and Ollama Cloud (only base_url differs), so provider.name alone can't
# tell them apart. Matched by host substring (not exact URL) so different
# path conventions ("http://localhost:11434/v1/" vs "https://openrouter.ai/api/v1")
# all resolve correctly. One uniform rule: provider identity comes from the
# base_url host, never from .name.
_PROVIDER_HOST_MARKERS = (
    ("localhost:11434", "ollama"),
    ("ollama.com", "ollama_cloud"),
    ("openrouter.ai", "openrouter"),
)
# Short badge labels — _PROVIDER_LABELS forms like "Ollama Cloud (cloud)"
# are too long for the toolbar.
_PROVIDER_BADGE_LABEL = {
    "ollama": "ollama",
    "openai_compatible": "llama.cpp / openai",
    "ollama_cloud": "ollama cloud",
    "openrouter": "openrouter",
}


def _resolve_provider_from_base_url(base_url: str) -> str:
    """Map a provider's base_url back to its canonical cfg key. Returns
    '' if base_url is empty."""
    if "openrouter.ai" in base_url:
        return "openrouter"
    if "ollama.com" in base_url:
        return "ollama_cloud"
    if "11434" in base_url:
        return "ollama"
    if base_url:
        return "openai_compatible"
    return ""


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
        "think_expander",
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
        self.think_body: Any = None
        self.think_expander: Gtk.Expander | None = None
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
        # Live-swap callback: when the Settings dialog saves a new provider/
        # model/key, this rebuilds the Agent in-place. Set by desktop_app.py
        # right after set_agent(). None in tests/headless mode (the Settings
        # dialog falls back to the old restart-gated behavior if unset).
        self._rebuild_agent: Callable[[], tuple[Agent, str | None]] | None = None
        # Active provider/model label shown in the toolbar; updated on every
        # set_agent call (startup + live-swap) so the user always sees which
        # backend the running agent is actually using.
        self._active_provider: str = ""
        self._active_model: str = ""
        self._active_provider_is_default: bool = False
        # True when the status bar currently shows an error. set_status uses
        # this to enforce the "background poll can't clobber a sticky error"
        # rule (M5) — saves save/preflight failures visible past the next
        # "Catalog indexed" transition.
        self._status_is_error: bool = False
        # Auto-scroll tracking: True by default (follow new content). Cleared
        # by a user-initiated scroll-up (scroll-event signal), re-enabled when
        # the user scrolls back near the bottom or sends a new message. Replaces
        # the old position-based stickiness check which death-spiraled during
        # streaming: once a scroll was skipped (>80px from bottom), the gap
        # only grew as more content arrived, so ALL subsequent scrolls were
        # skipped until the agent finished.
        self._auto_scroll: bool = True
        self._flowgraph_proxy: object | None = None
        # Cache for the compiled block-name badge regex: (frozenset(names), pattern).
        # Rebuilt only when the active flowgraph's block-name set changes.
        self._badge_regex_cache: tuple[frozenset, re.Pattern] | None = None
        # Last width _rewrap_prose_textviews() ran against — history can be
        # rendered before the window is ever shown (get_allocated_width()
        # is 0 then), and the paned divider can be dragged after the fact;
        # both need prose bubbles re-clamped once a real width is known.
        self._last_listbox_width = 0
        # Pending idle source id for a deferred _rewrap_prose_textviews, or
        # None. _on_listbox_size_allocate coalesces the O(history) rewrap onto
        # idle so dragging the paned divider (size-allocate fires dozens of
        # times/sec) doesn't re-measure every prose bubble on every event —
        # ~18ms at N=200, which exceeds a 16.7ms frame and drops frames on the
        # unified gbulb loop. The idle runs once after the allocate burst
        # settles, reading the live listbox width.
        self._rewrap_idle_id: int | None = None
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
        # Set by shutting_down() (called from desktop_app.py's _shutdown)
        # just before stop_chat(). _run_agent_turn's finally block checks
        # this to skip widget operations on widgets that are mid-destroy
        # when the window closes (L7).
        self._shutting_down: bool = False
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
        self._blocks_toggle.set_valign(Gtk.Align.FILL)
        self._blocks_arrow = Gtk.Image.new_from_icon_name("pan-end-symbolic", Gtk.IconSize.SMALL_TOOLBAR)
        self._blocks_toggle.set_image(self._blocks_arrow)
        self._blocks_toggle.set_tooltip_text("Toggle GRC block library")
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

        self.connect("key-press-event", self._on_key_press_event)

        # Refresh relative timestamps ("2m ago") on the recent-sessions list
        # while the welcome screen is visible. Re-renders only when idle and
        # empty so live-streaming bubbles are never wiped.
        GLib.timeout_add_seconds(60, self._refresh_welcome_times)

        # Poll the RAG index-build status (set by the worker thread that runs
        # ingest) and surface progress in the status bar. Cheap dict reads; the
        # build itself runs off the main loop via asyncio.to_thread.
        GLib.timeout_add(500, self._poll_indexing)

    def _on_key_press_event(self, _widget: Gtk.Widget, event: Gdk.EventKey) -> bool:
        if (event.state & Gdk.ModifierType.CONTROL_MASK) and event.keyval in (Gdk.KEY_comma, Gdk.KEY_Comma):
            self._open_settings()
            return True
        return False

    def _build_toolbar(self, content: Gtk.Box) -> None:
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        bar.set_border_width(4)

        def _icon_btn(icon_name: str, tooltip: str, signal: str | None = None, cb=None) -> Gtk.Button:
            b = Gtk.Button.new_from_icon_name(icon_name, Gtk.IconSize.SMALL_TOOLBAR)
            b.set_tooltip_text(tooltip)
            b.get_style_context().add_class("chat-toolbar-btn")
            if signal:
                b.connect("clicked", lambda *_: self.emit(signal))
            if cb:
                b.connect("clicked", cb)
            bar.pack_start(b, False, False, 0)
            return b

        self._new_session_btn = _icon_btn("document-new-symbolic", "New chat session", "new-session-clicked")
        self._clear_hist_btn = _icon_btn("edit-clear-all-symbolic", "Clear conversation history", cb=self._on_clear_history_clicked)

        # Active graph badge — ellipsizes so it shrinks with the sidebar
        self._graph_label = Gtk.Label(label="Active Graph: none")
        self._graph_label.get_style_context().add_class("graph-badge")
        self._graph_label.set_ellipsize(Pango.EllipsizeMode.END)
        self._graph_label.set_max_width_chars(15)
        self.set_active_graph(None)
        bar.pack_start(self._graph_label, True, True, 4)

        # Active provider badge — reflects the *running* agent's actual
        # provider/model, not the saved .env (which can diverge after a
        # Settings save until a live-swap or restart). Updated by
        # set_active_provider on startup and after every live-swap.
        self._provider_label = Gtk.Label(label="")
        self._provider_label.get_style_context().add_class("provider-badge")
        self._provider_label.set_tooltip_text(
            "The provider/model the running chat agent is using right now. "
            "Settings changes apply immediately on Save."
        )
        self._provider_label.set_ellipsize(Pango.EllipsizeMode.END)
        self._provider_label.set_max_width_chars(20)
        bar.pack_start(self._provider_label, True, True, 0)

        # Settings
        self._gear_btn = _icon_btn("preferences-system-symbolic", "Preferences (Ctrl+,)", cb=lambda *_: self._open_settings())

        bar.get_style_context().add_class("chat-toolbar")
        content.pack_start(bar, False, False, 0)

    def _build_message_list(self, content: Gtk.Box) -> None:
        self._listbox = Gtk.ListBox()
        self._listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self._listbox.set_activate_on_single_click(False)
        self._listbox.set_border_width(4)
        self._listbox.get_style_context().add_class("chat-msg-list")
        self._listbox.connect("size-allocate", self._on_listbox_size_allocate)

        self._scrolled = Gtk.ScrolledWindow()
        self._scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._scrolled.set_vexpand(True)
        self._scrolled.add(self._listbox)
        # Track user scroll intent: if the user scrolls UP to read, stop
        # auto-scrolling so they're not yanked back down. When they scroll
        # back near the bottom, resume auto-scroll. This is the standard
        # terminal/chat-scroll pattern and replaces the position-based
        # stickiness check that death-spiraled during streaming.
        self._scrolled.connect("scroll-event", self._on_user_scroll)

        content.pack_start(self._scrolled, True, True, 0)

    def _build_input_area(self, content: Gtk.Box) -> None:
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        vbox.set_border_width(4)

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

        self._entry = Gtk.Entry()
        self._entry.set_placeholder_text("Open a flowgraph in GRC to start chatting...")
        self._entry.set_hexpand(True)
        self._entry.get_style_context().add_class("chat-entry")
        self._entry.connect("activate", self._on_entry_activate)
        self._entry.connect("key-press-event", self._on_entry_key_press)
        self._entry.connect("changed", lambda *_: self._update_send_sensitivity())
        self._entry.set_sensitive(False)

        self._send_btn = Gtk.Button.new_from_icon_name("media-playback-start-symbolic", Gtk.IconSize.SMALL_TOOLBAR)
        self._send_btn.set_tooltip_text("Send message (Enter)")
        self._send_btn.get_style_context().add_class("chat-send-btn")
        self._send_btn.connect("clicked", self._on_send_clicked)
        self._send_btn.set_sensitive(False)

        box.pack_start(self._entry, True, True, 0)
        box.pack_start(self._send_btn, False, False, 0)
        vbox.pack_start(box, False, False, 0)

        # Context usage label right under the text input box
        self._context_label = Gtk.Label()
        self._context_label.set_xalign(0.0)
        self._context_label.set_halign(Gtk.Align.START)
        self._context_label.get_style_context().add_class("chat-context-label")
        self._context_label.set_margin_start(4)
        self._context_label.set_margin_top(2)
        self._context_label.set_margin_bottom(2)
        vbox.pack_start(self._context_label, False, False, 0)

        content.pack_start(vbox, False, False, 0)
        self._update_context_label()

    def _update_context_label(self) -> None:
        """Update the context usage label under the input box using Pydantic AI's native msg.usage."""
        last_input_tokens = 0
        total_session_tokens = 0
        for msg in self._message_history:
            if msg.__class__.__name__ == "ModelResponse" and hasattr(msg, "usage") and msg.usage:
                u = msg.usage
                inp = getattr(u, "input_tokens", 0) or 0
                if inp:
                    last_input_tokens = inp
                total_session_tokens += getattr(u, "total_tokens", 0) or 0

        active_provider = getattr(self, "_active_provider", "") or ""
        active_model = getattr(self, "_active_model", "") or ""
        max_context = resolve_model_context_length(active_provider, active_model)

        if not self._message_history or last_input_tokens == 0:
            if max_context:
                text = f"<span fgcolor='#555555' size='small'>Context: 0 / {format_tokens(max_context)} tokens</span>"
            else:
                text = "<span fgcolor='#555555' size='small'>Context: 0 tokens</span>"
        else:
            if max_context:
                pct = min(100.0, (last_input_tokens / max_context) * 100)
                color = "#444444" if pct < 75 else ("#b45309" if pct < 90 else "#c53030")
                text = (
                    f"<span fgcolor='{color}' size='small'>"
                    f"Context: {format_tokens(last_input_tokens)} / {format_tokens(max_context)} tokens ({pct:.0f}%)"
                    f"</span>"
                )
            else:
                text = f"<span fgcolor='#555555' size='small'>Context: {format_tokens(last_input_tokens)} tokens</span>"

        if hasattr(self, "_context_label"):
            self._context_label.set_markup(text)
            self._context_label.set_tooltip_text(
                f"Active model: {active_model or 'default'}\n"
                f"Provider: {active_provider or 'unknown'}\n"
                f"Last turn input context: {last_input_tokens:,} tokens\n"
                f"Total session tokens: {total_session_tokens:,} tokens\n"
                f"Max model context: {f'{max_context:,}' if max_context else 'unknown'}"
            )

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

    def set_status(self, msg: str, *, error: bool = False, background: bool = False) -> None:
        """Update the status bar.

        Errors are sticky — a background message (``background=True``, e.g.
        the indexing poll) cannot overwrite a current error. User-initiated
        actions (the default) and other errors always overwrite. One uniform
        rule that keeps save errors / preflight failures / unreachable-backend
        warnings visible past the next "Catalog indexed" transition (M5).
        """
        if background and not error and self._status_is_error:
            return
        self._status_label.set_text(msg)
        self._status_is_error = error
        if error:
            self._status_label.get_style_context().remove_class("validation-valid")
            self._status_label.get_style_context().add_class("validation-invalid")
        else:
            self._status_label.get_style_context().remove_class("validation-invalid")
            self._status_label.get_style_context().remove_class("validation-valid")

    def set_active_graph(self, name: str | None, path: str | None = None) -> None:
        self._active_graph_name = name
        self._active_graph_path = path
        self._graph_label.set_text(f"Active Graph: {name}" if name else "Active Graph: none")
        if name and path:
            self._graph_label.set_tooltip_text(f"Active Flowgraph: {name}\nFull Path: {path}")
        elif name:
            self._graph_label.set_tooltip_text(f"Active Flowgraph: {name}\nFull Path: (Unsaved / In-memory)")
        else:
            self._graph_label.set_tooltip_text("No flowgraph currently active or open in GRC")

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
                    # background=True so a "Catalog indexed" transition can't
                    # clobber a sticky save/preflight error the user still
                    # needs to read (M5).
                    self.set_status(
                        f"{label.capitalize()} indexed \u2014 {n} entries ready for search.",
                        background=True,
                    )
                else:
                    # Indexing failures ARE surfaced — they're actionable
                    # ("search may return no or stale results") and the
                    # error class is preserved by the sticky rule.
                    self.set_status(
                        f"{label.capitalize()} indexing failed; search may return no or stale results.",
                        error=True,
                    )
                return True  # re-arm
        if building_msg is not None and building_msg != self._last_index_msg:
            self._last_index_msg = building_msg
            self.set_status(building_msg, background=True)
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
        except Exception as e:
            _log.error("Failed to delete session %s: %s", session_id, e)
            self.set_status(f"Failed to delete session: {e}", error=True)
        self._render_history()

    def grab_entry_focus(self) -> bool:
        """Grab keyboard focus for the chat text entry box if sensitive."""
        if hasattr(self, "_entry") and self._entry.get_sensitive():
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
                cm = getattr(self._flowgraph_proxy, "_canvas_manager", None)
                path = cm.path if cm else ""
            if not path:
                self._entry.set_placeholder_text("Save the flowgraph to keep this chat. Ask about your flowgraph...")
            else:
                self._entry.set_placeholder_text("Ask about your flowgraph...")
            self.grab_entry_focus()
        else:
            self._entry.set_placeholder_text("Open or create a flowgraph in GRC to start chatting...")

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
        # Reflect the *running* agent's provider/model in the toolbar badge.
        # The provider is resolved from the model's base_url (not provider.name
        # — OllamaProvider.name returns "ollama" for both local and cloud, so
        # only base_url can tell them apart). See _PROVIDER_BASE_URL.
        model = getattr(agent, "model", None)
        model_name = ""
        resolved_provider = ""
        base_url = ""
        if model is not None:
            model_name = getattr(model, "_model_name", getattr(model, "model_name", "")) or ""
            provider = getattr(model, "_provider", None) or getattr(model, "provider", None)
            base_url = str(getattr(provider, "base_url", "") or "")
            resolved_provider = _resolve_provider_from_base_url(base_url)
        try:
            cfg = load_settings()
            expected = cfg.get("provider", "")
            is_default = (
                bool(resolved_provider) and bool(expected) and resolved_provider != expected
            )
        except Exception:
            is_default = False
        self.set_active_provider(resolved_provider, model_name, is_default=is_default, base_url=base_url)

    def set_rebuild_agent_callback(self, cb: Callable[[], tuple[Agent, str | None]]) -> None:
        """Wire the live-swap entry point. desktop_app.py calls this once at
        startup with a closure over `build_agent_from_cfg(load_settings())`;
        the Settings dialog invokes it after a successful Save to apply the
        new provider/model/key to the running process immediately."""
        self._rebuild_agent = cb

    def set_active_provider(self, provider: str, model: str, *, is_default: bool = False, base_url: str | None = None) -> None:
        """Update the toolbar's active-provider badge and rich tooltip. `is_default` is True
        when the running agent's resolved provider doesn't match the saved
        cfg (e.g. a startup build failure fell back to local Ollama), shown
        orange to distinguish from a deliberate choice."""
        self._active_provider = provider
        self._active_model = model
        self._active_provider_is_default = is_default
        self._active_base_url = base_url
        if not provider:
            self._provider_label.set_text("")
            self._provider_label.hide()
            return
        short_model = model.rsplit("/", 1)[-1]
        badge_label = _PROVIDER_BADGE_LABEL.get(provider, provider)
        self._provider_label.set_text(f"{badge_label} \u00b7 {short_model}")
        ctx = self._provider_label.get_style_context()
        if is_default:
            ctx.add_class("is-default")
        else:
            ctx.remove_class("is-default")

        provider_title = _PROVIDER_LABELS.get(provider, provider.capitalize())
        resolved_url = base_url or (
            "https://openrouter.ai/api/v1"
            if provider == "openrouter"
            else ("https://ollama.com" if provider == "ollama_cloud" else "http://localhost:11434")
        )
        status_str = (
            "Fallback default (configured provider unreachable)"
            if is_default
            else "Configured provider active"
        )
        tooltip_text = (
            f"Provider: {provider_title}\n"
            f"Model: {model}\n"
            f"Base URL: {resolved_url}\n"
            f"Status: {status_str}\n\n"
            f"Click Preferences (Ctrl+Comma) to change settings."
        )
        self._provider_label.set_tooltip_text(tooltip_text)
        self._provider_label.show()
        self._update_context_label()

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

    def sync_to_file(self, path: str | None) -> None:  # noqa: ARG002
        """Called when the active graph changes (tab switch / open / close).

        Graphs NEVER auto-load chats. The only entry point for loading a
        saved conversation is explicitly clicking it from the recent-
        sessions list, which opens the associated graph file AND loads the
        session. That click sets ``_loading_session_id`` before triggering
        the tab switch, so the resulting call to this method sees it set
        and returns without clearing the session the click just loaded.

        On every other path (user opens a graph, switches tabs, etc.), the
        chat area clears to the welcome screen — no session is bound to
        the graph. A new chat starts fresh on the next Send.
        """
        if self._loading_session_id is not None:
            return
        # Reset per-tab UI state: a new graph means the old tab's sticky
        # error and auto-scroll intent no longer apply.
        self._auto_scroll = True
        self._status_is_error = False
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
        self._render_history()

    def _refresh_welcome_times(self) -> bool:
        """Periodically re-render the welcome/recent-sessions list so the
        relative timestamps ("2m ago") stay fresh. Only runs when idle and the
        history is empty (the only state in which the list is visible); never
        disturbs a live chat stream."""
        if not self._busy and not self._message_history:
            self._render_history()
        return True  # re-arm

    def _render_history(self) -> None:  # noqa: C901
        for child in self._listbox.get_children():
            self._listbox.remove(child)

        # A full rebuild destroys any badge pill mid-hover without a
        # leave-notify-event (GTK3 doesn't synthesize one on widget
        # destruction), which could otherwise leave a stale canvas highlight.
        cm = getattr(self._flowgraph_proxy, "_canvas_manager", None)
        if cm:
            cm.clear_highlight()

        if not self._message_history:
            self._render_welcome_screen()
            self._listbox.show_all()
            self._update_context_label()
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
        self._update_context_label()

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
            welcome_box.pack_start(sub_lbl, False, False, 0)

            # Quick Action Prompt Chips
            chips_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            chips_box.set_margin_top(4)

            quick_prompts = [
                ("🔍 Inspect graph", "Inspect this flowgraph and summarize its architecture."),
                ("⚡ Check errors", "Check this flowgraph for configuration errors or missing parameters."),
                ("❓ Explain pipeline", "Explain what signal processing pipeline this flowgraph implements."),
            ]

            for label_text, prompt_text in quick_prompts:
                btn = Gtk.Button(label=label_text)
                btn.get_style_context().add_class("chat-toolbar-btn")
                btn.set_tooltip_text(f'Send: "{prompt_text}"')
                btn.connect("clicked", lambda _, p=prompt_text: self._send_quick_prompt(p))
                chips_box.pack_start(btn, False, False, 0)

            welcome_box.pack_start(chips_box, False, False, 0)
        else:
            sub_lbl.set_markup(
                "<span fgcolor='#444444' size='small'>"
                "No flowgraph is currently open.\n"
                "Open or create a flowgraph in GRC (<b>File &gt; New</b> or <b>File &gt; Open</b>), "
                "or select a recent session below:"
                "</span>"
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

    def _send_quick_prompt(self, text: str) -> None:
        if self._busy or self.current_page is None:
            return
        self.grab_entry_focus()
        self.send_message(text)

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
                    _log.debug("recent-session: skipping page %r during resolve", p_path, exc_info=True)

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

    def shutting_down(self) -> None:
        """Signal that the app is shutting down — any in-flight widget cleanup
        (streaming flush, scroll-to-bottom, busy reset) should be skipped to
        avoid GTK warnings/crashes on mid-destroy widgets (L7)."""
        self._shutting_down = True

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
            self._flush_streaming(ctx)
        elif isinstance(delta, ThinkingPartDelta):
            self._close_text(ctx)
            ctx.think_acc += delta.content_delta
            ctx.full_raw_text += delta.content_delta
            self._ensure_thinking(ctx)
            ctx.think_dirty = True
            self._flush_streaming(ctx)

    def _flush_streaming(self, ctx: _StreamCtx, *, force: bool = False) -> None:
        """Push accumulated streamed text/thinking to their labels at an adaptive
        interval. Each ``Gtk.Label.set_text`` re-runs Pango's line-wrap layout over
        the full (growing) text, so calling it per token is O(n^2). Adaptive
        throttling scales the flush interval with text length to prevent Pango layout
        computation from starving the single-threaded event loop on high-velocity
        token streams. A forced flush (part start/close, stream end) bypasses the
        interval so transitions never show stale text."""
        now = time.monotonic()
        if not force:
            text_len = len(ctx.text_acc) + len(ctx.think_acc)
            if text_len > 5000:
                interval = 0.066
            elif text_len > 2000:
                interval = 0.050
            else:
                interval = _STREAM_FLUSH_INTERVAL
            if (now - ctx.last_flush) < interval:
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
        if flushed or force:
            ctx.last_flush = now
            if force:
                self._update_copy_text(ctx.box, ctx.full_raw_text)
            if flushed:
                self._scroll_to_bottom()

    def _close_text(self, ctx: _StreamCtx) -> None:
        self._flush_streaming(ctx, force=True)
        ctx.text_lbl = None
        ctx.text_acc = ""
        ctx.text_dirty = False

    def _close_thinking(self, ctx: _StreamCtx) -> None:
        self._flush_streaming(ctx, force=True)
        if ctx.think_expander is not None:
            ctx.think_expander.set_label("Thinked")
        ctx.think_body = None
        ctx.think_expander = None
        ctx.think_acc = ""
        ctx.think_dirty = False

    def _ensure_text(self, ctx: _StreamCtx) -> Gtk.Label:
        if ctx.text_lbl is None:
            ctx.text_lbl = self._make_text_label()
            ctx.box.pack_start(ctx.text_lbl, False, False, 0)
            ctx.text_lbl.show_all()
        return ctx.text_lbl

    def _make_thinking_textview(self, text: str = "") -> Gtk.TextView:
        tv = Gtk.TextView()
        tv.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        tv.set_editable(False)
        tv.set_cursor_visible(False)
        tv.get_style_context().add_class("chat-thinking-textview")
        tv.set_text = lambda t: tv.get_buffer().set_text(t)  # type: ignore[attr-defined]
        tv.get_text = lambda: tv.get_buffer().get_text(  # type: ignore[attr-defined]
            tv.get_buffer().get_start_iter(), tv.get_buffer().get_end_iter(), True
        )
        if text:
            tv.set_text(text)
        return tv

    def _make_thinking_widget(
        self, text: str = "", label: str = "Thinking..."
    ) -> tuple[Gtk.Expander, Gtk.TextView]:
        exp = Gtk.Expander(label=label)
        exp.set_expanded(False)
        exp.get_style_context().add_class("chat-thinking-expander")
        exp.set_hexpand(True)
        exp.set_halign(Gtk.Align.FILL)

        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sw.set_shadow_type(Gtk.ShadowType.NONE)
        sw.set_min_content_height(80)
        sw.set_max_content_height(250)
        sw.set_propagate_natural_height(True)
        sw.set_hexpand(True)
        sw.set_halign(Gtk.Align.FILL)

        tv = self._make_thinking_textview(text)
        tv.set_hexpand(True)
        tv.set_halign(Gtk.Align.FILL)

        sw.add(tv)
        exp.add(sw)
        return exp, tv

    def _ensure_thinking(self, ctx: _StreamCtx) -> Any:
        if ctx.think_body is None:
            exp, tv = self._make_thinking_widget(label="Thinking...")
            ctx.box.pack_start(exp, True, True, 0)
            exp.show_all()
            ctx.think_expander = exp
            ctx.think_body = tv
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

    def _copy_to_clipboard(self, text: str, btn: Gtk.Button | None = None) -> None:
        if not text:
            return
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        clipboard.set_text(text, -1)
        self.set_status("Copied message to clipboard.")
        if btn is not None:
            btn.set_image(Gtk.Image.new_from_icon_name("emblem-ok-symbolic", Gtk.IconSize.MENU))
            btn.set_tooltip_text("Copied!")

            def _revert() -> bool:
                btn.set_image(Gtk.Image.new_from_icon_name("edit-copy-symbolic", Gtk.IconSize.MENU))
                btn.set_tooltip_text("Copy message")
                return False

            GLib.timeout_add(1500, _revert)

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
        copy_btn.connect("clicked", lambda b: self._copy_to_clipboard(text, b))

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
        copy_btn.connect("clicked", lambda b: self._copy_to_clipboard(getattr(b, "_grc_copy_text", ""), b))

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

    def _get_active_block_names(self) -> set[str]:
        cm = getattr(self._flowgraph_proxy, "_canvas_manager", None)
        fg = cm.current_flow_graph if cm else None
        return {b.name for b in fg.blocks} if fg else set()

    def _compile_badge_regex(self) -> re.Pattern | None:
        """One uniform whole-word rule built from the live flowgraph's block
        names — no per-scenario heuristics. Cached by the block-name set so
        it's rebuilt only when blocks are added/removed/renamed.

        Known tradeoff: a block name that is also a common English word
        (filter, sink, source, add, ...) is badged wherever it appears in
        ordinary prose. Accepted — the alternative (an allowlist of
        "badge-worthy" names) would be exactly the per-scenario heuristic
        this function exists to avoid."""
        names = self._get_active_block_names()
        if not names:
            self._badge_regex_cache = None
            return None
        key = frozenset(names)
        if self._badge_regex_cache and self._badge_regex_cache[0] == key:
            return self._badge_regex_cache[1]
        alternation = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
        # \w (not an ASCII [A-Za-z0-9_] class) so the word boundary matches
        # GRC's own block-id validation `^[a-z|A-Z]\w*$` (Python3 \w is
        # Unicode) — otherwise an ASCII block name immediately followed by a
        # Unicode letter in prose (e.g. block "data" inside "dataéx") reads
        # the é as a non-word boundary and false-badges the substring.
        pattern = re.compile(r"(?<!\w)(" + alternation + r")(?!\w)")
        self._badge_regex_cache = (key, pattern)
        return pattern

    def _on_badge_enter(self, _widget: Gtk.Widget, _event: Any, name: str) -> bool:
        cm = getattr(self._flowgraph_proxy, "_canvas_manager", None)
        if cm is not None:
            cm.set_highlight_block(name)
        return False

    def _on_badge_leave(self, _widget: Gtk.Widget, _event: Any, _name: str) -> bool:
        cm = getattr(self._flowgraph_proxy, "_canvas_manager", None)
        if cm is not None:
            cm.clear_highlight()
        return False

    def _on_badge_click(self, _widget: Gtk.Widget, event: Any, name: str) -> bool:
        if event.type == Gdk.EventType.BUTTON_PRESS and getattr(event, "button", 1) == 1:
            cm = getattr(self._flowgraph_proxy, "_canvas_manager", None)
            if cm is not None:
                cm.scroll_to_block(name)
            return True
        return False

    def _make_block_badge_widget(self, name: str) -> Gtk.EventBox:
        eb = Gtk.EventBox()
        eb.get_style_context().add_class("chat-block-badge")
        eb.set_above_child(True)  # events route to the EventBox, not the child label
        eb.add_events(
            Gdk.EventMask.ENTER_NOTIFY_MASK
            | Gdk.EventMask.LEAVE_NOTIFY_MASK
            | Gdk.EventMask.BUTTON_PRESS_MASK
        )
        # Marker so _size_prose_textview_to_content can account for the pills'
        # own width without coupling to the EventBox type or the CSS chrome.
        eb.grc_is_badge = True

        lbl = Gtk.Label(label=name)
        lbl.set_selectable(False)
        eb.add(lbl)

        # Bound methods (not lambdas) so tests can invoke the hover behavior
        # directly without fabricating a Gdk.EventCrossing.
        eb.connect("enter-notify-event", self._on_badge_enter, name)
        eb.connect("leave-notify-event", self._on_badge_leave, name)
        eb.connect("button-press-event", self._on_badge_click, name)
        return eb

    def _ensure_buffer_tags(self, buffer: Gtk.TextBuffer) -> None:
        """Idempotent named-tag setup mirroring the markup vocabulary the old
        _node_to_pango used. Per-link tags are created separately (per href),
        not here."""
        tag_table = buffer.get_tag_table()
        if tag_table.lookup("bold") is None:
            buffer.create_tag("bold", weight=Pango.Weight.BOLD)
        if tag_table.lookup("italic") is None:
            buffer.create_tag("italic", style=Pango.Style.ITALIC)
        if tag_table.lookup("code") is None:
            buffer.create_tag("code", family="monospace", background="#f0f0f0")
        if tag_table.lookup("heading") is None:
            buffer.create_tag("heading", weight=Pango.Weight.BOLD, scale=1.15)

    def _insert_plain_tagged(self, buffer: Gtk.TextBuffer, text: str, tags: list) -> None:
        if not text:
            return
        start_offset = buffer.get_end_iter().get_offset()
        buffer.insert(buffer.get_end_iter(), text)
        if tags:
            # get_iter_at_offset (not the iter passed to insert()) — GTK
            # revalidates that iter to the END of the inserted text, so
            # start/end would otherwise both land on the same position.
            start = buffer.get_iter_at_offset(start_offset)
            end = buffer.get_end_iter()
            for t in tags:
                if isinstance(t, str):
                    buffer.apply_tag_by_name(t, start, end)
                else:
                    buffer.apply_tag(t, start, end)

    def _insert_prose_text_with_badges(
        self, buffer: Gtk.TextBuffer, text: str, tags: list, tv: Gtk.TextView
    ) -> None:
        rx = self._compile_badge_regex()
        if rx is None:
            self._insert_plain_tagged(buffer, text, tags)
            return

        last_end = 0
        for m in rx.finditer(text):
            self._insert_plain_tagged(buffer, text[last_end : m.start()], tags)

            name = m.group(1)
            anchor = buffer.create_child_anchor(buffer.get_end_iter())
            pill = self._make_block_badge_widget(name)
            tv.add_child_at_anchor(pill, anchor)
            pill.show_all()

            last_end = m.end()

        self._insert_plain_tagged(buffer, text[last_end:], tags)

    def _on_link_tag_event(self, _tag: Any, _widget: Any, event: Any, _iter: Any, href: str) -> bool:
        """Mirrors Gtk.Label's built-in activate-link default handler, which
        the old Pango-markup <a href> path got for free."""
        if href and event.type == Gdk.EventType.BUTTON_RELEASE:
            Gtk.show_uri_on_window(None, href, event.time)
            return True
        return False

    def _on_prose_motion_notify(self, tv: Gtk.TextView, event: Any) -> bool:
        """Shows a pointer cursor over link text, matching Label's own link
        hover affordance — and resets it once the cursor leaves the link, so
        it doesn't get stuck as a hand cursor for the rest of the message."""
        bx, by = tv.window_to_buffer_coords(Gtk.TextWindowType.TEXT, int(event.x), int(event.y))
        _found, it = tv.get_iter_at_location(bx, by)
        hovering_link = any(getattr(t, "grc_href", None) for t in it.get_tags())
        window = tv.get_window(Gtk.TextWindowType.TEXT)
        if window is not None:
            cursor = Gdk.Cursor.new_from_name(window.get_display(), "pointer") if hovering_link else None
            window.set_cursor(cursor)
        return False

    def _element_to_buffer(  # noqa: C901
        self, element: Any, buffer: Gtk.TextBuffer, tv: Gtk.TextView, active_tags: list
    ) -> None:
        """Structural port of the old _node_to_pango: same recursive tag
        dispatch, writing directly into a TextBuffer (with badge-aware leaf
        text and real per-link click handling) instead of composing a Pango
        markup string."""
        if isinstance(element, NavigableString):
            self._insert_prose_text_with_badges(buffer, str(element), active_tags, tv)
            return

        tag = element.name
        if not tag:
            return

        if tag in ("ul", "ol"):
            li_children = [c for c in element.children if getattr(c, "name", None) == "li"]
            for i, li in enumerate(li_children, start=1):
                prefix = f"{i}." if tag == "ol" else "•"
                self._insert_plain_tagged(buffer, f"  {prefix}  ", active_tags)
                for child in li.children:
                    self._element_to_buffer(child, buffer, tv, active_tags)
                self._insert_plain_tagged(buffer, "\n", active_tags)
            return

        if tag in ("p", "div"):
            for child in element.children:
                self._element_to_buffer(child, buffer, tv, active_tags)
            self._insert_plain_tagged(buffer, "\n", active_tags)
        elif tag in ("strong", "b"):
            for child in element.children:
                self._element_to_buffer(child, buffer, tv, active_tags + ["bold"])
        elif tag in ("em", "i"):
            for child in element.children:
                self._element_to_buffer(child, buffer, tv, active_tags + ["italic"])
        elif tag in ("code", "tt"):
            for child in element.children:
                self._element_to_buffer(child, buffer, tv, active_tags + ["code"])
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            for child in element.children:
                self._element_to_buffer(child, buffer, tv, active_tags + ["heading"])
            self._insert_plain_tagged(buffer, "\n", active_tags)
        elif tag == "a":
            href = element.get("href", "")
            link_tag = buffer.create_tag(None, foreground="#1565c0", underline=Pango.Underline.SINGLE)
            link_tag.grc_href = href
            link_tag.connect("event", self._on_link_tag_event, href)
            for child in element.children:
                self._element_to_buffer(child, buffer, tv, active_tags + [link_tag])
        elif tag == "li":
            # Defensive fallback for a stray orphaned <li> outside any ul/ol —
            # the normal case is handled above by the ul/ol branch itself.
            self._insert_plain_tagged(buffer, "  •  ", active_tags)
            for child in element.children:
                self._element_to_buffer(child, buffer, tv, active_tags)
            self._insert_plain_tagged(buffer, "\n", active_tags)
        elif tag in ("table", "thead", "tbody", "tr", "td", "th", "pre"):
            # Never reached in practice — _render_markdown_to_box intercepts
            # <table>/<pre> at the top level before this walker is invoked.
            # Suppressed here (not recursed into) for parity with the old
            # _node_to_pango's identical thead/tbody/tr/td/th no-op branch.
            return
        else:
            for child in element.children:
                self._element_to_buffer(child, buffer, tv, active_tags)

    def _make_prose_textview(self) -> Gtk.TextView:
        tv = Gtk.TextView()
        tv.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        tv.set_editable(False)
        tv.set_cursor_visible(False)
        tv.get_style_context().add_class("chat-agent-label")
        tv.grc_is_prose = True  # marks it for _rewrap_prose_textviews, distinct
        # from the unrelated "Thinking" expander's fixed-height textview.
        tv.set_left_margin(0)
        tv.set_right_margin(0)
        tv.set_top_margin(0)
        tv.set_bottom_margin(0)
        # Established in this file's own _make_thinking_widget (hexpand +
        # halign FILL) as the pattern that gets a non-editable TextView
        # correct width-for-height word-wrap negotiation inside a Gtk.Box.
        tv.set_hexpand(True)
        tv.set_halign(Gtk.Align.FILL)
        tv.add_events(Gdk.EventMask.POINTER_MOTION_MASK)
        tv.connect("motion-notify-event", self._on_prose_motion_notify)
        return tv

    def _size_prose_textview_to_content(self, tv: Gtk.TextView, plain_text: str) -> None:
        """Gtk.TextView deliberately reports only a minimal preferred width
        for word-wrapped content — it can't compute wrapped height until a
        width is chosen, unlike Gtk.Label, which self-measures via Pango.
        Left alone, the agent message bubble (hbox.set_halign(START), which
        hugs its content instead of stretching) collapses to that minimal
        width and wraps one word per line. Measuring the actual text's
        unwrapped Pango extent and capping it at the available column width
        restores a sane wrap width for long messages while still hugging
        short ones, matching Label's old behavior."""
        layout = tv.create_pango_layout(plain_text)
        _ink, logical = layout.get_pixel_extents()
        available = self._listbox.get_allocated_width() or 320
        max_width = max(160, available - 90)
        width = min(logical.width, max_width)
        # A pill badge is wider than the name glyphs plain_text contributed
        # (CSS padding/border/margin around its label), so plain_text alone
        # under-counts and a badge-only/badge-heavy bubble could request less
        # than its own pills need. Floor the request at the badges' natural
        # widths (get_preferred_width() is valid before allocation), capped at
        # the column so a badge flood still wraps instead of overflowing.
        badges = [c for c in tv.get_children() if getattr(c, "grc_is_badge", False)]
        if badges:
            min_for_pills = min(sum(c.get_preferred_width()[1] for c in badges), max_width)
            width = max(width, min_for_pills)
        tv.set_size_request(width, -1)

    def _on_listbox_size_allocate(self, _listbox: Gtk.ListBox, allocation: Any) -> None:
        width = allocation.width
        if width == self._last_listbox_width:
            return
        self._last_listbox_width = width
        # Defer — see _rewrap_idle_id. Multiple allocates during a drag all
        # schedule the same single idle source; _do_idle_rewrap reads the
        # current listbox width when it actually runs, so stale intermediate
        # widths are never applied.
        if self._rewrap_idle_id is None:
            self._rewrap_idle_id = GLib.idle_add(self._do_idle_rewrap)

    def _do_idle_rewrap(self) -> bool:
        self._rewrap_idle_id = None
        if not self._shutting_down:
            self._rewrap_prose_textviews(self._listbox)
        return False  # one-shot

    def _rewrap_prose_textviews(self, container: Gtk.Widget) -> None:
        """Re-clamp every already-rendered prose bubble to the new width —
        needed both for history loaded before the window's first
        size-allocate (get_allocated_width() was 0 at render time, so the
        fallback width was used) and for the user dragging the sidebar's
        paned divider after messages are already on screen.

        Reuses the original sizing text stored on the textview
        (grc_plain_for_size) rather than buffer.get_slice(): the slice
        carries a \uFFFC placeholder per pill badge, which Pango measures at
        ~1 char and would collapse a badge-heavy bubble by ~25px/badge on
        every resize."""
        for child in container.get_children():
            if getattr(child, "grc_is_prose", False):
                plain = getattr(child, "grc_plain_for_size", None)
                if plain is None:
                    buffer = child.get_buffer()
                    plain = buffer.get_slice(buffer.get_start_iter(), buffer.get_end_iter(), True)
                self._size_prose_textview_to_content(child, plain)
            elif isinstance(child, Gtk.Container):
                self._rewrap_prose_textviews(child)

    def _render_markdown_to_box(self, box: Gtk.Box, text: str, clear: bool = True) -> None:  # noqa: C901
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
                        tv = self._make_prose_textview()
                        buffer = tv.get_buffer()
                        self._ensure_buffer_tags(buffer)
                        self._insert_prose_text_with_badges(buffer, t, [], tv)
                        # Remember the sizing text so _rewrap_prose_textviews
                        # re-measures the SAME basis — buffer.get_slice() yields
                        # a \uFFFC placeholder per pill badge (~1 char to Pango),
                        # which would collapse a badge-heavy bubble on resize.
                        tv.grc_plain_for_size = t
                        self._size_prose_textview_to_content(tv, t)
                        box.pack_start(tv, False, False, 0)
                    continue

                tag = element.name
                if tag == "table":
                    table_str = self._format_table(element)

                    tv = self._make_prose_textview()
                    tv.set_wrap_mode(Gtk.WrapMode.NONE)
                    tv.get_style_context().add_class("chat-monospace")
                    buffer = tv.get_buffer()
                    self._ensure_buffer_tags(buffer)
                    self._insert_prose_text_with_badges(buffer, table_str, ["code"], tv)
                    tv.grc_plain_for_size = table_str
                    self._size_prose_textview_to_content(tv, table_str)

                    sw = Gtk.ScrolledWindow()
                    sw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
                    sw.add(tv)
                    sw.set_min_content_height(60)
                    sw.get_style_context().add_class("chat-code-block")

                    box.pack_start(sw, False, False, 0)
                elif tag == "pre":
                    code_text = element.get_text()
                    code_text_esc = _esc(code_text).replace(" ", "\u00A0")

                    code_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
                    code_box.get_style_context().add_class("chat-code-block")
                    code_box.set_margin_start(4)
                    code_box.set_margin_end(4)
                    code_box.set_margin_top(4)
                    code_box.set_margin_bottom(4)

                    header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
                    header_box.get_style_context().add_class("chat-code-header")

                    code_child = element.find("code")
                    lang = ""
                    if code_child and code_child.has_attr("class"):
                        classes = code_child["class"]
                        for c in classes:
                            if c.startswith("language-"):
                                lang = c[9:]
                                break

                    lang_lbl = Gtk.Label()
                    lang_lbl.set_xalign(0.0)
                    lang_lbl.get_style_context().add_class("dim-label")
                    if lang:
                        lang_lbl.set_text(lang)

                    copy_btn = Gtk.Button(label="Copy")
                    copy_btn.get_style_context().add_class("chat-copy-btn")
                    copy_btn.set_halign(Gtk.Align.END)
                    copy_btn.set_valign(Gtk.Align.CENTER)
                    copy_btn.set_tooltip_text("Copy code to clipboard")

                    def _on_copy_clicked(_btn: Gtk.Button, raw_text: str) -> None:
                        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
                        clipboard.set_text(raw_text, -1)
                        _btn.set_label("✓ Copied!")

                        def _reset():
                            _btn.set_label("Copy")
                            return False

                        GLib.timeout_add_seconds(2, _reset)

                    copy_btn.connect("clicked", _on_copy_clicked, code_text)

                    header_box.pack_start(lang_lbl, True, True, 4)
                    header_box.pack_end(copy_btn, False, False, 4)

                    lbl = Gtk.Label()
                    lbl.get_style_context().add_class("chat-monospace")
                    lbl.set_markup(f'<span face="monospace" size="small">{code_text_esc}</span>')
                    lbl.set_line_wrap(True)
                    lbl.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
                    lbl.set_xalign(0.0)
                    lbl.set_selectable(True)
                    lbl.set_margin_start(6)
                    lbl.set_margin_end(6)
                    lbl.set_margin_top(6)
                    lbl.set_margin_bottom(6)

                    code_box.pack_start(header_box, False, False, 0)
                    code_box.pack_start(lbl, True, True, 0)

                    box.pack_start(code_box, False, False, 0)
                else:
                    tv = self._make_prose_textview()
                    buffer = tv.get_buffer()
                    self._ensure_buffer_tags(buffer)
                    self._element_to_buffer(element, buffer, tv, active_tags=[])
                    # get_slice (not get_text) — get_text() excludes the
                    # U+FFFC child-anchor placeholder entirely, so a
                    # badge-only paragraph would otherwise look "empty" and
                    # get silently dropped.
                    content = buffer.get_slice(buffer.get_start_iter(), buffer.get_end_iter(), True).strip()
                    if content:
                        # Same sizing basis on rewrap — see comment above.
                        tv.grc_plain_for_size = element.get_text()
                        self._size_prose_textview_to_content(tv, element.get_text())
                        box.pack_start(tv, False, False, 0)

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

        # A re-render destroys any badge pill mid-hover without a
        # leave-notify-event (GTK3 doesn't synthesize one on widget removal),
        # which could leave a stale canvas highlight — same guard as
        # _render_history's full rebuild.
        cm = getattr(self._flowgraph_proxy, "_canvas_manager", None)
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
        for part in msg.parts:
            part_cls = part.__class__.__name__
            if isinstance(part, NativeToolReturnPart):
                continue
            if part_cls == "TextPart":
                self._render_markdown_to_box(box, part.content, clear=False)
                full_text += part.content
            elif part_cls == "ThinkingPart":
                exp, _tv = self._make_thinking_widget(part.content, label="Thinked")
                box.pack_start(exp, True, True, 0)
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
        # Force scroll on every new row (user message, agent bubble) so the
        # user always sees what was just added.
        # The _auto_scroll flag handles the "user scrolled up to read" case
        # during streaming — but for a new row, we always want to show it.
        self._scroll_to_bottom(force=True)

    def _make_tool_expander(self, tool_name: str) -> Gtk.Expander:
        exp = Gtk.Expander(label=f"\u2699 {tool_name} ...")
        exp.set_expanded(False)
        exp.get_style_context().add_class("chat-tool-expander")
        exp.set_hexpand(True)
        exp.set_halign(Gtk.Align.FILL)
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

    def notify_run_failure(self, return_code: int, log_text: str) -> None:  # noqa: ARG002
        """Called by exec_monitor when a flowgraph run fails. Sends a short
        notification to the agent so it can decide whether to investigate via
        ``get_run_log`` and propose a fix — replacing the old Yes/No bubble
        that injected the full log as a prompt.

        The full log is NOT injected here — the agent reads it on demand via
        the ``get_run_log`` tool (one source of truth, structured tool result
        instead of a prompt blob).
        """
        _log.info("notify_run_failure: code=%d, log=%d chars", return_code, len(log_text))
        origin_page = self.current_page
        prompt = (
            f"Flowgraph run failed (return code {return_code}). "
            "Use the get_run_log tool to read the console output and diagnose the error."
        )
        asyncio.ensure_future(self._send_fix_when_free(prompt, origin_page))

    async def _send_fix_when_free(self, text: str, origin_page: Any) -> None:
        """Wait out any in-flight agent turn, then send `text` as the next
        user message in the ORIGIN page's session — not whatever page happens
        to be current when the await returns.

        The await yields control to the gbulb loop, which can process a
        notebook ``switch-page`` in the meantime. Without the origin-page
        capture, the fix would silently dispatch against whatever page is
        current when the await returns, "fixing" the wrong flowgraph (H2).
        On a detected switch we surface a status message instead of acting
        on the wrong target — same one-rule shape as _run_agent_turn's
        ``origin_page`` guard.
        """
        if self._chat_task and not self._chat_task.done():
            await asyncio.gather(self._chat_task, return_exceptions=True)
        if self.current_page is not origin_page:
            self.set_status(
                "Auto-fix cancelled \u2014 you switched flowgraphs. Re-open the failed flowgraph and try again.",
                error=True,
            )
            return
        self.send_message(text)

    def _on_entry_key_press(self, entry: Gtk.Entry, event: Gdk.EventKey) -> bool:
        if event.keyval == Gdk.KEY_Escape:
            if entry.get_text():
                entry.set_text("")
            toplevel = entry.get_toplevel()
            if isinstance(toplevel, Gtk.Window):
                toplevel.set_focus(None)
            return True
        if event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            if event.state & Gdk.ModifierType.SHIFT_MASK:
                pos = entry.get_position()
                text = entry.get_text()
                new_text = text[:pos] + "\n" + text[pos:]
                entry.set_text(new_text)
                entry.set_position(pos + 1)
                return True
            else:
                self._dispatch_send()
                return True
        return False

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
        # Sending a message always re-engages auto-scroll — the user wants
        # to see their message and the agent's reply, even if they had
        # scrolled up to read earlier content.
        self._auto_scroll = True
        self._append_user_message(text)

        if self._active_session_id is None:
            path = None
            if self._flowgraph_proxy is not None:
                cm = getattr(self._flowgraph_proxy, "_canvas_manager", None)
                path = cm.path if cm else None
            if path:
                try:
                    # Save with the user prompt included inline — NOT by
                    # mutating _message_history. agent.iter(text, ...) below
                    # appends `text` to the canonical history itself; if we
                    # pre-loaded it into _message_history here, the success
                    # path's run.result.all_messages() would contain the
                    # prompt TWICE (once from our pre-load, once from
                    # pydantic-ai's own append) and _render_history() would
                    # display it twice. Keeping _message_history clean until
                    # the run completes avoids that duplication (M2 fix).
                    history_with_prompt = self._message_history + [
                        ModelRequest(parts=[UserPromptPart(content=text)])
                    ]
                    self._active_session_id = save_session(None, path, history_with_prompt)
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
            # happened. The eager save in send_message includes the prompt
            # inline (not in _message_history), so persist it into the
            # canonical history now via a fire-and-forget save (not awaited —
            # this task is already cancelling and must not suspend).
            if self.current_page is origin_page and self._clear_generation == origin_gen:
                self._remember_user_message(text)
                asyncio.ensure_future(self._save_history())
                self._append_error("[aborted]", style="aborted")
                rich_rendered = True
            raise
        except Exception as e:
            _log.exception("agent run failed")
            if self.current_page is origin_page:
                self._remember_user_message(text)
                await self._save_history()
                self._append_error(_format_turn_error(e))
                rich_rendered = True
        finally:
            # Paint any throttled-but-unflushed tail before deciding whether to
            # markdown-render, so an error/cancel mid-part never leaves the live
            # bubble stuck at a ~33ms-stale snapshot (the per-token throttle can
            # hold back the last chunk when the stream raises before a flush).
            # Skip during app shutdown to avoid widget ops on mid-destroy
            # widgets — the window's `destroy` signal fires _shutdown, which
            # sets _shutting_down before stop_chat() cancels this task (L7).
            if self._shutting_down:
                return  # noqa: B012
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
            self._send_btn.set_image(Gtk.Image.new_from_icon_name("media-playback-stop-symbolic", Gtk.IconSize.SMALL_TOOLBAR))
            self._send_btn.set_tooltip_text("Stop")
            self._send_btn.set_sensitive(True)
            self._entry.set_sensitive(False)
        else:
            self._send_btn.set_image(Gtk.Image.new_from_icon_name("media-playback-start-symbolic", Gtk.IconSize.SMALL_TOOLBAR))
            self._send_btn.set_tooltip_text("Send")
            self._entry.set_sensitive(can_type)
            self._update_send_sensitivity()
            if can_type:
                self._entry.grab_focus()

    def _on_user_scroll(self, _sw: Gtk.ScrolledWindow, event: Gdk.EventScroll) -> bool:
        """Track user scroll intent. If the user scrolls UP, stop auto-scrolling
        so they can read without being yanked. If they scroll back DOWN to near
        the bottom, resume auto-scroll. Returns False so the scroll event
        propagates normally."""
        direction = event.direction
        if direction == Gdk.ScrollDirection.UP:
            self._auto_scroll = False
        elif direction == Gdk.ScrollDirection.DOWN:
            adj = self._scrolled.get_vadjustment()
            near_bottom = (adj.get_upper() - adj.get_page_size() - adj.get_value()) <= _SCROLL_STICK_THRESHOLD
            if near_bottom:
                self._auto_scroll = True
        elif direction == Gdk.ScrollDirection.SMOOTH:
            # Touchpad smooth-scroll: delta_y < 0 = up, > 0 = down
            _, _, delta_y = event.get_scroll_deltas()
            if delta_y < 0:
                self._auto_scroll = False
            elif delta_y > 0:
                adj = self._scrolled.get_vadjustment()
                near_bottom = (adj.get_upper() - adj.get_page_size() - adj.get_value()) <= _SCROLL_STICK_THRESHOLD
                if near_bottom:
                    self._auto_scroll = True
        return False

    def _scroll_to_bottom(self, *, force: bool = False) -> None:
        def _do_scroll():
            sw = self._scrolled
            if sw is None:
                return False
            adj = sw.get_vadjustment()
            # Skip if the user scrolled up to read (unless explicitly forced,
            # e.g. after a full rebuild or message send). The _auto_scroll flag
            # is set by _on_user_scroll's scroll-event handler — not inferred
            # from the adjustment position, which death-spiraled during
            # streaming (content grew >80px between flushes → every subsequent
            # scroll was skipped → gap only grew).
            if not force and not self._auto_scroll:
                return False
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
        dlg.set_default_response(Gtk.ResponseType.APPLY)
        content = dlg.get_content_area()
        content.set_spacing(8)
        content.set_border_width(12)

        cfg = load_settings()
        grid = Gtk.Grid(column_spacing=8, row_spacing=8)

        # Section 1 Header
        hdr_pm = Gtk.Label()
        hdr_pm.set_markup("<b>Provider &amp; Model Configuration</b>")
        hdr_pm.set_xalign(0.0)
        grid.attach(hdr_pm, 0, 0, 2, 1)

        # Provider Row
        lbl_p = Gtk.Label(label="Provider:")
        lbl_p.set_xalign(0.0)
        lbl_p.set_tooltip_text("Select local Ollama daemon or a cloud provider (OpenRouter / Ollama Cloud)")
        provider_combo = Gtk.ComboBoxText()
        provider_combo.set_tooltip_text(
            "Select your AI model provider:\n"
            "• Ollama (local) — Local or custom Ollama daemon\n"
            "• OpenRouter (cloud) — OpenRouter cloud API\n"
            "• Ollama Cloud (cloud) — Remote Ollama cloud API"
        )
        for p in _PROVIDER_ORDER:
            provider_combo.append_text(_PROVIDER_LABELS[p])
        provider_combo.set_active(_PROVIDER_ORDER.index(cfg["provider"]))
        grid.attach(lbl_p, 0, 1, 1, 1)
        grid.attach(provider_combo, 1, 1, 1, 1)

        # Model Row
        lbl_m = Gtk.Label(label="Model:")
        lbl_m.set_xalign(0.0)
        lbl_m.set_tooltip_text("The specific LLM model ID or tag for chat responses")
        model_entry = Gtk.Entry()
        model_entry.set_text(cfg["model"])
        model_entry.set_hexpand(True)
        model_entry.set_activates_default(True)
        model_entry.set_tooltip_text(
            "Enter model ID or tag for chat.\n"
            "Examples:\n"
            "• Local Ollama: qwen3.6:35b-a3b-q4_K_M\n"
            "• OpenRouter: deepseek/deepseek-v4-flash\n"
            "• Ollama Cloud: deepseek-v4-flash:cloud"
        )
        grid.attach(lbl_m, 0, 2, 1, 1)
        grid.attach(model_entry, 1, 2, 1, 1)

        # API Key Row
        lbl_k = Gtk.Label(label="API Key:")
        lbl_k.set_xalign(0.0)
        lbl_k.set_tooltip_text("Authentication key required for OpenRouter or Ollama Cloud")
        key_entry = Gtk.Entry()
        key_entry.set_visibility(False)
        key_entry.set_activates_default(True)
        key_entry.set_tooltip_text("API key for the selected cloud provider. Not required for local Ollama.")
        grid.attach(lbl_k, 0, 3, 1, 1)
        grid.attach(key_entry, 1, 3, 1, 1)

        # Separator 1
        sep1 = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        grid.attach(sep1, 0, 4, 2, 1)

        # Section 2 Header
        hdr_ol = Gtk.Label()
        hdr_ol.set_markup("<b>Ollama &amp; Model Execution Options</b>")
        hdr_ol.set_xalign(0.0)
        grid.attach(hdr_ol, 0, 5, 2, 1)

        # Ollama Base URL Row
        lbl_u = Gtk.Label(label="Ollama Base URL:")
        lbl_u.set_xalign(0.0)
        lbl_u.set_tooltip_text("Base URL endpoint for the Ollama daemon (default http://localhost:11434)")
        ollama_url_entry = Gtk.Entry()
        ollama_url_entry.set_text(cfg.get("ollama_base_url", "http://localhost:11434"))
        ollama_url_entry.set_hexpand(True)
        ollama_url_entry.set_activates_default(True)
        ollama_url_entry.set_tooltip_text("Base URL for the Ollama daemon (e.g. http://localhost:11434 or http://192.168.1.100:11434)")
        grid.attach(lbl_u, 0, 6, 1, 1)
        grid.attach(ollama_url_entry, 1, 6, 1, 1)

        # Model Reasoning Checkbox Row
        lbl_t = Gtk.Label(label="Model Reasoning:")
        lbl_t.set_xalign(0.0)
        lbl_t.set_tooltip_text("Enable or disable model thinking output (think: true/false)")
        thinking_check = Gtk.CheckButton(label="Enable reasoning / thinking tags (think: true)")
        thinking_check.set_active(cfg.get("ollama_thinking_enabled", True))
        thinking_check.set_tooltip_text("Controls whether model reasoning is enabled (think: True/False) for supported Ollama models.")
        grid.attach(lbl_t, 0, 7, 1, 1)
        grid.attach(thinking_check, 1, 7, 1, 1)

        # Separator 2
        sep2 = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        grid.attach(sep2, 0, 8, 2, 1)

        info = Gtk.Label(label="Changes apply immediately on Save.")
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

            if p == "ollama":
                lbl_u.set_text("Ollama Base URL:")
                ollama_url_entry.set_text(cfg.get("ollama_base_url", "http://localhost:11434"))
                ollama_url_entry.set_sensitive(True)
                thinking_check.set_sensitive(True)
            elif p == "openai_compatible":
                lbl_u.set_text("Base URL:")
                ollama_url_entry.set_text(cfg.get("openai_compatible_base_url", "http://localhost:8080/v1"))
                ollama_url_entry.set_sensitive(True)
                thinking_check.set_sensitive(False)
            elif p == "ollama_cloud":
                lbl_u.set_text("Ollama Base URL:")
                ollama_url_entry.set_sensitive(False)
                thinking_check.set_sensitive(True)
            else:
                lbl_u.set_text("Base URL:")
                ollama_url_entry.set_sensitive(False)
                thinking_check.set_sensitive(False)

        provider_combo.connect("changed", _sync_provider_fields)
        _sync_provider_fields(provider_combo)

        content.pack_start(grid, False, False, 0)
        content.pack_start(info, False, False, 0)
        content.show_all()

        self._open_dialog = dlg

        def _on_response(_dlg: Gtk.Dialog, response: int) -> None:
            if response == Gtk.ResponseType.APPLY:
                idx = provider_combo.get_active()
                provider = _PROVIDER_ORDER[idx] if idx >= 0 else "ollama"
                model = model_entry.get_text().strip()
                key_var = _PROVIDER_API_KEY.get(provider)
                key_val = key_entry.get_text().strip()
                ollama_url_val = ollama_url_entry.get_text().strip()
                thinking_val = thinking_check.get_active()
            else:
                provider = model = key_var = key_val = ollama_url_val = thinking_val = None
            self._open_dialog = None
            dlg.destroy()
            if response != Gtk.ResponseType.APPLY:
                return
            if not model:
                self.set_status("Settings not saved — model name is required.", error=True)
                return
            self._apply_settings_save(
                provider, model, key_var, key_val, ollama_base_url=ollama_url_val, thinking_enabled=thinking_val, toplevel=toplevel
            )

        dlg.connect("response", _on_response)
        dlg.show()

    def _apply_settings_save(
        self,
        provider: str,
        model: str,
        key_var: str | None,
        key_val: str,
        ollama_base_url: str = "http://localhost:11434",
        thinking_enabled: bool = True,
        toplevel: Gtk.Window | None = None,
    ) -> None:
        """Post-Save flow: preflight → persist → live-swap.

        Preflight and persist run synchronously (bounded at 5s, acceptable for
        a user-initiated action) so tests can assert on the persisted state
        immediately. Only the agent rebuild + set_agent runs async via
        ``asyncio.ensure_future`` — it's pure computation with no I/O the test
        needs to observe.
        """
        from .agent_factory import preflight_connection

        provider_label = _PROVIDER_LABELS.get(provider, provider)

        # 1. Pre-flight reachability BEFORE writing to .env (no save/restore
        #    dance if it fails). Bounded at 5s inside preflight_connection.
        self.set_status(f"Checking {provider_label}\u2026")
        preflight_err = preflight_connection(provider, key_val, ollama_base_url=ollama_base_url)
        if preflight_err and not self._confirm_unreachable(
            provider, preflight_err, toplevel, ollama_base_url=ollama_base_url
        ):
            self.set_status("Settings not saved — provider unreachable.", error=True)
            return

        # 2. Persist to .env synchronously — tests assert on load_settings()
        #    immediately after emitting the response signal.
        try:
            if provider == "openai_compatible":
                save_settings(
                    provider, model, openai_compatible_base_url=ollama_base_url, thinking_enabled=thinking_enabled
                )
            else:
                save_settings(
                    provider, model, ollama_base_url=ollama_base_url, thinking_enabled=thinking_enabled
                )
            if key_var:
                upsert_env_key(key_var, key_val)
        except Exception as e:
            _log.exception("Failed to save settings")
            self.set_status(f"Settings not saved ({e}).", error=True)
            return

        # 3. Live-swap the running Agent in-place. Dispatched async so the
        #    gbulb loop stays responsive during model construction (which
        #    spins up an httpx client and pydantic-ai Agent). The history is
        #    kept verbatim — ModelMessage objects are provider-agnostic.
        if self._rebuild_agent is None:
            self.set_status("Settings saved. Restart to apply.")
            return
        try:
            new_agent, model_err = self._rebuild_agent()
        except Exception as e:
            _log.exception("Live-swap rebuild failed")
            self.set_status(f"Settings saved but live-swap failed: {e}", error=True)
            return
        self.set_agent(new_agent)
        if model_err:
            self.set_status(
                f"Switched with warning ({model_err}). Running on defaults.",
                error=True,
            )
        else:
            self.set_status(f"Switched to {provider_label} \u00b7 {model}.")

    def _confirm_unreachable(
        self,
        provider: str,
        err: str,
        toplevel: Gtk.Window | None,
        *,
        ollama_base_url: str = "http://localhost:11434",
    ) -> bool:
        """Modal Yes/No confirm when the preflight ping fails. Returns True
        if the user wants to save anyway. Anchors the dialog on `self` so
        PyGObject doesn't GC it mid-`.run()`."""
        provider_label = _PROVIDER_LABELS.get(provider, provider)
        if provider == "ollama":
            hint = f"• Ensure local Ollama daemon is running ('ollama serve').\n• Verify host is reachable at {ollama_base_url}."
        elif provider == "openai_compatible":
            hint = f"• Ensure local OpenAI-compatible server (e.g. llama-server) is running.\n• Verify endpoint is reachable at {ollama_base_url}."
        elif provider == "openrouter":
            hint = "• Check network connectivity to openrouter.ai.\n• Verify your OPENROUTER_API_KEY in Preferences."
        elif provider == "ollama_cloud":
            hint = "• Check network connectivity to ollama.com.\n• Verify your OLLAMA_CLOUD_API_KEY in Preferences."
        else:
            hint = "• Check provider configuration and network connectivity."

        confirm = Gtk.MessageDialog(
            transient_for=toplevel,
            flags=Gtk.DialogFlags.MODAL,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.YES_NO,
            text=f"Cannot reach {provider_label}",
        )
        confirm.format_secondary_text(
            f"Preflight check error: {err}\n\n"
            f"Actionable hints:\n{hint}\n\n"
            f"Save anyway? The agent will retry when you send a message."
        )
        self._open_dialog = confirm
        keep = confirm.run() == Gtk.ResponseType.YES
        self._open_dialog = None
        confirm.destroy()
        return keep
