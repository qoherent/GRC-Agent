"""Disconnect endpoint resolution helpers.

This module resolves wrapper/internal disconnect arguments to one executable
connection id. It does not mutate graph state.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from grc_agent._payload import ErrorCode
from grc_agent.flowgraph_session import FlowgraphSession


@dataclass(frozen=True)
class DisconnectResolution:
    connection_id: str | None = None
    ambiguous_candidates: list[dict[str, Any]] | None = None
    ok: bool = False
    message: str | None = None
    error_type: str | None = None
    state_revision: int | None = None
    validation_errors: list[dict[str, Any]] | None = None


def resolve_disconnect_connection_id(
    *,
    session: FlowgraphSession,
    connection_id: str | None = None,
    src_block: str | None = None,
    src_port: int | str | None = None,
    dst_block: str | None = None,
    dst_port: int | str | None = None,
) -> DisconnectResolution:
    endpoint_args = {
        "src_block": src_block,
        "src_port": src_port,
        "dst_block": dst_block,
        "dst_port": dst_port,
    }
    has_endpoint_hint = any(value is not None for value in endpoint_args.values())
    if has_endpoint_hint:
        resolved = session.find_connection_candidates(
            src_block=src_block,
            src_port=src_port,
            dst_block=dst_block,
            dst_port=dst_port,
        )
        candidates = resolved["candidates"]
        if not candidates:
            return DisconnectResolution(
                ok=False,
                message="No existing connection matches the provided endpoint fields.",
                error_type="connection_not_found",
                state_revision=session.state_revision,
            )
        if len(candidates) > 1:
            return DisconnectResolution(
                ok=False,
                ambiguous_candidates=candidates,
            )

        resolved_connection_id = candidates[0]["connection_id"]
        if connection_id is not None and connection_id != resolved_connection_id:
            return DisconnectResolution(
                ok=False,
                message=(
                    "connection_id does not match the provided endpoint fields: "
                    f"{connection_id}"
                ),
                error_type="connection_endpoint_mismatch",
                state_revision=session.state_revision,
            )
        connection_id = resolved_connection_id

    if not isinstance(connection_id, str) or not connection_id.strip():
        return DisconnectResolution(
            ok=False,
            message=(
                "remove_connection requires either connection_id or enough "
                "endpoint fields to resolve one existing connection."
            ),
            error_type=ErrorCode.TOOL_CALL_INVALID,
            validation_errors=[
                {
                    "code": "missing_required",
                    "field": "connection_id",
                    "message": "Provide connection_id or endpoint fields.",
                }
            ],
        )

    return DisconnectResolution(ok=True, connection_id=connection_id.strip())
