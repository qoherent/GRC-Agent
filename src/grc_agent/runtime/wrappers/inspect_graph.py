"""Read-only `inspect_graph` MVP wrapper."""

from __future__ import annotations

from collections import Counter
import time
from typing import TYPE_CHECKING, Any

from grc_agent.models import Block, Connection
from grc_agent.runtime.editable_parameters import (
    EditableParameterCandidate,
    build_editable_parameter_candidates,
)
from grc_agent.session import summarize_graph
from grc_agent.session_ops import connection_id as render_connection_id

if TYPE_CHECKING:
    from grc_agent.agent import GrcAgent, ToolResult


VALID_VIEWS = {"overview", "details"}
MAX_TARGETS = 5
MAX_PARAMS = 12
MAX_PARAMS_PER_BLOCK = 50
MAX_OVERVIEW_PARAMS_PER_BLOCK = 6
MAX_CONNECTIONS_PER_BLOCK = 12
MAX_OVERVIEW_CONNECTIONS = 12
MAX_CANDIDATES = 8


def inspect_graph(
    agent: "GrcAgent",
    *,
    view: str,
    targets: list[str],
    params: list[str],
    debug: bool = False,
) -> "ToolResult":
    started = time.monotonic()
    before_revision = agent.session.state_revision
    before_dirty = agent.session.is_dirty
    selected_view = str(view).strip().lower()
    validation_run = False
    output_truncated = False

    missing_session = agent._missing_session_result("inspect_graph")
    if missing_session is not None:
        return agent._attach_wrapper_dispatch_telemetry(
            debug=debug,
            wrapper_name="inspect_graph",
            wrapper_action=selected_view or "invalid",
            internal_handlers=["none"],
            started=started,
            before_revision=before_revision,
            before_dirty=before_dirty,
            result=missing_session,
            validation_run=False,
            output_truncated=False,
        )

    normalized_targets = _normalize_string_list(targets, limit=MAX_TARGETS)
    normalized_params = _normalize_string_list(params, limit=MAX_PARAMS)
    if selected_view == "details" and not normalized_params:
        normalized_params = ["all"]
    if selected_view not in VALID_VIEWS:
        result = _invalid_request(
            agent,
            view=selected_view,
            code="invalid_view",
            message="inspect_graph.view must be 'overview' or 'details'.",
        )
    elif len(targets) > MAX_TARGETS:
        result = _invalid_request(
            agent,
            view=selected_view,
            code="target_limit_exceeded",
            message=f"inspect_graph accepts at most {MAX_TARGETS} targets.",
        )
    elif len(params) > MAX_PARAMS:
        result = _invalid_request(
            agent,
            view=selected_view,
            code="param_limit_exceeded",
            message=f"inspect_graph accepts at most {MAX_PARAMS} params.",
        )
    elif selected_view == "overview":
        result = _overview(agent, targets=normalized_targets, params=normalized_params)
        output_truncated = bool(result.get("truncation", {}).get("truncated"))
    else:
        result = _details(agent, targets=normalized_targets, params=normalized_params)
        output_truncated = bool(result.get("truncation", {}).get("truncated"))

    tool_result = agent._payload_result("inspect_graph", result)
    return agent._attach_wrapper_dispatch_telemetry(
        debug=debug,
        wrapper_name="inspect_graph",
        wrapper_action=selected_view or "invalid",
        internal_handlers=["inspect_graph_view"],
        started=started,
        before_revision=before_revision,
        before_dirty=before_dirty,
        result=tool_result,
        validation_run=validation_run,
        output_truncated=output_truncated,
    )


def _overview(
    agent: "GrcAgent",
    *,
    targets: list[str],
    params: list[str],
) -> dict[str, Any]:
    summary = summarize_graph(
        agent.session,
        max_blocks=agent._guardrails_cfg.max_graph_summary_blocks,
    )
    if not summary.get("ok"):
        return _base_payload(
            agent,
            ok=False,
            view="overview",
            complete=False,
            errors=[
                {
                    "code": str(summary.get("error_type") or "inspect_failed"),
                    "message": str(summary.get("message") or "Graph summary failed."),
                }
            ],
        )

    assert agent.session.flowgraph is not None
    candidates = build_editable_parameter_candidates(
        agent.session,
        catalog_root=agent.catalog_root,
        include_connections=True,
    )
    by_block = _group_candidates_by_block(candidates)
    block_types = Counter(block.block_type for block in agent.session.flowgraph.blocks)
    connections = list(summary.get("connections", []))
    shown_connections = connections[:MAX_OVERVIEW_CONNECTIONS]
    omitted_connections = max(0, len(connections) - len(shown_connections))
    incoming, outgoing = _connection_summaries(agent.session.flowgraph.connections)
    overview = {
        "graph_name": _graph_name(agent),
        "graph_metadata": _graph_metadata(agent),
        "path": summary.get("path"),
        "graph_id": summary.get("graph_id"),
        "counts": {
            "blocks": summary.get("block_count", 0),
            "connections": summary.get("connection_count", 0),
            "variables": summary.get("variable_count", 0),
        },
        "blocks": _overview_block_rows(
            agent.session.flowgraph.blocks,
            by_block=by_block,
            incoming=incoming,
            outgoing=outgoing,
        ),
        "connections": shown_connections,
        "parameter_dependencies": _parameter_dependencies(
            agent.session.flowgraph.blocks,
            candidates,
        ),
        "top_block_types": [
            {"type": block_type, "count": count}
            for block_type, count in block_types.most_common(8)
        ],
        "notes": [
            "Compact overview only. Use details with specific targets for parameters, connections, and edit handles."
        ],
    }
    omitted_blocks = int(summary.get("blocks_truncated") or 0)
    truncated = omitted_blocks > 0 or omitted_connections > 0
    omitted_counts: dict[str, int] = {}
    if omitted_blocks:
        omitted_counts["blocks"] = omitted_blocks
    if omitted_connections:
        omitted_counts["connections"] = omitted_connections
    return _base_payload(
        agent,
        ok=True,
        view="overview",
        complete=not truncated,
        summary=overview,
        truncation={
            "truncated": truncated,
            "reason": "overview_limit" if truncated else None,
            "omitted_counts": omitted_counts,
        },
    )


def _details(
    agent: "GrcAgent",
    *,
    targets: list[str],
    params: list[str],
) -> dict[str, Any]:
    if not targets:
        return _base_payload(
            agent,
            ok=False,
            view="details",
            complete=False,
            params_filter=_params_filter(params, []),
            errors=[
                {
                    "code": "target_required",
                    "message": "details requires at least one graph-local target.",
                }
            ],
        )
    assert agent.session.flowgraph is not None
    candidates = build_editable_parameter_candidates(
        agent.session,
        catalog_root=agent.catalog_root,
        include_connections=True,
    )
    by_block = _group_candidates_by_block(candidates)
    connections = list(agent.session.flowgraph.connections)
    incoming, outgoing = _connection_summaries(connections)
    resolved_rows: list[dict[str, Any]] = []
    target_matches: list[dict[str, Any]] = []
    editable_handles: list[dict[str, Any]] = []
    matched_params: set[str] = set()
    has_ambiguity = False
    errors: list[dict[str, Any]] = []
    truncated = False
    omitted_counts: dict[str, int] = {}

    for requested_target in targets:
        match = _resolve_target(
            requested_target,
            agent.session.flowgraph.blocks,
            candidates,
            params=_specific_params(params),
            connections=connections,
        )
        target_matches.append(match.match_payload())
        if match.status == "ambiguous":
            has_ambiguity = True
            errors.append(
                {
                    "code": "ambiguous_target",
                    "message": f"Target {requested_target!r} matched multiple graph objects.",
                }
            )
            continue
        if match.status == "not_found" or match.block is None:
            errors.append(
                {
                    "code": "target_not_found",
                    "message": f"Target {requested_target!r} did not match the active graph.",
                }
            )
            continue

        block_candidates = by_block.get(match.block.block_uid, [])
        row, row_handles, row_matched_params, row_truncated = _block_details_row(
            match.block,
            block_candidates,
            requested=requested_target,
            matched_by=match.matched_by,
            params=params,
            incoming_connections=incoming.get(match.block.instance_name, ()),
            outgoing_connections=outgoing.get(match.block.instance_name, ()),
        )
        resolved_rows.append(row)
        editable_handles.extend(row_handles)
        matched_params.update(row_matched_params)
        if row_truncated:
            truncated = True
            omitted_counts["parameters"] = omitted_counts.get("parameters", 0) + 1

    if _params_request_all(params):
        unmatched_params = []
    else:
        unmatched_params = [
            param
            for param in params
            if _normalize_text(param)
            not in {_normalize_text(item) for item in matched_params}
        ]
    complete = not has_ambiguity and not errors and not truncated
    return _base_payload(
        agent,
        ok=not errors,
        view="details",
        complete=complete,
        targets=resolved_rows,
        target_matches=target_matches,
        params_filter=_params_filter(
            params,
            sorted(matched_params),
            unmatched=unmatched_params,
        ),
        editable_handles=editable_handles,
        ambiguity={
            "has_ambiguity": has_ambiguity,
            "reason": "Target matched multiple graph objects. Re-run details with an exact candidate name or uid."
            if has_ambiguity
            else None,
        },
        truncation={
            "truncated": truncated,
            "reason": "parameter_limit" if truncated else None,
            "omitted_counts": omitted_counts,
        },
        errors=errors,
    )


class _TargetMatch:
    def __init__(
        self,
        *,
        request: str,
        status: str,
        block: Block | None = None,
        matched_by: str | None = None,
        candidates: list[dict[str, Any]] | None = None,
    ) -> None:
        self.request = request
        self.status = status
        self.block = block
        self.matched_by = matched_by
        self.candidates = candidates or []

    def match_payload(self) -> dict[str, Any]:
        return {
            "request": self.request,
            "status": self.status,
            "resolved_name": self.block.instance_name if self.block is not None else None,
            "matched_by": self.matched_by,
            "candidates": self.candidates,
        }


def _resolve_target(
    target: str,
    blocks: list[Block],
    candidates: list[EditableParameterCandidate],
    *,
    params: list[str],
    connections: list[Connection],
) -> _TargetMatch:
    request = str(target).strip()
    if not request:
        return _TargetMatch(request=request, status="not_found")

    exact_blocks = [
        block
        for block in blocks
        if request in {block.instance_name, block.block_uid, block.block_type}
    ]
    exact_blocks = _dedupe_blocks(exact_blocks)
    if len(exact_blocks) == 1:
        return _TargetMatch(
            request=request,
            status="resolved",
            block=exact_blocks[0],
            matched_by="exact_identifier",
        )
    if len(exact_blocks) > 1:
        return _TargetMatch(
            request=request,
            status="ambiguous",
            candidates=_candidate_payloads(exact_blocks),
        )

    request_tokens = _tokens(request)
    requested_param_tokens = set().union(*(_tokens(param) for param in params)) if params else set()
    scored: list[tuple[int, Block]] = []
    for block in blocks:
        block_candidates = [
            candidate for candidate in candidates if candidate.block_uid == block.block_uid
        ]
        score = _target_score(
            block,
            block_candidates,
            request=request,
            request_tokens=request_tokens,
            requested_param_tokens=requested_param_tokens,
        )
        if score > 0:
            scored.append((score, block))
    if not scored:
        conn_blocks = _connection_target_blocks(request, connections, blocks)
        if len(conn_blocks) == 1:
            return _TargetMatch(
                request=request,
                status="resolved",
                block=conn_blocks[0],
                matched_by="connection_endpoint",
            )
        if len(conn_blocks) > 1:
            return _TargetMatch(
                request=request,
                status="ambiguous",
                candidates=_candidate_payloads(conn_blocks),
            )
        return _TargetMatch(request=request, status="not_found")

    best_score = max(score for score, _block in scored)
    best = _dedupe_blocks([block for score, block in scored if score == best_score])
    if len(best) == 1:
        return _TargetMatch(
            request=request,
            status="resolved",
            block=best[0],
            matched_by="graph_local_metadata",
        )
    return _TargetMatch(
        request=request,
        status="ambiguous",
        candidates=_candidate_payloads(best),
    )


def _target_score(
    block: Block,
    candidates: list[EditableParameterCandidate],
    *,
    request: str,
    request_tokens: set[str],
    requested_param_tokens: set[str],
) -> int:
    score = 0
    block_fields = [block.instance_name, block.block_type]
    block_fields.extend(
        candidate.block_label
        for candidate in candidates
        if isinstance(candidate.block_label, str)
    )
    if _any_field_matches(block_fields, request=request, tokens=request_tokens):
        score += 3

    for candidate in candidates:
        if _any_field_matches(
            [candidate.param_key, candidate.param_label],
            request=request,
            tokens=request_tokens | requested_param_tokens,
        ):
            score += 2
        current_value = _compact_value(candidate.current_value)
        if current_value and current_value.lower() in request.lower():
            score += 2
    return score


def _connection_target_blocks(
    target: str,
    connections: list[Connection],
    blocks: list[Block],
) -> list[Block]:
    by_name = {block.instance_name: block for block in blocks}
    matches: list[Block] = []
    target_lower = target.lower()
    for connection in connections:
        conn_id = render_connection_id(
            connection.src_block,
            connection.src_port,
            connection.dst_block,
            connection.dst_port,
        )
        if conn_id.lower() != target_lower:
            continue
        for name in (connection.src_block, connection.dst_block):
            block = by_name.get(name)
            if block is not None:
                matches.append(block)
    return _dedupe_blocks(matches)


def _block_details_row(
    block: Block,
    candidates: list[EditableParameterCandidate],
    *,
    requested: str,
    matched_by: str | None,
    params: list[str],
    incoming_connections: tuple[str, ...],
    outgoing_connections: tuple[str, ...],
) -> tuple[dict[str, Any], list[dict[str, Any]], set[str], bool]:
    specific_params = _specific_params(params)
    requested_param_tokens = [_tokens(param) for param in specific_params]
    matched_params: set[str] = set()
    selected_candidates: list[EditableParameterCandidate] = []
    for candidate in candidates:
        if _params_request_all(params) and _default_detail_param(candidate):
            selected_candidates.append(candidate)
            matched_params.add(candidate.param_key)
        elif specific_params and _param_matches(
            candidate,
            specific_params,
            requested_param_tokens,
        ):
            selected_candidates.append(candidate)
            matched_params.add(candidate.param_key)
    if _params_request_all(params) and not selected_candidates:
        selected_candidates = candidates[:MAX_PARAMS_PER_BLOCK]

    params_truncated = len(selected_candidates) > MAX_PARAMS_PER_BLOCK
    returned_candidates = selected_candidates[:MAX_PARAMS_PER_BLOCK]
    parameters = [
        {
            "name": candidate.param_key,
            "label": candidate.param_label,
            "dtype": candidate.param_dtype,
            "default": candidate.param_default,
            "options": list(candidate.param_options),
            "option_labels": list(candidate.param_option_labels),
            "value_label": _option_label_for_current(candidate),
            "visibility": candidate.param_hide,
            "value": candidate.current_value,
            "editable": True,
            "editable_handle": _editable_handle_id(candidate),
            "target_ref": candidate.target_ref,
            "state_revision": candidate.state_revision,
        }
        for candidate in returned_candidates
    ]
    handles = [
        {
            "handle": _editable_handle_id(candidate),
            "kind": "parameter",
            "display_name": f"{candidate.instance_name}.{candidate.param_key}",
            "allowed_change_kinds": ["set_param"],
            "requires_state_revision": candidate.state_revision,
            "target_ref": candidate.target_ref,
            "instance_name": candidate.instance_name,
            "param_key": candidate.param_key,
            "current_value": candidate.current_value,
        }
        for candidate in returned_candidates
    ]
    incoming = _connection_dicts(incoming_connections)
    outgoing = _connection_dicts(outgoing_connections)
    row = {
        "request": requested,
        "match_status": "resolved",
        "matched_by": matched_by,
        "name": block.instance_name,
        "kind": "block",
        "type": block.block_type,
        "catalog_label": _block_label(candidates),
        "uid": block.block_uid,
        "parameters": parameters,
        "connections": {
            "incoming": incoming[:MAX_CONNECTIONS_PER_BLOCK],
            "outgoing": outgoing[:MAX_CONNECTIONS_PER_BLOCK],
        },
        "safe_actions": ["inspect", "set_param"],
        "params_truncated": params_truncated,
    }
    return row, handles, matched_params, params_truncated


def _param_matches(
    candidate: EditableParameterCandidate,
    params: list[str],
    token_sets: list[set[str]],
) -> bool:
    fields = [candidate.param_key, candidate.param_label]
    for param, tokens in zip(params, token_sets, strict=False):
        if _any_field_matches(fields, request=param, tokens=tokens):
            return True
    return False


def _base_payload(
    agent: "GrcAgent",
    *,
    ok: bool,
    view: str,
    complete: bool,
    summary: Any = None,
    targets: list[dict[str, Any]] | None = None,
    target_matches: list[dict[str, Any]] | None = None,
    params_filter: dict[str, Any] | None = None,
    editable_handles: list[dict[str, Any]] | None = None,
    ambiguity: dict[str, Any] | None = None,
    truncation: dict[str, Any] | None = None,
    errors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "ok": ok,
        "view": view,
        "state_revision": agent.session.state_revision,
        "complete": complete,
        "summary": summary,
        "targets": targets or [],
        "target_matches": target_matches or [],
        "params_filter": params_filter
        or {"requested": [], "matched": [], "unmatched": []},
        "editable_handles": editable_handles or [],
        "ambiguity": ambiguity or {"has_ambiguity": False, "reason": None},
        "truncation": truncation
        or {"truncated": False, "reason": None, "omitted_counts": {}},
        "validation_status": _validation_status(agent),
        "errors": errors or [],
    }


def _invalid_request(
    agent: "GrcAgent",
    *,
    view: str,
    code: str,
    message: str,
) -> dict[str, Any]:
    return _base_payload(
        agent,
        ok=False,
        view=view,
        complete=False,
        errors=[{"code": code, "message": message}],
    )


def _validation_status(agent: "GrcAgent") -> dict[str, Any]:
    state = agent.session.validation_state()
    return {
        "status": state.get("status"),
        "last_checked_revision": state.get("state_revision"),
        "summary": state.get("stderr") or state.get("stdout"),
    }


def _params_filter(
    requested: list[str],
    matched: list[str],
    *,
    unmatched: list[str] | None = None,
) -> dict[str, Any]:
    matched_norm = {_normalize_text(item) for item in matched}
    if _params_request_all(requested):
        return {
            "requested": requested,
            "matched": matched,
            "unmatched": [],
        }
    return {
        "requested": requested,
        "matched": matched,
        "unmatched": unmatched
        if unmatched is not None
        else [item for item in requested if _normalize_text(item) not in matched_norm],
    }


def _group_candidates_by_block(
    candidates: list[EditableParameterCandidate],
) -> dict[str, list[EditableParameterCandidate]]:
    grouped: dict[str, list[EditableParameterCandidate]] = {}
    for candidate in candidates:
        grouped.setdefault(candidate.block_uid, []).append(candidate)
    return grouped


def _connection_summaries(
    connections: list[Connection],
) -> tuple[dict[str, tuple[str, ...]], dict[str, tuple[str, ...]]]:
    incoming: dict[str, list[str]] = {}
    outgoing: dict[str, list[str]] = {}
    for connection in connections:
        conn_id = render_connection_id(
            connection.src_block,
            connection.src_port,
            connection.dst_block,
            connection.dst_port,
        )
        outgoing.setdefault(connection.src_block, []).append(conn_id)
        incoming.setdefault(connection.dst_block, []).append(conn_id)
    return (
        {key: tuple(value) for key, value in incoming.items()},
        {key: tuple(value) for key, value in outgoing.items()},
    )


def _candidate_payloads(blocks: list[Block]) -> list[dict[str, Any]]:
    return [
        {
            "name": block.instance_name,
            "kind": "block",
            "type": block.block_type,
            "uid": block.block_uid,
            "reason": "graph-local target candidate",
        }
        for block in blocks[:MAX_CANDIDATES]
    ]


def _dedupe_blocks(blocks: list[Block]) -> list[Block]:
    deduped: dict[str, Block] = {}
    for block in blocks:
        deduped.setdefault(block.block_uid, block)
    return list(deduped.values())


def _connection_dicts(connection_ids: tuple[str, ...]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for conn_id in connection_ids:
        rows.append({"connection_id": conn_id})
    return rows


def _overview_block_rows(
    blocks: list[Block],
    *,
    by_block: dict[str, list[EditableParameterCandidate]],
    incoming: dict[str, tuple[str, ...]],
    outgoing: dict[str, tuple[str, ...]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for block in blocks:
        candidates = by_block.get(block.block_uid, [])
        row = {
            "name": block.instance_name,
            "type": block.block_type,
            "catalog_label": _block_label(candidates),
            "role": _block_role(
                block,
                incoming=incoming.get(block.instance_name, ()),
                outgoing=outgoing.get(block.instance_name, ()),
            ),
            "parameters": [
                _overview_param(candidate)
                for candidate in candidates
                if _overview_param_visible(candidate)
            ][:MAX_OVERVIEW_PARAMS_PER_BLOCK],
        }
        rows.append(row)
    return rows


def _overview_param(candidate: EditableParameterCandidate) -> dict[str, Any]:
    payload = {
        "name": candidate.param_key,
        "label": candidate.param_label,
        "value": candidate.current_value,
        "value_label": _option_label_for_current(candidate),
        "dtype": candidate.param_dtype,
    }
    return {key: value for key, value in payload.items() if value not in (None, [], {})}


def _overview_param_visible(candidate: EditableParameterCandidate) -> bool:
    if not _default_detail_param(candidate):
        return False
    value = _compact_value(candidate.current_value)
    return bool(value) and value != "0"


def _default_detail_param(candidate: EditableParameterCandidate) -> bool:
    if candidate.param_hide in {"all", "part"}:
        return False
    if isinstance(candidate.param_label, str) and candidate.param_label.strip():
        value = _compact_value(candidate.current_value)
        return bool(value)
    return False


def _block_label(candidates: list[EditableParameterCandidate]) -> str | None:
    for candidate in candidates:
        if isinstance(candidate.block_label, str) and candidate.block_label.strip():
            return candidate.block_label
    return None


def _option_label_for_current(candidate: EditableParameterCandidate) -> Any:
    if not candidate.param_options or not candidate.param_option_labels:
        return None
    current = _compact_value(candidate.current_value)
    for option, label in zip(
        candidate.param_options,
        candidate.param_option_labels,
        strict=False,
    ):
        if _compact_value(option) == current:
            return label
    return None


def _block_role(
    block: Block,
    *,
    incoming: tuple[str, ...],
    outgoing: tuple[str, ...],
) -> str:
    block_type = block.block_type.lower()
    if block_type == "variable" or block_type.startswith("variable"):
        return "variable_or_control"
    if outgoing and not incoming:
        return "source"
    if incoming and not outgoing:
        return "sink"
    if incoming and outgoing:
        return "transform"
    return "metadata"


def _parameter_dependencies(
    blocks: list[Block],
    candidates: list[EditableParameterCandidate],
) -> list[dict[str, Any]]:
    block_names = {block.instance_name for block in blocks}
    dependencies: list[dict[str, Any]] = []
    for candidate in candidates:
        value = candidate.current_value
        if not isinstance(value, str):
            continue
        stripped = value.strip()
        if stripped not in block_names or stripped == candidate.instance_name:
            continue
        dependencies.append(
            {
                "symbol": stripped,
                "used_by": f"{candidate.instance_name}.{candidate.param_key}",
                "parameter_label": candidate.param_label,
                "value": stripped,
            }
        )
    return dependencies[:24]


def _graph_metadata(agent: "GrcAgent") -> dict[str, Any]:
    flowgraph = agent.session.flowgraph
    if flowgraph is None or not isinstance(flowgraph.raw_data, dict):
        return {}
    options = flowgraph.raw_data.get("options")
    if not isinstance(options, dict):
        return {}
    parameters = options.get("parameters")
    if not isinstance(parameters, dict):
        return {}
    keys = ("id", "title", "description", "author", "category", "generate_options")
    return {
        key: value
        for key in keys
        if (value := parameters.get(key)) not in (None, "")
    }


def _editable_handle_id(candidate: EditableParameterCandidate) -> str:
    return f"param:{candidate.block_uid}:{candidate.param_key}"


def _params_request_all(params: list[str]) -> bool:
    return any(_normalize_text(param) == "all" for param in params)


def _specific_params(params: list[str]) -> list[str]:
    return [param for param in params if _normalize_text(param) != "all"]


def _any_field_matches(
    fields: list[str | None],
    *,
    request: str,
    tokens: set[str],
) -> bool:
    request_lower = f" {request.lower()} "
    for field in fields:
        if not isinstance(field, str) or not field.strip():
            continue
        field_lower = field.strip().lower()
        if f" {field_lower} " in request_lower:
            return True
        field_tokens = _tokens(field_lower)
        meaningful = {token for token in field_tokens if len(token) > 1}
        if meaningful and meaningful.issubset(tokens):
            return True
    return False


def _tokens(text: str) -> set[str]:
    return {
        token
        for token in _normalize_text(text).replace("_", " ").replace("-", " ").split()
        if token
    }


def _normalize_text(text: Any) -> str:
    return " ".join(str(text).lower().strip().split())


def _normalize_string_list(values: list[str], *, limit: int) -> list[str]:
    normalized: list[str] = []
    for value in values[:limit]:
        if not isinstance(value, str):
            continue
        stripped = value.strip()
        if stripped:
            normalized.append(stripped)
    return normalized


def _compact_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return " ".join(value.split())
    return str(value)


def _graph_name(agent: "GrcAgent") -> str | None:
    path = agent.session.path
    return path.name if path is not None else None
