"""Read-only ``inspect_graph`` + ``query_knowledge`` wrappers.

inspect_graph delegates to :mod:`grc_agent.grc_native_adapter` — no dict-crawl.
query_knowledge routes to :mod:`grc_agent.runtime.search_blocks` (catalog)
or :mod:`grc_agent.runtime.doc_answer` (docs RAG).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from grc_agent.domain_models import ErrorCode
from grc_agent.grc_native_adapter import render_flow_graph
from grc_agent.runtime.connection_ids import parse_connection_id
from grc_agent.runtime.enums import SearchDomain
from grc_agent.runtime.param_filter import OVERVIEW

if TYPE_CHECKING:
    from grc_agent.agent import GrcAgent, ToolResult

VALID_VIEWS = {"overview"}

# Redundant/dead fields stripped from the model-facing inspect payload:
# - top-level ``ok``: mirrors ``validation.status`` and the outer transport
#   envelope already carries its own ``ok`` (the dual-ok ambiguity).
# - top-level ``errors``: always ``[]`` (real errors live in
#   ``validation.errors``).
# - ``validation.native_ok``: always ``== (status == "valid")``.
# All three carry zero information. Internal consumers (flowgraph_session,
# run_agent_flow) read the :class:`GrcFlowgraph` model directly, not this dump.
_INSPECT_PAYLOAD_EXCLUDE = {"ok": True, "errors": True, "validation": {"native_ok": True}}


# --------------------------------------------------------------------------- #
# inspect_graph                                                                #
# --------------------------------------------------------------------------- #


def inspect_graph(
    agent: GrcAgent,
    *,
    view: str = "overview",
    targets: list[str],
) -> ToolResult:
    selected_view = str(view).strip().lower()

    missing_session = agent._missing_session_result("inspect_graph")
    if missing_session is not None:
        return missing_session

    gc = agent._guardrails_cfg
    normalized_targets = _normalize_string_list(targets, limit=gc.max_inspect_targets)
    whole_graph = not normalized_targets or any(t in ("all", "*") for t in normalized_targets)

    if selected_view not in VALID_VIEWS:
        result = _base_payload(
            agent,
            errors=[
                {
                    "code": "invalid_view",
                    "message": "inspect_graph.view must be 'overview'.",
                }
            ],
        )
    elif len(targets) > gc.max_inspect_targets:
        result = _base_payload(
            agent,
            errors=[
                {
                    "code": "target_limit_exceeded",
                    "message": f"inspect_graph accepts at most {gc.max_inspect_targets} targets.",
                }
            ],
        )
    elif whole_graph:
        result = _overview(agent)
    else:
        result = _specific(agent, targets=normalized_targets)

    return agent._payload_result("inspect_graph", result)


def _overview(agent: GrcAgent) -> dict[str, Any]:
    fg = agent.session.flowgraph
    if fg is None:
        return _base_payload(
            agent, errors=[{"code": "no_flowgraph", "message": "No flowgraph loaded."}]
        )
    snapshot = render_flow_graph(fg, mode=OVERVIEW)
    payload = snapshot.model_dump(
        exclude_none=True, exclude=_INSPECT_PAYLOAD_EXCLUDE
    )
    return _base_payload(agent, graph=payload)


def _specific(agent: GrcAgent, *, targets: list[str]) -> dict[str, Any]:
    """Overview-filtered snapshot scoped to the requested block instance_names.

    Same Stage A+B filter and the same per-block shape as :func:`_overview`;
    only the scope differs — returned ``blocks`` are the requested ones and
    ``connections`` are those touching any requested block (so the agent can
    reason about rewires). Unknown names yield a ``block_not_found`` error
    listing every valid block name.
    """
    fg = agent.session.flowgraph
    if fg is None:
        return _base_payload(
            agent, errors=[{"code": "no_flowgraph", "message": "No flowgraph loaded."}]
        )
    snapshot = render_flow_graph(fg, mode=OVERVIEW)
    payload = snapshot.model_dump(
        exclude_none=True, exclude=_INSPECT_PAYLOAD_EXCLUDE
    )

    blocks = payload.get("blocks", [])
    connections = payload.get("connections", [])
    valid_names = [b["instance_name"] for b in blocks]
    valid_set = set(valid_names)
    missing = [t for t in targets if t not in valid_set]
    if missing:
        return _base_payload(
            agent,
            errors=[
                {
                    "code": "block_not_found",
                    "message": f"Unknown block name(s): {', '.join(missing)}",
                    "valid_block_names": sorted(valid_set),
                }
            ],
        )

    requested = set(targets)
    payload["blocks"] = [b for b in blocks if b["instance_name"] in requested]
    payload["connections"] = [
        c for c in connections if _connection_touches(c, requested)
    ]
    return _base_payload(agent, ok=True, graph=payload)


def _connection_touches(conn_id: str, requested: set[str]) -> bool:
    """True if the connection's source or destination block is requested."""
    parsed = parse_connection_id(conn_id)
    return bool(parsed) and (parsed["src_block"] in requested or parsed["dst_block"] in requested)


def _base_payload(
    agent: GrcAgent,
    *,
    graph: dict[str, Any] | None = None,
    errors: list[dict[str, str]] | None = None,
    ok: bool | None = None,
) -> dict[str, Any]:
    actual_ok = ok if ok is not None else (not bool(errors))
    payload: dict[str, Any] = {
        "ok": actual_ok,
        "graph": graph if graph is not None else {},
    }
    if errors:
        payload["errors"] = list(errors)
    return payload


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
# query_knowledge (unchanged — Phase 3 proved no native refactor needed)      #
# --------------------------------------------------------------------------- #


def query_knowledge(
    agent: GrcAgent,
    query: str,
    domain: str,
) -> ToolResult:
    """Query GNU Radio knowledge — catalog (block IDs/params) or docs (concepts)."""
    if domain not in {SearchDomain.CATALOG, SearchDomain.DOCS}:
        return agent._tool_result(
            "query_knowledge",
            ok=False,
            message=f"Invalid domain '{domain}'.",
            error_type=ErrorCode.INVALID_REQUEST,
        )

    if domain == SearchDomain.CATALOG:
        from grc_agent.runtime.search_blocks import search_blocks as _search

        result = _search(agent, query=query)
    else:
        from grc_agent.runtime.doc_answer import ask_grc_docs as _docs

        result = _docs(agent, question=query)

    if isinstance(result, dict):
        result.pop("active_session", None)
    return result
