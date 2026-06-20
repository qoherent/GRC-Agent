"""Read-only `inspect_graph` MVP wrapper."""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from grc_agent._payload import ErrorCode
from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent._payload import Block, Connection
from grc_agent.runtime.block_semantics import (
    EditableParameterCandidate,
    build_block_semantics_by_type,
    build_editable_parameter_candidates,
    evaluated_param_hides,
)
from grc_agent.runtime.block_semantics import _connection_summaries
from grc_agent.runtime.enums import SearchDomain
from grc_agent.runtime.text_utils import compact_whitespace, tokenize_identifier
from grc_agent.runtime.tool_context import is_meaningful, is_variable_block, truncate_list
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

    if targets and any(t.strip().lower() in ("*", "all") for t in targets):
        targets = []
        selected_view = "overview"

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
        output_truncated = bool(result.get("omitted"))
    else:
        result = _details(agent, targets=normalized_targets, params=normalized_params)
        output_truncated = bool(result.get("omitted"))

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
    del targets
    param_filter = (
        None if (_params_request_all(params) or not params) else set(_specific_params(params))
    )
    summary = summarize_graph(
        agent.session,
        max_blocks=agent._guardrails_cfg.max_graph_summary_blocks,
    )
    if not summary.get("ok"):
        return _base_payload(
            agent,
            ok=False,
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
        param_filter=param_filter,
    )
    limit = gc.max_graph_summary_blocks
    if summary.get("blocks_truncated"):
        block_rows = block_rows[:limit]
    val_state = agent.session.validation_state()
    graph = {
        "blocks": block_rows,
        "connections": shown_connections,
        "validation": {
            "status": val_state.get("status", "unknown"),
        },
    }
    omitted_blocks = int(summary.get("blocks_truncated") or 0)
    omitted_counts: dict[str, int] = {}
    if omitted_blocks:
        omitted_counts["blocks"] = omitted_blocks
    if omitted_connections:
        omitted_counts["connections"] = omitted_connections
    return _base_payload(
        agent,
        ok=True,
        graph=graph,
        omitted=omitted_counts,
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
    matched_params: set[str] = set()
    matched_requested_params: set[str] = set()
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
        if match.status == "ambiguous":
            matched_names = sorted(
                str(c.get("instance_name") or "")
                for c in match.candidates
                if c.get("instance_name")
            )
            errors.append(
                {
                    "code": "ambiguous_target",
                    "message": (
                        f"Target {requested_target!r} matched multiple graph objects: "
                        f"{', '.join(matched_names)}."
                    ),
                }
            )
            continue
        if match.status == "not_found" or match.block is None:
            valid_names = [
                block.instance_name for block in agent.session.flowgraph.blocks
            ]
            if match.message:
                message = match.message
            else:
                message = (
                    f"Target {requested_target!r} not found. "
                    f"Valid block names: {_format_valid_block_names(valid_names)}."
                )
            errors.append(
                {
                    "code": "target_not_found",
                    "message": message,
                }
            )
            continue

        block_candidates = by_block.get(match.block.block_uid, [])
        block_param_values = match.block.params.get("parameters") if isinstance(match.block.params, dict) else {}
        block_param_values = block_param_values if isinstance(block_param_values, dict) else {}
        evaluated_hides = evaluated_param_hides(match.block.block_type, block_param_values)
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
            evaluated_hides=evaluated_hides,
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
    return _base_payload(
        agent,
        ok=not errors,
        graph={"validation": {"status": "valid"}},
        targets=resolved_rows,
        omitted=omitted_counts,
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

    # Reject glob placeholders like "*block_name*" — these are
    # documentation-style examples, not real identifiers. Without this
    # guard, "*block_name*" can match real blocks via fuzzy scoring
    # (the tokens "block" and "name" overlap with most block names),
    # giving a misleading "success" that hides the model's mistake.
    if request.startswith("*") and request.endswith("*") and len(request) > 2:
        return _TargetMatch(
            request=request,
            status="not_found",
            message=(
                f"Target {request!r} looks like a documentation placeholder. "
                "Use a real block instance_name, block_uid, or block_type."
            ),
        )

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
            message=f"Target {request!r} uses unknown parameter key {param_key!r}.",
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
    evaluated_hides: dict[str, str],
    gc: Any,
) -> tuple[dict[str, Any], set[str], set[str], bool]:
    specific_params = _specific_params(params)
    requested_param_tokens = [_tokens(param) for param in specific_params]
    matched_params: set[str] = set()
    matched_requested_params: set[str] = set()
    selected_candidates: list[EditableParameterCandidate] = []
    for candidate in candidates:
        if not params:
            if _is_configured_or_prominent(candidate, evaluated_hides, variable_values):
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
    if _params_request_all(params) and not selected_candidates:
        selected_candidates = candidates[:gc.max_detail_params_requested]

    if selected_candidates and not specific_params:
        selected_candidates.sort(
            key=lambda candidate: (
                0 if evaluated_hides.get(candidate.param_key) == "none"
                else 1 if evaluated_hides.get(candidate.param_key) == "part"
                else 2
            )
        )

    if not params:
        param_limit = None
    elif _params_request_all(params):
        param_limit = gc.max_detail_params_all
    else:
        param_limit = gc.max_detail_params_requested
    if param_limit is None:
        returned_candidates = selected_candidates
        params_truncated = False
    else:
        params_truncated = len(selected_candidates) > param_limit
        returned_candidates = selected_candidates[:param_limit]
    parameters = [
        _parameter_payload(candidate, variable_values=variable_values)
        for candidate in returned_candidates
    ]
    available_param_count = len(candidates)
    returned_param_count = len(returned_candidates)
    omitted_param_count = max(0, len(selected_candidates) - returned_param_count)
    incoming = _connection_dicts(incoming_connections)
    outgoing = _connection_dicts(outgoing_connections)
    row: dict[str, Any] = {
        "request": requested,
        "matched_by": matched_by,
        "instance_name": block.instance_name,
        "block_type": block.block_type,
        "name": block.instance_name,
        "type": block.block_type,
        "role": _block_role(
            block,
            semantics={},
            incoming=incoming_connections,
            outgoing=outgoing_connections,
        ),
        "catalog_label": _block_label(candidates),
    }
    if parameters:
        row["parameters"] = parameters
    if incoming or outgoing:
        row["connections"] = {
            "incoming": incoming,
            "outgoing": outgoing,
        }
    if params_truncated:
        row["params_truncated"] = True
        row["omitted_param_count"] = omitted_param_count
    if not params and available_param_count > returned_param_count:
        row["available_param_count"] = available_param_count
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


def _is_configured_or_prominent(
    candidate: EditableParameterCandidate,
    evaluated_hides: dict[str, str],
    variable_values: dict[str, Any],
) -> bool:
    if evaluated_hides.get(candidate.param_key) == "all":
        return False
    value = _compact_value(candidate.current_value)
    if not is_meaningful(value):
        return False
    if evaluated_hides.get(candidate.param_key) == "none":
        return True
    if isinstance(variable_values, dict) and value in variable_values:
        return True
    default = _compact_value(candidate.param_default)
    if not is_meaningful(default):
        return False
    return value != default


def _variable_reference_map(
    blocks: list[Block],
    variable_values: dict[str, Any],
) -> dict[str, list[dict[str, str]]]:
    refs: dict[str, list[dict[str, str]]] = {}
    for block in blocks:
        params = block.params.get("parameters") if isinstance(block.params, dict) else None
        if not isinstance(params, dict):
            continue
        for key, value in params.items():
            if isinstance(value, str) and value in variable_values:
                refs.setdefault(value, []).append(
                    {"block": block.instance_name, "param": key}
                )
    return refs


def _all_variable_references(
    blocks: list[Block],
    variable_values: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Lifted from the ``param_filter`` gate: every variable → its references.

    The previous implementation only computed references for variables the
    model explicitly requested via ``params=[...]``. Surfacing the full map on
    every call means the model can answer "what uses variable X" without
    first asking for a narrower view, and never needs to fall back to the
    catalog to discover block/param names.
    """
    if not variable_values:
        return {}
    ref_map = _variable_reference_map(blocks, variable_values)
    return {
        name: {
            "value": variable_values[name],
            "referenced_by": ref_map.get(name, []),
        }
        for name in variable_values
    }


def _format_valid_block_names(names: list[str], *, limit: int = 20) -> str:
    """Format a sorted, capped list of valid block names for an error message.

    Sort: deterministic order. Cap: at most ``limit`` names plus a ``+N more`` suffix.
    The cap is uniform across all `target_not_found` errors so a 50-block graph
    does not produce a 900-char message.
    """
    deduped = sorted(set(n for n in names if n))
    if len(deduped) <= limit:
        return ", ".join(deduped)
    shown = deduped[:limit]
    remainder = len(deduped) - limit
    return f"{', '.join(shown)}, +{remainder} more"


def _param_keys_by_block(blocks: list[Block]) -> dict[str, dict[str, str]]:
    """Essential params per block: only prominent or configured, with values.

    Applies four GRC-native filters (see ``docs/GNU_NATIVE_METHODS.md``):

    1. ``hide != 'all'`` — drops hidden params.
    2. ``category != ADVANCED_PARAM_TAB`` — drops auto-added metadata.
    3. ``category != 'Config'`` — drops styling params.
    4. **Prominence**: ``hide == 'none'`` (always visible) OR value differs
       from catalog default (user has configured it). Params at default
       values are omitted — they're not interesting.

    Returns ``{block_name: {param_key: param_value}}`` — the actual
    configured values, not just key lists. Falls back to all visible
    keys (without values) if GRC evaluation is unavailable.
    """
    try:
        from gnuradio.grc.core.Constants import ADVANCED_PARAM_TAB
    except ImportError:
        ADVANCED_PARAM_TAB = "Advanced"
    _excluded = {ADVANCED_PARAM_TAB, "Config"}

    result: dict[str, dict[str, str]] = {}
    for block in blocks:
        params = block.params.get("parameters") if isinstance(block.params, dict) else None
        if not isinstance(params, dict):
            result[block.instance_name] = {}
            continue
        evaluated = evaluated_param_hides(block.block_type, params)
        if not evaluated:
            result[block.instance_name] = {str(k): str(v) for k, v in params.items()}
            continue

        param_cats = _platform_param_categories(block.block_type)
        # Get catalog defaults to detect "configured" (value != default)
        from grc_agent.catalog.loaders import describe_block
        details = describe_block(block.block_type)
        defaults = {}
        if details.get("ok"):
            for p in details.get("parameters", []):
                pid = p.get("id")
                if pid:
                    defaults[str(pid)] = str(p.get("default", ""))

        essential: dict[str, str] = {}
        for key, value in params.items():
            key_str = str(key)
            hide = evaluated.get(key_str, "all")
            cat = param_cats.get(key_str, "General")
            # Filter 1-3: visibility + category
            if hide == "all" or cat in _excluded:
                continue
            # Filter 4: prominence — hide='none' OR configured (value != default)
            is_prominent = hide == "none"
            default_val = defaults.get(key_str)
            is_configured = (
                default_val is not None
                and str(value).strip() != ""
                and str(value) != default_val
            )
            if is_prominent or is_configured:
                val_str = str(value).strip()
                if val_str:
                    essential[key_str] = val_str
        result[block.instance_name] = essential
    return result


def _platform_param_categories(block_type: str) -> dict[str, str]:
    """Read GRC's native ``category`` attribute for each param of a block type.

    Instantiates a throwaway flow graph block (same pattern as
    :func:`evaluated_param_hides`) and reads ``param.category`` for each
    parameter. Falls back to an empty dict if the platform is unavailable.
    """
    try:
        from grc_agent.session import _ensure_platform

        platform = _ensure_platform()
        if platform is None:
            return {}
        flow_graph = platform.make_flow_graph()
        block = flow_graph.new_block(block_type)
        if block is None:
            return {}
        return {
            str(name): str(getattr(param, "category", "General"))
            for name, param in block.params.items()
        }
    except Exception:
        return {}


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
    graph: dict[str, Any] | None = None,
    targets: list[dict[str, Any]] | None = None,
    errors: list[dict[str, Any]] | None = None,
    unmatched_params: list[str] | None = None,
    omitted: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Uniform model-visible payload for ``inspect_graph``.

    Shape (every call has the same five fields; ``errors`` and ``targets`` are
    populated only when relevant):

    - ``errors``: list of ``{code, message}`` — first-class, not nested in JSON.
    - ``unmatched_params``: param strings the model asked for that didn't match.
    - ``variable_references``: every variable → ``{value, referenced_by: [{block, param}]}``.
      Lifted from the previous ``param_filter`` gate so the model can answer
      "what uses X" without first requesting a narrower view.
    - ``param_keys_by_block``: every block → sorted list of its param keys.
      Surfaces ``GrcAgent._inspect_param_keys_by_block`` at the model boundary
      so the model can see what valid ``targets`` / ``params`` look like.
    - ``graph``: ``{graph_name, counts, blocks, connections}`` for overview,
      or just ``{graph_name, counts}`` for details.
    - ``targets`` (details only): block details rows.
    - ``omitted`` (optional): ``{blocks, connections, parameters}`` counts.

    The renderer in ``tool_context.py`` promotes every ``errors[i].message``
    to a structural line so failure facts are never buried inside a JSON dump.
    """
    assert agent.session.flowgraph is not None
    blocks = agent.session.flowgraph.blocks
    variable_values = _graph_variable_values(blocks)
    payload: dict[str, Any] = {
        "ok": ok,
        "params": _param_keys_by_block(blocks),
        "graph": graph if graph is not None else {},
    }
    if errors:
        payload["errors"] = list(errors)
    if variable_references := _all_variable_references(blocks, variable_values):
        payload["variable_references"] = variable_references
    if targets:
        payload["targets"] = targets
    if omitted:
        payload["omitted"] = omitted
    return payload


def _invalid_request(
    agent: GrcAgent,
    *,
    view: str,
    code: str,
    message: str,
) -> dict[str, Any]:
    del view
    return _base_payload(
        agent,
        ok=False,
        errors=[{"code": code, "message": message}],
    )


def _group_candidates_by_block(
    candidates: list[EditableParameterCandidate],
) -> dict[str, list[EditableParameterCandidate]]:
    grouped: dict[str, list[EditableParameterCandidate]] = {}
    for candidate in candidates:
        grouped.setdefault(candidate.block_uid, []).append(candidate)
    return grouped


def _candidate_payloads(blocks: list[Block], *, max_candidates: int = 12) -> list[dict[str, Any]]:
    payloads = [
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
    if len(blocks) > max_candidates:
        payloads.append({"_truncated": f"was {len(blocks)}, kept {max_candidates}"})
    return payloads


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
    param_filter: set[str] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for block in blocks:
        semantics = semantics_by_type.get(block.block_type, {})
        row = {
            "instance_name": block.instance_name,
            "block_type": block.block_type,
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
            if param_filter:
                meaningful = {k: v for k, v in params.items() if is_meaningful(v) and k != "value" and k in param_filter}
                if meaningful:
                    row["params"] = meaningful
        elif param_filter:
            block_params = block.params.get("parameters", {})
            if isinstance(block_params, dict):
                meaningful = {k: v for k, v in block_params.items() if is_meaningful(v) and k in param_filter}
                if meaningful:
                    row["params"] = meaningful
        rows.append({key: value for key, value in row.items() if is_meaningful(value)})
    return rows


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
    return set(tokenize_identifier(text))


def _normalize_text(text: Any) -> str:
    return compact_whitespace(str(text).lower())


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


# --- merged from get_grc_context_internal.py ---

ContextFn = Callable[..., dict[str, Any]]
SymbolResolver = Callable[[str], str | None]


def get_grc_context_internal(
    node_id: str,
    *,
    hops: int,
    max_nodes: int | None,
    session: FlowgraphSession,
    catalog_root: Path | None,
    default_max_nodes: int,
    symbol_resolver: SymbolResolver,
    context_fn: ContextFn,
) -> dict[str, Any]:
    resolved_node_id = symbol_resolver(node_id) or node_id
    resolved_max_nodes = default_max_nodes if max_nodes is None else max_nodes
    payload = context_fn(
        session,
        resolved_node_id,
        hops=hops,
        max_nodes=resolved_max_nodes,
    )
    if payload.get("ok") is False and payload.get("error_type") == ErrorCode.BLOCK_NOT_FOUND:
        if session.flowgraph is not None:
            fallback_candidates = [
                b.instance_name for b in session.flowgraph.blocks[: min(5, max_nodes)]
            ]
            if fallback_candidates:
                payload["candidate_nodes"] = fallback_candidates
                payload["hint"] = (
                    "Specified block name was not found. "
                    f"Available block names: {', '.join(fallback_candidates)}."
                )
    return payload


# --- merged from query_knowledge.py ---


def query_knowledge(
    agent: GrcAgent,
    query: str,
    domain: str,
    debug: bool = False,
) -> ToolResult:
    """Query GNU Radio knowledge — catalog (block IDs/params) or docs (concepts)."""
    started = time.monotonic()

    if domain not in {SearchDomain.CATALOG, SearchDomain.DOCS}:
        return agent._tool_result(
            "query_knowledge",
            ok=False,
            message=f"Invalid domain '{domain}'.",
            error_type="invalid_request",
        )

    if domain == SearchDomain.CATALOG:
        from grc_agent.runtime.search_blocks import search_blocks as _search
        result = _search(agent, query=query, debug=debug)
    else:
        from grc_agent.runtime.doc_answer import ask_grc_docs as _docs
        result = _docs(agent, question=query, debug=debug)

    if isinstance(result, dict):
        result.pop("active_session", None)  # internal snapshot, not needed for discovery
        result["domain"] = domain
        result["query_knowledge_time"] = round(time.monotonic() - started, 3)
    return result
