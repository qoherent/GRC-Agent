import contextlib
import fcntl
import functools
import hashlib
import logging
import os
import re
import shutil
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

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
    """GUI Platform (gnuradio.grc.gui) for the in-process MainWindow. Kept lazy
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


def gui_actions() -> Any:
    """Lazy accessor for GRC's gui Actions namespace (in-process).

    Same self-contained gi setup as get_gui_platform. Platform is imported
    first — the app's canonical import order — because importing Actions
    directly into a fresh interpreter hits an upstream circular import
    (Actions → Dialogs/Utils → Bars → partially-initialized Actions; verified
    live: `from gnuradio.grc.gui import Actions` alone raises
    `AttributeError: ... no attribute 'FLOW_GRAPH_NEW'` from Bars.py, while
    Platform-first succeeds)."""
    import gi

    gi.require_version("Gtk", "3.0")
    gi.require_version("PangoCairo", "1.0")
    import gnuradio.grc.gui.Platform  # noqa: F401  (import-order anchor)
    from gnuradio.grc.gui import Actions

    return Actions


def gui_application_cls() -> Any:
    """Lazy accessor for the GRC GUI Application class (in-process).
    Same self-contained gi setup as get_gui_platform."""
    import gi

    gi.require_version("Gtk", "3.0")
    gi.require_version("PangoCairo", "1.0")
    from gnuradio.grc.gui.Application import Application

    return Application


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
    except Exception:
        _log.warning(
            "Failed to set Block Library panel visibility via action %s", action, exc_info=True
        )
    return bool(action.get_active())


def get_blocks_panel_visibility() -> bool:
    """Read GRC's native Block Library panel's current visibility state."""
    from gnuradio.grc.gui import Actions

    return bool(Actions.TOGGLE_BLOCKS_WINDOW.get_active())


_UNTITLED_SAVE_FOLDER_FN: Callable[[], str | Path | None] | None = None
_UNTITLED_SAVE_INSTALLED = False


def install_untitled_save_folder_provider(folder_fn: Callable[[], str | Path | None] | None) -> None:
    """Point the flowgraph Save-As dialog at the configured project directory
    when saving a NEW untitled graph, so Ctrl+S proposes the sidebar's work
    directory instead of GRC's arbitrary default folder.

    GRC's own SAVE/SAVE_AS handler (gnuradio.grc.gui.Application) resolves
    ``FileDialogs.SaveFlowGraph`` as a module attribute at call time, so we
    swap that class for a thin subclass that seeds the dialog's default folder
    — and only that. Everything else (the dialog itself, the save, id rename,
    recent-files bookkeeping, ``page.file_path``/``page.saved``) stays GRC's
    native handler running end-to-end: one uniform rule ("the untitled save
    dialog starts in the project directory"), no duplicated save logic, no key
    interception, no new ``.run()`` in our code. A named path
    (``current_file_path`` non-empty) keeps GRC's own "start in the file's
    folder" behavior.

    Idempotent: the class swap happens at most once per process; later calls
    only update the folder provider. The seed is applied only when the provider
    returns a real existing directory.
    """
    global _UNTITLED_SAVE_FOLDER_FN, _UNTITLED_SAVE_INSTALLED
    import gi

    gi.require_version("Gtk", "3.0")
    gi.require_version("PangoCairo", "1.0")
    from gnuradio.grc.gui import FileDialogs

    _UNTITLED_SAVE_FOLDER_FN = folder_fn
    if _UNTITLED_SAVE_INSTALLED:
        return

    class _ProjectSeededFolderDialog(FileDialogs.SaveFlowGraph):
        def __init__(self, parent: Any, current_file_path: str = "") -> None:
            super().__init__(parent, current_file_path)
            if current_file_path:
                return
            fn = _UNTITLED_SAVE_FOLDER_FN
            if fn is None:
                return
            raw = fn()
            try:
                proj = Path(raw).resolve() if raw else None
            except (TypeError, OSError):
                proj = None
            if proj is not None and proj.is_dir():
                self.set_current_folder(str(proj))

    FileDialogs.SaveFlowGraph = _ProjectSeededFolderDialog
    _UNTITLED_SAVE_INSTALLED = True


def register_execution_messenger(callback: Callable[[str], None]) -> None:
    """Register a callback to receive every message GRC sends to its native
    console panel (see gnuradio.grc.core.Messages). Used to detect flow
    graph execution failures without a dedicated log-scraping mechanism."""
    from gnuradio.grc.core import Messages

    Messages.register_messenger(callback)


def flow_graph_content_hash(flow_graph: Any) -> str:
    """Hash of the serialization the atomic save path writes for this
    flow_graph — directly comparable to a hash of the on-disk file's raw
    bytes (e.g. native_canvas.py's `_sha256_file`/`last_disk_hash`), since it's
    the exact same serialization. Used to detect in-memory edits that
    haven't reached disk yet (a safety net for GTK-native interactions that
    don't go through a specific, hooked signal — see native_canvas.py)."""
    return hashlib.sha256(_serialize_flow_graph(flow_graph).encode()).hexdigest()


def _serialize_flow_graph(flow_graph: Any) -> str:
    from gnuradio.grc.core.io import yaml as _grc_yaml

    return _grc_yaml.dump(flow_graph.export_data())


MAX_BACKUPS_PER_DIR = 50


def _prune_old_backups(backup_dir: Path) -> None:
    """Bound the per-directory backup set.

    Every save copies the previous file into ``backup_dir``; left alone that
    grows without bound over a project's life. Backup filenames lead with a
    timestamp (``{timestamp}-{hash}{suffix}``), so the oldest beyond the cap
    sort first. Best-effort: a pruning failure must never fail the save.
    """
    try:
        backups = sorted(backup_dir.iterdir(), key=lambda p: p.name)
        excess = len(backups) - MAX_BACKUPS_PER_DIR
        for old in backups[: max(0, excess)]:
            old.unlink(missing_ok=True)
    except Exception as exc:
        _log.debug("backup pruning failed for %s: %s", backup_dir, exc)


def load_flow_graph(file_path: str) -> Any:
    platform = get_platform()
    flow_graph = platform.make_flow_graph()
    flow_graph.grc_file_path = str(Path(file_path).resolve())
    parsed = platform.parse_flow_graph(str(file_path))
    flow_graph.import_data(parsed)
    flow_graph.rewrite()
    return flow_graph


def parse_conn(conn_str: str):
    # Exactly one '->' separator and exactly one ':' on each side — anything
    # else is malformed and returns None so the caller reports a precise
    # invalid_connection_format error instead of raising ValueError on unpack.
    if conn_str.count("->") != 1:
        return None
    src, dst = conn_str.split("->")
    if src.count(":") != 1 or dst.count(":") != 1:
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
        _log.debug("throwaway block creation failed for %r", block_type, exc_info=True)
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
        _log.warning("param_metadata extraction failed for %r", block_type, exc_info=True)
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
        _log.warning("port_metadata extraction failed for %r", block_type, exc_info=True)
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
    new_block_names: set[str] | None = None,
    is_add_phase: bool = True,
    add_blocks: list[dict] | None = None,
    update_params: list[dict] | None = None,
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
                        if ab.get("instance_name") == other and ab.get("block_id"):
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
    return bool(
        variable_names and any(tok in variable_names for tok in _IDENTIFIER_RE.findall(value))
    )


def render_port(port: Any) -> dict[str, Any] | None:
    """Render one port, or None when it carries no information.

    An optional port nobody connected is noise in the model-facing payload;
    inspect_graph counts the omissions rather than listing them.
    """
    optional = bool(getattr(port, "optional", False))
    connected = len(list(port.connections(enabled=True))) > 0
    if optional and not connected:
        return None
    domain = str(getattr(port, "domain", "") or "")
    res = {"port_id": str(port.key), "dtype": str(getattr(port, "dtype", ""))}
    if domain and domain != "stream":
        res["domain"] = domain
    # Vector lengths are the structural cause behind item-size mismatch
    # errors (an fft_vxx port is vlen=1024, a scalar sink vlen=1) — without
    # them two ports with the same dtype read as interchangeable. One uniform
    # rule: emit vlen whenever it differs from the scalar default.
    vlen = getattr(port, "vlen", 1)
    if vlen not in (None, 1, "1", ""):
        res["vlen"] = vlen
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
    flow_graph: Any, targets: list[str] | str | None = None
) -> dict[str, Any]:
    blocks_all = []
    connections_all = []

    for c in flow_graph.connections:
        conn_str = (
            f"{c.source_block.name}:{c.source_port.key}->{c.sink_block.name}:{c.sink_port.key}"
        )
        connections_all.append(conn_str)

    # GRC stores connections in a set, so iteration order varies between
    # calls on an unchanged graph. Sort them: the model otherwise sees a
    # different payload every time it inspects the same graph, which defeats
    # prompt caching and makes two inspections impossible to diff.
    connections_all.sort()

    variable_names = {b.name for b in flow_graph.blocks if getattr(b, "is_variable", False)}

    for b in flow_graph.blocks:
        params = {}
        omitted_params_count = 0
        for k, p in b.params.items():
            if keep_param(k, p, b, mode="overview", variable_names=variable_names):
                params[k] = str(p.value)
            else:
                omitted_params_count += 1

        inputs = []
        omitted_inputs_count = 0
        for p in getattr(b, "active_sinks", ()) or ():
            rendered = render_port(p)
            if rendered is not None:
                inputs.append(rendered)
            else:
                omitted_inputs_count += 1

        outputs = []
        omitted_outputs_count = 0
        for p in getattr(b, "active_sources", ()) or ():
            rendered = render_port(p)
            if rendered is not None:
                outputs.append(rendered)
            else:
                omitted_outputs_count += 1

        role = classify_role(b)
        state = str(getattr(b, "state", "enabled"))
        if state == "bypassed":
            state = "bypass"

        # An omission counter is emitted only when something was actually
        # omitted, and an empty port list is left out entirely. Absence says
        # exactly what a zero said — nothing was hidden — and on the repo's
        # own fixtures the zeros and empty arrays were 21% of the payload the
        # model pays for on every inspection. The tool's description states
        # the convention so a missing key is never ambiguous.
        entry = {
            "instance_name": b.name,
            "block_id": b.key,
            "role": role,
            "state": state,
            "params": params,
        }
        if inputs:
            entry["inputs"] = inputs
        if outputs:
            entry["outputs"] = outputs
        for key, count in (
            ("omitted_params_count", omitted_params_count),
            ("omitted_inputs_count", omitted_inputs_count),
            ("omitted_outputs_count", omitted_outputs_count),
        ):
            if count:
                entry[key] = count
        blocks_all.append(entry)

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

    if isinstance(targets, str):
        cleaned = targets.strip().lower()
        targets = None if cleaned in ("", "all", "*") else [targets.strip()]

    whole_graph = not targets or any(
        isinstance(t, str) and t.strip().lower() in ("", "all", "*") for t in targets
    )
    if not whole_graph:
        assert targets is not None
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


def _fsync_directory(path: Path) -> None:
    """Durably commit a just-replaced file entry in its containing directory
    (the rename is not persistent until the directory entry itself is fsynced).
    Best-effort on non-POSIX platforms (no os.O_DIRECTORY) and on transient
    failures — the debug log carries the reason."""
    try:
        dir_fd = os.open(str(path), os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except AttributeError:
        # os.O_DIRECTORY not available on non-POSIX platforms.
        pass
    except OSError as exc:
        _log.debug("directory fsync failed for %s: %s", path, exc)


def _atomic_write_text(payload: str, path: Path) -> None:
    """Atomically replace ``path``'s content with ``payload`` (temp → fsync →
    os.replace → directory fsync). Does NOT take a lock — callers that need
    cross-process mutual exclusion acquire ``fcntl.flock`` on
    ``.grc_agent/<name>.lock`` themselves (see change_graph's save path and
    native_canvas.sync_manual_edit)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        _fsync_directory(path.parent)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


# GRC's DEFAULT_FLOW_GRAPH_ID (gnuradio.grc.core.Constants) — inlined as a
# literal so this naming authority stays importable headless without pulling
# gnuradio (adapter's gnuradio imports are lazy by contract, see get_platform).
_DEFAULT_FLOW_GRAPH_ID = "default"

# Directive for every tool gated on an active project directory: the fs and
# shell toolsets raise this same message (imported from here), so the wording
# has exactly one source and cannot drift between tool families.
_NO_ACTIVE_GRAPH_MSG = (
    "No project directory is set or saved to disk, so there is no project folder to "
    "operate in. Select a Project directory in the sidebar or save the flowgraph first."
)


def sanitize_id_stem(stem: str) -> str:
    """Map a file stem to a GRC-safe options id (a Python identifier).

    One uniform rule, applied identically to every stem: invalid identifier
    characters at the edges are dropped, interior runs of them collapse to a
    single underscore, and a leading digit is prefixed with ``_``. Valid
    identifiers (including ones with edge underscores) pass through
    unchanged, which makes the mapping idempotent — so re-deriving the id
    from an already-derived id is a no-op.

    The edge-dropping is what pins the SAVE_COPY-parity outcome
    ``untitled(1).grc`` -> id ``untitled_1``: the trailing ``)`` sits at the
    edge and is dropped, the interior ``(`` collapses to ``_``.
    """
    stem = re.sub(r"^[^0-9A-Za-z_]+|[^0-9A-Za-z_]+$", "", stem)
    stem = re.sub(r"[^0-9A-Za-z_]+", "_", stem)
    if stem[:1].isdigit():
        stem = "_" + stem
    return stem


def resolve_save_target(
    project_dir: str | Path | None,
    options_id: str,
    file_path: str | Path | None,
) -> tuple[Path, str | None]:
    """Resolve where an active flowgraph page saves, and the id it takes.

    Pure, display-free naming authority (R3/R5): no GTK, no flowgraph object;
    the only side effect allowed is filesystem *existence* checks — nothing
    is created or written.

    - Titled page (``file_path`` non-empty): re-saves in place. The existing
      path is returned unchanged and the id stem is ``None`` — a titled page
      derives nothing and takes no SAVE_AS-parity id rename.
    - Untitled page: derives the target from the options id. GRC's default
      id (``'default'``) names ``untitled.grc``; a non-default id names
      ``<id>.grc`` (a GRC id already equals its file stem). Whenever the
      candidate exists, the smallest ``<stem>(<n>).grc`` that is not present
      wins (GRC's SAVE_COPY counter precedent) — derivation never clobbers.

    Returns ``(path, id_stem)`` where ``id_stem`` is the sanitized id-safe
    stem of the *chosen* file for the caller to apply via GRC's
    SAVE_AS-parity options-id rename (``untitled(1)`` -> ``untitled_1``).

    Raises:
        ValueError: with the fs-tool directive wording when no project
            directory is configured (same gate as the filesystem tools).
    """
    if not project_dir:
        raise ValueError(_NO_ACTIVE_GRAPH_MSG)
    if file_path:
        return Path(file_path), None
    root = Path(project_dir)
    stem = "untitled" if options_id == _DEFAULT_FLOW_GRAPH_ID else options_id
    candidate = root / f"{stem}.grc"
    n = 1
    while candidate.exists():
        candidate = root / f"{stem}({n}).grc"
        n += 1
    return candidate, sanitize_id_stem(candidate.stem)



def _sanitize_data(data: Any) -> Any:
    """Recursively normalize non-breaking spaces (U+00A0) in strings, lists, and dicts."""
    if isinstance(data, str):
        return data.replace("\u00a0", " ")
    if isinstance(data, dict):
        return {k: _sanitize_data(v) for k, v in data.items()}
    if isinstance(data, list):
        return [_sanitize_data(item) for item in data]
    return data


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

    raw_value = str(value).replace("\u00a0", " ")
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


def _revert_flow_graph(flow_graph: Any, initial_data: Any) -> str | None:
    """Restore the shared flowgraph to its pre-mutation state.

    Returns an error string if the revert itself failed, and never raises.
    Every caller is already on a failure path returning ok:false, so
    propagating from here would replace that structured result with a raw
    traceback while leaving the canvas-rendered graph half-mutated — the exact
    outcome the rollback exists to prevent.

    The revert can genuinely fail: GNU Radio >= 3.10.12's `import_data` calls
    `flow_graph.validate()` itself (`core/blocks/options.py`'s
    `insert_grc_parameters`), so whatever raised on the way in can raise again
    on the way back out. A failed revert is reported to the caller rather than
    swallowed.
    """
    try:
        # import_data reports a partial restore by RETURN VALUE, not by
        # raising: GNU Radio's own docstring says "any blocks or connections
        # in error will be ignored" and it returns connection_error. Ignoring
        # that return made a rollback that silently dropped connections
        # indistinguishable from a clean one.
        had_connection_errors = bool(flow_graph.import_data(initial_data))
        flow_graph.rewrite()
        if had_connection_errors:
            return (
                "rollback restored the flowgraph but GNU Radio dropped one or more "
                "connections while re-importing it; the graph may be missing wiring "
                "that was present before this call"
            )
        return None
    except Exception as exc:
        grc_file_path = getattr(flow_graph, "grc_file_path", "")
        if grc_file_path and Path(grc_file_path).is_file():
            try:
                platform = get_platform()
                disk_data = platform.parse_flow_graph(str(grc_file_path))
                flow_graph.import_data(disk_data)
                flow_graph.rewrite()
                # NOT a clean revert: the on-disk file is not initial_data.
                # Any unsaved manual canvas edit the user made before this
                # call is gone, and reporting success here hid that.
                return (
                    f"in-memory rollback failed ({exc}); the flowgraph was reloaded from "
                    f"{grc_file_path} instead, so any unsaved manual edits made before "
                    "this call have been lost"
                )
            except Exception as reload_exc:
                return (
                    f"rollback failed ({exc}) and reloading from disk also failed "
                    f"({type(reload_exc).__name__}: {reload_exc}); the flowgraph is "
                    "left mutated"
                )
        return f"rollback failed, flowgraph may be left mutated: {exc}"


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
    from grc_agent.adapter.layout import _compute_layout_model, compute_full_layout

    add_blocks = _sanitize_data(add_blocks)
    update_params = _sanitize_data(update_params)

    # Whether this batch will relayout the whole graph (the layout hook's
    # gate, computed here from the same sanitized values so the success
    # payload can tell the caller a rearrangement happened — the canvas uses
    # it to fit the graph into view).
    relayout = bool(
        add_blocks or remove_blocks or add_connections or remove_connections
    )

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

    pre_existing_errors: list[str] = []
    try:
        flow_graph.validate()
        if not flow_graph.is_valid():
            for elem, msg in flow_graph.iter_error_messages():
                parent = getattr(elem, "parent_block", None)
                if parent is not None and parent is not elem:
                    pre_existing_errors.append(f"{parent.name}: {elem}: {msg}")
                else:
                    pre_existing_errors.append(f"{elem}: {msg}")
    except Exception:
        pass

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
            # Placement strategy: every block (existing and new) is
            # relaid-out from scratch after this batch's blocks are created
            # — see compute_full_layout below, called once after the loop.
            # Must never need agent or user input: the agent's own context
            # has block coordinates filtered out entirely, so positioning
            # has to be fully self-contained.
            try:
                model = _compute_layout_model(flow_graph, new_block_names, add_connections)
            except Exception as exc:
                _log.warning("Pre-add layout model computation failed: %s", exc)
                from grc_agent.adapter.layout import LayoutModel
                model = LayoutModel(ranks={}, components=[], ordered_ranks=[])

            # Sort add_blocks topologically by rank so upstream blocks (sources)
            # are placed first, providing solid layout anchors for downstream blocks.
            add_blocks_sorted = sorted(
                add_blocks,
                key=lambda item: model.ranks.get(item["instance_name"], 0),
            )

            for item in add_blocks_sorted:
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

            # Relayout every block in the flowgraph from scratch whenever
            # this batch changes topology — one uniform rule: the layout
            # always reflects the current topology, so a later wire-only call
            # re-ranks blocks that were added unwired (and previously frozen
            # in a stale alphabetical stack). variable/options/import/snippet
            # blocks pack into a header band at the top, everything else
            # flows below via rank-ordered placement. change_graph is the
            # only caller of compute_full_layout, and manual/GUI edits never
            # call change_graph, so this can't run outside an agent-driven
            # edit.
        if add_blocks or remove_blocks or add_connections or remove_connections:
            try:
                if not add_blocks:
                    model = _compute_layout_model(flow_graph, set(), add_connections)
                full_positions = compute_full_layout(
                    flow_graph, new_block_names, add_connections, model=model
                )
                for b in flow_graph.blocks:
                    pos = full_positions.get(b.name)
                    if pos is not None:
                        b.states["coordinate"] = list(pos)
            except Exception as exc:
                # relayout was derived from the REQUEST, so a layout that threw
                # still reported relayout:true and the canvas fitted a view to
                # coordinates that were never recomputed. Report what happened.
                relayout = False
                _log.warning("Full layout computation failed during change_graph: %s", exc)

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
                src_port = None
                dst_port = None
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
        mutation_errors = [{"code": "mutation_failed", "message": str(exc)}]
        revert_error = _revert_flow_graph(flow_graph, initial_data)
        if revert_error:
            mutation_errors.append({"code": "rollback_failed", "message": revert_error})
        return {
            "ok": False,
            "error_type": "mutation_failed",
            "errors": mutation_errors,
            "rollback_failed": bool(revert_error),
        }

    if errors:
        revert_error = _revert_flow_graph(flow_graph, initial_data)
        if revert_error:
            errors.append({"code": "rollback_failed", "message": revert_error})
        return {
            "ok": False,
            "error_type": "batch_failed",
            "errors": errors,
            "rollback_failed": bool(revert_error),
        }

    # See inspect_graph's identical call for why this is required: without
    # it, is_valid() reports "valid" regardless of actual state (confirmed
    # live: removing a required connection without force=True was silently
    # accepted, leaving a genuinely broken graph persisted to disk).
    still_invalid = False
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
                    validation_errors.append(
                        {"code": "gnu_validation", "message": f"{elem}: {msg}"}
                    )
            if not validation_errors:
                validation_errors = [
                    {"code": "gnu_validation", "message": "GRC validation failed."}
                ]
            new_errors = [
                e for e in validation_errors if e["message"] not in pre_existing_errors
            ]
            if not new_errors:
                # Every remaining error was already there before this call, so
                # the edit is not blamed for them — but the graph IS still
                # invalid and is about to be committed. ok:true alone read as
                # "valid now"; still_invalid says otherwise.
                still_invalid = True
            if new_errors:
                revert_error = _revert_flow_graph(flow_graph, initial_data)
                if revert_error:
                    new_errors.append({"code": "rollback_failed", "message": revert_error})
                return {
                    "ok": False,
                    "error_type": "validation_failed",
                    "errors": new_errors,
                    "rollback_failed": bool(revert_error),
                }
    except Exception as exc:
        # The validation gate itself raised (rather than populating an error
        # list). The phases above already mutated the shared, canvas-rendered
        # flowgraph, so revert it exactly like the enclosing mutation rollback
        # instead of propagating the exception and leaving the graph mutated.
        gate_errors = [{"code": "mutation_failed", "message": str(exc)}]
        revert_error = _revert_flow_graph(flow_graph, initial_data)
        if revert_error:
            gate_errors.append({"code": "rollback_failed", "message": revert_error})
        return {
            "ok": False,
            "error_type": "mutation_failed",
            "errors": gate_errors,
            "rollback_failed": bool(revert_error),
        }

    # Write atomically with lock and backup
    committed = False
    try:
        grc_file_path = getattr(flow_graph, "grc_file_path", "")
        if not grc_file_path or Path(grc_file_path).is_dir():
            # The in-memory graph IS mutated, but nothing was written. Saying
            # so explicitly stops the caller reading ok:true as "persisted".
            res = {"ok": True, "relayout": relayout, "persisted": False}
            if still_invalid:
                res["still_invalid"] = True
            if pre_existing_errors:
                res["pre_existing_errors"] = pre_existing_errors
            return res
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
                _atomic_write_text(_serialize_flow_graph(flow_graph), target_path)
                committed = True
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    except Exception as exc:
        if committed:
            # The bytes are already on disk. Reverting memory now would leave
            # disk holding the new graph and memory the old one — the two
            # silently disagreeing is worse than the failure being reported.
            _log.warning("change_graph: failed after committing to disk: %s", exc)
            res = {"ok": True, "relayout": relayout, "persisted": True}
            if still_invalid:
                res["still_invalid"] = True
            res["post_commit_warning"] = (
                f"The edit was written to disk, but finalising afterwards failed: {exc}"
            )
            if pre_existing_errors:
                res["pre_existing_errors"] = pre_existing_errors
            return res
        save_errors = [
            {"code": "save_failed", "message": f"Failed to commit changes atomically: {exc}"}
        ]
        revert_error = _revert_flow_graph(flow_graph, initial_data)
        if revert_error:
            save_errors.append({"code": "rollback_failed", "message": revert_error})
        return {
            "ok": False,
            "error_type": "save_failed",
            "errors": save_errors,
            "rollback_failed": bool(revert_error),
        }

    res = {"ok": True, "relayout": relayout, "persisted": True}
    if still_invalid:
        res["still_invalid"] = True
    if pre_existing_errors:
        res["pre_existing_errors"] = pre_existing_errors
    return res


def _check_codegen_preconditions(flow_graph: Any) -> None:
    """Shared gate for preview_flowgraph_py: the graph must be valid, and
    hierarchical-block or C++ output can't be generated this way (a
    hierarchical block's own Generator subclass does an os.mkdir as a side
    effect of construction — not just of writing — so there is no
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


def preview_flowgraph_py(flow_graph: Any, k: int = 5) -> dict[str, Any]:
    """Render the Python source GNU Radio would generate from the current
    flowgraph, without writing anything to disk.

    Shares the codegen validity/hier-block/C++ gate, but applies no
    run_options override — this shows the flowgraph's actual configured
    output (e.g. a real 'no_gui' script may still contain
    input('Press Enter to quit:') if that's how run_options is set), since
    the point here is showing what GRC would really generate, not what a
    Run/Stop launch needs.

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
    returned "omitted_files", never silently. Callers are responsible for
    bounding `k`; the model-facing tool declares 1-20 in its schema.
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
