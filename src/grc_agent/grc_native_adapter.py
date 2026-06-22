"""Phase 5 — complete native GRC adapter (load, mutate, validate, identity).

Per ``docs/refactor_plan/plan_context.md`` §5 (env facts) and §4 (no in-band
control flow). The single source of truth for GNU Radio access in the agent.

Public surface (consumed by Phase 6's cutover):

- ``get_platform()`` — lazy singleton, never imports ``gnuradio`` at module
  top-level so CI without GNU Radio can still import the module.
- ``GraphIdentity`` — file-bytes SHA-256 + per-``FlowGraph`` revision counter.
  Per the consultant's perf review, no deep-JSON hashing of the Pydantic model.
- ``load_flow_graph``, ``load_and_inspect`` — loading + Pydantic snapshot.
- ``add_block``, ``remove_block``, ``set_param``, ``set_block_state``,
  ``connect``, ``disconnect``, ``apply_mutation``, ``validate_and_finalize`` —
  the six ``change_graph`` op_types, applied to a native ``FlowGraph``.
- ``validate``, ``render_block``, ``render_connection``, ``render_parameter``,
  ``render_flow_graph``, ``classify_role`` — inspection helpers; visibility
  delegates to the single authority :mod:`grc_agent.runtime.param_filter`.
- ``serialize_flow_graph``, ``write_flow_graph_atomic`` — persistence.

``connect``/``disconnect`` consume resolved ``Port`` objects; ``apply_mutation``
and the test surface accept ``(block_name, port_key)`` and resolve internally.
"""
from __future__ import annotations

import hashlib
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from grc_agent.domain_models import (
    BlockRole,
    GrcBlock,
    GrcConnection,
    GrcFlowgraph,
    GrcParameter,
    GrcValidation,
)
from grc_agent.runtime.block_semantics import evaluated_param_hides
from grc_agent.runtime.param_filter import (
    DEFAULT_PARAM_TAB,
    EXCLUDED_PARAM_CATEGORIES,
    categories,
    keep_param,
    prominence_rank,
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
        raw_text = file_path.read_text(encoding="utf-8-sig")
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


def classify_role(block: Any) -> BlockRole:
    if getattr(block, "is_variable", False):
        return BlockRole.VARIABLE
    if getattr(block, "is_import", False):
        return BlockRole.IMPORT
    if getattr(block, "is_snippet", False):
        return BlockRole.SNIPPET
    if getattr(block, "is_virtual_or_pad", False):
        return BlockRole.VIRTUAL_OR_PAD
    if block.key == "options":
        return BlockRole.OPTIONS
    has_out = len(block.active_sources) > 0
    has_in = len(block.active_sinks) > 0
    if has_out and not has_in:
        return BlockRole.SOURCE
    if has_in and not has_out:
        return BlockRole.SINK
    if has_in and has_out:
        return BlockRole.TRANSFORM
    return BlockRole.OTHER


def _safe_evaluate(param: Any) -> Any | None:
    try:
        return param.get_evaluated()
    except Exception:
        return None


def render_parameter(block: Any, param_key: str, param: Any,
                     evaluated_hides: dict[str, str] | None = None) -> GrcParameter | None:
    """One uniform visibility filter — delegates to the bible (``keep_param``).
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
    if not keep_param(hide=hide, category=category, dtype=dtype, value=value,
                      default=default, mode="visibility"):
        return None
    evaluated = _safe_evaluate(param)
    if evaluated is not None and not isinstance(evaluated, (str, int, float, bool, list, dict)):
        evaluated = str(evaluated)
    return GrcParameter(
        name=param_key,
        dtype=dtype,
        value=value,
        evaluated_value=evaluated,
        category=category,
        hide=hide,
    )


def render_block(block: Any, flow_graph: Any | None = None) -> GrcBlock:
    evaluated_hides: dict[str, str] | None = None
    if flow_graph is not None:
        try:
            current = {k: str(p.value) for k, p in block.params.items()}
            evaluated_hides = evaluated_param_hides(block.key, current)
        except Exception:
            evaluated_hides = None

    parameters = []
    for k, p in block.params.items():
        rendered = render_parameter(block, k, p, evaluated_hides=evaluated_hides)
        if rendered is not None:
            parameters.append(rendered)
    if evaluated_hides:
        parameters.sort(key=lambda p: (prominence_rank(evaluated_hides.get(p.name, "all")), p.name))

    coordinate = None
    states = getattr(block, "states", {}) or {}
    coord = states.get("coordinate")
    if isinstance(coord, (list, tuple)) and len(coord) >= 2:
        try:
            coordinate = (float(coord[0]), float(coord[1]))
        except (TypeError, ValueError):
            coordinate = None
    return GrcBlock(
        instance_name=block.name or block.key,
        block_type=block.key,
        block_uid=str(getattr(block, "id", "") or ""),
        role=classify_role(block),
        state=str(states.get("state", "enabled")),
        parameters=parameters,
        coordinate=coordinate,
    )


def render_connection(conn: Any) -> GrcConnection:
    src = conn.source_block
    dst = conn.sink_block
    sp = conn.source_port
    dp = conn.sink_port
    return GrcConnection(
        connection_id=f"{src.name}:{sp.key}->{dst.name}:{dp.key}",
        src_block=src.name or src.key,
        src_port=sp.key,
        dst_block=dst.name or dst.key,
        dst_port=dp.key,
        dtype=getattr(sp, "dtype", None),
    )


def render_flow_graph(flow_graph: Any) -> GrcFlowgraph:
    blocks = [render_block(b, flow_graph) for b in flow_graph.blocks]
    connections = [render_connection(c) for c in flow_graph.connections]
    valid = bool(flow_graph.is_valid())
    errors = []
    if not valid:
        for _elem, message in flow_graph.iter_error_messages():
            errors.append(str(message))
    file_format = None
    grc_version = None
    options = getattr(flow_graph, "options_block", None)
    if options is not None:
        ff = options.params.get("file_format")
        if ff is not None:
            try:
                file_format = int(str(ff.value))
            except (TypeError, ValueError):
                file_format = None
        gv = options.params.get("grc_version")
        if gv is not None:
            grc_version = str(gv.value) or None
    return GrcFlowgraph(
        ok=valid,
        graph_name=options.name if options is not None else "",
        file_format=file_format,
        grc_version=grc_version,
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


def _find_block(flow_graph: Any, instance_name: str) -> Any:
    for b in flow_graph.blocks:
        if (b.name or b.key) == instance_name:
            return b
    raise KeyError(f"Block {instance_name!r} not found")


def _find_port(flow_graph: Any, block_name: str, port_key: str, *, kind: str) -> Any:
    block = _find_block(flow_graph, block_name)
    ports = block.active_sinks if kind == "sink" else block.active_sources
    for p in ports:
        if p.key == port_key:
            return p
    raise KeyError(f"{kind} port {port_key!r} not on block {block_name!r}")


def add_block(flow_graph: Any, block_type: str, instance_name: str,
              parameters: dict[str, Any]) -> Any:
    """Add a new block. Names it via ``params['id']`` (the empirically correct
    path — GRC's ``Block.name`` is a read-only property)."""
    if any((b.name or b.key) == instance_name for b in flow_graph.blocks):
        raise ValueError(
            f"duplicate_block_name: a block named {instance_name!r} already exists"
        )
    block = flow_graph.new_block(block_type)
    if block is None:
        raise KeyError(f"Block type {block_type!r} not found in catalog")
    block.params["id"].set_value(str(instance_name))
    flow_graph.rewrite()
    for k, v in (parameters or {}).items():
        if k in block.params:
            block.params[k].set_value(str(v))
    return block


def remove_block(flow_graph: Any, instance_name: str) -> None:
    # GRC has no public remove_block; mutate the internal list after disconnecting
    # any connections that touch the victim (Phase 2 experiment's answer).
    target = _find_block(flow_graph, instance_name)
    for conn in list(flow_graph.connections):
        if conn.source_block is target or conn.sink_block is target:
            flow_graph.disconnect(conn.source_port, conn.sink_port)
    for i, b in enumerate(flow_graph.blocks):
        if b is target:
            del flow_graph.blocks[i]
            return
    raise KeyError(f"Block {instance_name!r} disappeared during removal")


def set_param(block: Any, param_key: str, value: str) -> None:
    if param_key not in block.params:
        raise KeyError(f"Param {param_key!r} not in block {block.name!r}")
    block.params[param_key].set_value(str(value))


def set_block_state(block: Any, state: str) -> None:
    aliases = {"bypass": "bypassed"}
    canonical = aliases.get(state, state)
    if canonical not in {"enabled", "disabled", "bypassed"}:
        raise ValueError(f"Invalid state {state!r}; must be enabled/disabled/bypassed")
    block.state = canonical


def connect(flow_graph: Any, src_block: str, src_port: str,
            dst_block: str, dst_port: str) -> Any:
    src = _find_port(flow_graph, src_block, src_port, kind="source")
    dst = _find_port(flow_graph, dst_block, dst_port, kind="sink")
    return flow_graph.connect(src, dst)


def disconnect(flow_graph: Any, src_block: str, src_port: str,
               dst_block: str, dst_port: str) -> None:
    """Remove a single connection. Native ``flow_graph.disconnect(src, dst)``
    removes every connection from the source port (not just the named edge),
    so we locate the exact ``Connection`` object and drop it from the set.
    """
    for connection in list(flow_graph.connections):
        if (connection.source_block.name == src_block
                and connection.source_port.key == src_port
                and connection.sink_block.name == dst_block
                and connection.sink_port.key == dst_port):
            flow_graph.connections.remove(connection)
            return
    raise KeyError(
        f"connection not found: {src_block}:{src_port}->{dst_block}:{dst_port}"
    )


def apply_mutation(flow_graph: Any, op_type: str, **kwargs: Any) -> None:
    if op_type == "add_block":
        add_block(flow_graph, **kwargs)
    elif op_type == "remove_block":
        remove_block(flow_graph, **kwargs)
    elif op_type == "update_params":
        block = _find_block(flow_graph, kwargs.pop("instance_name"))
        for k, v in (kwargs.pop("params") or {}).items():
            set_param(block, k, v)
    elif op_type == "update_states":
        block = _find_block(flow_graph, kwargs.pop("instance_name"))
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
        errors=[str(m) for _e, m in flow_graph.iter_error_messages()],
        native_ok=valid,
    )


def validate_and_finalize(flow_graph: Any) -> GrcValidation:
    """One call to use after a batch of mutations. Runs ``rewrite()`` and
    ``validate()`` and returns a :class:`GrcValidation`."""
    return validate(flow_graph)


# --------------------------------------------------------------------------- #
# Serialization                                                               #
# --------------------------------------------------------------------------- #


def serialize_flow_graph(flow_graph: Any) -> str:
    """Return the GRC-native YAML representation. Import is local because
    ``io.yaml`` triggers a circular import if loaded before the platform is
    warmed; ``get_platform()`` earlier in the call sequence has done that."""
    from gnuradio.grc.core.io import yaml as _grc_yaml
    return _grc_yaml.dump(flow_graph.export_data())


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
