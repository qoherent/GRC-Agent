"""Thin wrapper around the graphify graph-construction API."""

from importlib.metadata import PackageNotFoundError, version
from typing import Any

import networkx as nx

try:
    from graphify import build_from_json as _build_from_json
except ImportError:  # pragma: no cover - exercised through readiness errors instead.
    _build_from_json = None


class GraphifyAdapterError(RuntimeError):
    """Raised when graphify is unavailable or graph construction fails."""


def graphify_status() -> dict[str, str | bool | None]:
    """Report whether graphify is importable and which package version is installed."""
    try:
        graphify_version = version("graphifyy")
    except PackageNotFoundError:
        graphify_version = None

    if _build_from_json is None:
        return {
            "ok": False,
            "version": graphify_version,
            "message": "graphify.build_from_json is unavailable in the current environment.",
        }

    return {
        "ok": True,
        "version": graphify_version,
        "message": "graphify is available.",
    }


def build_graph(extraction: dict[str, Any], *, directed: bool = True) -> nx.DiGraph:
    """Build one retrieval graph from a graphify-compatible extraction payload."""
    if _build_from_json is None:
        raise GraphifyAdapterError(
            "graphify.build_from_json is unavailable. Install graphifyy in the project environment."
        )

    try:
        graph = _build_from_json(extraction, directed=directed)
    except Exception as exc:  # pragma: no cover - hard to trigger without breaking graphify.
        raise GraphifyAdapterError(f"graphify graph build failed: {exc}") from exc

    if directed and not isinstance(graph, nx.DiGraph):
        raise GraphifyAdapterError("graphify returned a non-directed graph for directed retrieval.")

    return graph
