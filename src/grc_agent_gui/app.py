import atexit
import os
import signal
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from grc_agent.agent import GrcAgent
from grc_agent.config import load_app_config
from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.session.load import load_grc
from grc_agent.startup import bootstrap_runtime
from grc_agent_gui.main_window import MainWindow


_STYLESHEET = """
QMainWindow {
    background-color: #1e1e2e;
}
QWidget {
    background-color: #1e1e2e;
    color: #cdd6f4;
    font-size: 13px;
}
QTextBrowser {
    background-color: #181825;
    border: 1px solid #45475a;
    border-radius: 4px;
    padding: 6px;
    selection-background-color: #45475a;
    selection-color: #cdd6f4;
}
QLineEdit {
    background-color: #313244;
    border: 1px solid #45475a;
    border-radius: 4px;
    padding: 6px 10px;
    color: #cdd6f4;
    font-size: 13px;
}
QLineEdit:focus {
    border: 1px solid #89b4fa;
}
QTableWidget, QTreeWidget, QListWidget {
    background-color: #181825;
    border: 1px solid #45475a;
    border-radius: 4px;
    alternate-background-color: #1e1e2e;
    outline: none;
}
QTableWidget::item, QTreeWidget::item, QListWidget::item {
    padding: 3px 6px;
    border: none;
}
QTableWidget::item:selected, QTreeWidget::item:selected, QListWidget::item:selected {
    background-color: #45475a;
    color: #cdd6f4;
}
QHeaderView::section {
    background-color: #313244;
    color: #cdd6f4;
    padding: 4px 8px;
    border: none;
    border-bottom: 1px solid #45475a;
    font-weight: bold;
}
QPlainTextEdit {
    background-color: #181825;
    border: 1px solid #45475a;
    border-radius: 4px;
    padding: 4px;
    color: #a6e3a1;
    font-family: monospace;
    font-size: 12px;
}
QSplitter::handle {
    background-color: #45475a;
}
QSplitter::handle:horizontal {
    width: 2px;
}
QSplitter::handle:vertical {
    height: 2px;
}
QStatusBar {
    background-color: #11111b;
    color: #a6adc8;
    border-top: 1px solid #45475a;
    font-size: 12px;
    padding: 2px 8px;
}
QStatusBar::item {
    border: none;
}
QPushButton {
    background-color: #313244;
    color: #cdd6f4;
    border: 1px solid #45475a;
    border-radius: 4px;
    padding: 5px 14px;
    font-size: 12px;
}
QPushButton:hover {
    background-color: #45475a;
}
QPushButton:pressed {
    background-color: #585b70;
}
QPushButton:disabled {
    background-color: #1e1e2e;
    color: #585b70;
    border-color: #45475a;
}
QLabel {
    color: #cdd6f4;
    font-size: 13px;
}
QToolTip {
    background-color: #313244;
    color: #cdd6f4;
    border: 1px solid #45475a;
    border-radius: 4px;
    padding: 4px;
}
QPushButton#runButton {
    background-color: #40a02b;
    color: #1e1e2e;
    font-weight: bold;
    border: 1px solid #40a02b;
}
QPushButton#runButton:hover {
    background-color: #54b43f;
}
QPushButton#runButton:pressed {
    background-color: #2e7d1e;
}
QPushButton#runButton:disabled {
    background-color: #313244;
    color: #585b70;
    border-color: #45475a;
}
QPushButton#stopButton {
    background-color: #d20f39;
    color: #1e1e2e;
    font-weight: bold;
    border: 1px solid #d20f39;
}
QPushButton#stopButton:hover {
    background-color: #e64545;
}
QPushButton#stopButton:pressed {
    background-color: #b00c2e;
}
QPushButton#stopButton:disabled {
    background-color: #313244;
    color: #585b70;
    border-color: #45475a;
}
"""


def main() -> None:
    """Launch the GRC Agent PySide6 GUI application.

    Usage:
        uv run grc-agent-gui [path/to/copy.grc]
    """
    app = QApplication(sys.argv)
    app.setStyleSheet(_STYLESHEET)

    config = load_app_config()

    session: FlowgraphSession | None = None
    if len(sys.argv) > 1:
        grc_path = Path(sys.argv[1])
        if not grc_path.is_file():
            print(f"Error: graph file not found: {grc_path}", file=sys.stderr)
            sys.exit(2)
        loaded = load_grc(grc_path)
        if isinstance(loaded, dict):
            print(
                f"Error: failed to load graph: {loaded.get('message', 'unknown error')}",
                file=sys.stderr,
            )
            sys.exit(2)
        if not loaded.validate():
            print(
                f"Error: refusing to load graph because validation failed "
                f"(state={loaded.validation_state().get('state', 'unknown')}).",
                file=sys.stderr,
            )
            sys.exit(2)
        session = loaded

    agent = GrcAgent(session=session)

    print("Checking model server...", flush=True)
    result = bootstrap_runtime(config, start_llama=True, init_retrieval=True)

    if not result.retrieval_ok and result.errors:
        print(f"Retrieval warning: {result.errors[0]}", file=sys.stderr)

    if result.catalog_root:
        agent.catalog_root = result.catalog_root

    if result.launch_status == "failed":
        print(
            f"Error: {result.errors[-1] if result.errors else 'Server startup failed'}",
            file=sys.stderr,
        )
        sys.exit(1)

    if result.launch_status == "started" and result.launch_pid is not None:
        _register_server_cleanup(result.launch_pid)

    window = MainWindow(agent, provider_config=result.provider_config)
    app.aboutToQuit.connect(window.process_manager.shutdown)
    window.show()

    model = result.model_alias or config.llama.model
    status = result.launch_status
    if status == "started":
        window.status_bar.showMessage(f"Started {model} — ready")
    else:
        window.status_bar.showMessage(f"Connected to {model}")

    print("GRC Agent GUI started — check your desktop for the window.", flush=True)
    sys.exit(app.exec())


def _register_server_cleanup(pid: int) -> None:
    """Arrange to terminate the llama-server process when this process exits."""

    def _cleanup():
        try:
            os.kill(pid, 0)
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass

    atexit.register(_cleanup)


if __name__ == "__main__":
    main()
