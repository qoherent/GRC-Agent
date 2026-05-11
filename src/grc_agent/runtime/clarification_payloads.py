"""Executable clarification payload builders shared by wrapper handlers."""

from __future__ import annotations

import copy
from typing import Any

from grc_agent.runtime.clarification import ClarificationOption, ClarificationRequest
from grc_agent.session_ops import connection_id as render_connection_id


def connection_clarification_payload(
    agent: Any,
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    labels = ("A", "B", "C")
    options: list[ClarificationOption] = []
    revision = agent.session.state_revision
    for label, candidate in zip(labels, candidates, strict=False):
        connection_id = candidate["connection_id"]
        options.append(
            ClarificationOption(
                label=label,
                title=connection_id,
                description=(
                    f"{candidate['src_block']}:{candidate['src_port']} -> "
                    f"{candidate['dst_block']}:{candidate['dst_port']}"
                ),
                tool_name="remove_connection",
                tool_args={"connection_id": connection_id},
                metadata={
                    "state_revision": revision,
                    "connection_id": connection_id,
                },
            )
        )
    request = ClarificationRequest(
        kind="connection_disambiguation",
        question="Multiple existing connections match. Choose the one to remove.",
        options=options,
        state_revision=revision,
    )
    payload = request.to_dict()
    payload.update(
        {
            "ok": False,
            "message": "Multiple existing connections match the provided endpoints.",
            "error_type": "ambiguous_connection",
        }
    )
    return payload


def rewire_new_endpoint_clarification_payload(
    agent: Any,
    *,
    old_connection_id: str,
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    revision = agent.session.state_revision
    options: list[ClarificationOption] = []
    for label, candidate in zip(("A", "B", "C"), candidates, strict=False):
        new_connection_id = render_connection_id(
            candidate["new_src_block"],
            candidate["new_src_port"],
            candidate["new_dst_block"],
            candidate["new_dst_port"],
        )
        options.append(
            ClarificationOption(
                label=label,
                title=new_connection_id,
                description=f"replace {old_connection_id} with {new_connection_id}",
                tool_name="rewire_connection",
                tool_args={
                    "old_connection_id": old_connection_id,
                    "new_src_block": candidate["new_src_block"],
                    "new_src_port": candidate["new_src_port"],
                    "new_dst_block": candidate["new_dst_block"],
                    "new_dst_port": candidate["new_dst_port"],
                },
                metadata={
                    "state_revision": revision,
                    "old_connection_id": old_connection_id,
                    "new_connection_id": new_connection_id,
                },
            )
        )
    request = ClarificationRequest(
        kind="rewire_new_endpoint_disambiguation",
        question="Multiple executable new endpoints match. Choose the exact rewire target.",
        options=options,
        state_revision=revision,
    )
    payload = request.to_dict()
    payload.update(
        {
            "ok": False,
            "message": "Multiple executable new endpoints match the provided hints.",
            "error_type": "ambiguous_rewire_endpoint",
        }
    )
    return payload


def rewire_clarification_payload(
    agent: Any,
    candidates: list[dict[str, Any]],
    *,
    new_src_block: str,
    new_src_port: int | str | None,
    new_dst_block: str,
    new_dst_port: int | str | None,
) -> dict[str, Any]:
    revision = agent.session.state_revision
    options: list[ClarificationOption] = []
    for label, candidate in zip(("A", "B", "C"), candidates, strict=False):
        old_connection_id = candidate["connection_id"]
        options.append(
            ClarificationOption(
                label=label,
                title=old_connection_id,
                description=(
                    f"replace {candidate['src_block']}:{candidate['src_port']} -> "
                    f"{candidate['dst_block']}:{candidate['dst_port']} with "
                    f"{new_src_block}:{new_src_port} -> {new_dst_block}:{new_dst_port}"
                ),
                tool_name="rewire_connection",
                tool_args={
                    "old_connection_id": old_connection_id,
                    "new_src_block": new_src_block,
                    "new_src_port": new_src_port,
                    "new_dst_block": new_dst_block,
                    "new_dst_port": new_dst_port,
                },
                metadata={
                    "state_revision": revision,
                    "old_connection_id": old_connection_id,
                },
            )
        )
    request = ClarificationRequest(
        kind="rewire_connection_disambiguation",
        question="Multiple old connections match. Choose the exact edge to rewire.",
        options=options,
        state_revision=revision,
    )
    payload = request.to_dict()
    payload.update(
        {
            "ok": False,
            "message": "Multiple old connections match the provided endpoint hints.",
            "error_type": "ambiguous_connection",
        }
    )
    return payload


def duplicate_block_clarification_payload(
    agent: Any,
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    """Build executable clarification for duplicate names only when safe."""
    errors = payload.get("errors")
    operations = payload.get("normalized_operations")
    if not isinstance(errors, list) or not isinstance(operations, list):
        return None

    duplicate_errors = [
        error
        for error in errors
        if isinstance(error, dict)
        and error.get("code") == "block_name_not_unique"
        and isinstance(error.get("op_index"), int)
    ]
    if len(duplicate_errors) != 1 or len(operations) != 1:
        return None

    op_index = duplicate_errors[0]["op_index"]
    if op_index < 0 or op_index >= len(operations):
        return None
    operation = operations[op_index]
    if not isinstance(operation, dict):
        return None
    if operation.get("op_type") not in {"update_params", "update_states", "remove_block"}:
        return None
    if "block_type" in operation:
        # Same-name same-type duplicates are not executable without a UID-based schema.
        return None
    instance_name = operation.get("instance_name")
    if not isinstance(instance_name, str) or not instance_name:
        return None

    resolved = agent.session.resolve_block_reference(instance_name)
    candidates = resolved.get("candidates", [])
    if not isinstance(candidates, list) or len(candidates) < 2 or len(candidates) > 3:
        return None

    block_types = [
        candidate.get("block_type")
        for candidate in candidates
        if isinstance(candidate, dict) and isinstance(candidate.get("block_type"), str)
    ]
    if len(block_types) != len(candidates):
        return None
    block_types_are_unique = len(set(block_types)) == len(block_types)

    revision = agent.session.state_revision
    options: list[ClarificationOption] = []
    for label, candidate in zip(("A", "B", "C"), candidates, strict=False):
        block_type = candidate["block_type"]
        transaction = copy.deepcopy(operation)
        if block_types_are_unique:
            transaction["block_type"] = block_type
        else:
            block_uid = candidate.get("block_uid")
            if not isinstance(block_uid, str) or not block_uid:
                return None
            transaction.pop("instance_name", None)
            transaction.pop("block_type", None)
            transaction["target_ref"] = {
                "block_uid": block_uid,
                "expected_instance_name": instance_name,
                "expected_block_type": block_type,
                "base_state_revision": revision,
            }
        options.append(
            ClarificationOption(
                label=label,
                title=f"{instance_name} ({block_type})",
                description=(
                    f"state={candidate.get('state')}; "
                    f"coordinate={candidate.get('coordinate')}"
                ),
                tool_name="apply_edit",
                tool_args={"transaction": transaction},
                metadata={
                    "state_revision": revision,
                    "block_uid": candidate.get("block_uid"),
                    "block_type": block_type,
                },
            )
        )

    request = ClarificationRequest(
        kind="block_disambiguation",
        question=f"Multiple blocks are named `{instance_name}`. Choose the exact target.",
        options=options,
        state_revision=revision,
    )
    clarification = request.to_dict()
    clarification.update(
        {
            "ok": False,
            "message": (
                "Multiple blocks match the requested instance_name. "
                "Choose one candidate before mutating."
            ),
            "error_type": "ambiguous_block",
            "errors": copy.deepcopy(errors),
            "normalized_operations": copy.deepcopy(operations),
        }
    )
    return clarification
