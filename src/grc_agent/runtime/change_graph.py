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
        if not isinstance(entry, dict):
            _record_error("invalid_field", f"remove_blocks entry must be an object: {entry}")
            continue
        name = str(entry.get("instance_name", "")).strip()
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

    # add_connections
    for entry in _as_list(add_connections, "add_connections", errors):
        src, dst = _parse_connection_endpoints(entry, errors)
        if src and dst:
            try:
                apply_mutation(fg, "add_connection", **src, **dst)
                ops_applied += 1
            except Exception as exc:
                hint = _connection_dtype_hint(
                    fg,
                    src["src_block"],
                    src["src_port"],
                    dst["dst_block"],
                    dst["dst_port"],
                )
                _record_error("add_connection_failed", str(exc), hint=hint)

    # Validate the final state.
    validation = validate(fg) if ops_applied else None
    validation_ok = validation.native_ok if validation else True
    native_validation_failure = False
    rollback_status = "none"
    if not validation_ok and not force:
        native_validation_failure = True
        rollback_status = _restore_snapshot(agent, before_snapshot)
        committed = False
    elif errors:
        # Adapter errors (unknown param, missing block, etc.) cannot be bypassed
        # by force — force only suppresses native-validation failures.
        committed = False
        rollback_status = _restore_snapshot(agent, before_snapshot)
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
        "committed": committed,
    }
    if not committed:
        payload["ops_applied"] = 0
        if errors and "error_type" not in payload:
            payload["error_type"] = ErrorCode.TOOL_CALL_INVALID
    if errors:
        payload["errors"] = errors
    # Surface validation errors whenever the graph is invalid (committed
    # via force=True, or rolled back). The model must know the graph is
    # invalid regardless of commit status — `committed: true` means the
    # batch applied; `errors` with code gnu_validation means the result is
    # not GRC-valid.
    if validation_errors and not validation_native_ok:
        for msg in validation_errors:
            payload.setdefault("errors", []).append(
                {"code": "gnu_validation", "message": msg}
            )
        if not committed:
            payload["error_type"] = ErrorCode.GNU_VALIDATION_FAILED
    if native_validation_failure and rollback_status == "failed":
        payload["rollback_failed"] = True

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


def _parse_connection_endpoints(
    entry: Any, errors: list[dict[str, str]]
) -> tuple[dict, dict] | tuple[None, None]:
    if not isinstance(entry, dict):
        errors.append(
            {"code": "invalid_connection", "message": f"connection entry must be a dict: {entry}"}
        )
        return None, None
    src = entry.get("src") or {}
    dst = entry.get("dst") or {}
    if not isinstance(src, dict) or not isinstance(dst, dict):
        errors.append(
            {"code": "invalid_connection", "message": f"connection needs src/dst dicts: {entry}"}
        )
        return None, None
    src_args = {"src_block": str(src.get("block", "")), "src_port": str(src.get("port", ""))}
    dst_args = {"dst_block": str(dst.get("block", "")), "dst_port": str(dst.get("port", ""))}
    if not src_args["src_block"] or not dst_args["dst_block"]:
        errors.append(
            {"code": "invalid_connection", "message": f"connection needs block+port: {entry}"}
        )
        return None, None
    return src_args, dst_args


def _connection_dtype_hint(
    fg: Any,
    src_block: str,
    src_port: str,
    dst_block: str,
    dst_port: str,
) -> str | None:
    """Extract source/sink dtype info for a failed connection attempt.

    Returns a human-readable hint so the model can repair its next call, or
    ``None`` if port resolution fails.
    """
    try:
        from grc_agent.grc_native_adapter import _find_port

        src_p = _find_port(fg, src_block, src_port, kind="source")
        dst_p = _find_port(fg, dst_block, dst_port, kind="sink")
        src_dtype = getattr(src_p, "dtype", None)
        dst_dtype = getattr(dst_p, "dtype", None)
        if src_dtype or dst_dtype:
            parts = []
            if src_dtype:
                parts.append(f"Source IO type: {src_dtype}")
            if dst_dtype:
                parts.append(f"Sink IO type: {dst_dtype}")
            return "; ".join(parts)
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
