"""Token usage and native cost collection for the context/cost readout.

Pure functions extracted from ``chat_sidebar.py`` — no GTK, no ``self``.
``_collect_token_usage`` derives the latest turn's totals from message
history alone; the two ``_run_usage_*_override`` helpers prefer a live run's
aggregated usage over the last-response-only figures while a turn is still
in flight (see ``_update_context_label``).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic_ai.messages import ModelRequest, ModelResponse, UserPromptPart


def _collect_token_usage(msgs) -> tuple[int, int, int, int, Decimal | None, bool]:
    """Extract token totals and native Pydantic AI cost for the latest turn.

    A turn can contain several model requests around tool calls, so its cost is
    the sum after the latest user prompt. It is complete only when every such
    response has ``usage.cost``; otherwise ``None`` prevents a partial sum.
    """
    last_input = last_output = last_reasoning = total = 0
    turn_cost = Decimal(0)
    has_usage = False
    cost_complete = True
    for msg in msgs:
        if isinstance(msg, ModelRequest):
            if any(isinstance(part, UserPromptPart) for part in msg.parts):
                turn_cost = Decimal(0)
                has_usage = False
                cost_complete = True
            continue
        if not isinstance(msg, ModelResponse) or not msg.usage:
            continue
        u = msg.usage
        inp = getattr(u, "input_tokens", 0) or 0
        out = getattr(u, "output_tokens", 0) or 0
        native_cost = getattr(u, "cost", None)
        if inp or out or native_cost is not None:
            has_usage = True
            if native_cost is None:
                cost_complete = False
            else:
                turn_cost += native_cost
        if inp:
            last_input = inp
            last_output = out
            reasoning = 0
            if hasattr(u, "details") and isinstance(u.details, dict):
                reasoning = u.details.get("reasoning_tokens", 0) or 0
            elif hasattr(u, "reasoning_tokens"):
                reasoning = getattr(u, "reasoning_tokens", 0) or 0
            last_reasoning = reasoning
        total += getattr(u, "total_tokens", 0) or 0
    return (
        last_input,
        last_output,
        last_reasoning,
        total,
        turn_cost if has_usage and cost_complete else None,
        has_usage,
    )


def _format_native_cost(cost: Decimal) -> str:
    """Render Pydantic AI's exact USD Decimal without inventing precision."""
    return f"${format(cost.normalize(), 'f')}"


def _run_usage_output_override(run: Any, last_output: int, last_reasoning: int) -> tuple[int, int]:
    """Replace last-response-only output/reasoning with the run's aggregated
    totals when a live run is available (see _update_context_label)."""
    if run is None:
        return last_output, last_reasoning
    u = getattr(run, "usage", None)
    if u is None:
        return last_output, last_reasoning
    details = getattr(u, "details", None) or {}
    return (
        getattr(u, "output_tokens", 0) or 0,
        details.get("reasoning_tokens", 0) or 0,
    )


def _run_usage_cost_override(
    run: Any, last_turn_cost: Decimal | None, has_usage: bool
) -> tuple[Decimal | None, bool]:
    """Use the active run's aggregate native cost while it is available."""
    if run is None:
        return last_turn_cost, has_usage
    usage = getattr(run, "usage", None)
    if usage is None or not (
        getattr(usage, "requests", 0)
        or getattr(usage, "input_tokens", 0)
        or getattr(usage, "output_tokens", 0)
        or getattr(usage, "cost", None) is not None
    ):
        return last_turn_cost, has_usage
    return getattr(usage, "cost", None), True
