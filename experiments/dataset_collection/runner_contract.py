"""Runner contract helpers (plan U3) — idle-drain, teardown, guards.

These helpers own the race conditions the flow analysis surfaced (C5/C6/I3):
the sidebar can be idle while background persistence is still in flight, a
pending auto-fix task can steal the next simulator send, and a running
flowgraph must be stopped before the next task boots. Every rule here is a
direct translation of the plan's runner contract; nothing else may invent
new sequencing.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart


class RunnerContractError(RuntimeError):
    """A contract assertion failed — the task aborts as infrastructure_abort."""


def last_assistant_text(messages: list[ModelMessage]) -> str:
    """The last ModelResponse's joined text parts (what the user 'saw')."""
    for msg in reversed(messages):
        if isinstance(msg, ModelResponse):
            text = "".join(
                part.content for part in msg.parts if isinstance(part, TextPart)
            )
            return text.strip()
    return ""


async def drain(sidebar, monitor, *, timeout: float = 120.0) -> None:
    """Full idle-drain (plan C5/I3): busy clears, tracked satellite tasks are
    done, background persistence joined, and no flowgraph run is tracking."""
    deadline = time.monotonic() + timeout
    while True:
        remaining = max(0.1, deadline - time.monotonic())
        if getattr(sidebar, "_busy", False):
            await asyncio.wait_for(sidebar._idle_event.wait(), timeout=remaining)
            continue
        for attr in ("_fix_task", "_compact_task", "_implement_plan_task"):
            task = getattr(sidebar, attr, None)
            if task is not None and not task.done():
                await asyncio.wait_for(asyncio.shield(task), timeout=remaining)
        pending = [t for t in sidebar._background_tasks if not t.done()]
        if pending:
            await asyncio.wait_for(
                asyncio.gather(*pending, return_exceptions=True), timeout=remaining
            )
        if monitor is not None and monitor.is_tracking:
            await asyncio.sleep(0.2)
            if time.monotonic() > deadline:
                raise RunnerContractError("drain: flowgraph run still tracking after timeout")
            continue
        # Re-check busy after joining satellites (an awaited fix task may have
        # started a new chat turn).
        if getattr(sidebar, "_busy", False):
            continue
        return


async def send_or_fail(sidebar, message: str) -> None:
    """send_message with the False handling rule (plan C5): a False return
    after a completed drain is fatal; the caller drains before calling."""
    sent = sidebar.send_message(message)
    if not sent:
        raise RunnerContractError("send_message returned False on an idle sidebar")


async def await_turn(sidebar) -> None:
    """Await the full turn, including approval-resume loops."""
    task = getattr(sidebar, "_chat_task", None)
    if task is None:
        raise RunnerContractError("no _chat_task after send_message")
    await asyncio.wait_for(asyncio.shield(task), timeout=1800.0)


def tree_hash(root: Path) -> str:
    """Content hash of a directory tree (containment check, plan S5)."""
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_file():
            digest.update(str(path.relative_to(root)).encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def teardown_task(
    sidebar,
    proxy,
    *,
    stop_run: Callable[[], Awaitable[None]],
    checks: list[Callable[[], None]],
) -> None:
    """Task teardown in the plan's fixed order (U3 step 4).

    ``stop_run`` stops any tracking flowgraph and awaits ``not is_tracking``;
    ``checks`` run AFTER the second drain so assertions see settled state
    (the resurrection-undo path can INSERT-then-DELETE right after a clear).
    """
    await stop_run()
    await drain(sidebar, proxy._exec_monitor if proxy is not None else None)
    sidebar.clear_messages()
    await drain(sidebar, proxy._exec_monitor if proxy is not None else None)
    for check in checks:
        check()
