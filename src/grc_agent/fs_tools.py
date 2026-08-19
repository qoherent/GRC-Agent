"""Filesystem tools scoped to the active flowgraph's folder.

The harness ``FileSystem`` toolset (pydantic_ai_harness) provides the
sandboxing: path resolution, symlink containment, allow/deny/protected glob
filtering, and result caps. This module subclasses it for the one thing a
native GRC app needs that a fixed ``root_dir`` cannot express:

- **Dynamic root.** The sandbox root is the directory of the active `.grc`
  file, re-resolved on every tool call from GRC's notebook — the same
  late-binding rule ``inspect_graph``/``change_graph`` already follow via
  ``window.current_page``. A flowgraph that has never been saved
  (``untitled.grc``) has no folder, so every tool reports that gating
  error back to the model instead of silently falling back to the
  process working directory (GRC changes its CWD dynamically; a CWD root
  would be arbitrary and user-hostile).

- **``.grc`` never reaches the model as raw XML.** ``read_file`` on any
  `.grc` path is routed through the structural inspect engine (the same
  one behind the ``inspect_graph`` tool): the active file inspects the
  live shared ``FlowGraph`` object, any other `.grc` in the folder is
  loaded headlessly. Flowgraph *writes* stay the sole province of
  ``change_graph`` (see the write-suffix allowlist added with
  ``write_file``/``edit_file``).
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.tools import AgentDepsT

# Private-but-stable helpers of the harness toolset: the error taxonomy
# (``_recoverable`` converts model-correctable failures into ModelRetry) and
# the 12-hex content hash that read_file headers surface and write/edit
# accept back as ``expected_hash`` — reusing it keeps the hash contract
# identical across the parent tools and this subclass.
from pydantic_ai_harness.filesystem._capability import _DEFAULT_PROTECTED
from pydantic_ai_harness.filesystem._toolset import FileSystemToolset, _recoverable

from grc_agent.adapter.graph import inspect_graph, load_flow_graph

# --------------------------------------------------------------------------
# Active-graph providers (installed by desktop_app once the canvas exists).
# --------------------------------------------------------------------------

def _no_grc() -> Path | str | None:
    return None


def _no_flow_graph() -> Any | None:
    return None


_active_grc_path_fn: Callable[[], Path | str | None] = _no_grc
_active_flow_graph_fn: Callable[[], Any | None] = _no_flow_graph


def set_active_graph_providers(
    grc_path_fn: Callable[[], Path | str | None],
    flow_graph_fn: Callable[[], Any | None],
) -> None:
    """Install the callables that resolve the active flowgraph.

    Both are invoked lazily on every filesystem tool call, so tab switches
    and saves are followed automatically. ``grc_path_fn`` returns the active
    page's file path (``None``/empty for an unsaved tab); ``flow_graph_fn``
    returns the live shared ``FlowGraph`` object (or ``None`` — reads of the
    active `.grc` then fall back to the on-disk file).
    """
    global _active_grc_path_fn, _active_flow_graph_fn
    _active_grc_path_fn = grc_path_fn
    _active_flow_graph_fn = flow_graph_fn


def active_grc_path() -> Path | None:
    """Resolved path of the active flowgraph file, or ``None`` if unsaved."""
    raw = _active_grc_path_fn()
    if not raw:
        return None
    return Path(raw).resolve()


# Placeholder root used only while no flowgraph is saved. It must be a real
# syntactic path for the parent __init__'s resolve()/realpath() calls, but it
# never names a directory that exists — every tool call is gated on
# ``active_grc_path()`` in _safe_resolve before any I/O touches it.
_UNSAVED_ROOT = Path("/grc-agent-unsaved-root")

_NO_ACTIVE_GRAPH_MSG = (
    "No active flowgraph is saved to disk, so there is no project folder to "
    "operate in. Ask the user to save the flowgraph first (File > Save), then retry."
)

# Read access is denied outright (the harness's own protected list only makes
# these read-only): the project root may be a repo checkout whose .env holds
# every provider API key, and .grc_agent/ holds our own lock/snapshot state.
_DENIED_PATTERNS = [".env", ".env.*", ".grc_agent/*"]


class GrcFileSystemToolset(FileSystemToolset[AgentDepsT]):
    """Harness filesystem toolset with a per-call dynamic root and `.grc` routing.

    The root follows the active flowgraph's folder: ``_root``/``_real_root``
    are properties over the providers above, so a tab switch or save changes
    the sandbox between two tool calls with no rebuild. The setters swallow
    the parent ``__init__``'s static assignments (it constructs against
    ``_UNSAVED_ROOT``); the dynamic value is authoritative.
    """

    def __init__(
        self,
        *,
        allowed_patterns: Sequence[str] = (),
        denied_patterns: Sequence[str] = _DENIED_PATTERNS,
        protected_patterns: Sequence[str] | None = None,
        max_read_lines: int = 1000,
        max_list_results: int = 200,
        max_search_results: int = 1000,
        max_find_results: int = 1000,
    ) -> None:
        super().__init__(
            root_dir=_UNSAVED_ROOT,
            allowed_patterns=list(allowed_patterns),
            denied_patterns=list(denied_patterns),
            protected_patterns=list(protected_patterns) if protected_patterns is not None else list(_DEFAULT_PROTECTED),
            max_read_lines=max_read_lines,
            max_list_results=max_list_results,
            max_search_results=max_search_results,
            max_find_results=max_find_results,
        )

    # -- dynamic root ------------------------------------------------------

    @property
    def _root(self) -> Path:
        grc = active_grc_path()
        return grc.parent.resolve() if grc is not None else _UNSAVED_ROOT

    @_root.setter
    def _root(self, value: Path) -> None:  # noqa: ARG002
        """Swallow the parent's static assignment — the dynamic root wins."""

    @property
    def _real_root(self) -> Path:
        return Path(os.path.realpath(self._root))

    @_real_root.setter
    def _real_root(self, value: Path) -> None:  # noqa: ARG002
        """Swallow the parent's static assignment — the dynamic root wins."""

    def _safe_resolve(self, path: str, *, write: bool = False, check_allowed: bool = True) -> Path:
        """Gate every tool on a saved active flowgraph, then resolve as usual."""
        if active_grc_path() is None:
            raise ValueError(_NO_ACTIVE_GRAPH_MSG)
        return super()._safe_resolve(path, write=write, check_allowed=check_allowed)

    # -- read_file with .grc routing ---------------------------------------

    @_recoverable
    async def read_file(self, path: str, *, offset: int = 0, limit: int | None = None) -> str:
        """Read a text file with line numbers.

        `.grc` files are never returned as raw XML — reading one yields the
        same structural inspection as the inspect_graph tool (topology,
        blocks, connections, parameter values, validation status). The active
        flowgraph is inspected from the live in-memory graph; any other `.grc`
        in the folder is loaded from disk. Use inspect_graph when you need a
        per-block (targets) view of the active graph.

        Args:
            path: File path relative to the root directory (the active
                flowgraph's folder).
            offset: Zero-based line offset to start reading from.
            limit: Maximum number of lines to return (default: 1000).

        Returns:
            File content with line numbers, plus metadata header.
        """
        resolved = self._safe_resolve(path)
        if resolved.suffix == ".grc":
            return self._inspect_grc_file(resolved)
        return await super().read_file(path, offset=offset, limit=limit)

    def _inspect_grc_file(self, resolved: Path) -> str:
        """Structural inspection of a `.grc` path via the inspect_graph engine."""
        active = active_grc_path()
        if active is not None and resolved == active:
            fg = _active_flow_graph_fn()
            if fg is not None:
                data = inspect_graph(fg, targets=None, view="overview")
                source = "live in-memory flowgraph (includes unsaved canvas edits)"
            else:
                data, source = self._load_and_inspect(resolved), "active file on disk"
        else:
            data, source = self._load_and_inspect(resolved), "file on disk"
        header = (
            f"[{resolved.name} | GNU Radio flowgraph | structural view via the "
            f"inspect_graph engine — source: {source}]\n"
        )
        return header + json.dumps(data)

    @staticmethod
    def _load_and_inspect(resolved: Path) -> dict[str, Any]:
        try:
            fg = load_flow_graph(str(resolved))
        except Exception as exc:  # arbitrary user files fail in arbitrary ways
            raise ValueError(f"Could not parse {resolved.name!r} as a flowgraph: {exc}") from exc
        return inspect_graph(fg, targets=None, view="overview")


@dataclass
class GrcFileSystem(AbstractCapability[AgentDepsT]):
    """Filesystem capability bound to the active flowgraph's folder.

    Configuration mirrors the harness ``FileSystem`` capability with this
    app's defaults: reads capped at 1000 lines per call, directory listings
    at 200 entries, `.env*`/`.grc_agent/` denied outright (the harness's
    own protected defaults — ``.git/``, keys, secrets — remain in force).
    """

    max_read_lines: int = 1000
    max_list_results: int = 200
    max_search_results: int = 1000
    max_find_results: int = 1000
    denied_patterns: Sequence[str] = field(default_factory=lambda: list(_DENIED_PATTERNS))

    def get_toolset(self) -> GrcFileSystemToolset[AgentDepsT]:
        return GrcFileSystemToolset[AgentDepsT](
            denied_patterns=self.denied_patterns,
            protected_patterns=list(_DEFAULT_PROTECTED),
            max_read_lines=self.max_read_lines,
            max_list_results=self.max_list_results,
            max_search_results=self.max_search_results,
            max_find_results=self.max_find_results,
        )
