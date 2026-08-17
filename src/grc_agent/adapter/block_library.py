"""Save an agent-authored Embedded Python Block (epy_block) into GNU Radio's
native hier-block library (``~/.grc_gnuradio``) so it becomes a real,
reusable catalog block for future flowgraphs — not an out-of-tree (OOT)
module (gr-modtool is a separate, heavier build toolchain this app has no
access to; see prompts.py).

Sole gnuradio-importing surface for this feature, alongside graph.py/rag.py
— adapter/ is the sole gnuradio importer (AGENTS.md)."""

import collections
import logging
from pathlib import Path
from typing import Any

from grc_agent.adapter.graph import _atomic_write_text, get_platform

_log = logging.getLogger(__name__)

_DEFAULT_CATEGORY = "[Custom]"


def hier_block_lib_dir() -> Path:
    """GNU Radio's own resolved hier-block library directory (respects
    GRC_HIER_PATH if set before this process started — Config.hier_block_lib_dir
    is a class attribute computed once at import time, so an env var change
    mid-process has no effect on it, same as on GNU Radio's own scan path;
    delegating to it (rather than re-reading the env var ourselves) keeps
    "where we write" and "where GNU Radio scans" from ever diverging)."""
    from gnuradio.grc.core.Config import Config

    return Path(Config.hier_block_lib_dir)


def _extract_epy_block_io(source: str) -> Any:
    """gnuradio.grc.core.utils.epy_block_io.extract() — the same
    introspection EPyBlock.rewrite() already uses to derive a live
    epy_block's params/ports from its source. Executes and instantiates the
    block's class as a side effect (that's how GNU Radio itself derives
    ports/params) — a source that fails here would already fail identically
    inside the live epy_block, so this doubles as a syntax/instantiation
    check for free."""
    from gnuradio.grc.core.utils import epy_block_io

    return epy_block_io.extract(source)


def _resolve_block_id(instance_name: str, block_id: str | None) -> str:
    candidate = block_id if block_id else instance_name
    if not candidate.isidentifier():
        raise ValueError(
            f"block_id {candidate!r} must be a valid Python identifier "
            "(it becomes both the catalog block id and the saved module's filename)."
        )
    return candidate


def _check_block_id_available(platform: Any, block_id: str, *, overwrite: bool) -> None:
    """GNU Radio's own loader does NOT raise on a duplicate block id — it
    silently overwrites (unless the id belongs to a genuine built-in). This
    is the one uniform rule that gates that risk before we ever write a
    file: derived from the id's real provenance (is its .block.yml already
    inside our own hier_block_lib_dir?), never a hardcoded id blacklist."""
    existing = platform.blocks.get(block_id)
    if existing is None:
        return
    loaded_from = getattr(existing, "loaded_from", None)
    is_ours = bool(loaded_from) and Path(loaded_from).parent == hier_block_lib_dir()
    if not is_ours:
        raise ValueError(
            f"block_id {block_id!r} already exists ({loaded_from or 'a built-in block'}) "
            "and is not a previously agent-saved block — choose a different block_id."
        )
    if not overwrite:
        raise ValueError(
            f"block_id {block_id!r} already exists as a previously agent-saved block "
            f"({loaded_from}). Pass overwrite=True to replace it, or choose a different block_id."
        )


def _render_block_yml(
    block_id: str, label: str, category: str, blk_io: Any, grc_source: str
) -> "collections.OrderedDict[str, Any]":
    """Builds the .block.yml nested data from an epy_block's extracted
    BlockIO. Every parameter gets dtype 'raw' uniformly — the same
    convention GNU Radio's own EPyBlock._update_params() already uses for
    these same dynamic params, not a guessed per-param dtype (which would be
    a hand-rolled heuristic)."""
    parameters = []
    for key, default_repr in blk_io.params:
        parameters.append(
            collections.OrderedDict(
                [
                    ("id", key),
                    ("label", key.replace("_", " ").title()),
                    ("dtype", "raw"),
                    ("default", default_repr),
                ]
            )
        )

    def render_ports(specs: list[tuple[str, str, int]]) -> list[dict]:
        ports = []
        for key, port_type, vlen in specs:
            if port_type == "message":
                ports.append(collections.OrderedDict([("domain", "message"), ("id", key)]))
            else:
                p = collections.OrderedDict([("id", key), ("dtype", port_type)])
                if vlen and vlen != 1:
                    p["vlen"] = str(vlen)
                ports.append(p)
        return ports

    # Import the whole module under an alias (not HierBlockGenerator's own
    # "from {id} import {id}" — that assumes the generated class name always
    # equals the module/block id, which only holds for hier blocks. Our .py
    # source is copied byte-for-byte from the user's epy_block, whose class
    # name (blk_io.cls) is NOT guaranteed to match block_id.) The trailing
    # "# grc-generated hier_block" comment is load-bearing, not decorative:
    # TopBlockGenerator._imports() greps every block's imports line for this
    # exact suffix to decide whether to inject the sys.path.append(...
    # ~/.grc_gnuradio ...) hack into a generated flowgraph script. Omitting
    # it would make this block silently unimportable (ModuleNotFoundError)
    # from any flowgraph that isn't itself already a hier block.
    imports = f"import {block_id} as {block_id}  # grc-generated hier_block"
    make_args = ", ".join(f"{k}=${{ {k} }}" for k, _ in blk_io.params)
    make = f"{block_id}.{blk_io.cls}({make_args})"
    callbacks = [f"{k} = ${{ {k} }}" for k in blk_io.callbacks]

    doc_lines = [line for line in (blk_io.doc or "").strip().splitlines() if line.strip()]
    doc_lines.append(f"Saved from GRC Agent flowgraph block {grc_source!r}.")

    data: collections.OrderedDict[str, Any] = collections.OrderedDict()
    data["id"] = block_id
    data["label"] = label
    data["category"] = category
    data["parameters"] = parameters
    data["inputs"] = render_ports(blk_io.sinks)
    data["outputs"] = render_ports(blk_io.sources)
    data["templates"] = collections.OrderedDict(
        [("imports", imports), ("make", make), ("callbacks", callbacks)]
    )
    data["documentation"] = "\n".join(doc_lines)
    data["grc_source"] = str(grc_source or "")
    data["file_format"] = 1
    return data


def _dump_block_yml(data: "collections.OrderedDict[str, Any]") -> str:
    # gnuradio.grc.core.io.yaml has a real circular import if imported
    # before the params/blocks package chain has fully initialized
    # (confirmed live). Every caller of this function reaches it only after
    # get_platform() has already run (which imports gnuradio.grc.core.platform
    # first), so that chain is always already warm by this point.
    from gnuradio.grc.core.io import yaml

    return yaml.dump(data)


def _validate_block_definition(yml_data: "collections.OrderedDict[str, Any]") -> dict[str, Any]:
    """Validates the rendered block definition WITHOUT ever calling
    Platform.build_library() on a fresh Platform instance.

    Confirmed live during development of this feature: gnuradio.grc.core.
    platform.Platform.block_classes (the headless Platform get_platform()
    builds) is a single ChainMap defined as a Platform CLASS attribute
    (``block_classes = ChainMap({}, block_classes_build_in)``), and
    Platform.__init__ does ``self.blocks = self.block_classes`` — a
    reference, not a copy. Every instance of that headless class, even a
    freshly-constructed "throwaway" one, therefore shares the exact same
    underlying dict — there is no way to get an isolated headless registry
    by constructing a new headless Platform. (gnuradio.grc.gui.Platform.
    Platform is a separate subclass that overrides block_classes with its
    own independent ChainMap — confirmed live — so this hazard is
    specifically headless-vs-headless, not headless-vs-GUI; this function
    only ever constructs headless instances.) Calling build_library() on
    ANY headless instance clears and rebuilds that ONE shared registry for
    the WHOLE PROCESS — this silently wiped the real get_platform()
    singleton's 'options' block process-wide when first tried here.

    Platform.new_block_class() (== blocks.build()) has no such side effect —
    confirmed live it's a pure function that just returns a new Block
    subclass, never touching self.blocks. Validation instantiates that class
    directly against a real, safe, throwaway FlowGraph from the REAL,
    already-built get_platform() singleton instead — this never mutates any
    shared registry.

    Deliberately does NOT call block.validate()/is_valid(): confirmed live
    that a freshly-instantiated, unwired, un-id'd block ALWAYS reports
    "Port is not connected" and "ID must begin with a letter" — both
    artifacts of validating a standalone instance outside a real flowgraph,
    not defects in the exported definition. Matches this module's own
    _throwaway_block() precedent (graph.py), which introspects a throwaway
    block's schema without ever calling validate() for the same reason."""
    platform = get_platform()
    try:
        block_cls = platform.new_block_class(**yml_data)
    except Exception as e:
        return {"ok": False, "error_type": "block_load_failed", "errors": [str(e)]}

    try:
        fg = platform.make_flow_graph()
        block = block_cls(fg)
        fg.blocks.append(block)
        block.rewrite()
    except Exception as e:
        return {"ok": False, "error_type": "block_load_failed", "errors": [str(e)]}

    return {"ok": True}


def _commit_to_library(block_id: str, yml_text: str, py_text: str) -> dict[str, Path]:
    lib_dir = hier_block_lib_dir()
    yml_path = lib_dir / f"{block_id}.block.yml"
    py_path = lib_dir / f"{block_id}.py"
    _atomic_write_text(yml_text, yml_path)
    _atomic_write_text(py_text, py_path)
    return {"yml": yml_path, "py": py_path}


def _refresh_catalog_platform() -> None:
    """Rebuilds the headless catalog Platform AND invalidates the RAG
    layer's own per-process freshness caches. Rebuilding build_library()
    alone is not enough: rag.py's _CORPUS_VERSION_CACHE/_FRESHNESS_CACHE
    short-circuit the staleness check once verified fresh in-process, so
    without popping them, query_knowledge would keep silently returning
    stale results (missing the new block) for the rest of the process's
    life."""
    from grc_agent.adapter import rag

    get_platform().build_library()
    rag._CORPUS_VERSION_CACHE.pop("catalog", None)
    rag._FRESHNESS_CACHE.pop("catalog", None)


def save_block_to_library(
    flow_graph: Any,
    instance_name: str,
    block_id: str | None = None,
    label: str | None = None,
    category: str | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Exports an existing epy_block instance's source into GNU Radio's
    native hier-block library (~/.grc_gnuradio) as a new, standalone catalog
    block. Never mutates the current flowgraph's own epy_block instance —
    it keeps using its own local inline source, unaffected. The exported
    block is a separately-block_id-named catalog entry available for FUTURE
    use in this flowgraph or any other.

    Only rebuilds the headless catalog Platform (_refresh_catalog_platform)
    — never a GUI Platform. The live app's caller (NativeFlowgraphProxy.
    save_block) always calls NativeCanvasManager.reload_block_library()
    afterward on success, which itself rebuilds the GUI Platform AND
    repopulates the visible block-tree panel; a separate gui_platform.
    build_library() call here would just be the same rebuild running twice."""
    try:
        block = flow_graph.get_block(instance_name)
    except KeyError:
        return {
            "ok": False,
            "error_type": "block_not_found",
            "errors": [f"No block named {instance_name!r} in the flowgraph."],
        }

    if getattr(block, "key", None) != "epy_block":
        return {
            "ok": False,
            "error_type": "not_an_epy_block",
            "errors": [
                f"Block {instance_name!r} is a {getattr(block, 'key', '?')!r}, not an "
                "epy_block. Only Embedded Python Blocks can be saved to the library."
            ],
        }

    try:
        resolved_id = _resolve_block_id(instance_name, block_id)
    except ValueError as e:
        return {"ok": False, "error_type": "invalid_block_id", "errors": [str(e)]}

    platform = get_platform()
    try:
        _check_block_id_available(platform, resolved_id, overwrite=overwrite)
    except ValueError as e:
        return {"ok": False, "error_type": "block_id_collision", "errors": [str(e)]}

    source = block.params["_source_code"].get_value()
    try:
        blk_io = _extract_epy_block_io(source)
    except Exception as e:
        return {"ok": False, "error_type": "epy_extract_failed", "errors": [str(e)]}

    resolved_label = label or resolved_id.replace("_", " ").title()
    resolved_category = category or _DEFAULT_CATEGORY
    grc_source = getattr(flow_graph, "grc_file_path", "") or ""
    yml_data = _render_block_yml(resolved_id, resolved_label, resolved_category, blk_io, grc_source)

    validation = _validate_block_definition(yml_data)
    if not validation.get("ok"):
        return {
            "ok": False,
            "error_type": validation.get("error_type", "validation_failed"),
            "errors": validation.get("errors", []),
        }

    yml_text = _dump_block_yml(yml_data)
    saved_paths = _commit_to_library(resolved_id, yml_text, source)
    _refresh_catalog_platform()

    return {
        "ok": True,
        "block_id": resolved_id,
        "label": resolved_label,
        "category": resolved_category,
        "saved_to": {"block_yml": str(saved_paths["yml"]), "py": str(saved_paths["py"])},
        "params": [k for k, _ in blk_io.params],
        "inputs": [k for k, _t, _v in blk_io.sinks],
        "outputs": [k for k, _t, _v in blk_io.sources],
    }
