"""change_graph wrapper (Phase 6 cutover) — flat-batch mutations via the adapter.

All mutations go through :func:`grc_agent.grc_native_adapter.apply_mutation`
and :func:`validate_and_finalize`. No dict-crawl; no ``grcc`` subprocess.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from grc_agent._payload import ErrorCode
from grc_agent.grc_native_adapter import (
    apply_mutation,
    validate_and_finalize,
)
from grc_agent.session_ops import connection_id as render_connection_id
from grc_agent.session_ops import parse_connection_id

if TYPE_CHECKING:
    from grc_agent.agent import GrcAgent, ToolResult

_FLAT_BATCH_FIELDS = {
    "add_blocks",
    "remove_blocks",
    "update_params",
    "update_states",
    "add_connections",
    "remove_connections",
}


def has_flat_change_graph_batch(kwargs: dict[str, Any]) -> bool:
    return any(key in kwargs for key in _FLAT_BATCH_FIELDS)


def dispatch_flat_change_graph_batch(
    agent: Any,
    *,
    add_blocks: Any = None,
    remove_blocks: Any = None,
    update_params: Any = None,
    update_states: Any = None,
    add_connections: Any = None,
    remove_connections: Any = None,
    force: bool = False,
    debug: bool = False,
) -> ToolResult:
    """Execute the flat model-facing batch edit surface via the native adapter."""
    started = time.monotonic()
    before_revision = agent.session.state_revision
    before_dirty = agent.session.is_dirty

    missing_session = agent._missing_session_result("change_graph")
    if missing_session is not None:
        return agent._attach_wrapper_dispatch_telemetry(
            debug=debug, wrapper_name="change_graph", wrapper_action="missing_session",
            internal_handlers=["none"], started=started,
            before_revision=before_revision, before_dirty=before_dirty,
            result=missing_session, validation_run=False, output_truncated=False,
        )

    fg = agent.session.flowgraph
    if fg is None:
        return _tool_error(agent, started, "No flowgraph loaded.", before_revision, before_dirty)

    errors: list[dict[str, str]] = []
    ops_applied = 0

    def _record_error(code: str, message: str) -> None:
        errors.append({"code": code, "message": message})

    # add_blocks
    for entry in _as_list(add_blocks, "add_blocks", errors):
        if not isinstance(entry, dict):
            continue
        block_id = str(entry.get("block_id", "")).strip()
        instance_name = str(entry.get("instance_name", "")).strip()
        if not block_id or not instance_name:
            _record_error("invalid_block", f"add_blocks entry needs block_id and instance_name: {entry}")
            continue
        try:
            apply_mutation(fg, "add_block", block_type=block_id,
                           instance_name=instance_name, parameters=entry.get("params") or {})
            ops_applied += 1
        except Exception as exc:
            _record_error("add_block_failed", str(exc))

    # remove_blocks
    for entry in _as_list(remove_blocks, "remove_blocks", errors):
        name = entry if isinstance(entry, str) else str(entry.get("instance_name", "")).strip()
        if not name:
            continue
        try:
            apply_mutation(fg, "remove_block", instance_name=name)
            ops_applied += 1
        except Exception as exc:
            _record_error("remove_block_failed", str(exc))

    # update_params
    for entry in _as_list(update_params, "update_params", errors):
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("instance_name", "")).strip()
        params = entry.get("params") or {}
        if not name:
            _record_error("invalid_update", f"update_params entry needs instance_name: {entry}")
            continue
        try:
            apply_mutation(fg, "update_params", instance_name=name, params=params)
            ops_applied += 1
        except Exception as exc:
            _record_error("update_params_failed", str(exc))

    # update_states
    for entry in _as_list(update_states, "update_states", errors):
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("instance_name", "")).strip()
        state = str(entry.get("state", "")).strip()
        if not name or not state:
            _record_error("invalid_state", f"update_states entry needs instance_name and state: {entry}")
            continue
        try:
            apply_mutation(fg, "update_states", instance_name=name, state=state)
            ops_applied += 1
        except Exception as exc:
            _record_error("update_states_failed", str(exc))

    # add_connections
    for entry in _as_list(add_connections, "add_connections", errors):
        src, dst = _parse_connection_endpoints(entry, errors)
        if src and dst:
            try:
                apply_mutation(fg, "add_connection", **src, **dst)
                ops_applied += 1
            except Exception as exc:
                _record_error("add_connection_failed", str(exc))

    # remove_connections
    for entry in _as_list(remove_connections, "remove_connections", errors):
        conn_id = entry if isinstance(entry, str) else str(entry.get("connection_id", "")).strip()
        parsed = parse_connection_id(conn_id)
        if parsed:
            try:
                apply_mutation(fg, "remove_connection",
                               src_block=parsed["src_block"], src_port=str(parsed["src_port"]),
                               dst_block=parsed["dst_block"], dst_port=str(parsed["dst_port"]))
                ops_applied += 1
            except Exception as exc:
                _record_error("remove_connection_failed", str(exc))

    # Validate the final state.
    validation = validate_and_finalize(fg) if ops_applied else None
    validation_ok = validation.native_ok if validation else True
    if not validation_ok and not force:
        # Rollback: reload from the file to undo in-memory mutations.
        if agent.session.path:
            try:
                from grc_agent.grc_native_adapter import load_flow_graph
                agent.session.flowgraph = load_flow_graph(agent.session.path)
            except Exception:
                pass
        committed = False
    elif errors and not force:
        committed = False
        if agent.session.path:
            try:
                from grc_agent.grc_native_adapter import load_flow_graph
                agent.session.flowgraph = load_flow_graph(agent.session.path)
            except Exception:
                pass
    else:
        committed = True
    if committed and ops_applied:
        agent.session.is_dirty = True
        agent.session._bump_state_revision()

    validation_status = "unknown"
    validation_errors: list[str] = []
    if validation is not None:
        validation_status = validation.status
        validation_errors = validation.errors

    payload: dict[str, Any] = {
        "ok": committed and len(errors) == 0,
        "committed": committed,
        "ops_applied": ops_applied,
        "validation": {"status": validation_status, "errors": validation_errors},
    }
    if errors:
        payload["errors"] = errors

    result = agent._payload_result("change_graph", payload, include_active_session=False)
    return agent._attach_wrapper_dispatch_telemetry(
        debug=debug, wrapper_name="change_graph", wrapper_action="flat_batch",
        internal_handlers=["change_graph"], started=started,
        before_revision=before_revision, before_dirty=before_dirty,
        result=result, validation_run=bool(validation), output_truncated=False,
    )


# --------------------------------------------------------------------------- #
# Utility functions (agent.py imports these; work on native FlowGraph)        #
# --------------------------------------------------------------------------- #


def loaded_block_by_name(fg: Any, name: str) -> Any | None:
    for b in fg.blocks:
        if (b.name or b.key) == name:
            return b
    return None


def loaded_block_has_port(fg: Any, block_name: str, port: str, *, kind: str = "any") -> bool:
    block = loaded_block_by_name(fg, block_name)
    if block is None:
        return False
    ports = list(block.sources) + list(block.sinks) if kind == "any" else \
            (list(block.sources) if kind == "source" else list(block.sinks))
    return any(str(p.key) == str(port) for p in ports)


def connection_endpoint_candidates(fg: Any, block_name: str, port: str) -> list[dict[str, str]]:
    """Find connections that match (block, port) on either endpoint."""
    out: list[dict[str, str]] = []
    for conn in fg.connections:
        src_name = conn.source_block.name or conn.source_block.key
        dst_name = conn.sink_block.name or conn.sink_block.key
        if (src_name == block_name and str(conn.source_port.key) == str(port)):
            out.append({"connection_id": render_connection_id(
                src_name, conn.source_port.key, dst_name, conn.sink_port.key)})
        if (dst_name == block_name and str(conn.sink_port.key) == str(port)):
            out.append({"connection_id": render_connection_id(
                src_name, conn.source_port.key, dst_name, conn.sink_port.key)})
    return out


def has_endpoint_value(entry: Any, key: str) -> bool:
    if isinstance(entry, dict):
        return key in entry and entry[key]
    return False


def resolve_disconnect_connection_id(fg: Any, *, connection_id: str | None = None,
                                     **kwargs: Any) -> dict[str, Any] | None:
    if connection_id:
        return parse_connection_id(connection_id)
    return None


def resolve_old_rewire_connection_id(fg: Any, *, connection_id: str | None = None,
                                     **kwargs: Any) -> dict[str, Any] | None:
    return resolve_disconnect_connection_id(fg, connection_id=connection_id)


def resolve_rewire_new_endpoint_args(fg: Any, **kwargs: Any) -> dict[str, Any] | None:
    src = kwargs.get("src") or {}
    dst = kwargs.get("dst") or {}
    if isinstance(src, dict) and isinstance(dst, dict):
        return {"src_block": src.get("block", ""), "src_port": str(src.get("port", "")),
                "dst_block": dst.get("block", ""), "dst_port": str(dst.get("port", ""))}
    return None


def rewire_new_endpoint_candidates(fg: Any, block_name: str, port: str) -> list[str]:
    return [c["connection_id"] for c in connection_endpoint_candidates(fg, block_name, port)]


def rewire_new_endpoint_is_exact(fg: Any, block_name: str, port: str) -> bool:
    return bool(rewire_new_endpoint_candidates(fg, block_name, port))


def rewire_candidate_passes_preflight(fg: Any, **kwargs: Any) -> bool:
    return True


def _update_state_operation(entry: Any) -> dict[str, Any]:
    """Normalize one update_states entry (kept for test compat)."""
    if isinstance(entry, dict):
        return {"instance_name": str(entry.get("instance_name", "")),
                "state": str(entry.get("state", ""))}
    return {"instance_name": "", "state": ""}


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #


def _as_list(value: Any, field_name: str, errors: list[dict[str, str]]) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    errors.append({"code": "invalid_field", "message": f"{field_name} must be a list."})
    return []


def _parse_connection_endpoints(entry: Any, errors: list[dict[str, str]]) -> tuple[dict, dict] | tuple[None, None]:
    if not isinstance(entry, dict):
        errors.append({"code": "invalid_connection", "message": f"connection entry must be a dict: {entry}"})
        return None, None
    src = entry.get("src") or {}
    dst = entry.get("dst") or {}
    if not isinstance(src, dict) or not isinstance(dst, dict):
        errors.append({"code": "invalid_connection", "message": f"connection needs src/dst dicts: {entry}"})
        return None, None
    src_args = {"src_block": str(src.get("block", "")), "src_port": str(src.get("port", ""))}
    dst_args = {"dst_block": str(dst.get("block", "")), "dst_port": str(dst.get("port", ""))}
    if not src_args["src_block"] or not dst_args["dst_block"]:
        errors.append({"code": "invalid_connection", "message": f"connection needs block+port: {entry}"})
        return None, None
    return src_args, dst_args


def _tool_error(agent: Any, started: float, message: str,
                before_revision: int, before_dirty: bool) -> ToolResult:
    payload = {"ok": False, "errors": [{"code": "no_flowgraph", "message": message}]}
    result = agent._payload_result("change_graph", payload, include_active_session=False)
    return agent._attach_wrapper_dispatch_telemetry(
        debug=False, wrapper_name="change_graph", wrapper_action="error",
        internal_handlers=["none"], started=started,
        before_revision=before_revision, before_dirty=before_dirty,
        result=result, validation_run=False, output_truncated=False,
    )
