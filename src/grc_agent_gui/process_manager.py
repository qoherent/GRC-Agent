import os
import sys
import tempfile
import shutil
import logging
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

    def resolve_flowgraph_id(self, session) -> str:
        """Resolve the options block id parameter from the session flowgraph metadata."""
        try:
            return session.flowgraph.metadata["options"]["parameters"]["id"]
        except (KeyError, TypeError, AttributeError) as e:
            logger.warning(f"Could not resolve flowgraph ID from metadata: {e}. Defaulting to 'top_block'.")
            return "top_block"

    def compile_and_run(self, session) -> None:
        """First stage: Compile `.grc` file via `grcc` into a temporary directory."""
        self.stop()
        self.cleanup_temp_dir()
        
        self.active_session = session
        grc_path = getattr(session, "path", "")
        if not grc_path:
            self.status_message.emit("Error: No flowgraph path in active session.")
            return

        # Create persistent temporary directory for this compile/run cycle
        self._current_temp_dir = tempfile.mkdtemp(prefix="grc_agent_run_")
        
        # Spawn compilation QProcess
        self.compile_process = QProcess(self)
        self.compile_process.setWorkingDirectory(os.path.dirname(grc_path))
        self.compile_process.setProcessEnvironment(QProcessEnvironment.systemEnvironment())
        
        # Connect compilation standard outputs, error occurred and finished slots
        self.compile_process.errorOccurred.connect(self.on_compile_error)
        self.compile_process.finished.connect(self.on_compilation_finished)
        self.compile_process.readyReadStandardOutput.connect(self.on_compile_stdout)
        self.compile_process.readyReadStandardError.connect(self.on_compile_stderr)
        
        self.status_message.emit(f"Compiling {os.path.basename(grc_path)} with grcc...")
        self.started.emit()
        
        # Execute grcc -o <temp_dir> <grc_path>
        self.compile_process.start("grcc", ["-o", self._current_temp_dir, grc_path])

    def on_compile_error(self, error: QProcess.ProcessError) -> None:
        err_msg = self.compile_process.errorString() if self.compile_process else "Unknown error"
        self.status_message.emit(f"Compilation process error occurred: {err_msg}")

    def on_compile_stdout(self) -> None:
        if self.compile_process:
            data = self.compile_process.readAllStandardOutput().data().decode("utf-8", errors="replace")
            self.stdout_received.emit(data)

    def on_compile_stderr(self) -> None:
        if self.compile_process:
            data = self.compile_process.readAllStandardError().data().decode("utf-8", errors="replace")
            self.stderr_received.emit(data)

    def on_compilation_finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        """Triggered when compilation process terminates."""
        # Robust comparison to handle patched QProcess in test environments
        is_normal_exit = False
        if exit_status == 0:
            is_normal_exit = True
        elif getattr(exit_status, "name", "") == "NormalExit":
            is_normal_exit = True
        elif "NormalExit" in str(exit_status):
            is_normal_exit = True
            
        if exit_code == 0 and is_normal_exit:
            self.status_message.emit("Compilation succeeded. Spawning flowgraph script...")
            self.start_execution()
        else:
            self.status_message.emit(f"Compilation failed with exit code {exit_code}.")
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
            self.status_message.emit(f"Compiled executable script not found at expected path: {py_file}")
            self.finished.emit(-1)
            return
            
        # Spawn execution process
        self.run_process = QProcess(self)
        self.run_process.setWorkingDirectory(os.path.dirname(grc_path))
        self.run_process.setProcessEnvironment(QProcessEnvironment.systemEnvironment())
        
        self.run_process.errorOccurred.connect(self.on_run_error)
        self.run_process.readyReadStandardOutput.connect(self.on_run_stdout)
        self.run_process.readyReadStandardError.connect(self.on_run_stderr)
        self.run_process.finished.connect(self.on_run_finished)
        
        self.status_message.emit(f"Running: {sys.executable} {py_file}")
        self.run_process.start(sys.executable, [py_file])

    def on_run_error(self, error: QProcess.ProcessError) -> None:
        err_msg = self.run_process.errorString() if self.run_process else "Unknown error"
        self.status_message.emit(f"Flowgraph run process error occurred: {err_msg}")

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
        self.status_message.emit(f"Flowgraph finished with exit code {exit_code}.")
        self.finished.emit(exit_code)

    def stop(self) -> None:
        """Initiates a Two-Phase termination sequence for compiling/running flowgraphs."""
        if self.compile_process and self.compile_process.state() == QProcess.ProcessState.Running:
            self._terminate_with_fallback(self.compile_process, "compilation")
            
        if self.run_process and self.run_process.state() == QProcess.ProcessState.Running:
            self._terminate_with_fallback(self.run_process, "flowgraph execution")

    def _terminate_with_fallback(self, proc: QProcess, label: str) -> None:
        self.status_message.emit(f"Terminating {label} process (Phase 1)...")
        proc.terminate()
        # Schedule forceful kill fallback after 2000ms
        QTimer.singleShot(2000, lambda: self._force_kill_process(proc, label))

    def _force_kill_process(self, proc: QProcess, label: str) -> None:
        if proc and proc.state() == QProcess.ProcessState.Running:
            self.status_message.emit(f"{label.capitalize()} process failed to terminate. Forcefully killing (Phase 2)...")
            proc.kill()

    def shutdown(self) -> None:
        """Synchronously shutdown running processes on application exit to avoid leaks."""
        # 1. Compilation process cleanup
        if self.compile_process and self.compile_process.state() == QProcess.ProcessState.Running:
            self.compile_process.terminate()
            if not self.compile_process.waitForFinished(1000):
                self.compile_process.kill()
                self.compile_process.waitForFinished(500)
                
        # 2. Flowgraph execution process cleanup
        if self.run_process and self.run_process.state() == QProcess.ProcessState.Running:
            self.run_process.terminate()
            if not self.run_process.waitForFinished(2000):
                self.run_process.kill()
                self.run_process.waitForFinished(500)
                
        # 3. Persistent directory cleanup
        self.cleanup_temp_dir()

    def cleanup_temp_dir(self) -> None:
        """Removes the persistent temp directory containing the compiled artifact."""
        if self._current_temp_dir and os.path.exists(self._current_temp_dir):
            try:
                shutil.rmtree(self._current_temp_dir, ignore_errors=True)
            except Exception as e:
                logger.error(f"Failed to remove temp directory {self._current_temp_dir}: {e}")
            self._current_temp_dir = None
