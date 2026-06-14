import json
import logging
import sys
from pathlib import Path
from typing import Any

from grc_agent.sessions_store import open_session_store
from PySide6.QtCore import (
    QEvent,
    QObject,
    QProcess,
    QRunnable,
    QSettings,
    Qt,
    QThread,
    QThreadPool,
    QUrl,
    Signal,
)
from PySide6.QtGui import QAction, QDesktopServices, QIcon
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

from .chat_widget import ChatWidget
from .inspector import InspectorWidget
from .model_toolbar import ModelToolbar
from .process_manager import ProcessManager
from .sidebar_widget import SidebarWidget
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

            new_url = getattr(self.llama_config, "server_url", "http://localhost:11434")
            new_model = getattr(self.llama_config, "model", "")

            if self.backend == "ollama":
                new_url = "http://localhost:11434"
                from grc_agent.config import default_app_config
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

                model_exists = any(
                    m == model_name or m == f"{model_name}:latest"
                    for m in local_models
                )

                if not model_exists:
                    self.signals.progress.emit(f"Pulling model '{model_name}'...")
                    try:
                        from grc_agent.model_manager import pull_ollama_model
                        pull_result = pull_ollama_model(model_name, server_url=new_url)
                        if not pull_result.get("ok"):
                            raise Exception(pull_result.get("error", "Unknown pull error"))
                    except Exception as exc:
                        raise Exception(f"Failed to pull Ollama model '{model_name}': {exc}")
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
                new_url = "https://openrouter.ai/api"
                import os
                new_model = os.getenv("OPENROUTER_MODEL", "deepseek/deepseek-v4-flash")

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
            overview_data = inspect_graph(self.agent, view="overview", targets=[], params=[])
            if self.agent.session and self.agent.session.flowgraph:
                params_map = {}
                for b in self.agent.session.flowgraph.blocks:
                    p = b.params.get("parameters", None)
                    if p:
                        params_map[b.instance_name] = p
                overview_data["_block_params"] = params_map
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
        setup_mode: bool = True,
    ) -> None:
        super().__init__(parent)
        self.agent = agent
        self.provider_config = provider_config
        self.llama_config = llama_config
        self.worker = None
        self._pending_swap_selection = None
        self.thread = None
        self.process_manager = None
        self._safe_to_close = False
        self.active_session_id = None
        self.sessions_store = open_session_store(_default_sessions_db())
        # Backend reachability state. ``None`` means "unknown / not yet
        # probed". ``True`` = healthy. ``False`` = degraded. The
        # ``bootstrap_result`` is consulted once at construction; live
        # mid-session failures are tracked via ``on_backend_unreachable``
        # callbacks wired into the agent worker.
        self.backend_reachable: bool | None = None
        self._backend_unreachable_hint: str | None = None
        if bootstrap_result is not None and getattr(
            bootstrap_result, "launch_status", ""
        ) in {"probe_failed", "failed"}:
            self.backend_reachable = False
            errs = list(getattr(bootstrap_result, "errors", []) or [])
            self._backend_unreachable_hint = (
                errs[0] if errs else "Backend unreachable."
            )

        self.setWindowTitle(f"{APP_DISPLAY_NAME} {_get_app_version()}")
        icon_path = Path(__file__).parent / "resources" / "icon.png"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        # Accept drops of `.grc` files onto the main window.
        self.setAcceptDrops(True)

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
        self.recent_sessions_action = QAction("&Session Sidebar", self)
        self.recent_sessions_action.setShortcut("Ctrl+Shift+H")
        self.recent_sessions_action.triggered.connect(self.toggle_sidebar)
        file_menu.addAction(self.recent_sessions_action)

        # Help Menu
        help_menu = menubar.addMenu("&Help")
        about_action = QAction("&About GRC Agent", self)
        about_action.triggered.connect(self.show_about_dialog)
        help_menu.addAction(about_action)

        open_docs_action = QAction("Open &Docs Folder", self)
        open_docs_action.triggered.connect(self.open_docs_folder)
        help_menu.addAction(open_docs_action)

        self._settings = QSettings("GRC_Agent", "GUI")
        geom = self._settings.value("window/geometry")
        if geom:
            self.restoreGeometry(geom)
        else:
            self.resize(800, 600)

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

        # Set stretch factors: only the Chat widget expands, sidebar and inspector keep fixed widths on resize.
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)

        # Set default splitter proportions (e.g. 9% sidebar, 50% chat, remaining for inspector)
        total_width = self.width() or 800
        sidebar_w = max(80, int(total_width * 0.09))
        chat_w = int(total_width * 0.50)
        inspector_w = max(200, total_width - sidebar_w - chat_w)
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
        self.validate_btn.setObjectName("validateButton")
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
        current_backend = getattr(self.llama_config, "backend", "ollama") if self.llama_config else "ollama"
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

        main_layout.addWidget(self.model_toolbar)
        main_layout.addWidget(v_splitter)

        # Aliases for backwards compatibility and automated testing
        self.chat_input = self.chat_widget.chat_input
        self.chat_display = self.chat_widget.chat_display
        self.chat_input.returnPressed.connect(self.send_prompt)
        self.chat_input.installEventFilter(self)

        # Splitter states are restored in showEvent once window geometry is fully realized.

        # Initialize Process Manager
        self.process_manager = ProcessManager(self)
        self._pending_close = False
        self._safe_to_close = False
        self._last_applied_revision = None
        self._validation_stdout = ""
        self._validation_stderr = ""

        # Wire execution buttons & process manager signals
        self.validate_btn.clicked.connect(self.on_validate_clicked)

        self.process_manager.started.connect(self.on_process_started)
        self.process_manager.stdout_received.connect(self.on_process_stdout)
        self.process_manager.stderr_received.connect(self.on_process_stderr)
        self.process_manager.status_message.connect(self.on_process_status)
        self.process_manager.finished.connect(self.on_process_finished)
        self.process_manager.finished.connect(self.on_deferred_close)
        # Defer thread.finished binding until start_generation assigns self.thread,
        # but the on_deferred_close gate (_pending_close) prevents double-firing.

        # Initialize inspector state and active GRC file path
        if self.agent.session and self.agent.session.path:
            self.inspector_widget.set_grc_file_path(str(self.agent.session.path))
        self.refresh_inspector()
        self.refresh_sidebar_sessions()

        # Status Bar
        self.status_bar = QStatusBar(self)
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Ready")

        self.model_status_label = QLabel(self)
        self.model_status_label.setObjectName("modelStatusLabel")
        self.status_bar.addPermanentWidget(self.model_status_label)
        self._update_model_status_label()

        self.validation_label = QLabel("Unknown", self)
        self.status_bar.addPermanentWidget(self.validation_label)
        self.update_ui_state()

        # Probe Ollama after the event loop starts (non-blocking).
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, self._probe_and_populate_models)

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
            self.model_status_label.setStyleSheet("color: #cdd6f4;")
        except Exception as exc:
            logger.debug("Failed to render model status label: %s", exc)
            self.model_status_label.setText("Model: unknown")
            self.model_status_label.setStyleSheet("color: #a6adc8;")

    def update_ui_state(self) -> None:
        """Enable or disable chat and validation controls based on active session state."""
        has_graph = (
            self.agent.session is not None
            and self.agent.session.flowgraph is not None
        )
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
            self.validation_label.setStyleSheet("color: #f38ba8;")
            self._render_backend_unreachable_banner()
        elif has_graph:
            self.chat_input.setPlaceholderText("Ask the assistant to modify or summarize the flowgraph...")
            self.validation_label.setStyleSheet("color: #a6e3a1;")
        else:
            self.chat_input.setPlaceholderText("Please load a .grc flowgraph (File -> Open) to start chatting...")
            self.validation_label.setText("No Graph")
            self.validation_label.setStyleSheet("color: #a6adc8;")

    def _on_backend_unreachable(self, result: dict[str, Any]) -> None:
        """Worker callback: backend is unreachable, switch to degraded mode."""
        self.backend_reachable = False
        self._backend_unreachable_hint = str(
            result.get("assistant_text") or "Backend unreachable."
        )
        self.status_bar.showMessage(
            f"Backend unreachable at {self._server_url_display()} — chat disabled, "
            f"use the toolbar to retry or select a different model."
        )
        if hasattr(self, "model_toolbar"):
            self.model_toolbar.set_status(connected=False)
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
        marker = "[backend status]"
        for entry in history:
            if entry.get("text", "").startswith(marker):
                return
        self.chat_widget.append_error(f"{marker} {hint}")

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

        server_url = getattr(self.llama_config, "server_url", "http://localhost:11434")
        try:
            models = discover_ollama_models(server_url)
        except Exception as exc:
            logger.warning("Model discovery failed: %s", exc)
            models = []

        current = self.model_toolbar.current_model()
        self.model_toolbar.set_models(models, current=current or (models[0] if models else ""))
        if models:
            self.model_toolbar.set_status(connected=True)
            self.backend_reachable = True
        else:
            self.model_toolbar.set_status(connected=False)
            self.backend_reachable = False
        self.update_ui_state()

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if obj is self.chat_input and event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Return and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
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
                    # 1. Sidebar should be max 20% of total width. If it's larger or collapsed, set it to 18%.
                    max_sidebar_w = int(total_w * 0.20)
                    if (sizes[0] > max_sidebar_w or sizes[0] < 50) and not self.sidebar_widget.isHidden():
                        sizes[0] = max(150, int(total_w * 0.18))

                    # 2. Ensure inspector (index 2) has a sensible size and was not collapsed by older 2-widget settings
                    if sizes[2] < 50 and not self.inspector_widget.isHidden():
                        sizes[2] = max(200, int(total_w * 0.32))

                    # 3. Chat gets the remainder
                    sizes[1] = max(300, total_w - sizes[0] - sizes[2])
                    self.h_splitter.setSizes(sizes)
            else:
                sidebar_w = max(150, int(total_w * 0.18))
                chat_w = int(total_w * 0.50)
                inspector_w = max(200, total_w - sidebar_w - chat_w)
                self.h_splitter.setSizes([sidebar_w, chat_w, inspector_w])

            # Restore vertical splitter state
            v_state = self._settings.value("window/v_splitter")
            if v_state:
                self.v_splitter.restoreState(v_state)

    def _ensure_active_session_db_record(self, first_user_prompt: str) -> None:
        """Create a new session record in SQLite if one isn't currently active."""
        if self.active_session_id is not None:
            return

        # Get path and hash from active agent session
        graph_path = ""
        graph_hash = ""
        if self.agent.session and self.agent.session.path:
            graph_path = str(self.agent.session.path)
            if hasattr(self.agent.session, "persisted_file_sha256") and self.agent.session.persisted_file_sha256:
                graph_hash = self.agent.session.persisted_file_sha256
            elif self.agent.session.path.exists():
                try:
                    import hashlib
                    graph_hash = hashlib.sha256(self.agent.session.path.read_bytes()).hexdigest()
                except Exception:
                    graph_hash = "unknown"

        model_alias = getattr(self.provider_config, "model", None)
        if model_alias is not None:
            if type(model_alias).__name__ in ("MagicMock", "Mock"):
                model_alias = "mock-model"
            else:
                model_alias = str(model_alias)
        else:
            model_alias = "unknown"
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
        except Exception as exc:
            logger.exception("Failed to open new database session: %s", exc)

    def send_prompt(self) -> None:
        """Read the input box, format as a user message, and start worker generation."""
        prompt = self.chat_input.text().strip()
        if prompt:
            self.chat_input.clear()
            if prompt.startswith("/save") or prompt.startswith("\\save"):
                self.chat_widget.append_message("user", prompt)
                self.save_file()
                return
            if (
                prompt.startswith("/model")
                or prompt.startswith("\\model")
                or prompt.startswith("/client")
                or prompt.startswith("\\client")
            ):
                self.chat_widget.append_message("user", prompt)
                self._on_toolbar_refresh()
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

        # Bind thread.finished once at creation so the deferred close path
        # can fire on thread teardown without re-connecting on every X-click.
        # cleanup_thread() disconnects this in its finally block to avoid
        # post-destruction slot invocations.
        self.thread.finished.connect(self.on_deferred_close)

        self.thread.start()

    def on_worker_started(self) -> None:
        """Lock input interface to prevent race conditions during generation."""
        self.chat_input.setEnabled(False)
        if hasattr(self, "model_toolbar"):
            self.model_toolbar.setEnabled(False)
        self.status_bar.showMessage("Agent is thinking...")
        self.chat_widget.start_stream()

    def on_tool_started(self, name: str, args: str) -> None:
        """Show the running tool name in the status bar and chat indicator."""
        self.status_bar.showMessage(f"-- {name}...")
        self.status_bar.setStyleSheet(
            "QStatusBar { color: #89b4fa; background-color: #11111b; "
            "border-top: 1px solid #45475a; font-size: 12px; padding: 2px 8px; } "
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
        if name == "change_graph" and result:
            self.chat_widget.append_mutation(result)
            self.refresh_inspector()
        if self._result_is_error(result):
            self.chat_widget.append_error(f"{name}: {result[:300]}")
        self.status_bar.showMessage("Agent is thinking...")
        self.status_bar.setStyleSheet("")

        if self.active_session_id is not None:
            try:
                if name == "change_graph" and result:
                    if self._result_is_error(result):
                        role = "error"
                    else:
                        role = "mutation"
                else:
                    if self._result_is_error(result):
                        role = "error"
                    else:
                        role = "tool_finished"
                self.sessions_store.append(
                    self.active_session_id,
                    role,
                    result,
                )
            except Exception as exc:
                logger.exception("Failed to save tool finish to DB: %s", exc)

    @staticmethod
    def _result_is_error(result_str: str) -> bool:
        for marker in ("'ok': False", '"ok": false', "'ok': false"):
            if marker in result_str.lower().replace(" ", ""):
                return True
        return result_str.lower().startswith("error")

    def on_response_chunk(self, text: str) -> None:
        """Append stream token chunks directly to the display."""
        self.chat_widget.append_stream_chunk(text)

    def on_turn_finished(self, result: dict[str, Any]) -> None:
        """Unlock the interface and clear status fields."""
        self.chat_input.setEnabled(True)
        if hasattr(self, "model_toolbar"):
            self.model_toolbar.setEnabled(True)
        assistant_text = result.get("assistant_text", "")
        tool_calls_executed = int(result.get("tool_calls_executed", 0))
        if not assistant_text.strip() and tool_calls_executed > 0:
            # The model never produced text — the assistant message
            # was a tool-call-only turn. Drop the empty streaming
            # placeholder and the empty display row; the typed
            # ``assistant_model`` row already carries the message.
            self.chat_widget.drop_last_assistant()
        else:
            self.chat_widget.finalize_stream(assistant_text)
        self.refresh_inspector()
        self.status_bar.setStyleSheet("")
        self.status_bar.showMessage("Ready")

        self.chat_input.setFocus(Qt.FocusReason.OtherFocusReason)

        if (
            self.active_session_id is not None
            and assistant_text.strip()
        ):
            try:
                self.sessions_store.append(
                    self.active_session_id,
                    "assistant",
                    assistant_text,
                )
            except Exception as exc:
                logger.exception("Failed to save assistant reply to DB: %s", exc)

        self.refresh_sidebar_sessions()

        # Audit 4.6: warn if the on-disk graph has been mutated while a
        # flowgraph is still running. The running subprocess has its own
        # in-memory state and will not pick up the change until it is
        # restarted.
        self._check_stale_running_graph()

    def _check_stale_running_graph(self) -> None:
        """Show a status-bar warning if the on-disk graph diverges from the running flowgraph."""
        try:
            if self.process_manager is None or self.process_manager.run_process is None:
                return
            if (
                self.process_manager.run_process.state()
                != QProcess.ProcessState.Running
            ):
                return
            session = getattr(self.agent, "session", None)
            if session is None:
                return
            current_revision = getattr(session, "state_revision", None)
            if current_revision is None:
                return
            if self._last_applied_revision is None:
                self._last_applied_revision = current_revision
                return
            if current_revision != self._last_applied_revision:
                self._last_applied_revision = current_revision
                self.status_bar.showMessage(
                    f"Flowgraph running with stale graph (revision {current_revision}). "
                    "Stop and re-run to apply changes."
                )
        except Exception as e:
            logger.debug(f"Stale-graph warning check failed: {e}")

    def cleanup_thread(self) -> None:
        """Gracefully wait for the execution thread to close and release resources.

        Disconnects the thread.finished -> on_deferred_close binding before
        terminating the thread to prevent a queued cross-thread slot from
        firing against a destroyed C++ object.
        """
        if self.thread:
            try:
                self.thread.finished.disconnect(self.on_deferred_close)
            except (TypeError, RuntimeError):
                pass
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
        val_status = overview_data.get("validation_result", {}).get("status", "unknown")
        if val_status == "valid":
            self.validation_label.setText("🟢 Valid")
            self.validation_label.setStyleSheet("color: #a6e3a1;")
        elif val_status == "invalid":
            self.validation_label.setText("🔴 Invalid")
            self.validation_label.setStyleSheet("color: #f38ba8;")
        else:
            self.validation_label.setText("⚪ Unvalidated")
            self.validation_label.setStyleSheet("color: #a6adc8;")

    def open_file_dialog(self) -> None:
        file_name, _ = QFileDialog.getOpenFileName(
            self, "Open GRC Flowgraph", "", "GNU Radio Companion Files (*.grc)"
        )
        if file_name:
            self.open_file(Path(file_name))

    def open_file(self, file_path: Path) -> None:
        """Load ``file_path`` into the agent and refresh the inspector.

        Shared by the file dialog and the drag-and-drop handler. Surfaces
        a ``QMessageBox`` on failure so the user gets a clear error
        rather than a tiny status-bar message.
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
        self.status_bar.showMessage(f"Loaded {file_path}")
        self.update_ui_state()

    def save_file(self) -> None:
        if not (self.agent.session and self.agent.session.path):
            self._show_info(
                "Nothing to save",
                "No flowgraph is currently loaded. Open a `.grc` first.",
            )
            return
        from grc_agent.runtime.change_graph import _write_committed_changes
        success = _write_committed_changes(self.agent.session)
        if success:
            self.status_bar.showMessage("Graph saved.")
        else:
            self._show_error(
                "Save failed",
                "Saving the graph returned a failure result. "
                "See the chat log for the underlying error.",
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
        model_name = (
            getattr(new_provider, "model", "")
            or "unknown"
        )
        if getattr(self, "llama_config", None) is not None and selection is not None:
            model_name = selection.ollama_model_name or getattr(
                getattr(result, "provider_config", None), "model", ""
            ) or "unknown"

            if selection.backend == "ollama":
                self.llama_config = dataclasses.replace(
                    self.llama_config,
                    backend=selection.backend,
                    server_url="http://localhost:11434",
                    model=selection.ollama_model_name,
                )
                try:
                    from grc_agent.config import resolve_config_path, update_toml_config_file
                    config_path = resolve_config_path(None)
                    if config_path:
                        update_toml_config_file(config_path, {
                            "backend": "ollama",
                            "server_url": "http://localhost:11434",
                            "model": selection.ollama_model_name,
                        })
                except Exception as exc:
                    logger.warning("Failed to persist to grc_agent.toml: %s", exc)

            elif selection.backend == "openrouter":
                import os
                env_model = os.getenv("OPENROUTER_MODEL", "deepseek/deepseek-v4-flash")
                self.llama_config = dataclasses.replace(
                    self.llama_config,
                    backend=selection.backend,
                    server_url="https://openrouter.ai/api",
                    model=env_model,
                )
                model_name = env_model
                try:
                    from grc_agent.config import resolve_config_path, update_toml_config_file
                    config_path = resolve_config_path(None)
                    if config_path:
                        update_toml_config_file(config_path, {
                            "backend": "openrouter",
                            "server_url": "https://openrouter.ai/api",
                            "model": env_model,
                        })
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
            self.model_toolbar.set_status(connected=True)
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
        self.status_bar.showMessage(
            f"Model switched to {model_name}", 5000
        )
        self.chat_widget.append_message(
            "assistant",
            f"[model selector] Switched to `{model_name}`. "
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
            f"[model selector] Model swap failed: {message}",
        )
        if hasattr(self, "model_toolbar"):
            self.model_toolbar.set_status(connected=False)

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
            if len(sizes) == 3 and sizes[0] < 50:
                sizes[0] = max(80, int(self.width() * 0.09))
                self.h_splitter.setSizes(sizes)

    def start_new_chat_session(self) -> None:
        """Start a fresh chat session, resetting the GUI and the agent state."""
        self.chat_widget.clear()
        if hasattr(self.agent, "reset_chat_session"):
            self.agent.reset_chat_session()
        self.active_session_id = None
        self.sidebar_widget.list_widget.clearSelection()
        self.status_bar.showMessage("Started a fresh chat session.", 5000)

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
        from grc_agent.session_ops import (
            DISPLAY_ROLES,
            chat_message_from_payload,
        )
        from grc_agent.sessions_store import get_session_sync, list_messages_sync

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
            if msg.role not in {"assistant_model", "tool_model"}:
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
            msg.role not in {"assistant_model", "tool_model"}
            for msg in messages
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

        # 4. Replay display rows into the chat widget.
        display_replayed = 0
        for msg in messages:
            if msg.role == "system":
                continue
            if msg.role in DISPLAY_ROLES:
                self.chat_widget.append_message(msg.role, msg.text)
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
        self.chat_input.setEnabled(not busy and (
            self.agent.session is not None
            and self.agent.session.flowgraph is not None
        ))
        if hasattr(self, "validate_btn"):
            self.validate_btn.setEnabled(not busy and (
                self.agent.session is not None
                and self.agent.session.flowgraph is not None
            ))
        if hasattr(self, "model_toolbar"):
            self.model_toolbar.setEnabled(not busy)

    def on_validate_clicked(self) -> None:
        """Handler for 'Validate' button click."""
        if self.agent.session and self.agent.session.path:
            self.console_log.clear()
            self._validation_stdout = ""
            self._validation_stderr = ""
            self.process_manager.validate_graph(self.agent.session)
        else:
            self.on_process_status("Error: No active flowgraph session path.")

    def on_process_started(self) -> None:
        """Disable validate button while compilation is active."""
        self.validate_btn.setEnabled(False)
        self.status_bar.showMessage("Validating flowgraph...")
        self._last_applied_revision = None

    def on_process_stdout(self, text: str) -> None:
        """Append standard output chunks to the console log."""
        self._validation_stdout += text
        self.console_log.insertPlainText(text)
        self.console_log.ensureCursorVisible()

    def on_process_stderr(self, text: str) -> None:
        """Append standard error chunks to the console log."""
        self._validation_stderr += text
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

        session = self.agent.session

        self.refresh_inspector()

    def on_deferred_close(self, *args: Any) -> None:
        """Wait until all running subprocesses and threads are stopped before closing window."""
        if not self._pending_close:
            return

        is_running = False
        if self.process_manager:
            if (
                self.process_manager.compile_process
                and self.process_manager.compile_process.state()
                == QProcess.ProcessState.Running
            ):
                is_running = True
            if (
                self.process_manager.run_process
                and self.process_manager.run_process.state()
                == QProcess.ProcessState.Running
            ):
                is_running = True

        worker_running = self.thread is not None and self.thread.isRunning()

        if not is_running and not worker_running:
            self._safe_to_close = True
            self._pending_close = False
            self.close()

    # ------------------------------------------------------------------
    # File drag-and-drop
    # ------------------------------------------------------------------
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
                target.write_text(
                    self.chat_widget.export_markdown(), encoding="utf-8"
                )
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
