import json
import logging
import sys
from pathlib import Path
from typing import Any

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
from .process_manager import ProcessManager
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


class InspectorWorkerSignals(QObject):
    finished = Signal(dict)
    error = Signal(str)


class InspectorRunnable(QRunnable):
    def __init__(self, agent: Any) -> None:
        super().__init__()
        self.agent = agent
        self.signals = InspectorWorkerSignals()

    def run(self) -> None:
        try:
            from grc_agent.runtime.wrappers.inspect_graph import inspect_graph
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
        self, agent: Any, provider_config: Any = None, parent: QWidget = None
    ) -> None:
        super().__init__(parent)
        self.agent = agent
        self.provider_config = provider_config
        self.worker = None
        self.thread = None
        self.process_manager = None
        self._safe_to_close = False

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

        # Instantiate primary vertical splitter
        v_splitter = QSplitter(Qt.Vertical, central_widget)
        self.v_splitter = v_splitter
        main_layout.addWidget(v_splitter)

        # Instantiate horizontal splitter for upper sidepanels
        splitter = QSplitter(Qt.Horizontal, v_splitter)
        self.h_splitter = splitter

        # Instantiate ChatWidget sidekick pane
        self.chat_widget = ChatWidget(splitter)
        splitter.addWidget(self.chat_widget)

        # Instantiate InspectorWidget sidekick pane
        self.inspector_widget = InspectorWidget(splitter)
        splitter.addWidget(self.inspector_widget)

        # Set splitter proportions (e.g. 60% chat, 40% inspector)
        splitter.setSizes([480, 320])
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

        # Aliases for backwards compatibility and automated testing
        self.chat_input = self.chat_widget.chat_input
        self.chat_display = self.chat_widget.chat_display
        self.chat_input.returnPressed.connect(self.send_prompt)
        self.chat_input.installEventFilter(self)

        # Restore saved splitter state
        h_state = self._settings.value("window/h_splitter")
        if h_state:
            self.h_splitter.restoreState(h_state)
        v_state = self._settings.value("window/v_splitter")
        if v_state:
            self.v_splitter.restoreState(v_state)

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

        # Status Bar
        self.status_bar = QStatusBar(self)
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Ready")

        self.validation_label = QLabel("Unknown", self)
        self.status_bar.addPermanentWidget(self.validation_label)
        self.update_ui_state()

    def update_ui_state(self) -> None:
        """Enable or disable chat and validation controls based on active session state."""
        has_graph = (
            self.agent.session is not None
            and self.agent.session.flowgraph is not None
        )
        self.chat_input.setEnabled(has_graph)
        self.validate_btn.setEnabled(has_graph)
        self.save_action.setEnabled(has_graph)
        self.export_chat_action.setEnabled(has_graph)

        if has_graph:
            self.chat_input.setPlaceholderText("Ask the assistant to modify or summarize the flowgraph...")
        else:
            self.chat_input.setPlaceholderText("Please load a .grc flowgraph (File -> Open or Drag & Drop) to start chatting...")
            self.validation_label.setText("No Graph")
            self.validation_label.setStyleSheet("color: #a6adc8;")

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if obj is self.chat_input and event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Return and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
                self.send_prompt()
                return True
            if event.key() == Qt.Key.Key_Escape:
                self.chat_input.clear()
                return True
        return super().eventFilter(obj, event)

    def send_prompt(self) -> None:
        """Read the input box, format as a user message, and start worker generation."""
        prompt = self.chat_input.text().strip()
        if prompt:
            self.chat_input.clear()
            if prompt.startswith("/save"):
                self.chat_widget.append_message("user", prompt)
                self.save_file()
                return
            self.chat_widget.append_message("user", prompt)
            self.start_generation(prompt)

    def start_generation(self, prompt: str) -> None:
        """Initialize and run the AgentWorker inside a QThread."""
        if self.thread is not None and self.thread.isRunning():
            logger.warning("Agent worker thread is already running.")
            return

        self.thread = QThread(self)
        self.worker = AgentWorker(self.agent, prompt, self.provider_config)
        self.worker.moveToThread(self.thread)

        # Setup signal routing
        self.thread.started.connect(self.worker.run_turn)

        self.worker.started.connect(self.on_worker_started)
        self.worker.tool_started.connect(self.on_tool_started)
        self.worker.tool_finished.connect(self.on_tool_finished)
        self.worker.response_chunk.connect(self.on_response_chunk)
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
        self.chat_widget.append_status(f"Running {name}...")

    def on_tool_finished(self, name: str, result: str) -> None:
        """Handle tool completion: show mutations, surface errors."""
        if name == "change_graph" and result:
            self.chat_widget.append_mutation(result)
            self.refresh_inspector()
        if self._result_is_error(result):
            self.chat_widget.append_error(f"{name}: {result[:300]}")
        self.status_bar.showMessage("Agent is thinking...")
        self.status_bar.setStyleSheet("")

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
        assistant_text = result.get("assistant_text", "")
        self.chat_widget.finalize_stream(assistant_text)
        self.refresh_inspector()
        self.status_bar.setStyleSheet("")
        self.status_bar.showMessage("Ready")
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
        from grc_agent.session.load import load_grc
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
        self.agent.history = []
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
        from grc_agent.runtime.wrappers.change_graph import _write_committed_changes
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
        if session:
            session.last_validation_ok = (exit_code == 0)
            session.last_validation_returncode = exit_code
            session.last_validation_stdout = getattr(self, "_validation_stdout", "")
            session.last_validation_stderr = getattr(self, "_validation_stderr", "")
            session.last_validation_revision = session.state_revision

        self.refresh_inspector()

    def closeEvent(self, event: Any) -> None:
        """Intercept application close to ensure background threads and run processes are reaped."""
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

        if (is_running or worker_running) and not self._safe_to_close:
            if not self._pending_close:
                event.ignore()
                self._pending_close = True
                self.on_process_status(
                    "Shutting down running processes and thread workers..."
                )
                self.validate_btn.setEnabled(False)
                self.chat_input.setEnabled(False)

                if self.worker:
                    self.worker.cancel()

                if is_running:
                    self.process_manager.stop()
            else:
                event.ignore()
        else:
            self._settings.setValue("window/geometry", self.saveGeometry())
            self._settings.setValue("window/h_splitter", self.h_splitter.saveState())
            self._settings.setValue("window/v_splitter", self.v_splitter.saveState())
            # Safe path: clean up temp directories and thread workers
            if self.process_manager:
                self.process_manager.cleanup_temp_dir()
            if self.worker:
                self.worker.cancel()
            self.cleanup_thread()
            event.accept()

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
    def dragEnterEvent(self, event: Any) -> None:
        """Accept the drag if it contains at least one .grc file."""
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.isLocalFile() and url.toLocalFile().lower().endswith(".grc"):
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dropEvent(self, event: Any) -> None:
        """Open the first .grc file dropped onto the window."""
        for url in event.mimeData().urls():
            if url.isLocalFile() and url.toLocalFile().lower().endswith(".grc"):
                self.open_file(Path(url.toLocalFile()))
                event.acceptProposedAction()
                return
        event.ignore()

    # ------------------------------------------------------------------
    # File menu: Open Output Folder, Export Chat
    # ------------------------------------------------------------------
    def open_output_folder(self) -> None:
        """Open the directory where the package writes its state."""
        try:
            from grc_agent.cli import _collect_package_paths
        except ImportError:
            self._show_error(
                "Output folder unavailable",
                "Could not import the CLI's path catalog. Run "
                "`uv run grc-agent paths` from a terminal to list the locations.",
            )
            return
        paths = _collect_package_paths()
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
