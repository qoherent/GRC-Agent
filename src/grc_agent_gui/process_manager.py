import logging
import os
import shutil
import sys
import tempfile

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal

logger = logging.getLogger(__name__)


class ProcessManager(QObject):
    """Manages compiling GRC flowgraphs and executing the compiled Python scripts.

    Implements a Split-Stage Execution framework:
    1. Compiles GRC to a temporary directory using `grcc`.
    2. Runs the compiled Python script using the python interpreter.

    Implements Two-Phase Termination (terminate -> kill fallback after 2000ms).
    Prevents zombie directories via manual rmtree cleanups.
    """

    # Signals for UI integration
    started = Signal()
    stdout_received = Signal(str)
    stderr_received = Signal(str)
    status_message = Signal(str)
    finished = Signal(int)  # Emits exit code when process chain completes

    def __init__(self, parent: QObject = None) -> None:
        super().__init__(parent)
        self.compile_process = None
        self.run_process = None
        self._current_temp_dir = None
        self.active_session = None
        # Per-slot kill timers, scoped to the two semantic process slots so
        # we never index by transient Python/C++ object IDs.
        self._compile_kill_timer = None
        self._run_kill_timer = None
        self._should_run_after_compile = False

    def resolve_flowgraph_id(self, session) -> str:
        """Resolve the top-block class name (single source of truth: ``FlowgraphSession.get_top_block_class_name``)."""
        return session.get_top_block_class_name()

    def _disconnect_and_reap(self, proc: QProcess, label: str) -> None:
        failed = 0
        for signal_name in (
            "errorOccurred",
            "finished",
            "readyReadStandardOutput",
            "readyReadStandardError",
        ):
            try:
                getattr(proc, signal_name).disconnect()
            except (TypeError, RuntimeError):
                failed += 1
        if failed == 4:
            logger.warning(
                "_disconnect_and_reap all disconnects failed for %s, skipping terminate", label
            )
            proc.deleteLater()
            return

        proc.terminate()

        # We need a timer to forcefully kill this specific process if it does not exit.
        # Since we disconnected it from the manager, we use a single-shot timer that kills it.
        kill_timer = QTimer(self)
        kill_timer.setSingleShot(True)
        kill_timer.setInterval(2000)

        def force_kill_orphaned():
            try:
                if proc.state() == QProcess.ProcessState.Running:
                    logger.warning(f"Orphaned {label} process refused SIGTERM, killing...")
                    proc.kill()
            except RuntimeError:
                pass
            proc.deleteLater()
            kill_timer.deleteLater()

        kill_timer.timeout.connect(force_kill_orphaned)
        kill_timer.start()

        # Also connect proc.finished to its own deleteLater and kill_timer stops to clean up if it exits early.
        proc.finished.connect(proc.deleteLater)
        proc.finished.connect(kill_timer.stop)
        proc.finished.connect(kill_timer.deleteLater)

    def _reap_active_processes(self) -> None:
        """Disconnect and forcefully reap any active processes before re-starting compile/run."""
        if self.compile_process:
            if self.compile_process.state() == QProcess.ProcessState.Running:
                self._disconnect_and_reap(self.compile_process, "compilation")
            else:
                self.compile_process.deleteLater()
            self.compile_process = None
            if self._compile_kill_timer is not None:
                self._compile_kill_timer.stop()
                self._compile_kill_timer.deleteLater()
                self._compile_kill_timer = None

        if self.run_process:
            if self.run_process.state() == QProcess.ProcessState.Running:
                self._disconnect_and_reap(self.run_process, "flowgraph execution")
            else:
                self.run_process.deleteLater()
            self.run_process = None
            if self._run_kill_timer is not None:
                self._run_kill_timer.stop()
                self._run_kill_timer.deleteLater()
                self._run_kill_timer = None

    def validate_graph(self, session) -> None:
        """Compile `.grc` file via `grcc` to validate the graph without executing."""
        if (
            self.compile_process and self.compile_process.state() == QProcess.ProcessState.Running
        ) or (self.run_process and self.run_process.state() == QProcess.ProcessState.Running):
            self.status_message.emit("Validation already in progress; ignoring re-entrant request.")
            logger.warning("Re-entrant validate_graph ignored while a validation is active.")
            return

        self._reap_active_processes()
        self.cleanup_temp_dir()

        self.active_session = session
        grc_path = str(getattr(session, "path", "") or "")
        if not grc_path:
            self.status_message.emit("Error: No flowgraph path in active session.")
            return
        grc_path = os.path.abspath(grc_path)

        # Create persistent temporary directory for this compile/run cycle
        self._current_temp_dir = tempfile.mkdtemp(prefix="grc_agent_run_")

        # Spawn compilation QProcess. If construction fails (extremely rare
        # for QProcess, but possible on exotic platforms), do not leak the
        # freshly created temp directory.
        try:
            self.compile_process = QProcess(self)
            self.compile_process.setWorkingDirectory(os.path.dirname(grc_path))
            self.compile_process.setProcessEnvironment(QProcessEnvironment.systemEnvironment())

            # Connect compilation standard outputs, error occurred and finished slots
            self.compile_process.errorOccurred.connect(self.on_compile_error)
            self.compile_process.finished.connect(self.on_compilation_finished)
            self.compile_process.readyReadStandardOutput.connect(self.on_compile_stdout)
            self.compile_process.readyReadStandardError.connect(self.on_compile_stderr)
        except Exception as e:
            logger.exception("Failed to construct compile QProcess")
            self.status_message.emit(f"Failed to spawn compile process: {e}")
            self.cleanup_temp_dir()
            self.finished.emit(-1)
            return

        self.status_message.emit(f"Validating {os.path.basename(grc_path)} with grcc...")
        self.started.emit()

        # Execute grcc -o <temp_dir> <grc_path>
        self.compile_process.start("grcc", ["-o", self._current_temp_dir, grc_path])

    def on_compile_error(self, error: QProcess.ProcessError) -> None:
        err_msg = self.compile_process.errorString() if self.compile_process else "Unknown error"
        self.status_message.emit(f"Compilation process error occurred: {err_msg}")
        # Clean up FailedToStart since finished is not emitted in that case.
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
        # Stop and delete the kill timer if it's running
        if self._compile_kill_timer is not None:
            self._compile_kill_timer.stop()
            self._compile_kill_timer.deleteLater()
            self._compile_kill_timer = None

        proc = self.compile_process
        if proc:
            self.compile_process = None
            proc.deleteLater()

        # Robust comparison to handle patched QProcess in test environments.
        # Keeps the integer branch (covers int test mocks) and the .name branch
        # (covers Qt's IntEnum). The substring branch was removed — it accepted
        # any string containing "NormalExit", including the class name itself.
        is_normal_exit = exit_status == 0 or getattr(exit_status, "name", "") == "NormalExit"

        if exit_code == 0 and is_normal_exit:
            self.status_message.emit("Validation passed.")
            if getattr(self, "_should_run_after_compile", False):
                self._should_run_after_compile = False
                self.start_execution()
            else:
                self.finished.emit(0)
        else:
            self._should_run_after_compile = False
            self.status_message.emit(f"Validation failed with exit code {exit_code}.")
            self.finished.emit(exit_code)

    def start_execution(self) -> None:
        """Second stage: execute the compiled Python script using the python interpreter."""
        if not self.active_session or not self._current_temp_dir:
            return

        flowgraph_id = self.resolve_flowgraph_id(self.active_session)
        grc_path = self.active_session.path
        py_file = os.path.join(self._current_temp_dir, f"{flowgraph_id}.py")

        # Verify that the compiled artifact exists before running it
        if not os.path.exists(py_file):
            self.status_message.emit(
                f"Compiled executable script not found at expected path: {py_file}"
            )
            self.finished.emit(-1)
            return

        try:
            self.run_process = QProcess(self)
            self.run_process.setWorkingDirectory(os.path.dirname(grc_path))
            self.run_process.setProcessEnvironment(QProcessEnvironment.systemEnvironment())

            self.run_process.errorOccurred.connect(self.on_run_error)
            self.run_process.readyReadStandardOutput.connect(self.on_run_stdout)
            self.run_process.readyReadStandardError.connect(self.on_run_stderr)
            self.run_process.finished.connect(self.on_run_finished)
        except Exception as e:
            logger.exception("Failed to construct run QProcess")
            self.status_message.emit(f"Failed to spawn run process: {e}")
            self.cleanup_temp_dir()
            self.finished.emit(-1)
            return

        self.status_message.emit(f"Running: {sys.executable} {py_file}")
        self.run_process.start(sys.executable, [py_file])

    def on_run_error(self, error: QProcess.ProcessError) -> None:
        err_msg = self.run_process.errorString() if self.run_process else "Unknown error"
        self.status_message.emit(f"Flowgraph run process error occurred: {err_msg}")
        # Clean up FailedToStart since finished is not emitted in that case.
        if error == QProcess.ProcessError.FailedToStart:
            proc = self.run_process
            if proc:
                self.run_process = None
                proc.deleteLater()
            self.finished.emit(-1)

    def on_run_stdout(self) -> None:
        if self.run_process:
            data = self.run_process.readAllStandardOutput().data().decode("utf-8", errors="replace")
            self.stdout_received.emit(data)

    def on_run_stderr(self) -> None:
        if self.run_process:
            data = self.run_process.readAllStandardError().data().decode("utf-8", errors="replace")
            self.stderr_received.emit(data)

    def on_run_finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        """Triggered when flowgraph run process terminates."""
        # Stop and delete the kill timer if it's running
        if self._run_kill_timer is not None:
            self._run_kill_timer.stop()
            self._run_kill_timer.deleteLater()
            self._run_kill_timer = None

        proc = self.run_process
        if proc:
            self.run_process = None
            proc.deleteLater()

        self.status_message.emit(f"Flowgraph finished with exit code {exit_code}.")
        self.finished.emit(exit_code)

    def stop(self) -> None:
        """Initiates a Two-Phase termination sequence for compiling/running flowgraphs."""
        if self.compile_process and self.compile_process.state() == QProcess.ProcessState.Running:
            self._terminate_with_fallback(self.compile_process, "compilation")

        if self.run_process and self.run_process.state() == QProcess.ProcessState.Running:
            self._terminate_with_fallback(self.run_process, "flowgraph execution")

    def _terminate_with_fallback(
        self,
        proc: QProcess,
        label: str,
    ) -> None:
        """Send SIGTERM and schedule a 2s SIGKILL fallback."""
        self.status_message.emit(f"Terminating {label} process (Phase 1)...")
        proc.terminate()

        # Cancel any existing pending kill for this slot, then schedule a
        # fresh one bound to the new QProcess.
        if label == "compilation":
            if self._compile_kill_timer is not None:
                self._compile_kill_timer.stop()
                self._compile_kill_timer.deleteLater()
                self._compile_kill_timer = None
        else:
            if self._run_kill_timer is not None:
                self._run_kill_timer.stop()
                self._run_kill_timer.deleteLater()
                self._run_kill_timer = None

        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(2000)
        timer.timeout.connect(lambda p=proc, lbl=label: self._force_kill_process(p, lbl))
        timer.start()

        if label == "compilation":
            self._compile_kill_timer = timer
        else:
            self._run_kill_timer = timer

    def _force_kill_process(self, proc: QProcess, label: str) -> None:
        try:
            if proc and proc.state() == QProcess.ProcessState.Running:
                self.status_message.emit(
                    f"{label.capitalize()} process failed to terminate. Forcefully killing (Phase 2)..."
                )
                proc.kill()
        except RuntimeError:
            pass

        # Always clear the slot timer after firing, regardless of kill outcome.
        if label == "compilation":
            if self._compile_kill_timer is not None:
                self._compile_kill_timer.deleteLater()
                self._compile_kill_timer = None
        else:
            if self._run_kill_timer is not None:
                self._run_kill_timer.deleteLater()
                self._run_kill_timer = None

    def shutdown(self) -> None:
        """Best-effort synchronous shutdown on application exit.

        Each waitForFinished is capped at 200ms to avoid blocking the Qt
        aboutToQuit event loop past Qt's grace period. If a flowgraph
        refuses SIGTERM within 400ms total, it is hard-killed; the spec
        requires this to prevent hardware lock leaks.
        """
        # 1. Compilation process cleanup
        if self.compile_process:
            if self.compile_process.state() == QProcess.ProcessState.Running:
                self.compile_process.terminate()
                if not self.compile_process.waitForFinished(200):
                    self.compile_process.kill()
                    self.compile_process.waitForFinished(200)
            self.compile_process.deleteLater()
            self.compile_process = None

        # 2. Flowgraph execution process cleanup
        if self.run_process:
            if self.run_process.state() == QProcess.ProcessState.Running:
                self.run_process.terminate()
                if not self.run_process.waitForFinished(200):
                    self.run_process.kill()
                    self.run_process.waitForFinished(200)
            self.run_process.deleteLater()
            self.run_process = None

        # 3. Cancel any pending per-slot kill timers
        for attr in ("_compile_kill_timer", "_run_kill_timer"):
            timer = getattr(self, attr, None)
            if timer is not None:
                timer.stop()
                timer.deleteLater()
                setattr(self, attr, None)

        # 4. Persistent directory cleanup
        self.cleanup_temp_dir()

    def cleanup_temp_dir(self) -> None:
        """Removes the persistent temp directory containing the compiled artifact."""
        if self._current_temp_dir and os.path.exists(self._current_temp_dir):
            try:
                shutil.rmtree(self._current_temp_dir, ignore_errors=True)
            except Exception as e:
                logger.error(f"Failed to remove temp directory {self._current_temp_dir}: {e}")
            self._current_temp_dir = None
