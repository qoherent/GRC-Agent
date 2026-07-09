import logging
import os
import shutil
import tempfile

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal

logger = logging.getLogger(__name__)


# Two-phase shutdown timing — both constants derived from the spec's
# "graceful-then-forceful" termination contract. One source of truth.
_KILL_FALLBACK_MS = 2000
_SYNC_SHUTDOWN_WAIT_MS = 200
# Signals subscribed on every QProcess. The cleanup reaper disconnects
# all of them; the constant avoids a magic number for "did every signal
# disconnect succeed?".
_QPROC_SIGNAL_NAMES = (
    "errorOccurred",
    "finished",
    "readyReadStandardOutput",
    "readyReadStandardError",
)


class ProcessManager(QObject):
    """Spawns ``grcc`` to validate the active flowgraph and streams its output.

    Single-stage validator: the GRC source is compiled into a temporary
    directory; the GUI runs the compile and surfaces stdout/stderr to the
    console. A stale compile process (re-entrant ``validate_graph`` call)
    is reaped via two-phase termination (terminate, then SIGKILL after a
    grace period, see ``_disconnect_and_reap``); app-exit shutdown is a
    separate, synchronous best-effort path (``shutdown``).
    """

    started = Signal()
    stdout_received = Signal(str)
    stderr_received = Signal(str)
    status_message = Signal(str)
    finished = Signal(int)  # Emits exit code when compile completes

    def __init__(self, parent: QObject = None) -> None:
        super().__init__(parent)
        self.compile_process: QProcess | None = None
        self._current_temp_dir: str | None = None

    def _disconnect_and_reap(self, proc: QProcess, label: str) -> None:
        failed = 0
        for signal_name in _QPROC_SIGNAL_NAMES:
            try:
                getattr(proc, signal_name).disconnect()
            except (TypeError, RuntimeError):
                failed += 1
        if failed == len(_QPROC_SIGNAL_NAMES):
            logger.warning(
                "_disconnect_and_reap all disconnects failed for %s, skipping terminate", label
            )
            proc.deleteLater()
            return

        proc.terminate()

        # SIGKILL fallback if the process refuses to exit gracefully.
        kill_timer = QTimer(self)
        kill_timer.setSingleShot(True)
        kill_timer.setInterval(_KILL_FALLBACK_MS)

        def force_kill_orphaned():
            try:
                if proc.state() == QProcess.ProcessState.Running:
                    logger.warning(f"Orphaned {label} process refused SIGTERM, killing...")
                    proc.kill()
            except RuntimeError:
                logger.debug("orphaned proc already destroyed", exc_info=True)
            proc.deleteLater()
            kill_timer.deleteLater()

        kill_timer.timeout.connect(force_kill_orphaned)
        kill_timer.start()

        # Clean up early if the process exits before the timer fires.
        proc.finished.connect(proc.deleteLater)
        proc.finished.connect(kill_timer.stop)
        proc.finished.connect(kill_timer.deleteLater)

    def _reap_active_processes(self) -> None:
        """Disconnect and forcefully reap the active compile process before re-starting."""
        proc = self.compile_process
        if not proc:
            return
        if proc.state() == QProcess.ProcessState.Running:
            self._disconnect_and_reap(proc, "compilation")
        else:
            proc.deleteLater()
        self.compile_process = None

    def validate_graph(self, session) -> None:
        """Compile ``.grc`` file via ``grcc`` to validate the graph without executing."""
        if self.compile_process and self.compile_process.state() == QProcess.ProcessState.Running:
            self.status_message.emit("Validation already in progress; ignoring re-entrant request.")
            logger.warning("Re-entrant validate_graph ignored while a validation is active.")
            return

        self._reap_active_processes()
        self.cleanup_temp_dir()

        grc_path = str(getattr(session, "path", "") or "")
        if not grc_path:
            self.status_message.emit("Error: No flowgraph path in active session.")
            return
        grc_path = os.path.abspath(grc_path)

        # Persistent temp directory for this compile cycle.
        self._current_temp_dir = tempfile.mkdtemp(prefix="grc_agent_run_")

        self.compile_process = QProcess(self)
        self.compile_process.setWorkingDirectory(os.path.dirname(grc_path))
        self.compile_process.setProcessEnvironment(QProcessEnvironment.systemEnvironment())

        self.compile_process.errorOccurred.connect(self.on_compile_error)
        self.compile_process.finished.connect(self.on_compilation_finished)
        self.compile_process.readyReadStandardOutput.connect(self.on_compile_stdout)
        self.compile_process.readyReadStandardError.connect(self.on_compile_stderr)

        self.status_message.emit(f"Validating {os.path.basename(grc_path)} with grcc...")
        self.started.emit()
        self.compile_process.start("grcc", ["-o", self._current_temp_dir, grc_path])

    def on_compile_error(self, error: QProcess.ProcessError) -> None:
        err_msg = self.compile_process.errorString() if self.compile_process else "Unknown error"
        self.status_message.emit(f"Compilation process error occurred: {err_msg}")
        # ``finished`` is not emitted on FailedToStart, so clean up here.
        if error == QProcess.ProcessError.FailedToStart:
            proc = self.compile_process
            if proc:
                self.compile_process = None
                proc.deleteLater()
            self.finished.emit(-1)

    def on_compile_stdout(self) -> None:
        if self.compile_process:
            data = (
                self.compile_process.readAllStandardOutput()
                .data()
                .decode("utf-8", errors="replace")
            )
            self.stdout_received.emit(data)

    def on_compile_stderr(self) -> None:
        if self.compile_process:
            data = (
                self.compile_process.readAllStandardError().data().decode("utf-8", errors="replace")
            )
            self.stderr_received.emit(data)

    def on_compilation_finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        """Triggered when compilation process terminates."""
        proc = self.compile_process
        if proc:
            self.compile_process = None
            proc.deleteLater()

        # Robust comparison — covers both int test mocks and Qt's IntEnum.
        is_normal_exit = exit_status == 0 or getattr(exit_status, "name", "") == "NormalExit"

        if exit_code == 0 and is_normal_exit:
            self.status_message.emit("Validation passed.")
            self.finished.emit(0)
        else:
            self.status_message.emit(f"Validation failed with exit code {exit_code}.")
            self.finished.emit(exit_code)

    def shutdown(self) -> None:
        """Best-effort synchronous shutdown on application exit.

        Each waitForFinished is capped at ``_SYNC_SHUTDOWN_WAIT_MS`` to
        avoid blocking the Qt aboutToQuit event loop past Qt's grace
        period. If the process refuses SIGTERM within ~2× that timeout
        total, it is hard-killed.
        """
        if self.compile_process:
            if self.compile_process.state() == QProcess.ProcessState.Running:
                self.compile_process.terminate()
                if not self.compile_process.waitForFinished(_SYNC_SHUTDOWN_WAIT_MS):
                    self.compile_process.kill()
                    self.compile_process.waitForFinished(_SYNC_SHUTDOWN_WAIT_MS)
            self.compile_process.deleteLater()
            self.compile_process = None

        self.cleanup_temp_dir()

    def cleanup_temp_dir(self) -> None:
        """Removes the persistent temp directory containing the compiled artifact."""
        if self._current_temp_dir and os.path.exists(self._current_temp_dir):
            try:
                shutil.rmtree(self._current_temp_dir, ignore_errors=True)
            except OSError as exc:
                logger.error("Failed to remove temp directory %s: %s", self._current_temp_dir, exc)
            self._current_temp_dir = None
