"""Validation helpers for the model-facing change_graph wrapper."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from grc_agent._payload import ErrorCode

if TYPE_CHECKING:
    from grc_agent.agent import GrcAgent, ToolResult

_VALID_VARIABLE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def validate_change_graph_operation_args(
    agent: "GrcAgent",
    *,
    dry_run: bool,
    operation_kind: str | None,
    target_ref: dict[str, Any] | None,
    block_id: str | None,
    candidate_id: str | None,
    instance_name: str | None,
    connection_id: str | None,
    src_block: str | None,
    src_port: int | str | None,
    dst_block: str | None,
    dst_port: int | str | None,
    new_src_block: str | None,
    new_src_port: int | str | None,
    new_dst_block: str | None,
    new_dst_port: int | str | None,
    insert_params: dict[str, Any] | None,
    detach_connections: bool | None,
    detach_connection_ids: list[str] | None,
    param_key: str | None,
    param_value: Any,
    state: str | None,
    variable_name: str | None,
    variable_value: Any,
) -> "ToolResult | None":
    if operation_kind is None:
        return None
    if operation_kind in {"clarify", "unsupported"}:
        return None

    if detach_connections is not None and not isinstance(detach_connections, bool):
        return agent._payload_result(
            "change_graph",
            {
                "ok": False,
                "dry_run": bool(dry_run),
                "operation_kind": operation_kind,
                "error_type": ErrorCode.INVALID_REQUEST,
                "message": "detach_connections must be boolean when provided.",
            },
        )
    if detach_connection_ids is not None:
        if not isinstance(detach_connection_ids, list):
            return agent._payload_result(
                "change_graph",
                {
                    "ok": False,
                    "dry_run": bool(dry_run),
                    "operation_kind": operation_kind,
                    "error_type": ErrorCode.INVALID_REQUEST,
                    "message": "detach_connection_ids must be an array of connection ids.",
                },
            )
        for connection_id in detach_connection_ids:
            if not isinstance(connection_id, str) or not connection_id.strip():
                return agent._payload_result(
                    "change_graph",
                    {
                        "ok": False,
                        "dry_run": bool(dry_run),
                        "operation_kind": operation_kind,
                        "error_type": ErrorCode.INVALID_REQUEST,
                        "message": (
                            "detach_connection_ids entries must be non-empty connection id strings."
                        ),
                    },
                )

    normalized_target_ref: dict[str, Any] | None = None
    if target_ref is not None:
        if not isinstance(target_ref, dict):
            return agent._payload_result(
                "change_graph",
                {
                    "ok": False,
                    "dry_run": bool(dry_run),
                    "operation_kind": operation_kind,
                    "error_type": ErrorCode.INVALID_REQUEST,
                    "message": "target_ref must be an object when provided.",
                },
            )
        normalized_target_ref = {
            str(key): value for key, value in target_ref.items() if isinstance(key, str)
        }
        # Accept both wrapper-era (`uid`, `instance_name`) and guarded
        # transaction-era (`block_uid`, `expected_instance_name`) references.
        target_uid = normalized_target_ref.get("uid")
        if not (isinstance(target_uid, str) and target_uid.strip()):
            target_uid = normalized_target_ref.get("block_uid")
        target_instance = normalized_target_ref.get("instance_name")
        if not (isinstance(target_instance, str) and target_instance.strip()):
            target_instance = normalized_target_ref.get("expected_instance_name")
        if not (
            isinstance(target_uid, str)
            and target_uid.strip()
            or isinstance(target_instance, str)
            and target_instance.strip()
        ):
            return agent._payload_result(
                "change_graph",
                {
                    "ok": False,
                    "dry_run": bool(dry_run),
                    "operation_kind": operation_kind,
                    "error_type": ErrorCode.INVALID_REQUEST,
                    "message": (
                        "target_ref must include at least one non-empty identifier: "
                        "`uid` or `instance_name`."
                    ),
                },
            )

    def _require(condition: bool, message: str) -> "ToolResult | None":
        if condition:
            return None
        return agent._payload_result(
            "change_graph",
            {
                "ok": False,
                "dry_run": bool(dry_run),
                "operation_kind": operation_kind,
                "error_type": ErrorCode.INVALID_REQUEST,
                "message": message,
            },
        )

    has_target = bool(normalized_target_ref or (isinstance(instance_name, str) and instance_name.strip()))

    if operation_kind == "set_param":
        missing = _require(
            has_target and isinstance(param_key, str) and param_key.strip() and param_value is not None,
            "set_param requires target_ref or instance_name plus param_key and param_value.",
        )
        return missing
    if operation_kind == "set_state":
        missing = _require(
            has_target and isinstance(state, str) and state in {"enabled", "disabled"},
            "set_state requires target_ref or instance_name plus state=enabled|disabled.",
        )
        return missing
    if operation_kind == "add_variable":
        variable_value_present = (
            variable_value is not None
            and (not isinstance(variable_value, str) or bool(variable_value.strip()))
        )
        if isinstance(variable_name, str) and variable_name.strip():
            if _VALID_VARIABLE_NAME_RE.fullmatch(variable_name.strip()) is None:
                return agent._payload_result(
                    "change_graph",
                    {
                        "ok": False,
                        "dry_run": bool(dry_run),
                        "operation_kind": operation_kind,
                        "error_type": ErrorCode.INVALID_REQUEST,
                        "message": (
                            "add_variable requires variable_name to be a valid identifier "
                            "(letters/digits/underscore, not starting with a digit)."
                        ),
                    },
                )
        missing = _require(
            isinstance(variable_name, str) and variable_name.strip() and variable_value_present,
            "add_variable requires variable_name and variable_value.",
        )
        return missing
    if operation_kind == "disconnect":
        has_new_endpoints = any(
            value is not None and (not isinstance(value, str) or value.strip())
            for value in (new_src_block, new_src_port, new_dst_block, new_dst_port)
        )
        if has_new_endpoints:
            return agent._payload_result(
                "change_graph",
                {
                    "ok": False,
                    "dry_run": bool(dry_run),
                    "operation_kind": operation_kind,
                    "error_type": ErrorCode.INVALID_REQUEST,
                    "message": (
                        "disconnect does not accept rewire fields. "
                        "Use operation_kind='rewire' for new endpoint arguments."
                    ),
                },
            )
        has_endpoint_hint = any(
            value is not None and (not isinstance(value, str) or value.strip())
            for value in (src_block, src_port, dst_block, dst_port)
        )
        return _require(
            (isinstance(connection_id, str) and connection_id.strip()) or has_endpoint_hint,
            "disconnect requires connection_id or endpoint hints (src_block/src_port/dst_block/dst_port).",
        )
    if operation_kind == "rewire":
        has_old_endpoint_hint = any(
            value is not None and (not isinstance(value, str) or value.strip())
            for value in (src_block, src_port, dst_block, dst_port)
        )
        has_new_source_hint = any(
            value is not None and (not isinstance(value, str) or value.strip())
            for value in (new_src_block, new_src_port)
        )
        has_new_destination_hint = any(
            value is not None and (not isinstance(value, str) or value.strip())
            for value in (new_dst_block, new_dst_port)
        )
        return _require(
            ((isinstance(connection_id, str) and connection_id.strip()) or has_old_endpoint_hint)
            and has_new_source_hint
            and has_new_destination_hint,
            (
                "rewire requires connection_id or old endpoint hints plus "
                "exact new endpoints or bounded hints for both new source and new destination."
            ),
        )
    if operation_kind == "insert_block":
        normalized_block_id = block_id.strip() if isinstance(block_id, str) and block_id.strip() else None
        normalized_candidate_id = (
            candidate_id.strip() if isinstance(candidate_id, str) and candidate_id.strip() else None
        )
        if (
            normalized_block_id is not None
            and normalized_candidate_id is not None
            and normalized_block_id != normalized_candidate_id
        ):
            return agent._payload_result(
                "change_graph",
                {
                    "ok": False,
                    "dry_run": bool(dry_run),
                    "operation_kind": operation_kind,
                    "error_type": ErrorCode.INVALID_REQUEST,
                    "message": (
                        "insert_block received conflicting block_id and candidate_id. "
                        "Provide one catalog id or matching values for both."
                    ),
                },
            )
        if insert_params is not None and not isinstance(insert_params, dict):
            return agent._payload_result(
                "change_graph",
                {
                    "ok": False,
                    "dry_run": bool(dry_run),
                    "operation_kind": operation_kind,
                    "error_type": ErrorCode.INVALID_REQUEST,
                    "message": "insert_params must be an object when provided.",
                },
            )
        return _require(
            isinstance(connection_id, str)
            and connection_id.strip()
            and isinstance(instance_name, str)
            and instance_name.strip()
            and (normalized_block_id is not None or normalized_candidate_id is not None),
            "insert_block requires connection_id, block_id (or candidate_id), and instance_name.",
        )
    if operation_kind == "remove_block":
        if detach_connections is not None and not isinstance(detach_connections, bool):
            return agent._payload_result(
                "change_graph",
                {
                    "ok": False,
                    "dry_run": bool(dry_run),
                    "operation_kind": operation_kind,
                    "error_type": ErrorCode.INVALID_REQUEST,
                    "message": "detach_connections must be boolean when provided.",
                },
            )
        return _require(has_target, "remove_block requires instance_name or guarded target_ref.")
    if detach_connections is not None or detach_connection_ids is not None:
        return agent._payload_result(
            "change_graph",
            {
                "ok": False,
                "dry_run": bool(dry_run),
                "operation_kind": operation_kind,
                "error_type": ErrorCode.INVALID_REQUEST,
                "message": (
                    "detach_connections and detach_connection_ids are only supported for remove_block."
                ),
            },
        )
    if operation_kind == "auto_insert":
        return _require(
            isinstance(connection_id, str) and connection_id.strip(),
            "auto_insert requires connection_id.",
        )
    if operation_kind in {"new_grc", "load_grc", "save_graph"}:
        return agent._payload_result(
            "change_graph",
            {
                "ok": False,
                "dry_run": bool(dry_run),
                "operation_kind": operation_kind,
                "error_type": ErrorCode.UNSUPPORTED_OP,
                "message": "change_graph is mutation-only. Use explicit lifecycle wrappers for save/load.",
            },
        )
    # Keep unused operation fields referenced so static checks do not regress silently.
    _ = (src_block, src_port, dst_block, dst_port)
    return None


def canonicalize_change_graph_target_ref(
    agent: "GrcAgent",
    *,
    dry_run: bool,
    operation_kind: str | None,
    target_ref: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, "ToolResult | None"]:
    if target_ref is None:
        return None, None
    if not isinstance(target_ref, dict):
        return None, agent._payload_result(
            "change_graph",
            {
                "ok": False,
                "dry_run": bool(dry_run),
                "operation_kind": operation_kind,
                "error_type": ErrorCode.INVALID_REQUEST,
                "message": "target_ref must be an object when provided.",
            },
        )

    normalized = {str(key): value for key, value in target_ref.items() if isinstance(key, str)}
    alias_map = {
        "uid": "block_uid",
        "instance_name": "expected_instance_name",
        "block_type": "expected_block_type",
        "state_revision": "base_state_revision",
    }
    canonical_fields = (
        "block_uid",
        "expected_instance_name",
        "expected_block_type",
        "base_state_revision",
    )
    allowed_fields = set(canonical_fields) | set(alias_map.keys())
    unknown_fields = sorted(key for key in normalized if key not in allowed_fields)
    if unknown_fields:
        return None, agent._payload_result(
            "change_graph",
            {
                "ok": False,
                "dry_run": bool(dry_run),
                "operation_kind": operation_kind,
                "error_type": ErrorCode.INVALID_REQUEST,
                "message": (
                    "target_ref contains unsupported keys: "
                    + ", ".join(unknown_fields)
                    + ". Allowed keys are guarded "
                    "(block_uid, expected_instance_name, expected_block_type, base_state_revision) "
                    "or wrapper-era aliases (uid, instance_name, block_type, state_revision)."
                ),
            },
        )

    canonical: dict[str, Any] = {}
    for alias_key, canonical_key in alias_map.items():
        canonical_value = normalized.get(canonical_key)
        alias_value = normalized.get(alias_key)
        if canonical_value is not None and alias_value is not None and canonical_value != alias_value:
            return None, agent._payload_result(
                "change_graph",
                {
                    "ok": False,
                    "dry_run": bool(dry_run),
                    "operation_kind": operation_kind,
                    "error_type": ErrorCode.INVALID_REQUEST,
                    "message": (
                        f"target_ref has conflicting values for {canonical_key!r} and its "
                        f"alias {alias_key!r}."
                    ),
                },
            )
        if canonical_value is not None:
            canonical[canonical_key] = canonical_value
        elif alias_value is not None:
            canonical[canonical_key] = alias_value

    missing_fields = [field for field in canonical_fields if field not in canonical]
    if missing_fields:
        return None, agent._payload_result(
            "change_graph",
            {
                "ok": False,
                "dry_run": bool(dry_run),
                "operation_kind": operation_kind,
                "error_type": ErrorCode.INVALID_REQUEST,
                "message": (
                    "target_ref must include guarded fields "
                    "(block_uid, expected_instance_name, expected_block_type, base_state_revision). "
                    "Missing: " + ", ".join(missing_fields)
                ),
            },
        )

    if not isinstance(canonical["block_uid"], str) or not canonical["block_uid"].strip():
        return None, agent._payload_result(
            "change_graph",
            {
                "ok": False,
                "dry_run": bool(dry_run),
                "operation_kind": operation_kind,
                "error_type": ErrorCode.INVALID_REQUEST,
                "message": "target_ref.block_uid must be a non-empty string.",
            },
        )
    if (
        not isinstance(canonical["expected_instance_name"], str)
        or not canonical["expected_instance_name"].strip()
    ):
        return None, agent._payload_result(
            "change_graph",
            {
                "ok": False,
                "dry_run": bool(dry_run),
                "operation_kind": operation_kind,
                "error_type": ErrorCode.INVALID_REQUEST,
                "message": "target_ref.expected_instance_name must be a non-empty string.",
            },
        )
    if (
        not isinstance(canonical["expected_block_type"], str)
        or not canonical["expected_block_type"].strip()
    ):
        return None, agent._payload_result(
            "change_graph",
            {
                "ok": False,
                "dry_run": bool(dry_run),
                "operation_kind": operation_kind,
                "error_type": ErrorCode.INVALID_REQUEST,
                "message": "target_ref.expected_block_type must be a non-empty string.",
            },
        )
    base_state_revision = canonical["base_state_revision"]
    if not isinstance(base_state_revision, int) or isinstance(base_state_revision, bool):
        return None, agent._payload_result(
            "change_graph",
            {
                "ok": False,
                "dry_run": bool(dry_run),
                "operation_kind": operation_kind,
                "error_type": ErrorCode.INVALID_REQUEST,
                "message": "target_ref.base_state_revision must be an integer.",
            },
        )

    canonical["block_uid"] = canonical["block_uid"].strip()
    canonical["expected_instance_name"] = canonical["expected_instance_name"].strip()
    canonical["expected_block_type"] = canonical["expected_block_type"].strip()
    return canonical, None
