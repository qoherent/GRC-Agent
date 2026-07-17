"""Detect flowgraph execution failures from GRC's native console message
stream (``gnuradio.grc.core.Messages``) and report them via a callback.

GRC's "Execute" toolbar button runs the generated flowgraph as a subprocess
and streams its merged stdout/stderr through a simple global pub/sub
(``Messages.register_messenger``). This module registers as one more
messenger, buffers the output of the current run, and calls back with the
captured log when a run ends in failure.
"""

import re
from collections.abc import Callable

_RETURN_CODE_RE = re.compile(r"\(return code (-?\d+)\)")
_START_MARKER = "Executing: "
# Messages.send_end_exec() always prepends a leading "\n" (even for code=0);
# Messages.send_end_load(), fired whenever any tab loads/opens a .grc file,
# emits ">>> Done\n" with no leading "\n". The leading "\n" is therefore a
# reliable way to tell "a flowgraph run just finished" apart from "some tab
# just finished loading a file" on this shared, originless message bus.
_EXEC_DONE_MARKER = "\n>>> Done"
_GENERATE_ERROR_MARKER = "Generate Error:"

# GRC's own "Kill" button calls process.terminate() (SIGTERM), which reports
# this exact code via send_end_exec(-15). That's a user-requested stop, not a
# crash, so it must not trigger a "fix this" prompt.
_SIGTERM_RETURN_CODE = -15


class ExecutionErrorMonitor:
    """Watches GRC's console message stream for a failed flowgraph run.

    Register ``handle_message`` with
    ``gnuradio.grc.core.Messages.register_messenger`` to receive every
    message sent to GRC's console panel (whole lines for start/end/generate
    markers, single characters during verbose execution output).
    """

    def __init__(self, on_error: Callable[[str], None]) -> None:
        self._on_error = on_error
        self._chunks: list[str] = []
        # Whether we're currently following a run we started tracking via
        # _START_MARKER. The message bus carries no origin, so this is the
        # only way to tell "our tracked run" apart from unrelated messages
        # from another tab's Generate/Execute/file-load happening at the
        # same time. This eliminates false resets and false failure
        # misattribution, but interleaved output bytes from a second,
        # ignored concurrent run can still leak into the tracked buffer —
        # full multi-tab isolation isn't possible without origin tagging.
        self._tracking = False

    def handle_message(self, text: str) -> None:
        if _START_MARKER in text:
            if self._tracking:
                return  # another run already in flight elsewhere; ignore
            self._tracking = True
            self._reset()

        self._append(text)

        if _EXEC_DONE_MARKER in text:
            if not self._tracking:
                return  # not our run (e.g. a stray done from an ignored run)
            self._tracking = False
            match = _RETURN_CODE_RE.search(text)
            code = int(match.group(1)) if match else 0
            if code != 0 and code != _SIGTERM_RETURN_CODE:
                self._fail()
            else:
                self._reset()
            return

        if _GENERATE_ERROR_MARKER in text:
            if self._tracking:
                # Generate always precedes Executing for a given run, so a
                # Generate Error seen while already tracking one belongs to
                # a different, untracked tab.
                return
            self._fail()

    def _append(self, text: str) -> None:
        self._chunks.append(text)

    def _reset(self) -> None:
        self._chunks.clear()

    def _fail(self) -> None:
        log_text = "".join(self._chunks)
        self._reset()
        self._on_error(log_text)
