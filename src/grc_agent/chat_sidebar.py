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
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")


from gi.repository import Gdk, GLib, GObject, Gtk, Pango
from pydantic_ai import Agent
from pydantic_ai.messages import (
    BinaryContent,
    ModelMessage,
    UserContent,
)
from pydantic_ai.tools import (
    DeferredToolRequests,
    DeferredToolResults,
)
from pydantic_graph import End

from .agent_factory import aresolve_model_context_length, describe_model
from .chat.approvals import ApprovalsMixin
from .chat.composer import ComposerMixin
from .chat.constants import _is_near_bottom
from .chat.errors import _format_turn_error
from .chat.format import format_tokens
from .chat.history import (
    _clean_message_history_for_new_turn,
    _messages_call_tool,
    _without_truncated_thinking_tail,
)
from .chat.settings_controller import SettingsControllerMixin
from .chat.stream_view import StreamViewMixin, _StreamCtx
from .chat.transcript_view import TranscriptViewMixin
from .chat.usage import (
    _collect_token_usage,
    _format_native_cost,
    _run_usage_cost_override,
    _run_usage_output_override,
)
from .chat.zoom_projection import ZoomProjectionMixin
from .db import (
    archive_transcript,
    conversation_id_for_session,
    delete_all_sessions,
    delete_session,
    deserialize_messages,
    load_plan_items,
    load_session,
    save_session,
    user_request,
)
from .settings import (
    get_env_value,
    get_theme_mode,
    load_settings,
    resolve_key,
    set_theme_mode,
    upsert_env_key,
)
from .ui.css import apply_css as _apply_css
from .ui.css import apply_theme, is_dark_theme
from .ui.markdown_view import MarkdownView
from .ui.providers import (
    PROVIDER_API_KEY as _PROVIDER_API_KEY,
)
from .ui.providers import (
    PROVIDER_BADGE_LABEL as _PROVIDER_BADGE_LABEL,
)
from .ui.providers import (
    PROVIDER_KEY_OPTIONAL as _PROVIDER_KEY_OPTIONAL,
)
from .ui.providers import (
    PROVIDER_LABELS as _PROVIDER_LABELS,
)
from .ui.providers import (
    resolve_provider_from_base_url as _resolve_provider_from_base_url,
)
from .ui.welcome_view import WelcomeView

_log = logging.getLogger(__name__)


class ChatSidebar(
    StreamViewMixin,
    TranscriptViewMixin,
    ComposerMixin,
    ApprovalsMixin,
    ZoomProjectionMixin,
    SettingsControllerMixin,
    Gtk.Box,
):
    """Complete chat sidebar: toolbar, streaming message list, input area.

    Toolbar buttons emit GObject signals for ``desktop_app.py`` to connect.
    The Send button doubles as a Stop/abort button while a request is running.

    ``StreamViewMixin`` (live-streaming render), ``TranscriptViewMixin``
    (rendering completed turns), ``ComposerMixin`` (input area, attachments,
    send), ``ApprovalsMixin`` (the approval gate), ``ZoomProjectionMixin``
    (canvas-zoom-to-font projection), and ``SettingsControllerMixin`` (the
    Preferences dialog and its save flow) are U15 splits; each shares this
    one instance's attributes by convention, not encapsulation — an
    organizational split of a genuinely single widget.
    """

    __gsignals__ = {
        "new-session-clicked": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "toggle-blocks-panel": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        _apply_css()
        self.get_style_context().add_class("chat-sidebar")
        # File drag-and-drop registers on the input area (composer.py), not
        # here: a sidebar-wide uri-list target turned every text-selection
        # drag in the transcript into a file-drop gesture.
        self._agent: Agent[Any, Any] | None = None
        self._executor_agent: Agent[Any, Any] | None = None
        self._planner_agent: Agent[Any, Any] | None = None
        self._agent_mode = "executor"
        self._changing_agent_mode = False
        # Live-swap callback: when the Settings dialog saves a new provider/
        # model/key, this rebuilds the Agent in-place. Set by desktop_app.py
        # right after set_agents(). None in tests/headless mode (the Settings
        # dialog falls back to the old restart-gated behavior if unset).
        self._rebuild_agent: Callable[[], Any] | None = None
        # Active provider/model label shown in the toolbar; updated on every
        # set_agents call (startup + live-swap) so the user always sees which
        # backend the running agent is actually using.
        self._active_provider: str = ""
        # Context-window resolution is cached per (provider, model) and
        # refreshed off the unified loop; the label reads the cache only.
        self._context_window_cache: dict[tuple[str, str], int] = {}
        self._context_window_probed: set[tuple[str, str]] = set()
        self._context_window_tasks: set[asyncio.Task] = set()
        self._active_model: str = ""
        self._active_base_url: str | None = None
        self._model_build_error: str | None = None
        # True when the status bar currently shows an error. set_status uses
        # this to enforce the "background poll can't clobber a sticky error"
        # rule (M5) — saves save/preflight failures visible past the next
        # "Catalog indexed" transition.
        self._status_is_error: bool = False
        # Model-wait elapsed indicator state (status bar right edge).
        self._wait_timer_id: int | None = None
        self._wait_started: float = 0.0
        # Auto-scroll tracking: True by default (follow new content). The only
        # authority on this flag is _on_scroll_value_changed, recomputed from
        # the scroll position on every vadjustment value-changed — every user
        # scroll source (wheel, scrollbar drag, keyboard, touch) changes the
        # value, while content growth does not (value-changed only fires for
        # the value property), so streaming appends cannot corrupt the intent.
        self._auto_scroll: bool = True
        # R9/KD2 zoom-projection state: ONE scoped CssProvider on THIS
        # sidebar's style context (created on the first projection, then
        # reload-only via load_from_data) carrying a single absolute
        # .chat-sidebar font-size rule. The base is the sidebar's measured
        # theme font, captured once at provider creation. Session-only;
        # never persisted and never written by the canvas side.
        self._zoom_css_provider: Gtk.CssProvider | None = None
        self._zoom_css_base_px: float | None = None
        # Last multiplier actually applied (R9): the canvas clamp band is
        # wider than the sidebar's, so zoom steps at the clamped tails fire
        # on_zoom_changed with a REAL canvas zoom change whose projected
        # multiplier is identical — reload + sweeps would be pure no-op work
        # on every wheel tick there.
        self._zoom_css_last_multiplier: float | None = None
        # The anchor-preserving settle idle below can outlive a destroyed
        # window (tests destroy their windows immediately after a
        # projection; the idle source holds the sidebar alive). One destroy
        # flag keeps the idle a silent no-op instead of walking finalized
        # widgets (observed live: Gtk-CRITICALs + AttributeError on
        # get_vadjustment of a destroyed ScrolledWindow).
        self._zoom_projection_dead = False
        self.connect("destroy", self._on_zoom_projection_destroy)
        self._flowgraph_proxy: object | None = None
        # MarkdownView (created in __init__ after the message list exists) owns
        # the badge-regex cache, the column-width pin/rewrap state, and the
        # listbox size-allocate connection.
        self._md: MarkdownView | None = None
        self._message_history: list[ModelMessage] = []
        # Session-scoped shell prefix-allows ('Always allow <tok>' on a shell
        # approval card): granted tokens plus the session they belong to. A
        # different active session id makes the set inert — no reset wiring
        # needed at the load/clear/switch sites.
        self._shell_allowed_prefixes: set[str] = set()
        self._shell_allowed_session: int | None = None
        self._active_session_id: int | None = None
        self._loading_session_id: int | None = None
        self._busy = False
        self._idle_event = asyncio.Event()
        self._idle_event.set()
        # Pending image attachments for the next composer dispatch — pydantic-ai
        # BinaryContent parts, kept raw so the send path builds the multimodal
        # prompt without a second copy of the bytes.
        self._attachments: list[BinaryContent] = []
        # Bumped on every global Clear History. _save_history captures it before
        # dispatching its (uncancellable) worker-thread save; if a clear lands
        # while that save is in flight, the saved row is removed so a cleared
        # session can't resurrect.
        self._clear_generation: int = 0
        # Named references exist only where a call site must ask "is THIS
        # specific kind of task running" (e.g. the implement-plan guard).
        # Lifecycle (cancel-all-on-clear/stop) reads _background_tasks, the
        # one set every task actually lives in, instead of two methods each
        # hand-enumerating the same four names.
        self._chat_task: asyncio.Task | None = None
        self._compact_task: asyncio.Task | None = None
        self._fix_task: asyncio.Task | None = None
        self._implement_plan_task: asyncio.Task | None = None
        self._background_tasks: set[asyncio.Task] = set()
        self._implement_plan_row: Gtk.ListBoxRow | None = None
        self._implement_plan_button: Gtk.Button | None = None
        self._project_directory: Path | None = None
        # Set by shutting_down() (called from desktop_app.py's _shutdown)
        # just before stop_chat(). _run_agent_turn's finally block checks
        # this to skip widget operations on widgets that are mid-destroy
        # when the window closes (L7).
        self._shutting_down: bool = False
        # Declared here rather than only assigned mid-turn: three readers used
        # `self._active_run` to survive the pre-first-turn
        # window, which also hid the type from mypy.
        self._active_run: Any = None
        # Per-domain last-seen RAG build status, so the poller only writes the
        # status bar on transitions (and while building) — never when idle.
        # Catalog and docs build independently and can run concurrently.
        self._last_index_state: dict[str, str] = {}
        self._last_index_msg: str | None = None
        self._open_dialog: Gtk.Dialog | None = None

        # Slim side toggle for GRC block library
        self._blocks_toggle = Gtk.Button()
        self._blocks_toggle.set_tooltip_text("Toggle block library")
        self._blocks_toggle.get_style_context().add_class("chat-side-toggle")
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
        self._build_project_bar(content)
        self._build_toolbar(content)
        self._build_message_list(content)
        self._md = MarkdownView(self._listbox, self._get_cm)
        self._welcome = WelcomeView(
            self._listbox,
            self._send_quick_prompt,
            self._on_recent_session_clicked,
            self._on_delete_recent_session,
            self._on_clear_history_clicked,
        )
        self._build_input_area(content)
        self._build_status_bar(content)
        self.pack_start(content, True, True, 0)

        # Apply saved theme mode
        apply_theme(get_theme_mode())

        self.connect("key-press-event", self._on_key_press_event)

        # Refresh relative timestamps ("2m ago") on the recent-sessions list
        # while the welcome screen is visible. Re-renders only when idle and
        # empty so live-streaming bubbles are never wiped.
        # Source ids are retained so destroy() can remove them. Without that
        # every sidebar ever constructed keeps polling the shared default
        # GMainContext for the life of the process, holding a strong reference
        # to a widget that is otherwise gone.
        self._welcome_timer_id: int | None = GLib.timeout_add_seconds(
            60, self._refresh_welcome_times
        )

        # Poll the RAG index-build status (set by the worker thread that runs
        # ingest) and surface progress in the status bar. Cheap dict reads; the
        # build itself runs off the main loop via asyncio.to_thread.
        self._indexing_timer_id: int | None = GLib.timeout_add(500, self._poll_indexing)

    def _on_key_press_event(self, _widget: Gtk.Widget, event: Gdk.EventKey) -> bool:
        if (event.state & Gdk.ModifierType.CONTROL_MASK) and event.keyval == Gdk.KEY_comma:
            self._open_settings()
            return True
        return False

    def _build_project_bar(self, content: Gtk.Box) -> None:
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        bar.set_border_width(4)
        bar.get_style_context().add_class("chat-project-bar")

        label = Gtk.Label(label="Project:")
        label.get_style_context().add_class("chat-project-label")
        bar.pack_start(label, False, False, 0)

        self._proj_label = Gtk.Label(label="")
        self._proj_label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self._proj_label.set_max_width_chars(32)
        self._proj_label.set_xalign(0.0)
        self._proj_label.get_style_context().add_class("chat-header-badge")
        bar.pack_start(self._proj_label, True, True, 0)

        self._browse_btn = Gtk.Button(label="Browse")
        self._browse_btn.set_tooltip_text("Select project directory")
        self._browse_btn.get_style_context().add_class("chat-compact-btn")
        self._browse_btn.connect("clicked", self._on_browse_clicked)
        bar.pack_start(self._browse_btn, False, False, 0)

        saved_dir = get_env_value("GRC_PROJECT_DIR")
        if saved_dir and Path(saved_dir).is_dir():
            self._project_directory = Path(saved_dir).resolve()
        else:
            self._project_directory = Path.cwd().resolve()

        self._proj_label.set_text(self._project_directory.name or str(self._project_directory))
        self._proj_label.set_tooltip_text(str(self._project_directory))

        content.pack_start(bar, False, False, 0)

    def _on_browse_clicked(self, _btn: Gtk.Button) -> None:
        top = self.get_toplevel()
        parent_win = top if isinstance(top, Gtk.Window) else None
        dialog = Gtk.FileChooserDialog(
            title="Select Project Directory",
            parent=parent_win,
            action=Gtk.FileChooserAction.SELECT_FOLDER,
        )
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Select", Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.OK)
        if self._project_directory and self._project_directory.is_dir():
            dialog.set_current_folder(str(self._project_directory))

        self._open_dialog = dialog

        def _on_response(_dlg: Gtk.Dialog, response: int) -> None:
            self._open_dialog = None
            if response == Gtk.ResponseType.OK:
                selected = dialog.get_filename()
                if selected:
                    self.set_project_directory(selected)
            dialog.destroy()

        dialog.connect("response", _on_response)
        dialog.show()

    def get_project_directory(self) -> Path | None:
        """The currently selected project directory."""
        return self._project_directory

    def set_project_directory(self, path: Path | str | None) -> None:
        """Programmatically set and persist the project directory."""
        if path:
            p = Path(path).resolve()
            self._project_directory = p
            if hasattr(self, "_proj_label") and self._proj_label:
                self._proj_label.set_text(p.name or str(p))
                self._proj_label.set_tooltip_text(str(p))
            upsert_env_key("GRC_PROJECT_DIR", str(p))
            self.set_status(f"Project: {p.name}")
        else:
            self._project_directory = None
            if hasattr(self, "_proj_label") and self._proj_label:
                self._proj_label.set_text("None")
                self._proj_label.set_tooltip_text("No project directory set")
            upsert_env_key("GRC_PROJECT_DIR", "")

    def _build_toolbar(self, content: Gtk.Box) -> None:
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        bar.set_border_width(4)

        def _icon_btn(
            icon_name: str, tooltip: str, signal: str | None = None, cb=None
        ) -> Gtk.Button:
            b = Gtk.Button.new_from_icon_name(icon_name, Gtk.IconSize.SMALL_TOOLBAR)
            b.set_tooltip_text(tooltip)
            b.get_accessible().set_name(tooltip)
            b.get_style_context().add_class("chat-toolbar-btn")
            if signal:
                b.connect("clicked", lambda *_: self.emit(signal))
            if cb:
                b.connect("clicked", cb)
            bar.pack_start(b, False, False, 0)
            return b

        self._new_session_btn = _icon_btn(
            "document-new-symbolic", "New chat", "new-session-clicked"
        )

        # Active provider badge — reflects the *running* agent's actual
        # provider/model. Expands across the toolbar.
        self._provider_label = Gtk.Label(label="")
        self._provider_label.set_tooltip_text(
            "Active provider/model. Click Preferences (Ctrl+,) to change settings."
        )
        self._provider_label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self._provider_label.set_max_width_chars(42)
        self._provider_label.get_style_context().add_class("chat-header-badge")
        bar.pack_start(self._provider_label, True, True, 2)

        # Quick Theme Toggle
        self._theme_btn = _icon_btn(
            "weather-clear-night-symbolic",
            "Toggle Dark (Black) / Light theme",
            cb=self._on_theme_toggle_clicked,
        )

        # Settings
        self._gear_btn = _icon_btn(
            "preferences-system-symbolic",
            "Preferences (Ctrl+,)",
            cb=lambda *_: self._open_settings(),
        )

        bar.get_style_context().add_class("chat-toolbar")
        content.pack_start(bar, False, False, 0)

    def _on_theme_toggle_clicked(self, _btn: Gtk.Button | None = None) -> None:
        current = get_theme_mode()
        new_mode = (
            "light"
            if (current == "dark" or (current == "system" and is_dark_theme()))
            else "dark"
        )
        set_theme_mode(new_mode)
        apply_theme(new_mode)
        self.set_status(f"Theme: {'Dark (Black)' if new_mode == 'dark' else 'Light'}")
        self._render_history()
        toplevel = self.get_toplevel()
        if isinstance(toplevel, Gtk.Window):
            toplevel.queue_draw()

    def _build_message_list(self, content: Gtk.Box) -> None:
        self._listbox = Gtk.ListBox()
        self._listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self._listbox.set_activate_on_single_click(False)
        self._listbox.set_border_width(4)

        self._scrolled = Gtk.ScrolledWindow()
        self._scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._scrolled.set_vexpand(True)
        self._scrolled.add(self._listbox)
        # Track user scroll intent on the vadjustment itself: every scroll
        # source (wheel, scrollbar drag, keyboard, kinetic/touch) changes the
        # adjustment value, while content growth only touches upper/page-size
        # (the `changed` signal). The old `scroll-event` handler covered wheel
        # events only — dragging the scrollbar or scrolling with the keyboard
        # left _auto_scroll stale, so reading upstream content got yanked back
        # to the bottom on the next streaming flush.
        self._scrolled.get_vadjustment().connect(
            "value-changed", self._on_scroll_value_changed
        )
        # R10: Control+wheel over the transcript is a zoom INPUT for the
        # canvas (one handler); the consumed event never scrolls the chat and
        # never touches the intent authority above.
        self._scrolled.connect("scroll-event", self._on_chat_scroll_event)

        content.pack_start(self._scrolled, True, True, 0)

    def _schedule_context_window_probe(self, provider: str, model: str) -> None:
        """Resolve the model's context window once, off the unified loop."""
        if not provider or not model:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # Called from a synchronous render path with no loop running (a
            # headless test, or before install()). Leave the key unprobed so
            # the next call under the unified loop schedules it.
            return
        key = (provider, model)
        self._context_window_probed.add(key)

        async def _probe() -> None:
            try:
                window = await aresolve_model_context_length(provider, model)
            except Exception as exc:  # never let a probe break a turn
                _log.debug("context-window probe failed for %s/%s: %s", provider, model, exc)
                return
            if window is not None:
                self._context_window_cache[key] = window
                self._update_context_label()

        task = loop.create_task(_probe())
        self._context_window_tasks.add(task)
        task.add_done_callback(self._context_window_tasks.discard)

    def _current_messages(self) -> list[ModelMessage]:
        """The one authoritative answer to "what are the messages right now".

        self._message_history is the STABLE snapshot, only reassigned at a
        few discrete points in the turn's lifecycle (a new prompt appended,
        the turn's final result, an approval-pause checkpoint) — it goes
        stale the moment a run starts streaming and stays stale until one of
        those points lands. self._active_run.all_messages() is live and
        current for exactly the window a run is in flight. Everything that
        needs "the current transcript" reads through this one method rather
        than re-deriving which of the two to trust.
        """
        return (
            self._active_run.all_messages()
            if self._active_run is not None
            else self._message_history
        )

    def _update_context_label(self) -> None:
        """Update the context usage label under the input box using Pydantic AI's native msg.usage."""
        msgs = self._current_messages()
        (
            last_input_tokens,
            last_output_tokens,
            last_reasoning_tokens,
            total_session_tokens,
            last_turn_cost,
            has_usage,
        ) = _collect_token_usage(msgs)
        # The run's own aggregated usage is the authoritative per-turn total:
        # all_messages() includes prior turns' responses, and the
        # last-response-only extraction undercounts multi-request turns. The
        # context label's main number (last_input_tokens) keeps the
        # last-response semantic — it is the context size at the end of the
        # turn.
        last_output_tokens, last_reasoning_tokens = _run_usage_output_override(
            self._active_run, last_output_tokens, last_reasoning_tokens
        )
        last_turn_cost, has_usage = _run_usage_cost_override(
            self._active_run, last_turn_cost, has_usage
        )

        active_provider = self._active_provider or ""
        active_model = self._active_model or ""
        # Read a cached value only. This runs inside the agent.iter() node
        # loop — after every node — and resolve_model_context_length makes a
        # blocking 3s HTTP request on a cache miss, which stalled the unified
        # GTK+asyncio loop mid-stream and did it again whenever the 60s
        # negative-cache TTL expired. The refresh happens off-loop instead,
        # scheduled once per (provider, model).
        max_context = self._context_window_cache.get((active_provider, active_model))
        if max_context is None and (active_provider, active_model) not in self._context_window_probed:
            self._schedule_context_window_probe(active_provider, active_model)

        pct: float | None = None
        if not msgs or last_input_tokens == 0:
            text = f"0 / {format_tokens(max_context)} tok" if max_context else "0 tok"
        else:
            if max_context:
                pct = min(100.0, (last_input_tokens / max_context) * 100)
                text = (
                    f"{format_tokens(last_input_tokens)} / {format_tokens(max_context)} tok ({pct:.0f}%)"
                )
            else:
                text = f"{format_tokens(last_input_tokens)} tok"

        if has_usage:
            cost_text = (
                f"Cost: {_format_native_cost(last_turn_cost)}"
                if last_turn_cost is not None
                else "Cost: N/A"
            )
            text = f"{text} · {cost_text}"

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
        self._context_label.set_text(text)
        reasoning_str = (
            f" ({last_reasoning_tokens:,} reasoning)" if last_reasoning_tokens else ""
        )
        self._context_label.set_tooltip_text(
            f"Active model: {active_model or 'default'}\n"
            f"Provider: {active_provider or 'unknown'}\n"
            f"Last turn input context: {last_input_tokens:,} tokens\n"
            f"Last turn output: {last_output_tokens:,} tokens{reasoning_str}\n"
            f"Total session tokens: {total_session_tokens:,} tokens\n"
            f"Native Pydantic AI last-turn cost: "
            f"{_format_native_cost(last_turn_cost) if last_turn_cost is not None else 'unavailable for one or more provider/model responses'}\n"
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

        # Elapsed-time indicator shown ONLY while a model request is in
        # flight (see _model_wait_start). Answers "is it dead or thinking?"
        # during long server-side queues — verified live: a 4-minute silent
        # queue on ollama_cloud read as a dead chat.
        self._wait_label = Gtk.Label(label="")
        self._wait_label.set_no_show_all(True)
        self._wait_label.get_style_context().add_class("dim-label")
        self._wait_label.set_halign(Gtk.Align.END)
        self._wait_label.set_valign(Gtk.Align.CENTER)
        bar.pack_end(self._wait_label, False, False, 0)

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

    # -- model-wait elapsed indicator --------------------------------------
    # One uniform rule: the label is visible exactly while a model request
    # is awaited in the turn loop (start before `await _stream_request`, stop
    # in the finally). Tool execution shows its own expanders — no timer
    # there.

    def _model_wait_start(self) -> None:
        if self._wait_timer_id is not None:
            return
        self._wait_started = time.monotonic()
        self._update_wait_label()
        self._wait_label.show()
        self._wait_timer_id = GLib.timeout_add_seconds(1, self._on_wait_tick)

    def _on_wait_tick(self) -> bool:
        self._update_wait_label()
        return GLib.SOURCE_CONTINUE

    def _update_wait_label(self) -> None:
        secs = max(0, int(time.monotonic() - self._wait_started))
        text = f"{secs}s" if secs < 60 else f"{secs // 60}m{secs % 60:02d}s"
        self._wait_label.set_text(f"Waiting for model\u2026 {text}")

    def _model_wait_stop(self) -> None:
        if self._wait_timer_id is not None:
            GLib.source_remove(self._wait_timer_id)
            self._wait_timer_id = None
        self._wait_label.hide()

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
        from .adapter import build_status

        # build_status() returns a snapshot: the worker thread may add a
        # domain entry concurrently, and iterating the live dict raises.
        building_msg: str | None = None
        for domain, entry in build_status().items():
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

            from .agent_factory import make_summarizing_strategy

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

    def set_blocks_expanded(self, expanded: bool) -> None:
        self._blocks_expanded = expanded
        icon = "pan-start-symbolic" if expanded else "pan-end-symbolic"
        self._blocks_arrow.set_from_icon_name(icon, Gtk.IconSize.SMALL_TOOLBAR)
        self._blocks_toggle.set_tooltip_text(
            "Hide block library" if expanded else "Show block library"
        )

    def set_agents(
        self,
        executor: Agent[Any, Any],
        planner: Agent[Any, Any],
        model_error: str | None = None,
    ) -> None:
        """Install both roles while preserving the user's selected mode."""
        self._executor_agent = executor
        self._planner_agent = planner
        self._agent = planner if self._agent_mode == "planner" else executor
        self._update_agent_mode_label()
        self._model_build_error = model_error
        # Reflect the *running* agent's provider/model in the toolbar badge.
        # The provider is resolved from the model's base_url (not provider.name
        # — OllamaProvider.name returns "ollama" for both local and cloud, so
        # only base_url can tell them apart). See _PROVIDER_BASE_URL.
        model = getattr(executor, "model", None)
        transport_provider, base_url, model_name = describe_model(model)
        resolved_provider = ""
        if model is not None:
            resolved_provider = _resolve_provider_from_base_url(base_url)
            # A local Ollama on a custom port/LAN host has no ":11434" marker
            # in its URL — the transport's own provider name is the authority
            # (OllamaProvider.name is "ollama" for local and cloud alike, and
            # the URL already split cloud from local above).
            if (
                resolved_provider == "openai_compatible"
                and transport_provider == "ollama"
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

    def set_rebuild_agent_callback(self, cb: Callable[[], Any]) -> None:
        """Wire the live-swap entry point. desktop_app.py calls this once at
        startup with a closure over `build_agents_from_cfg(load_settings())`;
        the Settings dialog invokes it after a successful Save to apply the
        new provider/model/key to both roles immediately."""
        self._rebuild_agent = cb

    def _select_executor(self) -> None:
        """Reset role selection without dispatching a planner turn."""
        self._agent_mode = "executor"
        self._agent = self._executor_agent
        self._changing_agent_mode = True
        try:
            self._planner_toggle.set_active(False)
        finally:
            self._changing_agent_mode = False
        self._update_agent_mode_label()

    def _update_agent_mode_label(self) -> None:
        is_planner = self._agent_mode == "planner"
        ctx = self._planner_toggle.get_style_context()
        if is_planner:
            self._planner_toggle.set_label("Active:Planner")
            self._planner_toggle.set_tooltip_text(
                "Planner mode active (read-only plan generator). Click to switch to Agent mode."
            )
            ctx.remove_class("chat-mode-agent")
            ctx.add_class("chat-mode-planner")
        else:
            self._planner_toggle.set_label("Active:Agent")
            self._planner_toggle.set_tooltip_text(
                "Agent mode active (edits flowgraph & files). Click to switch to Planner mode."
            )
            ctx.remove_class("chat-mode-planner")
            ctx.add_class("chat-mode-agent")

    def _on_planner_toggled(self, button: Gtk.ToggleButton, _pspec: Any = None) -> None:
        if self._changing_agent_mode:
            return
        if self._busy:
            self._changing_agent_mode = True
            try:
                button.set_active(self._agent_mode == "planner")
            finally:
                self._changing_agent_mode = False
            return

        if button.get_active():
            if self._planner_agent is None:
                self._select_executor()
                self.set_status("Planner is not configured.", error=True)
                return
            self._agent_mode = "planner"
            self._agent = self._planner_agent
            self._update_agent_mode_label()
            if self._message_history:
                self._remove_implement_plan_action()
                self.set_status("Planner active — reviewing this conversation.")
                self.send_message(
                    "Create or revise a complete plan for the current request. "
                    "Do not execute it."
                )
            else:
                self.set_status("Planner active — describe what you want planned.")
                self.grab_entry_focus()
        else:
            self._agent_mode = "executor"
            self._agent = self._executor_agent
            self._update_agent_mode_label()
            self.set_status("GRC agent active — send a message when you want to execute the plan.")

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
        self._provider_label.set_text(f"{badge_label} · {short_model}")

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
        self.sync_to_file()

    @property
    def current_page(self) -> Any:
        if self._flowgraph_proxy is None:
            return None
        cm = self._get_cm()
        return cm.current_page if cm else None

    def sync_to_file(self) -> None:
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
        self._select_executor()
        self._render_history()

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

    def _refresh_welcome_times(self) -> bool:
        """Periodically re-render the welcome/recent-sessions list so the
        relative timestamps ("2m ago") stay fresh. Only runs when idle and the
        history is empty (the only state in which the list is visible); never
        disturbs a live chat stream."""
        if not self._busy and not self._message_history:
            self._render_history()
        return True  # re-arm

    def _render_welcome_screen(self) -> None:
        """Delegates to WelcomeView (welcome card + recent sessions)."""
        self._welcome.render(self.current_page, self._active_session_id)

    def _send_quick_prompt(self, text: str) -> None:
        if self._busy or self.current_page is None:
            return
        self._remove_implement_plan_action()
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

    def _get_effective_path(self) -> str | None:
        if self._flowgraph_proxy is None:
            return None
        cm = self._get_cm()
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
                # Off-thread like the save two lines above: this is the undo for
                # that same write, and it was the one SQLite call in this async
                # function still running on the GLib loop.
                await asyncio.to_thread(delete_session, new_id)
            except Exception:
                _log.exception("Failed to remove session resurrected by in-flight save")

    async def _archive_truncated_thinking(
        self,
        messages: list[ModelMessage],
        session_id: int | None,
        agent_mode: str,
    ) -> bool:
        """Preserve failed reasoning in StepPersistence before active-history cleanup."""
        if session_id is None:
            return False
        try:
            await archive_transcript(
                messages,
                conversation_id=conversation_id_for_session(session_id),
                agent_name=f"grc_{agent_mode}",
                kind="truncated_thinking_transcript",
            )
            return True
        except Exception:
            _log.exception("Failed to archive truncated thinking transcript")
            return False

    def _track_background_task(self, task: asyncio.Task) -> asyncio.Task:
        """Register a fire-and-forget task in the one set lifecycle reads.

        Returns the same task so a call site can still keep its own named
        reference where it needs to ask "is THIS kind of task running"
        (e.g. the implement-plan guard) — this only adds the task to the
        shared set that _cancel_background_tasks reads, so clear_messages
        and stop_chat stop hand-enumerating the same task attributes.
        """
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    def _cancel_background_tasks(self) -> None:
        """Cancel every still-running task this sidebar has dispatched.

        One list, read by both clear_messages (a global Clear History) and
        stop_chat (app shutdown) — previously each hand-enumerated the same
        four named attributes.
        """
        for task in list(self._background_tasks):
            if not task.done():
                task.cancel()

    def stop_chat(self) -> None:
        self._cancel_background_tasks()
        self._implement_plan_task = None

    def _remove_timers(self) -> None:
        """Disarm the two repeating sources __init__ armed.

        Both return True forever, so nothing ever removed them: a destroyed
        sidebar went on polling the default context, and with enough of them
        armed a `while Gtk.events_pending()` drain never runs dry. That is what
        made the GTK suite order-dependent.
        """
        for attr in ("_welcome_timer_id", "_indexing_timer_id"):
            source_id = getattr(self, attr, None)
            if source_id is not None:
                GLib.source_remove(source_id)
                setattr(self, attr, None)

    def destroy(self) -> None:
        """Remove this sidebar's timers before GTK tears the widget down."""
        self._remove_timers()
        super().destroy()

    def shutting_down(self) -> None:
        """Signal that the app is shutting down — any in-flight widget cleanup
        (streaming flush, scroll-to-bottom, busy reset) should be skipped to
        avoid GTK warnings/crashes on mid-destroy widgets (L7)."""
        self._shutting_down = True
        self._remove_timers()
        self._model_wait_stop()
        if self._md is not None:
            self._md.set_shutting_down(True)

    def _get_cm(self):
        """Resolve the live canvas manager (it changes across tab switches)."""
        return (
            getattr(self._flowgraph_proxy, "_canvas_manager", None)
            if self._flowgraph_proxy
            else None
        )

    async def _recover_history_after_failure(
        self,
        active_run: Any,
        *,
        session_id: int | None,
        agent_mode: str,
        fallback_text: str | Sequence[UserContent],
    ) -> bool:
        """Salvage a failed turn's messages into `_message_history`.

        Returns True when a truncated-thinking tail was archived and dropped, so
        the caller can explain that specific failure. The cancel and exception
        paths of `_run_agent_turn` each carried a verbatim copy of this sequence;
        with no run to salvage from, or if the salvage itself fails, the user's
        prompt is re-remembered so it is not lost from the history.
        """
        if active_run is None:
            self._remember_user_message(fallback_text)
            return False
        try:
            failed_messages = _clean_message_history_for_new_turn(active_run.all_messages())
            cleaned_messages, had_truncated_thinking = _without_truncated_thinking_tail(
                failed_messages
            )
            archived = False
            if had_truncated_thinking and await self._archive_truncated_thinking(
                failed_messages, session_id, agent_mode
            ):
                failed_messages = cleaned_messages
                archived = True
            self._message_history = failed_messages
            return archived
        except Exception:
            self._remember_user_message(fallback_text)
            return False

    def _archive_agent_name(self) -> str:
        """StepPersistence's agent name for the active role — the same value
        `agent_factory` passes as `agent_name=`, derived in one place."""
        return f"grc_{self._agent_mode}"

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
        self._fix_task = self._track_background_task(
            asyncio.ensure_future(self._send_fix_when_free(prompt, origin_page))
        )

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
        while self._busy:
            await self._idle_event.wait()
        if self.current_page is not origin_page:
            self.set_status(
                "Auto-fix cancelled \u2014 you switched flowgraphs. Re-open the failed flowgraph and try again.",
                error=True,
            )
            return
        if not self.send_message(text):
            _log.warning("Failed to dispatch auto-fix message despite idle event")
            self.set_status("Flowgraph run failed. Check console or send message to diagnose.", error=True)

    async def _run_agent_turn(self, prompt: str | Sequence[UserContent]) -> None:  # noqa: C901
        rich_rendered = False
        origin_page = self.current_page
        origin_gen = self._clear_generation
        origin_agent_mode = self._agent_mode
        ctx: _StreamCtx | None = None
        active_run: Any = None
        try:
            if self._agent is None:
                self._append_error("No agent configured.")
                return

            # Create the session row off the unified loop (the same
            # asyncio.to_thread rule _save_history follows — never a blocking
            # SQLite INSERT on the GLib loop) BEFORE capturing the origin
            # session id, so conversation grouping, the plan handoff, and the
            # archive paths all see it. Payload: the user prompt included
            # inline — NOT by mutating _message_history. agent.iter(prompt, ...)
            # appends the prompt to the canonical history itself; if we pre-loaded
            # it into _message_history here, the success path's
            # run.result.all_messages() would contain the prompt TWICE (once
            # from our pre-load, once from pydantic-ai's own append) and
            # _render_history() would display it twice. Keeping
            # _message_history clean until the run completes avoids that
            # duplication (M2 fix).
            if self._active_session_id is None:
                path = self._get_effective_path()
                if path:
                    try:
                        history_with_prompt = [
                            *self._message_history,
                            user_request(prompt),
                        ]
                        self._active_session_id = await asyncio.to_thread(
                            save_session, None, path, history_with_prompt
                        )
                    except Exception as e:
                        _log.error("Failed to create new session in database: %s", e)
            origin_session_id = self._active_session_id

            try:
                cfg = load_settings()
                configured_provider = cfg.get("provider", self._active_provider)
            except Exception:
                configured_provider = self._active_provider

            key_var = _PROVIDER_API_KEY.get(configured_provider)
            if key_var and configured_provider not in _PROVIDER_KEY_OPTIONAL:
                key_val = resolve_key(key_var)
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

            # Human-in-the-loop approval loop: change_graph requires approval
            # (pydantic-ai requires_approval=True), so a run can END with a
            # DeferredToolRequests output before the model's final answer.
            # Persist that run's messages, surface the approval card(s), then
            # resume the SAME turn with the native deferred-tool results
            # (ToolApproved/ToolDenied) until the run reaches a final output.
            deferred_results: DeferredToolResults | None = None
            turn_required_approval = False
            while True:
                async with self._agent.iter(
                    prompt if deferred_results is None else None,
                    message_history=self._message_history,
                    deferred_tool_results=deferred_results,
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
                            self._model_wait_start()
                            try:
                                await self._stream_request(ctx, node, run)
                            finally:
                                self._model_wait_stop()
                        elif Agent.is_call_tools_node(node):
                            self._close_text(ctx)
                            self._close_thinking(ctx)
                            await self._stream_tools(ctx, node, run)
                        self._scroll_to_bottom()
                        node = await run.next(node)
                        self._update_context_label()

                if run.result is None or not isinstance(
                    run.result.output, DeferredToolRequests
                ):
                    break
                # A change_graph call is pending approval. The run's messages
                # (including the unapproved call) are persisted now so a crash
                # mid-approval keeps the transcript; the next turn strips the
                # unfulfilled trailing call. The approval cards live in the
                # streaming row and are transient — the final transcript is
                # rebuilt from canonical history below.
                self._message_history = run.result.all_messages()
                await self._save_history()
                deferred_results = await self._request_approvals(ctx, run.result.output)
                prompt = None
                turn_required_approval = True

            if run.result is not None:
                planner_wrote_plan = origin_agent_mode == "planner" and _messages_call_tool(
                    run.result.new_messages(), "write_plan"
                )
                self._message_history = run.result.all_messages()
                await self._save_history()
                if turn_required_approval:
                    # The turn spanned an approval pause; rebuild the
                    # transcript from canonical history so the first run's
                    # tool calls (and the resumed final answer) both render —
                    # the streaming row carried the transient approval cards.
                    self._render_history()
                else:
                    self._replace_streaming_turn(ctx, run.result.new_messages())
                if planner_wrote_plan and origin_session_id is not None:
                    await self._show_implement_plan_if_ready(origin_session_id)
                rich_rendered = True
        except asyncio.CancelledError:
            if self.current_page is origin_page and self._clear_generation == origin_gen:
                await self._recover_history_after_failure(
                    active_run,
                    session_id=origin_session_id,
                    agent_mode=origin_agent_mode,
                    fallback_text=prompt,
                )
                # Tracked like every other fire-and-forget: a bare
                # ensure_future here was invisible to _cancel_background_tasks,
                # so a racing clear orphaned the handle (U3/F-04).
                self._track_background_task(asyncio.ensure_future(self._save_history()))
                self._append_error("[aborted]", style="aborted")
                rich_rendered = True
            raise
        except Exception as e:
            _log.exception("agent run failed")
            if self.current_page is origin_page:
                truncated_thinking_archived = await self._recover_history_after_failure(
                    active_run,
                    session_id=origin_session_id,
                    agent_mode=origin_agent_mode,
                    fallback_text=prompt,
                )
                await self._save_history()
                if truncated_thinking_archived:
                    self._append_error(
                        "Model reasoning repeated until the provider output limit. "
                        "The full failed trace was archived, and the unusable repetition was removed "
                        "from active context. Send Continue to resume from the completed tool steps."
                    )
                else:
                    self._append_error(_format_turn_error(e))
                rich_rendered = True
        finally:
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
                self._render_markdown_to_box(ctx.box, str(ctx.full_raw_text))
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
        # Belt-and-braces: the turn loop's finally already stops the timer
        # (including on cancellation, which unwinds through it); this catches
        # any future path that ends a task without unwinding the loop. Note
        # task.cancelled() returns early above — a cancelled task's timer is
        # stopped by that finally, not here.
        self._model_wait_stop()

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        if hasattr(self, "_idle_event"):
            if busy:
                self._idle_event.clear()
            else:
                self._idle_event.set()
        can_type = self._flowgraph_proxy is not None
        self._gear_btn.set_sensitive(not busy)
        self._new_session_btn.set_sensitive(not busy)
        if hasattr(self, "_theme_btn"):
            self._theme_btn.set_sensitive(not busy)
        self._compact_btn.set_sensitive(not busy and bool(self._message_history))
        self._planner_toggle.set_sensitive(not busy)
        self._approval_toggle.set_sensitive(not busy)
        if self._implement_plan_button is not None:
            self._implement_plan_button.set_sensitive(not busy)
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
                toplevel = self.get_toplevel()
                focus = toplevel.get_focus() if isinstance(toplevel, Gtk.Window) else None
                if focus in (None, self._entry.tv, self._send_btn):
                    self._entry.grab_focus()

    def _on_scroll_value_changed(self, adj: Gtk.Adjustment) -> None:
        """Recompute stick-to-bottom intent from the scroll position.

        The single authority on ``_auto_scroll``. Connected to the
        vadjustment's ``value-changed`` so every user-driven scroll source
        (wheel, scrollbar drag, keyboard, touch/kinetic) is handled by one
        uniform rule — the previous ``scroll-event`` handler missed scrollbar
        drags and keyboard scrolling entirely, so a user reading upstream
        content was yanked back to the bottom on the next streaming flush.
        Content growth never fires ``value-changed`` (only ``changed``), so
        streaming appends cannot corrupt the intent flag.
        """
        near_bottom = _is_near_bottom(adj)
        self._auto_scroll = near_bottom

    def _on_expander_toggled(self, exp: Gtk.Expander, _pspec: Any) -> None:
        """Shared ``notify::expanded`` handler for thinking/tool expanders.

        GTK keeps the viewport anchored to the adjustment *value*, so a row
        growing above the fold pushes all visible content down and the view
        jumps ("expands and the chat scrolls somewhere else"). Compensate by
        shifting the value by the row's bottom-edge delta so the visible
        content stays anchored — the same compensation Polari applies when
        older log entries are prepended above the viewport. Growth at/below
        the fold extends downward and needs no compensation.

        The re-layout must come from the ScrolledWindow: the ListBox's own
        ``check_resize`` compares requisition vs its (viewport-fixed)
        allocation and only queues a resize, so rows never re-allocate there;
        ``self._scrolled.check_resize()`` re-allocates the scrollable child
        synchronously, which is also what makes the post-toggle allocation
        read below valid.
        """
        row = exp.get_ancestor(Gtk.ListBoxRow)
        adj = self._scrolled.get_vadjustment() if self._scrolled is not None else None
        if row is not None and adj is not None and row.get_allocated_height() > 0:
            before = row.get_allocation()
            value_before = adj.get_value()
            self._scrolled.check_resize()
            after = row.get_allocation()
            delta = (after.y + after.height) - (before.y + before.height)
            # A row ending at/above the viewport top shifts the visible
            # content when it grows; anything at/below it extends downward.
            if delta != 0 and before.y + before.height <= value_before:
                adj.set_value(value_before + delta)
        else:
            self._scrolled.check_resize()
        if exp.get_expanded():
            # Reveal the expanded content for users pinned to the bottom;
            # everyone else keeps their position (gated by _auto_scroll).
            self._scroll_to_bottom()

    def _scroll_to_bottom(self, *, force: bool = False) -> None:
        def _do_scroll():
            sw = self._scrolled
            if sw is None:
                return False
            adj = sw.get_vadjustment()
            # Skip if the user scrolled up to read (unless explicitly forced,
            # e.g. after a full rebuild or message send). The _auto_scroll flag
            # is recomputed by _on_scroll_value_changed on every vadjustment
            # value-changed — not inferred here from the adjustment position,
            # which death-spiraled during
            # streaming (content grew >80px between flushes → every subsequent
            # scroll was skipped → gap only grew).
            if not force and not self._auto_scroll:
                return False
            adj.set_value(adj.get_upper() - adj.get_page_size())
            return False

        GLib.idle_add(_do_scroll)

    # -- chat zoom input + sidebar font projection (R9/R10, KD2) -----------

    def _on_chat_scroll_event(self, _sw: Gtk.ScrolledWindow, event: Gdk.Event) -> bool:
        """KTD9: the message ScrolledWindow's ONE zoom-only scroll handler.

        Control-masked wheel UP/DOWN is a zoom gesture for the CANVAS: one
        step per wheel tick through GRC's own DrawingArea.zoom_in()/
        zoom_out() (the same native methods GRC's View menu uses), returned
        True so the gesture is consumed — the chat itself must not scroll
        mid-zoom. Everything else (plain wheel, smooth deltas, horizontal)
        returns False and falls through to the ScrolledWindow's native
        scrolling and, through it, to the single-authority intent logic on
        the vadjustment.

        One-directional by construction: this handler never writes sidebar
        styles (they follow the canvas via on_zoom_changed →
        set_zoom_projection), never calls grab_focus, and never touches
        ``_auto_scroll`` — the consumed event changes no adjustment value, so
        the intent authority never even fires (same standing rule as the
        rest of this widget: handlers here must not own scroll intent).
        """
        if not event.state & Gdk.ModifierType.CONTROL_MASK:
            return False
        direction = getattr(event, "direction", None)
        if direction not in (Gdk.ScrollDirection.UP, Gdk.ScrollDirection.DOWN):
            return False
        cm = self._get_cm()
        drawing_area = getattr(cm, "drawing_area", None) if cm is not None else None
        if drawing_area is not None:
            if direction == Gdk.ScrollDirection.UP and hasattr(drawing_area, "zoom_in"):
                drawing_area.zoom_in()
            elif direction == Gdk.ScrollDirection.DOWN and hasattr(
                drawing_area, "zoom_out"
            ):
                drawing_area.zoom_out()
        # Consume even when no canvas is reachable: the gesture was a zoom
        # attempt, and the transcript must not scroll for it either way.
        return True

