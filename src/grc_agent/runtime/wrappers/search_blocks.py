"""search_blocks wrapper implementation extracted from GrcAgent."""

from __future__ import annotations

import re
import sqlite3
from collections import OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from grc_agent._payload import ErrorCode
from grc_agent.catalog import describe_block
from grc_agent.catalog.errors import CatalogError
from grc_agent.catalog.loaders import get_catalog_snapshot
from grc_agent.runtime.output_policy import is_meaningful

# ---------------------------------------------------------------------------
# Block-specific FTS synonym injection (Fix: search tuning, not prompt tuning)
# Maps block_id -> extra terms appended to that block's FTS body so that
# natural-language queries surface the correct block without prompt coaching.
# ---------------------------------------------------------------------------
_BLOCK_SEARCH_SYNONYMS: dict[str, tuple[str, ...]] = {
    # 'variable' block: model queries 'constant value block', 'named value', etc.
    "variable": ("constant", "value", "named value", "static", "scalar",
                  "flowgraph variable", "define variable", "set variable"),
    "analog_sig_source_x": ("complex source", "signal source", "sine wave",
                             "cosine wave", "waveform generator"),
}

if TYPE_CHECKING:
    from grc_agent.agent import GrcAgent, ToolResult


_CATALOG_SEARCH_INDEX_CACHE_MAX = 4
_CATALOG_DETAIL_LIMIT = 3
_CATALOG_SEARCH_INDEX_CACHE: OrderedDict[
    tuple[str, int, int], _CatalogSearchIndex
] = OrderedDict()


@dataclass(frozen=True)
class _CatalogSearchEntry:
    block_id: str
    item: dict[str, Any]
    field_norms: set[str]
    field_tokens: set[str]
    doc_tokens: set[str]
    label: str
    params: list[str]
    ports: list[str]


@dataclass
class _CatalogSearchIndex:
    entries: list[_CatalogSearchEntry]
    all_items: dict[str, dict[str, Any]]
    fts_conn: sqlite3.Connection | None
    fts_error: str | None

    def close(self) -> None:
        if self.fts_conn is None:
            return
        self.fts_conn.close()
        self.fts_conn = None


def search_blocks(
    agent: GrcAgent,
    query: str,
    k: int | None = None,
    debug: bool = False,
    enrich: bool = False,
) -> ToolResult:
    import time

    import grc_agent.agent as agent_module

    started = time.monotonic()
    before_revision = agent.session.state_revision
    before_dirty = agent.session.is_dirty
    handlers: list[str] = []
    q = " ".join(str(query).split()) if isinstance(query, str) else ""
    if not q:
        result = agent._tool_result(
            "search_blocks",
            ok=False,
            message="query must be non-empty.",
            error_type=ErrorCode.INVALID_REQUEST,
            include_active_session=False,
        )
        return agent._attach_wrapper_dispatch_telemetry(
            debug=debug,
            wrapper_name="search_blocks",
            wrapper_action="query",
            internal_handlers=["none"],
            started=started,
            before_revision=before_revision,
            before_dirty=before_dirty,
            result=result,
            validation_run=False,
            output_truncated=False,
        )
    limit_value = (
        agent._retrieval_cfg.search_blocks_default_k
        if k is None
        else int(k)
    )
    limit = max(1, min(limit_value, agent._retrieval_cfg.search_blocks_max_k))
    cacheable = not debug and not enrich
    retrieval_mode = "lexical"
    degraded_retrieval = False

    cache_key: tuple[str, int, str] | None = None
    if cacheable:
        cache_key = agent._search_blocks_cache_key(query=q, k=limit)
        cached_payload = agent._search_blocks_cache_get(cache_key)
        if cached_payload is not None:
            handlers.append("search_blocks_cache(hit)")
            result = agent._payload_result(
                "search_blocks",
                {
                    "ok": True,
                    "query": q,
                    "results": cached_payload["results"],
                    "degraded_retrieval": bool(cached_payload["degraded_retrieval"]),
                    "retrieval_mode": str(cached_payload["retrieval_mode"]),
                    "output_truncated": bool(cached_payload.get("output_truncated", False)),
                    "message": "Block candidates returned.",
                },
                include_active_session=False,
            )
            return agent._attach_wrapper_dispatch_telemetry(
                debug=debug,
                wrapper_name="search_blocks",
                wrapper_action="query",
                internal_handlers=handlers,
                started=started,
                before_revision=before_revision,
                before_dirty=before_dirty,
                result=result,
                validation_run=False,
                output_truncated=bool(cached_payload.get("output_truncated", False)),
            )

    if cache_key is not None:
        handlers.append("search_blocks_cache(miss)")
    handlers.append("catalog_lexical_search")
    candidates, lexical_error = _lexical_catalog_candidates(
        agent=agent,
        query=q,
        limit=max(limit * 4, limit),
    )
    if lexical_error is not None:
        degraded_retrieval = True
        handlers.append("catalog_lexical_search(degraded)")

    if not candidates and lexical_error is not None:
        result = agent._payload_result(
            "search_blocks",
            {
                "ok": False,
                "query": q,
                "results": [],
                "degraded_retrieval": bool(degraded_retrieval),
                "retrieval_mode": retrieval_mode,
                "output_truncated": False,
                "message": f"Catalog lexical search failed: {lexical_error}",
                "error_type": ErrorCode.RETRIEVAL_NOT_READY,
            },
            include_active_session=False,
        )
        return agent._attach_wrapper_dispatch_telemetry(
            debug=debug,
            wrapper_name="search_blocks",
            wrapper_action="query",
            internal_handlers=handlers,
            started=started,
            before_revision=before_revision,
            before_dirty=before_dirty,
            result=result,
            validation_run=False,
            output_truncated=False,
        )

    if not candidates:
        result = agent._payload_result(
            "search_blocks",
            {
                "ok": True,
                "query": q,
                "results": [],
                "degraded_retrieval": bool(degraded_retrieval),
                "retrieval_mode": retrieval_mode,
                "output_truncated": False,
                "message": "No block candidates matched the query.",
            },
            include_active_session=False,
        )
        return agent._attach_wrapper_dispatch_telemetry(
            debug=debug,
            wrapper_name="search_blocks",
            wrapper_action="query",
            internal_handlers=handlers,
            started=started,
            before_revision=before_revision,
            before_dirty=before_dirty,
            result=result,
            validation_run=False,
            output_truncated=False,
        )

    query_raw = q.casefold()
    query_norm = agent_module._normalize_alias_key(q)

    def identity_rank(item: dict[str, Any]) -> int:
        block_id = str(item.get("block_id", ""))
        name = str(item.get("name", ""))
        if query_raw in {block_id.casefold(), f"catalog:block:{block_id}".casefold()}:
            return 0
        if query_norm and query_norm == agent_module._normalize_alias_key(block_id):
            return 0
        if query_raw == name.casefold():
            return 1
        if query_norm and query_norm == agent_module._normalize_alias_key(name):
            return 1
        return 2

    candidates.sort(key=identity_rank)

    if enrich:
        handlers.append("describe_block(enrichment)")
        for item in candidates:
            if item.get("summary"):
                continue
            details = describe_block(str(item.get("block_id", "")))
            if details.get("ok"):
                summary = details.get("summary")
                if isinstance(summary, str) and summary:
                    item["summary"] = agent_module._compact_block_summary(summary)

    limited = candidates[:limit]
    output_truncated = len(candidates) > len(limited)
    text_lines: list[str] = []

    # ── Tier 1 / Tier 2 detection ──────────────────────────────────────
    has_exact_hit = any(
        str(item.get("match_type", "")) in {"exact_block_id", "exact_label"}
        for item in limited
    )
    # Promote to Tier-2 when the query explicitly names a known block_id.
    # e.g. query='blocks_null_sink parameters' contains 'blocks_null_sink'.
    if not has_exact_hit:
        query_tokens = set(re.findall(r"[a-z0-9_]+", q.casefold()))
        has_exact_hit = any(
            str(item.get("block_id", "")).casefold() in query_tokens
            or str(item.get("block_id", "")).casefold() in q.casefold()
            for item in limited
        )

    if not debug and not enrich and not has_exact_hit:
        # Tier 1 — concept search: ultra-compact, no JSON bloat.
        # Return only block_id + name so the model's attention
        # heads land directly on the IDs it needs to copy.
        compact = []
        for idx, item in enumerate(limited, 1):
            bid = str(item.get("block_id", ""))
            name = str(item.get("name", ""))
            text_lines.append(f"{idx}. ID: {bid} | Name: {name}")
            compact.append({"block_id": bid, "name": name})
        limited = compact

    elif not debug:
        # Tier 2 — exact match or debug: full catalog details.
        for idx, item in enumerate(limited):
            block_id = str(item.get("block_id", ""))
            if idx < _CATALOG_DETAIL_LIMIT:
                details = _compact_catalog_details(block_id)
                if details:
                    item["catalog"] = details
        limited = [
            {
                "block_id": str(item.get("block_id", "")),
                "catalog_label": str(item.get("name", "")),
                "name": str(item.get("name", "")),
                "summary": str(item.get("summary", "")),
                "match_type": str(item.get("match_type", "")),
                "why": str(item.get("why", "")),
                **(
                    {"catalog": item["catalog"]}
                    if isinstance(item.get("catalog"), dict)
                    else {}
                ),
            }
            for item in limited
        ]
        # Build text summary for Tier 2 as well
        for idx, item in enumerate(limited, 1):
            bid = str(item.get("block_id", ""))
            name = str(item.get("name", ""))
            text_lines.append(f"{idx}. ID: {bid} | Name: {name}")
    if cache_key is not None and cacheable:
        agent._search_blocks_cache_put(
            cache_key,
            {
                "results": limited,
                "degraded_retrieval": degraded_retrieval,
                "retrieval_mode": retrieval_mode,
                "output_truncated": output_truncated,
            },
        )
    result = agent._payload_result(
        "search_blocks",
        {
            "ok": True,
            "query": q,
            "results": limited,
            **(
                {"results_text": "\n".join(text_lines)}
                if text_lines
                else {}
            ),
            "degraded_retrieval": degraded_retrieval,
            "retrieval_mode": retrieval_mode,
            "output_truncated": output_truncated,
            "message": "Block candidates returned.",
        },
        include_active_session=False,
    )
    return agent._attach_wrapper_dispatch_telemetry(
        debug=debug,
        wrapper_name="search_blocks",
        wrapper_action="query",
        internal_handlers=handlers,
        started=started,
        before_revision=before_revision,
        before_dirty=before_dirty,
        result=result,
        validation_run=False,
        output_truncated=output_truncated,
    )


def _lexical_catalog_candidates(
    *,
    agent: GrcAgent,
    query: str,
    limit: int,
) -> tuple[list[dict[str, Any]], str | None]:
    query_norm = _normalize_for_search(query)
    query_terms = _search_terms(query)
    if not query_norm and not query_terms:
        return [], None
    try:
        snapshot = get_catalog_snapshot(agent.catalog_root)
    except CatalogError as exc:
        return [], str(exc)

    index = _catalog_search_index(agent=agent, snapshot=snapshot)
    scored_by_id: dict[str, tuple[float, str, dict[str, Any]]] = {}
    for entry in index.entries:
        score, match_type = _lexical_score(
            query=query,
            query_norm=query_norm,
            query_terms=query_terms,
            block_id=entry.block_id,
            label=entry.label,
            params=entry.params,
            ports=entry.ports,
            field_norms=entry.field_norms,
            field_tokens=entry.field_tokens,
            doc_tokens=entry.doc_tokens,
        )
        if score > 0:
            item = dict(entry.item)
            item["match_type"] = match_type
            evidence = _catalog_match_evidence(query_terms, entry)
            if evidence:
                item["why"] = "matched catalog metadata: " + ", ".join(evidence[:6])
            scored_by_id[entry.block_id] = (float(score), match_type, item)

    fts_ranked, fts_error = _fts5_catalog_rank(
        index=index,
        query_terms=query_terms,
        limit=limit * 2,
    )
    if fts_error is None:
        fts_boost_max = 60.0
        n_ranked = max(1, len(fts_ranked))
        for rank, block_id in enumerate(fts_ranked, start=1):
            item = index.all_items.get(block_id)
            if item is None:
                continue
            fts_boost = fts_boost_max * (1.0 - (rank - 1) / n_ranked)
            existing = scored_by_id.get(block_id)
            if existing is not None:
                boosted_score = existing[0] + fts_boost
                boosted_item = dict(existing[2])
                boosted_item["match_type"] = "fts5" if fts_boost > existing[0] else existing[1]
                scored_by_id[block_id] = (boosted_score, existing[1], boosted_item)
            else:
                fts_item = dict(item)
                fts_item["match_type"] = "fts5"
                fts_item["source"] = "catalog_fts5"
                scored_by_id[block_id] = (fts_boost, "fts5", fts_item)

    scored = [
        (score, block_id, item)
        for block_id, (score, _, item) in scored_by_id.items()
    ]
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [item for _, _, item in scored[:limit]], fts_error


def _catalog_search_index(*, agent: GrcAgent, snapshot: Any) -> _CatalogSearchIndex:
    """Return cached structured and FTS views for one catalog snapshot."""
    blocks = getattr(snapshot, "blocks", {})
    cache_key = (str(agent.catalog_root), id(snapshot), len(blocks))
    cached = _CATALOG_SEARCH_INDEX_CACHE.get(cache_key)
    if cached is not None:
        _CATALOG_SEARCH_INDEX_CACHE.move_to_end(cache_key)
        return cached

    index = _build_catalog_search_index(snapshot=snapshot)
    _CATALOG_SEARCH_INDEX_CACHE[cache_key] = index
    _CATALOG_SEARCH_INDEX_CACHE.move_to_end(cache_key)
    while len(_CATALOG_SEARCH_INDEX_CACHE) > _CATALOG_SEARCH_INDEX_CACHE_MAX:
        _, evicted = _CATALOG_SEARCH_INDEX_CACHE.popitem(last=False)
        evicted.close()
    return index


def _build_catalog_search_index(*, snapshot: Any) -> _CatalogSearchIndex:
    import grc_agent.agent as agent_module

    entries: list[_CatalogSearchEntry] = []
    all_items: dict[str, dict[str, Any]] = {}
    fts_records: list[tuple[str, str]] = []
    for raw_block in getattr(snapshot, "blocks", {}).values():
        payload = raw_block.payload
        label = _string_value(payload.get("label")) or raw_block.block_id
        params = _id_label_values(payload.get("parameters"))
        raw_param_ids = []
        for p in (payload.get("parameters") or []):
            if isinstance(p, dict) and p.get("id"):
                raw_param_ids.append(str(p.get("id")))
        inputs = _port_values(payload.get("inputs"))
        outputs = _port_values(payload.get("outputs"))
        categories = [
            " ".join(part for part in category_path if part)
            for category_path in getattr(raw_block, "category_paths", ())
        ]
        documentation = _string_value(payload.get("documentation")) or ""
        fields = [raw_block.block_id, label, *params, *inputs, *outputs, *categories]
        synonyms = _BLOCK_SEARCH_SYNONYMS.get(raw_block.block_id, ())
        fts_text = " ".join([*fields, documentation, *synonyms])
        fts_records.append((raw_block.block_id, fts_text))
        field_norms = {_normalize_for_search(field) for field in fields if field}
        field_tokens: set[str] = set()
        for field in fields:
            field_tokens.update(_search_terms(field))
        for synonym in synonyms:
            field_tokens.update(_search_terms(synonym))
        item = {
            "block_id": raw_block.block_id,
            "name": label,
            "summary": agent_module._compact_block_summary(
                _catalog_summary(
                    documentation=documentation,
                    params=params,
                    inputs=inputs,
                    outputs=outputs,
                    categories=categories,
                    templates_make=(
                        _string_value(
                            (payload.get("templates") or {}).get("make"))
                    ),
                )
            ),
            "match_type": "catalog",
            "source": "catalog",
        }
        all_items[raw_block.block_id] = item
        entries.append(
            _CatalogSearchEntry(
                block_id=raw_block.block_id,
                item=item,
                field_norms=field_norms,
                field_tokens=field_tokens,
                doc_tokens=_search_terms(documentation[:1200]) | _search_terms(" ".join(synonyms)),
                label=label,
                params=params + raw_param_ids,
                ports=[*inputs, *outputs],
            )
        )

    fts_conn, fts_error = _build_fts5_connection(fts_records)
    return _CatalogSearchIndex(
        entries=entries,
        all_items=all_items,
        fts_conn=fts_conn,
        fts_error=fts_error,
    )


def _is_exact_catalog_query(query: str, *, block_id: str, name: str) -> bool:
    import grc_agent.agent as agent_module

    query_raw = query.casefold()
    query_norm = agent_module._normalize_alias_key(query)
    return (
        bool(block_id)
        and query_raw in {block_id.casefold(), f"catalog:block:{block_id}".casefold()}
        or bool(query_norm)
        and query_norm
        in {
            agent_module._normalize_alias_key(block_id),
            agent_module._normalize_alias_key(name),
        }
    )


def _compact_catalog_details(block_id: str) -> dict[str, Any]:
    details = describe_block(block_id)
    if details.get("ok") is not True:
        return {}
    params = []
    for raw_param in details.get("parameters", [])[:10]:
        if not isinstance(raw_param, dict):
            continue
        param = {
            key: raw_param.get(key)
            for key in ("id", "label", "dtype", "default")
            if is_meaningful(raw_param.get(key))
        }
        options = raw_param.get("options")
        if isinstance(options, list) and options:
            param["options"] = options[:8]
        labels = raw_param.get("option_labels")
        if isinstance(labels, list) and labels:
            param["option_labels"] = labels[:8]
        params.append(param)
    ports = {}
    for direction in ("inputs", "outputs"):
        compact_ports = []
        for raw_port in details.get(direction, [])[:8]:
            if not isinstance(raw_port, dict):
                continue
            compact_ports.append(
                {
                    key: raw_port.get(key)
                    for key in ("id", "domain", "dtype", "optional")
                    if is_meaningful(raw_port.get(key))
                }
            )
        if compact_ports:
            ports[direction] = compact_ports
    return {
        key: value
        for key, value in {"params": params, **ports}.items()
        if is_meaningful(value)
    }


def _lexical_score(
    *,
    query: str,
    query_norm: str,
    query_terms: set[str],
    block_id: str,
    label: str,
    params: list[str],
    ports: list[str],
    field_norms: set[str],
    field_tokens: set[str],
    doc_tokens: set[str],
) -> tuple[int, str]:
    del query
    block_norm = _normalize_for_search(block_id)
    label_norm = _normalize_for_search(label)
    if query_norm in {block_norm, f"catalog block {block_norm}"}:
        return 1000, "exact_block_id"
    if query_norm == label_norm:
        return 900, "exact_label"
    param_norms = {_normalize_for_search(param) for param in params}
    if query_norm and query_norm in param_norms:
        return 760, "param"
    port_norms = {_normalize_for_search(port) for port in ports}
    if query_norm and query_norm in port_norms:
        return 720, "port"
    if query_norm and query_norm in field_norms:
        return 850, "exact_metadata"

    score = 0
    match_type = "lexical"
    if query_norm and (query_norm in block_norm or query_norm in label_norm):
        score += 500
        match_type = "name"
    if query_terms:
        metadata_hits = len(query_terms & field_tokens)
        doc_hits = len(query_terms & doc_tokens)
        if metadata_hits:
            score += 80 * metadata_hits
            match_type = "metadata"
        if doc_hits:
            score += 15 * doc_hits
        if query_terms <= field_tokens:
            score += 360
            match_type = "metadata"
    return score, match_type


def _catalog_match_evidence(
    query_terms: set[str],
    entry: _CatalogSearchEntry,
) -> list[str]:
    if not query_terms:
        return []
    values = [entry.label, *entry.params, *entry.ports]
    evidence: list[str] = []
    seen: set[str] = set()
    for value in values:
        value_terms = _search_terms(value)
        if not value_terms or not _search_terms_overlap(query_terms, value_terms):
            continue
        text = " ".join(str(value).split())
        key = text.casefold()
        if text and key not in seen:
            evidence.append(text)
            seen.add(key)
    return evidence


def _search_terms_overlap(left: set[str], right: set[str]) -> bool:
    if left & right:
        return True
    for left_term in left:
        if len(left_term) < 3:
            continue
        for right_term in right:
            if right_term.startswith(left_term):
                return True
    return False


def _fts5_catalog_rank(
    *,
    index: _CatalogSearchIndex,
    query_terms: set[str],
    limit: int,
) -> tuple[list[str], str | None]:
    terms = [
        term
        for term in sorted(query_terms)
        if len(term) > 1 and not term.isdigit()
    ][:8]
    if not terms:
        return [], None
    if index.fts_conn is None:
        return [], index.fts_error
    escaped_terms: list[str] = []
    for term in terms:
        escaped = term.replace('"', '""')
        escaped_terms.append(f'"{escaped}"')
    match_query = " OR ".join(escaped_terms)
    try:
        rows = index.fts_conn.execute(
            "SELECT block_id FROM catalog WHERE catalog MATCH ? ORDER BY bm25(catalog) LIMIT ?",
            (match_query, max(1, limit)),
        ).fetchall()
    except sqlite3.Error as exc:
        return [], str(exc)
    ranked: list[str] = []
    seen: set[str] = set()
    for row in rows:
        block_id = str(row[0]) if row else ""
        if not block_id or block_id in seen:
            continue
        seen.add(block_id)
        ranked.append(block_id)
    return ranked, None


def _build_fts5_connection(
    records: list[tuple[str, str]],
) -> tuple[sqlite3.Connection | None, str | None]:
    if not records:
        return None, None
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(":memory:", check_same_thread=False)
        conn.execute("CREATE VIRTUAL TABLE catalog USING fts5(block_id UNINDEXED, body)")
        conn.executemany(
            "INSERT INTO catalog(block_id, body) VALUES (?, ?)",
            records,
        )
    except sqlite3.Error as exc:
        if conn is not None:
            conn.close()
        return None, str(exc)
    return conn, None


def _catalog_summary(
    *,
    documentation: str,
    params: list[str],
    inputs: list[str],
    outputs: list[str],
    categories: list[str],
    templates_make: str | None = None,
) -> str:
    if documentation:
        return " ".join(documentation.split())
    parts: list[str] = []
    if inputs:
        parts.append("inputs: " + ", ".join(inputs[:4]))
    if outputs:
        parts.append("outputs: " + ", ".join(outputs[:4]))
    if params:
        parts.append("params: " + "; ".join(params[:4]))
    if categories:
        parts.append("category: " + categories[0])
    if templates_make:
        usage = _string_value(templates_make)
        if usage:
            # Strip Mako template syntax ${...} → flat names so the model
            # does not leak raw compiler syntax into JSON payloads.
            parts.append("Usage: " + re.sub(r"\$\{([^}]+)\}", r"\1", usage))
    return "; ".join(parts)


def _id_label_values(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    values: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        param_id = _string_value(item.get("id"))
        label = _string_value(item.get("label"))
        dtype = _string_value(item.get("dtype"))
        default_val = _string_value(item.get("default"))

        desc = param_id or ""
        if label:
            desc += f" ({label})"
        if dtype:
            desc += f", {dtype}"

        options = _string_list_values(item.get("options"))
        option_labels = _string_list_values(item.get("option_labels"))
        if options:
            if option_labels and len(option_labels) == len(options):
                pairs = [f"{o} ({lbl})" for o, lbl in zip(options, option_labels)]
                desc += ": " + ", ".join(pairs)
            else:
                desc += ": " + "/".join(options)
        if default_val:
            desc += f", default: {default_val}"

        if desc:
            values.append(desc)
    return _dedup(values)


def _port_values(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    values: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        for key in ("id", "label", "domain", "dtype"):
            text = _string_value(item.get(key))
            if text:
                values.append(text)
    return _dedup(values)


def _string_value(value: Any) -> str | None:
    if isinstance(value, str):
        text = " ".join(value.split())
        return text or None
    return None


def _string_list_values(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    values: list[str] = []
    for item in value:
        text = _string_value(item)
        if text:
            values.append(text)
    return values


def _normalize_for_search(value: Any) -> str:
    text = str(value).casefold()
    return " ".join(_search_terms(text))


def _search_terms(value: Any) -> set[str]:
    return set(re.findall(r"[a-z0-9_]+", str(value).casefold()))


def _dedup(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result
