"""Shared text helpers for runtime modules.

Owns:
- format_truncation_flag: one uniform truncation sentinel for every
  model-facing string. AGENTS.md mandates explicit (what, was, kept) flags
  on every model-visible truncation. This is the single place that emits them.
- tokenize_identifier: the canonical identifier tokenizer. casefold, split on
  non-alphanumeric, drop empties.
- compact_whitespace: the canonical whitespace compactor.

No model-visible string ever truncates or tokenizes without going through
this module.
"""

from __future__ import annotations

import re

__all__ = [
    "format_truncation_flag",
    "tokenize_identifier",
    "compact_whitespace",
]


def format_truncation_flag(
    source: str,
    was: int,
    kept: int,
    *,
    unit: str = "chars",
) -> str:
    """Return the canonical truncation sentinel.

    Format: ``"... [TRUNCATED <source>: was <was> <unit>, kept <kept> <unit>]"``

    AGENTS.md requires every model-visible truncation to emit a flag with
    what was clipped and how much was kept. ``source`` is the field/list
    name (e.g. ``block_summary`` or ``connections``).
    """
    return f"... [TRUNCATED {source}: was {was} {unit}, kept {kept} {unit}]"


_IDENTIFIER_SPLIT = re.compile(r"[^a-z0-9]+")


def tokenize_identifier(value: str) -> list[str]:
    """One canonical identifier tokenizer.

    Casefold, split on non-alphanumeric, drop empty tokens. Replaces the
    six near-duplicate helpers in agent.py, search_blocks.py,
    inspect_graph.py, and session.py.
    """
    return [t for t in _IDENTIFIER_SPLIT.split(value.casefold()) if t]


def compact_whitespace(value: str) -> str:
    """Collapse all runs of whitespace to single spaces, strip ends."""
    return " ".join(value.split())
