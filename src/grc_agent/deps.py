"""The dependency contract the model-facing tools run against.

Structural, and deliberately GTK-free. The real object is
``native_canvas.NativeFlowgraphProxy``, which forwards attribute access to the
live ``FlowGraph`` the canvas renders — but importing it here would pull gi/GTK
into the agent layer's import path. ``agent_factory`` already keeps that class
behind ``if TYPE_CHECKING:`` for exactly that reason, and ``agent.py`` has no
postponed-annotation import, so a direct annotation would be evaluated at
definition time and make the whole tool surface unimportable without PyGObject.

A Protocol gives the tools a real type without the import. The proxy satisfies
it by construction; test doubles satisfy it by implementing the same members,
which is what lets the tools stop probing their own dependency with
``hasattr``/``getattr`` — a pattern AGENTS.md section 1 forbids, and one that
was only ever there to tolerate a fake.

Members are optional-by-capability rather than all-required: a bare
``FlowGraph`` (no canvas) legitimately cannot run or save, and the tools that
need those report an environment fault. Runtime presence is still checked, but
against a declared contract instead of an ad-hoc string.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SupportsNotifyEdit(Protocol):
    """Something that can tell a live canvas the shared graph was mutated."""

    async def notify_edit(self, relayout: bool = False) -> Any: ...


@runtime_checkable
class SupportsRunFlowgraph(Protocol):
    """Something that can start and stop the active flowgraph natively."""

    async def run_flowgraph(
        self,
        action: str = "start",
        wait: bool = True,
        timeout_seconds: float = 60.0,
        stop_after_seconds: float | None = None,
    ) -> dict[str, Any]: ...


@runtime_checkable
class SupportsGetRunLog(Protocol):
    """Something that retains the last completed run's console output."""

    def get_run_log(self) -> dict[str, Any] | None: ...


@runtime_checkable
class SupportsSaveGraph(Protocol):
    """Something that can save the active flowgraph through GRC's own path."""

    async def save_graph(self) -> dict[str, Any]: ...


@runtime_checkable
class SupportsSaveBlock(Protocol):
    """Something that can export an epy_block into the hier-block library."""

    async def save_block(self, instance_name: str, **kwargs: Any) -> dict[str, Any]: ...


class FlowgraphDeps(Protocol):
    """What a tool receives as ``ctx.deps``.

    The graph surface is forwarded from the underlying ``FlowGraph``, so it is
    typed loosely here; the capability protocols above are what the tools
    actually narrow against.
    """

    def __getattr__(self, name: str) -> Any: ...
