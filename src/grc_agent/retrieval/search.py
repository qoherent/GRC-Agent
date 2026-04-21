"""Deterministic search over catalog and session retrieval indexes."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from grc_agent._payload import ErrorCode, build_error_payload
from grc_agent.flowgraph_session import FlowgraphSession

from .graphify_adapter import GraphifyAdapterError
from .index import RetrievalIndexError, build_session_index, get_catalog_index
from .schema import (
    DEFAULT_RESULT_LIMIT,
    MAX_RESULT_LIMIT,
    PreparedSearchNode,
    VALID_SCOPES,
    IndexedNode,
    RetrievalIndex,
    build_success_payload,
)
from .text import expand_terms, normalize_text, tokenize_text

FIELD_WEIGHTS = {
    "label": 18.0,
    "identifier": 16.0,
    "summary": 9.0,
    "related": 5.0,
}

PHRASE_BONUSES = {
    "label": 24.0,
    "identifier": 20.0,
    "summary": 10.0,
    "related": 5.0,
}

NODE_TYPE_BONUS = {
    "session_block": 8.0,
    "block": 7.0,
    "category": 1.5,
    "domain": 1.5,
}

_BOUND_SESSION: ContextVar[FlowgraphSession | None] = ContextVar(
    "grc_agent_retrieval_bound_session", default=None
)
_BOUND_CATALOG_ROOT: ContextVar[str | None] = ContextVar(
    "grc_agent_retrieval_bound_catalog_root", default=None
)
_BOUND_SESSION_INDEX: ContextVar[RetrievalIndex | None] = ContextVar(
    "grc_agent_retrieval_bound_session_index", default=None
)
_BOUND_SESSION_INDEX_KEY: ContextVar[tuple[str, int, str | None, str | None] | None] = (
    ContextVar("grc_agent_retrieval_bound_session_index_key", default=None)
)


@dataclass(frozen=True)
class _SearchMatch:
    node: IndexedNode
    score: float
    reason: str


def bind_retrieval_context(
    *,
    session: FlowgraphSession | None = None,
    catalog_root: str | None = None,
) -> None:
    """Bind retrieval context for the narrow public search surface.

    The binding is context-local so concurrent callers do not overwrite each
    other's active session or session-index cache.
    """
    _BOUND_SESSION.set(session)
    _BOUND_CATALOG_ROOT.set(catalog_root)
    _BOUND_SESSION_INDEX.set(None)
    _BOUND_SESSION_INDEX_KEY.set(None)


_bind_retrieval_context = bind_retrieval_context


def _clear_retrieval_context() -> None:
    """Clear any previously bound runtime retrieval context."""
    bind_retrieval_context(session=None, catalog_root=None)


def search_grc(
    query: str, scope: str = "catalog", k: int = DEFAULT_RESULT_LIMIT
) -> dict[str, Any]:
    """Search the GNU Radio catalog or active session with bounded structured results."""
    return _search_grc_with_context(
        query,
        scope=scope,
        k=k,
        session=_BOUND_SESSION.get(),
        catalog_root=_BOUND_CATALOG_ROOT.get(),
    )


def _search_grc_with_context(
    query: str,
    scope: str = "catalog",
    k: int = DEFAULT_RESULT_LIMIT,
    *,
    session: FlowgraphSession | None = None,
    catalog_root: str | None = None,
) -> dict[str, Any]:
    """Internal search helper that accepts explicit runtime context."""
    normalized_query = _normalize_query_text(query)
    if not normalized_query:
        return build_error_payload(
            error_type=ErrorCode.INVALID_REQUEST,
            message="Query must be a non-empty string.",
        )

    searchable_query = normalize_text(normalized_query)
    query_tokens = list(tokenize_text(normalized_query))
    if not query_tokens:
        return build_error_payload(
            error_type=ErrorCode.INVALID_REQUEST,
            message="Query must contain at least one searchable letter or digit.",
        )
    query_terms = list(expand_terms(query_tokens))

    if scope not in VALID_SCOPES:
        return build_error_payload(
            error_type=ErrorCode.INVALID_REQUEST,
            message=f"Unsupported search scope: {scope}",
            details={"supported_scopes": sorted(VALID_SCOPES)},
        )

    try:
        applied_limit, warnings = _normalize_limit(k)
    except ValueError as exc:
        return build_error_payload(error_type=ErrorCode.INVALID_REQUEST, message=str(exc))

    try:
        if scope == "catalog":
            retrieval_index = get_catalog_index(catalog_root)
        else:
            if session is None:
                return build_error_payload(
                    error_type=ErrorCode.MISSING_SESSION,
                    message="Session scope requires a loaded FlowgraphSession.",
                )
            retrieval_index = _get_session_index(session, catalog_root=catalog_root)
    except (GraphifyAdapterError, RetrievalIndexError) as exc:
        return build_error_payload(
            error_type=ErrorCode.INTERNAL_ERROR,
            message=str(exc),
        )

    results = _search_index(
        retrieval_index,
        normalized_query=normalized_query,
        searchable_query=searchable_query,
        query_tokens=query_tokens,
        query_terms=query_terms,
        applied_limit=applied_limit,
    )
    if not results:
        warnings.append(f"No {scope} matches found for '{normalized_query}'.")

    return build_success_payload(
        scope=scope,
        query=normalized_query,
        results=[
            match.node.to_result(score=match.score, reason=match.reason)
            for match in results
        ],
        warnings=warnings or None,
    )


def _normalize_query_text(query: Any) -> str:
    if not isinstance(query, str):
        return ""
    return " ".join(query.split())


def _normalize_limit(k: Any) -> tuple[int, list[str]]:
    if isinstance(k, bool) or not isinstance(k, int):
        raise ValueError("k must be an integer.")
    if k < 1:
        raise ValueError("k must be greater than zero.")
    if k > MAX_RESULT_LIMIT:
        return MAX_RESULT_LIMIT, [f"k capped at {MAX_RESULT_LIMIT}."]
    return k, []


def _maybe_get_catalog_index(catalog_root: str | None) -> RetrievalIndex | None:
    try:
        return get_catalog_index(catalog_root)
    except (GraphifyAdapterError, RetrievalIndexError):
        return None


def _get_session_index(
    session: FlowgraphSession,
    *,
    catalog_root: str | None,
) -> RetrievalIndex:
    cache_key = (
        session.graph_id(),
        session.state_revision,
        str(session.path) if session.path is not None else None,
        catalog_root,
    )
    active_session_index = _BOUND_SESSION_INDEX.get()
    active_session_index_key = _BOUND_SESSION_INDEX_KEY.get()
    if (
        active_session_index is not None
        and active_session_index_key == cache_key
    ):
        return active_session_index

    catalog_index = _maybe_get_catalog_index(catalog_root)
    retrieval_index = build_session_index(session, catalog_index=catalog_index)
    _BOUND_SESSION_INDEX.set(retrieval_index)
    _BOUND_SESSION_INDEX_KEY.set(cache_key)
    return retrieval_index


def _search_index(
    retrieval_index: RetrievalIndex,
    *,
    normalized_query: str,
    searchable_query: str,
    query_tokens: list[str],
    query_terms: list[str],
    applied_limit: int,
) -> list[_SearchMatch]:
    candidate_ids = _candidate_node_ids(retrieval_index, query_terms)
    if not candidate_ids:
        return []

    matches: list[_SearchMatch] = []
    for node_id in candidate_ids:
        record = retrieval_index.node_records[node_id]
        prepared_record = retrieval_index.prepared_records.get(node_id)
        if prepared_record is None:
            continue
        match = _score_record(
            record,
            prepared_record,
            normalized_query=normalized_query,
            searchable_query=searchable_query,
            query_tokens=query_tokens,
            query_terms=query_terms,
        )
        if match is not None:
            matches.append(match)

    return sorted(
        matches,
        key=lambda item: (
            -item.score,
            item.node.label.lower(),
            item.node.node_id,
        ),
    )[:applied_limit]


def _candidate_node_ids(
    retrieval_index: RetrievalIndex, query_terms: list[str]
) -> set[str]:
    candidate_ids: set[str] = set()
    for term in query_terms:
        candidate_ids.update(retrieval_index.token_index.get(term, ()))
    return candidate_ids


def _score_record(
    record: IndexedNode,
    prepared_record: PreparedSearchNode,
    *,
    normalized_query: str,
    searchable_query: str,
    query_tokens: list[str],
    query_terms: list[str],
) -> _SearchMatch | None:
    field_hits: dict[str, list[str]] = {}
    exact_fields: list[str] = []
    matched_tokens: set[str] = set()
    score = NODE_TYPE_BONUS.get(record.node_type, 0.0)

    for field_name, normalized_field in prepared_record.normalized_fields.items():
        if not normalized_field:
            continue

        if searchable_query and searchable_query in normalized_field:
            score += PHRASE_BONUSES.get(field_name, 0.0)
            exact_fields.append(field_name)

        token_hits = [
            token
            for token in query_terms
            if token in prepared_record.field_terms.get(field_name, frozenset())
        ]
        if token_hits:
            deduped_hits = list(dict.fromkeys(token_hits))
            field_hits[field_name] = deduped_hits
            matched_tokens.update(deduped_hits)
            score += len(deduped_hits) * FIELD_WEIGHTS.get(field_name, 0.0)

    if not field_hits and not exact_fields:
        return None

    score += (len(matched_tokens) / len(query_terms)) * 10.0
    reason = _build_reason(
        normalized_query=normalized_query,
        field_hits=field_hits,
        exact_fields=exact_fields,
    )
    return _SearchMatch(node=record, score=score, reason=reason)


def _build_reason(
    *,
    normalized_query: str,
    field_hits: dict[str, list[str]],
    exact_fields: list[str],
) -> str:
    parts: list[str] = []
    if exact_fields:
        parts.append(
            f"Exact phrase match for '{normalized_query}' in "
            f"{', '.join(_field_label(field) for field in exact_fields[:2])}"
        )

    for field_name in ("label", "identifier", "summary", "related"):
        hits = field_hits.get(field_name)
        if not hits:
            continue
        parts.append(
            f"{_field_label(field_name).capitalize()} matched {', '.join(hits[:3])}"
        )
        if len(parts) >= 2:
            break

    if not parts:
        return "Matched retrieval context."
    return "; ".join(parts[:2]) + "."


def _field_label(field_name: str) -> str:
    if field_name == "identifier":
        return "identifier"
    if field_name == "related":
        return "adjacent context"
    return field_name
