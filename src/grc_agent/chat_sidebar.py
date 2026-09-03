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
from collections.abc import Callable
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
)

from .agent_factory import describe_model
from .chat.approvals import ApprovalsMixin
from .chat.composer import ComposerMixin
from .chat.constants import _is_near_bottom
from .chat.session import SessionMixin
from .chat.settings_controller import SettingsControllerMixin
from .chat.status_view import StatusContextMixin
from .chat.stream_view import StreamViewMixin
from .chat.transcript_view import TranscriptViewMixin
from .chat.turn_driver import TurnDriverMixin
from .chat.zoom_projection import ZoomProjectionMixin
from .db import (
    archive_transcript,
    conversation_id_for_session,
)
from .settings import (
    get_env_value,
    get_theme_mode,
    load_settings,
    set_theme_mode,
    upsert_env_key,
)
from .ui.css import apply_css as _apply_css
from .ui.css import apply_theme, is_dark_theme
from .ui.markdown_view import MarkdownView
from .ui.providers import PROVIDER_BADGE_LABEL as _PROVIDER_BADGE_LABEL
from .ui.providers import PROVIDER_LABELS as _PROVIDER_LABELS
from .ui.providers import resolve_provider_from_base_url as _resolve_provider_from_base_url
from .ui.welcome_view import WelcomeView

_log = logging.getLogger(__name__)


class ChatSidebar(
    StreamViewMixin,
    TranscriptViewMixin,
    ComposerMixin,
    ApprovalsMixin,
    ZoomProjectionMixin,
    SettingsControllerMixin,
    SessionMixin,
    StatusContextMixin,
    TurnDriverMixin,
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


    def _archive_agent_name(self) -> str:
        """StepPersistence's agent name for the active role — the same value
        `agent_factory` passes as `agent_name=`, derived in one place."""
        return f"grc_{self._agent_mode}"

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

