import functools
import hashlib
import os
import re
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Any

import sqlite_vec
from openai import OpenAI

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
        return "float"

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
                else:
                    ports = (
                        other_block.active_sources
                        if own_direction == "inputs"
                        else other_block.active_sinks
                    )
                    for prt in ports:
                        if str(prt.key) == str(port_key):
                            dtype = getattr(prt, "dtype", None)
                            if dtype:
                                dtype_str = str(dtype)
                                if new_block_names and other in new_block_names:
                                    if new_dtype is None:
                                        new_dtype = dtype_str
                                else:
                                    return dtype_str
            except KeyError:
                continue

    if new_dtype:
        return new_dtype
    return "float"


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
    category = getattr(param, "category", "") or ""
    dtype = getattr(param, "dtype", "") or ""
    value = str(param.value)
    default = str(getattr(param, "default", ""))

    if dtype == "id" or param_key == "showports" or param_key.startswith("bus_structure_"):
        return False
    if hide == "all":
        return False
    if category in ("Advanced", "Config"):
        return False
    if dtype == "gui_hint":
        return False

    if mode != "overview":
        return True

    # Stage B Parameter visibility rules
    if hide == "none":
        return True

    is_type_controlling = param_key in type_controlling_params(block.key)
    # Port-count-controlling params are deliberately excluded here: legacy
    # (param_filter.py) only treats type-controlling params and
    # generate_options as structural for the Stage B keep rule.
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


def write_flow_graph_atomic(flow_graph: Any, path: Path) -> None:
    from gnuradio.grc.core.io import yaml as _grc_yaml

    payload = _grc_yaml.dump(flow_graph.export_data())
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


def set_param(block: Any, param_key: str, value: str) -> None:
    if param_key not in block.params:
        raise KeyError(f"Param {param_key!r} not in block {block.name!r}")
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
            for k, p in b.params.items():
                if str(p.value) == "auto":
                    is_add = b.name in new_block_names
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
        target_path = Path(flow_graph.grc_file_path).resolve()
        if target_path.exists():
            if target_path.is_symlink():
                raise OSError(f"Refusing to save through symlink: {target_path}")
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

        lock_path = target_path.parent / ".grc_agent" / (target_path.name + ".lock")
        lock_path.parent.mkdir(mode=0o700, exist_ok=True)

        with lock_path.open("a", encoding="utf-8") as lock_file:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                write_flow_graph_atomic(flow_graph, target_path)
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
    from grc_agent.settings import load_settings
    cfg = load_settings()
    is_openrouter = (cfg.get("provider") == "openrouter")

    if is_openrouter:
        model = os.getenv("OPENROUTER_EMBEDDING_MODEL", "text-embedding-3-small")
        db_name = f"{domain}_openrouter.db"
    else:
        model = os.getenv("OLLAMA_EMBEDDING_MODEL", "embeddinggemma:latest")
        db_name = f"{domain}_ollama.db"

    db_path = vectors_dir() / db_name
    return str(db_path), model


def _embed_endpoint() -> tuple[str, str]:
    """Shared base_url/api_key selection for both query- and document-side
    embedding calls."""
    from grc_agent.settings import load_settings
    cfg = load_settings()
    is_openrouter = (cfg.get("provider") == "openrouter")

    if is_openrouter:
        return "https://openrouter.ai/api/v1", os.getenv("OPENROUTER_API_KEY", "")
    return "http://localhost:11434/v1", "not-needed"


def embed_query(query: str) -> list[float]:
    from grc_agent.settings import load_settings
    cfg = load_settings()
    is_openrouter = (cfg.get("provider") == "openrouter")

    _, model = get_db_and_model("catalog")
    base_url, api_key = _embed_endpoint()

    client = OpenAI(base_url=base_url, api_key=api_key)
    response = client.embeddings.create(
        model=model, input="task: search result | query: " + query if not is_openrouter else query
    )
    return response.data[0].embedding


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
    is_openrouter = (cfg.get("provider") == "openrouter")

    base_url, api_key = _embed_endpoint()
    body = text if is_openrouter else _DOCUMENT_PREFIX + text

    client = OpenAI(base_url=base_url, api_key=api_key)
    response = client.embeddings.create(model=model, input=body)
    return response.data[0].embedding


def _ensure_db_built(domain: str, db_path: str, model: str) -> None:
    if os.path.exists(db_path):
        # Verify schema dimension matches model embedding dimension to handle model/backend swaps
        try:
            conn = sqlite3.connect(db_path)
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            sql_row = conn.execute(
                f"SELECT sql FROM sqlite_master WHERE name = '{domain}_idx'"
            ).fetchone()
            conn.close()
            if sql_row and sql_row[0]:
                match = re.search(r"float\[(\d+)\]", sql_row[0])
                if match:
                    db_dim = int(match.group(1))
                    dummy_emb = embed_document("test", model)
                    if len(dummy_emb) != db_dim:
                        print(
                            f"[grc-agent] Vector DB dimension mismatch: DB has {db_dim}, model has {len(dummy_emb)}. Rebuilding..."
                        )
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

    print(
        f"[grc-agent] {domain} vector DB not found or dimension mismatch — building it now "
        f"(first run only, may take a few minutes)..."
    )
    from grc_agent import ingest

    if domain == "catalog":
        ingest.ingest_catalog(db_path, model)
    else:
        ingest.ingest_docs(db_path, model)
    print(f"[grc-agent] {domain} vector DB build complete: {db_path}")


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
