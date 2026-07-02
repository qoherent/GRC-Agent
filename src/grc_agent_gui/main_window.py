import json
import logging
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from grc_agent.config import (
    DEFAULT_OLLAMA_URL,
    DEFAULT_OPENROUTER_URL,
    default_openrouter_model,
)
from grc_agent.domain_models import ValidationStatus
from grc_agent.runtime.model_context import GRAPH_MUTATING_TOOL_NAME
from grc_agent.sessions_store import open_session_store
from PySide6.QtCore import (
    QEvent,
    QObject,
    QRunnable,
    QSettings,
    Qt,
    QThread,
    QThreadPool,
    QUrl,
    Signal,
)
from PySide6.QtGui import QAction, QCloseEvent, QDesktopServices, QIcon
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from .chat_widget import ChatWidget, strip_think_blocks
from .inspector import InspectorWidget
from .model_toolbar import ModelToolbar
from .process_manager import ProcessManager
from .sidebar_widget import SidebarWidget
from .ui_constants import (
    BACKEND_STATUS_MARKER,
    COLOR_BASE,
    COLOR_BLUE,
    COLOR_GREEN,
    COLOR_RED,
    COLOR_SUBTEXT,
    COLOR_SURFACE,
    COLOR_TEXT,
    COLOR_YELLOW,
    INVALID_ICON,
    MODEL_SELECTOR_MARKER,
    SPLITTER,
    UNVALIDATED_ICON,
    VALID_ICON,
)
from .workers import AgentWorker

logger = logging.getLogger(__name__)


# Public, user-facing constants surfaced in the About dialog and in
# ``grc-agent paths`` -- keep in sync with ``pyproject.toml``.
APP_NAME = "GRC Agent"
APP_DISPLAY_NAME = "GRC Agent Companion"
APP_ORGANIZATION = "Qoherent"
APP_HOMEPAGE_URL = "https://github.com/qoherent/grc-agent"
APP_LICENSE_NAME = "MIT"
APP_LICENSE_URL = "https://opensource.org/licenses/MIT"


def _get_app_version() -> str:
    """Return the installed grc-agent version, or 'unknown' if not installed."""
    try:
        from importlib.metadata import version

        return version("grc-agent")
    except Exception:
        return "unknown"


def _default_sessions_db() -> "Path":
    """Return the default sessions DB path. Module-level for
    import-time access from inner functions."""
    from grc_agent.sessions_store import default_sessions_db_path

    return default_sessions_db_path()


# ---------------------------------------------------------------------------
# Slash-command dispatch (uniform rule — one canonical '/' prefix)
# ---------------------------------------------------------------------------
# Each handler is a bound-method lookup keyed on the canonical command name.
# Forward-slash only; the legacy backslash alternates were a per-command
# special case and have been removed. Add new commands here, not inline.


def _slash_command_name(prompt: str) -> str | None:
    """Return the canonical slash-command name (e.g. 'save') or None.

    Recognises only a single leading '/'; any other input (including the
    legacy '\\save' form) returns None and falls through to the agent.
    """
    if not prompt.startswith("/"):
        return None
    token = prompt[1:].split(maxsplit=1)[0]
    return token.lower() if token else None


def _slash_save(window: "MainWindow") -> None:
    window.save_file()


def _slash_refresh_model(window: "MainWindow") -> None:
    window._on_toolbar_refresh()


_SLASH_COMMANDS: dict[str, Callable[["MainWindow"], None]] = {
    "save": _slash_save,
    "model": _slash_refresh_model,
    "client": _slash_refresh_model,
}


class InspectorWorkerSignals(QObject):
    finished = Signal(dict)
    error = Signal(str)


class ModelSwapWorkerSignals(QObject):
    finished = Signal(object)
    error = Signal(str)
    progress = Signal(str)


class ModelSwapRunnable(QRunnable):
    """Background swap of the model / backend.

    Handles Ollama model discovery/pull and OpenRouter configuration,
    then probes the new backend via bootstrap_runtime.
    """

    def __init__(
        self,
        llama_config: Any,
        backend: str,
        ollama_model_name: str | None = None,
    ) -> None:
        super().__init__()
        self.llama_config = llama_config
        self.backend = backend
        self.ollama_model_name = ollama_model_name
        self.signals = ModelSwapWorkerSignals()

    def run(self) -> None:
        try:
            import dataclasses

            from grc_agent.config import AppConfig, default_app_config
            from grc_agent.startup import bootstrap_runtime

            new_url = getattr(self.llama_config, "server_url", DEFAULT_OLLAMA_URL)
            new_model = getattr(self.llama_config, "model", "")

            if self.backend == "ollama":
                new_url = DEFAULT_OLLAMA_URL

                model_name = (
                    self.ollama_model_name
                    or getattr(self.llama_config, "model", "")
                    or default_app_config().llama.model
                )

                self.signals.progress.emit(f"Checking Ollama model '{model_name}'...")

                # Check if model exists locally
                try:
                    from grc_agent.model_manager import discover_ollama_models

                    local_models = discover_ollama_models(new_url)
                except Exception:
                    local_models = []

                from grc_agent.toolagents_runtime import model_name_matches

                model_exists = bool(model_name) and model_name_matches(model_name, local_models)

                if not model_exists:
                    self.signals.progress.emit(f"Pulling model '{model_name}'...")
                    try:
                        from grc_agent.model_manager import pull_ollama_model

                        pull_result = pull_ollama_model(model_name, server_url=new_url)
                        if not pull_result.get("ok"):
                            raise Exception(pull_result.get("error", "Unknown pull error"))
                    except Exception as exc:
                        raise Exception(
                            f"Failed to pull Ollama model '{model_name}': {exc}"
                        ) from exc
                    self.signals.progress.emit(f"Model '{model_name}' pulled successfully.")

                new_model = model_name

                # Probe tool support
                try:
                    from grc_agent.model_manager import check_ollama_tool_support

                    tool_ok = check_ollama_tool_support(new_url, new_model)
                    if tool_ok is False:
                        logger.warning(
                            "Ollama model '%s' does not support tool calling. "
                            "Its chat template lacks {{ .Tools }}.",
                            new_model,
                        )
                except Exception:
                    pass

            elif self.backend == "openrouter":
                new_url = DEFAULT_OPENROUTER_URL
                new_model = default_openrouter_model()

            target_llama_config = dataclasses.replace(
                self.llama_config,
                backend=self.backend,
                server_url=new_url,
                model=new_model,
            )

            app_config = AppConfig(
                llama=target_llama_config,
                agent=default_app_config().agent,
            )

            self.signals.progress.emit("Probing backend...")
            result = bootstrap_runtime(app_config, init_retrieval=False)
            if result.errors:
                raise Exception(", ".join(result.errors))
            self.signals.finished.emit(result)
        except Exception as exc:
            logger.exception("Backend/model swap failure")
            self.signals.error.emit(f"Swap failed: {exc}")


class InspectorRunnable(QRunnable):
    def __init__(self, agent: Any) -> None:
        super().__init__()
        self.agent = agent
        self.signals = InspectorWorkerSignals()

    def run(self) -> None:
        try:
            from grc_agent.runtime.inspect_graph import inspect_graph

            overview_data = inspect_graph(self.agent, view="overview", targets=[])
            self.signals.finished.emit(overview_data)
        except Exception as e:
            self.signals.error.emit(str(e))


class MainWindow(QMainWindow):
    """Main window for the GRC Agent desktop UI sidekick."""

    def __init__(
        self,
        agent: Any,
        provider_config: Any = None,
        llama_config: Any = None,
        parent: QWidget = None,
        *,
        bootstrap_result: Any = None,
    ) -> None:
        super().__init__(parent)
        self.agent = agent
        self.provider_config = provider_config
        self.llama_config = llama_config
        self.worker = None
        self._pending_swap_selection = None
        self.thread = None
        self.process_manager = None
        self.active_session_id = None
        self.sessions_store = open_session_store(_default_sessions_db())
        # Backend reachability state. ``None`` means "unknown / not yet
        # probed". ``True`` = healthy. ``False`` = degraded. The
        # ``bootstrap_result`` is consulted once at construction; live
        # mid-session failures are tracked via ``on_backend_unreachable``
        # callbacks wired into the agent worker.
        self.backend_reachable: bool | None = None
        self._backend_unreachable_hint: str | None = None
        if bootstrap_result is not None and getattr(bootstrap_result, "launch_status", "") in {
            "probe_failed",
            "failed",
        }:
            self.backend_reachable = False
            errs = list(getattr(bootstrap_result, "errors", []) or [])
            self._backend_unreachable_hint = errs[0] if errs else "Backend unreachable."

        self.setWindowTitle(f"{APP_DISPLAY_NAME} {_get_app_version()}")
        icon_path = Path(__file__).parent / "resources" / "icon.png"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        # Add File Menu
        menubar = self.menuBar()
        file_menu = menubar.addMenu("&File")

        open_action = QAction("&Open...", self)
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self.open_file_dialog)
        file_menu.addAction(open_action)

        self.save_action = QAction("&Save", self)
        self.save_action.setShortcut("Ctrl+S")
        self.save_action.triggered.connect(self.save_file)
        file_menu.addAction(self.save_action)

        file_menu.addSeparator()

        self.export_chat_action = QAction("&Export Chat...", self)
        self.export_chat_action.setShortcut("Ctrl+E")
        self.export_chat_action.triggered.connect(self.export_chat_dialog)
        file_menu.addAction(self.export_chat_action)

        open_output_action = QAction("Open &Output Folder", self)
        open_output_action.triggered.connect(self.open_output_folder)
        file_menu.addAction(open_output_action)

        file_menu.addSeparator()
        self.recent_sessions_action = QAction("Session Side&bar", self)
        self.recent_sessions_action.setShortcut("Ctrl+Shift+H")
        self.recent_sessions_action.triggered.connect(self.toggle_sidebar)
        file_menu.addAction(self.recent_sessions_action)

        # View Menu
        view_menu = menubar.addMenu("&View")

        zoom_in_action = QAction("Zoom &In", self)
        zoom_in_action.setShortcuts(["Ctrl++", "Ctrl+="])
        zoom_in_action.triggered.connect(self.zoom_in)
        view_menu.addAction(zoom_in_action)

        zoom_out_action = QAction("Zoom &Out", self)
        zoom_out_action.setShortcut("Ctrl+-")
        zoom_out_action.triggered.connect(self.zoom_out)
        view_menu.addAction(zoom_out_action)

        zoom_reset_action = QAction("Reset &Zoom", self)
        zoom_reset_action.setShortcut("Ctrl+0")
        zoom_reset_action.triggered.connect(self.zoom_reset)
        view_menu.addAction(zoom_reset_action)

        # Help Menu
        help_menu = menubar.addMenu("&Help")
        about_action = QAction("&About GRC Agent", self)
        about_action.triggered.connect(self.show_about_dialog)
        help_menu.addAction(about_action)

        open_docs_action = QAction("Open &Docs Folder", self)
        open_docs_action.triggered.connect(self.open_docs_folder)
        help_menu.addAction(open_docs_action)

        self._settings = QSettings("GRC_Agent", "GUI")
        self._zoom_factor = float(self._settings.value("window/zoom_factor", 3.5))
        geom = self._settings.value("window/geometry")
        if geom:
            self.restoreGeometry(geom)
        else:
            self.resize(800, 600)

        # Status Bar (moved to top to prevent initialization order issues with status bar widgets)
        self.status_bar = QStatusBar(self)
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Ready")

        # Permanent (never overwritten by transient tool-name messages)
        # indicator for whether the agent is currently generating.
        self.generation_status_label = QLabel(self)
        self._set_generation_status_label(False)
        self.status_bar.addPermanentWidget(self.generation_status_label)

        self.connection_status_label = QLabel("● checking", self)
        self.connection_status_label.setStyleSheet(f"color: {COLOR_YELLOW};")
        self.status_bar.addPermanentWidget(self.connection_status_label)

        self.model_status_label = QLabel(self)
        self.status_bar.addPermanentWidget(self.model_status_label)
        self._update_model_status_label()

        self.validation_label = QLabel("Unknown", self)
        self.status_bar.addPermanentWidget(self.validation_label)

        # Central layout initialization
        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)

        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)

        # Instantiate primary vertical splitter
        v_splitter = QSplitter(Qt.Vertical, central_widget)
        self.v_splitter = v_splitter
        # NOTE: v_splitter is added to main_layout below the model toolbar.

        # Instantiate horizontal splitter for upper sidepanels
        splitter = QSplitter(Qt.Horizontal, v_splitter)
        self.h_splitter = splitter

        # Instantiate Sidebar widget
        self.sidebar_widget = SidebarWidget(splitter)
        splitter.addWidget(self.sidebar_widget)

        # Instantiate ChatWidget sidekick pane
        self.chat_widget = ChatWidget(splitter)
        splitter.addWidget(self.chat_widget)

        # Instantiate InspectorWidget sidekick pane
        self.inspector_widget = InspectorWidget(splitter)
        splitter.addWidget(self.inspector_widget)

        # Connect sidebar signals
        self.sidebar_widget.session_selected.connect(self._open_past_session)
        self.sidebar_widget.new_chat_requested.connect(self.start_new_chat_session)
        self.sidebar_widget.collapse_requested.connect(self.toggle_sidebar)
        self.sidebar_widget.clear_all_requested.connect(self._on_clear_all_history)

        # Set stretch factors: only the Chat widget expands, sidebar and inspector keep fixed widths on resize.
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)

        # Set default splitter proportions (initial paint, before any saved
        # window state is restored — see SplitterProportions).
        total_width = self.width() or 800
        sidebar_w = max(
            SPLITTER.sidebar_min_px_initial,
            int(total_width * SPLITTER.sidebar_fraction_initial),
        )
        chat_w = int(total_width * SPLITTER.chat_fraction)
        inspector_w = max(SPLITTER.inspector_min_px, total_width - sidebar_w - chat_w)
        splitter.setSizes([sidebar_w, chat_w, inspector_w])
        v_splitter.addWidget(splitter)

        # Console Log Panel (lower pane)
        console_panel = QWidget(v_splitter)
        console_layout = QVBoxLayout(console_panel)
        console_layout.setContentsMargins(0, 5, 0, 0)

        console_controls = QHBoxLayout()
        console_controls.addWidget(QLabel("<b>Console Output</b>", console_panel))
        console_controls.addStretch()

        self.validate_btn = QPushButton("Validate", console_panel)
        console_controls.addWidget(self.validate_btn)
        console_layout.addLayout(console_controls)

        self.console_log = QPlainTextEdit(console_panel)
        self.console_log.setReadOnly(True)
        self.console_log.setPlaceholderText(
            "Flowgraph compilation and execution logs will appear here..."
        )
        self.console_log.setMaximumBlockCount(10000)
        console_layout.addWidget(self.console_log)

        v_splitter.addWidget(console_panel)
        v_splitter.setSizes([450, 150])

        # --- Inline model toolbar (replaces the setup wizard + Model dialog) ---
        current_backend = (
            getattr(self.llama_config, "backend", "ollama") if self.llama_config else "ollama"
        )
        current_model = getattr(self.llama_config, "model", "") if self.llama_config else ""
        if self.provider_config is not None:
            pm = getattr(self.provider_config, "model", "")
            if pm:
                current_model = str(pm)
        self.model_toolbar = ModelToolbar(
            backend=current_backend,
            model=current_model,
        )
        self.model_toolbar.connect_requested.connect(self._on_toolbar_connect)
        self.model_toolbar.refresh_requested.connect(self._on_toolbar_refresh)
        self.model_toolbar.open_graph_location_requested.connect(self._on_open_graph_location)
        self.model_toolbar.browse_graph_requested.connect(self.open_file_dialog)

        main_layout.addWidget(self.model_toolbar)
        main_layout.addWidget(v_splitter)

        # Aliases for backwards compatibility and automated testing
        self.chat_input = self.chat_widget.chat_input
        self.chat_display = self.chat_widget.chat_display
        self.chat_input.returnPressed.connect(self.send_prompt)
        self.chat_input.installEventFilter(self)
        self.chat_widget.stop_clicked.connect(self._on_stop_clicked)

        # Splitter states are restored in showEvent once window geometry is fully realized.

        # Initialize Process Manager
        self.process_manager = ProcessManager(self)

        # Wire execution buttons & process manager signals
        self.validate_btn.clicked.connect(self.on_validate_clicked)

        self.process_manager.started.connect(self.on_process_started)
        self.process_manager.stdout_received.connect(self.on_process_stdout)
        self.process_manager.stderr_received.connect(self.on_process_stderr)
        self.process_manager.status_message.connect(self.on_process_status)
        self.process_manager.finished.connect(self.on_process_finished)

        # Initialize inspector state and active GRC file path
        if self.agent.session and self.agent.session.path:
            self.inspector_widget.set_grc_file_path(str(self.agent.session.path))
        self.refresh_inspector()
        self.refresh_sidebar_sessions()

        self.update_ui_state()

        # Probe Ollama after the event loop starts (non-blocking).
        from PySide6.QtCore import QTimer

        QTimer.singleShot(0, self._probe_and_populate_models)

        self.apply_zoom(self._zoom_factor)

    def _resolve_model_status(self) -> str:
        """Build a single-line summary of the loaded model for the status bar."""
        cfg = getattr(self, "llama_config", None)
        backend = getattr(cfg, "backend", "ollama") if cfg is not None else "ollama"
        if not isinstance(backend, str):
            backend = "ollama"

        model_name = ""
        if cfg is not None:
            model_name = getattr(cfg, "model", "") or "unknown"

        provider = getattr(self, "provider_config", None)
        if provider is not None:
            provider_model = getattr(provider, "model", None)
            if provider_model:
                model_name = str(provider_model)

        return f"Client: {backend} · Model: {model_name}"

    def _update_model_status_label(self) -> None:
        """Render the model status string into the permanent status-bar label."""
        try:
            self.model_status_label.setText(self._resolve_model_status())
            self.model_status_label.setStyleSheet(f"color: {COLOR_TEXT};")
        except Exception as exc:
            logger.debug("Failed to render model status label: %s", exc)
            self.model_status_label.setText("Model: unknown")
            self.model_status_label.setStyleSheet(f"color: {COLOR_SUBTEXT};")

    def update_ui_state(self) -> None:
        """Enable or disable chat and validation controls based on active session state."""
        has_graph = self.agent.session is not None and self.agent.session.flowgraph is not None
        backend_ok = self.backend_reachable is not False
        self.chat_input.setEnabled(has_graph and backend_ok)
        self.validate_btn.setEnabled(has_graph and backend_ok)
        self.save_action.setEnabled(has_graph)
        self.export_chat_action.setEnabled(has_graph)

        if not backend_ok:
            self.chat_input.setPlaceholderText(
                "Backend unreachable — use the toolbar to retry or select a different model."
            )
            self.validation_label.setText("Backend Down")
            self.validation_label.setStyleSheet(f"color: {COLOR_RED};")
            self._render_backend_unreachable_banner()
        elif has_graph:
            self.chat_input.setPlaceholderText(
                "Ask the assistant to modify or summarize the flowgraph..."
            )
            self.validation_label.setText(f"{VALID_ICON} Valid")
            self.validation_label.setStyleSheet(f"color: {COLOR_GREEN};")
        else:
            self.chat_input.setPlaceholderText(
                "Please load a .grc flowgraph (File -> Open) to start chatting..."
            )
            self.validation_label.setText("No Graph")
            self.validation_label.setStyleSheet(f"color: {COLOR_SUBTEXT};")

    def zoom_in(self) -> None:
        self.apply_zoom(self._zoom_factor + 0.1)

    def zoom_out(self) -> None:
        self.apply_zoom(self._zoom_factor - 0.1)

    def zoom_reset(self) -> None:
        self.apply_zoom(1.0)

    def apply_zoom(self, zoom_factor: float) -> None:
        # Clamp zoom factor to reasonable boundaries to prevent UI layout bugs
        self._zoom_factor = max(0.5, min(4.0, zoom_factor))
        self._settings.setValue("window/zoom_factor", self._zoom_factor)

        # Regenerate and apply global stylesheet
        from PySide6.QtWidgets import QApplication

        from grc_agent_gui.styles import get_stylesheet, ui_font_metrics

        QApplication.instance().setStyleSheet(get_stylesheet(self._zoom_factor))

        # Apply the same font to the chat QTextBrowser document so that
        # HTML content (which is immune to Qt stylesheets) also scales.
        # chat_pt comes from the same ui_font_metrics() used by the
        # stylesheet — one source of truth for body / mono / small / chat.
        if hasattr(self, "chat_widget") and self.chat_widget is not None:
            from PySide6.QtGui import QFont

            metrics = ui_font_metrics(self._zoom_factor)
            chat_pt = metrics.chat_pt
            font = QFont("Ubuntu Sans", chat_pt)
            self.chat_widget.chat_display.document().setDefaultFont(font)
            # Push the same chat_pt + user_text_px down to the chat
            # widget so the user-message body div (which lives outside
            # the document font cascade) tracks zoom uniformly, and
            # the user text is consistently larger than the agent
            # body so the user's own input is the most prominent
            # text in the conversation.
            self.chat_widget.set_chat_pt(chat_pt, user_text_px=metrics.user_text_px)

        # Apply zoom to the model toolbar
        if hasattr(self, "model_toolbar") and self.model_toolbar is not None:
            self.model_toolbar.apply_zoom(self._zoom_factor)

        # Invalidate rendered chat cache and trigger re-render
        if hasattr(self, "chat_widget") and self.chat_widget is not None:
            for msg in self.chat_widget._history:
                msg["_rendered"] = None
            self.chat_widget._render_chat()

    def set_backend_connected(self, connected: bool | None) -> None:
        if not hasattr(self, "connection_status_label"):
            return
        if connected is None:
            self.connection_status_label.setText("● checking")
            self.connection_status_label.setStyleSheet(f"color: {COLOR_YELLOW};")
        elif connected:
            self.connection_status_label.setText("● connected")
            self.connection_status_label.setStyleSheet(f"color: {COLOR_GREEN};")
        else:
            self.connection_status_label.setText("● unreachable")
            self.connection_status_label.setStyleSheet(f"color: {COLOR_RED};")

    def _on_backend_unreachable(self, result: dict[str, Any]) -> None:
        """Worker callback: backend is unreachable, switch to degraded mode."""
        self.backend_reachable = False
        self._backend_unreachable_hint = str(result.get("assistant_text") or "Backend unreachable.")
        self.status_bar.showMessage(
            f"Backend unreachable at {self._server_url_display()} — chat disabled, "
            f"use the toolbar to retry or select a different model."
        )
        self.set_backend_connected(False)
        self.update_ui_state()

    def _on_backend_recovered(self) -> None:
        """Called after a successful model swap to leave degraded mode."""
        self.backend_reachable = True
        self._backend_unreachable_hint = None
        self._update_model_status_label()
        self.update_ui_state()

    def _server_url_display(self) -> str:
        url = getattr(self.llama_config, "server_url", "") or ""
        return url or "the configured backend"

    def _render_backend_unreachable_banner(self) -> None:
        """Render the platform-agnostic hint in the chat view exactly once.

        Idempotent: re-rendering does not stack banners. The hint is
        stored on ``self._backend_unreachable_hint`` so the model-swap
        recovery path can also clear it.
        """
        hint = self._backend_unreachable_hint
        if not hint:
            return
        history = self.chat_widget.get_history()
        for entry in history:
            if entry.get("text", "").startswith(BACKEND_STATUS_MARKER):
                return
        self.chat_widget.append_error(f"{BACKEND_STATUS_MARKER} {hint}")

    # ------------------------------------------------------------------
    # Model toolbar handlers (replace the setup wizard + Model dialog)
    # ------------------------------------------------------------------
    def _on_toolbar_connect(self, backend: str, model_name: str) -> None:
        """User picked a provider/model in the toolbar — run the swap."""
        if not model_name or model_name == "(select model)":
            return
        if self.thread is not None and self.thread.isRunning():
            self.status_bar.showMessage("Cannot swap model while a chat turn is running.")
            return
        if getattr(self, "llama_config", None) is None:
            self.status_bar.showMessage("Cannot swap model: no llama_config on this session.")
            return

        self._set_swap_in_progress(True)
        self.status_bar.showMessage(f"Connecting to {backend} ({model_name})...")

        from .model_dialog import ModelDialogSelection

        self._pending_swap_selection = ModelDialogSelection(
            backend=backend,
            ollama_model_name=model_name,
        )

        runnable = ModelSwapRunnable(
            llama_config=self.llama_config,
            backend=backend,
            ollama_model_name=model_name,
        )
        runnable.signals.finished.connect(self._on_model_swap_finished)
        runnable.signals.error.connect(self._on_model_swap_error)
        runnable.signals.progress.connect(self._on_model_swap_progress)
        QThreadPool.globalInstance().start(runnable)

    def _on_toolbar_refresh(self) -> None:
        """User clicked refresh — re-probe and repopulate the model list."""
        self._probe_and_populate_models()



    def _probe_and_populate_models(self) -> None:
        """Probe Ollama and populate the toolbar's model dropdown."""
        from grc_agent.model_manager import discover_ollama_models

        backend = self.model_toolbar.current_backend()
        if backend != "ollama":
            return

        server_url = getattr(self.llama_config, "server_url", DEFAULT_OLLAMA_URL)
        try:
            models = discover_ollama_models(server_url)
        except Exception as exc:
            logger.warning("Model discovery failed: %s", exc)
            models = []

        current = self.model_toolbar.current_model()
        self.model_toolbar.set_models(models, current=current or (models[0] if models else ""))
        if models:
            self.set_backend_connected(True)
            self.backend_reachable = True
        else:
            self.set_backend_connected(False)
            self.backend_reachable = False
        self.update_ui_state()

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if obj is self.chat_input and event.type() == QEvent.Type.KeyPress:
            if (
                event.key() == Qt.Key.Key_Return
                and event.modifiers() == Qt.KeyboardModifier.ControlModifier
            ):
                self.send_prompt()
                return True
            if event.key() == Qt.Key.Key_Escape:
                self.chat_input.clear()
                return True
        return super().eventFilter(obj, event)

    def showEvent(self, event) -> None:
        """Handle layout initialization after window geometry is fully realized."""
        super().showEvent(event)
        if not hasattr(self, "_first_shown"):
            self._first_shown = True
            total_w = self.width() or 800

            # Restore horizontal splitter state
            h_state = self._settings.value("window/h_splitter")
            if h_state:
                self.h_splitter.restoreState(h_state)
                sizes = self.h_splitter.sizes()
                if len(sizes) == 3:
                    # 1. Sidebar should be max SPLITTER.sidebar_fraction_max of
                    #    total width. If it's larger or collapsed, restore it.
                    max_sidebar_w = int(total_w * SPLITTER.sidebar_fraction_max)
                    if (
                        sizes[0] > max_sidebar_w
                        or sizes[0] < SPLITTER.sidebar_collapsed_floor_px
                    ) and not self.sidebar_widget.isHidden():
                        sizes[0] = max(
                            SPLITTER.sidebar_min_px_restored,
                            int(total_w * SPLITTER.sidebar_fraction_restored),
                        )

                    # 2. Ensure inspector (index 2) has a sensible size and was not collapsed by older 2-widget settings
                    if (
                        sizes[2] < SPLITTER.sidebar_collapsed_floor_px
                        and not self.inspector_widget.isHidden()
                    ):
                        sizes[2] = max(
                            SPLITTER.inspector_min_px,
                            int(total_w * SPLITTER.inspector_fraction),
                        )

                    # 3. Chat gets the remainder
                    sizes[1] = max(SPLITTER.chat_min_px, total_w - sizes[0] - sizes[2])
                    self.h_splitter.setSizes(sizes)
            else:
                sidebar_w = max(
                    SPLITTER.sidebar_min_px_restored,
                    int(total_w * SPLITTER.sidebar_fraction_restored),
                )
                chat_w = int(total_w * SPLITTER.chat_fraction)
                inspector_w = max(SPLITTER.inspector_min_px, total_w - sidebar_w - chat_w)
                self.h_splitter.setSizes([sidebar_w, chat_w, inspector_w])

            # Restore vertical splitter state
            v_state = self._settings.value("window/v_splitter")
            if v_state:
                self.v_splitter.restoreState(v_state)

    def closeEvent(self, event: QCloseEvent) -> None:
        """Cancel running worker, clean up thread, and close DB on close."""
        self._on_stop_clicked()
        self.cleanup_thread()
        if hasattr(self, "sessions_store") and self.sessions_store is not None:
            try:
                self.sessions_store.close()
            except Exception as exc:
                logger.warning("Failed to close sessions DB on window exit: %s", exc)
        event.accept()

    def _ensure_active_session_db_record(self, first_user_prompt: str) -> None:
        """Create a new session record in SQLite if one isn't currently active."""
        if self.active_session_id is not None:
            return

        # Get path and hash from active agent session
        graph_path = ""
        graph_hash = ""
        if self.agent.session and self.agent.session.path:
            graph_path = str(self.agent.session.path)
            if (
                hasattr(self.agent.session, "persisted_file_sha256")
                and self.agent.session.persisted_file_sha256
            ):
                graph_hash = self.agent.session.persisted_file_sha256
            elif self.agent.session.path.exists():
                try:
                    import hashlib

                    graph_hash = hashlib.sha256(self.agent.session.path.read_bytes()).hexdigest()
                except Exception:
                    graph_hash = "unknown"

        model_alias = getattr(self.provider_config, "model", None)
        model_alias = str(model_alias) if model_alias is not None else "unknown"
        backend = getattr(self.llama_config, "backend", "ollama")

        # Create a title using the first 40 chars of the user's prompt
        title = first_user_prompt[:40]
        if len(first_user_prompt) > 40:
            title += "..."

        try:
            self.active_session_id = self.sessions_store.open_session(
                graph_path=graph_path,
                graph_hash=graph_hash,
                model_alias=model_alias,
                backend=backend,
                title=title,
            )
            # Update the sidebar listing after creating a new session
            self.refresh_sidebar_sessions()
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to open new database session: %s", exc)
            self.active_session_id = None

    def on_model_message_added(self, role: str, payload_json: str) -> None:
        """Persist a typed model ``ChatMessage`` so resume can replay it.

        ``role`` is one of ``assistant_model`` or ``tool_model``.
        The text column is empty (it is display-only for these roles);
        the full ``ChatMessage`` lives in the ``payload`` column.
        """
        if self.active_session_id is None:
            return
        try:
            payload = json.loads(payload_json) if payload_json else None
        except (TypeError, ValueError):
            payload = None
        if payload is None:
            return
        try:
            self.sessions_store.append(
                self.active_session_id,
                role,
                "",
                payload=payload,
            )
        except Exception as exc:
            logger.exception("Failed to save model message to DB: %s", exc)

    def send_prompt(self) -> None:
        """Read the input box, format as a user message, and start worker generation."""
        prompt = self.chat_input.text().strip()
        if not prompt:
            return
        self.chat_input.clear()

        # Slash-command dispatch. One uniform rule: a single canonical '/'
        # prefix routes to a registered handler. Unknown commands fall
        # through to the agent.
        slash_handler = _SLASH_COMMANDS.get(_slash_command_name(prompt))
        if slash_handler is not None:
            self.chat_widget.append_message("user", prompt)
            slash_handler(self)
            return

        self.chat_widget.append_message("user", prompt)

        # Auto-save
        self._ensure_active_session_db_record(prompt)
        if self.active_session_id is not None:
            try:
                self.sessions_store.append(self.active_session_id, "user", prompt)
            except Exception as exc:
                logger.exception("Failed to save user prompt to DB: %s", exc)

        self.start_generation(prompt)

    def start_generation(self, prompt: str) -> None:
        """Initialize and run the AgentWorker inside a QThread."""
        if self.thread is not None and self.thread.isRunning():
            logger.warning("Agent worker thread is already running.")
            return

        self.thread = QThread(self)
        self.worker = AgentWorker(
            self.agent,
            prompt,
            self.provider_config,
            on_backend_unreachable=self._on_backend_unreachable,
        )
        self.worker.moveToThread(self.thread)

        # Setup signal routing
        self.thread.started.connect(self.worker.run_turn)

        self.worker.started.connect(self.on_worker_started)
        self.worker.tool_started.connect(self.on_tool_started)
        self.worker.tool_finished.connect(self.on_tool_finished)
        self.worker.response_chunk.connect(self.on_response_chunk)
        self.worker.model_message_added.connect(self.on_model_message_added)
        self.worker.turn_finished.connect(self.on_turn_finished)

        # Enforce dynamic cleanup sequence on turn completion
        self.worker.turn_finished.connect(self.cleanup_thread)

        self.thread.start()

    def on_worker_started(self) -> None:
        """Lock input interface to prevent race conditions during generation."""
        self.chat_input.setEnabled(False)
        if hasattr(self, "model_toolbar"):
            self.model_toolbar.setEnabled(False)
        self.chat_widget.set_generating(True)
        self._set_generation_status_label(True)
        self.status_bar.showMessage("Agent is thinking...")

    def _set_generation_status_label(self, is_generating: bool) -> None:
        """Persistent (never overwritten by tool-name messages) run-state indicator."""
        if is_generating:
            self.generation_status_label.setText("● Generating…")
            self.generation_status_label.setStyleSheet(f"color: {COLOR_BLUE};")
        else:
            self.generation_status_label.setText("○ Idle")
            self.generation_status_label.setStyleSheet(f"color: {COLOR_SUBTEXT};")

    def _on_stop_clicked(self) -> None:
        """User clicked Stop: cancel the in-flight worker, if any."""
        if self.worker is not None:
            self.worker.cancel()
            self.status_bar.showMessage("Stopping…")

    def on_tool_started(self, name: str, args: str) -> None:
        """Show the running tool name in the status bar and chat indicator.

        Finalize any in-flight assistant stream so tool calls render in
        the correct temporal position. The empty assistant placeholder
        (created by ``start_stream``) is intentionally NOT dropped —
        the chat widget's ``start_stream`` reuses it across
        ``Agent: text → tool call → result → Agent: text`` so the
        whole turn renders under one "Agent:" header.
        """
        if self.chat_widget._streaming:
            final = self.chat_widget.current_stream_text()
            visible = strip_think_blocks(final)
            if visible:
                self.chat_widget.finalize_stream(final)
            # else: keep the empty assistant entry — the next
            # ``start_stream`` reuses it for the post-tool text.

        self.status_bar.showMessage(f"-- {name}...")
        self.status_bar.setStyleSheet(
            f"QStatusBar {{ color: {COLOR_BLUE}; background-color: {COLOR_BASE}; "
            f"border-top: 1px solid {COLOR_SURFACE}; font-size: 0.9em; padding: 2px 8px; }} "
            "QStatusBar::item { border: none; }"
        )
        self.chat_widget.append_status(name, args)

        if self.active_session_id is not None:
            try:
                self.sessions_store.append(
                    self.active_session_id,
                    "tool_started",
                    f"Tool: {name}\nArgs: {args}",
                )
            except Exception as exc:
                logger.exception("Failed to save tool start to DB: %s", exc)

    def on_tool_finished(self, name: str, result: str) -> None:
        """Handle tool completion: show mutations, surface errors."""
        self.chat_widget.append_tool_finished(name, result)

        if name == GRAPH_MUTATING_TOOL_NAME and result:
            self.chat_widget.append_mutation(result)
            self.refresh_inspector()
        if self._result_is_error(result):
            self.chat_widget.append_error(f"{name}: {result[:300]}")
        self.status_bar.showMessage("Agent is thinking...")
        self.status_bar.setStyleSheet("")

        if self.active_session_id is not None:
            try:
                # Always save tool_finished to DB
                self.sessions_store.append(
                    self.active_session_id,
                    "tool_finished",
                    result,
                    payload={"tool_name": name},
                )

                # Save mutation or error to DB if applicable
                if name == GRAPH_MUTATING_TOOL_NAME and result:
                    self.sessions_store.append(
                        self.active_session_id,
                        "mutation",
                        result,
                        payload={"tool_name": name},
                    )
                if self._result_is_error(result):
                    self.sessions_store.append(
                        self.active_session_id,
                        "error",
                        f"{name}: {result[:300]}",
                        payload={"tool_name": name},
                    )
            except Exception as exc:
                logger.exception("Failed to save tool finish to DB: %s", exc)

    @staticmethod
    def _result_is_error(result_str: str) -> bool:
        """Read the structured result's canonical ``ok`` flag.

        Every tool result is built by ``GrcAgent._tool_result`` /
        ``build_error_payload`` (see domain_models.py), both of which
        guarantee an ``ok: bool`` key — ``ok: False`` is the single
        canonical error signal callers can rely on.
        """
        try:
            parsed = json.loads(result_str)
        except (json.JSONDecodeError, TypeError):
            return result_str.strip().lower().startswith("error")
        if isinstance(parsed, dict) and "ok" in parsed:
            return not bool(parsed["ok"])
        return False

    def on_response_chunk(self, text: str) -> None:
        """Append stream token chunks directly to the display."""
        if not self.chat_widget._streaming:
            self.chat_widget.start_stream()
        self.chat_widget.append_stream_chunk(text)

    def on_turn_finished(self, result: dict[str, Any]) -> None:
        """Unlock the interface and clear status fields."""
        self.chat_input.setEnabled(True)
        if hasattr(self, "model_toolbar"):
            self.model_toolbar.setEnabled(True)
        self.chat_widget.set_generating(False)
        self._set_generation_status_label(False)
        assistant_text = result.get("assistant_text", "")
        if self.chat_widget._streaming:
            # Finalize with everything streamed across every round (this
            # turn's own chunk(s) already flowed in via response_chunk,
            # so the accumulated text is authoritative), not just the
            # terminal round's assistant_text, which would discard any
            # earlier tool-round reasoning/<think> content.
            self.chat_widget.finalize_stream(self.chat_widget.current_stream_text())
        elif assistant_text.strip():
            self.chat_widget.append_message("assistant", assistant_text)
        self.refresh_inspector()
        self.status_bar.setStyleSheet("")
        self.status_bar.showMessage("Ready")

        self.chat_input.setFocus(Qt.FocusReason.OtherFocusReason)

        if self.active_session_id is not None and assistant_text.strip():
            try:
                self.sessions_store.append(
                    self.active_session_id,
                    "assistant",
                    assistant_text,
                )
            except Exception as exc:
                logger.exception("Failed to save assistant reply to DB: %s", exc)

        self.refresh_sidebar_sessions()

    def cleanup_thread(self) -> None:
        """Gracefully wait for the execution thread to close and release resources."""
        if self.thread:
            self.thread.quit()
            if not self.thread.wait(1500):
                logger.warning(
                    "Agent worker thread did not exit within 1500ms. Forcefully terminating..."
                )
                self.thread.terminate()
                self.thread.wait(500)
            self.thread.deleteLater()
            self.thread = None
        if self.worker:
            self.worker.deleteLater()
            self.worker = None

    def refresh_inspector(self) -> None:
        """Query the inspect_graph wrapper asynchronously using QThreadPool."""
        worker = InspectorRunnable(self.agent)
        worker.signals.finished.connect(self.on_inspector_refreshed)
        worker.signals.error.connect(self.on_inspector_error)
        QThreadPool.globalInstance().start(worker)

    def on_inspector_refreshed(self, overview_data: dict[str, Any]) -> None:
        self.inspector_widget.update_state(overview_data)
        graph = overview_data.get("graph", {}) or {}
        val_status = graph.get("validation", {}).get("status", ValidationStatus.UNKNOWN)
        if val_status == ValidationStatus.VALID:
            self.validation_label.setText(f"{VALID_ICON} Valid")
            self.validation_label.setStyleSheet(f"color: {COLOR_GREEN};")
        elif val_status == ValidationStatus.INVALID:
            self.validation_label.setText(f"{INVALID_ICON} Invalid")
            self.validation_label.setStyleSheet(f"color: {COLOR_RED};")
        else:
            self.validation_label.setText(f"{UNVALIDATED_ICON} Unvalidated")
            self.validation_label.setStyleSheet(f"color: {COLOR_SUBTEXT};")

    def open_file_dialog(self) -> None:
        file_name, _ = QFileDialog.getOpenFileName(
            self, "Open GRC Flowgraph", "", "GNU Radio Companion Files (*.grc)"
        )
        if file_name:
            self.open_file(Path(file_name))

    def open_file(self, file_path: Path) -> None:
        """Load ``file_path`` into the agent and refresh the inspector.

        Surfaces a ``QMessageBox`` on failure so the user gets a clear
        error rather than a tiny status-bar message.
        """
        from grc_agent.session import load_grc

        try:
            loaded = load_grc(file_path)
        except Exception as exc:
            self._show_error("Open failed", f"Could not load {file_path}:\n{exc}")
            return
        if isinstance(loaded, dict):
            self._show_error(
                "Open failed",
                f"Could not load {file_path}:\n{loaded.get('message', 'unknown error')}",
            )
            return
        self.chat_widget.clear()
        self.active_session_id = None
        if hasattr(self.agent, "reset_chat_session"):
            self.agent.reset_chat_session()
        self.agent.session = loaded
        self.inspector_widget.set_grc_file_path(str(file_path))
        self.refresh_inspector()
        # Update the toolbar's graph-path label so the user can see
        # which file is loaded at a glance and use the open-location
        # / browse buttons to navigate.
        self.model_toolbar.set_graph_path(str(file_path))
        # Drop a visible confirmation in the chatbox so the user has
        # proof the load succeeded. The status-bar message is too
        # transient (auto-clears on the next event).
        self.chat_widget.append_info(f"Loaded graph: {file_path.name}")
        self.status_bar.showMessage(f"Loaded {file_path}")
        self.update_ui_state()

    def _on_open_graph_location(self) -> None:
        """Open the OS file manager at the folder of the loaded .grc.

        Wired to the model toolbar's "open containing folder" button.
        Shows a status-bar hint (not a modal) if the path is missing
        or the folder does not exist on disk.
        """
        path = self.model_toolbar.current_graph_path()
        if not path:
            self.status_bar.showMessage("No graph loaded.", 5000)
            return
        from pathlib import Path

        folder = Path(path).expanduser().parent
        if not folder.exists():
            self._show_error(
                "Folder missing",
                f"The folder for {path} no longer exists on disk.",
            )
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))):
            self._show_error(
                "Open folder",
                f"Could not open {folder} in the system file manager.",
            )
            return
        self.status_bar.showMessage(f"Opened {folder}", 5000)

    def save_file(self) -> None:
        if not (self.agent.session and self.agent.session.path):
            self._show_info(
                "Nothing to save",
                "No flowgraph is currently loaded. Open a `.grc` first.",
            )
            return
        try:
            self.agent.session.save()
            self.status_bar.showMessage("Graph saved.")
        except Exception as exc:
            self._show_error(
                "Save failed",
                str(exc),
            )

    def on_inspector_error(self, err_msg: str) -> None:
        logger.error(f"Failed to refresh inspector asynchronously: {err_msg}")

    def _on_model_swap_progress(self, message: str) -> None:
        """Update the status bar with progress from the swap worker."""
        self.status_bar.showMessage(message)

    def _on_model_swap_finished(self, result: Any) -> None:
        """Apply the new backend state and notify the user."""
        import dataclasses

        new_provider = getattr(result, "provider_config", None)
        if new_provider is None:
            self._on_model_swap_error("Launcher returned no provider_config.")
            return

        selection = getattr(self, "_pending_swap_selection", None)
        # Default the model name from the new provider so the status
        # bar always has a label to show, even if the swap was driven
        # by a code path that did not go through the dialog (tests,
        # programmatic recovery from a degraded state, etc.).
        model_name = getattr(new_provider, "model", "") or "unknown"
        if getattr(self, "llama_config", None) is not None and selection is not None:
            model_name = (
                selection.ollama_model_name
                or getattr(getattr(result, "provider_config", None), "model", "")
                or "unknown"
            )

            chosen_model: str | None = None
            server_url: str | None = None
            if selection.backend == "ollama":
                chosen_model = selection.ollama_model_name
                server_url = DEFAULT_OLLAMA_URL
            elif selection.backend == "openrouter":
                chosen_model = default_openrouter_model()
                server_url = DEFAULT_OPENROUTER_URL
                model_name = chosen_model

            if chosen_model is not None:
                self.llama_config = dataclasses.replace(
                    self.llama_config,
                    backend=selection.backend,
                    server_url=server_url,
                    model=chosen_model,
                )
                try:
                    from grc_agent.config import resolve_config_path, update_toml_config_file

                    config_path = resolve_config_path(None)
                    if config_path:
                        update_toml_config_file(
                            config_path,
                            {
                                "backend": selection.backend,
                                "server_url": server_url,
                                "model": chosen_model,
                            },
                        )
                except Exception as exc:
                    logger.warning("Failed to persist to grc_agent.toml: %s", exc)

        self._pending_swap_selection = None
        self.provider_config = new_provider
        self._set_swap_in_progress(False)
        self._update_model_status_label()
        # Sync the toolbar with the new model.
        if hasattr(self, "model_toolbar"):
            backend = getattr(self.llama_config, "backend", "ollama")
            self.model_toolbar.set_backend(backend)
            self.model_toolbar.set_current_model(model_name)
            self.set_backend_connected(True)
        # Persist to preferences.json so the selection survives restarts.
        try:
            from grc_agent.config import update_last_model, update_provider_chosen

            backend_str = getattr(self.llama_config, "backend", "ollama")
            update_last_model(model_name)
            update_provider_chosen(backend_str)
        except Exception as exc:
            logger.warning("Failed to persist model selection to preferences: %s", exc)
        # Recovery path: a successful swap means the (possibly
        # different) backend is reachable. Drop the degraded mode and
        # re-enable the chat input.
        self._on_backend_recovered()
        self.status_bar.showMessage(f"Model switched to {model_name}", 5000)
        self.chat_widget.append_message(
            "assistant",
            f"{MODEL_SELECTOR_MARKER} Switched to `{model_name}`. "
            "Existing chat history is preserved; "
            "the next turn uses the new model.",
        )

    def _on_model_swap_error(self, message: str) -> None:
        """Surface a swap failure and re-enable the controls."""
        self._set_swap_in_progress(False)
        self._pending_swap_selection = None
        self.status_bar.showMessage(f"Model swap failed: {message}", 8000)
        self.chat_widget.append_message(
            "assistant",
            f"{MODEL_SELECTOR_MARKER} Model swap failed: {message}",
        )
        self.set_backend_connected(False)

    def refresh_sidebar_sessions(self) -> None:
        """Fetch all sessions from the database and populate the sidebar list."""
        from grc_agent.sessions_store import list_sessions_sync

        try:
            sessions = list_sessions_sync(
                _default_sessions_db(),
                limit=200,
            )
            self.sidebar_widget.populate_sessions(sessions)
        except Exception as exc:
            logger.exception("Failed to list recent sessions for sidebar: %s", exc)

    def toggle_sidebar(self) -> None:
        """Toggle the visibility of the session history sidebar."""
        should_show = self.sidebar_widget.isHidden()
        self.sidebar_widget.setVisible(should_show)
        if should_show:
            # When showing, make sure it has a non-zero size
            sizes = self.h_splitter.sizes()
            if len(sizes) == 3 and sizes[0] < SPLITTER.sidebar_collapsed_floor_px:
                sizes[0] = max(
                    SPLITTER.sidebar_min_px_initial,
                    int(self.width() * SPLITTER.sidebar_fraction_initial),
                )
                self.h_splitter.setSizes(sizes)

    def start_new_chat_session(self) -> None:
        """Start a fresh chat session, resetting the GUI and the agent state."""
        self.chat_widget.clear()
        if hasattr(self.agent, "reset_chat_session"):
            self.agent.reset_chat_session()
        self.active_session_id = None
        self.sidebar_widget.list_widget.clearSelection()
        self.status_bar.showMessage("Started a fresh chat session.", 5000)

    def _on_clear_all_history(self) -> None:
        """Wipe every session row from the on-disk sessions DB.

        Triggered by the sidebar's "Clear all" button. Prompts the
        user (destructive action), then calls
        :meth:`SessionStore.clear_all` and refreshes the sidebar.
        """
        confirm = QMessageBox.question(
            self,
            "Clear all chat history?",
            "This permanently deletes every session from the local "
            "history database. The currently-open chat (if any) is "
            "also closed. This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        try:
            removed = self.sessions_store.clear_all()
        except Exception as exc:
            logger.exception("Failed to clear session history: %s", exc)
            self.status_bar.showMessage(f"Clear failed: {exc}", 8000)
            return
        # Drop the in-memory chat and any resume state.
        self.chat_widget.clear()
        if hasattr(self.agent, "reset_chat_session"):
            self.agent.reset_chat_session()
        self.active_session_id = None
        self.sidebar_widget.list_widget.clear()
        self.status_bar.showMessage(f"Cleared {removed} session(s) from history.", 5000)

    def _open_past_session(self, session_id: int) -> None:
        """Clear the chat widget, autoload the associated .grc graph, and replay past messages.

        Per the agreed design, the next user message starts a
        fresh turn on the new model; the old conversation is
        preserved in the sessions DB and can be reopened again.

        The DB has two kinds of rows: display rows (existing
        ``user``/``assistant``/``tool_*``/``mutation``/``error`` text)
        and model rows (``assistant_model``/``tool_model`` with the
        typed ``ChatMessage`` in the ``payload`` column). Display rows
        are replayed into the chat widget; model rows are replayed into
        the agent's ``ChatHistory`` so the next model step has the
        same inspect/search/change evidence the user originally saw.
        """
        from grc_agent.chat_roles import (
            ASSISTANT_MODEL_ROLE,
            DISPLAY_ROLES,
            TOOL_MODEL_ROLE,
            chat_message_from_payload,
        )
        from grc_agent.sessions_store import get_session_sync, list_messages_sync

        model_roles = {ASSISTANT_MODEL_ROLE, TOOL_MODEL_ROLE}

        try:
            session_rec = get_session_sync(_default_sessions_db(), session_id)
            messages = list_messages_sync(_default_sessions_db(), session_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to open session %s: %s", exc)
            self.status_bar.showMessage(f"Open session failed: {exc}", 5000)
            return

        # 1. Autoload the GRC graph associated with the session.
        if session_rec and session_rec.graph_path:
            g_path = Path(session_rec.graph_path)
            if g_path.exists():
                self.open_file(g_path)
            else:
                self.status_bar.showMessage(
                    f"Warning: Associated graph file not found: {g_path}", 5000
                )
                self.chat_widget.clear()
        else:
            self.chat_widget.clear()

        # 2. Reset the agent's chat session to clear any prior in-memory history.
        #    and any prior in-memory history. The new ``ChatHistory`` is
        #    populated from the model rows below.
        if hasattr(self.agent, "reset_chat_session"):
            self.agent.reset_chat_session()

        # 3. Replay model rows into the agent's ``ChatHistory``. The
        #    typed ``ChatMessage`` objects carry the original tool
        #    calls and tool results so the model has full context on
        #    the next turn. Sessions written before the model rows
        #    existed are *not* supported — they contain no
        #    ``assistant_model`` / ``tool_model`` rows and the model
        #    has no typed history to resume from. AGENTS.md forbids
        #    backward-compat shims; delete the legacy session and
        #    start fresh.

        model_replayed = 0
        for msg in messages:
            if msg.role not in model_roles:
                continue
            chat_message = chat_message_from_payload(msg.payload)
            if chat_message is None:
                logger.warning(
                    "Skipping undecodable model row %s in session %s",
                    msg.id,
                    session_id,
                )
                continue
            try:
                self.agent.chat_history.add_message(chat_message)
                model_replayed += 1
            except Exception as exc:
                logger.exception("Failed to replay model row %s: %s", msg.id, exc)

        if model_replayed == 0 and any(
            msg.role not in model_roles for msg in messages
        ):
            self.status_bar.showMessage(
                f"Session {session_id} predates the typed-history "
                "format and cannot be resumed. Start a new chat to "
                "continue.",
                8000,
            )
            logger.warning(
                "Session %s has no assistant_model/tool_model rows; "
                "refusing to synthesize a typed history. User must "
                "start a new chat.",
                session_id,
            )
            self.active_session_id = None
            return

        # 4. Replay display rows into the chat widget. DISPLAY_ROLES already
        # excludes "system" rows, so no separate skip is needed.
        display_replayed = 0
        for msg in messages:
            if msg.role in DISPLAY_ROLES:
                self.chat_widget.append_message(msg.role, msg.text, payload=msg.payload)
                display_replayed += 1

        # 5. Keep the active session ID to allow active continuation.
        self.active_session_id = session_id

        self.status_bar.showMessage(
            f"Resumed session {session_id} "
            f"({display_replayed} display, {model_replayed} model rows).",
            5000,
        )

    def _set_swap_in_progress(self, busy: bool) -> None:
        """Lock or unlock the chat input / Validate button around a swap."""
        if not hasattr(self, "chat_input"):
            return
        self.chat_input.setEnabled(
            not busy
            and (self.agent.session is not None and self.agent.session.flowgraph is not None)
        )
        if hasattr(self, "validate_btn"):
            self.validate_btn.setEnabled(
                not busy
                and (self.agent.session is not None and self.agent.session.flowgraph is not None)
            )
        if hasattr(self, "model_toolbar"):
            self.model_toolbar.setEnabled(not busy)

    def on_validate_clicked(self) -> None:
        """Handler for 'Validate' button click."""
        if self.agent.session and self.agent.session.path:
            self.console_log.clear()
            self.process_manager.validate_graph(self.agent.session)
        else:
            self.on_process_status("Error: No active flowgraph session path.")

    def on_process_started(self) -> None:
        """Disable validate button while compilation is active."""
        self.validate_btn.setEnabled(False)
        self.status_bar.showMessage("Validating flowgraph...")

    def on_process_stdout(self, text: str) -> None:
        """Append standard output chunks to the console log."""
        self.console_log.insertPlainText(text)
        self.console_log.ensureCursorVisible()

    def on_process_stderr(self, text: str) -> None:
        """Append standard error chunks to the console log."""
        self.console_log.insertPlainText(text)
        self.console_log.ensureCursorVisible()

    def on_process_status(self, text: str) -> None:
        """Log status message to both status bar and console log."""
        self.status_bar.showMessage(text)
        self.console_log.insertPlainText(f"\n>>> {text}\n")
        self.console_log.ensureCursorVisible()

    def on_process_finished(self, exit_code: int) -> None:
        """Re-enable validate button once compilation completes."""
        self.validate_btn.setEnabled(True)
        self.status_bar.showMessage(f"Ready (last execution exit code: {exit_code})")

        self.refresh_inspector()

    # ------------------------------------------------------------------
    # File menu: Open Output Folder, Export Chat
    # ------------------------------------------------------------------
    def open_output_folder(self) -> None:
        """Open the directory where the package writes its state."""
        from grc_agent.config import collect_package_paths

        paths = collect_package_paths()
        # Prefer the launcher-logs dir (most useful for triage), fall back
        # to the user-state dir.
        target = Path(paths.get("llama_logs") or paths.get("grc_agent_state"))
        if not target.exists():
            target.mkdir(parents=True, exist_ok=True)
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(target))):
            self._show_error(
                "Open output folder",
                f"Could not open {target} in the system file manager.\n\n"
                f"You can browse to it manually; the path is also in the tooltip.",
            )
        self.status_bar.showMessage(f"Output folder: {target}", 5000)

    def export_chat_dialog(self) -> None:
        """Write the current conversation to a Markdown or JSON file."""
        history = self.chat_widget.get_history()
        if not history:
            self._show_info(
                "Nothing to export",
                "The chat is empty. Have a conversation first, then export.",
            )
            return
        path_str, selected_filter = QFileDialog.getSaveFileName(
            self,
            "Export chat",
            "grc-agent-chat.md",
            "Markdown (*.md);;JSON (*.json)",
        )
        if not path_str:
            return
        try:
            target = Path(path_str)
            if selected_filter.startswith("JSON") or target.suffix.lower() == ".json":
                payload = {"history": history}
                target.write_text(
                    json.dumps(payload, indent=2, sort_keys=True),
                    encoding="utf-8",
                )
            else:
                target.write_text(self.chat_widget.export_markdown(), encoding="utf-8")
        except OSError as exc:
            self._show_error("Export failed", f"Could not write {path_str}:\n{exc}")
            return
        self.status_bar.showMessage(f"Exported chat to {path_str}", 5000)

    # ------------------------------------------------------------------
    # Help menu: About, Open Docs Folder
    # ------------------------------------------------------------------
    def show_about_dialog(self) -> None:
        """Show the About dialog with version, license, and a copy-info button."""
        version = _get_app_version()
        text = (
            f"<h3>{APP_DISPLAY_NAME}</h3>"
            f"<p>Version <b>{version}</b></p>"
            f"<p>License: <a href='{APP_LICENSE_URL}'>{APP_LICENSE_NAME}</a></p>"
            f"<p>Project: <a href='{APP_HOMEPAGE_URL}'>{APP_HOMEPAGE_URL}</a></p>"
            f"<p>&copy; 2026 {APP_ORGANIZATION}</p>"
        )
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle(f"About {APP_NAME}")
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(text)
        copy_button = box.addButton("&Copy version info", QMessageBox.ButtonRole.ActionRole)
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.exec()
        if copy_button is not None and box.clickedButton() is copy_button:
            from PySide6.QtWidgets import QApplication as _QApp

            info = (
                f"{APP_DISPLAY_NAME} {version}\n"
                f"License: {APP_LICENSE_NAME}\n"
                f"Python: {sys.version.split()[0]}\n"
                f"Qt: {_QApp.instance().applicationVersion() or 'n/a'}\n"
                f"Platform: {sys.platform}\n"
            )
            _QApp.clipboard().setText(info)
            self.status_bar.showMessage("Version info copied to clipboard.", 5000)

    def open_docs_folder(self) -> None:
        """Open the bundled docs/ directory in the system file manager.

        Falls back to a message pointing at the GitHub URL when the docs
        directory is not present (typical for installed `uv tool install`
        users, who do not get the source tree).
        """
        # The repo ships `docs/` at the project root; the installed tool
        # does not. Resolve the local path and fall back gracefully.
        repo_root_candidates = [
            Path(__file__).resolve().parents[2] / "docs",
            Path.cwd() / "docs",
        ]
        for candidate in repo_root_candidates:
            if candidate.is_dir():
                if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(candidate))):
                    self._show_error(
                        "Open docs folder",
                        f"Could not open {candidate} in the system file manager.",
                    )
                return
        self._show_info(
            "Docs not available locally",
            "This install was packaged without the docs/ directory.\n\n"
            f"Read the docs online at {APP_HOMEPAGE_URL}/tree/main/docs",
        )

    # ------------------------------------------------------------------
    # Modal helpers (QMessageBox wrappers)
    # ------------------------------------------------------------------
    def _show_error(self, title: str, message: str) -> None:
        """Surface a non-recoverable error as a modal dialog (and the status bar)."""
        logger.error("%s: %s", title, message)
        QMessageBox.critical(self, title, message)
        self.status_bar.showMessage(f"{title}: {message.splitlines()[0]}", 8000)

    def _show_info(self, title: str, message: str) -> None:
        """Surface an informational message as a modal dialog."""
        QMessageBox.information(self, title, message)
