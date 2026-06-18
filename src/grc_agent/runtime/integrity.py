"""Unified file-integrity compactor.

The two pre-existing copies of this helper (agent.py:_compact_save_file_integrity
and change_graph.py:_compact_file_integrity) had divergent behavior: one
returned the full hash, the other silently clipped to 12 chars with no
flag. Per AGENTS.md 'no silent transformation', the unified implementation
always returns the full hash. If a future limit is imposed, it MUST go
through :func:`format_truncation_flag` so the consumer can see what was
dropped.
"""

from __future__ import annotations

from typing import Any

__all__ = ["compact_file_integrity"]


def compact_file_integrity(file_integrity: dict[str, Any]) -> dict[str, Any]:
    """Return a stable whitelist of file-integrity fields, full hashes intact.

    The shape (status, path, persisted_sha256, current_sha256, optional
    error) is the union of the two pre-refactor call sites. Empty values
    are dropped so callers can pass the result through unchanged.
    """
    compact: dict[str, Any] = {
        "status": file_integrity.get("status"),
        "path": file_integrity.get("path"),
        "persisted_sha256": _full_hash(file_integrity.get("persisted_sha256")),
        "current_sha256": _full_hash(file_integrity.get("current_sha256")),
    }
    error = file_integrity.get("error")
    if isinstance(error, str) and error:
        compact["error"] = error
    return {key: value for key, value in compact.items() if value}


def _full_hash(value: Any) -> str | None:
    """Return ``value`` if it is a non-empty SHA hex string, else ``None``.

    The full hash is always preserved — the pre-refactor 12-char clip
    silently dropped data the consumer could not recover. The flag-based
    truncation helper is available if a future cap is needed.
    """
    return value if isinstance(value, str) and value else None
