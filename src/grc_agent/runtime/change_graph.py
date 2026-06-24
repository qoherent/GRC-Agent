"""change_graph wrapper (Phase 6 cutover) — flat-batch mutations via the adapter.

All mutations go through :func:`grc_agent.grc_native_adapter.apply_mutation`
and :func:`validate`. No dict-crawl; no ``grcc`` subprocess.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from grc_agent.domain_models import ErrorCode
from grc_agent.grc_native_adapter import (
    apply_mutation,
    validate,
)
from grc_agent.runtime.connection_ids import parse_connection_id
from grc_agent.transaction import capture_session_state, restore_session_state

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from grc_agent.agent import ToolResult


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
) -> ToolResult:
    """Execute the flat model-facing batch edit surface via the native adapter."""

    missing_session = agent._missing_session_result("change_graph")
    if missing_session is not None:
        return missing_session

    fg = agent.session.flowgraph
    if fg is None:
        return _tool_error(agent, "No flowgraph loaded.")

    integrity = agent.session.file_integrity_state()
    if integrity.get("externally_modified"):
        return agent._payload_result(
            "change_graph",
            {
                "ok": False,
                "committed": False,
                "ops_applied": 0,
                "error_type": "stale_revision",
                "file_integrity": integrity,
                "errors": [
                    {"code": "stale_revision", "message": "file changed on disk; reload before editing"}
                ],
            },
            include_active_session=False,
        )

    errors: list[dict[str, str]] = []
    ops_applied = 0

    # Snapshot serialized form before any mutation to detect true no-ops.
    from grc_agent.grc_native_adapter import serialize_flow_graph as _serialize_fg

    before_serialized: str | None = None
    if agent.session.path is not None:
        try:
            before_serialized = _serialize_fg(fg)
        except Exception:
            before_serialized = None

    # Capture a pre-batch snapshot for rollback. Uses GRC-native
    # export_data/import_data (not file reload) so unsaved dirty edits
    # are preserved on rollback.
    before_snapshot = capture_session_state(agent.session)

    def _record_error(code: str, message: str, *, hint: str | None = None) -> None:
        entry: dict[str, str] = {"code": code, "message": message}
        if hint:
            entry["hint"] = hint
        errors.append(entry)

    # Collect instance names added in this batch (used by connection hint).
    new_block_names: set[str] = set()
    for entry in _as_list(add_blocks, "add_blocks", errors):
        if isinstance(entry, dict):
            name = str(entry.get("instance_name", "")).strip()
            if name:
                new_block_names.add(name)

    # add_blocks
    for entry in _as_list(add_blocks, "add_blocks", errors):
        if not isinstance(entry, dict):
            continue
        block_id = str(entry.get("block_id", "")).strip()
        instance_name = str(entry.get("instance_name", "")).strip()
        if not block_id or not instance_name:
            _record_error(
                "invalid_block", f"add_blocks entry needs block_id and instance_name: {entry}"
            )
            continue
        # Duplicate name detection: GRC allows duplicate names but they
        # cause validation chaos. Reject here with a clear error.
        try:
            fg.get_block(instance_name)
            _record_error("duplicate_block_name", f"a block named {instance_name!r} already exists")
            continue
        except KeyError:
            pass
        try:
            apply_mutation(
                fg,
                "add_block",
                block_type=block_id,
                instance_name=instance_name,
                parameters=entry.get("params") or {},
                state=entry.get("state"),
            )
            ops_applied += 1
        except KeyError as exc:
            _record_error("parameter_not_found", str(exc))
        except Exception as exc:
            _record_error("add_block_failed", str(exc))

    # remove_blocks
    for entry in _as_list(remove_blocks, "remove_blocks", errors):
        name = str(entry).strip() if not isinstance(entry, dict) else str(entry.get("instance_name", "")).strip()
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
        except KeyError as exc:
            _record_error("parameter_not_found", str(exc))
        except Exception as exc:
            _record_error("update_params_failed", str(exc))

    # update_states
    for entry in _as_list(update_states, "update_states", errors):
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("instance_name", "")).strip()
        state = str(entry.get("state", "")).strip()
        if not name or not state:
            _record_error(
                "invalid_state", f"update_states entry needs instance_name and state: {entry}"
            )
            continue
        try:
            apply_mutation(fg, "update_states", instance_name=name, state=state)
            ops_applied += 1
        except Exception as exc:
            _record_error("update_states_failed", str(exc))

    # remove_connections (MUST run before add_connections so inline-insert
    # doesn't create a transient double-upstream that GRC rejects).
    # Idempotent: if the edge is already gone (e.g. cascaded by a prior
    # remove_block), skip silently — the desired state is already achieved.
    for entry in _as_list(remove_connections, "remove_connections", errors):
        conn_id = entry if isinstance(entry, str) else str(entry.get("connection_id", "")).strip()
        parsed = parse_connection_id(conn_id)
        if not parsed:
            _record_error("invalid_connection", f"unparseable connection_id: {conn_id!r}")
            continue
        try:
            apply_mutation(
                fg,
                "remove_connection",
                src_block=parsed["src_block"],
                src_port=str(parsed["src_port"]),
                dst_block=parsed["dst_block"],
                dst_port=str(parsed["dst_port"]),
            )
            ops_applied += 1
        except KeyError:
            pass
        except Exception as exc:
            _record_error("remove_connection_failed", str(exc))

    # add_connections (flat strings: "src:port->dst:port")
    for entry in _as_list(add_connections, "add_connections", errors):
        parsed = parse_connection_id(str(entry))
        if not parsed:
            _record_error("invalid_connection", f"unparseable connection: {entry!r}")
            continue
        try:
            apply_mutation(
                fg,
                "add_connection",
                src_block=parsed["src_block"],
                src_port=str(parsed["src_port"]),
                dst_block=parsed["dst_block"],
                dst_port=str(parsed["dst_port"]),
            )
            ops_applied += 1
        except Exception as exc:
            hint = _connection_dtype_hint(
                fg,
                parsed["src_block"],
                str(parsed["src_port"]),
                parsed["dst_block"],
                str(parsed["dst_port"]),
                new_block_names,
            )
            _record_error("add_connection_failed", str(exc), hint=hint)

    # Validate the final state.
    validation = validate(fg) if ops_applied else None
    validation_ok = validation.native_ok if validation else True
    if not validation_ok and not force:
        _restore_snapshot(agent, before_snapshot)
        committed = False
    elif errors:
        # Adapter errors (unknown param, missing block, etc.) cannot be bypassed
        # by force — force only suppresses native-validation failures.
        _restore_snapshot(agent, before_snapshot)
        committed = False
    else:
        committed = True
    if committed and ops_applied:
        agent.session.is_dirty = True
        agent.session._bump_state_revision()
        # Skip save if serialized form is unchanged (noop detection).
        try:
            after_serialized = _serialize_fg(fg)
        except Exception:
            after_serialized = None
        if agent.session.path is not None and before_serialized != after_serialized:
            try:
                agent.session.save()
            except Exception:
                pass

    validation_errors: list[str] = []
    validation_native_ok = True
    if validation is not None:
        validation_errors = validation.errors
        validation_native_ok = bool(validation.native_ok)

    payload: dict[str, Any] = {
        "ok": committed and not errors,
    }
    if not committed and "error_type" not in payload:
        payload["error_type"] = ErrorCode.TOOL_CALL_INVALID
    if errors:
        payload["errors"] = errors
    # Surface validation errors when the graph is invalid (committed via
    # force=True, or rolled back). The model needs to know the graph is
    # invalid so it can decide whether to fix the issue or set force=true.
    if validation_errors and not validation_native_ok:
        type_hint = _type_hint_for_validation(
            fg, validation_errors, new_block_names
        )
        for msg in validation_errors:
            entry: dict[str, Any] = {"code": "gnu_validation", "message": msg}
            if type_hint:
                entry["hint"] = type_hint
            payload.setdefault("errors", []).append(entry)
        if not committed:
            payload["error_type"] = ErrorCode.GNU_VALIDATION_FAILED

    result = agent._payload_result("change_graph", payload, include_active_session=False)
    return result


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


def _type_hint_for_validation(
    fg: Any,
    validation_errors: list[str],
    new_block_names: set[str],
) -> str | None:
    """If a validation error is an IO type/size mismatch and the batch
    contains a newly-added block with a ``type`` enum param, return a
    hint suggesting the matching enum value.
    """
    if not new_block_names:
        return None
    is_io_mismatch = any(
        "IO type" in msg or "IO size" in msg for msg in validation_errors
    )
    if not is_io_mismatch:
        return None
    for name in new_block_names:
        try:
            block = fg.get_block(name)
        except Exception:
            continue
        type_param = block.params.get("type")
        if type_param is None:
            continue
        if type_param.dtype != "enum":
            continue
        opts = list(type_param.options or [])
        for msg in validation_errors:
            for dtype, value in [
                ("float", "float"),
                ("complex", "complex"),
                ("int", "int"),
                ("short", "short"),
            ]:
                if dtype in msg and value in opts:
                    return f"Set type='{value}' on '{name}'"
    return None


def _connection_dtype_hint(
    fg: Any,
    src_block: str,
    src_port: str,
    dst_block: str,
    dst_port: str,
    new_block_names: set[str] | None = None,
) -> str | None:
    """Extract source/sink dtype info for a failed connection attempt.

    Returns a human-readable hint so the model can repair its next call, or
    ``None`` if port resolution fails.

    If ``new_block_names`` is provided and one of the endpoint blocks was
    added in the same batch, inspect its ``type`` enum param (if any) and
    append a hint suggesting which enum value would match the neighbor's
    dtype. This is the uniform rule: every freshly-added block whose
    connection failed on dtype is a candidate for a ``type`` adjustment,
    and the matching option (if any) is found mechanically from the enum.
    """
    try:
        from grc_agent.grc_native_adapter import _find_port

        src_p = _find_port(fg, src_block, src_port, kind="source")
        dst_p = _find_port(fg, dst_block, dst_port, kind="sink")
        src_dtype = getattr(src_p, "dtype", None)
        dst_dtype = getattr(dst_p, "dtype", None)
        parts: list[str] = []
        if src_dtype:
            parts.append(f"Source IO type: {src_dtype}")
        if dst_dtype:
            parts.append(f"Sink IO type: {dst_dtype}")

        if new_block_names and (src_block in new_block_names or dst_block in new_block_names):
            new_name = src_block if src_block in new_block_names else dst_block
            neighbor_dtype = dst_dtype if src_block in new_block_names else src_dtype
            if neighbor_dtype:
                try:
                    block = fg.get_block(new_name)
                    type_param = block.params.get("type")
                    if type_param is not None and type_param.dtype == "enum":
                        opts = list(type_param.options or [])
                        if neighbor_dtype in opts:
                            parts.append(
                                f"Set type='{neighbor_dtype}' on '{new_name}'"
                            )
                except Exception:
                    pass

        return "; ".join(parts) if parts else None
    except Exception:
        pass
    return None


def _tool_error(agent: Any, message: str) -> ToolResult:
    payload = {"ok": False, "errors": [{"code": "no_flowgraph", "message": message}]}
    return agent._payload_result("change_graph", payload, include_active_session=False)


def _restore_snapshot(agent: Any, snapshot: Any) -> str:
    """Restore session from a pre-batch snapshot via GRC-native import_data.

    Returns ``"complete"`` on success or ``"failed"`` if the restore itself
    raised. Never silently swallows the failure.
    """
    try:
        restore_session_state(agent.session, snapshot)
        return "complete"
    except Exception as exc:
        logger.error("change_graph rollback failed: %s", exc)
        return "failed"
