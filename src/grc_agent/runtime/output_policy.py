"""Semantic-aware output bounding utilities.

Shared functions that replace scattered hardcoded thresholds across all
model-facing tool wrappers.  Every truncation is signalled explicitly so
the LLM can re-query with targeted parameters when needed.
"""

from __future__ import annotations

from typing import Any, TypeVar

_T = TypeVar("_T")


def is_meaningful(value: Any) -> bool:
    """Return False only for the markers of *absence*.

    ``0``, ``0.0``, ``False``, and ``"0"`` are legitimate set values and
    return ``True``.  Only ``None``, ``""`` (empty string), ``[]``
    (empty list), and ``{}`` (empty dict) are considered absent.
    """
    if value is None:
        return False
    if isinstance(value, str) and value == "":
        return False
    if isinstance(value, list) and len(value) == 0:
        return False
    if isinstance(value, dict) and len(value) == 0:
        return False
    return True


def is_variable_block(block_type: str) -> bool:
    """Return True for any block that holds a modifiable value.

    Matches ``variable`` and all ``variable_*`` prefixed types
    (``variable_qtgui_range``, ``variable_slider``, etc.).
    """
    if not isinstance(block_type, str):
        return False
    return block_type == "variable" or block_type.startswith("variable_")


def truncate_list(items: list[_T], max_items: int) -> tuple[list[_T], list[_T]]:
    """Split a list into *visible* and *omitted* parts.

    Every caller is forced to handle the omitted side so truncation is
    never silent — the LLM receives structured ``omitted_counts``.
    """
    if max_items < 0:
        raise ValueError("max_items must be >= 0")
    shown = items[:max_items]
    omitted = items[max_items:]
    return shown, omitted


def truncate_text(text: str, max_chars: int) -> str:
    """Truncate at a sentence or word boundary.

    Looks for ``. `` / ``? `` / ``! `` inside a 20 % overrun window;
    falls back to a word boundary (space), then to ``max_chars`` exactly.
    """
    if len(text) <= max_chars:
        return text
    overrun = int(max_chars * 0.2)
    window = min(max_chars + overrun, len(text))

    # Sentence boundary
    for boundary in (". ", "? ", "! "):
        idx = text.rfind(boundary, max_chars, window)
        if idx != -1:
            return text[: idx + len(boundary)] + "…"

    # Word boundary
    idx = text.rfind(" ", max_chars, window)
    if idx != -1:
        return text[:idx] + " …"

    return text[:max_chars] + "…"


def compact_dict(d: dict[str, Any]) -> dict[str, Any]:
    """Keep only meaningful entries (uses ``is_meaningful``)."""
    return {k: v for k, v in d.items() if is_meaningful(v)}


__all__ = [
    "compact_dict",
    "is_meaningful",
    "is_variable_block",
    "truncate_list",
    "truncate_text",
]
