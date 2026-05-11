"""Rewire endpoint resolution helpers for change_graph wrappers."""

from __future__ import annotations

from typing import Any

from grc_agent._payload import ErrorCode
from grc_agent.catalog import describe_block
from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.session_ops import parse_connection_id
from grc_agent.transaction import propose_edit


def has_endpoint_value(value: Any) -> bool:
    return value is not None and not (isinstance(value, str) and not value.strip())


def rewire_new_endpoint_is_exact(
    *,
    new_src_block: str | None,
    new_src_port: int | str | None,
    new_dst_block: str | None,
    new_dst_port: int | str | None,
) -> bool:
    return all(
        has_endpoint_value(value)
        for value in (new_src_block, new_src_port, new_dst_block, new_dst_port)
    )


def resolve_rewire_new_endpoint_args(
    agent: Any,
    *,
    old_connection_id: str,
    new_src_block: str | None,
    new_src_port: int | str | None,
    new_dst_block: str | None,
    new_dst_port: int | str | None,
) -> dict[str, Any]:
    if agent._rewire_new_endpoint_is_exact(
        new_src_block=new_src_block,
        new_src_port=new_src_port,
        new_dst_block=new_dst_block,
        new_dst_port=new_dst_port,
    ):
        return {
            "ok": True,
            "new_src_block": str(new_src_block),
            "new_src_port": new_src_port,
            "new_dst_block": str(new_dst_block),
            "new_dst_port": new_dst_port,
        }

    missing_fields = [
        field
        for field, value in (
            ("new_src_block", new_src_block),
            ("new_src_port", new_src_port),
            ("new_dst_block", new_dst_block),
            ("new_dst_port", new_dst_port),
        )
        if not agent._has_endpoint_value(value)
    ]
    has_source_hint = agent._has_endpoint_value(new_src_block) or agent._has_endpoint_value(new_src_port)
    has_destination_hint = agent._has_endpoint_value(new_dst_block) or agent._has_endpoint_value(new_dst_port)
    if not has_source_hint or not has_destination_hint:
        missing_side = "new_source" if not has_source_hint else "new_destination"
        return {
            "ok": False,
            "message": (
                "rewire_connection requires at least one hint for both the "
                "new source and new destination; it will not infer an entire endpoint side."
            ),
            "error_type": ErrorCode.TOOL_CALL_INVALID,
            "state_revision": agent.session.state_revision,
            "validation_errors": [
                {
                    "code": "missing_required",
                    "field": missing_side,
                    "message": (
                        "Provide exact fields or at least one bounded hint for "
                        "this new endpoint side."
                    ),
                }
            ],
        }
    candidates = agent._rewire_new_endpoint_candidates(
        old_connection_id=old_connection_id,
        new_src_block=new_src_block,
        new_src_port=new_src_port,
        new_dst_block=new_dst_block,
        new_dst_port=new_dst_port,
    )
    if not candidates:
        return {
            "ok": False,
            "message": (
                "rewire_connection requires exact new endpoints or endpoint hints "
                "that resolve to existing executable candidates."
            ),
            "error_type": ErrorCode.TOOL_CALL_INVALID,
            "state_revision": agent.session.state_revision,
            "validation_errors": [
                {
                    "code": "missing_required",
                    "field": field,
                    "message": (
                        "Provide an exact new endpoint field or enough endpoint "
                        "hints to resolve executable candidates."
                    ),
                }
                for field in missing_fields
            ],
        }
    if len(candidates) == 1:
        candidate = candidates[0]
        return {"ok": True, **candidate}
    if len(candidates) > 3:
        return {
            "ok": False,
            "message": (
                "Too many executable new endpoint candidates match. "
                "Provide exact new source and destination endpoints."
            ),
            "error_type": "ambiguous_rewire_endpoint",
            "state_revision": agent.session.state_revision,
            "candidate_count": len(candidates),
        }
    return agent._rewire_new_endpoint_clarification_payload(
        old_connection_id=old_connection_id,
        candidates=candidates,
    )


def rewire_new_endpoint_candidates(
    agent: Any,
    *,
    old_connection_id: str,
    new_src_block: str | None,
    new_src_port: int | str | None,
    new_dst_block: str | None,
    new_dst_port: int | str | None,
) -> list[dict[str, Any]]:
    parsed_old = parse_connection_id(old_connection_id)
    if parsed_old is None:
        return []
    source_candidates = agent._connection_endpoint_candidates(
        side="source",
        block=new_src_block,
        port=new_src_port,
    )
    destination_candidates = agent._connection_endpoint_candidates(
        side="destination",
        block=new_dst_block,
        port=new_dst_port,
    )
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, int | str, str, int | str]] = set()
    for source_block, source_port in source_candidates:
        for destination_block, destination_port in destination_candidates:
            connection = (source_block, source_port, destination_block, destination_port)
            if connection == parsed_old or connection in seen:
                continue
            seen.add(connection)
            candidate = {
                "new_src_block": source_block,
                "new_src_port": source_port,
                "new_dst_block": destination_block,
                "new_dst_port": destination_port,
            }
            if agent._rewire_candidate_passes_preflight(old_connection_id, candidate):
                candidates.append(candidate)
    return candidates


def connection_endpoint_candidates(
    agent: Any,
    *,
    side: str,
    block: str | None,
    port: int | str | None,
) -> list[tuple[str, int | str]]:
    if agent._has_endpoint_value(block) and agent._has_endpoint_value(port):
        loaded_block = agent._loaded_block_by_name(str(block))
        if loaded_block is None:
            return []
        if not agent._loaded_block_has_port(
            block_type=loaded_block.block_type,
            port=port,
            side=side,
        ):
            return []
        return [(str(block), port)]
    flowgraph = agent.session.flowgraph
    if flowgraph is None:
        return []
    candidates: set[tuple[str, int | str]] = set()
    if agent._has_endpoint_value(port):
        if agent._has_endpoint_value(block):
            candidates.add((str(block), port))
        else:
            for loaded_block in flowgraph.blocks:
                if agent._loaded_block_has_port(
                    block_type=loaded_block.block_type,
                    port=port,
                    side=side,
                ):
                    candidates.add((loaded_block.instance_name, port))
    for connection in flowgraph.connections:
        if side == "source":
            endpoint_block = connection.src_block
            endpoint_port = connection.src_port
        else:
            endpoint_block = connection.dst_block
            endpoint_port = connection.dst_port
        if agent._has_endpoint_value(block) and endpoint_block != block:
            continue
        if agent._has_endpoint_value(port) and not FlowgraphSession._port_matches(endpoint_port, port):
            continue
        candidates.add((endpoint_block, endpoint_port))
    return sorted(candidates, key=lambda item: (item[0], str(item[1])))


def loaded_block_by_name(agent: Any, instance_name: str) -> Any | None:
    flowgraph = agent.session.flowgraph
    if flowgraph is None:
        return None
    return next(
        (
            loaded_block
            for loaded_block in flowgraph.blocks
            if loaded_block.instance_name == instance_name
        ),
        None,
    )


def loaded_block_has_port(
    *,
    block_type: str,
    port: int | str,
    side: str,
) -> bool:
    description = describe_block(block_type)
    if not description.get("ok"):
        return False
    field_name = "outputs" if side == "source" else "inputs"
    ports = description.get(field_name)
    if not isinstance(ports, list):
        return False
    if not isinstance(port, str):
        return any(
            isinstance(candidate, dict)
            and candidate.get("domain") != "message"
            and not candidate.get("id")
            for candidate in ports
        )
    return any(
        isinstance(candidate, dict) and candidate.get("id") == port
        for candidate in ports
    )


def rewire_candidate_passes_preflight(
    agent: Any,
    old_connection_id: str,
    candidate: dict[str, Any],
) -> bool:
    proposal = propose_edit(
        agent.session,
        [
            {
                "op_type": "remove_connection",
                "connection_id": old_connection_id,
            },
            {
                "op_type": "add_connection",
                "src_block": candidate["new_src_block"],
                "src_port": candidate["new_src_port"],
                "dst_block": candidate["new_dst_block"],
                "dst_port": candidate["new_dst_port"],
            },
        ],
        agent.catalog_root,
    )
    return bool(proposal.get("ok"))


def resolve_old_rewire_connection_id(
    agent: Any,
    *,
    old_connection_id: str | None,
    old_src_block: str | None,
    old_src_port: int | str | None,
    old_dst_block: str | None,
    old_dst_port: int | str | None,
    new_src_block: str | None,
    new_src_port: int | str | None,
    new_dst_block: str | None,
    new_dst_port: int | str | None,
) -> dict[str, Any]:
    old_endpoint_args = {
        "src_block": old_src_block,
        "src_port": old_src_port,
        "dst_block": old_dst_block,
        "dst_port": old_dst_port,
    }
    has_old_hint = any(value is not None for value in old_endpoint_args.values())

    if has_old_hint:
        resolved = agent.session.find_connection_candidates(**old_endpoint_args)
        candidates = resolved["candidates"]
        if not candidates:
            return {
                "ok": False,
                "message": "No existing old connection matches the provided endpoint fields.",
                "error_type": "connection_not_found",
                "state_revision": agent.session.state_revision,
            }
        if len(candidates) > 1:
            if not agent._rewire_new_endpoint_is_exact(
                new_src_block=new_src_block,
                new_src_port=new_src_port,
                new_dst_block=new_dst_block,
                new_dst_port=new_dst_port,
            ):
                return {
                    "ok": False,
                    "message": (
                        "Multiple old connections match. Provide an exact old "
                        "connection before resolving partial new endpoint hints."
                    ),
                    "error_type": "ambiguous_connection",
                    "state_revision": agent.session.state_revision,
                }
            return agent._rewire_clarification_payload(
                candidates,
                new_src_block=str(new_src_block),
                new_src_port=new_src_port,
                new_dst_block=str(new_dst_block),
                new_dst_port=new_dst_port,
            )
        resolved_connection_id = candidates[0]["connection_id"]
        if old_connection_id is not None and old_connection_id != resolved_connection_id:
            return {
                "ok": False,
                "message": (
                    "old_connection_id does not match the provided old endpoint fields: "
                    f"{old_connection_id}"
                ),
                "error_type": "connection_endpoint_mismatch",
                "state_revision": agent.session.state_revision,
            }
        return {"ok": True, "old_connection_id": resolved_connection_id}

    if not isinstance(old_connection_id, str) or not old_connection_id.strip():
        return {
            "ok": False,
            "message": (
                "rewire_connection requires old_connection_id or enough old "
                "endpoint fields to resolve one existing connection."
            ),
            "error_type": ErrorCode.TOOL_CALL_INVALID,
            "state_revision": agent.session.state_revision,
            "validation_errors": [
                {
                    "code": "missing_required",
                    "field": "old_connection_id",
                    "message": "Provide old_connection_id or old endpoint fields.",
                }
            ],
        }

    parsed = parse_connection_id(old_connection_id.strip())
    if parsed is None:
        return {
            "ok": False,
            "message": "old_connection_id must be in form src_block:src_port->dst_block:dst_port.",
            "error_type": ErrorCode.TOOL_CALL_INVALID,
            "state_revision": agent.session.state_revision,
        }
    src_block, src_port, dst_block, dst_port = parsed
    resolved = agent.session.find_connection_candidates(
        src_block=src_block,
        src_port=src_port,
        dst_block=dst_block,
        dst_port=dst_port,
    )
    candidates = resolved["candidates"]
    if not candidates:
        return {
            "ok": False,
            "message": f"Old connection not found: {old_connection_id.strip()}",
            "error_type": "connection_not_found",
            "state_revision": agent.session.state_revision,
        }
    if len(candidates) > 1:
        if not agent._rewire_new_endpoint_is_exact(
            new_src_block=new_src_block,
            new_src_port=new_src_port,
            new_dst_block=new_dst_block,
            new_dst_port=new_dst_port,
        ):
            return {
                "ok": False,
                "message": (
                    "Multiple old connections match. Provide an exact old "
                    "connection before resolving partial new endpoint hints."
                ),
                "error_type": "ambiguous_connection",
                "state_revision": agent.session.state_revision,
            }
        return agent._rewire_clarification_payload(
            candidates,
            new_src_block=str(new_src_block),
            new_src_port=new_src_port,
            new_dst_block=str(new_dst_block),
            new_dst_port=new_dst_port,
        )
    return {"ok": True, "old_connection_id": candidates[0]["connection_id"]}
