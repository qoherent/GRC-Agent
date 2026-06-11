"""Read-only `inspect_graph` MVP wrapper."""

from __future__ import annotations

import time
from collections import Counter
from typing import TYPE_CHECKING, Any

from grc_agent.models import Block, Connection
from grc_agent.runtime.block_semantics import build_block_semantics_by_type
from grc_agent.runtime.editable_parameters import (
    EditableParameterCandidate,
    build_editable_parameter_candidates,
)
from grc_agent.runtime.output_policy import is_meaningful, is_variable_block, truncate_list
from grc_agent.session import summarize_graph
from grc_agent.session_ops import connection_id as render_connection_id

if TYPE_CHECKING:
    from grc_agent.agent import GrcAgent, ToolResult

VALID_VIEWS = {"overview", "details"}


def inspect_graph(
    agent: GrcAgent,
    *,
    view: str,
    targets: list[str],
    params: list[str],
    debug: bool = False,
) -> ToolResult:
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

    gc = agent._guardrails_cfg
    normalized_targets = _normalize_string_list(targets, limit=gc.max_inspect_targets)
    normalized_params = _normalize_string_list(params, limit=gc.max_inspect_params)
    if selected_view not in VALID_VIEWS:
        result = _invalid_request(
            agent,
            view=selected_view,
            code="invalid_view",
            message="inspect_graph.view must be 'overview' or 'details'.",
        )
    elif len(targets) > gc.max_inspect_targets:
        result = _invalid_request(
            agent,
            view=selected_view,
            code="target_limit_exceeded",
            message=f"inspect_graph accepts at most {gc.max_inspect_targets} targets.",
        )
    elif len(params) > gc.max_inspect_params:
        result = _invalid_request(
            agent,
            view=selected_view,
            code="param_limit_exceeded",
            message=f"inspect_graph accepts at most {gc.max_inspect_params} params.",
        )
    elif selected_view == "overview":
        result = _overview(agent, targets=normalized_targets, params=normalized_params)
        output_truncated = bool(result.get("truncation", {}).get("truncated"))
    else:
        result = _details(agent, targets=normalized_targets, params=normalized_params)
        output_truncated = bool(result.get("truncation", {}).get("truncated"))

    tool_result = agent._payload_result(
        "inspect_graph",
        result,
        include_active_session=False,
    )
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
    agent: GrcAgent,
    *,
    targets: list[str],
    params: list[str],
) -> dict[str, Any]:
    del targets, params
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
    block_types = Counter(block.block_type for block in agent.session.flowgraph.blocks)
    semantics_by_type = build_block_semantics_by_type(
        block_types,
        catalog_root=agent.catalog_root,
    )
    connections = [
        render_connection_id(
            connection.src_block,
            connection.src_port,
            connection.dst_block,
            connection.dst_port,
        )
        for connection in agent.session.flowgraph.connections
    ]
    gc = agent._guardrails_cfg
    shown_connections, omitted_connections_list = truncate_list(connections, gc.max_overview_connections)
    omitted_connections = len(omitted_connections_list)
    incoming, outgoing = _connection_summaries(agent.session.flowgraph.connections)
    block_rows = _overview_block_rows(
        agent.session.flowgraph.blocks,
        semantics_by_type=semantics_by_type,
        incoming=incoming,
        outgoing=outgoing,
    )
    overview = {
        "graph_name": _graph_name(agent),
        "counts": {
            "blocks": summary.get("block_count", 0),
            "connections": summary.get("connection_count", 0),
            "variables": summary.get("variable_count", 0),
        },
        "blocks": block_rows,
        "connections": shown_connections,
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
    agent: GrcAgent,
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
    gc = agent._guardrails_cfg
    candidates = build_editable_parameter_candidates(
        agent.session,
        catalog_root=agent.catalog_root,
        include_connections=True,
    )
    variable_values = _graph_variable_values(agent.session.flowgraph.blocks)
    by_block = _group_candidates_by_block(candidates)
    connections = list(agent.session.flowgraph.connections)
    incoming, outgoing = _connection_summaries(connections)
    resolved_rows: list[dict[str, Any]] = []
    target_matches: list[dict[str, Any]] = []
    matched_params: set[str] = set()
    matched_requested_params: set[str] = set()
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
        if match.status != "resolved":
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
            message = match.message or f"Target {requested_target!r} did not match any active block instance or connection in the graph."
            errors.append(
                {
                    "code": "target_not_found",
                    "message": message,
                }
            )
            continue

        block_candidates = by_block.get(match.block.block_uid, [])
        row, row_matched_params, row_matched_requested, row_truncated = _block_details_row(
            match.block,
            block_candidates,
            requested=requested_target,
            matched_by=match.matched_by,
            params=params,
            state_revision=agent.session.state_revision,
            incoming_connections=incoming.get(match.block.instance_name, ()),
            outgoing_connections=outgoing.get(match.block.instance_name, ()),
            variable_values=variable_values,
            gc=gc,
        )
        resolved_rows.append(row)
        matched_params.update(row_matched_params)
        matched_requested_params.update(row_matched_requested)
        if row_truncated:
            truncated = True
            omitted = row.get("omitted_param_count")
            omitted_counts["parameters"] = omitted_counts.get("parameters", 0) + (
                omitted if isinstance(omitted, int) and omitted > 0 else 1
            )

    if _params_request_all(params):
        unmatched_params = []
    else:
        matched_requested_norm = {_normalize_text(item) for item in matched_requested_params}
        unmatched_params = [
            param
            for param in params
            if _normalize_text(param) not in matched_requested_norm
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
        ambiguity={
            "has_ambiguity": has_ambiguity,
            "reason": "Target matched multiple graph objects."
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
        message: str | None = None,
    ) -> None:
        self.request = request
        self.status = status
        self.block = block
        self.matched_by = matched_by
        self.candidates = candidates or []
        self.message = message

    def match_payload(self) -> dict[str, Any]:
        return {
            "request": self.request,
            "status": self.status,
            "resolved_name": self.block.instance_name if self.block is not None else None,
            "matched_by": self.matched_by,
            "candidates": self.candidates,
            "message": self.message,
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

    invalid_ref = _unknown_parameter_ref(request, blocks, candidates)
    if invalid_ref is not None:
        block, param_key = invalid_ref
        return _TargetMatch(
            request=request,
            status="not_found",
            candidates=[
                {
                    "name": block.instance_name,
                    "kind": "block",
                    "type": block.block_type,
                    "uid": block.block_uid,
                    "reason": f"unknown parameter key {param_key!r}",
                }
            ],
            message=(
                f"Target {request!r} uses unknown parameter key {param_key!r}. "
                "Use an exact block.param reference or pass the block in targets "
                "and the parameter key in params."
            ),
        )

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


def _unknown_parameter_ref(
    request: str,
    blocks: list[Block],
    candidates: list[EditableParameterCandidate],
) -> tuple[Block, str] | None:
    if "." not in request:
        return None
    by_block = _group_candidates_by_block(candidates)
    for block in sorted(blocks, key=lambda item: len(item.instance_name), reverse=True):
        prefix = f"{block.instance_name}."
        if not request.startswith(prefix):
            continue
        param_key = request.removeprefix(prefix)
        known_params = {candidate.param_key for candidate in by_block.get(block.block_uid, [])}
        if param_key and param_key not in known_params:
            return block, param_key
    return None


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
    state_revision: int,
    incoming_connections: tuple[str, ...],
    outgoing_connections: tuple[str, ...],
    variable_values: dict[str, Any],
    gc: Any,
) -> tuple[dict[str, Any], set[str], set[str], bool]:
    specific_params = _specific_params(params)
    requested_param_tokens = [_tokens(param) for param in specific_params]
    matched_params: set[str] = set()
    matched_requested_params: set[str] = set()
    selected_candidates: list[EditableParameterCandidate] = []
    for candidate in candidates:
        if not params:
            if len(candidates) <= gc.min_detail_params_before_truncation or _default_detail_param(candidate):
                selected_candidates.append(candidate)
                matched_params.add(candidate.param_key)
        elif _params_request_all(params):
            selected_candidates.append(candidate)
            matched_params.add(candidate.param_key)
        elif specific_params:
            matched_requests = _matched_param_requests(
                candidate,
                specific_params,
                requested_param_tokens,
            )
            if matched_requests:
                selected_candidates.append(candidate)
                matched_params.add(candidate.param_key)
                matched_requested_params.update(matched_requests)
    if not params and len(candidates) <= gc.min_detail_params_before_truncation and not selected_candidates:
        selected_candidates = candidates[:gc.min_detail_params_before_truncation]
    elif _params_request_all(params) and not selected_candidates:
        selected_candidates = candidates[:gc.max_detail_params_requested]

    if not params:
        param_limit = gc.max_detail_params_default
    elif _params_request_all(params):
        param_limit = gc.max_detail_params_all
    else:
        param_limit = gc.max_detail_params_requested
    params_truncated = len(selected_candidates) > param_limit
    returned_candidates = selected_candidates[:param_limit]
    parameters = [
        _parameter_payload(candidate, variable_values=variable_values)
        for candidate in returned_candidates
    ]
    available_param_count = len(candidates)
    returned_param_count = len(returned_candidates)
    omitted_param_count = max(0, len(selected_candidates) - returned_param_count)
    more_params_available = available_param_count > returned_param_count
    incoming = _connection_dicts(incoming_connections)
    outgoing = _connection_dicts(outgoing_connections)
    row: dict[str, Any] = {
        "request": requested,
        "matched_by": matched_by,
        "instance_name": block.instance_name,
        "block_type": block.block_type,
        "name": block.instance_name,
        "type": block.block_type,
        "catalog_label": _block_label(candidates),
    }
    if parameters:
        row["parameters"] = parameters
    if incoming or outgoing:
        row["connections"] = {
            "incoming": incoming[:gc.max_connections_per_block],
            "outgoing": outgoing[:gc.max_connections_per_block],
        }
    if params_truncated:
        row["params_truncated"] = True
        row["omitted_param_count"] = omitted_param_count
    elif not params and more_params_available:
        row["more_params_available"] = True
        row["available_param_count"] = available_param_count
        row["params_omitted"] = True
    return row, matched_params, matched_requested_params, params_truncated


def _parameter_payload(
    candidate: EditableParameterCandidate,
    *,
    variable_values: dict[str, Any],
) -> dict[str, Any]:
    value = _compact_value(candidate.current_value)
    payload: dict[str, Any] = {
        "name": candidate.param_key,
        "label": candidate.param_label,
        "dtype": candidate.param_dtype,
        "value": value,
    }
    if isinstance(value, str) and value in variable_values:
        payload["resolved_value"] = _compact_value(variable_values[value])
    value_label = _option_label_for_current(candidate)
    if value_label is not None:
        payload["value_label"] = value_label
    if candidate.param_options:
        payload["options"] = list(candidate.param_options)
    if candidate.param_option_labels:
        payload["option_labels"] = list(candidate.param_option_labels)
    return {key: value for key, value in payload.items() if is_meaningful(value)}


def _graph_variable_values(blocks: list[Block]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for block in blocks:
        params = block.params.get("parameters") if isinstance(block.params, dict) else None
        if not isinstance(params, dict) or "value" not in params:
            continue
        if is_variable_block(block.block_type):
            values[block.instance_name] = params.get("value")
    return values


def _matched_param_requests(
    candidate: EditableParameterCandidate,
    params: list[str],
    token_sets: list[set[str]],
) -> set[str]:
    fields = [candidate.param_key, candidate.param_label]
    matched: set[str] = set()
    for param, tokens in zip(params, token_sets, strict=False):
        if _any_field_matches(fields, request=param, tokens=tokens):
            matched.add(param)
    return matched


def _base_payload(
    agent: GrcAgent,
    *,
    ok: bool,
    view: str,
    complete: bool,
    summary: Any = None,
    targets: list[dict[str, Any]] | None = None,
    target_matches: list[dict[str, Any]] | None = None,
    params_filter: dict[str, Any] | None = None,
    ambiguity: dict[str, Any] | None = None,
    truncation: dict[str, Any] | None = None,
    errors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    validation = _validation_status(agent)
    payload: dict[str, Any] = {
        "ok": ok,
        "view": view,
        "state_revision": agent.session.state_revision,
        "complete": complete,
        "validation_status": validation,
    }
    if validation.get("errors"):
        payload["validation_errors"] = validation["errors"]
    if summary is not None:
        payload["summary"] = summary
    if targets:
        payload["targets"] = targets
    if target_matches:
        payload["target_matches"] = target_matches
    if params_filter and params_filter.get("unmatched"):
        payload["params_filter"] = params_filter
    if ambiguity and ambiguity.get("has_ambiguity"):
        payload["ambiguity"] = ambiguity
    if truncation and truncation.get("truncated"):
        payload["truncation"] = truncation
    if errors:
        payload["errors"] = errors
    return payload


def _invalid_request(
    agent: GrcAgent,
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


def _validation_status(agent: GrcAgent) -> dict[str, Any]:
    if not agent.session.flowgraph:
        return {"status": "unknown"}
    agent.session.validate()
    state = agent.session.validation_state()
    result: dict[str, Any] = {
        "status": state.get("status"),
        "last_checked_revision": state.get("state_revision"),
    }
    raw_stderr = state.get("stderr") or ""
    raw_stdout = state.get("stdout") or ""
    error_text = raw_stdout or raw_stderr
    if error_text.strip() and result["status"] == "invalid":
        result["summary"] = error_text.strip()
        errors: list[str] = []
        in_errors = False
        for line in error_text.splitlines():
            line = line.strip()
            if not line or line.startswith("*"):
                continue
            if "errors from flowgraph" in line:
                in_errors = True
                continue
            if line.startswith(">>>") or "Welcome" in line:
                continue
            if in_errors:
                if line:
                    errors.append(line.strip("\t"))
        if errors:
            result["errors"] = errors[:12]
    return result


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


def _candidate_payloads(blocks: list[Block], *, max_candidates: int = 12) -> list[dict[str, Any]]:
    return [
        {
            "instance_name": block.instance_name,
            "block_type": block.block_type,
            "name": block.instance_name,
            "kind": "block",
            "type": block.block_type,
            "uid": block.block_uid,
            "reason": "graph-local target candidate",
        }
        for block in blocks[:max_candidates]
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
    semantics_by_type: dict[str, dict[str, Any]],
    incoming: dict[str, tuple[str, ...]],
    outgoing: dict[str, tuple[str, ...]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for block in blocks:
        semantics = semantics_by_type.get(block.block_type, {})
        row = {
            "instance_name": block.instance_name,
            "block_type": block.block_type,
            "catalog_label": semantics.get("label"),
            "role": _block_role(
                block,
                semantics=semantics,
                incoming=incoming.get(block.instance_name, ()),
                outgoing=outgoing.get(block.instance_name, ()),
            ),
        }
        if is_variable_block(block.block_type):
            params = block.params.get("parameters", {})
            if isinstance(params, dict) and "value" in params:
                row["value"] = params["value"]
            meaningful = {k: v for k, v in params.items() if is_meaningful(v) and k != "value"}
            if meaningful:
                row["params"] = meaningful
        else:
            block_params = block.params.get("parameters", {})
            if isinstance(block_params, dict):
                meaningful = {k: v for k, v in block_params.items() if is_meaningful(v)}
                if meaningful:
                    row["params"] = meaningful
        rows.append({key: value for key, value in row.items() if is_meaningful(value)})
    return rows


def _default_detail_param(candidate: EditableParameterCandidate) -> bool:
    hide = str(candidate.param_hide or "").strip().lower()
    if hide and hide != "none":
        return False
    if isinstance(candidate.param_label, str) and candidate.param_label.strip():
        value = _compact_value(candidate.current_value)
        return is_meaningful(value)
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
    semantics: dict[str, Any],
    incoming: tuple[str, ...],
    outgoing: tuple[str, ...],
) -> str:
    role = semantics.get("role")
    if isinstance(role, str) and role.strip():
        return role.strip()
    if outgoing and not incoming:
        return "source"
    if incoming and not outgoing:
        return "sink"
    if incoming and outgoing:
        return "transform"
    return "metadata"


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
        if tokens and tokens.issubset(meaningful):
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


def _graph_name(agent: GrcAgent) -> str | None:
    path = agent.session.path
    return path.name if path is not None else None
