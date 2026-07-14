import fcntl
import functools
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Any

import sqlite_vec
from grandalf.graphs import Edge as GrandalfEdge
from grandalf.graphs import Graph as GrandalfGraph
from grandalf.graphs import Vertex as GrandalfVertex
from grandalf.layouts import SugiyamaLayout, VertexViewer
from openai import APIConnectionError, OpenAI

from grc_agent._paths import vectors_dir

_PLATFORM: Any = None


def get_platform() -> Any:
    global _PLATFORM
    if _PLATFORM is not None:
        return _PLATFORM
    from gnuradio import gr
    from gnuradio.grc.core.platform import Platform

    _PLATFORM = Platform(
        name="grc_agent",
        prefs=gr.prefs(),
        version=gr.version(),
        version_parts=(gr.major_version(), gr.api_version(), gr.minor_version()),
    )
    _PLATFORM.build_library()
    return _PLATFORM


def get_gui_platform() -> Any:
    """GUI Platform (gnuradio.grc.gui) for the canvas subprocess. Kept lazy
    and separate from the headless get_platform() so importing adapter never
    pulls GTK/gi — adapter stays the sole importer of gnuradio (core *and*
    gui), and headless paths (unit tests, scenario harness) stay GTK-free.

    gnuradio.grc.gui transitively imports Gtk at module load, which requires
    gi.require_version first — we set that up here so the accessor is
    self-contained (idempotent if the caller already did it)."""
    import gi

    gi.require_version("Gtk", "3.0")
    gi.require_version("PangoCairo", "1.0")
    from gnuradio import gr
    from gnuradio.grc.gui.Platform import Platform

    platform = Platform(
        version=gr.version(),
        version_parts=(gr.major_version(), gr.api_version(), gr.minor_version()),
        prefs=gr.prefs(),
        install_prefix=gr.prefix(),
    )
    platform.build_library()
    return platform


def gui_application_cls() -> Any:
    """Lazy accessor for the GRC GUI Application class (canvas subprocess).
    Same self-contained gi setup as get_gui_platform."""
    import gi

    gi.require_version("Gtk", "3.0")
    gi.require_version("PangoCairo", "1.0")
    from gnuradio.grc.gui.Application import Application

    return Application


def disable_native_undo_redo() -> None:
    """GRC's own GUI ships a complete, working undo/redo (gnuradio.grc.gui's
    StateCache + Actions.FLOW_GRAPH_UNDO/REDO on Ctrl+Z/Ctrl+Y) that stays
    reachable even with canvas_app.py's chrome hidden — live-confirmed: it
    visibly moves things back on the canvas, but never touches disk, so it
    silently diverges from the shared undo/redo stack in this module (which
    IS disk-synced across both the agent and manual-edit paths). Disabling
    the native one keeps exactly one undo/redo history reachable, avoiding
    that divergence. canvas_app.py calls this once at startup rather than
    importing gnuradio.grc.gui.Actions itself, keeping this module the sole
    gnuradio importer."""
    from gnuradio.grc.gui import Actions

    Actions.FLOW_GRAPH_UNDO.set_enabled(False)
    Actions.FLOW_GRAPH_REDO.set_enabled(False)


def hide_panels_by_default(app: Any) -> None:
    """Hide GRC's panels (block library, console, and variable editor) by default."""
    from gnuradio.grc.gui import Actions

    for action in (
        Actions.TOGGLE_BLOCKS_WINDOW,
        Actions.TOGGLE_CONSOLE_WINDOW,
        Actions.TOGGLE_FLOW_GRAPH_VAR_EDITOR,
    ):
        try:
            if action.get_active():
                app._handle_action(action)
        except Exception as e:
            print(f"Failed to hide GRC panel via action {action}: {e}")


def flow_graph_content_hash(flow_graph: Any) -> str:
    """Hash of what write_flow_graph_atomic would currently write for this
    flow_graph — directly comparable to a hash of the on-disk file's raw
    bytes (e.g. canvas_app.py's `_sha256_file`/`last_disk_hash`), since it's
    the exact same serialization. Used to detect in-memory edits that
    haven't reached disk yet (a safety net for GTK-native interactions that
    don't go through a specific, hooked signal — see canvas_app.py)."""
    return hashlib.sha256(_serialize_flow_graph(flow_graph).encode()).hexdigest()


def load_flow_graph(file_path: str) -> Any:
    platform = get_platform()
    flow_graph = platform.make_flow_graph()
    flow_graph.grc_file_path = str(Path(file_path).resolve())
    parsed = platform.parse_flow_graph(str(file_path))
    flow_graph.import_data(parsed)
    flow_graph.rewrite()
    return flow_graph


def parse_conn(conn_str: str):
    if "->" not in conn_str:
        return None
    src, dst = conn_str.split("->")
    if ":" not in src or ":" not in dst:
        return None
    src_block, src_port = src.split(":")
    dst_block, dst_port = dst.split(":")
    return {
        "src_block": src_block.strip(),
        "src_port": src_port.strip(),
        "dst_block": dst_block.strip(),
        "dst_port": dst_port.strip(),
    }


# Regex to find Python identifier tokens
_IDENTIFIER_RE = re.compile(r"[A-Za-z_]\w*")
# GRC template variable format
_VARIABLE_TEMPLATE_RE = re.compile(r"^\$\{variable:\s*([A-Za-z_]\w*)\s*\}$")


def _throwaway_block(block_type: str) -> Any:
    try:
        platform = get_platform()
        flow_graph = platform.make_flow_graph()
        return flow_graph.new_block(block_type)
    except Exception:
        return None


@functools.lru_cache(maxsize=128)
def param_metadata(block_type: str) -> dict[str, dict[str, str]]:
    block = _throwaway_block(block_type)
    if block is None:
        return {}
    try:
        return {
            str(name): {
                "category": str(getattr(param, "category", "General")),
                "dtype": str(getattr(param, "dtype", "")),
                "default": str(getattr(param, "default", "")),
            }
            for name, param in block.params.items()
        }
    except Exception:
        return {}


@functools.lru_cache(maxsize=128)
def port_metadata(block_type: str) -> dict[str, dict[str, dict[str, Any]]]:
    block = _throwaway_block(block_type)
    if block is None:
        return {}
    try:

        def _collect(ports: Any) -> dict[str, dict[str, Any]]:
            return {
                str(port.key): {
                    "hidden": bool(getattr(port, "hidden", False)),
                    "raw_dtype": str(getattr(port, "_dtype", "") or ""),
                    "raw_multiplicity": str(getattr(port, "_multiplicity", "") or ""),
                }
                for port in ports
            }

        return {
            "inputs": _collect(getattr(block, "sinks", ()) or ()),
            "outputs": _collect(getattr(block, "sources", ()) or ()),
        }
    except Exception:
        return {}


@functools.lru_cache(maxsize=128)
def type_controlling_params(block_type: str) -> frozenset[str]:
    enum_params = {k for k, v in param_metadata(block_type).items() if v["dtype"] == "enum"}
    if not enum_params:
        return frozenset()
    referenced: set[str] = set()
    for direction_meta in port_metadata(block_type).values():
        for info in direction_meta.values():
            raw = info["raw_dtype"]
            if raw:
                referenced.update(_IDENTIFIER_RE.findall(raw))
    return frozenset(enum_params & referenced)


@functools.lru_cache(maxsize=128)
def port_count_controlling_params(block_type: str) -> frozenset[str]:
    param_ids = set(param_metadata(block_type).keys())
    if not param_ids:
        return frozenset()
    referenced: set[str] = set()
    for direction_meta in port_metadata(block_type).values():
        for info in direction_meta.values():
            raw = info["raw_multiplicity"]
            if raw:
                referenced.update(_IDENTIFIER_RE.findall(raw))
    return frozenset(param_ids & referenced)


def ports_governed_by(block_type: str, param_key: str) -> tuple[frozenset[str], frozenset[str]]:
    meta = port_metadata(block_type)

    def _match(direction: str) -> frozenset[str]:
        return frozenset(
            key
            for key, info in meta.get(direction, {}).items()
            if param_key in _IDENTIFIER_RE.findall(info["raw_dtype"])
        )

    return _match("inputs"), _match("outputs")


def resolve_auto(
    flow_graph: Any,
    block_name: str,
    param_key: str,
    add_connections: list[str] = None,
    new_block_names: set[str] = None,
    is_add_phase: bool = True,
    add_blocks: list[dict] = None,
    update_params: list[dict] = None,
) -> str | None:
    try:
        block = flow_graph.get_block(block_name)
        block_type = block.key
    except KeyError:
        raise ValueError(
            f"Cannot auto-resolve param {param_key!r}: block {block_name!r} not found."
        ) from None

    in_ports, out_ports = ports_governed_by(block_type, param_key)

    if not is_add_phase:
        # Check existing live connections on target block for update_params phase
        live_dtypes = set()
        for conn in flow_graph.connections:
            if conn.source_block.name == block_name:
                own_port_key = str(conn.source_port.key)
                if own_port_key in out_ports:
                    dtype = getattr(conn.sink_port, "dtype", None)
                    if dtype:
                        live_dtypes.add(str(dtype))
            elif conn.sink_block.name == block_name:
                own_port_key = str(conn.sink_port.key)
                if own_port_key in in_ports:
                    dtype = getattr(conn.source_port, "dtype", None)
                    if dtype:
                        live_dtypes.add(str(dtype))
        if len(live_dtypes) == 1:
            return list(live_dtypes)[0]
        elif len(live_dtypes) > 1:
            raise ValueError(
                f"Auto-resolution conflict: multiple neighbor types found {live_dtypes}"
            )

    # Check batch new connections
    new_dtype = None
    if add_connections:
        for conn_str in add_connections:
            p = parse_conn(conn_str)
            if not p:
                continue
            other = None
            port_key = None
            own_port_key = None
            own_direction = None
            if p["src_block"] == block_name:
                other = p["dst_block"]
                port_key = p["dst_port"]
                own_port_key = p["src_port"]
                own_direction = "outputs"
            elif p["dst_block"] == block_name:
                other = p["src_block"]
                port_key = p["src_port"]
                own_port_key = p["dst_port"]
                own_direction = "inputs"

            if not other or not port_key:
                continue
            if own_direction == "inputs" and own_port_key not in in_ports:
                continue
            if own_direction == "outputs" and own_port_key not in out_ports:
                continue

            try:
                other_block = flow_graph.get_block(other)

                # Check batch context first
                other_type_val = None
                if add_blocks:
                    for ab in add_blocks:
                        if ab.get("instance_name") == other:
                            ctrls = type_controlling_params(ab["block_id"])
                            for cp in ctrls:
                                val = (ab.get("params") or {}).get(cp)
                                if val and val != "auto":
                                    other_type_val = val
                                    break
                if not other_type_val and update_params:
                    for up in update_params:
                        if up.get("instance_name") == other:
                            ctrls = type_controlling_params(other_block.key)
                            for cp in ctrls:
                                val = (up.get("params") or {}).get(cp)
                                if val and val != "auto":
                                    other_type_val = val
                                    break

                if other_type_val:
                    dtype_map = {
                        "complex": "complex",
                        "float": "float",
                        "int": "int",
                        "short": "short",
                        "byte": "byte",
                        "fc32": "complex",
                        "f32": "float",
                        "s32": "int",
                        "s16": "short",
                        "u8": "byte",
                    }
                    dtype_str = dtype_map.get(other_type_val, other_type_val)
                    if new_block_names and other in new_block_names:
                        if new_dtype is None:
                            new_dtype = dtype_str
                    else:
                        return dtype_str
                elif not (new_block_names and other in new_block_names):
                    # `other` is an existing, pre-existing block with no
                    # explicit value set on it THIS batch — its current live
                    # port dtype is a real, already-in-effect value (whatever
                    # a prior save left it at), so propagating it is a
                    # legitimate resolution. If `other` is ALSO brand-new
                    # with no explicit value anywhere, its "live" port dtype
                    # is just its own untouched schema default — reading
                    # that here would silently pair two arbitrary,
                    # independently-defaulted blocks and call it resolved
                    # (confirmed live: analog_sig_source_x + qtgui_time_sink_x
                    # both default to 'complex', so this looked like a
                    # working resolution purely by coincidence; a pair with
                    # different defaults produced a genuinely mismatched,
                    # silently-broken connection). Deliberately not treated
                    # as a candidate at all in that case — see the final
                    # ValueError below.
                    ports = (
                        other_block.active_sources
                        if own_direction == "inputs"
                        else other_block.active_sinks
                    )
                    for prt in ports:
                        if str(prt.key) == str(port_key):
                            dtype = getattr(prt, "dtype", None)
                            if dtype:
                                return str(dtype)
            except KeyError:
                continue

    if new_dtype:
        return new_dtype
    raise ValueError(
        f"Cannot auto-resolve param {param_key!r} on block {block_name!r}: no "
        f"explicit (non-'auto') type value found on this block, any connected "
        f"neighbor in this batch, or any pre-existing connected neighbor. Set "
        f"an explicit type value on at least one side of this connection "
        f"instead of 'auto' on both."
    )


def set_block_state(block: Any, state: str) -> None:
    aliases = {"bypass": "bypassed"}
    canonical = aliases.get(state, state)
    if canonical not in block.STATE_LABELS:
        raise ValueError(f"Invalid state {state!r}; must be one of {block.STATE_LABELS}")
    block.state = canonical


def keep_param(
    param_key: str,
    param: Any,
    block: Any,
    mode: str = "overview",
    variable_names: set[str] | None = None,
) -> bool:
    hide = getattr(param, "hide", "none") or "none"
    dtype = getattr(param, "dtype", "") or ""
    value = str(param.value)
    default = str(getattr(param, "default", ""))

    if dtype == "id" or param_key == "showports" or param_key.startswith("bus_structure_"):
        return False
    if hide == "all":
        return False
    if dtype == "gui_hint":
        return False

    if mode != "overview":
        return True

    # Stage B Parameter visibility rules
    if hide == "none":
        return True

    is_type_controlling = param_key in type_controlling_params(block.key)
    # Port-count-controlling params are deliberately excluded here: only
    # type-controlling params and generate_options count as structural for
    # the Stage B keep rule.
    is_structural_enum = is_type_controlling or param_key == "generate_options"

    if hide == "part" and not is_structural_enum:
        is_custom = value != default
        is_var_ref = variable_names and any(
            tok in variable_names for tok in _IDENTIFIER_RE.findall(value)
        )
        if not (is_custom or is_var_ref):
            return False

    if dtype == "enum":
        if value != default or is_structural_enum:
            return True
        return False

    if value != default:
        return True
    if variable_names and any(tok in variable_names for tok in _IDENTIFIER_RE.findall(value)):
        return True

    return False


def render_port(port: Any, mode: str = "overview") -> dict[str, Any] | None:
    optional = bool(getattr(port, "optional", False))
    connected = len(list(port.connections(enabled=True))) > 0
    if mode == "overview" and optional and not connected:
        return None
    domain = str(getattr(port, "domain", "") or "")
    res = {"port_id": str(port.key), "dtype": str(getattr(port, "dtype", ""))}
    if domain and domain != "stream":
        res["domain"] = domain
    return res


def classify_role(b: Any) -> str:
    is_variable = bool(getattr(b, "is_variable", False))
    is_import = bool(getattr(b, "is_import", False))
    is_snippet = bool(getattr(b, "is_snippet", False))
    is_virtual_or_pad = bool(getattr(b, "is_virtual_or_pad", False))
    has_sources = len(getattr(b, "active_sources", ()) or ()) > 0
    has_sinks = len(getattr(b, "active_sinks", ()) or ()) > 0

    if is_variable:
        return "variable"
    if is_import:
        return "import"
    if is_snippet:
        return "snippet"
    if is_virtual_or_pad:
        return "virtual_or_pad"
    if getattr(b, "key", "") == "options":
        return "options"
    if has_sources and not has_sinks:
        return "source"
    if has_sinks and not has_sources:
        return "sink"
    if has_sources and has_sinks:
        return "transform"
    return "other"


def port_object(flow_graph: Any, block_name: str, port_key: str, *, kind: str) -> Any:
    try:
        block = flow_graph.get_block(block_name)
    except KeyError:
        return None
    ports = block.active_sinks if kind == "sink" else block.active_sources
    for p in ports:
        if p.key == port_key:
            return p
    return None


def _find_port(flow_graph: Any, block_name: str, port_key: str, *, kind: str) -> Any:
    port = port_object(flow_graph, block_name, port_key, kind=kind)
    if port is not None:
        return port
    try:
        block = flow_graph.get_block(block_name)
    except KeyError:
        raise KeyError(f"block {block_name!r} does not exist") from None
    message = f"{kind} port {port_key!r} not on block {block_name!r}"
    count_params = port_count_controlling_params(block.key)
    if count_params:
        current = ", ".join(
            f"{key}={block.params[key].value!r}"
            for key in sorted(count_params)
            if key in block.params
        )
        if current:
            message += f". This block's port count is controlled by {current}."
    raise KeyError(message)


def inspect_graph(
    flow_graph: Any, targets: list[str] = None, view: str = "overview"
) -> dict[str, Any]:
    selected_view = str(view).strip().lower()
    blocks_all = []
    connections_all = []

    for c in flow_graph.connections:
        conn_str = (
            f"{c.source_block.name}:{c.source_port.key}->{c.sink_block.name}:{c.sink_port.key}"
        )
        connections_all.append(conn_str)

    variable_names = {b.name for b in flow_graph.blocks if getattr(b, "is_variable", False)}

    for b in flow_graph.blocks:
        params = {}
        omitted_params_count = 0
        for k, p in b.params.items():
            if keep_param(k, p, b, mode=selected_view, variable_names=variable_names):
                params[k] = str(p.value)
            else:
                omitted_params_count += 1

        inputs = []
        omitted_inputs_count = 0
        for p in getattr(b, "active_sinks", ()) or ():
            rendered = render_port(p, mode=selected_view)
            if rendered is not None:
                inputs.append(rendered)
            else:
                omitted_inputs_count += 1

        outputs = []
        omitted_outputs_count = 0
        for p in getattr(b, "active_sources", ()) or ():
            rendered = render_port(p, mode=selected_view)
            if rendered is not None:
                outputs.append(rendered)
            else:
                omitted_outputs_count += 1

        role = classify_role(b)
        state = str(getattr(b, "state", "enabled"))
        if state == "bypassed":
            state = "bypass"

        blocks_all.append(
            {
                "instance_name": b.name,
                "block_id": b.key,
                "role": role,
                "state": state,
                "params": params,
                "inputs": inputs,
                "outputs": outputs,
                "omitted_params_count": omitted_params_count,
                "omitted_inputs_count": omitted_inputs_count,
                "omitted_outputs_count": omitted_outputs_count,
            }
        )

    # is_valid()/iter_error_messages() only ever read _error_messages, which
    # validate() populates and rewrite() (called after every load/mutation)
    # clears without refilling — without this call they report "valid" with
    # zero errors regardless of the graph's actual state (confirmed live:
    # an unconnected required port went undetected until this was added).
    flow_graph.validate()
    valid = bool(flow_graph.is_valid())
    errors = []
    if not valid:
        for elem, msg in flow_graph.iter_error_messages():
            parent = getattr(elem, "parent_block", None)
            if parent is not None and parent is not elem:
                errors.append(f"{parent.name}: {elem}: {msg}")
            else:
                errors.append(f"{elem}: {msg}")

    whole_graph = not targets or any(t in ("all", "*") for t in targets)
    if not whole_graph:
        requested = set(targets)
        existing_names = {b["instance_name"] for b in blocks_all}
        missing = [t for t in targets if t not in existing_names]
        if missing:
            return {
                "ok": False,
                "errors": [
                    {
                        "code": "block_not_found",
                        "message": f"Unknown block name(s): {', '.join(missing)}",
                        "valid_blocks": [
                            {"instance_name": b["instance_name"], "block_id": b["block_id"]}
                            for b in blocks_all
                        ],
                    }
                ],
            }
        blocks = [b for b in blocks_all if b["instance_name"] in requested]
        connections = []
        for c in connections_all:
            p = parse_conn(c)
            if p and (p["src_block"] in requested or p["dst_block"] in requested):
                connections.append(c)
    else:
        blocks = blocks_all
        connections = connections_all

    opt_block = getattr(flow_graph, "options_block", None)
    graph_name = opt_block.name if opt_block is not None else ""

    return {
        "ok": True,
        "graph": {
            "graph_name": graph_name,
            "blocks": blocks,
            "connections": connections,
            "validation": {"status": "valid" if valid else "invalid", "errors": errors},
        },
    }


def _serialize_flow_graph(flow_graph: Any) -> str:
    from gnuradio.grc.core.io import yaml as _grc_yaml

    return _grc_yaml.dump(flow_graph.export_data())


def _atomic_write_text(payload: str, path: Path) -> None:
    """Shared by write_flow_graph_atomic and the undo/redo snapshot-restore
    path (undo_flowgraph/redo_flowgraph) — both need to atomically replace a
    file's content with an already-fully-serialized YAML string; the only
    difference is where that string came from (a live flow_graph object vs.
    an already-serialized undo snapshot read back off disk)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        try:
            dir_fd = os.open(str(path.parent), os.O_DIRECTORY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except (OSError, AttributeError):
            pass
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def write_flow_graph_atomic(flow_graph: Any, path: Path) -> None:
    _atomic_write_text(_serialize_flow_graph(flow_graph), Path(path))


MAX_BACKUPS_PER_DIR = 50


def _prune_old_backups(backup_dir: Path) -> None:
    """Every save snapshots the previous file into backup_dir with no
    pruning at all — left alone, this grows without bound over the life of
    a project. Backup filenames encode a timestamp
    (`{timestamp}-{hash}{suffix}`), so the oldest N beyond the cap are the
    ones to go; best-effort, a failure here shouldn't fail the save itself."""
    try:
        backups = sorted(backup_dir.iterdir(), key=lambda p: p.name)
        excess = len(backups) - MAX_BACKUPS_PER_DIR
        for old in backups[: max(0, excess)]:
            old.unlink(missing_ok=True)
    except Exception:
        pass


# ---- Undo/redo: a shared, disk-based snapshot stack ----
#
# Both change_graph() (this process) and canvas_app.py's manual drag-save
# path write to the SAME target .grc file, from two separate processes —
# so the undo/redo history has to live on disk too, not as an in-memory
# stack in either process. Mirrors GRC's own native undo (gnuradio.grc.gui's
# StateCache), which is also just export_data()/import_data() snapshots —
# independent confirmation this is the right shape, not just a plausible
# analogy. Each snapshot is a plain numbered .grc file (same serialization
# as a real save), so restoring one is exactly write_flow_graph_atomic's
# own atomic-replace, and reading one back is exactly load_flow_graph.
UNDO_MAX_DEPTH = 50


def _undo_dir(target_path: Path) -> Path:
    # Per-filename (not per-directory, unlike backups/) since this needs a
    # stateful cursor, not just a pile of independently-timestamped files.
    return target_path.parent / ".grc_agent" / (target_path.name + ".undo")


def _read_undo_cursor(undo_dir: Path) -> dict:
    try:
        return json.loads((undo_dir / "cursor.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"index": -1, "count": 0, "hash": None}


def _write_undo_cursor(undo_dir: Path, cursor: dict) -> None:
    (undo_dir / "cursor.json").write_text(json.dumps(cursor), encoding="utf-8")


def _prune_undo_stack(undo_dir: Path, cursor: dict) -> None:
    """Bound depth like _prune_old_backups — but snapshots are addressed by
    contiguous 0-based index (not an independently-sortable timestamp), so
    the oldest ones must be deleted AND everything else renumbered down, or
    the index<->filename mapping breaks. Mutates `cursor` in place."""
    excess = cursor["count"] - UNDO_MAX_DEPTH
    if excess <= 0:
        return
    for i in range(excess):
        (undo_dir / f"{i:05d}.grc").unlink(missing_ok=True)
    for old_index in range(excess, cursor["count"]):
        old_path = undo_dir / f"{old_index:05d}.grc"
        if old_path.exists():
            old_path.rename(undo_dir / f"{old_index - excess:05d}.grc")
    cursor["index"] -= excess
    cursor["count"] -= excess


def push_undo_snapshot(flow_graph: Any, target_path: Path, initial_data: dict = None) -> None:
    """Push the CURRENT (post-edit) state of flow_graph onto target_path's
    undo stack. Called from both change_graph's own success path and
    canvas_app.py's manual drag-save path, so both mutation sources share
    one history. Deduplicates against a genuinely-unchanged state (e.g. a
    selection click that didn't move anything) via content hash. A new push
    after an undo discards the redo branch — standard undo/redo semantics.
    Best-effort: a failure here must never fail the caller's own
    already-committed save, so exceptions are logged, not raised.

    `initial_data` — change_graph's own pre-mutation
    flow_graph.export_data(), taken before any phase runs — seeds a
    baseline entry the first time this is ever called for a file, so undo
    can return all the way to "before the first tracked edit," not just
    between edits. Manual canvas saves have no pre-edit snapshot of their
    own to offer (the in-memory graph is already post-edit by the time
    _do_trigger_reload runs); they rely on a prior change_graph call (or an
    earlier canvas save) having already seeded the baseline.
    """
    try:
        target_path = Path(target_path)
        undo_dir = _undo_dir(target_path)
        undo_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        cursor = _read_undo_cursor(undo_dir)

        current_payload = _serialize_flow_graph(flow_graph)
        current_hash = hashlib.sha256(current_payload.encode()).hexdigest()

        if cursor["count"] == 0 and initial_data is not None:
            from gnuradio.grc.core.io import yaml as _grc_yaml

            baseline_payload = _grc_yaml.dump(initial_data)
            baseline_hash = hashlib.sha256(baseline_payload.encode()).hexdigest()
            (undo_dir / "00000.grc").write_text(baseline_payload, encoding="utf-8")
            cursor = {"index": 0, "count": 1, "hash": baseline_hash}

        if current_hash == cursor["hash"]:
            return  # nothing actually changed since the last tracked state

        for stale in undo_dir.glob("*.grc"):
            try:
                if int(stale.stem) > cursor["index"]:
                    stale.unlink()
            except ValueError:
                continue

        new_index = cursor["index"] + 1
        (undo_dir / f"{new_index:05d}.grc").write_text(current_payload, encoding="utf-8")
        cursor = {"index": new_index, "count": new_index + 1, "hash": current_hash}
        _prune_undo_stack(undo_dir, cursor)
        _write_undo_cursor(undo_dir, cursor)
    except Exception as e:
        print(f"[grc-agent] Failed to push undo snapshot for {target_path}: {e}")


def _apply_undo_redo_snapshot(
    target_path: Path, undo_dir: Path, cursor: dict, new_index: int
) -> dict:
    snapshot_path = undo_dir / f"{new_index:05d}.grc"
    if not snapshot_path.exists():
        return {"ok": False, "message": "Undo history is corrupted or incomplete."}
    payload = snapshot_path.read_text(encoding="utf-8")

    # Same lock file + atomic-replace convention as change_graph's own save
    # and canvas_app.py's drag-save — a reader can never observe a torn file
    # regardless of which of the three writers is active. The cursor
    # read-modify-write must be INSIDE this same critical section too (not
    # just the target-file write): push_undo_snapshot (canvas_app.py's
    # drag-save, and change_graph) always wraps its own cursor.json
    # read+write in this identical lock, so leaving this function's cursor
    # update unlocked let a concurrent drag-save read a stale cursor between
    # this function's file write and its cursor write, then both writers'
    # cursor.json updates race with whichever lands last silently winning.
    lock_path = target_path.parent / ".grc_agent" / (target_path.name + ".lock")
    lock_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with lock_path.open("a", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            cursor = _read_undo_cursor(undo_dir)
            _atomic_write_text(payload, target_path)
            cursor["index"] = new_index
            cursor["hash"] = hashlib.sha256(payload.encode()).hexdigest()
            _write_undo_cursor(undo_dir, cursor)
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    return {
        "ok": True,
        "can_undo": cursor["index"] > 0,
        "can_redo": cursor["index"] < cursor["count"] - 1,
    }


def undo_flowgraph(target_path: Path) -> dict:
    """Move target_path's undo cursor back one step and write that
    snapshot to disk. Does NOT touch any in-memory flow_graph object —
    callers (web.py's /grc/undo) reload from disk afterward, the same way
    grc_reload already does for a canvas-triggered disk change."""
    target_path = Path(target_path)
    undo_dir = _undo_dir(target_path)
    cursor = _read_undo_cursor(undo_dir)
    if cursor["index"] <= 0:
        return {"ok": False, "message": "Nothing to undo."}
    return _apply_undo_redo_snapshot(target_path, undo_dir, cursor, cursor["index"] - 1)


def redo_flowgraph(target_path: Path) -> dict:
    target_path = Path(target_path)
    undo_dir = _undo_dir(target_path)
    cursor = _read_undo_cursor(undo_dir)
    if cursor["index"] >= cursor["count"] - 1:
        return {"ok": False, "message": "Nothing to redo."}
    return _apply_undo_redo_snapshot(target_path, undo_dir, cursor, cursor["index"] + 1)


def undo_status(target_path: Path) -> dict:
    """Cheap read for /grc/status — no lock needed, just reflects the
    cursor file as it currently stands."""
    target_path = Path(target_path)
    undo_dir = _undo_dir(target_path)
    if not undo_dir.exists():
        return {"can_undo": False, "can_redo": False}
    cursor = _read_undo_cursor(undo_dir)
    return {
        "can_undo": cursor["index"] > 0,
        "can_redo": cursor["index"] < cursor["count"] - 1,
    }


def set_param(block: Any, param_key: str, value: str) -> None:
    if param_key not in block.params:
        valid_keys = sorted(block.params.keys())
        raise KeyError(
            f"Param {param_key!r} not in block {block.name!r}. "
            f"Valid param names for this block: {valid_keys}"
        )
    if param_key == "id":
        if str(value) != str(block.params["id"].value):
            value = str(block.params["id"].value)
        block.params[param_key].set_value(str(value))
        return

    raw_value = str(value)
    template = _VARIABLE_TEMPLATE_RE.match(raw_value)
    if template:
        bare = template.group(1)
        raise ValueError(
            f"Invalid value for param {param_key!r} on block {block.name!r}: "
            f"{raw_value!r} is a template literal. Use the bare variable name "
            f"{bare!r} (e.g. {param_key}={bare})."
        )

    param = block.params[param_key]
    if str(getattr(param, "dtype", "") or "") == "enum":
        options = [str(o) for o in (getattr(param, "options", None) or [])]
        labels = [str(o) for o in (getattr(param, "option_labels", None) or [])]
        accepted = set(options) | set(labels)
        if accepted and raw_value not in accepted:
            raise ValueError(
                f"Invalid enum value for param {param_key!r} on block "
                f"{block.name!r}: {raw_value!r} is not one of the valid "
                f"options {options}. Use one of those exact tokens."
            )
    param.set_value(raw_value)


# Conservative estimate of a block's on-canvas footprint, used only to place
# newly-added blocks without overlap — see change_graph's add_blocks phase
# for why this can't be the block's real rendered size (that's GUI-only and
# unavailable to this headless code path).
#
# A per-block estimate derived from counting each param's native `hide`
# attribute (`hide not in ('all', 'part')` is exactly the rule GRC's own
# canvas rendering uses to decide whether a param gets a row — see
# gui/canvas/block.py) was tried and rejected: it's accurate for simple
# blocks, but multi-channel sink/source blocks (e.g. qtgui_time_sink_x) carry
# ~10 near-duplicate per-channel param groups (label1..label10, color1..10,
# etc.) that GRC's canvas dynamically collapses down to however many
# channels are actually connected — a raw hide-attribute count sees all ~60+
# of them as visible regardless. Replicating that collapsing correctly would
# mean hardcoding which params group together and how, per block family —
# exactly the "no hand-picked heuristics" this codebase avoids elsewhere. A
# single, generously-sized constant is the more honest fix: it costs some
# wasted canvas space for simple blocks, in exchange for not silently
# overlapping busier ones (live-reproduced: a Signal Source with 6 visible
# rows — samp_rate/waveform/freq/amp/offset/phase — placed exactly
# BLOCK_FOOTPRINT_H=100 above a newly-added sink rendered tall enough to
# visibly overlap it, since 100 was sized for a near-empty block).
BLOCK_FOOTPRINT_W = 300
BLOCK_FOOTPRINT_H = 220
BLOCK_SPACING = 60


def _rects_overlap(ax: float, ay: float, bx: float, by: float) -> bool:
    """AABB collision check with spacing gap. Coordinates are top-left
    corners; both blocks share the same conservative footprint estimate."""
    gap = BLOCK_SPACING
    return (
        ax < bx + BLOCK_FOOTPRINT_W + gap
        and ax + BLOCK_FOOTPRINT_W + gap > bx
        and ay < by + BLOCK_FOOTPRINT_H + gap
        and ay + BLOCK_FOOTPRINT_H + gap > by
    )


def _compute_ranks(
    flow_graph: Any, new_block_names: set[str], add_connections: list[str] | None
) -> dict[str, int]:
    """Topological rank (layer index, 0 = sources) for every existing block
    plus every new block about to be added, via grandalf's Sugiyama-style
    layer assignment (proper longest-path ranking with cycle breaking) over
    the full topology — existing connections plus the new ones from this
    same batch. Used only to anchor NEW blocks relative to their real
    distance from a neighbor in the existing graph; an existing block's own
    coordinate is never touched, and its computed rank here is read purely
    as context, never used to move it. Grandalf splits disconnected
    subgraphs into independent components (e.g. a variable block with no
    wire connections), each ranked from its own rank-0 root(s)."""
    vertices: dict[str, Any] = {}
    for b in flow_graph.blocks:
        v = GrandalfVertex(b.name)
        v.view = VertexViewer(w=BLOCK_FOOTPRINT_W, h=BLOCK_FOOTPRINT_H)
        vertices[b.name] = v
    for name in new_block_names:
        if name not in vertices:
            v = GrandalfVertex(name)
            v.view = VertexViewer(w=BLOCK_FOOTPRINT_W, h=BLOCK_FOOTPRINT_H)
            vertices[name] = v

    edges = []
    for c in flow_graph.connections:
        src, dst = c.source_block.name, c.sink_block.name
        if src in vertices and dst in vertices:
            edges.append(GrandalfEdge(vertices[src], vertices[dst]))
    for conn_str in add_connections or []:
        p = parse_conn(conn_str)
        if p and p["src_block"] in vertices and p["dst_block"] in vertices:
            edges.append(GrandalfEdge(vertices[p["src_block"]], vertices[p["dst_block"]]))

    ranks: dict[str, int] = {}
    graph = GrandalfGraph(list(vertices.values()), edges)
    for component in graph.C:
        sug = SugiyamaLayout(component)
        try:
            sug.init_all()
        except Exception:
            continue
        for v in component.sV:
            ranks[v.data] = sug.grx[v].rank
    return ranks


def _find_block_placement(
    new_block_name: str,
    occupied: list[tuple[float, float]],
    neighbor_map: dict[str, set[str]],
    block_coords: dict[str, tuple[float, float]],
    bbox: tuple[float, float, float, float],
    ranks: dict[str, int] | None = None,
) -> tuple[float, float]:
    """Find a non-overlapping position for a new block.

    Prioritizes placement near connected neighbors (from the same batch's
    add_connections), anchored by each neighbor's grandalf-computed rank
    distance (see _compute_ranks) rather than always assuming exactly one
    hop to the right — a neighbor several hops downstream in an existing
    chain gets anchored that many grid columns further out, not one, and a
    neighbor at the same rank (e.g. a parallel branch) stays in the same
    column. Falls back to the graph's centroid with no neighbors. Uses a
    spiral grid search to find the nearest empty slot — fills empty space
    rather than always extending to the right.
    """
    grid_w = BLOCK_FOOTPRINT_W + BLOCK_SPACING
    grid_h = BLOCK_FOOTPRINT_H + BLOCK_SPACING

    # 1. Find connected neighbors' coordinates (existing blocks or
    #    already-placed new blocks from the same batch), anchored by rank
    #    distance where available — otherwise one grid step to the right,
    #    matching the previous unconditional assumption.
    neighbor_coords = []
    my_rank = (ranks or {}).get(new_block_name)
    for other in neighbor_map.get(new_block_name, ()):
        if other not in block_coords:
            continue
        ox, oy = block_coords[other]
        other_rank = (ranks or {}).get(other)
        if my_rank is not None and other_rank is not None:
            neighbor_coords.append((ox + (my_rank - other_rank) * grid_w, oy))
        else:
            neighbor_coords.append((ox + grid_w, oy))

    # 2. Compute target point
    if neighbor_coords:
        target_x = sum(c[0] for c in neighbor_coords) / len(neighbor_coords)
        target_y = sum(c[1] for c in neighbor_coords) / len(neighbor_coords)
    elif bbox:
        # No connections — place at graph centroid to fill empty space
        target_x = (bbox[0] + bbox[2]) / 2
        target_y = (bbox[1] + bbox[3]) / 2
    else:
        target_x = 200.0
        target_y = 12.0

    # 3. Snap to grid, clamping to non-negative boundaries
    gx = max(0.0, round(target_x / grid_w) * grid_w)
    gy = max(0.0, round(target_y / grid_h) * grid_h)

    # 4. Spiral search: ring 0 is just the target, ring N is the perimeter
    #    of cells at Chebyshev distance N. Expands outward until it finds
    #    a non-overlapping slot.
    if gx >= 0 and gy >= 0 and not any(_rects_overlap(gx, gy, ox, oy) for ox, oy in occupied):
        return (gx, gy)

    for ring in range(1, 60):
        for dx in range(-ring, ring + 1):
            for dy in range(-ring, ring + 1):
                if max(abs(dx), abs(dy)) != ring:
                    continue
                cx = gx + dx * grid_w
                cy = gy + dy * grid_h
                if cx < 0 or cy < 0:
                    continue
                if not any(_rects_overlap(cx, cy, ox, oy) for ox, oy in occupied):
                    return (cx, cy)

    # 5. Fallback: place to the right of everything, ensuring non-negative coordinates
    fallback_x = max(o[0] for o in occupied) + grid_w if occupied else 200.0
    return (max(0.0, fallback_x), max(0.0, gy))


def change_graph(
    flow_graph: Any,
    add_blocks: list[dict] = None,
    remove_blocks: list[str] = None,
    update_params: list[dict] = None,
    update_states: list[dict] = None,
    add_connections: list[str] = None,
    remove_connections: list[str] = None,
    force: bool = False,
) -> dict[str, Any]:

    if not any(
        [
            add_blocks,
            remove_blocks,
            update_params,
            update_states,
            add_connections,
            remove_connections,
        ]
    ):
        return {
            "ok": False,
            "error_type": "invalid_request",
            "errors": [
                {
                    "code": "invalid_request",
                    "message": (
                        "change_graph requires at least one non-empty operation "
                        "array (add_blocks, remove_blocks, update_params, "
                        "update_states, add_connections, or remove_connections)."
                    ),
                }
            ],
        }

    initial_data = flow_graph.export_data()
    errors = []
    new_block_names = set()
    if add_blocks:
        new_block_names = {str(item.get("instance_name", "")).strip() for item in add_blocks}

    try:
        # Phase 1: remove_connections
        if remove_connections:
            for conn_str in remove_connections:
                p = parse_conn(conn_str)
                if not p:
                    errors.append(
                        {
                            "code": "invalid_connection_format",
                            "message": f"Unparseable connection format: {conn_str}",
                        }
                    )
                    continue
                found = False
                for connection in list(flow_graph.connections):
                    if (
                        connection.source_block.name == p["src_block"]
                        and str(connection.source_port.key) == str(p["src_port"])
                        and connection.sink_block.name == p["dst_block"]
                        and str(connection.sink_port.key) == str(p["dst_port"])
                    ):
                        flow_graph.remove_element(connection)
                        found = True
                        break
                if not found:
                    errors.append(
                        {
                            "code": "connection_not_found",
                            "message": f"Connection not found: {conn_str}",
                        }
                    )

        # Phase 2: remove_blocks
        if remove_blocks:
            for name in remove_blocks:
                try:
                    block = flow_graph.get_block(name)
                    flow_graph.remove_element(block)
                except Exception as e:
                    errors.append(
                        {
                            "code": "remove_block_failed",
                            "message": f"Failed to remove block {name!r}: {e}",
                        }
                    )

        # Phase 3: add_blocks
        if add_blocks:
            # GNU Radio's own headless block-creation API never sets a
            # coordinate (that's a GUI-layer-only default, applied only once
            # the file is next opened in a canvas) — added blocks otherwise
            # all land on top of each other at (0, 0) (confirmed live: 3
            # blocks added in one batch were indistinguishable in the
            # canvas).
            #
            # GRC's own GUI placement isn't reusable here: its "add block"
            # action (gui/canvas/flowgraph.py add_new_block) just drops the
            # block at a random point inside the current scroll viewport
            # with no collision check at all, and its one genuine anti-
            # overlap logic (paste_from_clipboard's grid-aligned nudge loop)
            # depends on gui.Constants.CANVAS_GRID_SIZE, which pulls in
            # gi.repository — a GTK dependency this headless, no-canvas code
            # path must not take on. A real block's pixel size is likewise
            # GUI-only (computed from Pango text metrics at draw time in
            # gui/canvas/block.py); core.Block carries no width/height at
            # all, so no code path — native or otherwise — can know a
            # block's true footprint headlessly.
            #
            # Placement strategy: for each new block, find a target point
            # near its connected neighbors (from add_connections in the same
            # batch), or the graph's centroid if it has no connections.
            # Then spiral-search outward on a grid to find the nearest
            # non-overlapping slot. This fills empty space instead of always
            # extending to the right, and keeps connected blocks near each
            # other so wires stay short. Must never need agent or user
            # input: the agent's own context has block coordinates filtered
            # out entirely, so positioning has to be fully self-contained.
            occupied: list[tuple[float, float]] = []
            block_coords: dict[str, tuple[float, float]] = {}
            for b in flow_graph.blocks:
                coord = b.states.get("coordinate")
                if isinstance(coord, (list, tuple)) and len(coord) == 2:
                    c = (float(coord[0]), float(coord[1]))
                    occupied.append(c)
                    block_coords[b.name] = c

            # Pre-parse add_connections to build a neighbor map so blocks
            # connecting to each other (or to existing blocks) get placed
            # near their neighbors.
            neighbor_map: dict[str, set[str]] = {}
            if add_connections:
                for conn_str in add_connections:
                    p = parse_conn(conn_str)
                    if p:
                        neighbor_map.setdefault(p["src_block"], set()).add(p["dst_block"])
                        neighbor_map.setdefault(p["dst_block"], set()).add(p["src_block"])

            # Compute graph bounding box for centroid fallback
            if occupied:
                bbox = (
                    min(c[0] for c in occupied),
                    min(c[1] for c in occupied),
                    max(c[0] for c in occupied),
                    max(c[1] for c in occupied),
                )
            else:
                bbox = ()

            ranks = _compute_ranks(flow_graph, new_block_names, add_connections)

            for item in add_blocks:
                block_id = item["block_id"]
                instance_name = item["instance_name"]

                if any(b.name == instance_name for b in flow_graph.blocks):
                    errors.append(
                        {
                            "code": "duplicate_block_name",
                            "message": f"Block instance name {instance_name!r} already exists in the flowgraph.",
                        }
                    )
                    continue

                block = flow_graph.new_block(block_id)
                if block is None:
                    errors.append(
                        {
                            "code": "block_type_not_found",
                            "message": f"Block type {block_id!r} not found in catalog",
                        }
                    )
                    continue

                placement = _find_block_placement(
                    instance_name, occupied, neighbor_map, block_coords, bbox, ranks
                )
                occupied.append(placement)
                block_coords[instance_name] = placement
                block.states["coordinate"] = list(placement)
                block.params["id"].set_value(str(instance_name))
                flow_graph.rewrite()

                for k, v in (item.get("params") or {}).items():
                    if v == "auto":
                        block.params[k].set_value("auto")
                        continue
                    try:
                        set_param(block, k, v)
                    except Exception as e:
                        errors.append(
                            {
                                "code": "set_param_failed",
                                "message": f"Failed to set param {k!r} on block {instance_name!r}: {e}",
                            }
                        )
                if "state" in item:
                    try:
                        set_block_state(block, item["state"])
                    except Exception as e:
                        errors.append(
                            {
                                "code": "set_state_failed",
                                "message": f"Failed to set state {item['state']!r} on block {instance_name!r}: {e}",
                            }
                        )

        # Phase 4: update_params
        if update_params:
            for item in update_params:
                name = item["instance_name"]
                try:
                    block = flow_graph.get_block(name)
                    for k, v in (item.get("params") or {}).items():
                        if v == "auto":
                            block.params[k].set_value("auto")
                            continue
                        if k == "id":
                            continue
                        try:
                            set_param(block, k, v)
                        except Exception as e:
                            errors.append(
                                {
                                    "code": "set_param_failed",
                                    "message": f"Failed to set param {k!r} on block {name!r}: {e}",
                                }
                            )
                except Exception as e:
                    errors.append(
                        {
                            "code": "update_params_failed",
                            "message": f"Failed to locate block {name!r} to update params: {e}",
                        }
                    )

        # Phase 5: auto_resolve_types
        for b in flow_graph.blocks:
            controlling = type_controlling_params(b.key)
            for k, p in b.params.items():
                # Restricted to actual type-controlling params (native-
                # derived: dtype == "enum" AND textually referenced in a
                # port's dtype template) — some blocks have unrelated,
                # non-type params whose own schema default happens to be the
                # literal string "auto" too (e.g. blocks_throttle2's numeric
                # "limit"), which have no connected ports to resolve from at
                # all and must not be routed through dtype resolution.
                if k in controlling and str(p.value) == "auto":
                    is_add = b.name in new_block_names
                    if is_add:
                        is_connected = False
                        for conn_str in add_connections or []:
                            parsed = parse_conn(conn_str)
                            if parsed and (
                                parsed["src_block"] == b.name or parsed["dst_block"] == b.name
                            ):
                                is_connected = True
                                break
                        if not is_connected:
                            continue
                    try:
                        resolved = resolve_auto(
                            flow_graph,
                            b.name,
                            k,
                            add_connections=add_connections or [],
                            new_block_names=new_block_names,
                            is_add_phase=is_add,
                            add_blocks=add_blocks,
                            update_params=update_params,
                        )
                        if resolved:
                            p.set_value(resolved)
                    except Exception as e:
                        errors.append(
                            {
                                "code": "auto_resolve_failed",
                                "message": f"Failed to auto-resolve type parameter {k!r} on block {b.name!r}: {e}",
                            }
                        )

        # Phase 6: update_states
        if update_states:
            for item in update_states:
                name = item["instance_name"]
                try:
                    block = flow_graph.get_block(name)
                    set_block_state(block, item["state"])
                except Exception as e:
                    errors.append(
                        {
                            "code": "update_states_failed",
                            "message": f"Failed to update state on block {name!r}: {e}",
                        }
                    )

        # Phase 7: add_connections
        if add_connections:
            for conn_str in add_connections:
                p = parse_conn(conn_str)
                if not p:
                    errors.append(
                        {
                            "code": "invalid_connection_format",
                            "message": f"Unparseable connection format: {conn_str}",
                        }
                    )
                    continue
                try:
                    src_port = _find_port(flow_graph, p["src_block"], p["src_port"], kind="source")
                    dst_port = _find_port(flow_graph, p["dst_block"], p["dst_port"], kind="sink")
                    flow_graph.connect(src_port, dst_port)
                except Exception as e:
                    errors.append(
                        {
                            "code": "add_connection_failed",
                            "message": f"Failed to connect {conn_str}: {e}",
                        }
                    )

        flow_graph.rewrite()

    except Exception as exc:
        flow_graph.import_data(initial_data)
        flow_graph.rewrite()
        return {"ok": False, "errors": [{"code": "mutation_failed", "message": str(exc)}]}

    if errors:
        flow_graph.import_data(initial_data)
        flow_graph.rewrite()
        return {"ok": False, "errors": errors}

    # See inspect_graph's identical call for why this is required: without
    # it, is_valid() reports "valid" regardless of actual state (confirmed
    # live: removing a required connection without force=True was silently
    # accepted, leaving a genuinely broken graph persisted to disk).
    flow_graph.validate()
    valid = bool(flow_graph.is_valid())
    if not valid and not force:
        validation_errors = []
        for elem, msg in flow_graph.iter_error_messages():
            parent = getattr(elem, "parent_block", None)
            if parent is not None and parent is not elem:
                validation_errors.append(
                    {"code": "gnu_validation", "message": f"{parent.name}: {elem}: {msg}"}
                )
            else:
                validation_errors.append({"code": "gnu_validation", "message": f"{elem}: {msg}"})
        flow_graph.import_data(initial_data)
        flow_graph.rewrite()
        return {
            "ok": False,
            "error_type": "validation_failed",
            "errors": validation_errors
            if validation_errors
            else [{"code": "gnu_validation", "message": "GRC validation failed."}],
        }

    # Write atomically with lock and backup
    try:
        original = Path(flow_graph.grc_file_path)
        # resolve() follows symlinks, so the symlink check must run on the
        # unresolved path — checking it after resolve() is always False and
        # silently defeats the guard.
        if original.is_symlink():
            raise OSError(f"Refusing to save through symlink: {original}")
        target_path = original.resolve()
        if target_path.exists():
            if target_path.stat().st_nlink > 1:
                raise OSError(f"Refusing to save hard-linked graph file: {target_path}")

        if target_path.exists():
            backup_dir = target_path.parent / ".grc_agent" / "backups"
            backup_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            with open(target_path, "rb") as f:
                old_hash = hashlib.sha256(f.read()).hexdigest()
            timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
            backup_path = backup_dir / f"{timestamp}-{old_hash[:16]}{target_path.suffix}"
            shutil.copy2(target_path, backup_path)
            _prune_old_backups(backup_dir)

        lock_path = target_path.parent / ".grc_agent" / (target_path.name + ".lock")
        lock_path.parent.mkdir(mode=0o700, exist_ok=True)

        with lock_path.open("a", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                write_flow_graph_atomic(flow_graph, target_path)
                push_undo_snapshot(flow_graph, target_path, initial_data)
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    except Exception as exc:
        flow_graph.import_data(initial_data)
        flow_graph.rewrite()
        return {
            "ok": False,
            "error_type": "save_failed",
            "errors": [
                {"code": "save_failed", "message": f"Failed to commit changes atomically: {exc}"}
            ],
        }

    return {"ok": True}


def get_db_and_model(domain: str) -> tuple[str, str]:
    from grc_agent.settings import get_env_value, load_settings

    cfg = load_settings()
    provider = cfg.get("provider", "ollama")

    if provider == "openrouter":
        model = get_env_value("OPENROUTER_EMBEDDING_MODEL") or os.getenv(
            "OPENROUTER_EMBEDDING_MODEL", "perplexity/pplx-embed-v1-0.6b"
        )
        db_name = f"{domain}_openrouter.db"
    else:
        # ollama and ollama_cloud both use local Ollama for embeddings
        # (Ollama Cloud's API doesn't expose /v1/embeddings)
        model = get_env_value("OLLAMA_EMBEDDING_MODEL") or os.getenv(
            "OLLAMA_EMBEDDING_MODEL", "embeddinggemma:latest"
        )
        db_name = f"{domain}_ollama.db"

    db_path = vectors_dir() / db_name
    return str(db_path), model


def _embed_endpoint() -> tuple[str, str]:
    """Shared base_url/api_key selection for both query- and document-side
    embedding calls."""
    from grc_agent.settings import get_env_value, load_settings

    cfg = load_settings()
    provider = cfg.get("provider", "ollama")

    if provider == "openrouter":
        key = get_env_value("OPENROUTER_API_KEY") or os.getenv("OPENROUTER_API_KEY", "")
        return "https://openrouter.ai/api/v1", key
    # ollama and ollama_cloud both use local Ollama for embeddings
    return "http://localhost:11434/v1", "not-needed"


def _embed(model: str, input_text: str) -> list[float]:
    """Shared embeddings.create() call for both query- and document-side
    embedding. Raises a clear, actionable error on connection failure — the
    bare "Connection error." from openai's client gives no hint that a local
    Ollama server (not necessarily the active chat provider) is what's
    actually being reached for embeddings."""
    base_url, api_key = _embed_endpoint()
    client = OpenAI(base_url=base_url, api_key=api_key)
    try:
        response = client.embeddings.create(model=model, input=input_text, encoding_format="float")
    except APIConnectionError as exc:
        hint = (
            f"Is `ollama serve` running locally, with `ollama pull {model}` done?"
            if "localhost" in base_url
            else "Check OPENROUTER_API_KEY and network connectivity."
        )
        raise RuntimeError(f"Cannot reach the embeddings endpoint at {base_url}. {hint}") from exc
    return response.data[0].embedding


def embed_query(query: str) -> list[float]:
    from grc_agent.settings import load_settings

    cfg = load_settings()
    provider = cfg.get("provider", "ollama")
    use_prefix = provider != "openrouter"

    _, model = get_db_and_model("catalog")
    return _embed(model, ("task: search result | query: " + query) if use_prefix else query)


_DOCUMENT_PREFIX = "task: search result | document: "
EMBED_MAX_WORDS = 900


def _cap_words(text: str, max_words: int) -> str:
    """Cap document text at a maximum word count.
    Used strictly to satisfy hard input token constraints of embedding model APIs
    during database ingestion (ingest_catalog, ingest_docs) to prevent API failures.
    """
    words = text.split()
    return text if len(words) <= max_words else " ".join(words[:max_words])


def embed_document(text: str, model: str) -> list[float]:
    """Document-side counterpart to embed_query() — same backend-conditional
    prefix convention, used only at ingestion time."""
    from grc_agent.settings import load_settings

    cfg = load_settings()
    provider = cfg.get("provider", "ollama")
    use_prefix = provider != "openrouter"

    body = text if not use_prefix else _DOCUMENT_PREFIX + text
    return _embed(model, body)


_EMBEDDING_DIM_CACHE: dict[str, int] = {}
_CORPUS_VERSION_CACHE: dict[str, str] = {}


# Exposed to the dashboard via /grc/status so the UI can show a "Building
# knowledge database..." banner instead of an indefinite hang during the
# first query_knowledge call (or after a provider switch that changes the
# embedding model). Set by _ensure_db_built, read by web.py's grc_status.
_rag_building: dict[str, str | None] = {"domain": None, "status": None}


def _get_embedding_dim(model: str) -> int:
    """Cache the embedding dimension for a model so we don't pay for a real
    embedding API call on every single vector query just to verify the cached
    DB still matches the current model."""
    if model not in _EMBEDDING_DIM_CACHE:
        _EMBEDDING_DIM_CACHE[model] = len(embed_document("test", model))
    return _EMBEDDING_DIM_CACHE[model]


def _corpus_version(domain: str) -> str:
    """A cheap identity for the domain's underlying source data, independent
    of the embedding model — GNU Radio's own version string for the catalog
    (its block library changes across GNU Radio versions), a content hash of
    the docs corpus for docs (its files change across grc-agent releases).
    Without this, a cached DB that still matches on embedding_model alone
    would silently keep serving stale results forever after a GNU Radio
    upgrade or a docs-corpus update, with no error or indication anything's
    wrong. Cached per-process: neither changes during a single run, and
    re-hashing ~100 markdown files on every query would be wasteful."""
    if domain in _CORPUS_VERSION_CACHE:
        return _CORPUS_VERSION_CACHE[domain]

    if domain == "catalog":
        from gnuradio import gr

        version = gr.version()
    else:
        from grc_agent._paths import docs_dir

        h = hashlib.sha256()
        for p in sorted(docs_dir().glob("*.md")):
            h.update(p.name.encode())
            h.update(p.read_bytes())
        version = h.hexdigest()[:16]

    _CORPUS_VERSION_CACHE[domain] = version
    return version


def _ensure_db_built(domain: str, db_path: str, model: str) -> None:
    global _rag_building
    if os.path.exists(db_path):
        # Check vector dimension, embedding model name, and corpus version
        # (all stored in _db_meta). Any mismatch triggers a rebuild —
        # different models produce different embedding spaces, and a changed
        # corpus/block-library would otherwise go stale silently forever.
        try:
            conn = sqlite3.connect(db_path)
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            sql_row = conn.execute(
                f"SELECT sql FROM sqlite_master WHERE name = '{domain}_idx'"
            ).fetchone()
            meta: dict[str, str] = {}
            try:
                for key, value in conn.execute("SELECT key, value FROM _db_meta"):
                    meta[key] = value
            except sqlite3.OperationalError:
                pass
            conn.close()

            if sql_row and sql_row[0]:
                match = re.search(r"float\[(\d+)\]", sql_row[0])
                if match:
                    db_dim = int(match.group(1))
                    model_dim = _get_embedding_dim(model)
                    reason = None
                    if model_dim != db_dim:
                        reason = f"dimension mismatch (DB has {db_dim}, model has {model_dim})"
                    elif not meta:
                        reason = "no metadata recorded"
                    elif meta.get("embedding_model") != model:
                        reason = (
                            f"embedding model changed (was '{meta.get('embedding_model')}', "
                            f"now '{model}')"
                        )
                    elif meta.get("corpus_version") != _corpus_version(domain):
                        reason = "source data changed since this DB was built"

                    if reason:
                        print(f"[grc-agent] {domain} vector DB stale: {reason}. Rebuilding...")
                        os.remove(db_path)
                    else:
                        return
                else:
                    os.remove(db_path)
            else:
                os.remove(db_path)
        except Exception:
            try:
                os.remove(db_path)
            except Exception:
                pass

    _rag_building["domain"] = domain
    _rag_building["status"] = "building"
    try:
        print(
            f"[grc-agent] {domain} vector DB not found or stale — building it now "
            f"(first run only, may take a few minutes)..."
        )
        from grc_agent import ingest

        if domain == "catalog":
            ingest.ingest_catalog(db_path, model)
        else:
            ingest.ingest_docs(db_path, model)
        print(f"[grc-agent] {domain} vector DB build complete: {db_path}")
        _rag_building["domain"] = domain
        _rag_building["status"] = "ready"
    except Exception:
        _rag_building["domain"] = domain
        _rag_building["status"] = "failed"
        raise


def query_catalog(query: str, limit: int = 5) -> dict[str, Any]:
    q = " ".join(str(query).split())
    if not q:
        return {"ok": False, "results": [], "message": "query must be non-empty"}

    try:
        query_vec = embed_query(q)
    except Exception as exc:
        return {"ok": False, "results": [], "message": f"Embedding failed: {exc}"}

    db_path, model = get_db_and_model("catalog")
    try:
        _ensure_db_built("catalog", db_path, model)
    except Exception as exc:
        return {"ok": False, "results": [], "message": f"Catalog DB build failed: {exc}"}
    if not os.path.exists(db_path):
        return {"ok": False, "results": [], "message": f"Catalog DB not found at: {db_path}"}

    conn = sqlite3.connect(db_path)
    try:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.row_factory = sqlite3.Row

        vec_rows = conn.execute(
            "SELECT rowid, distance FROM catalog_idx WHERE embedding MATCH ? AND k = ? ORDER BY distance",
            (sqlite_vec.serialize_float32(query_vec), limit + 1),
        ).fetchall()

        results = []
        for row in vec_rows:
            rowid = row["rowid"]
            distance = row["distance"]
            chunk = conn.execute(
                "SELECT block_id FROM catalog_chunks WHERE rowid = ?",
                (rowid,),
            ).fetchone()
            if not chunk:
                continue

            block_id = chunk["block_id"]
            rendered = render_catalog_block(block_id, distance)
            if rendered:
                results.append(rendered)

            if len(results) >= limit:
                break

        return {
            "ok": True,
            "query": q,
            "results": results,
            "output_truncated": len(vec_rows) > limit,
        }
    finally:
        conn.close()


def render_catalog_block(block_id: str, distance: float) -> dict[str, Any]:
    platform = get_platform()
    fg = platform.make_flow_graph()
    try:
        b = fg.new_block(block_id)
    except KeyError:
        return None
    fg.rewrite()

    params = {}
    type_controlling = type_controlling_params(block_id)

    for k, p in b.params.items():
        if keep_param(k, p, b, mode="details"):
            dtype = getattr(p, "dtype", "") or "raw"
            default = getattr(p, "default", "") or ""

            cleaned_default = default
            if cleaned_default.startswith("${") and cleaned_default.endswith("}"):
                cleaned_default = cleaned_default[2:-1].strip()
            if not cleaned_default and k in type_controlling:
                cleaned_default = "auto"

            opts = getattr(p, "options", None)
            if dtype == "enum" and opts:
                opt_keys = [str(o) for o in opts]
                if set(opt_keys) == {"True", "False"}:
                    params[k] = f"[bool]={cleaned_default}"
                else:
                    params[k] = f"enum=[{','.join(opt_keys)}]={cleaned_default}"
            else:
                params[k] = f"[{dtype}]={cleaned_default}"

    inputs = []
    for p in b.active_sinks:
        inputs.append(
            {
                "port_id": str(p.key),
                "dtype": str(getattr(p, "dtype", "")),
                "domain": str(getattr(p, "domain", "") or "stream"),
            }
        )
    outputs = []
    for p in b.active_sources:
        outputs.append(
            {
                "port_id": str(p.key),
                "dtype": str(getattr(p, "dtype", "")),
                "domain": str(getattr(p, "domain", "") or "stream"),
            }
        )

    return {
        "block_id": block_id,
        "label": getattr(b, "label", block_id),
        "category": " > ".join(b.category) if isinstance(b.category, list) else str(b.category),
        "params": params,
        "inputs": inputs,
        "outputs": outputs,
        "distance": round(distance, 3),
    }


def query_docs(query: str, limit: int = 5) -> dict[str, Any]:
    q = " ".join(str(query).split())
    if not q:
        return {"ok": False, "answer": "", "message": "query must be non-empty"}

    try:
        query_vec = embed_query(q)
    except Exception as exc:
        return {"ok": False, "answer": "", "message": f"Embedding failed: {exc}"}

    db_path, model = get_db_and_model("docs")
    try:
        _ensure_db_built("docs", db_path, model)
    except Exception as exc:
        return {"ok": False, "answer": "", "message": f"Docs DB build failed: {exc}"}
    if not os.path.exists(db_path):
        return {"ok": False, "answer": "", "message": f"Docs DB not found at: {db_path}"}

    conn = sqlite3.connect(db_path)
    try:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.row_factory = sqlite3.Row

        vec_rows = conn.execute(
            "SELECT rowid, distance FROM docs_idx WHERE embedding MATCH ? AND k = ? ORDER BY distance",
            (sqlite_vec.serialize_float32(query_vec), limit),
        ).fetchall()

        chunks = []
        for row in vec_rows:
            rowid = row["rowid"]
            chunk = conn.execute(
                "SELECT payload FROM docs_chunks WHERE rowid = ?",
                (rowid,),
            ).fetchone()
            if chunk:
                chunks.append(chunk["payload"])

        answer = "\n\n---\n\n".join(chunks)
        return {"ok": True, "query": q, "answer": answer}
    finally:
        conn.close()


def lite_web_search(query: str) -> str:
    """Local web-search fallback for pydantic-ai's provider-adaptive
    ``WebSearch`` capability on providers without native search (Ollama).

    DuckDuckGo's primary endpoint silently empties responses for the standard
    ``ddgs``/``duckduckgo_search`` client (and for pydantic-ai's own built-in
    duckduckgo fallback, which calls the same ``DDGS().text()``), returning zero
    results for every query including controls. ``lite.duckduckgo.com`` is the
    one DuckDuckGo surface that still returns real results, so this scrapes it.

    Raw snippets are returned verbatim for the model to ground on directly — no
    secondary synthesis call, no clipping. Network errors propagate so a backend
    failure surfaces honestly instead of being masked as "no results" (the exact
    bug that made the previous ``web_search`` silently return nothing).
    """
    from urllib.parse import parse_qs, urlparse

    import httpx
    from bs4 import BeautifulSoup

    response = httpx.get(
        "https://lite.duckduckgo.com/lite/",
        params={"q": query},
        headers={
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
            )
        },
        timeout=15.0,
        follow_redirects=True,
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    links = soup.select("a.result-link")
    snippets = soup.select("td.result-snippet")

    results = []
    for anchor, snippet in zip(links, snippets, strict=False):
        uddg = parse_qs(urlparse(anchor.get("href", "")).query).get("uddg")
        url = uddg[0] if uddg else anchor.get("href", "")
        results.append(f"{anchor.get_text(strip=True)}\n{url}\n{snippet.get_text(strip=True)}")

    if not results:
        return f"No web results found for: {query}"
    return "\n\n".join(results)
