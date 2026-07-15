import asyncio
import contextlib
import os
import signal
from collections.abc import Callable


class FlowgraphRunner:
    """Manages a single running flowgraph subprocess.

    Single-flight: only one run active at a time. Uses start_new_session=True
    (equivalent to preexec_fn=os.setsid but safer with asyncio).
    Stop: SIGTERM the process group, wait 2s, SIGKILL fallback.
    """

    def __init__(self) -> None:
        self._proc: asyncio.subprocess.Process | None = None
        self._returncode: int | None = None
        self._on_output: Callable[[str], None] | None = None
        self._on_done: Callable[[int], None] | None = None

    @property
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    @property
    def returncode(self) -> int | None:
        return self._returncode

    async def start(
        self,
        run_command: list[str],
        cwd: str,
        on_output: Callable[[str], None] | None = None,
        on_done: Callable[[int], None] | None = None,
    ) -> None:
        if self.is_running:
            raise RuntimeError("A flowgraph is already running.")
        self._on_output = on_output
        self._on_done = on_done
        self._returncode = None

        self._proc = await asyncio.create_subprocess_exec(
            *run_command,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        asyncio.ensure_future(self._read_output())

    async def _read_output(self) -> None:
        if not self._proc or not self._proc.stdout:
            return
        try:
            async for line_bytes in self._proc.stdout:
                line = line_bytes.decode("utf-8", errors="replace").rstrip("\n\r")
                if self._on_output:
                    self._on_output(line)
        except Exception:
            pass
        finally:
            if self._proc:
                rc = await self._proc.wait()
                self._returncode = rc
                self._proc = None
                if self._on_done:
                    self._on_done(rc)

    async def stop(self) -> None:
        if not self.is_running or self._proc is None:
            return
        try:
            os.killpg(os.getpgid(self._proc.pid), signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(self._proc.wait(), timeout=2.0)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(os.getpgid(self._proc.pid), signal.SIGKILL)
            if self._proc:
                await self._proc.wait()
