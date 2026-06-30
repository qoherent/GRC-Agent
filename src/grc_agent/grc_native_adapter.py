"""Native GRC adapter — load, mutate, validate, identity.

Per AGENTS.md (no in-band control flow; no gnuradio imports outside this
module). The single source of truth for GNU Radio access in the agent.

Public surface (consumed by Phase 6's cutover):

- ``get_platform()`` — lazy singleton, never imports ``gnuradio`` at module
  top-level so CI without GNU Radio can still import the module.
- ``GraphIdentity`` — file-bytes SHA-256 + per-``FlowGraph`` revision counter.
  Per the consultant's perf review, no deep-JSON hashing of the Pydantic model.
- ``load_flow_graph``, ``load_and_inspect`` — loading + Pydantic snapshot.
- ``add_block``, ``remove_block``, ``set_param``, ``set_block_state``,
  ``connect``, ``disconnect``, ``apply_mutation``, ``validate`` —
  the six ``change_graph`` op_types, applied to a native ``FlowGraph``.
- ``validate``, ``render_block``, ``render_connection``, ``render_parameter``,
  ``render_flow_graph``, ``classify_role`` — inspection helpers; filtering
  delegates to the single authority :mod:`grc_agent.runtime.param_filter`.
- ``serialize_flow_graph``, ``write_flow_graph_atomic`` — persistence.

``connect``/``disconnect`` consume resolved ``Port`` objects; ``apply_mutation``
and the test surface accept ``(block_name, port_key)`` and resolve internally.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from grc_agent.domain_models import (
    BlockRole,
    GrcBlock,
    GrcFlowgraph,
    GrcValidation,
)
from grc_agent.runtime.block_semantics import evaluated_param_hides
from grc_agent.runtime.connection_ids import connection_id
from grc_agent.runtime.param_filter import (
    DEFAULT_PARAM_TAB,
    OVERVIEW,
    keep_param,
    overview_rank,
)

# --------------------------------------------------------------------------- #
# Singleton platform                                                           #
# --------------------------------------------------------------------------- #

_PLATFORM: Any | None = None


def get_platform() -> Any:
    """Return a fully-warmed ``gnuradio.grc.core.platform.Platform``.

    Lazy singleton. Never import ``gnuradio`` at module top level — CI without
    GNU Radio must be able to import this module without crashing.
    """
    global _PLATFORM
    if _PLATFORM is not None:
        return _PLATFORM
    try:
        from gnuradio import gr
        from gnuradio.grc.core.platform import Platform as _PlatformCls
    except ImportError as exc:
        raise RuntimeError(
            "GRC Agent requires GNU Radio 3.10.x with grc.core. "
            f"Import failed: {exc}. On Debian/Ubuntu install: "
            "apt install gnuradio gnuradio-dev."
        ) from exc
    _PLATFORM = _PlatformCls(
        name="grc_agent",
        prefs=gr.prefs(),
        version=gr.version(),
        version_parts=(gr.major_version(), gr.api_version(), gr.minor_version()),
    )
    _PLATFORM.build_library()
    return _PLATFORM


def get_platform_or_none() -> Any:
    """Return the platform singleton or ``None`` if unavailable.

    Graceful-degradation wrapper around :func:`get_platform` for callers that
    must not raise when GNU Radio is absent (catalog inspection, param
    metadata, semantic classification).
    """
    try:
        return get_platform()
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Graph identity (no deep-JSON hashing)                                        #
# --------------------------------------------------------------------------- #


@dataclass
class GraphIdentity:
    file_sha256: str | None
    instance_id: int = 0
    revision: int = 0


def new_graph_identity(file_bytes: bytes | None) -> GraphIdentity:
    sha = hashlib.sha256(file_bytes).hexdigest() if file_bytes else None
    return GraphIdentity(file_sha256=sha)


def bind_to_flow_graph(identity: GraphIdentity, flow_graph: Any) -> None:
    identity.instance_id = id(flow_graph)


def bump_revision(identity: GraphIdentity) -> None:
    identity.revision += 1


# --------------------------------------------------------------------------- #
# Loading                                                                      #
# --------------------------------------------------------------------------- #


def _parse_flow_graph(file_path: Path) -> Any:
    platform = get_platform()
    return platform.parse_flow_graph(str(file_path))


def load_flow_graph(file_path: Path) -> Any:
    """Load a .grc file into a native ``FlowGraph`` (raises on failure)."""
    flow_graph = get_platform().make_flow_graph()
    flow_graph.grc_file_path = str(file_path.resolve())
    flow_graph.import_data(_parse_flow_graph(file_path))
    flow_graph.rewrite()
    return flow_graph


def _err_graph(file_path: Path, code: str, message: str) -> GrcFlowgraph:
    return GrcFlowgraph(
        ok=False,
        graph_name=file_path.stem,
        errors=[{"code": code, "message": message}],
        validation=GrcValidation(status="unknown"),
    )


def load_and_inspect(file_path: Path) -> GrcFlowgraph:
    """Load a .grc file and return a ``GrcFlowgraph`` snapshot. Structured errors
    on failure; never raises. ``ok=False`` + ``errors[0].code`` identifies the
    failure mode."""
    try:
        file_path.read_text(encoding="utf-8-sig")
    except (IsADirectoryError, FileNotFoundError, PermissionError, UnicodeDecodeError) as exc:
        return _err_graph(file_path, "FILE_READ_ERROR", str(exc))
    try:
        flow_graph = get_platform().make_flow_graph()
        flow_graph.grc_file_path = str(file_path.resolve())
        flow_graph.import_data(_parse_flow_graph(file_path))
    except Exception as exc:
        return _err_graph(file_path, "YAML_PARSE_ERROR", str(exc))
    try:
        flow_graph.rewrite()
    except Exception as exc:
        return _err_graph(file_path, "REWRITE_FAILED", str(exc))
    return render_flow_graph(flow_graph)


# --------------------------------------------------------------------------- #
# Inspection helpers                                                           #
# --------------------------------------------------------------------------- #


def _classify_role_core(
    *,
    key: str,
    is_variable: bool,
    is_import: bool,
    is_snippet: bool,
    is_virtual_or_pad: bool,
    has_sources: bool,
    has_sinks: bool,
) -> BlockRole:
    """Single source of truth for block role classification.

    Mirrors the logic that used to live inline in ``catalog/schema.py``.
    Restores ``VIRTUAL_OR_PAD`` (previously dropped by the catalog inline
    ladder) so every catalog payload classifies to the same role a live
    GRC block would.
    """
    if is_variable:
        return BlockRole.VARIABLE
    if is_import:
        return BlockRole.IMPORT
    if is_snippet:
        return BlockRole.SNIPPET
    if is_virtual_or_pad:
        return BlockRole.VIRTUAL_OR_PAD
    if key == "options":
        return BlockRole.OPTIONS
    if has_sources and not has_sinks:
        return BlockRole.SOURCE
    if has_sinks and not has_sources:
        return BlockRole.SINK
    if has_sources and has_sinks:
        return BlockRole.TRANSFORM
    return BlockRole.OTHER


def classify_role(block: Any) -> BlockRole:
    """Classify a live GRC block (uses native ``Block`` discriminators)."""
    return _classify_role_core(
        key=getattr(block, "key", ""),
        is_variable=bool(getattr(block, "is_variable", False)),
        is_import=bool(getattr(block, "is_import", False)),
        is_snippet=bool(getattr(block, "is_snippet", False)),
        is_virtual_or_pad=bool(getattr(block, "is_virtual_or_pad", False)),
        has_sources=len(getattr(block, "active_sources", ()) or ()) > 0,
        has_sinks=len(getattr(block, "active_sinks", ()) or ()) > 0,
    )


def classify_role_from_catalog(
    key: str,
    flags: list[str] | tuple[str, ...],
    *,
    has_sources: bool,
    has_sinks: bool,
) -> BlockRole:
    """Classify a catalog payload to the same role a live block would get.

    Single source of truth with :func:`classify_role` via
    :func:`_classify_role_core`. Derives native discriminators from
    catalog metadata (``flags`` and ``key``).
    """
    flags_tuple = tuple(flags or ())
    return _classify_role_core(
        key=key,
        is_variable="variable" in flags_tuple or key.startswith("variable"),
        is_import=key == "import",
        is_snippet=key == "snippet",
        is_virtual_or_pad=key.startswith(("virtual_", "pad_")),
        has_sources=has_sources,
        has_sinks=has_sinks,
    )


def render_parameter(
    block: Any,
    param_key: str,
    param: Any,
    evaluated_hides: dict[str, str] | None = None,
    mode: str = OVERVIEW,
    variable_names: set[str] | None = None,
) -> str | None:
    """One uniform parameter filter — delegates to the bible (``keep_param``).
    Returns ``None`` if the param is hidden or in an excluded category.
    """
    hide_map = evaluated_hides
    if hide_map is None:
        # Fall back to a direct evaluation if no pre-fetched map is supplied.
        try:
            current = {param_key: str(param.value)}
            hide_map = evaluated_param_hides(block.key, current)
        except Exception:
            hide_map = {}
    hide = hide_map.get(param_key, "all")
    category = str(getattr(param, "category", DEFAULT_PARAM_TAB))
    dtype = str(getattr(param, "dtype", ""))
    value = str(param.value)
    default = str(getattr(param, "default", ""))
    if not keep_param(
        hide=hide,
        category=category,
        dtype=dtype,
        value=value,
        default=default,
        mode=mode,
        variable_names=variable_names,
        param_key=param_key,
    ):
        return None
    # Empty values carry no information; the bible's live path
    # (filter_live_block_params) already strips them, so mirror that here so an
    # empty enum such as realtime_scheduling="" never leaks into the inspect
    # payload. Consistency fix, not a new omission.
    if not value.strip():
        return None
    return value


def render_block(
    block: Any,
    flow_graph: Any | None = None,
    mode: str = OVERVIEW,
    variable_names: set[str] | None = None,
) -> GrcBlock:
    evaluated_hides: dict[str, str] | None = None
    if flow_graph is not None:
        try:
            current = {k: str(p.value) for k, p in block.params.items()}
            evaluated_hides = evaluated_param_hides(block.key, current)
        except Exception:
            evaluated_hides = None

    unsorted_params = []
    for k, p in block.params.items():
        rendered = render_parameter(
            block,
            k,
            p,
            evaluated_hides=evaluated_hides,
            mode=mode,
            variable_names=variable_names,
        )
        if rendered is not None:
            unsorted_params.append((k, rendered))

    if evaluated_hides:
        unsorted_params.sort(key=lambda item: (overview_rank(evaluated_hides.get(item[0], "all")), item[0]))

    parameters = {k: v for k, v in unsorted_params}

    states = getattr(block, "states", {}) or {}
    raw_state = str(states.get("state", "enabled"))
    canonical_state = "bypass" if raw_state == "bypassed" else raw_state
    return GrcBlock(
        instance_name=block.name,
        block_id=block.key,
        role=classify_role(block),
        state=canonical_state,
        params=parameters,
    )


def render_connection(conn: Any) -> str:
    src = conn.source_block
    sp = conn.source_port
    dst = conn.sink_block
    dp = conn.sink_port
    return connection_id(src.name, sp.key, dst.name, dp.key)


def render_flow_graph(flow_graph: Any, mode: str = OVERVIEW) -> GrcFlowgraph:
    variable_names = {b.name for b in flow_graph.blocks if getattr(b, "is_variable", False)}
    blocks = [
        render_block(b, flow_graph, mode=mode, variable_names=variable_names)
        for b in flow_graph.blocks
    ]
    connections = [render_connection(c) for c in flow_graph.connections]
    valid = bool(flow_graph.is_valid())
    errors = []
    if not valid:
        for elem, message in flow_graph.iter_error_messages():
            errors.append(_format_error(elem, message))
    options = getattr(flow_graph, "options_block", None)
    return GrcFlowgraph(
        ok=valid,
        graph_name=options.name if options is not None else "",
        blocks=blocks,
        connections=connections,
        validation=GrcValidation(
            status="valid" if valid else "invalid",
            errors=errors,
            native_ok=valid,
        ),
    )


# --------------------------------------------------------------------------- #
# Mutation helpers                                                             #
# --------------------------------------------------------------------------- #


def _format_error(elem: Any, msg: Any) -> str:
    """Format a GRC validation error with element identity.

    GRC's ``iter_error_messages`` yields ``(element, message)`` tuples
    where the element is the Block/Port/Connection that has the error.
    The element's ``str()`` includes port direction and key (e.g.,
    ``'Sink - in2(2)'``). Prefixing the message with the parent block
    name makes the error actionable: the model can identify WHICH block
    and WHICH port has the problem.
    """
    parent = getattr(elem, "parent_block", None)
    if parent is not None and parent is not elem:
        return f"{parent.name}: {elem}: {msg}"
    loc = str(elem)
    return f"{loc}: {msg}" if loc else str(msg)


def port_object(
    flow_graph: Any, block_name: str, port_key: str, *, kind: str
) -> Any:
    """Return the live port object on ``block_name`` matching ``port_key``.

    Returns ``None`` when the block has no port with that key under
    ``kind`` (``"source"`` or ``"sink"``). Single source of truth for
    port resolution; replaces the inline ``active_sinks``/``active_sources``
    scans that previously lived in runtime modules.
    """
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
    """Internal raise-on-miss wrapper preserved for callers that want an error."""
    port = port_object(flow_graph, block_name, port_key, kind=kind)
    if port is None:
        raise KeyError(f"{kind} port {port_key!r} not on block {block_name!r}")
    return port


def add_block(
    flow_graph: Any,
    block_type: str,
    instance_name: str,
    parameters: dict[str, Any] | None = None,
    state: str | None = None,
) -> Any:
    """Add a new block. Names it via ``params['id']`` (the empirically correct
    path — GRC's ``Block.name`` is a read-only property). Unknown params raise
    ``KeyError`` (uniform with :func:`set_param`/:func:`apply_mutation`
    ``update_params``)."""
    block = flow_graph.new_block(block_type)
    if block is None:
        raise KeyError(f"Block type {block_type!r} not found in catalog")
    block.params["id"].set_value(str(instance_name))
    flow_graph.rewrite()
    for k, v in (parameters or {}).items():
        set_param(block, k, v)
    if state is not None and state != "enabled":
        set_block_state(block, state)
    return block


def remove_block(flow_graph: Any, instance_name: str) -> None:
    target = flow_graph.get_block(instance_name)
    flow_graph.remove_element(target)


# A value of the form ``${variable:NAME}`` is a templating literal some models
# emit instead of the bare variable name GRC expressions require. Uniform rule:
# detect it once for every param and surface a clear error (never silently
# strip — AGENTS.md "no silent transformation").
_VARIABLE_TEMPLATE_RE = re.compile(r"^\$\{variable:\s*([A-Za-z_]\w*)\s*\}$")


def set_param(block: Any, param_key: str, value: str) -> None:
    if param_key not in block.params:
        raise KeyError(f"Param {param_key!r} not in block {block.name!r}")
    if param_key == "id":
        # The 'id' param is the instance name; renaming via set_param is
        # intentionally ignored (rename = remove + add). Short-circuit before
        # the enum/template checks — id is never an enum or a template value.
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
    # Validate enum inputs against the authoritative option set BEFORE setting.
    # Native set_value accepts ANY string on an enum and ``rewrite()`` then
    # silently resets an invalid token to the default (observed in the wild:
    # 'float_const/float' -> 'complex' with no error). Reject here so the model
    # learns the token is wrong — one uniform rule for every enum param.
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


def set_block_state(block: Any, state: str) -> None:
    aliases = {"bypass": "bypassed"}
    canonical = aliases.get(state, state)
    if canonical not in block.STATE_LABELS:
        raise ValueError(
            f"Invalid state {state!r}; must be one of {block.STATE_LABELS}"
        )
    block.state = canonical


def connect(flow_graph: Any, src_block: str, src_port: str, dst_block: str, dst_port: str) -> Any:
    src = _find_port(flow_graph, src_block, src_port, kind="source")
    dst = _find_port(flow_graph, dst_block, dst_port, kind="sink")
    return flow_graph.connect(src, dst)


def disconnect(
    flow_graph: Any, src_block: str, src_port: str, dst_block: str, dst_port: str
) -> None:
    """Remove a single connection edge.

    Native ``flow_graph.disconnect(*ports)`` removes every connection touching
    any of the named ports, not a single edge, so we locate the exact
    ``Connection`` object and use ``remove_element`` (the same native API the
    GRC GUI calls for single-edge deletion).
    """
    for connection in list(flow_graph.connections):
        if (
            connection.source_block.name == src_block
            and connection.source_port.key == src_port
            and connection.sink_block.name == dst_block
            and connection.sink_port.key == dst_port
        ):
            flow_graph.remove_element(connection)
            return
    raise KeyError(f"connection not found: {src_block}:{src_port}->{dst_block}:{dst_port}")


def apply_mutation(flow_graph: Any, op_type: str, **kwargs: Any) -> None:
    if op_type == "add_block":
        add_block(flow_graph, **kwargs)
    elif op_type == "remove_block":
        remove_block(flow_graph, **kwargs)
    elif op_type == "update_params":
        block = flow_graph.get_block(kwargs.pop("instance_name"))
        for k, v in (kwargs.pop("params") or {}).items():
            set_param(block, k, v)
        # Regenerate derived IO: params like ``num_inputs`` / ``type`` /
        # ``vlen`` determine port count and dtype via GRC's templates, and
        # those ports are only (re)created on rewrite(). Without this, a batch
        # that bumps ``num_inputs`` then connects to the new port fails because
        # the port doesn't exist at connect-time. Mirrors add_block's rewrite.
        flow_graph.rewrite()
    elif op_type == "update_states":
        block = flow_graph.get_block(kwargs.pop("instance_name"))
        set_block_state(block, kwargs.pop("state"))
    elif op_type == "add_connection":
        connect(flow_graph, **kwargs)
    elif op_type == "remove_connection":
        disconnect(flow_graph, **kwargs)
    else:
        raise ValueError(f"Unknown op_type: {op_type!r}")


def validate(flow_graph: Any) -> GrcValidation:
    flow_graph.rewrite()
    flow_graph.validate()
    valid = bool(flow_graph.is_valid())
    return GrcValidation(
        status="valid" if valid else "invalid",
        errors=[_format_error(e, m) for e, m in flow_graph.iter_error_messages()],
        native_ok=valid,
    )


# --------------------------------------------------------------------------- #
# Serialization                                                               #
# --------------------------------------------------------------------------- #


def serialize_flow_graph(flow_graph: Any) -> str:
    """Return the GRC-native YAML representation. Import is local because
    ``io.yaml`` triggers a circular import if loaded before the platform is
    warmed; ``get_platform()`` earlier in the call sequence has done that."""
    from gnuradio.grc.core.io import yaml as _grc_yaml

    return _grc_yaml.dump(flow_graph.export_data())


def serialize_raw_data(raw_data: Any) -> str:
    """Serialize a raw-data dict (GRC ``import_data`` format) to native YAML.

    Consolidates the ``gnuradio.grc.core.io.yaml`` import behind the adapter
    boundary (mirrors :func:`serialize_flow_graph` which operates on a live
    FlowGraph). Warms the platform first to avoid io.yaml's circular import.
    """
    get_platform()  # warm the platform (io.yaml circular-import guard)
    from gnuradio.grc.core.io import yaml as _grc_yaml

    return _grc_yaml.dump(raw_data)


def write_flow_graph_atomic(flow_graph: Any, path: Path) -> None:
    """Atomic write: temp file in the same directory, fsync, replace, fsync dir."""
    payload = serialize_flow_graph(flow_graph)
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


# --------------------------------------------------------------------------- #
# Save primitives (single source of truth for the save lifecycle)              #
# --------------------------------------------------------------------------- #

_logger = logging.getLogger(__name__)


def refuse_ambiguous_save_target(target_path: Path) -> None:
    """Refuse to save through a symlink or a hard-linked file.

    Saving through a symlink could write to a location the user did not
    intend. Hard-linked files share an inode, so an atomic-replace on one
    would silently clobber the other. Both cases are configuration
    mistakes and the safe behavior is to fail loud.
    """
    if not target_path.exists():
        return
    if target_path.is_symlink():
        raise OSError(f"Refusing to save through symlink: {target_path}")
    try:
        stat_result = target_path.stat()
    except OSError as exc:
        raise OSError(f"Could not stat save target {target_path}: {exc}") from exc
    if stat_result.st_nlink > 1:
        raise OSError(f"Refusing to save hard-linked graph file: {target_path}")


def write_save_backup(target_path: Path) -> Path | None:
    """Snapshot the existing file to ``.grc_agent/backups/<ts>-<hash><ext>``.

    Returns the backup path, or ``None`` when the target does not exist
    (first save).
    """
    if not target_path.exists():
        return None
    backup_dir = target_path.parent / ".grc_agent" / "backups"
    try:
        backup_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError as exc:
        raise OSError(
            f"Could not create save backup directory for {target_path}: {exc}"
        ) from exc
    with open(target_path, "rb") as f:
        old_hash = hashlib.sha256(f.read()).hexdigest()
    timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    backup_path = backup_dir / f"{timestamp}-{old_hash[:16]}{target_path.suffix}"
    if backup_path.exists():
        backup_path = backup_dir / (
            f"{timestamp}-{old_hash[:16]}-{time.time_ns()}{target_path.suffix}"
        )
    try:
        shutil.copy2(target_path, backup_path)
    except OSError as exc:
        raise OSError(f"Could not create save backup for {target_path}: {exc}") from exc
    return backup_path


@contextmanager
def exclusive_file_lock(lock_path: Path) -> Iterator[None]:
    """Acquire an exclusive ``fcntl.flock`` on ``lock_path``.

    Creates the parent directory (mode 0o700) if needed. The lock is
    released when the context exits, even on exception.
    """
    try:
        lock_path.parent.mkdir(mode=0o700, exist_ok=True)
    except OSError as exc:
        raise OSError(f"Could not create lock directory for {lock_path}: {exc}") from exc
    try:
        with lock_path.open("a", encoding="utf-8") as lock_file:
            try:
                import fcntl as _fcntl

                _fcntl.flock(lock_file.fileno(), _fcntl.LOCK_EX)
            except OSError as exc:
                raise OSError(
                    f"Could not acquire exclusive lock on {lock_path}: {exc}"
                ) from exc
            try:
                yield
            finally:
                try:
                    import fcntl as _fcntl

                    _fcntl.flock(lock_file.fileno(), _fcntl.LOCK_UN)
                except OSError:
                    _logger.warning("file_lock_release_failed path=%s", lock_path)
    except OSError:
        raise
