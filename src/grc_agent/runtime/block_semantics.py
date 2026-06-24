"""GRC-core-evaluated parameter visibility + port domain enum.

The single source of truth for the per-param ``hide`` value GRC computes
at runtime (``'none' | 'part' | 'all'``) given a block type and its
current param values. Consumed by :mod:`grc_agent.runtime.param_filter`
(the Bible) to drive Stage A/B decisions uniformly across every model-
visible tool.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class PortDomain(StrEnum):
    """Domain classification for a block port."""

    STREAM = "stream"
    MESSAGE = "message"


_EVALUATED_HIDE_CACHE: dict[tuple[str, tuple[tuple[str, str], ...]], dict[str, str]] = {}


def evaluated_param_hides(block_type: str, param_values: dict[str, Any]) -> dict[str, str]:
    """GRC-core-evaluated 'hide' value ('none'|'part'|'all') per param key."""
    cache_key = (
        block_type,
        tuple(
            sorted(
                (str(key), "" if value is None else str(value))
                for key, value in param_values.items()
            )
        ),
    )
    cached = _EVALUATED_HIDE_CACHE.get(cache_key)
    if cached is not None:
        return cached
    hides = _compute_evaluated_param_hides(block_type, param_values)
    _EVALUATED_HIDE_CACHE[cache_key] = hides
    return hides


def _compute_evaluated_param_hides(block_type: str, param_values: dict[str, Any]) -> dict[str, str]:
    try:
        from grc_agent.grc_native_adapter import get_platform_or_none

        platform = get_platform_or_none()
    except Exception:
        return {}
    if platform is None:
        return {}
    try:
        flow_graph = platform.make_flow_graph()
        block = flow_graph.new_block(block_type)
    except Exception:
        return {}
    # ``new_block`` returns None for control blocks (variable, parameter,
    # options, etc.) — the platform does not model them as instance blocks
    # in a flow graph. Return an empty hide map; the caller falls back to
    # the full param list.
    if block is None:
        return {}
    try:
        for key, value in param_values.items():
            param = block.params.get(key) if hasattr(block.params, "get") else None
            if param is not None:
                try:
                    param.value = "" if value is None else str(value)
                except Exception:
                    pass
        try:
            flow_graph.rewrite()
        except Exception:
            pass
        return {str(name): str(param.hide) for name, param in block.params.items()}
    except Exception:
        return {}
