import logging
import os
import shutil
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


from grc_agent_gui.styles import get_stylesheet


def _prune_orphan_temp_dirs() -> None:
    """Remove stale ``grc_agent_run_*`` directories from ``/tmp``.

    ``ProcessManager`` creates one of these for every compile/run
    cycle and normally removes it on graceful close. If the GUI
    crashes (segfault, OOM-kill, machine reboot) the directory is
    left behind. This function is called once at GUI startup to
    reclaim that space. A directory is treated as orphaned when
    its mtime is older than :data:`_GUI_TEMP_DIR_MIN_AGE_SECONDS`;
    that floor avoids racing with a freshly-spawned compile from a
    concurrently-running GUI process under a different user.
    """
    try:
        tmp_root = Path(tempfile.gettempdir())
    except OSError as exc:
        logger.debug("_prune_orphan_temp_dirs: gettempdir failed: %s", exc)
        return
    cutoff = time.time() - _GUI_TEMP_DIR_MIN_AGE_SECONDS
    try:
        entries = list(tmp_root.glob("grc_agent_run_*"))
    except OSError as exc:
        logger.debug("_prune_orphan_temp_dirs: glob failed on %s: %s", tmp_root, exc)
        return
    removed = 0
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
            removed += 1
        except OSError as exc:
            logger.debug("_prune_orphan_temp_dirs: rmtree failed on %s: %s", entry, exc)
    if removed:
        logger.info("_prune_orphan_temp_dirs removed=%d", removed)


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

    from PySide6.QtCore import QSettings

    settings = QSettings("GRC_Agent", "GUI")
    zoom_factor = float(settings.value("window/zoom_factor", 3.5))
    app.setStyleSheet(get_stylesheet(zoom_factor))

    config = load_app_config()
    # Overlay user preferences onto the config. Preferences carry only the
    # last-chosen provider; when it differs from the toml backend the overlay
    # re-resolves the chat + embedding models from .env for that backend.
    # Model names themselves are never sourced from preferences — .env is the
    # single source of truth. A malformed prefs file is logged and ignored by
    # the loader; a load failure here is non-fatal.
    try:
        from grc_agent.config import (
            apply_user_preferences_to_llama_config,
            load_user_preferences,
        )

        prefs = load_user_preferences()
        config = AppConfig(
            llama=apply_user_preferences_to_llama_config(config.llama, prefs),
            agent=config.agent,
        )
    except Exception as exc:  # noqa: BLE001 - defensive
        logger.debug("Failed to apply user preferences: %s", exc)

    # Graph load failures are non-fatal: capture the error and surface it
    # in-window so the user can open a different file. AGENTS.md 'non-blocking
    # flow' — never sys.exit on a load failure.
    graph_load_error: str | None = None
    session: FlowgraphSession | None = None
    if len(sys.argv) > 1:
        grc_path = Path(sys.argv[1])
        if not grc_path.is_file():
            graph_load_error = f"Graph file not found: {grc_path}"
        else:
            loaded = load_grc(grc_path)
            if isinstance(loaded, dict):
                graph_load_error = f"Failed to load graph: {loaded.get('message', 'unknown error')}"
            else:
                if not loaded.validate():
                    print(
                        f"Warning: loaded graph with validation failure "
                        f"(state={loaded.validation_state().get('state', 'unknown')}). "
                        f"Fix the graph using the agent before compiling.",
                        file=sys.stderr,
                    )
                session = loaded

    agent = GrcAgent(session=session)
    agent.warmup_vector_index()

    # The inline model toolbar (ModelToolbar) replaces the old setup wizard
    # and Model > Select Model dialog. It lives permanently at the top of
    # the chat pane. No pre-launch modal dialogs.

    print("Checking model server...", flush=True)
    result = bootstrap_runtime(config, init_retrieval=True)

    if not result.retrieval_ok and result.errors:
        print(f"Retrieval warning: {result.errors[0]}", file=sys.stderr)

    if result.catalog_root:
        agent.catalog_root = result.catalog_root

    # AGENTS.md 'non-blocking flow': never sys.exit on network failure.
    # MainWindow self-configures degraded mode (backend_reachable=False)
    # when bootstrap_result.launch_status in {"probe_failed","failed"} —
    # see main_window.py around line 274. The user recovers via the inline
    # model toolbar.
    window = MainWindow(
        agent,
        provider_config=result.provider_config,
        llama_config=config.llama,
        bootstrap_result=result,
    )
    app.aboutToQuit.connect(window.process_manager.shutdown)
    window.show()

    # Surface launch / load status in the status bar (and chat on error).
    model = result.model_alias or config.llama.model
    status = result.launch_status
    if graph_load_error:
        window.status_bar.showMessage(graph_load_error)
        window.chat_widget.append_error(graph_load_error)
    elif status == "failed":
        detail = result.errors[-1] if result.errors else "Backend unreachable"
        window.status_bar.showMessage(
            f"Backend unreachable — chat disabled ({detail}). Recover via the model toolbar."
        )
    elif status == "probe_failed":
        window.status_bar.showMessage(
            f"Backend unreachable at {result.server_url} — chat disabled. Recover via the model toolbar."
        )
    elif status == "started":
        window.status_bar.showMessage(f"Started {model} — ready")
    else:
        window.status_bar.showMessage(f"Connected to {model}")

    print("GRC Agent GUI started — check your desktop for the window.", flush=True)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
