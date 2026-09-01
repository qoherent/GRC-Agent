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
import logging
import mimetypes
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, get_args

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import BinaryContent, ImageMediaType
from pydantic_ai.tools import AgentDepsT

# Private-but-stable helpers of the harness toolset: the error taxonomy
# (``_recoverable`` converts model-correctable failures into ModelRetry) and
# the 12-hex content hash that read_file headers surface and write/edit
# accept back as ``expected_hash`` — reusing it keeps the hash contract
# identical across the parent tools and this subclass.
#
# These three have NO public equivalent as of pydantic_ai_harness 0.23:
# ``filesystem.__all__`` is only {FileSystem, FileSystemToolset,
# READ_ONLY_TOOL_NAMES}. Re-checked on every harness bump — if any of them
# gains a public name, switch to it; the coupling is deliberate, not an
# oversight.
from pydantic_ai_harness.filesystem._capability import _DEFAULT_PROTECTED
from pydantic_ai_harness.filesystem._toolset import (
    FileSystemToolset,
    _content_hash,
    _recoverable,
)

from grc_agent.adapter.graph import (
    _NO_ACTIVE_GRAPH_MSG,
    _atomic_write_text,
    inspect_graph,
    load_flow_graph,
)

_log = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Active-graph providers (installed by desktop_app once the canvas exists).
# --------------------------------------------------------------------------

def _no_grc() -> Path | str | None:
    return None


def _no_flow_graph() -> Any | None:
    return None


_active_grc_path_fn: Callable[[], Path | str | None] = _no_grc
_active_flow_graph_fn: Callable[[], Any | None] = _no_flow_graph
_project_dir_fn: Callable[[], Path | str | None] = _no_grc


def set_active_graph_providers(
    grc_path_fn: Callable[[], Path | str | None],
    flow_graph_fn: Callable[[], Any | None],
    project_dir_fn: Callable[[], Path | str | None] | None = None,
) -> None:
    """Install the callables that resolve the active flowgraph and project directory.

    Invoked lazily on every filesystem tool call. ``grc_path_fn`` returns the
    active page's file path; ``flow_graph_fn`` returns the live shared
    ``FlowGraph`` object; ``project_dir_fn`` returns the explicit project directory.
    """
    global _active_grc_path_fn, _active_flow_graph_fn, _project_dir_fn
    _active_grc_path_fn = grc_path_fn
    _active_flow_graph_fn = flow_graph_fn
    if project_dir_fn is not None:
        _project_dir_fn = project_dir_fn


def active_grc_path() -> Path | None:
    """Resolved path of the active flowgraph file, or ``None`` if unsaved."""
    raw = _active_grc_path_fn()
    if not raw:
        return None
    return Path(raw).resolve()


def active_project_dir() -> Path | None:
    """Resolved explicit project directory, or ``None`` if unset."""
    raw = _project_dir_fn()
    if not raw:
        return None
    p = Path(raw).resolve()
    return p if p.is_dir() else None


# Placeholder root used only while no flowgraph or project folder is saved.
_UNSAVED_ROOT = Path("/grc-agent-unsaved-root")

# Read access is denied outright (the harness's own protected list only makes
# these read-only): the project root may be a repo checkout whose .env holds
# every provider API key, and .grc_agent/ holds our own lock/snapshot state.
# `**/`-duplicated entries cover NESTED files (pkg/.env, .venv/.env) — the
# harness `_matches` strips a leading `**/` to cover the root-level case, and
# bare fnmatch `*` spans `/` for the nested one. Without the `**/` forms the
# deny patterns matched only root-level names (adversarial audit, live repro).
_DENIED_PATTERNS = [
    ".env",
    ".env.*",
    "**/.env",
    "**/.env.*",
    ".grc_agent/*",
    "**/.grc_agent/*",
    ".git/*",
    "**/.git/*",
    ".envrc",
    "**/.envrc",
]

# The one uniform write rule: a file's suffix decides whether it may be
# created or edited. `.grc` is deliberately absent — flowgraph writes are
# change_graph's exclusive province — and everything OOT-module work needs
# (gr-modtool emits CMake, C/C++/Python, YAML, XML, conf, RST) is present.
WRITE_SUFFIXES = frozenset(
    {
        ".py",
        ".cmake",
        ".txt",
        ".md",
        ".m",
        ".json",
        ".yml",
        ".yaml",
        ".c",
        ".cc",
        ".cpp",
        ".cxx",
        ".h",
        ".hh",
        ".hpp",
        ".xml",
        ".conf",
        ".rst",
        ".i",
    }
)

_WRITE_GRC_MSG = (
    "Writing .grc files is not allowed — flowgraphs are edited through the "
    "change_graph tool (add/remove blocks and connections, set parameter values)."
)


# Derived from pydantic-ai's ImageMediaType — the same upstream source the
# chat sidebar derives its set from, so the file tool's image passthrough and
# the composer's attachment gate cannot drift apart by construction.
_IMAGE_MEDIA_TYPES: tuple[str, ...] = get_args(ImageMediaType)


def _is_grc_name(name: str) -> bool:
    """One uniform flowgraph-name rule: any name containing `.grc` (case-insensitive).

    Covers `.grc`, editor backups (`.grc~`, `.grc.orig`), and case variants
    (`.GRC`) with one rule instead of a per-form list. The conservative
    direction is deliberate: routing an oddly-named text file (e.g.
    `notes.about.grc.md`) to the flowgraph parser fails with a clear
    could-not-parse error, while missing a real flowgraph variant would leak
    its raw source to the model.
    """
    return ".grc" in Path(name).name.lower()


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
        write_suffixes: frozenset[str] = WRITE_SUFFIXES,
        max_read_lines: int = 1000,
        max_list_results: int = 200,
        max_search_results: int = 1000,
        max_find_results: int = 1000,
    ) -> None:
        self._write_suffixes = frozenset(write_suffixes)
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

    # -- project root ------------------------------------------------------

    @property
    def _root(self) -> Path:
        proj = active_project_dir()
        if proj is not None:
            return proj
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
        """Gate every tool on a configured project folder, then resolve as usual."""
        if self._root == _UNSAVED_ROOT:
            raise ValueError(_NO_ACTIVE_GRAPH_MSG)
        return super()._safe_resolve(path, write=write, check_allowed=check_allowed)

    def _assert_writable_suffix(self, path: str, resolved: Path | None = None) -> None:
        """One uniform write rule: the suffix allowlist, applied to BOTH the
        requested name and the resolved target (an in-root symlink can name a
        `.grc` target as `alias.py` — the resolved name closes that bypass)."""
        names = [Path(path).name] + ([resolved.name] if resolved is not None else [])
        for name in names:
            if _is_grc_name(name):
                raise ValueError(_WRITE_GRC_MSG)
        suffixes = [Path(path).suffix.lower()] + ([resolved.suffix.lower()] if resolved is not None else [])
        for suffix in suffixes:
            if suffix not in self._write_suffixes:
                what = f"{suffix!r} files" if suffix else "files without an extension"
                allowed = ", ".join(sorted(self._write_suffixes))
                raise ValueError(f"Writing {what} is not allowed. Allowed extensions: {allowed}.")

    # -- read_file with .grc routing ---------------------------------------

    @_recoverable
    async def read_file(self, path: str, *, offset: int = 0, limit: int | None = None) -> str | BinaryContent:
        """Read a file with line numbers; image files pass through whole.

        `.grc` files are never returned as raw XML — reading one yields the
        same structural inspection as the inspect_graph tool (topology,
        blocks, connections, parameter values, validation status). The active
        flowgraph is inspected from the live in-memory graph; any other `.grc`
        in the folder is loaded from disk. Use inspect_graph when you need a
        per-block (targets) view of the active graph.

        Image files (pydantic-ai's `ImageMediaType` set, derived via
        `get_args` so it cannot drift) are returned as `BinaryContent` so
        vision-capable models receive the actual pixels in their multimodal
        context — pydantic-ai's `ToolReturnContent` carries them natively —
        instead of the parent's binary-placeholder string.

        Args:
            path: File path relative to the project directory.
            offset: Zero-based line offset to start reading from.
            limit: Maximum number of lines to return (default: 1000).

        Returns:
            File content with line numbers, or the image as `BinaryContent`.
        """
        resolved = self._safe_resolve(path)
        if _is_grc_name(resolved.name):
            if not resolved.is_file():
                raise FileNotFoundError(f"File not found: {path}")
            return self._inspect_grc_file(resolved)
        media_type, _ = mimetypes.guess_type(resolved.name)
        if media_type in _IMAGE_MEDIA_TYPES:
            if not resolved.is_file():
                raise FileNotFoundError(f"File not found: {path}")
            return BinaryContent(data=resolved.read_bytes(), media_type=media_type)
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
            # Fixed message only: parse exceptions interpolate arbitrary file
            # content, and ModelRetry text is NOT classified by the injection
            # defender (retries skip after_tool_execute). The details go to the
            # log instead.
            _log.warning("read_file could not parse %s as a flowgraph: %s", resolved.name, exc)
            raise ValueError(f"Could not parse {resolved.name!r} as a GNU Radio flowgraph file.") from exc
        return inspect_graph(fg, targets=None, view="overview")

    # -- write_file with a suffix allowlist and atomic replacement ----------

    @_recoverable
    async def write_file(self, path: str, content: str, *, expected_hash: str | None = None) -> str:
        """Create or overwrite a file with conflict detection.

        Writes are restricted by extension to source/config formats (.py,
        .cmake, .txt, .md, .m, .json, .yml/.yaml, C/C++ headers and sources,
        .xml, .conf, .rst, .i). `.grc` can never be written — flowgraph edits
        go through the change_graph tool. The write is atomic (temp file →
        fsync → rename), and the parent directory must already exist (use
        create_directory first).

        Args:
            path: File path relative to the project directory.
            content: The text content to write.
            expected_hash: If provided, the write is rejected when the file exists
                and its current hash doesn't match (optimistic concurrency).

        Returns:
            Confirmation message with new hash.
        """
        self._assert_writable_suffix(path)
        resolved = self._safe_resolve(path, write=True)
        self._assert_writable_suffix(path, resolved)

        # Optimistic concurrency: reject stale writes
        if expected_hash is not None and resolved.is_file():
            current = resolved.read_text(encoding="utf-8")
            if _content_hash(current) != expected_hash:
                raise ValueError(
                    f"Conflict: file {path!r} has changed (expected hash:{expected_hash}, "
                    f"got hash:{_content_hash(current)}). Re-read the file and retry."
                )

        if not resolved.parent.exists():
            parent_rel = str(resolved.parent.relative_to(self._root))
            raise FileNotFoundError(
                f"Parent directory '{parent_rel}' does not exist. Use create_directory first."
            )
        _atomic_write_text(content, resolved)
        new_hash = _content_hash(content)
        lines = len(content.splitlines())
        return f"Wrote {len(content)} chars ({lines} lines) to {path}. [hash:{new_hash}]"

    # -- edit_file: same write rule, exact-string replacement -----------------

    @_recoverable
    async def edit_file(self, path: str, old_text: str, new_text: str, *, expected_hash: str | None = None) -> str:
        """Edit a file by exact string replacement with conflict detection.

        Same extension allowlist as write_file (never `.grc` — flowgraph edits
        go through the change_graph tool). The old_text must appear exactly
        once in the file; include surrounding context to ensure uniqueness.
        The replacement is written atomically.

        Args:
            path: File path relative to the root directory.
            old_text: The exact text to find (must appear exactly once).
            new_text: The replacement text.
            expected_hash: If provided, rejects the edit when the file's
                current hash doesn't match (optimistic concurrency).

        Returns:
            Summary with new hash for subsequent operations.
        """
        self._assert_writable_suffix(path)
        resolved = self._safe_resolve(path, write=True)
        self._assert_writable_suffix(path, resolved)
        if not resolved.is_file():
            raise FileNotFoundError(f"File not found: {path}")

        text = resolved.read_text(encoding="utf-8")
        current_hash = _content_hash(text)

        if expected_hash is not None and current_hash != expected_hash:
            raise ValueError(
                f"Conflict: file {path!r} has changed (expected hash:{expected_hash}, "
                f"got hash:{current_hash}). Re-read the file and retry."
            )

        count = text.count(old_text)
        if count == 0:
            raise ValueError(f"old_text not found in {path}.")
        if count > 1:
            raise ValueError(
                f"old_text found {count} times in {path}. Include more surrounding context to make the match unique."
            )

        new_content = text.replace(old_text, new_text, 1)
        _atomic_write_text(new_content, resolved)
        return f"Edited {path}. [hash:{_content_hash(new_content)}]"


@dataclass
class GrcFileSystem(AbstractCapability[AgentDepsT]):
    """Filesystem capability bound to the active flowgraph's folder.

    Configuration mirrors the harness ``FileSystem`` capability with this
    app's defaults: reads capped at 1000 lines per call, directory listings
    at 200 entries, and `.env`/`.env.*`/`.envrc`/`.grc_agent/`/`.git/` denied
    outright — root-level AND nested (`**/` forms) — on top of the
    harness-protected defaults (keys, secrets).
    """

    max_read_lines: int = 1000
    max_list_results: int = 200
    max_search_results: int = 1000
    max_find_results: int = 1000
    denied_patterns: Sequence[str] = field(default_factory=lambda: list(_DENIED_PATTERNS))
    write_suffixes: frozenset[str] = WRITE_SUFFIXES

    def get_toolset(self) -> GrcFileSystemToolset[AgentDepsT]:
        return GrcFileSystemToolset[AgentDepsT](
            denied_patterns=self.denied_patterns,
            protected_patterns=list(_DEFAULT_PROTECTED),
            write_suffixes=self.write_suffixes,
            max_read_lines=self.max_read_lines,
            max_list_results=self.max_list_results,
            max_search_results=self.max_search_results,
            max_find_results=self.max_find_results,
        )
