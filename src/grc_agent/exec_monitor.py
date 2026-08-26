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

import asyncio
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
        # Whether the cap below actually dropped output from the run being
        # captured, and the same fact frozen at Done time for the retained log.
        self._evicted = False
        self._last_run_evicted = False
        self._tracking = False
        # Completion signaling for run_flowgraph: cleared on the start marker,
        # set on every terminal marker (Done / Generate Error). Safe to set
        # from handle_message because every Messages.send_* caller in GRC's
        # exec path runs on the GLib main-loop thread (Executor.py marshals
        # via GLib.idle_add; send_start_exec fires synchronously inside the
        # ExecFlowGraphThread constructor on the caller's thread), which under
        # the unified asyncio+GLib loop IS the loop thread. Verified
        # empirically under xvfb on the gbulb path.
        self._run_end = asyncio.Event()
        # True only between mark_run_agent_initiated() and the run's terminal
        # marker, while that specific run was started by the agent's
        # run_flowgraph tool (not the user's Execute button). Consumed (set
        # back to False) at the terminal marker after use: the tool already
        # reports failures to the model in-turn, so the follow-up
        # notify_run_failure turn would be redundant.
        self._agent_initiated = False
        # Monotonic count of start markers seen. run_flowgraph captures it
        # BEFORE triggering Execute and passes it to wait_for_run_end: if no
        # start marker fired for that attempt (silent no-op — a disabled Gio
        # action, an unsaved page slipping past the gates), the count is
        # unchanged and the wait reports not_started instead of serving the
        # previous run's stale "completed".
        self._run_epoch = 0
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

    def get_last_run_log(self) -> dict | None:
        """Return the last completed run's log as a dict, or None if no run
        has completed yet.

        Shape: ``{"return_code": int, "log_text": str, "ran_successfully": bool}``,
        plus ``log_truncated: True`` when the ``_MAX_LOG_BYTES`` cap dropped the
        run's oldest output, and ``note`` when the graph changed since the run.
        ``ran_successfully`` is False when either the return code is non-zero
        OR a ``:error:`` runtime error was detected in the output.

        ``log_truncated`` exists because the reduction has to be visible to the
        model: the cap silently drops the *front* of the log, and a diagnostic
        read as complete when it is not is exactly the kind of silent
        transformation the tool contract forbids.
        """
        if self._last_run_log is None or self._last_run_code is None:
            return None
        res = {
            "return_code": self._last_run_code,
            "log_text": self._last_run_log,
            "ran_successfully": self._last_run_code == 0 and not self._last_run_had_runtime_error,
            # Always present, never a silent transformation: while a run is
            # in flight this log belongs to the PREVIOUS run — the model must
            # be able to tell the two states apart.
            "run_in_progress": self._tracking,
        }
        if self._tracking:
            res["in_progress_note"] = (
                "A flowgraph execution is currently running; this log is from the previous "
                "completed run. Wait for the current run to finish (run_flowgraph reports "
                "completion) before reading it as the current run's output."
            )
        if self._last_run_evicted:
            res["log_truncated"] = True
            res["truncation_note"] = (
                f"This run produced more than {_MAX_LOG_BYTES} bytes of output; the oldest "
                "output was dropped and the log starts mid-run."
            )
        if self._graph_modified_since_last_run:
            res["note"] = (
                "The flowgraph has been modified in memory since this run completed, so this "
                "log describes the state before those changes. Ask the user to run the "
                "flowgraph again to test the current state."
            )
        return res

    def mark_run_agent_initiated(self) -> None:
        """Flag the NEXT terminal marker as belonging to an agent-initiated run.

        Called by NativeFlowgraphProxy.run_flowgraph immediately BEFORE
        triggering GRC's Execute action (the start marker fires synchronously
        inside the action, so a post-action call would race it). Consumed at
        the terminal marker: while set, _fail() suppresses the user-facing
        notify_run_failure follow-up turn — the run_flowgraph tool result
        already tells the model about the failure in the same turn.
        """
        self._agent_initiated = True

    def mark_run_agent_initiated_cancelled(self) -> None:
        """Drop the suppression flag: no start marker will ever follow.

        Called by run_flowgraph when the Execute action raised, or when the
        action returned without the synchronous 'Executing:' start marker
        (a silent no-op). Without this, the flag would linger and wrongly
        suppress the failure notification of a LATER user-initiated run.
        """
        self._agent_initiated = False

    @property
    def is_tracking(self) -> bool:
        """True while a run's output is being captured (start marker seen,
        terminal marker not yet)."""
        return self._tracking

    @property
    def run_epoch(self) -> int:
        """Monotonic count of runs started; unchanged while none is running."""
        return self._run_epoch

    async def wait_for_run_end(
        self, timeout: float, *, epoch: int | None = None
    ) -> str:
        """Await the current run's terminal marker.

        Returns "completed" when the run ended within `timeout` (or had
        already ended before the call — GRC's spawn-failure path emits its
        Done marker synchronously), "still_running" on timeout, and
        "not_started" when the Execute action was a silent no-op: pass the
        `epoch` captured by the caller BEFORE triggering the action; if no
        start marker has fired since, the epoch is unchanged and no run
        belongs to this attempt.
        """
        if epoch is not None and epoch == self._run_epoch:
            # No start marker fired since the caller captured the epoch — the
            # Execute was a silent no-op. Do NOT serve the previous run's log.
            return "not_started"
        if self._tracking:
            try:
                await asyncio.wait_for(self._run_end.wait(), timeout)
                return "completed"
            except TimeoutError:
                return "still_running"
        if self._last_run_log is not None:
            # Not tracking, but a completed run exists: the run this caller
            # just started already hit its terminal marker synchronously
            # (ultra-fast exit or GRC's spawn-failure path).
            return "completed"
        return "not_started"

    @property
    def last_run_code(self) -> int | None:
        """Return code of the last completed run, or None before any run."""
        return self._last_run_code

    def handle_message(self, text: str) -> None:
        if _START_MARKER in text:
            if self._tracking:
                _log.debug("exec_monitor: ignoring start (already tracking): %r", text[:80])
                return
            self._tracking = True
            self._graph_modified_since_last_run = False
            self._reset()
            self._run_end.clear()
            self._run_epoch += 1
            _log.info("exec_monitor: started tracking run: %r", text[:120])

        self._append(text)

        if _EXEC_DONE_MARKER in text:
            if not self._tracking:
                _log.debug("exec_monitor: ignoring done (not tracking): %r", text[:80])
                return
            self._tracking = False
            match = _RETURN_CODE_RE.search(text)
            code = int(match.group(1)) if match else 0
            _log.info(
                "exec_monitor: run finished with code=%d, chunks=%d bytes",
                code,
                self._chunk_bytes,
            )
            # Retain the log for get_run_log BEFORE resetting the buffer.
            self._last_run_log = "".join(self._chunks)
            self._last_run_code = code
            self._last_run_evicted = self._evicted
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
            self._agent_initiated = False
            self._run_end.set()
            return

        if _GENERATE_ERROR_MARKER in text:
            if self._tracking:
                return
            _log.info("exec_monitor: generate error detected")
            self._run_epoch += 1
            self._last_run_log = "".join(self._chunks)
            self._last_run_code = 1
            self._last_run_evicted = self._evicted
            self._fail(1)
            self._agent_initiated = False
            self._run_end.set()

    def _append(self, text: str) -> None:
        self._chunks.append(text)
        # Count encoded bytes, not characters, so the 512KB cap is a byte cap
        # even for multibyte UTF-8 output.
        self._chunk_bytes += len(text.encode())
        while self._chunk_bytes > _MAX_LOG_BYTES and len(self._chunks) > 1:
            self._chunk_bytes -= len(self._chunks.popleft().encode())
            self._evicted = True

    def _reset(self) -> None:
        self._chunks.clear()
        self._chunk_bytes = 0
        self._evicted = False
        self._has_runtime_error = False

    def _fail(self, code: int) -> None:
        log_text = self._last_run_log or ""
        if self._agent_initiated:
            # The run_flowgraph tool reports this failure to the model
            # in-turn; the follow-up notify_run_failure turn would only be
            # redundant (and cost another model request).
            _log.info(
                "exec_monitor: suppressing failure callback (agent-initiated run, code=%d)",
                code,
            )
            self._reset()
            return
        _log.info(
            "exec_monitor: reporting failure (code=%d, %d chars), invoking callback",
            code,
            len(log_text),
        )
        try:
            self._on_error(code, log_text)
        except Exception:
            _log.exception("exec_monitor: callback raised")
        self._reset()
