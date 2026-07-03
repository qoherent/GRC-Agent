"""change_graph wrapper (Phase 6 cutover) — flat-batch mutations via the adapter.

All mutations go through :func:`grc_agent.grc_native_adapter.apply_mutation`
and :func:`validate`. No dict-crawl; no ``grcc`` subprocess.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from grc_agent.domain_models import ErrorCode
from grc_agent.grc_native_adapter import (
    apply_mutation,
    validate,
)
from grc_agent.runtime.connection_ids import connection_id, parse_connection_id
from grc_agent.transaction import capture_session_state, restore_session_state

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from grc_agent.agent import ToolResult


@dataclass
class ChangeGraphContext:
    """Inputs and accumulated state shared across the seven change_graph phases.

    Built once in :func:`dispatch_flat_change_graph_batch`; each phase reads
    from it, appends to ``errors``, and increments ``ops_applied`` directly
    via ``ctx.ops_applied += 1``.
    """

    agent: Any
    fg: Any
    errors: list[dict[str, str]] = field(default_factory=list)
    ops_applied: int = 0

    raw_add_blocks: Any = None
    raw_remove_blocks: Any = None
    raw_update_params: Any = None
    raw_update_states: Any = None
    raw_add_connections: Any = None
    raw_remove_connections: Any = None

    add_blocks_list: list[Any] = field(default_factory=list)
    remove_blocks_list: list[Any] = field(default_factory=list)
    update_params_list: list[Any] = field(default_factory=list)
    update_states_list: list[Any] = field(default_factory=list)
    add_connections_list: list[Any] = field(default_factory=list)
    remove_connections_list: list[Any] = field(default_factory=list)

    new_block_names: set[str] = field(default_factory=set)
    removed_names: set[str] = field(default_factory=set)
    type_already_set: set[str] = field(default_factory=set)
    pre_edges: set[str] = field(default_factory=set)
    before_snapshot: Any = None
    before_serialized: str | None = None

    def __post_init__(self) -> None:
        """Single-pass derivation from raw inputs to derived lists/sets.

        Replaces the dispatcher's three re-iterations of ``add_blocks``
        and the parallel edges/removed-names passes.
        """
        self.add_blocks_list = list(_as_list_safe(self.raw_add_blocks))
        self.remove_blocks_list = list(_as_list_safe(self.raw_remove_blocks))
        self.update_params_list = list(_as_list_safe(self.raw_update_params))
        self.update_states_list = list(_as_list_safe(self.raw_update_states))
        self.add_connections_list = list(_as_list_safe(self.raw_add_connections))
        self.remove_connections_list = list(_as_list_safe(self.raw_remove_connections))

        self.new_block_names = {
            str(e.get("instance_name", "")).strip()
            for e in self.add_blocks_list
            if isinstance(e, dict) and str(e.get("instance_name", "")).strip()
        }
        self.removed_names = {
            str(e).strip()
            for e in self.remove_blocks_list
            if str(e).strip()
        }


def _as_list_safe(value: Any) -> list[Any]:
    """Coerce a raw input to a list, accepting None as empty.

    Wraps the existing ``_as_list`` (which records errors) with a
    no-side-effect variant for context construction where we have
    no error sink to populate.
    """
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]  # pragma: no cover - dispatcher normalizes this


def _phase_add_blocks(ctx: ChangeGraphContext) -> None:
    """Add every entry in ``ctx.add_blocks_list`` via the native adapter.

    Behavior is identical to the original inline block (lines 203-237 of
    the pre-refactor file): duplicate-name detection runs first; missing
    block_id/instance_name → ``invalid_block``; KeyError on adapter →
    ``parameter_not_found``; anything else → ``add_block_failed``.
    """
    for entry in ctx.add_blocks_list:
        if not isinstance(entry, dict):
            continue
        block_id = str(entry.get("block_id", "")).strip()
        instance_name = str(entry.get("instance_name", "")).strip()
        if not block_id or not instance_name:
            ctx.errors.append({
                "code": "invalid_block",
                "message": f"add_blocks entry needs block_id and instance_name: {entry}",
            })
            continue
        try:
            ctx.fg.get_block(instance_name)
            ctx.errors.append({
                "code": "duplicate_block_name",
                "message": f"a block named {instance_name!r} already exists",
            })
            continue
        except KeyError:
            pass
        try:
            apply_mutation(
                ctx.fg,
                "add_block",
                block_type=block_id,
                instance_name=instance_name,
                parameters=entry.get("params") or {},
                state=entry.get("state"),
            )
            ctx.ops_applied += 1
        except KeyError as exc:
            ctx.errors.append({"code": "parameter_not_found", "message": str(exc)})
        except Exception as exc:
            ctx.errors.append({"code": "add_block_failed", "message": str(exc)})


def _phase_remove_blocks(ctx: ChangeGraphContext) -> None:
    """Remove each block name; reject connection-id strings with a clear hint.

    Behavior is identical to the original inline block (lines 239-255).
    """
    for entry in ctx.remove_blocks_list:
        name = str(entry).strip()
        if not name:
            continue
        if "->" in name:
            ctx.errors.append({
                "code": "remove_block_failed",
                "message": (
                    f"You passed {name!r} to remove_blocks. This looks like a "
                    "connection ID. Connections must be removed using the "
                    "remove_connections parameter, not remove_blocks."
                ),
            })
            continue
        try:
            apply_mutation(ctx.fg, "remove_block", instance_name=name)
            ctx.ops_applied += 1
        except Exception as exc:
            ctx.errors.append({"code": "remove_block_failed", "message": str(exc)})


def _phase_update_params(ctx: ChangeGraphContext) -> None:
    """Apply each ``update_params`` entry; missing instance_name → ``invalid_update``.

    Behavior is identical to the original inline block (lines 257-272).
    """
    for entry in ctx.update_params_list:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("instance_name", "")).strip()
        params = entry.get("params") or {}
        if not name:
            ctx.errors.append({
                "code": "invalid_update",
                "message": f"update_params entry needs instance_name: {entry}",
            })
            continue
        try:
            apply_mutation(
                ctx.fg, "update_params", instance_name=name, params=params
            )
            ctx.ops_applied += 1
        except KeyError as exc:
            ctx.errors.append({"code": "parameter_not_found", "message": str(exc)})
        except Exception as exc:
            ctx.errors.append({"code": "update_params_failed", "message": str(exc)})


def _phase_auto_resolve_types(ctx: ChangeGraphContext) -> None:
    """Set ``type`` on newly-added blocks that don't have it explicit.

    Uniform rule: skip if the block already has a ``type`` set (via
    ``add_blocks`` OR ``update_params``); otherwise derive the dtype from the
    first neighbor port in ``ctx.add_connections_list`` and assign it.

    Behavior is identical to the original inline block (lines 274-298 of
    the pre-refactor file).
    """
    for name in ctx.new_block_names:
        if name in ctx.type_already_set:
            continue
        try:
            block = ctx.fg.get_block(name)
        except KeyError:
            continue
        if "type" not in block.params:
            continue
        dtype = _neighbor_dtype_for(
            ctx.fg, name, ctx.add_connections_list, ctx.new_block_names
        )
        if not dtype:
            continue
        try:
            block.params["type"].set_value(dtype)
            ctx.fg.rewrite()
        except Exception as exc:
            logger.warning(
                "Failed to auto-resolve type for block %s: %s", name, exc
            )


def _phase_update_states(ctx: ChangeGraphContext) -> None:
    """Apply each ``update_states`` entry; missing keys → invalid_state.

    Behavior is identical to the original inline block (lines 300-315 of
    the pre-refactor file).
    """
    for entry in ctx.update_states_list:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("instance_name", "")).strip()
        state = str(entry.get("state", "")).strip()
        if not name or not state:
            ctx.errors.append({
                "code": "invalid_state",
                "message": (
                    f"update_states entry needs instance_name and state: {entry}"
                ),
            })
            continue
        try:
            apply_mutation(ctx.fg, "update_states", instance_name=name, state=state)
            ctx.ops_applied += 1
        except Exception as exc:
            ctx.errors.append({"code": "update_states_failed", "message": str(exc)})


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
                "error_type": ErrorCode.STALE_REVISION,
                "errors": [{
                    "code": ErrorCode.STALE_REVISION,
                    "message": "file changed on disk; reload before editing",
                }],
            },
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

    # Collect instance names added in this batch (used by connection hint
    # and by auto-resolve).
    new_block_names: set[str] = set()
    for entry in _as_list(add_blocks, "add_blocks", errors):
        if isinstance(entry, dict):
            name = str(entry.get("instance_name", "")).strip()
            if name:
                new_block_names.add(name)

    # Capture the pre-batch edge set and the blocks being removed, so that a
    # removal which orphans another block's port can be traced causally and
    # surfaced as a validation hint (deterministic topology offloading).
    pre_edges: set[str] = {
        connection_id(c.source_block.name, c.source_port.key,
                      c.sink_block.name, c.sink_port.key)
        for c in fg.connections
    }
    removed_names: set[str] = set()
    for entry in _as_list(remove_blocks, "remove_blocks", errors):
        name = str(entry).strip()
        if name:
            removed_names.add(name)

    # Track which new blocks had `type` explicitly set by the model — via
    # EITHER add_blocks params OR an update_params entry on a new block.
    # Auto-resolve must never clobber an explicit value, and it now runs AFTER
    # update_params (so ports created by a same-batch `num_inputs` bump exist
    # when the neighbor dtype is read), so it would otherwise overwrite a type
    # set via update_params.
    type_already_set: set[str] = set()
    for entry in _as_list(add_blocks, "add_blocks", errors):
        if isinstance(entry, dict):
            params = entry.get("params")
            name = str(entry.get("instance_name", "")).strip()
            if name and isinstance(params, dict) and "type" in params:
                type_already_set.add(name)
    for entry in _as_list(update_params, "update_params", errors):
        if isinstance(entry, dict):
            params = entry.get("params")
            name = str(entry.get("instance_name", "")).strip()
            if name in new_block_names and isinstance(params, dict) and "type" in params:
                type_already_set.add(name)

    # add_blocks
    add_blocks_list = _as_list(add_blocks, "add_blocks", errors)
    add_connections_list = _as_list(add_connections, "add_connections", errors)
    for entry in add_blocks_list:
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
        name = str(entry).strip()
        if not name:
            continue
        if "->" in name:
            _record_error(
                "remove_block_failed",
                f"You passed {name!r} to remove_blocks. This looks like a connection ID. "
                "Connections must be removed using the remove_connections parameter, not remove_blocks."
            )
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

    # Auto-resolve missing `type` params on newly-added polymorphic blocks.
    # Runs AFTER update_params (and the rewrite it triggers) so that ports
    # created by a same-batch structural change — e.g. bumping `num_inputs` on
    # an adder to expose port 3 — already exist when the neighbor dtype is read.
    # Uniform rule: if a newly-added block (a) was added in this batch,
    # (b) did not have `type` set explicitly (add_blocks or update_params), and
    # (c) is connected to a block whose port dtype resolves, set its `type` to
    # that dtype so the connection validates.
    for name in new_block_names:
        if name in type_already_set:
            continue
        try:
            block = fg.get_block(name)
        except KeyError:
            continue
        if "type" not in block.params:
            continue
        dtype = _neighbor_dtype_for(fg, name, add_connections_list, new_block_names)
        if not dtype:
            continue
        try:
            block.params["type"].set_value(dtype)
            fg.rewrite()
        except Exception as exc:
            logger.warning("Failed to auto-resolve type for block %s: %s", name, exc)

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
        conn_id = str(entry).strip()
        parsed = parse_connection_id(conn_id)
        if not parsed:
            hint = ""
            if "->" not in conn_id:
                hint = f" Did you mean to pass {conn_id!r} to remove_blocks instead?"
            _record_error("invalid_connection", f"unparseable connection_id: {conn_id!r}.{hint}")
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
        agent.session.bump_revision()
        # Skip save if serialized form is unchanged (noop detection).
        try:
            after_serialized = _serialize_fg(fg)
        except Exception:
            after_serialized = None
        if agent.session.path is not None and before_serialized != after_serialized:
            try:
                agent.session.save()
            except Exception as exc:
                logger.warning("Failed to save session for change_graph: %s", exc)

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
        orphaned_hints = (
            _orphaned_port_hints(pre_edges, removed_names) if removed_names else {}
        )
        for entry in _validation_error_entries(validation_errors, type_hint, orphaned_hints):
            payload.setdefault("errors", []).append(entry)
        if not committed:
            payload["error_type"] = ErrorCode.GNU_VALIDATION_FAILED

    result = agent._payload_result("change_graph", payload)
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


def _neighbor_dtype_for(
    fg: Any,
    instance_name: str,
    add_connections: list[Any],
    new_block_names: set[str] | None = None,
) -> str | None:
    """Return the port dtype of the first neighbor block connected to
    ``instance_name`` in the batch's ``add_connections``.

    The neighbor block may be either an existing block in the graph or
    another newly-added block. We prioritize existing blocks first to
    correctly bootstrap type resolution in new block chains.
    """
    # Single pass: record the first hit from an existing block and the first
    # hit from a newly-added block; return the existing-block hit if any,
    # otherwise fall back to the new-block hit.
    from grc_agent.grc_native_adapter import port_object

    existing_dtype: str | None = None
    new_dtype: str | None = None
    for conn_entry in add_connections:
        parsed = parse_connection_id(str(conn_entry))
        if not parsed:
            continue
        other: str | None = None
        port_key: str | None = None
        if parsed["src_block"] == instance_name:
            other = parsed["dst_block"]
            port_key = str(parsed["dst_port"])
        elif parsed["dst_block"] == instance_name:
            other = parsed["src_block"]
            port_key = str(parsed["src_port"])
        if not other or not port_key:
            continue
        kind = "sink" if parsed["src_block"] == instance_name else "source"
        port = port_object(fg, other, port_key, kind=kind)
        if port is None:
            continue
        dtype = getattr(port, "dtype", None)
        if not dtype:
            continue
        if new_block_names and other in new_block_names:
            if new_dtype is None:
                new_dtype = str(dtype)
        else:
            return str(dtype)
    return existing_dtype or new_dtype


def _neighbor_port_dtype(fg: Any, block_name: str) -> str | None:
    """Return the resolved IO dtype of the port on the OTHER side of the first
    connection touching ``block_name``.

    This is the dtype the block's own ``type`` should adopt to satisfy the
    connection. Reads live native ports (post-rewrite), so the value is the
    actual neighbor dtype — not a token parsed from an error message.
    """
    try:
        block = fg.get_block(block_name)
    except Exception:
        return None
    for conn in fg.connections:
        if conn.source_block is block:
            other_port = conn.sink_port
        elif conn.sink_block is block:
            other_port = conn.source_port
        else:
            continue
        dtype = getattr(other_port, "dtype", None)
        if dtype:
            return str(dtype)
    return None


def _type_hint_for_validation(
    fg: Any,
    validation_errors: list[str],
    new_block_names: set[str],
) -> str | None:
    """If a validation error is an IO type/size mismatch and the batch
    contains a newly-added block with a ``type`` enum param, return a
    hint naming the dtype the block should adopt (the neighbor's dtype).

    The neighbor dtype is resolved from the live flowgraph ports, NOT by
    pattern-matching tokens out of the error message — that old approach
    suggested the block's own *wrong* current type (it appears first in the
    "Source IO type X does not match sink IO type Y" message) instead of the
    correct neighbor type.
    """
    if not new_block_names:
        return None
    if not any("IO type" in msg or "IO size" in msg for msg in validation_errors):
        return None
    for name in new_block_names:
        try:
            block = fg.get_block(name)
        except Exception:
            continue
        type_param = block.params.get("type")
        if type_param is None or type_param.dtype != "enum":
            continue
        opts = [str(o) for o in (type_param.options or [])]
        neighbor_dtype = _neighbor_port_dtype(fg, name)
        if neighbor_dtype and neighbor_dtype in opts:
            return (
                f"'{name}' type enum includes '{neighbor_dtype}' "
                f"(neighbor dtype is {neighbor_dtype})"
            )
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
        from grc_agent.grc_native_adapter import port_object

        src_p = port_object(fg, src_block, src_port, kind="source")
        dst_p = port_object(fg, dst_block, dst_port, kind="sink")
        if src_p is None:
            raise KeyError(f"source port {src_port!r} not on block {src_block!r}")
        if dst_p is None:
            raise KeyError(f"sink port {dst_port!r} not on block {dst_block!r}")
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
                                f"'{new_name}' type enum includes '{neighbor_dtype}' (neighbor dtype is {neighbor_dtype})"
                            )
                except Exception:
                    pass

        return "; ".join(parts) if parts else None
    except Exception:
        pass
    return None


def _orphaned_port_hints(
    pre_edges: set[str], removed_names: set[str]
) -> dict[str, str]:
    """Map orphaned block name -> causal hint.

    For each pre-batch edge that touched a removed block, the OTHER endpoint's
    port is now dangling (the removed block took its connection with it via
    GRC's native cascade). The hint names the ORPHANED block first (the subject
    of the validation error) and states the current defect + the cause, so the
    model connects "Port is not connected" to the right block. Informational
    only — never prescribes an action (no "remove"/"force"). One uniform rule;
    direction is reflected in the wording (source output vs. sink input).
    """
    hints: dict[str, str] = {}
    for edge in pre_edges:
        parsed = parse_connection_id(edge)
        if not parsed:
            continue
        src, dst = parsed["src_block"], parsed["dst_block"]
        if src in removed_names and dst not in removed_names:
            hints.setdefault(
                dst,
                f"'{dst}' input port is unconnected because it was fed by removed block '{src}'",
            )
        if dst in removed_names and src not in removed_names:
            hints.setdefault(
                src,
                f"'{src}' output port is unconnected because it fed removed block '{dst}'",
            )
    return hints


def _validation_error_entries(
    validation_errors: list[str],
    type_hint: str | None,
    orphaned_hints: dict[str, str],
) -> list[dict[str, Any]]:
    """Build model-facing gnu_validation entries with the most specific hint.

    Prefers the orphaned-port causal hint (specific to the block in the error)
    over the generic IO-type hint. Errors matching neither get no hint.
    """
    entries: list[dict[str, Any]] = []
    for msg in validation_errors:
        entry: dict[str, Any] = {"code": "gnu_validation", "message": msg}
        block_name = msg.split(": ", 1)[0] if ": " in msg else ""
        if block_name and block_name in orphaned_hints:
            entry["hint"] = orphaned_hints[block_name]
        elif type_hint:
            entry["hint"] = type_hint
        entries.append(entry)
    return entries


def _tool_error(agent: Any, message: str) -> ToolResult:
    payload = {"ok": False, "errors": [{"code": "no_flowgraph", "message": message}]}
    return agent._payload_result("change_graph", payload)


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
