import contextlib
import fcntl
import functools
import hashlib
import os
import re
import shutil
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

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


def set_blocks_panel_visibility(app: Any, visible: bool) -> bool:
    """Show/hide GRC's native Block Library panel. Idempotent: a no-op if
    already in the requested state, since the underlying GTK action
    (TOGGLE_BLOCKS_WINDOW) only flips whatever the current state is — see
    gnuradio.grc.gui.Application's handler for that action."""
    from gnuradio.grc.gui import Actions

    action = Actions.TOGGLE_BLOCKS_WINDOW
    try:
        if bool(action.get_active()) != bool(visible):
            app._handle_action(action)
    except Exception as e:
        print(f"Failed to set Block Library panel visibility via action {action}: {e}")
    return bool(action.get_active())


def get_blocks_panel_visibility() -> bool:
    """Read GRC's native Block Library panel's current visibility state."""
    from gnuradio.grc.gui import Actions

    return bool(Actions.TOGGLE_BLOCKS_WINDOW.get_active())


def register_execution_messenger(callback: Callable[[str], None]) -> None:
    """Register a callback to receive every message GRC sends to its native
    console panel (see gnuradio.grc.core.Messages). Used to detect flow
    graph execution failures without a dedicated log-scraping mechanism."""
    from gnuradio.grc.core import Messages

    Messages.register_messenger(callback)


def flow_graph_content_hash(flow_graph: Any) -> str:
    """Hash of what write_flow_graph_atomic would currently write for this
    flow_graph — directly comparable to a hash of the on-disk file's raw
    bytes (e.g. native_canvas.py's `_sha256_file`/`last_disk_hash`), since it's
    the exact same serialization. Used to detect in-memory edits that
    haven't reached disk yet (a safety net for GTK-native interactions that
    don't go through a specific, hooked signal — see native_canvas.py)."""
    return hashlib.sha256(_serialize_flow_graph(flow_graph).encode()).hexdigest()


def _serialize_flow_graph(flow_graph: Any) -> str:
    from gnuradio.grc.core.io import yaml as _grc_yaml

    return _grc_yaml.dump(flow_graph.export_data())


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


_DTYPE_CANON_CACHE: dict[str, str] | None = None


def _canonical_dtype(token: str) -> str:
    """Resolve a dtype token (canonical core type or alias) to its canonical
    core type using GNU Radio's own ``Constants.ALIASES_OF`` — not a hand-
    maintained alias table, which had drifted (a bogus ``u8`` entry that maps
    to no real GNU Radio type, and missing ``sc16`` / ``s8`` / ``sc8``).

    Core types map to themselves; recognized aliases map to their core;
    unrecognized tokens pass through unchanged so an explicit value is never
    silently rewritten (feeding straight into the silent-reset mechanism).
    """
    global _DTYPE_CANON_CACHE
    if _DTYPE_CANON_CACHE is None:
        from gnuradio.grc.core import Constants

        core_types = ("complex", "float", "int", "short", "byte")
        cache = {c: c for c in core_types}
        for core in core_types:
            for alias in Constants.ALIASES_OF.get(core, ()):
                cache[alias] = core
        _DTYPE_CANON_CACHE = cache
    return _DTYPE_CANON_CACHE.get(token, token)


def resolve_auto(  # noqa: C901
    flow_graph: Any,
    block_name: str,
    param_key: str,
    add_connections: list[str] | None = None,
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
                    dtype_str = _canonical_dtype(other_type_val)
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


def keep_param(  # noqa: C901
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
        return bool(value != default or is_structural_enum)

    if value != default:
        return True
    return bool(variable_names and any(tok in variable_names for tok in _IDENTIFIER_RE.findall(value)))


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


def inspect_graph(  # noqa: C901
    flow_graph: Any, targets: list[str] | None = None, view: str = "overview"
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


def set_param(block: Any, param_key: str, value: str) -> None:
    if param_key not in block.params:
        valid_keys = sorted(block.params.keys())
        raise KeyError(
            f"Param {param_key!r} not in block {block.name!r}. "
            f"Valid param names for this block: {valid_keys}"
        )
    if param_key == "id":
        if str(value) != str(block.params["id"].value):
            raise ValueError(
                f"Cannot rename block {block.name!r} via param 'id': block "
                f"identity is fixed at creation. Attempted to change id from "
                f"{block.params['id'].value!r} to {value!r}."
            )
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
    if raw_value == "auto":
        # change_graph's own sentinel for deferred dtype resolution (Phase
        # 5) — GNU Radio does not define 'auto' as a real option value on
        # type-controlling enums, so it must bypass enum validation below
        # rather than be rejected as invalid.
        param.set_value(raw_value)
        return
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


def change_graph(  # noqa: C901
    flow_graph: Any,
    add_blocks: list[dict] | None = None,
    remove_blocks: list[str] | None = None,
    update_params: list[dict] | None = None,
    update_states: list[dict] | None = None,
    add_connections: list[str] | None = None,
    remove_connections: list[str] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    from grc_agent.adapter.layout import _compute_ranks, _find_block_placement
    from grc_agent.adapter.snapshots import _prune_old_backups, push_undo_snapshot

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

        # Snapshot every connection object that legitimately survives the
        # caller's own deliberate removals (Phase 1/2), before any phase or
        # rewrite() call that could have side effects on ports. Compared
        # against the post-final-rewrite state below to catch ANY connection
        # — pre-existing or newly made in this batch — that a block's own
        # rewrite (e.g. an epy_block reparsing changed source) silently
        # disconnects as a side effect of replacing a port object, including
        # via the conditional Phase-5 rewrite below, not just the final one.
        connections_before_rewrites = set(flow_graph.connections)

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

                try:
                    flow_graph.get_block(instance_name)
                except KeyError:
                    pass
                else:
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

                for k, v in (item.get("params") or {}).items():
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
                            # A brand-new block with type='auto' and no
                            # connection in this batch has nothing to resolve
                            # from. Failing loudly here (the batch rolls back
                            # via the `if errors:` gate below) instead of
                            # silently letting rewrite() reset it to GNU Radio's
                            # arbitrary schema default.
                            errors.append(
                                {
                                    "code": "auto_resolve_failed",
                                    "message": (
                                        f"Block {b.name!r} has type parameter {k!r} set to "
                                        "'auto' but no connection in this batch to resolve it "
                                        "from. Set an explicit type value, or connect it to an "
                                        "already-typed block."
                                    ),
                                }
                            )
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

        if add_blocks:
            flow_graph.rewrite()

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
        made_connections = []
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
                    connection = flow_graph.connect(src_port, dst_port)
                    made_connections.append((conn_str, connection))
                except Exception as e:
                    # Enrich with port dtype details so the model can diagnose
                    # mismatches (e.g. complex source → float sink) and decide
                    # whether to split the batch, insert a converter, or change
                    # a type param — instead of reflexively re-batching.
                    detail = str(e)
                    if src_port is not None and dst_port is not None:
                        with contextlib.suppress(Exception):
                            detail += (
                                f" (source dtype={getattr(src_port, 'dtype', '?')}, "
                                f"sink dtype={getattr(dst_port, 'dtype', '?')})"
                            )
                    errors.append(
                        {
                            "code": "add_connection_failed",
                            "message": f"Failed to connect {conn_str}: {detail}",
                        }
                    )

        flow_graph.rewrite()

        # A block's own rewrite (e.g. an epy_block reparsing changed
        # _source_code) can replace one of its ports as a side effect,
        # silently disconnecting anything attached to the old port object —
        # a pre-existing connection untouched by this batch, or one Phase 7
        # just made, if that block wasn't also in add_blocks (the only thing
        # that triggers the earlier, Phase-5 rewrite). Verified live: with
        # force=True this would otherwise return ok=true while a connection
        # is silently absent — for BOTH a pre-existing connection dropped by
        # an update_params-only batch (no add_connections at all, so nothing
        # upstream of this point would have tracked it) and a same-batch
        # add_connections drop. Checked unconditionally, not just under
        # `not force` — a connection vanishing without a word is never
        # acceptable, force or not; force only bypasses GNU Radio's own
        # general validity opinion, not this.
        #
        # Compares actual Connection objects (via set membership/identity),
        # not (block_name, port_key) string tuples: GNU Radio can rekey a
        # port in place (Port.rewrite() sets self.key = self.name — same
        # object — whenever a stream/vector port's dtype becomes "message",
        # e.g. a pad_sink reconfigured to type='message'), which would make
        # a string-tuple comparison false-positive on a connection that
        # never actually dropped. Object identity is immune to that.
        expected_connections = connections_before_rewrites | {c for _, c in made_connections}
        actual_connections = set(flow_graph.connections)
        dropped = expected_connections - actual_connections
        if dropped:
            conn_str_by_connection = {c: s for s, c in made_connections}
            for connection in dropped:
                label = conn_str_by_connection.get(connection)
                if label is None:
                    label = (
                        f"{connection.source_block.name}:{connection.source_port.key}"
                        f"->{connection.sink_block.name}:{connection.sink_port.key}"
                    )
                errors.append(
                    {
                        "code": "connection_silently_dropped",
                        "message": (
                            f"Connection {label!r} no longer exists after this batch "
                            "finished — a block's own code/port regeneration (e.g. an "
                            "epy_block's _source_code change) likely replaced the port "
                            "it was attached to. Change the block's code/ports in its "
                            "own change_graph call first, confirm the new ports via "
                            "inspect_graph, then add/re-add this connection in a "
                            "follow-up call."
                        ),
                    }
                )

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
    try:
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
    except Exception as exc:
        # The validation gate itself raised (rather than populating an error
        # list). The phases above already mutated the shared, canvas-rendered
        # flowgraph, so revert it exactly like the enclosing mutation rollback
        # instead of propagating the exception and leaving the graph mutated.
        flow_graph.import_data(initial_data)
        flow_graph.rewrite()
        return {"ok": False, "errors": [{"code": "mutation_failed", "message": str(exc)}]}

    # Write atomically with lock and backup
    try:
        grc_file_path = getattr(flow_graph, "grc_file_path", "")
        if not grc_file_path or Path(grc_file_path).is_dir():
            return {"ok": True}
        original = Path(grc_file_path)
        # resolve() follows symlinks, so the symlink check must run on the
        # unresolved path — checking it after resolve() is always False and
        # silently defeats the guard.
        if original.is_symlink():
            raise OSError(f"Refusing to save through symlink: {original}")
        target_path = original.resolve()
        if target_path.exists() and target_path.stat().st_nlink > 1:
            raise OSError(f"Refusing to save hard-linked graph file: {target_path}")

        lock_path = target_path.parent / ".grc_agent" / (target_path.name + ".lock")
        lock_path.parent.mkdir(mode=0o700, exist_ok=True)

        with lock_path.open("a", encoding="utf-8") as lock_file:
            # Non-blocking: this runs on the unified gbulb UI thread (the agent
            # write path). LOCK_NB means a held lock raises BlockingIOError
            # immediately instead of freezing GTK+asyncio for the contention
            # window; the outer except rolls back and returns save_failed, which
            # the change_graph tool surfaces as a retryable ModelRetry.
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                # Backup is taken INSIDE the lock so it snapshots exactly the
                # on-disk state about to be overwritten — a concurrent writer
                # can't slip in between the backup copy and the locked write
                # and leave the backup stale.
                if target_path.exists():
                    backup_dir = target_path.parent / ".grc_agent" / "backups"
                    backup_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
                    with open(target_path, "rb") as f:
                        old_hash = hashlib.file_digest(f, "sha256").hexdigest()
                    timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
                    backup_path = backup_dir / f"{timestamp}-{old_hash[:16]}{target_path.suffix}"
                    shutil.copy2(target_path, backup_path)
                    _prune_old_backups(backup_dir)
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


def _check_codegen_preconditions(flow_graph: Any) -> None:
    """Shared gate for generate_flowgraph_py/preview_flowgraph_py: the graph
    must be valid, and hierarchical-block or C++ output can't be generated
    this way (a hierarchical block's own Generator subclass does an os.mkdir
    as a side effect of construction — not just of writing — so there is no
    side-effect-free path for it here; C++ output requires a separate build
    step this harness doesn't perform)."""
    flow_graph.validate()
    if not flow_graph.is_valid():
        errors = [msg for _, msg in flow_graph.iter_error_messages()]
        raise ValueError(f"Flowgraph is not valid: {errors}")

    gen_opts = flow_graph.get_option("generate_options")
    if gen_opts.startswith("hb"):
        raise ValueError("Hierarchical blocks cannot be generated this way.")
    if flow_graph.get_option("output_language") == "cpp":
        raise ValueError("C++ output requires a build step — not supported.")


def generate_flowgraph_py(flow_graph: Any, output_dir: "Path | str") -> Path:
    """Generate a runnable Python script from a flowgraph.

    Validates the graph, rejects hierarchical blocks (hb*) and C++ output.
    Overrides run_options to 'run' (no input() prompt) — MUST call
    .rewrite() after setting the value, or the cached _evaluated stays
    stale and the generated script still contains input('Press Enter to
    quit:').
    """
    _check_codegen_preconditions(flow_graph)

    rop = flow_graph.options_block.params["run_options"]
    original = rop.value
    rop.set_value("run")
    rop.rewrite()
    try:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        from gnuradio.grc.core.generator.Generator import Generator

        gen = Generator(flow_graph, str(out))
        gen.write()
        file_path = Path(gen.file_path)
    finally:
        rop.set_value(original)
        rop.rewrite()

    return file_path


def preview_flowgraph_py(flow_graph: Any, k: int = 5) -> dict[str, Any]:
    """Render the Python source GNU Radio would generate from the current
    flowgraph, without writing anything to disk.

    Shares generate_flowgraph_py's validity/hier-block/C++ gate, but does
    NOT apply that function's run_options override — this shows the
    flowgraph's actual configured output (e.g. a real 'no_gui' script may
    still contain input('Press Enter to quit:') if that's how run_options
    is set), since the point here is showing what GRC would really
    generate, not what a Run/Stop launch needs.

    GNU Radio's own Generator (gnuradio.grc.core.generator.top_block.
    TopBlockGenerator) already separates in-memory rendering from disk
    writing internally: write() is a thin wrapper that calls
    _build_python_code_from_template() (pure computation, no I/O) and then
    opens/writes each returned (path, source) pair. Calling the former
    directly — confirmed by reading GNU Radio's installed source and by
    direct testing against real fixtures — never touches the filesystem.
    Each entry's "path" is informational only (where GRC would write it if
    the user clicked Generate) — it is not a real file and nothing can be
    read from or downloaded at it.

    GNU Radio's generator always appends the main flowgraph script last,
    after one entry per Embedded Python Block/Module instance (confirmed by
    reading TopBlockGenerator._build_python_code_from_template), so the main
    script — what most callers actually want — is kept unconditionally and
    never counts against `k`; `k` caps how many of the (usually few, but
    unbounded) block-source entries are included alongside it. Excess
    entries are dropped from the end of that block-source list (arbitrarily,
    since GNU Radio doesn't order them meaningfully) and counted in the
    returned "omitted_files", never silently.
    """
    _check_codegen_preconditions(flow_graph)

    grc_file_path = getattr(flow_graph, "grc_file_path", "")
    output_dir = Path(grc_file_path).parent if grc_file_path else Path(tempfile.gettempdir())

    from gnuradio.grc.core.generator.Generator import Generator

    gen = Generator(flow_graph, str(output_dir))
    if not hasattr(gen, "_build_python_code_from_template"):
        raise ValueError(
            "GNU Radio's code generator no longer exposes the in-memory "
            "rendering step this preview relies on (_build_python_code_from_template) "
            "— this installed GNU Radio version isn't supported by generate_python."
        )
    rendered = gen._build_python_code_from_template()

    files = [{"path": path, "source": source} for path, source in rendered]
    # The main script is always the last entry (see docstring); only the
    # block-source entries before it count against k.
    block_source_count = len(files) - 1
    omitted_files = 0
    if block_source_count > k:
        main_script = files[-1]
        kept = files[:k]
        omitted_files = block_source_count - len(kept)
        files = [*kept, main_script]

    return {"files": files, "omitted_files": omitted_files}
