"""Shared context/result types for change_graph operation helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

ToolResult = dict[str, Any]


@dataclass(slots=True)
class ChangeGraphOperationContext:
    """Runtime context passed to operation helper functions."""

    agent: Any
    debug: bool
    started: float
    before_revision: int
    before_dirty: bool
    dry_run: bool
    resolved_operation_kind: str | None
    handlers: list[str]


@dataclass(slots=True)
class ChangeGraphOperationResult:
    """Result contract for a change_graph operation helper."""

    handled: bool
    operation_summary: str
    tool_result: ToolResult | None = None
    terminal_result: ToolResult | None = None
