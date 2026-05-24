"""Path safety helpers for graph loading, autosave, and manual save."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable


def resolved_path(path_value: str | Path) -> Path:
    """Resolve a path without requiring filesystem existence."""
    return Path(path_value).expanduser().resolve(strict=False)


def unsafe_graph_root_for_path(
    path_value: str | Path,
    *,
    installed_graph_roots: Iterable[Path],
    canonical_fixture_root: Path,
) -> str | None:
    """Return protected root when `path_value` targets canonical/example graphs."""
    candidate = resolved_path(path_value)
    roots = (*installed_graph_roots, canonical_fixture_root)
    for root in roots:
        resolved_root = root.expanduser().resolve(strict=False)
        try:
            candidate.relative_to(resolved_root)
        except ValueError:
            continue
        return str(resolved_root)
    return None
