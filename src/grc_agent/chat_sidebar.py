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
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
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
    load_session,
    save_session,
)
from .settings import (
    load_settings,
    save_settings,
    upsert_env_key,
)
from .trace import (
    TraceRecorder,
    delete_turn_trace,
    save_turn_trace,
)
from .ui.block_badge import (
    BlockBadge,
    badge_click,
    badge_enter,
    badge_leave,
)
from .ui.css import apply_css as _apply_css
from .ui.markdown_view import MarkdownView
from .ui.providers import (
    PROVIDER_BADGE_LABEL as _PROVIDER_BADGE_LABEL,
)
from .ui.providers import (
    PROVIDER_LABELS as _PROVIDER_LABELS,
)
from .ui.providers import (
    resolve_provider_from_base_url as _resolve_provider_from_base_url,
)
from .ui.settings_dialog import SettingsDialog
from .ui.welcome_view import WelcomeView

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
# Failed/unresolvable probes are negative-cached with a TTL so a down backend
# doesn't re-block the main loop (each probe is a sync HTTP call) on every
# label update during a turn. Entries age out so a recovered backend is re-tried.
_context_negative_cache: dict[tuple[str, str], float] = {}
_CONTEXT_NEGATIVE_TTL = 60.0


def format_tokens(n: int) -> str:
    """Format token count for display (e.g. 1.2k, 14.7k, 128k, 1M)."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M".replace(".0M", "M")
    if n >= 1_000:
        return f"{n / 1_000:.1f}k".replace(".0k", "k")
    return str(n)


def _ollama_context_length(provider: str, model: str) -> int | None:
    """POST {base_url}/api/show -> model_info context_length, falling back to
    parsing num_ctx from the parameters blob. Returns None if unresolvable."""
    import httpx

    from grc_agent.settings import get_env_value

    base_url = (
        "https://ollama.com"
        if provider == "ollama_cloud"
        else (get_env_value("OLLAMA_BASE_URL") or "http://localhost:11434").rstrip("/")
    )
    api_key = get_env_value("OLLAMA_CLOUD_API_KEY") if provider == "ollama_cloud" else ""
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    with httpx.Client(timeout=3.0) as client:
        r = client.post(f"{base_url}/api/show", json={"name": model}, headers=headers)
    if r.status_code != 200:
        return None
    data = r.json()
    for k, v in data.get("model_info", {}).items():
        if "context_length" in k and isinstance(v, (int, float)):
            return int(v)
    for line in str(data.get("parameters", "")).splitlines():
        if "num_ctx" in line:
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                return int(parts[1])
    return None


def _openrouter_context_length(model: str) -> int | None:
    """GET https://openrouter.ai/api/v1/models -> context_length for the model."""
    import httpx

    from grc_agent.settings import get_env_value

    api_key = get_env_value("OPENROUTER_API_KEY") or ""
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    with httpx.Client(timeout=3.0) as client:
        r = client.get("https://openrouter.ai/api/v1/models", headers=headers)
    if r.status_code != 200:
        return None
    for m in r.json().get("data", []):
        m_id = m.get("id", "")
        if m_id == model or m_id.endswith(model):
            ctx_len = m.get("context_length")
            if isinstance(ctx_len, (int, float)):
                return int(ctx_len)
    return None


def resolve_model_context_length(provider: str, model: str) -> int | None:
    """Dynamically query the active provider's API for the model's exact context
    length. Cached in-memory per (provider, model) pair; returns None if
    unresolvable, so callers render exact token counts without hardcoded guesses.
    """
    key = (provider or "", model or "")
    if key in _context_length_cache:
        return _context_length_cache[key]
    if not provider or not model:
        return None
    neg_at = _context_negative_cache.get(key)
    if neg_at is not None and time.monotonic() - neg_at < _CONTEXT_NEGATIVE_TTL:
        return None

    try:
        if provider in ("ollama", "ollama_cloud"):
            ctx_len = _ollama_context_length(provider, model)
        elif provider == "openai_codex":
            from grc_agent.providers.openai_codex.model import context_window

            ctx_len = context_window(model)
        elif provider in ("openai_compatible", "openrouter"):
            # Was gated on "openrouter" alone, which load_settings() has not
            # been able to return since the backends were consolidated — so
            # every OpenAI-compatible endpoint fell through to None and the
            # label showed a bare token count with nothing to compare against.
            ctx_len = _openrouter_context_length(model)
        else:
            ctx_len = None
    except Exception as e:
        _log.debug(
            "Failed to resolve dynamic context length for provider=%s model=%s: %s",
            provider,
            model,
            e,
        )
        _context_negative_cache[key] = time.monotonic()
        return None

    if ctx_len is None:
        _context_negative_cache[key] = time.monotonic()
        return None
    _context_length_cache[key] = ctx_len
    return ctx_len


def _format_turn_error(e: Exception) -> str:
    """User-facing message for a failed agent turn (_run_agent_turn's catch-all).
    Exposes exact status codes, provider error message details, and underlying causes.
    """
    cause_str = ""
    if hasattr(e, "__cause__") and e.__cause__:
        cause = e.__cause__
        cause_msg = str(cause)
        if hasattr(cause, "body") and cause.body:
            cause_msg = str(cause.body)
        if cause_msg and cause_msg != str(e):
            cause_str = f" (Cause: {cause_msg})"

    if isinstance(e, ModelHTTPError):
        msg = f"Model HTTP {e.status_code} Error"
        if e.body:
            body_detail = ""
            if isinstance(e.body, dict):
                body_detail = (
                    e.body.get("message") or e.body.get("error", {}).get("message") or str(e.body)
                )
            else:
                body_detail = str(e.body)
            return f"{msg}: {body_detail}{cause_str}"
        model_name = getattr(e, "model_name", "model")
        return f"{msg} from {model_name}{cause_str}"

    if isinstance(e, ModelAPIError):
        return f"Model API Error: {e}{cause_str}"

    if isinstance(e, UsageLimitExceeded):
        return f"Usage Limit Exceeded: {e}{cause_str}"

    if isinstance(e, UnexpectedModelBehavior):
        return f"Unexpected Model Behavior: {e}{cause_str}"

    return f"Agent Error: {e}{cause_str}"


def _clean_message_history_for_new_turn(
    messages: list[ModelMessage],
) -> list[ModelMessage]:
    """Ensure message_history is valid for a new user prompt.

    PydanticAI rejects any run whose message_history ends on a ModelResponse
    with unfulfilled tool_calls (raising UserError: "Cannot provide a new user
    prompt when the message history contains unprocessed tool calls.").

    If an earlier turn aborted, hit max retries, or was persisted with
    trailing unprocessed tool calls, pop trailing ModelResponse messages with
    tool_calls so the next turn can start cleanly.
    """
    cleaned = list(messages)
    while cleaned:
        last = cleaned[-1]
        if isinstance(last, ModelResponse) and last.tool_calls:
            cleaned.pop()
            continue
        break
    return cleaned


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
        "think_flushed",
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
        # Chars of think_acc already appended to the buffer. Flushing appends
        # only the delta beyond this (never replaces the buffer), so the
        # thinking ScrolledWindow keeps the user's scroll position while
        # streaming — a full buffer replacement would snap it back to the top.
        self.think_flushed = 0
        self.think_dirty = False
        self.tools: dict[str, Gtk.Expander] = {}
        self.full_raw_text = ""
        self.last_flush = 0.0


class _ChatTextView(Gtk.ScrolledWindow):
    """Multi-line text input widget using GTK3 TextView and ScrolledWindow."""

    def __init__(self) -> None:
        super().__init__()
        self.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.set_shadow_type(Gtk.ShadowType.NONE)
        self.set_min_content_height(36)
        self.set_max_content_height(120)
        self.set_propagate_natural_height(True)
        self.set_hexpand(True)
        self.get_style_context().add_class("chat-entry-frame")

        self.tv = Gtk.TextView()
        self.tv.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.tv.set_hexpand(True)
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
        if detailed_signal == "activate":
            return 0
        return self.tv.connect(detailed_signal, handler, *args)


def _collect_token_usage(msgs) -> tuple[int, int, int, int]:
    """Extract (last_input, last_output, last_reasoning, total_session) tokens
    from pydantic-ai ModelResponse.usage objects."""
    last_input = last_output = last_reasoning = total = 0
    for msg in msgs:
        if msg.__class__.__name__ != "ModelResponse" or not hasattr(msg, "usage") or not msg.usage:
            continue
        u = msg.usage
        inp = getattr(u, "input_tokens", 0) or 0
        out = getattr(u, "output_tokens", 0) or 0
        if inp:
            last_input = inp
            last_output = out
            reasoning = 0
            if hasattr(u, "details") and isinstance(u.details, dict):
                reasoning = u.details.get("reasoning_tokens", 0) or 0
            elif hasattr(u, "reasoning_tokens"):
                reasoning = getattr(u, "reasoning_tokens", 0) or 0
            last_reasoning = reasoning
        total += getattr(u, "total_tokens", 0) or 0
    return last_input, last_output, last_reasoning, total


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
        self._active_base_url: str | None = None
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
        # MarkdownView (created in __init__ after the message list exists) owns
        # the badge-regex cache, the prose width/rewrap state, and the listbox
        # size-allocate connection.
        self._md: MarkdownView | None = None
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
        self._blocks_arrow = Gtk.Image.new_from_icon_name(
            "pan-end-symbolic", Gtk.IconSize.SMALL_TOOLBAR
        )
        self._blocks_toggle.set_valign(Gtk.Align.FILL)
        self._blocks_arrow = Gtk.Image.new_from_icon_name(
            "pan-end-symbolic", Gtk.IconSize.SMALL_TOOLBAR
        )
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
        self._md = MarkdownView(self._listbox, self._get_cm)
        self._welcome = WelcomeView(
            self._listbox,
            self._send_quick_prompt,
            self._on_recent_session_clicked,
            self._on_delete_recent_session,
        )
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
        if (event.state & Gdk.ModifierType.CONTROL_MASK) and event.keyval in (
            Gdk.KEY_comma,
            Gdk.KEY_Comma,
        ):
            self._open_settings()
            return True
        return False

    def _build_toolbar(self, content: Gtk.Box) -> None:
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        bar.set_border_width(4)

        def _icon_btn(
            icon_name: str, tooltip: str, signal: str | None = None, cb=None
        ) -> Gtk.Button:
            b = Gtk.Button.new_from_icon_name(icon_name, Gtk.IconSize.SMALL_TOOLBAR)
            b.set_tooltip_text(tooltip)
            b.get_style_context().add_class("chat-toolbar-btn")
            if signal:
                b.connect("clicked", lambda *_: self.emit(signal))
            if cb:
                b.connect("clicked", cb)
            bar.pack_start(b, False, False, 0)
            return b

        self._new_session_btn = _icon_btn(
            "document-new-symbolic", "New chat session", "new-session-clicked"
        )
        self._clear_hist_btn = _icon_btn(
            "edit-clear-all-symbolic",
            "Clear conversation history",
            cb=self._on_clear_history_clicked,
        )

        # Active graph badge — expands to fill the toolbar's leftover space.
        self._graph_label = Gtk.Label(label="Active Graph: none")
        self._graph_label.set_ellipsize(Pango.EllipsizeMode.END)
        self._graph_label.set_max_width_chars(15)
        self.set_active_graph(None)
        bar.pack_start(self._graph_label, True, True, 4)

        # Active provider badge — reflects the *running* agent's actual
        # provider/model, not the saved .env (which can diverge after a
        # Settings save until a live-swap or restart). Updated by
        # set_active_provider on startup and after every live-swap.
        self._provider_label = Gtk.Label(label="")
        self._provider_label.set_tooltip_text(
            "The provider/model the running chat agent is using right now. "
            "Settings changes apply immediately on Save."
        )
        # Non-expanding + ellipsize START so the model id (the useful tail) stays
        # visible while the graph badge gets the toolbar's variable space.
        self._provider_label.set_ellipsize(Pango.EllipsizeMode.START)
        self._provider_label.set_max_width_chars(14)
        bar.pack_start(self._provider_label, False, False, 0)

        # Settings
        self._gear_btn = _icon_btn(
            "preferences-system-symbolic",
            "Preferences (Ctrl+,)",
            cb=lambda *_: self._open_settings(),
        )

        bar.get_style_context().add_class("chat-toolbar")
        content.pack_start(bar, False, False, 0)

    def _build_message_list(self, content: Gtk.Box) -> None:
        self._listbox = Gtk.ListBox()
        self._listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self._listbox.set_activate_on_single_click(False)
        self._listbox.set_border_width(4)

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

        self._entry = _ChatTextView()
        self._entry.set_placeholder_text("Open a flowgraph in GRC to start chatting...")
        self._entry.set_hexpand(True)
        self._entry.connect("key-press-event", self._on_entry_key_press)
        self._entry.connect("changed", lambda *_: self._update_send_sensitivity())
        self._entry.set_sensitive(False)

        self._send_btn = Gtk.Button.new_from_icon_name(
            "media-playback-start-symbolic", Gtk.IconSize.SMALL_TOOLBAR
        )
        self._send_btn.set_tooltip_text("Send message (Enter, Shift+Enter for newline)")
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
        msgs = (
            self._active_run.all_messages()
            if getattr(self, "_active_run", None)
            else self._message_history
        )
        (
            last_input_tokens,
            last_output_tokens,
            last_reasoning_tokens,
            total_session_tokens,
        ) = _collect_token_usage(msgs)

        active_provider = getattr(self, "_active_provider", "") or ""
        active_model = getattr(self, "_active_model", "") or ""
        max_context = resolve_model_context_length(active_provider, active_model)

        pct: float | None = None
        if not msgs or last_input_tokens == 0:
            if max_context:
                text = f"<span size='small'>Context: 0 / {format_tokens(max_context)} tokens</span>"
            else:
                text = "<span size='small'>Context: 0 tokens</span>"
        else:
            if max_context:
                pct = min(100.0, (last_input_tokens / max_context) * 100)
                text = (
                    f"<span size='small'>"
                    f"Context: {format_tokens(last_input_tokens)} / {format_tokens(max_context)} tokens ({pct:.0f}%)"
                    f"</span>"
                )
            else:
                text = (
                    f"<span size='small'>Context: {format_tokens(last_input_tokens)} tokens</span>"
                )

        if hasattr(self, "_context_label"):
            # Escalation ramp via CSS classes (ui/css.py): quiet at 0-74%,
            # bold at 75-89%, theme accent at >=90%. No hardcoded colors.
            ctx_classes = self._context_label.get_style_context()
            ctx_classes.remove_class("warn")
            ctx_classes.remove_class("alarm")
            if pct is not None:
                if pct >= 90:
                    ctx_classes.add_class("alarm")
                elif pct >= 75:
                    ctx_classes.add_class("warn")
            self._context_label.set_markup(text)
            reasoning_str = (
                f" ({last_reasoning_tokens:,} reasoning)" if last_reasoning_tokens else ""
            )
            self._context_label.set_tooltip_text(
                f"Active model: {active_model or 'default'}\n"
                f"Provider: {active_provider or 'unknown'}\n"
                f"Last turn input context: {last_input_tokens:,} tokens\n"
                f"Last turn output: {last_output_tokens:,} tokens{reasoning_str}\n"
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
            self._status_label.get_style_context().add_class("validation-invalid")
        else:
            self._status_label.get_style_context().remove_class("validation-invalid")

    def set_active_graph(self, name: str | None, path: str | None = None) -> None:
        self._active_graph_name = name
        self._active_graph_path = path
        self._graph_label.set_text(f"Active Graph: {name}" if name else "Active Graph: none")
        if name and path:
            self._graph_label.set_tooltip_text(f"Active Flowgraph: {name}\nFull Path: {path}")
        elif name:
            self._graph_label.set_tooltip_text(
                f"Active Flowgraph: {name}\nFull Path: (Unsaved / In-memory)"
            )
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
            transient_for=self.get_toplevel()
            if isinstance(self.get_toplevel(), Gtk.Window)
            else None,
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
        # whitespace-only text is a silent no-op (see _dispatch_send).
        self._send_btn.set_sensitive(
            self._entry.get_sensitive() and bool(self._entry.get_text().strip())
        )

    def set_blocks_expanded(self, expanded: bool) -> None:
        self._blocks_expanded = expanded
        icon = "pan-start-symbolic" if expanded else "pan-end-symbolic"
        self._blocks_arrow.set_from_icon_name(icon, Gtk.IconSize.SMALL_TOOLBAR)
        self._blocks_toggle.set_tooltip_text(
            "Hide block library" if expanded else "Show block library"
        )

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
        self.set_active_provider(
            resolved_provider, model_name, is_default=is_default, base_url=base_url
        )

    def set_rebuild_agent_callback(self, cb: Callable[[], tuple[Agent, str | None]]) -> None:
        """Wire the live-swap entry point. desktop_app.py calls this once at
        startup with a closure over `build_agent_from_cfg(load_settings())`;
        the Settings dialog invokes it after a successful Save to apply the
        new provider/model/key to the running process immediately."""
        self._rebuild_agent = cb

    def set_active_provider(
        self, provider: str, model: str, *, is_default: bool = False, base_url: str | None = None
    ) -> None:
        """Update the toolbar's active-provider badge and rich tooltip.
        `is_default` is True when the running agent's resolved provider doesn't
        match the saved cfg (e.g. a startup build failure fell back to local
        Ollama); it is surfaced in the tooltip's Status line."""
        self._active_provider = provider
        self._active_model = model
        self._active_base_url = base_url
        if not provider:
            self._provider_label.set_text("")
            self._provider_label.hide()
            return
        short_model = model.rsplit("/", 1)[-1]
        badge_label = _PROVIDER_BADGE_LABEL.get(provider, provider)
        self._provider_label.set_text(f"{badge_label} \u00b7 {short_model}")

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
        """Delegates to WelcomeView (welcome card + recent sessions)."""
        self._welcome.render(self.current_page, self._active_session_id)

    def _send_quick_prompt(self, text: str) -> None:
        if self._busy or self.current_page is None:
            return
        self.grab_entry_focus()
        self.send_message(text)

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
        self._message_history = deserialize_messages(session_data["messages"])
        self._render_history()

        self._loading_session_id = session_id
        try:
            self._switch_or_open_file(path)
        finally:
            self._loading_session_id = None

    def _switch_or_open_file(self, path: str) -> None:
        cm = (
            getattr(self._flowgraph_proxy, "_canvas_manager", None)
            if self._flowgraph_proxy
            else None
        )
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

    def _get_effective_path(self) -> str | None:
        if self._flowgraph_proxy is None:
            return None
        cm = getattr(self._flowgraph_proxy, "_canvas_manager", None)
        if cm is None:
            return None
        if cm.path:
            return cm.path
        page_title = getattr(cm, "page_title", "untitled.grc") or "untitled.grc"
        return f"untitled:{page_title}"

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
                delete_session(new_id)
            except Exception:
                _log.exception("Failed to remove session resurrected by in-flight save")

    async def _save_trace(self, row: dict[str, Any]) -> None:
        """Persist one finalized turn-trace row. Mirrors ``_save_history``'s
        pattern: the SQLite insert runs on a worker thread (``asyncio.to_thread``)
        on the WAL-enabled DB, and the same clear-generation guard prevents a
        late insert from resurrecting a trace row for a session the user just
        cleared. (The ON DELETE CASCADE on ``turn_traces.session_id`` already
        handles the case where the session ROW is gone; this guard covers the
        case where the chat UI was cleared via ``clear_messages`` without
        removing the underlying session row.)"""
        if row.get("session_id") is None:
            return
        gen = self._clear_generation
        try:
            new_id = await asyncio.to_thread(save_turn_trace, row)
        except Exception as e:
            _log.error("Failed to save turn trace to database: %s", e)
            return
        if new_id is not None and gen != self._clear_generation:
            try:
                delete_turn_trace(new_id)
            except Exception:
                _log.exception("Failed to remove trace resurrected by in-flight save")

    def stop_chat(self) -> None:
        if self._chat_task and not self._chat_task.done():
            self._chat_task.cancel()

    def shutting_down(self) -> None:
        """Signal that the app is shutting down — any in-flight widget cleanup
        (streaming flush, scroll-to-bottom, busy reset) should be skipped to
        avoid GTK warnings/crashes on mid-destroy widgets (L7)."""
        self._shutting_down = True
        if self._md is not None:
            self._md.set_shutting_down(True)

    async def _stream_request(
        self, ctx: _StreamCtx, node, run, recorder: TraceRecorder | None = None
    ) -> None:
        async with node.stream(run.ctx) as stream:
            async for event in stream:
                if isinstance(event, PartStartEvent):
                    self._on_part_start(ctx, event)
                elif isinstance(event, PartDeltaEvent):
                    self._on_part_delta(ctx, event)
                if recorder is not None:
                    recorder.on_event(event)
        # Force a final flush so the last throttled chunk is painted before the
        # node hands control back (and before any markdown re-render).
        self._flush_streaming(ctx, force=True)

    async def _stream_tools(
        self, ctx: _StreamCtx, node, run, recorder: TraceRecorder | None = None
    ) -> None:
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
                # Feed the recorder AFTER the GTK handlers — best-effort capture
                # that never blocks a UI update, and never gets skipped by a
                # GTK-handler exception (same ordering as _stream_request).
                if recorder is not None:
                    recorder.on_event(event)

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
            # A new part replaces the previous one's content: reset the delta
            # watermark and clear the buffer so only this part is shown.
            ctx.think_flushed = 0
            ctx.think_body.get_buffer().set_text("")
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
            self._flush_thinking(ctx)
            flushed = True
        if flushed or force:
            ctx.last_flush = now
            if force:
                self._update_copy_text(ctx.box, ctx.full_raw_text)
            if flushed:
                self._scroll_to_bottom()

    def _flush_thinking(self, ctx: _StreamCtx) -> None:
        """Append only the thinking delta since the last flush — replacing the
        whole buffer would reset the thinking ScrolledWindow to its initial
        scroll position, yanking a user who scrolled to read."""
        buffer = ctx.think_body.get_buffer()
        delta = ctx.think_acc[ctx.think_flushed :]
        if delta:
            buffer.insert(buffer.get_end_iter(), delta)
        ctx.think_flushed = len(ctx.think_acc)
        ctx.think_dirty = False

    def _close_text(self, ctx: _StreamCtx) -> None:
        self._flush_streaming(ctx, force=True)
        ctx.text_lbl = None
        ctx.text_acc = ""
        ctx.text_dirty = False

    def _close_thinking(self, ctx: _StreamCtx) -> None:
        self._flush_streaming(ctx, force=True)
        if ctx.think_expander is not None:
            ctx.think_expander.set_label("Thought")
        ctx.think_body = None
        ctx.think_expander = None
        ctx.think_acc = ""
        ctx.think_flushed = 0
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

        def _on_expander_toggled(expander: Gtk.Expander, _pspec: Any) -> None:
            if expander.get_expanded():
                self._auto_scroll = False

        exp.connect("notify::expanded", _on_expander_toggled)

        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sw.set_shadow_type(Gtk.ShadowType.NONE)
        sw.set_min_content_height(120)
        sw.set_max_content_height(500)
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
        box.set_hexpand(True)

        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        hbox.set_hexpand(True)
        hbox.set_halign(Gtk.Align.FILL)
        hbox.pack_start(box, True, True, 0)

        copy_btn = Gtk.Button()
        copy_btn.set_relief(Gtk.ReliefStyle.NONE)
        copy_btn.set_focus_on_click(False)
        copy_btn.set_valign(Gtk.Align.START)
        img = Gtk.Image.new_from_icon_name("edit-copy-symbolic", Gtk.IconSize.MENU)
        copy_btn.set_image(img)
        copy_btn.set_tooltip_text("Copy message")

        copy_btn._grc_copy_text = ""
        copy_btn.connect(
            "clicked", lambda b: self._copy_to_clipboard(getattr(b, "_grc_copy_text", ""), b)
        )

        hbox.pack_start(copy_btn, False, False, 0)
        hbox._grc_copy_btn = copy_btn

        self._add_message_row(hbox)
        return box

    def _get_cm(self):
        """Resolve the live canvas manager (it changes across tab switches)."""
        return (
            getattr(self._flowgraph_proxy, "_canvas_manager", None)
            if self._flowgraph_proxy
            else None
        )

    def _compile_badge_regex(self) -> re.Pattern | None:
        """Delegates to the MarkdownView (cached, whole-word over live block names)."""
        return self._md.compile_badge_regex()

    def _on_badge_enter(self, _widget, _event, name: str) -> bool:
        badge_enter(self._get_cm(), name)
        return False

    def _on_badge_leave(self, _widget, _event, _name: str) -> bool:
        badge_leave(self._get_cm())
        return False

    def _on_badge_click(self, _widget, event, name: str) -> bool:
        return badge_click(self._get_cm(), event, name)

    def _make_block_badge_widget(self, name: str) -> Gtk.EventBox:
        return BlockBadge(name, self._get_cm)

    def _render_markdown_to_box(self, box: Gtk.Box, text: str, clear: bool = True) -> None:
        """Render markdown into ``box``. Delegates to the MarkdownView."""
        self._md.render(box, text, clear)

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
                exp, _tv = self._make_thinking_widget(part.content, label="Thought")
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
                                if (
                                    p.__class__.__name__ == "ToolReturnPart"
                                    and p.tool_call_id == tcid
                                ):
                                    ret_content = str(p.content)
                                    is_success = p.outcome != "failed"
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
                    full_text += (
                        f"<Tool Call: {tool_name}>\nArgs: {args_str}\nResult: {ret_content}\n"
                    )
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
                    is_success = ret_part.outcome != "failed"
                    self._set_tool_body(exp, ret_content)
                    exp.set_label(f"⚙ {tool_name} {'✓' if is_success else '✗'}")
                    full_text += (
                        f"<Tool Call: {tool_name}>\nArgs: {args_str}\nResult: {ret_content}\n"
                    )
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

        def _on_tool_expander_toggled(_exp: Gtk.Expander, _pspec: Any) -> None:
            self._auto_scroll = False

        exp.connect("notify::expanded", _on_tool_expander_toggled)

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

    def _on_entry_key_press(self, _widget: Any, event: Gdk.EventKey) -> bool:
        if event.keyval == Gdk.KEY_Escape:
            if self._entry.get_text():
                self._entry.set_text("")
            toplevel = self.get_toplevel()
            if isinstance(toplevel, Gtk.Window):
                toplevel.set_focus(None)
            return True
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
            path = self._get_effective_path()
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
        active_run: Any = None
        # Per-turn trace recorder. Captures provider/model/base_url snapshot
        # at turn start so a live-swap mid-turn stays attributable. Fed by
        # _stream_request/_stream_tools during the iter() loop; finalized +
        # persisted once at the end via _save_trace (mirror of _save_history).
        recorder: TraceRecorder | None = None
        turn_exc: BaseException | None = None
        try:
            if self._agent is None:
                self._append_error("No agent configured.")
                return
            ctx = _StreamCtx(self._start_agent_message())
            recorder = TraceRecorder(
                session_id=self._active_session_id,
                provider=getattr(self, "_active_provider", "") or "",
                model=getattr(self, "_active_model", "") or "",
                base_url=getattr(self, "_active_base_url", "") or "",
                user_prompt=text,
                origin_page_path=self._get_effective_path(),
            )
            self._message_history = _clean_message_history_for_new_turn(self._message_history)
            async with self._agent.iter(
                text,
                message_history=self._message_history,
                deps=self._flowgraph_proxy,
            ) as run:
                active_run = run
                self._active_run = run
                node = run.next_node
                while node is not None and not isinstance(node, End):
                    if Agent.is_model_request_node(node):
                        await self._stream_request(ctx, node, run, recorder)
                    elif Agent.is_call_tools_node(node):
                        self._close_text(ctx)
                        self._close_thinking(ctx)
                        await self._stream_tools(ctx, node, run, recorder)
                    self._scroll_to_bottom()
                    node = await run.next(node)
                    self._update_context_label()

            if run.result is not None:
                self._message_history = run.result.all_messages()
                await self._save_history()
                self._render_history()
                rich_rendered = True
        except asyncio.CancelledError as e:
            turn_exc = e
            if recorder is not None:
                recorder.record_error("cancelled", e)
            if self.current_page is origin_page and self._clear_generation == origin_gen:
                if active_run is not None:
                    try:
                        self._message_history = _clean_message_history_for_new_turn(
                            active_run.all_messages()
                        )
                    except Exception:
                        self._remember_user_message(text)
                else:
                    self._remember_user_message(text)
                asyncio.ensure_future(self._save_history())
                self._append_error("[aborted]", style="aborted")
                rich_rendered = True
            raise
        except Exception as e:
            turn_exc = e
            _log.exception("agent run failed")
            if recorder is not None:
                recorder.record_error("run", e)
            if self.current_page is origin_page:
                if active_run is not None:
                    try:
                        self._message_history = _clean_message_history_for_new_turn(
                            active_run.all_messages()
                        )
                    except Exception:
                        self._remember_user_message(text)
                else:
                    self._remember_user_message(text)
                await self._save_history()
                self._append_error(_format_turn_error(e))
                rich_rendered = True
        finally:
            # Persist the trace row for this turn. Finalize even if recorder
            # saw no events (e.g. the agent was None or iter() raised early)
            # — a turn that ended in an error is still a recorded turn. The
            # clear-generation guard inside _save_trace prevents a late insert
            # from resurrecting a trace against a cleared session.
            #
            # On the success path (turn_exc is None) we await so the trace is
            # saved before widgets update. On cancel/error paths we dispatch as
            # a detached future — mirroring the cancel-path's _save_history
            # pattern — so a double-cancel can't skip the widget cleanup below
            # (CancelledError inherits BaseException, not Exception).
            if recorder is not None:
                try:
                    row = recorder.finalize(active_run, turn_exc)
                    if turn_exc is None:
                        await self._save_trace(row)
                    else:
                        asyncio.ensure_future(self._save_trace(row))
                except Exception:
                    _log.exception("Failed to dispatch trace save")
            self._active_run = None
            self._update_context_label()
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
            if (
                ctx is not None
                and not rich_rendered
                and ctx.full_raw_text
                and self.current_page is origin_page
            ):
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
            self._send_btn.set_image(
                Gtk.Image.new_from_icon_name(
                    "media-playback-stop-symbolic", Gtk.IconSize.SMALL_TOOLBAR
                )
            )
            self._send_btn.set_tooltip_text("Stop")
            self._send_btn.set_sensitive(True)
            self._entry.set_sensitive(False)
        else:
            self._send_btn.set_image(
                Gtk.Image.new_from_icon_name(
                    "media-playback-start-symbolic", Gtk.IconSize.SMALL_TOOLBAR
                )
            )
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
            near_bottom = (
                adj.get_upper() - adj.get_page_size() - adj.get_value()
            ) <= _SCROLL_STICK_THRESHOLD
            if near_bottom:
                self._auto_scroll = True
        elif direction == Gdk.ScrollDirection.SMOOTH:
            # Touchpad smooth-scroll: delta_y < 0 = up, > 0 = down
            _, _, delta_y = event.get_scroll_deltas()
            if delta_y < 0:
                self._auto_scroll = False
            elif delta_y > 0:
                adj = self._scrolled.get_vadjustment()
                near_bottom = (
                    adj.get_upper() - adj.get_page_size() - adj.get_value()
                ) <= _SCROLL_STICK_THRESHOLD
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

    def _open_settings(self) -> None:
        toplevel = self.get_toplevel()
        if not isinstance(toplevel, Gtk.Window):
            toplevel = None
        dlg = SettingsDialog(
            toplevel=toplevel,
            cfg=load_settings(),
            on_save=self._apply_settings_save,
        )
        self._open_dialog = dlg
        dlg.connect("destroy", lambda *_: setattr(self, "_open_dialog", None))
        dlg.show()

    @staticmethod
    def _persist_settings(
        provider: str,
        model: str,
        key_var: str | None,
        key_val: str,
        base_url: str,
        thinking_enabled: bool,
        embed_backend: str,
    ) -> None:
        """Write the new config to `.env`. The base-URL argument is routed to
        the key belonging to the active provider — ChatGPT/Codex has neither a
        base URL nor an API key, and passing one through would write whatever
        the (hidden) URL entry still held over OLLAMA_BASE_URL."""
        if provider == "openai_codex":
            save_settings(provider, model, embed_backend=embed_backend)
        elif provider == "openai_compatible":
            save_settings(
                provider,
                model,
                openai_compatible_base_url=base_url,
                thinking_enabled=thinking_enabled,
                embed_backend=embed_backend,
            )
        else:
            save_settings(
                provider,
                model,
                ollama_base_url=base_url,
                thinking_enabled=thinking_enabled,
                embed_backend=embed_backend,
            )
        if key_var:
            upsert_env_key(key_var, key_val)

    def _apply_settings_save(
        self,
        provider: str,
        model: str,
        key_var: str | None,
        key_val: str,
        ollama_base_url: str = "http://localhost:11434",
        thinking_enabled: bool = True,
        embed_backend: str = "auto",
    ) -> None:
        """Post-Save flow: preflight → persist → live-swap.

        All three phases run synchronously and are bounded (preflight ≤ 5s),
        which is acceptable for a user-initiated action and lets tests assert
        on the persisted state immediately after the dialog's APPLY response.
        """
        from .agent_factory import preflight_connection

        if not model:
            self.set_status("Settings not saved — model name is required.", error=True)
            return

        toplevel = self.get_toplevel()
        if not isinstance(toplevel, Gtk.Window):
            toplevel = None

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
            self._persist_settings(
                provider, model, key_var, key_val, ollama_base_url, thinking_enabled, embed_backend
            )
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
        if provider == "openai_codex":
            hint = "• Click 'Sign in with ChatGPT' in Preferences.\n• Codex requires an active ChatGPT Plus or Pro subscription."
        elif provider == "ollama":
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
