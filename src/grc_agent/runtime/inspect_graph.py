"""Read-only ``inspect_graph`` + ``query_knowledge`` wrappers (Phase 6 cutover).

inspect_graph delegates to :mod:`grc_agent.grc_native_adapter` — no dict-crawl.
query_knowledge is unchanged (Phase 3 proved no native refactor needed).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from grc_agent._payload import ErrorCode
from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.grc_native_adapter import render_flow_graph
from grc_agent.runtime.enums import SearchDomain
from grc_agent.runtime.param_filter import PROMINENCE, filter_live_block_params

if TYPE_CHECKING:
    from grc_agent.agent import GrcAgent, ToolResult

VALID_VIEWS = {"overview", "details"}


# --------------------------------------------------------------------------- #
# inspect_graph                                                                #
# --------------------------------------------------------------------------- #


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
        result = _base_payload(agent, ok=False,
                               errors=[{"code": "invalid_view",
                                        "message": "inspect_graph.view must be 'overview' or 'details'."}])
    elif len(targets) > gc.max_inspect_targets:
        result = _base_payload(agent, ok=False,
                               errors=[{"code": "target_limit_exceeded",
                                        "message": f"inspect_graph accepts at most {gc.max_inspect_targets} targets."}])
    elif len(params) > gc.max_inspect_params:
        result = _base_payload(agent, ok=False,
                               errors=[{"code": "param_limit_exceeded",
                                        "message": f"inspect_graph accepts at most {gc.max_inspect_params} params."}])
    elif selected_view == "overview":
        result = _overview(agent, targets=normalized_targets, params=normalized_params)
    else:
        result = _details(agent, targets=normalized_targets, params=normalized_params)

    output_truncated = bool(result.get("omitted"))
    tool_result = agent._payload_result("inspect_graph", result, include_active_session=False)
    return agent._attach_wrapper_dispatch_telemetry(
        debug=debug,
        wrapper_name="inspect_graph",
        wrapper_action=selected_view or "invalid",
        internal_handlers=["inspect_graph_view"],
        started=started,
        before_revision=before_revision,
        before_dirty=before_dirty,
        result=tool_result,
        validation_run=False,
        output_truncated=output_truncated,
    )


def _overview(agent: GrcAgent, *, targets: list[str], params: list[str]) -> dict[str, Any]:
    fg = agent.session.flowgraph
    if fg is None:
        return _base_payload(agent, ok=False,
                             errors=[{"code": "no_flowgraph", "message": "No flowgraph loaded."}])
    snapshot = render_flow_graph(fg)
    payload = snapshot.model_dump(exclude_none=True)
    return _base_payload(agent, ok=True, graph=payload)


def _details(agent: GrcAgent, *, targets: list[str], params: list[str]) -> dict[str, Any]:
    fg = agent.session.flowgraph
    if fg is None:
        return _base_payload(agent, ok=False,
                             errors=[{"code": "no_flowgraph", "message": "No flowgraph loaded."}])
    snapshot = render_flow_graph(fg)
    all_blocks = {b.instance_name: b for b in snapshot.blocks}
    matched: list[str] = []
    errors: list[dict[str, str]] = []
    for target in targets:
        if target in all_blocks:
            matched.append(target)
        else:
            errors.append({"code": "target_not_found", "message": f"Block '{target}' not found."})
    target_rows = []
    for name in matched:
        block = all_blocks[name]
        row = {
            "instance_name": block.instance_name,
            "block_type": block.block_type,
            "role": block.role.value if hasattr(block.role, "value") else str(block.role),
            "state": block.state,
            "parameters": [p.model_dump(exclude_none=True) for p in block.parameters],
        }
        target_rows.append(row)
    payload = snapshot.model_dump(exclude_none=True)
    result = _base_payload(agent, ok=len(errors) == 0, graph=payload)
    if target_rows:
        result["targets"] = target_rows
    if errors:
        result["errors"] = errors
    return result


def _base_payload(agent: GrcAgent, *, ok: bool,
                  graph: dict[str, Any] | None = None,
                  errors: list[dict[str, str]] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": ok,
        "graph": graph if graph is not None else {},
    }
    if errors:
        payload["errors"] = list(errors)
    return payload


def _param_keys_by_block(blocks: list[Any]) -> dict[str, dict[str, str]]:
    """Thin delegate to the unified filter — kept for agent.py compatibility."""
    from grc_agent.runtime.tool_context import is_variable_block
    variable_names = {b.name for b in blocks if is_variable_block(b.key)}
    result: dict[str, dict[str, str]] = {}
    for block in blocks:
        params = {}
        for k, p in block.params.items():
            params[k] = str(p.value)
        result[block.name or block.key] = filter_live_block_params(
            block.key, params, mode=PROMINENCE, variable_names=variable_names,
        )
    return result


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #


def _normalize_string_list(values: list[str], *, limit: int) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        s = str(v).strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out[:limit]


# --------------------------------------------------------------------------- #
# get_grc_context_internal (kept for agent.py compat; native block names)     #
# --------------------------------------------------------------------------- #


def get_grc_context_internal(
    node_id: str,
    *,
    hops: int,
    max_nodes: int | None,
    session: FlowgraphSession,
    catalog_root: Path | None,
    default_max_nodes: int,
    symbol_resolver: Any,
    context_fn: Any,
) -> dict[str, Any]:
    resolved_node_id = symbol_resolver(node_id) or node_id
    resolved_max_nodes = default_max_nodes if max_nodes is None else max_nodes
    payload = context_fn(
        session, resolved_node_id, hops=hops, max_nodes=resolved_max_nodes,
    )
    if payload.get("ok") is False and payload.get("error_type") == ErrorCode.BLOCK_NOT_FOUND:
        if session.flowgraph is not None:
            fallback_candidates = [
                b.name for b in session.flowgraph.blocks[: min(5, resolved_max_nodes)]
            ]
            if fallback_candidates:
                payload["candidate_nodes"] = fallback_candidates
    return payload


# --------------------------------------------------------------------------- #
# query_knowledge (unchanged — Phase 3 proved no native refactor needed)      #
# --------------------------------------------------------------------------- #


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
        result.pop("active_session", None)
    return result
