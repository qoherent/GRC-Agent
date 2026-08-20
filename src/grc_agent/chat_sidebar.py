# ruff: noqa: E402
"""Native GTK3 ChatSidebar widget for the grc-agent desktop app.

Streams agent responses via ``agent.iter()``'s node-by-node iteration:
``ModelRequestNode`` yields ``PartStartEvent`` / ``PartDeltaEvent`` (text,
tool calls, reasoning in strict arrival order), ``CallToolsNode`` yields
``FunctionToolCallEvent`` / ``FunctionToolResultEvent``.

Message history is stored as pydantic-ai's native ``ModelMessage`` objects.
"""

import asyncio
import json
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")

from uuid import uuid4

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

from .agent_factory import resolve_model_context_length
from .db import (
    conversation_id_for_session,
    delete_all_sessions,
    delete_session,
    deserialize_messages,
    get_step_store,
    load_session,
    save_session,
)
from .settings import (
    get_env_value,
    load_settings,
    save_settings,
    upsert_env_key,
)
from .ui.css import apply_css as _apply_css
from .ui.markdown_view import MarkdownView
from .ui.providers import (
    PROVIDER_API_KEY as _PROVIDER_API_KEY,
)
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


_MAX_TOOL_DISPLAY_CHARS = 8000


def _format_tool_display(text: str, max_chars: int = _MAX_TOOL_DISPLAY_CHARS) -> str:
    """Format tool argument/result text for Gtk.Expander display labels, keeping Pango bounded."""
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    return f"{text[:half]}\n\n... [truncated {len(text) - max_chars} chars] ...\n\n{text[-half:]}"


def _parse_final_summary(args: Any) -> tuple[list[str], str] | None:
    """Recover the model's final structured output from a `final_result` tool call.

    The agent's output type is `[GrcAgentResponse, str]`, so a structured turn
    ends with a call to pydantic-ai's generated `final_result` tool whose args
    are the GrcAgentResponse JSON (`actions_taken` + `explanation`). Returns
    (actions, explanation) when the args carry that shape, else None — the
    caller then renders the call as an ordinary tool expander instead.
    """
    if not args:
        return None
    if isinstance(args, str):
        try:
            data = json.loads(args)
        except (ValueError, TypeError):
            return None
    elif isinstance(args, dict):
        data = args
    else:
        return None
    actions = data.get("actions_taken")
    explanation = data.get("explanation")
    if not isinstance(actions, list) or not all(isinstance(a, str) for a in actions):
        return None
    if not isinstance(explanation, str):
        explanation = ""
    return actions, explanation


def format_tokens(n: int) -> str:
    """Format token count for display (e.g. 1.2k, 14.7k, 128k, 1M)."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M".replace(".0M", "M")
    if n >= 1_000:
        return f"{n / 1_000:.1f}k".replace(".0k", "k")
    return str(n)


def _tokens_per_second(output_tokens: int | None, duration_ms: int | None) -> float | None:
    """Generation rate for the last turn, or None when it cannot be measured.

    `output_tokens` is the turn's VISIBLE output (total minus reasoning) and
    `duration_ms` is the time the model was actually generating — computed
    natively from pydantic-ai's ModelRequest/ModelResponse timestamps (see
    _generation_ms_from_messages), so tool-call time is excluded and the
    number is the rate the user watched text stream, not tokens per
    wall-clock turn second.

    Returns None rather than 0 for a turn that produced no tokens or took no
    measurable time — showing "0 tok/s" would read as a stalled backend.
    """
    if not output_tokens or not duration_ms or duration_ms <= 0:
        return None
    return output_tokens / (duration_ms / 1000)


def _generation_ms_from_messages(new_msgs: list[Any]) -> int:
    """Sum of (ModelResponse.timestamp - ModelRequest.timestamp) per pair.

    pydantic-ai stamps high-precision local timestamps on ModelRequest
    (send) and ModelResponse (received) — the delta per (request, response)
    pair is that request's model processing time (TTFT + generation). Tool
    execution happens between a response and the next request, so summing
    the pairs excludes it. `new_msgs` must be THIS run's own messages
    (`result.new_messages()` — input history and older runs excluded), so
    no prior-turn leakage. Verified live: a tool-calling turn's pair-sum
    matched the measured generation time and excluded the tool sleep.
    """
    total_ms = 0.0
    # The pairing is inherently (n, n-1): the last message has no successor.
    for prev, m in zip(new_msgs, new_msgs[1:], strict=False):
        if m.__class__.__name__ == "ModelResponse" and prev.__class__.__name__ == "ModelRequest":
            total_ms += (m.timestamp - prev.timestamp).total_seconds() * 1000
    return int(total_ms)


def _extract_httpx_message(resp) -> str:
    """Provider JSON error message from an httpx response, if any."""
    try:
        data = resp.json()
    except Exception:
        return getattr(resp, "text", "")[:300]
    if not isinstance(data, dict):
        return ""
    err = data.get("error")
    if isinstance(err, dict) and err.get("message"):
        return str(err["message"])
    if isinstance(err, str):
        return err
    if data.get("message"):
        return str(data["message"])
    if data.get("detail"):
        return str(data["detail"])
    return ""


def _extract_body_message(body) -> str:
    """Provider JSON error message from a body attribute, if any."""
    if not isinstance(body, dict):
        return str(body)
    err = body.get("error")
    if isinstance(err, dict) and err.get("message"):
        return str(err["message"])
    return str(body.get("message") or body.get("detail") or body)


def _extract_cause_message(cause: Exception) -> str:
    """Best-effort human message from an exception chain cause.

    Prefers the provider's JSON error payload (httpx response or ``body``
    attribute) over the raw exception string, so the user sees e.g.
    "Invalid API key provided" instead of a bare status line.
    """
    cause_msg = ""
    resp = getattr(cause, "response", None)
    if resp is not None:
        cause_msg = _extract_httpx_message(resp)
    body = getattr(cause, "body", None)
    if not cause_msg and body:
        cause_msg = _extract_body_message(body)
    return cause_msg or str(cause)


def _format_turn_error(e: Exception) -> str:
    """User-facing message for a failed agent turn (_run_agent_turn's catch-all).
    Exposes exact status codes, provider error message details, and underlying causes.
    """
    cause_str = ""
    if hasattr(e, "__cause__") and e.__cause__:
        cause_msg = _extract_cause_message(e.__cause__)
        if cause_msg and cause_msg != str(e):
            cause_str = f" (Cause: {cause_msg})"

    if isinstance(e, ModelHTTPError):
        msg = f"Model HTTP {e.status_code} Error"
        if e.body:
            body_detail = ""
            if isinstance(e.body, dict):
                body_detail = (
                    e.body.get("message")
                    or e.body.get("error", {}).get("message")
                    or e.body.get("detail")
                    or str(e.body)
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


def _run_usage_output_override(run: Any, last_output: int, last_reasoning: int) -> tuple[int, int]:
    """Replace last-response-only output/reasoning with the run's aggregated
    totals when a live run is available (see _update_context_label)."""
    if run is None:
        return last_output, last_reasoning
    u = getattr(run, "usage", None)
    if u is None:
        return last_output, last_reasoning
    details = getattr(u, "details", None) or {}
    return (
        getattr(u, "output_tokens", 0) or 0,
        details.get("reasoning_tokens", 0) or 0,
    )


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
        self._model_build_error: str | None = None
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
        self._compact_task: asyncio.Task | None = None
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
        if (event.state & Gdk.ModifierType.CONTROL_MASK) and event.keyval == Gdk.KEY_comma:
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
        self._compact_btn = _icon_btn(
            "view-refresh-symbolic",
            "Compact conversation history — summarize older messages (keeps recent context)",
            cb=self._on_compact_clicked,
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
        vbox.get_style_context().add_class("chat-input-area")
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
        # The run's own aggregated usage is the authoritative per-turn total:
        # all_messages() includes prior turns' responses, and the
        # last-response-only extraction undercounts multi-request turns. The
        # context label's main number (last_input_tokens) keeps the
        # last-response semantic — it is the context size at the end of the
        # turn.
        last_output_tokens, last_reasoning_tokens = _run_usage_output_override(
            getattr(self, "_active_run", None), last_output_tokens, last_reasoning_tokens
        )

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

        rate = getattr(self, "_last_turn_rate", None)
        if rate:
            text = text.replace("</span>", f" \u00b7 {rate:.0f} tok/s</span>")

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

    def _on_compact_clicked(self, _btn: Gtk.Button) -> None:
        """Manual compaction (compact_now button): summarize the older part of
        the conversation between runs, on the unified event loop — never the
        GTK thread. The pre-compact history is snapshotted into the step store
        first so ConversationSearch can still recall what the summary drops
        (D3), and the send button becomes Stop so the user can cancel."""
        if self._busy or self._agent is None or not self._message_history:
            return
        if self._active_session_id is None:
            # No session row = no conversation id: the pre-compact snapshot
            # cannot be registered, so compacting would destroy the only
            # (in-memory) copy of the summarized turns. Refuse.
            self.set_status("Cannot compact — history is not saved to a session yet.", error=True)
            return
        self._set_busy(True)
        self._compact_task = asyncio.ensure_future(self._run_compact_now())

    async def _run_compact_now(self) -> None:
        try:
            from pydantic_ai_harness.compaction._manual import compact_now
            from pydantic_ai_harness.step_persistence import (
                ContinuableSnapshot,
                RunRecord,
            )

            from .agent_factory import make_summarizing_strategy

            # D3: snapshot the pre-compact history first so ConversationSearch
            # can still recall what the summary drops. The run id mirrors
            # StepPersistence's own derivation ('{agent_name}-{8-hex}').
            sid = self._active_session_id
            if sid is not None:
                store = get_step_store()
                conv = conversation_id_for_session(sid)
                rid = f"grc_chat-{uuid4().hex[:8]}"
                await store.register_run(
                    RunRecord(run_id=rid, conversation_id=conv, agent_name="grc_chat")
                )
                await store.save_snapshot(
                    ContinuableSnapshot(
                        run_id=rid,
                        step_index=0,
                        messages=self._message_history,
                        conversation_id=conv,
                        agent_name="grc_chat",
                    )
                )

            strategy = make_summarizing_strategy()
            compacted = await compact_now(
                strategy,
                self._message_history,
                model=self._agent.model,  # D1: model=None inherits this
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

    def set_agent(self, agent: Agent, model_error: str | None = None) -> None:
        self._agent = agent
        self._model_build_error = model_error
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
            # A local Ollama on a custom port/LAN host has no ":11434" marker
            # in its URL — the transport's own provider name is the authority
            # (OllamaProvider.name is "ollama" for local and cloud alike, and
            # the URL already split cloud from local above).
            if (
                resolved_provider == "openai_compatible"
                and str(getattr(provider, "name", "")) == "ollama"
            ):
                resolved_provider = "ollama_local"
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
        # base_url is the running model's own provider URL; when it is empty
        # the provider resolution above already early-returned, so there is
        # no fallback URL to invent here.
        resolved_url = base_url or ""
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
        self._compact_btn.set_sensitive(False)
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

    def stop_chat(self) -> None:
        if self._chat_task and not self._chat_task.done():
            self._chat_task.cancel()
        if self._compact_task and not self._compact_task.done():
            self._compact_task.cancel()

    def shutting_down(self) -> None:
        """Signal that the app is shutting down — any in-flight widget cleanup
        (streaming flush, scroll-to-bottom, busy reset) should be skipped to
        avoid GTK warnings/crashes on mid-destroy widgets (L7)."""
        self._shutting_down = True
        if self._md is not None:
            self._md.set_shutting_down(True)

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
            summary = _parse_final_summary(part.args)
            if (part.tool_name or "") == "final_result" and summary is not None:
                # The model's final structured output (GrcAgentResponse)
                # arrives as a final_result tool call — render it as a
                # readable summary card, not a raw-JSON tool expander.
                # Deliberately NOT registered in ctx.tools: the
                # FunctionToolResultEvent handler would overwrite the card
                # with the "Final result processed." return.
                widget = self._make_final_summary_widget(*summary)
                ctx.box.pack_start(widget, False, False, 0)
                widget.show_all()
                ctx.full_raw_text += f"<Summary>\n{summary[0]}\n{summary[1]}\n</Summary>\n"
                self._update_copy_text(ctx.box, ctx.full_raw_text)
                return
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
            # content_delta is Optional on this delta type (unlike TextPartDelta):
            # a ThinkingPartDelta may carry only a signature/provider-metadata
            # update with no text at all. Codex sends those around its reasoning
            # summary parts, and appending one raised
            # "can only concatenate str (not NoneType) to str", killing the turn.
            if delta.content_delta is None:
                return
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
        hbox.get_style_context().add_class("chat-user-msg-box")

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
                summary = _parse_final_summary(part.args)
                if tool_name == "final_result" and summary is not None:
                    # Same summary-card treatment as the streaming path — a
                    # re-render (e.g. after a settings live-swap) must not
                    # degrade the final structured output back to raw JSON.
                    widget = self._make_final_summary_widget(*summary)
                    box.pack_start(widget, False, False, 0)
                    widget.show_all()
                    full_text += f"<Summary>\n{summary[0]}\n{summary[1]}\n</Summary>\n"
                    continue
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
        try:
            if self._agent is None:
                self._append_error("No agent configured.")
                return

            try:
                cfg = load_settings()
                configured_provider = cfg.get("provider", self._active_provider)
            except Exception:
                configured_provider = self._active_provider

            key_var = _PROVIDER_API_KEY.get(configured_provider)
            if key_var and configured_provider not in ("ollama_local", "openai_compatible"):
                import os
                key_val = get_env_value(key_var) or os.environ.get(key_var)
                if not key_val:
                    provider_title = _PROVIDER_LABELS.get(
                        configured_provider, configured_provider
                    )
                    self._append_error(
                        f"API key for {provider_title} ({key_var}) is not set. "
                        "Open Preferences (Ctrl+,) to configure your API key."
                    )
                    return

            if configured_provider == "openai_codex":
                from .providers.openai_codex import is_signed_in as codex_is_signed_in

                if not codex_is_signed_in():
                    self._append_error(
                        "Not signed in to ChatGPT. Open Preferences (Ctrl+,) and click 'Sign in with ChatGPT'."
                    )
                    return

            if self._model_build_error:
                provider_title = _PROVIDER_LABELS.get(
                    configured_provider, configured_provider
                )
                self._append_error(
                    f"Cannot run {provider_title}: {self._model_build_error}. "
                    "Open Preferences (Ctrl+,) to configure."
                )
                return

            ctx = _StreamCtx(self._start_agent_message())
            self._message_history = _clean_message_history_for_new_turn(self._message_history)
            async with self._agent.iter(
                text,
                message_history=self._message_history,
                deps=self._flowgraph_proxy,
                # Groups this turn's StepPersistence runs/events/snapshots
                # under the active chat session — the same conversation id
                # db.py's cleanup SQL matches. Inherited by message_history
                # on later turns, but passed explicitly every turn as one
                # uniform rule (runs before a session row exists — e.g. a
                # failed first send — fall back to pydantic-ai's fresh id
                # and are simply ungrouped).
                conversation_id=(
                    conversation_id_for_session(self._active_session_id)
                    if self._active_session_id is not None
                    else None
                ),
            ) as run:
                active_run = run
                self._active_run = run
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
                    self._update_context_label()

            if run.result is not None:
                self._message_history = run.result.all_messages()
                await self._save_history()
                self._render_history()
                rich_rendered = True
        except asyncio.CancelledError:
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
            _log.exception("agent run failed")
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
            # Generation rate for the status line. Visible output tokens
            # (turn total minus hidden reasoning) over the time the model was
            # actually generating, computed natively from pydantic-ai's own
            # ModelRequest/ModelResponse timestamps (tool-call time excluded).
            if active_run is not None and getattr(active_run, "result", None) is not None:
                try:
                    usage = getattr(active_run, "usage", None)
                    output_tokens = getattr(usage, "output_tokens", 0) or 0 if usage else 0
                    details = (getattr(usage, "details", None) or {}) if usage else {}
                    reasoning = details.get("reasoning_tokens", 0) or 0
                    self._last_turn_rate = _tokens_per_second(
                        max(0, output_tokens - reasoning),
                        _generation_ms_from_messages(active_run.result.new_messages()),
                    )
                except Exception:
                    _log.exception("Failed to compute generation rate")
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
        self._compact_btn.set_sensitive(not busy and bool(self._message_history))
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
        embed_backend: str,
    ) -> None:
        """Write the new config to `.env`. Base-URL routing: editable-URL
        providers (ollama_local, openai_compatible) persist their URL var;
        fixed-endpoint providers (ollama_cloud, openrouter, openai) have a
        canonical URL that is never persisted; ChatGPT/Codex has neither a
        base URL nor an API key."""
        if provider == "openai_codex":
            save_settings(provider, model, embed_backend=embed_backend)
        elif provider == "openai_compatible":
            save_settings(
                provider,
                model,
                openai_compatible_base_url=base_url,
                embed_backend=embed_backend,
            )
        elif provider == "ollama_local":
            save_settings(
                provider,
                model,
                ollama_base_url=base_url,
                embed_backend=embed_backend,
            )
        else:
            save_settings(provider, model, embed_backend=embed_backend)
        if key_var:
            upsert_env_key(key_var, key_val)

    def _apply_settings_save(
        self,
        provider: str,
        model: str,
        key_var: str | None,
        key_val: str,
        base_url: str = "http://localhost:11434",
        embed_backend: str = "lexical",
    ) -> None:
        """Post-Save flow: preflight → persist → live-swap.

        All three phases run synchronously and are bounded (preflight ≤ 5s),
        which is acceptable for a user-initiated action and lets tests assert
        on the persisted state immediately after the dialog's APPLY response.
        """
        from .agent_factory import probe_backend

        if not model:
            self.set_status("Settings not saved — model name is required.", error=True)
            return

        toplevel = self.get_toplevel()
        if not isinstance(toplevel, Gtk.Window):
            toplevel = None

        provider_label = _PROVIDER_LABELS.get(provider, provider)

        # 1. ONE bounded probe answers both questions: can we reach the
        #    backend, and does it serve this model? A missing tag on a local
        #    daemon means a silent multi-GB pull that reads as a hung chat —
        #    surface it, but never block on it: the status bar warns, the
        #    save proceeds, and the live-swap still happens.
        self.set_status(f"Checking {provider_label}\u2026")
        reach_err, model_warn = probe_backend(provider, key_val, base_url, model)
        if reach_err and not self._confirm_unreachable(
            provider, reach_err, toplevel, base_url=base_url
        ):
            self.set_status("Settings not saved — provider unreachable.", error=True)
            return
        if model_warn:
            self.set_status(model_warn, error=True)

        # 2. Persist to .env synchronously — tests assert on load_settings()
        #    immediately after emitting the response signal.
        try:
            self._persist_settings(provider, model, key_var, key_val, base_url, embed_backend)
        except Exception as e:
            _log.exception("Failed to save settings")
            self.set_status(f"Settings not saved ({e}).", error=True)
            return

        # 3. Live-swap the running Agent in-place. Dispatched async so the
        #    gbulb loop stays responsive during model construction (which
        #    spins up an httpx client and pydantic-ai Agent). The history is
        #    kept verbatim — ModelMessage objects are provider-agnostic.
        warn_suffix = f" ⚠ {model_warn}" if model_warn else ""
        if self._rebuild_agent is None:
            self.set_status(
                f"Settings saved. Restart to apply.{warn_suffix}", error=bool(model_warn)
            )
            return
        try:
            new_agent, model_err = self._rebuild_agent()
        except Exception as e:
            _log.exception("Live-swap rebuild failed")
            self.set_status(f"Settings saved but live-swap failed: {e}", error=True)
            return
        self.set_agent(new_agent, model_error=model_err)
        if model_err:
            self.set_status(
                f"Switched with warning ({model_err}). Running on defaults.",
                error=True,
            )
        else:
            self.set_status(
                f"Switched to {provider_label} \u00b7 {model}.{warn_suffix}",
                error=bool(model_warn),
            )

    def _confirm_unreachable(
        self,
        provider: str,
        err: str,
        toplevel: Gtk.Window | None,
        *,
        base_url: str = "http://localhost:11434",
    ) -> bool:
        """Modal Yes/No confirm when the preflight ping fails. Returns True
        if the user wants to save anyway. Anchors the dialog on `self` so
        PyGObject doesn't GC it mid-`.run()`."""
        provider_label = _PROVIDER_LABELS.get(provider, provider)
        if provider == "openai_codex":
            hint = "• Click 'Sign in with ChatGPT' in Preferences.\n• Codex requires an active ChatGPT Plus or Pro subscription."
        elif provider == "ollama_local":
            hint = f"• Ensure local Ollama daemon is running ('ollama serve').\n• Verify host is reachable at {base_url}."
        elif provider == "ollama_cloud":
            hint = f"• Verify your Ollama Cloud API key.\n• Check reachability of {base_url}."
        elif provider in ("openrouter", "openai"):
            hint = f"• Verify your API key for {provider}.\n• Check reachability of {base_url}."
        else:
            hint = f"• Ensure your OpenAI-compatible server is running.\n• Verify endpoint is reachable at {base_url}."
        return self._confirm_yes_no(
            toplevel,
            title=f"Cannot reach {provider_label}",
            body=(
                f"Preflight check error: {err}\n\n"
                f"Actionable hints:\n{hint}\n\n"
                f"Save anyway? The agent will retry when you send a message."
            ),
        )

    def _confirm_yes_no(self, toplevel: Gtk.Window | None, *, title: str, body: str) -> bool:
        """Modal Yes/No warning dialog. Returns the user's answer. Anchored
        on `self` so PyGObject doesn't GC it mid-`.run()`."""
        confirm = Gtk.MessageDialog(
            transient_for=toplevel,
            flags=Gtk.DialogFlags.MODAL,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.YES_NO,
            text=title,
        )
        confirm.format_secondary_text(body)
        self._open_dialog = confirm
        keep = confirm.run() == Gtk.ResponseType.YES
        self._open_dialog = None
        confirm.destroy()
        return keep
