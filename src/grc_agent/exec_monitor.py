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
from typing import Any

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

    def __init__(
        self,
        on_error: Callable[[int, str, bool], None],
        process_provider: Callable[[], tuple[object, object]] | None = None,
    ) -> None:
        self._on_error = on_error
        # Optional legacy (page_key, process) provider over the displayed GRC
        # page. An explicitly bound run process takes precedence; this provider
        # remains the spawn-failure fallback when no process was bound.
        self._process_provider = process_provider
        # (page_key, process) captured at the start marker, compared at the
        # Done marker. Reset to None outside an active run.
        self._spawn_snapshot: tuple[object, object] | None = None
        self._chunks: deque[str] = deque()
        self._chunk_bytes = 0
        # Whether the cap below actually dropped output from the run being
        # captured, and the same fact frozen at Done time for the retained log.
        self._evicted = False
        self._last_run_evicted = False
        self._tracking = False
        # Exact native Popen bound synchronously after GRC assigns
        # page.process. Console messages alone are not execution IDs.
        self._bound_run_process: Any | None = None
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
        self._runtime_error_tail = ""
        self._last_run_log: str | None = None
        self._last_run_code: int | None = None
        # Saved copy of _has_runtime_error at Done time — _fail() calls
        # _reset() which clears _has_runtime_error, but get_last_run_log
        # must still reflect whether errors occurred.
        self._last_run_had_runtime_error = False
        self._last_run_process_exited = False
        # Retained spawn-failure verdict of the last completed run — set at
        # every Done evaluation (the live snapshot is cleared there), so
        # _reset() must not clear it (same pattern as _last_run_had_runtime_error).
        self._last_run_spawn_failed = False
        # Kill-escalation provenance of the last completed run. A run the app
        # itself SIGKILLed (stop escalation) retains its buffered output with
        # these set; a naturally-terminated run clears them. Same retention
        # discipline as the fields above: set at every terminal evaluation,
        # never cleared by _reset().
        self._last_run_terminated_by_kill = False
        self._last_run_termination_signal: int | None = None
        # True when the retained terminal state was synthesized (kill
        # escalation or vanished process) rather than observed via a natural
        # marker. Gates the termination disclosure; a natural terminal always
        # clears it.
        self._last_run_synthesized = False
        # The run_epoch of the last completed run, exposed as run_id in
        # get_last_run_log so consumers can tell two quiet runs apart (the
        # (log_text, return_code) fingerprint collides otherwise).
        self._last_run_id = 0
        self._graph_modified_since_last_run = False

    def set_process_provider(
        self, process_provider: Callable[[], tuple[object, object]] | None
    ) -> None:
        """Wire the (page_key, process) provider after construction.

        desktop_app constructs the monitor before the canvas manager exists,
        so the provider is attached as soon as the canvas is available.
        Passing None disables spawn-failure detection (legacy behavior)."""
        self._process_provider = process_provider

    def bind_run_process(self, process: Any) -> bool:
        """Bind the native process to the currently tracked execution epoch.

        Called synchronously after GRC's Execute action assigns
        ``page.process``. The first process wins for an epoch; late or
        duplicate bindings cannot redirect terminal evidence.
        """
        if not self._tracking or process is None or self._bound_run_process is not None:
            return False
        self._bound_run_process = process
        return True

    def notify_graph_modified(self) -> None:
        """Called when change_graph modifies the flowgraph state."""
        self._graph_modified_since_last_run = True

    def get_last_run_log(self) -> dict | None:
        """Return the last completed run's log as a dict, or None if no run
        has completed yet.

        Shape includes ``process_exited``: true only when the exact process
        bound to that run was observed with an integer exit status; false when
        no process was bound or the terminal state was synthesized/generated.
        It also includes ``{"return_code": int | None, "run_id": int,
        "log_text": str, "ran_successfully": bool}``, plus
        ``log_truncated: True`` when the
        ``_MAX_LOG_BYTES`` cap dropped the run's oldest output,
        ``spawn_failed``/``spawn_note`` when the run died at subprocess spawn,
        ``terminated_by_kill``/``termination_note`` when no natural terminal
        marker arrived and the state was synthesized — either the app SIGKILLed
        the run after its SIGTERM shutdown stalled, or the process vanished
        (crash/external reap). The log is the captured output; ``return_code``
        is then ``None`` because no exit code was observed), and ``note`` when
        the graph changed since the run.
        ``ran_successfully`` is False when either the return code is non-zero
        (or absent) OR a ``:error:`` runtime error was detected in the output
        OR the run failed to spawn OR the terminal state was synthesized.

        ``log_truncated`` exists because the reduction has to be visible to the
        model: the cap silently drops the *front* of the log, and a diagnostic
        read as complete when it is not is exactly the kind of silent
        transformation the tool contract forbids.
        """
        if self._last_run_log is None:
            return None
        res = {
            "return_code": self._last_run_code,
            "run_id": self._last_run_id,
            "log_text": self._last_run_log,
            "runtime_error": self._last_run_had_runtime_error,
            "process_exited": self._last_run_process_exited,
            "terminal_observed": not self._last_run_synthesized,
            "ran_successfully": (
                self._last_run_code == 0
                and not self._last_run_had_runtime_error
                and not self._last_run_spawn_failed
                and not self._last_run_terminated_by_kill
            ),
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
        if self._last_run_spawn_failed:
            res["spawn_failed"] = True
            res["spawn_note"] = (
                "The run's subprocess failed to spawn — nothing was executed. The exception "
                "text is retained at the end of the log. Common causes: the python interpreter "
                "or terminal wrapper is missing, or the generated script is not runnable. "
                "Fix the environment and run again."
            )
        if self._last_run_synthesized:
            res["terminated_by_kill"] = self._last_run_terminated_by_kill
            if self._last_run_termination_signal is not None:
                res["termination_note"] = (
                    "The run was forcibly terminated: SIGTERM did not shut it down within "
                    f"the grace window, so signal {self._last_run_termination_signal} was sent. "
                    "The graph DID execute — this log is everything captured before "
                    "termination, and output after the kill point is missing. A short or "
                    "empty log here reflects the kill, not necessarily a broken graph."
                )
            else:
                res["termination_note"] = (
                    "The run's process ended without delivering its terminal marker (it "
                    "may have crashed or been reaped externally). The graph DID execute — "
                    "this log is everything captured, and output after the last captured "
                    "line is missing."
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

    @property
    def last_run_spawn_failed(self) -> bool:
        """True when the last completed run died at subprocess spawn: a
        code-less Done marker whose wired process provider reported an absent
        or unchanged identity (Executor assigns page.process only on a
        successful spawn). Always False without a wired provider."""
        return self._last_run_spawn_failed

    def handle_message(self, text: str) -> None:
        if _START_MARKER in text:
            if self._tracking:
                _log.debug("exec_monitor: ignoring start (already tracking): %r", text[:80])
                return
            self._tracking = True
            self._bound_run_process = None
            self._graph_modified_since_last_run = False
            self._reset()
            self._run_end.clear()
            self._run_epoch += 1
            # Executor assigns page.process only after _popen's start marker
            # fires, so this snapshot holds the pre-run identity (usually
            # None, or a stale object from an un-cleaned prior run).
            self._spawn_snapshot = (
                self._process_provider() if self._process_provider is not None else None
            )
            _log.info("exec_monitor: started tracking run: %r", text[:120])

        self._append(text)

        if _EXEC_DONE_MARKER in text:
            if not self._tracking:
                _log.debug("exec_monitor: ignoring done (not tracking): %r", text[:80])
                return
            match = _RETURN_CODE_RE.search(text)
            code = int(match.group(1)) if match else 0
            bound_process = self._bound_run_process
            process_exited = False
            if bound_process is not None:
                actual_code = bound_process.poll()
                if actual_code is None:
                    _log.debug(
                        "exec_monitor: ignoring unrelated Done while bound process is alive: %r",
                        text[:80],
                    )
                    return
                code = actual_code
                process_exited = True
            self._tracking = False
            # Without a bound process, a code-less Done marker is ambiguous
            # between a real clean exit and GRC's spawn-crash path (both emit
            # send_end_exec() with default code 0). Keep the page provider's
            # identity check as that legacy fallback; a tab switch or missing
            # provider remains inconclusive rather than guessing.
            spawn_failed = False
            if (
                bound_process is None
                and match is None
                and self._process_provider is not None
                and self._spawn_snapshot is not None
            ):
                page_key, proc = self._process_provider()
                snap_key, snap_proc = self._spawn_snapshot
                if (
                    page_key is not None
                    and page_key == snap_key
                    and (proc is None or proc is snap_proc)
                ):
                    spawn_failed = True
            self._spawn_snapshot = None
            _log.info(
                "exec_monitor: run finished with code=%d, chunks=%d bytes, spawn_failed=%s",
                code,
                self._chunk_bytes,
                spawn_failed,
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
            self._last_run_process_exited = process_exited
            self._last_run_spawn_failed = spawn_failed
            self._last_run_terminated_by_kill = False
            self._last_run_termination_signal = None
            self._last_run_synthesized = False
            self._last_run_id = self._run_epoch
            if spawn_failed or code != _SIGTERM_RETURN_CODE and (code != 0 or self._has_runtime_error):
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
            self._last_run_process_exited = False
            self._last_run_spawn_failed = False  # a generate error is not a spawn crash
            self._last_run_terminated_by_kill = False
            self._last_run_termination_signal = None
            self._last_run_synthesized = False
            self._bound_run_process = None
            self._last_run_id = self._run_epoch
            self._spawn_snapshot = None
            self._fail(1)
            self._agent_initiated = False
            self._run_end.set()

    def synthesize_terminal_state(self, *, signal: int | None) -> bool:
        """Retain the buffered output as a terminal state for a marker-less run end.

        Called by the stop-escalation path after SIGKILL when no natural Done
        marker arrived, or when the run's process is already gone with the
        monitor still tracking (crashed or externally reaped): the run is over
        for all observable purposes, so the monitor records what it captured
        rather than wedging `_tracking` forever. `signal` is the terminating
        signal the app itself sent, or None when the process vanished without
        app action. `return_code` stays None (no exit code was observed — never
        fabricated); provenance travels in the disclosure fields read by
        `get_last_run_log`. Never invokes the failure callback: a deliberate
        stop is not a failure turn. Returns True when a state was
        synthesized, False when no run was tracking (a natural terminal
        already won the race — a late natural marker afterwards is ignored by
        the existing not-tracking guard, so double-terminal arrival is safe)."""
        if not self._tracking:
            return False
        self._last_run_log = "".join(self._chunks)
        self._last_run_code = None
        self._last_run_evicted = self._evicted
        self._last_run_had_runtime_error = self._has_runtime_error
        self._last_run_process_exited = False
        self._last_run_spawn_failed = False
        self._last_run_terminated_by_kill = signal is not None
        self._last_run_termination_signal = signal
        self._last_run_synthesized = True
        self._last_run_id = self._run_epoch
        self._reset()
        self._tracking = False
        self._bound_run_process = None
        self._agent_initiated = False
        self._run_end.set()
        return True

    def _append(self, text: str) -> None:
        # Detect the native marker across arbitrary console chunk boundaries
        # before eviction. This suffix is parser state, not model-facing output.
        scan = self._runtime_error_tail + text
        if _RUNTIME_ERROR_MARKER in scan:
            self._has_runtime_error = True
        self._runtime_error_tail = scan[-(len(_RUNTIME_ERROR_MARKER) - 1):]
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
        self._runtime_error_tail = ""

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
            self._on_error(code, log_text, self._last_run_spawn_failed)
        except Exception:
            _log.exception("exec_monitor: callback raised")
        self._reset()
