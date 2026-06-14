import atexit
import logging
import os
import shutil
import signal
import sys
import tempfile
import time
from pathlib import Path

from grc_agent.agent import GrcAgent
from grc_agent.config import AppConfig, load_app_config
from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.session import load_grc
from grc_agent.startup import bootstrap_runtime
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from grc_agent_gui.main_window import MainWindow

logger = logging.getLogger(__name__)


# Age (seconds) below which a `grc_agent_run_*` temp dir is treated as
# in-flight and *not* pruned. 1 hour is conservative — the actual
# `grcc` compile + first execute cycle completes in seconds under
# normal conditions; the floor protects against racing with another
# live GUI process whose compile just started.
_GUI_TEMP_DIR_MIN_AGE_SECONDS = 3600


_STYLESHEET = """
QMainWindow {
    background-color: #1e1e2e;
}
QWidget {
    background-color: #1e1e2e;
    color: #cdd6f4;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    font-size: 13px;
}
QScrollBar:vertical {
    border: none;
    background: #11111b;
    width: 8px;
    margin: 0px;
    border-radius: 4px;
}
QScrollBar::handle:vertical {
    background: #45475a;
    min-height: 20px;
    border-radius: 4px;
}
QScrollBar::handle:vertical:hover {
    background: #585b70;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    background: none;
}
QScrollBar:horizontal {
    border: none;
    background: #11111b;
    height: 8px;
    margin: 0px;
    border-radius: 4px;
}
QScrollBar::handle:horizontal {
    background: #45475a;
    min-width: 20px;
    border-radius: 4px;
}
QScrollBar::handle:horizontal:hover {
    background: #585b70;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0px;
}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
    background: none;
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


def _prune_orphan_temp_dirs() -> list[str]:
    """Remove stale ``grc_agent_run_*`` directories from ``/tmp``.

    ``ProcessManager`` creates one of these for every compile/run
    cycle and normally removes it on graceful close. If the GUI
    crashes (segfault, OOM-kill, machine reboot) the directory is
    left behind. This function is called once at GUI startup to
    reclaim that space. A directory is treated as orphaned when
    its mtime is older than :data:`_GUI_TEMP_DIR_MIN_AGE_SECONDS`;
    that floor avoids racing with a freshly-spawned compile from a
    concurrently-running GUI process under a different user.

    Returns the list of removed paths (empty if none).
    """
    try:
        tmp_root = Path(tempfile.gettempdir())
    except OSError as exc:
        logger.debug("_prune_orphan_temp_dirs: gettempdir failed: %s", exc)
        return []
    removed: list[str] = []
    cutoff = time.time() - _GUI_TEMP_DIR_MIN_AGE_SECONDS
    try:
        entries = list(tmp_root.glob("grc_agent_run_*"))
    except OSError as exc:
        logger.debug("_prune_orphan_temp_dirs: glob failed on %s: %s", tmp_root, exc)
        return removed
    for entry in entries:
        if not entry.is_dir():
            continue
        try:
            mtime = entry.stat().st_mtime
        except OSError as exc:
            logger.debug("_prune_orphan_temp_dirs: stat failed on %s: %s", entry, exc)
            continue
        if mtime >= cutoff:
            # Recent enough to be in flight from another GUI process.
            continue
        try:
            shutil.rmtree(entry, ignore_errors=True)
            removed.append(str(entry))
        except OSError as exc:
            logger.debug("_prune_orphan_temp_dirs: rmtree failed on %s: %s", entry, exc)
    if removed:
        logger.info("_prune_orphan_temp_dirs removed=%d", len(removed))
    return removed


def main() -> None:
    """Launch the GRC Agent PySide6 GUI application.

    Usage:
        uv run grc-agent-gui [path/to/copy.grc]
    """
    # Reclaim temp dirs left behind by a previously-crashed GUI.
    _prune_orphan_temp_dirs()

    # Ensure GUI module loggers (which currently route to `logger.warning` /
    # `logger.error` calls scattered across the GUI) have somewhere to land.
    # The level is configurable via `GRC_AGENT_LOG_LEVEL` (e.g. "DEBUG").
    log_level = os.environ.get("GRC_AGENT_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    app = QApplication(sys.argv)
    app.setApplicationName("GRC Agent")
    app.setOrganizationName("Qoherent")
    app.setApplicationDisplayName("GRC Agent Companion")
    icon_path = Path(__file__).parent / "resources" / "icon.png"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    app.setStyleSheet(_STYLESHEET)

    config = load_app_config()
    # Overlay user preferences (e.g. the model last picked in the
    # GUI) onto the config. Preferences win over ``grc_agent.toml``
    # for the model field; everything else is preserved. A malformed
    # prefs file is logged and ignored by the loader; a load failure
    # here is non-fatal.
    try:
        from grc_agent.config import (
            apply_user_preferences_to_llama_config,
            load_user_preferences,
        )

        prefs = load_user_preferences()
        if prefs.last_model.model:
            config = AppConfig(
                llama=apply_user_preferences_to_llama_config(
                    config.llama, prefs
                ),
                agent=config.agent,
            )
    except Exception as exc:  # noqa: BLE001 - defensive
        logger.debug("Failed to apply user preferences: %s", exc)

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
                f"Warning: loaded graph with validation failure "
                f"(state={loaded.validation_state().get('state', 'unknown')}). "
                f"Fix the graph using the agent before compiling.",
                file=sys.stderr,
            )
        session = loaded

    agent = GrcAgent(session=session)

    # The provider picker + Ollama setup flow is now embedded in the
    # main window itself (setup_mode=True).  No pre-launch modal
    # dialogs.  The user sees the picker inside the main view on
    # every launch, picks their provider, and on Confirm the window
    # swaps to the chat / inspector work area.  If the daemon is
    # down, the existing Phase 2 ``backend_unreachable`` degraded
    # path handles it inside the chat view.

    print("Checking model server...", flush=True)
    result = bootstrap_runtime(config, init_retrieval=True)

    if not result.retrieval_ok and result.errors:
        print(f"Retrieval warning: {result.errors[0]}", file=sys.stderr)

    if result.catalog_root:
        agent.catalog_root = result.catalog_root

    if result.launch_status == "failed":
        print(
            f"Error: {result.errors[-1] if result.errors else 'Server startup failed'}",
            file=sys.stderr,
        )
        if result.error_type == "backend_server_missing":
            print(
                "Hint: ensure the backend server is installed and on PATH. "
                "See the README install table.",
                file=sys.stderr,
            )
        elif result.error_type == "model_not_found":
            print(
                "Hint: set [llama].model to a valid model name for your backend. "
                "Use `uv run grc-agent init` to write a starter config.",
                file=sys.stderr,
            )
        sys.exit(1)

    if result.launch_status == "started" and result.launch_pid is not None:
        _register_server_cleanup(result.launch_pid)

    window = MainWindow(
        agent,
        provider_config=result.provider_config,
        llama_config=config.llama,
        bootstrap_result=result,
        setup_mode=True,
    )
    app.aboutToQuit.connect(window.process_manager.shutdown)
    window.show()

    model = result.model_alias or config.llama.model
    status = result.launch_status
    if status == "probe_failed":
        window.status_bar.showMessage(
            f"Backend unreachable at {result.server_url} — chat disabled, "
            f"use Model > Select Model to recover."
        )
    elif status == "started":
        window.status_bar.showMessage(f"Started {model} — ready")
    else:
        window.status_bar.showMessage(f"Connected to {model}")

    print("GRC Agent GUI started — check your desktop for the window.", flush=True)
    sys.exit(app.exec())


def _register_server_cleanup(pid: int) -> None:
    """Arrange to terminate the backend server process when this process exits."""

    def _cleanup():
        try:
            os.kill(pid, 0)
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass

    atexit.register(_cleanup)


if __name__ == "__main__":
    main()
