"""Detect flowgraph execution failures from GRC's native console message
stream (``gnuradio.grc.core.Messages``) and report them via a callback.

GRC's "Execute" toolbar button runs the generated flowgraph as a subprocess
and streams its merged stdout/stderr through a simple global pub/sub
(``Messages.register_messenger``). This module registers as one more
messenger, buffers the output of the current run, and calls back with the
captured log when a run ends in failure.

The last completed run's log (success OR failure) is retained in
``_last_run_log`` / ``_last_run_code`` so the ``get_run_log`` agent tool
can read it on demand — the agent is no longer blind to runtime output.
"""

import logging
import re
from collections import deque
from collections.abc import Callable

_log = logging.getLogger(__name__)

_RETURN_CODE_RE = re.compile(r"\(return code (-?\d+)\)")
_START_MARKER = "Executing: "
_EXEC_DONE_MARKER = "\n>>> Done"
_GENERATE_ERROR_MARKER = "Generate Error:"
# GNU Radio's log subsystem prints runtime errors (buffer overflows, rate
# mismatches, dropped samples) with ":error:" as the log-level prefix —
# these do NOT crash the process (exit code stays 0), so the monitor must
# detect them separately from non-zero return codes. The ":error:" string
# does NOT appear in block names, parameter values, or normal output.
_RUNTIME_ERROR_MARKER = ":error:"
_SIGTERM_RETURN_CODE = -15

# Bound the retained run log at 512KB so a verbose/infinite-looping flowgraph
# cannot grow memory without limit. Oldest chunks are dropped (whole, never
# by slicing the joined string) until under the cap.
_MAX_LOG_BYTES = 512 * 1024


class ExecutionErrorMonitor:
    """Watches GRC's console message stream for a failed flowgraph run.

    Register ``handle_message`` with
    ``gnuradio.grc.core.Messages.register_messenger`` to receive every
    message sent to GRC's console panel (whole lines for start/end/generate
    markers, single characters during verbose execution output).
    """

    def __init__(self, on_error: Callable[[int, str], None]) -> None:
        self._on_error = on_error
        self._chunks: deque[str] = deque()
        self._chunk_bytes = 0
        self._tracking = False
        # Set to True when a ":error:" runtime error is seen in the output
        # during tracking — even if the process exits cleanly (code 0).
        # GNU Radio's scheduler handles buffer/rate errors gracefully, so
        # non-zero return codes don't catch all failures.
        self._has_runtime_error = False
        self._last_run_log: str | None = None
        self._last_run_code: int | None = None
        # Saved copy of _has_runtime_error at Done time — _fail() calls
        # _reset() which clears _has_runtime_error, but get_last_run_log
        # must still reflect whether errors occurred.
        self._last_run_had_runtime_error = False
        self._graph_modified_since_last_run = False

    def notify_graph_modified(self) -> None:
        """Called when change_graph modifies the flowgraph state."""
        self._graph_modified_since_last_run = True

    @property
    def has_last_run(self) -> bool:
        """True if at least one run has completed (success or failure)."""
        return self._last_run_log is not None

    def get_last_run_log(self) -> dict | None:
        """Return the last completed run's log as a dict, or None if no run
        has completed yet.

        Shape: ``{"return_code": int, "log_text": str, "ran_successfully": bool}``.
        ``ran_successfully`` is False when either the return code is non-zero
        OR a ``:error:`` runtime error was detected in the output.
        """
        if self._last_run_log is None or self._last_run_code is None:
            return None
        res = {
            "return_code": self._last_run_code,
            "log_text": self._last_run_log,
            "ran_successfully": self._last_run_code == 0 and not self._last_run_had_runtime_error,
        }
        if self._graph_modified_since_last_run:
            res["note"] = (
                "IMPORTANT: The flowgraph has been modified in memory since this run completed. "
                "This log reflects the PREVIOUS run BEFORE your recent changes. "
                "Do NOT assume the previous error still exists or that the file on disk is stale. "
                "Ask the user to click Execute/Play in GRC to test your recent changes."
            )
        return res

    def handle_message(self, text: str) -> None:
        if _START_MARKER in text:
            if self._tracking:
                _log.debug("exec_monitor: ignoring start (already tracking): %r", text[:80])
                return
            self._tracking = True
            self._graph_modified_since_last_run = False
            self._reset()
            _log.info("exec_monitor: started tracking run: %r", text[:120])

        self._append(text)

        if _EXEC_DONE_MARKER in text:
            if not self._tracking:
                _log.debug("exec_monitor: ignoring done (not tracking): %r", text[:80])
                return
            self._tracking = False
            match = _RETURN_CODE_RE.search(text)
            code = int(match.group(1)) if match else 0
            _log.info("exec_monitor: run finished with code=%d, chunks=%d bytes", code, len("".join(self._chunks)))
            # Retain the log for get_run_log BEFORE resetting the buffer.
            self._last_run_log = "".join(self._chunks)
            self._last_run_code = code
            # Check for runtime errors in the full buffer — verbose exec
            # arrives character-by-character via read(1), so per-message
            # marker checks can't match multi-char patterns.
            if _RUNTIME_ERROR_MARKER in self._last_run_log:
                self._has_runtime_error = True
            self._last_run_had_runtime_error = self._has_runtime_error
            if code != _SIGTERM_RETURN_CODE and (code != 0 or self._has_runtime_error):
                self._fail(code)
            else:
                self._reset()
            return

        if _GENERATE_ERROR_MARKER in text:
            if self._tracking:
                return
            _log.info("exec_monitor: generate error detected")
            self._last_run_log = "".join(self._chunks)
            self._last_run_code = 1
            self._fail(1)

    def _append(self, text: str) -> None:
        self._chunks.append(text)
        self._chunk_bytes += len(text)
        while self._chunk_bytes > _MAX_LOG_BYTES and len(self._chunks) > 1:
            self._chunk_bytes -= len(self._chunks.popleft())

    def _reset(self) -> None:
        self._chunks.clear()
        self._chunk_bytes = 0
        self._has_runtime_error = False

    def _fail(self, code: int) -> None:
        log_text = self._last_run_log or ""
        _log.info("exec_monitor: reporting failure (code=%d, %d chars), invoking callback", code, len(log_text))
        try:
            self._on_error(code, log_text)
        except Exception:
            _log.exception("exec_monitor: callback raised")
        self._reset()
