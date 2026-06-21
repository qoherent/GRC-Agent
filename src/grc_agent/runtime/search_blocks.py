"""search_blocks wrapper — vector search over the GNU Radio catalog.

Replaces the FTS5 lexical backend. Uses the same vec1 + embeddinggemma
pipeline as docs (see :mod:`grc_agent.runtime.doc_answer`).
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from grc_agent._payload import ErrorCode
from grc_agent.catalog.loaders import CatalogError, describe_block, get_catalog_snapshot
from grc_agent.runtime.block_semantics import evaluated_param_hides
from grc_agent.runtime.tool_context import is_meaningful
from grc_agent.runtime.catalog_vector import (
    CATALOG_DB_PATH,
    VectorCatalogStore,
    embed_query,
    is_catalog_db_usable,
)

# GRC-native param-category constants (see docs/GNU_NATIVE_METHODS.md)
try:
    from gnuradio.grc.core.Constants import ADVANCED_PARAM_TAB, DEFAULT_PARAM_TAB
except ImportError:
    ADVANCED_PARAM_TAB = "Advanced"
    DEFAULT_PARAM_TAB = "General"

# Categories that contain only non-essential params:
#   ADVANCED_PARAM_TAB — GRC auto-added metadata (alias, affinity, comment, buffers)
#   "Config"           — 100% styling (colors, alphas, markers, styles; verified across 564 blocks)
_EXCLUDED_PARAM_CATEGORIES: frozenset[str] = frozenset({ADVANCED_PARAM_TAB, "Config"})

if TYPE_CHECKING:
    from grc_agent.agent import GrcAgent, ToolResult


logger = logging.getLogger(__name__)


def search_blocks(
    agent: "GrcAgent",
    query: str,
    k: int | None = None,
    debug: bool = False,
) -> "ToolResult":
    """Vector search over the GNU Radio catalog.

    Returns the same payload shape the model already sees: a list of
    ``{block_id, name, summary, distance, match_type, why}`` rows.
    """

    started = time.monotonic()
    before_revision = agent.session.state_revision
    before_dirty = agent.session.is_dirty
    handlers: list[str] = []
    q = " ".join(str(query).split()) if isinstance(query, str) else ""
    if not q:
        return _tool_error(
            agent,
            started,
            "query must be non-empty.",
            handlers,
            before_revision,
            before_dirty,
        )

    limit_value = (
        agent._retrieval_cfg.search_blocks_default_k
        if k is None
        else int(k)
    )
    limit = max(1, min(limit_value, agent._retrieval_cfg.search_blocks_max_k))

    handlers.append("catalog_vector_search")
    if not is_catalog_db_usable(CATALOG_DB_PATH):
        # Try to (re-)ingest on the fly so the model isn't stuck behind a missing index.
        try:
            snapshot = get_catalog_snapshot(agent.catalog_root)
            blocks_payload = []
            for bid, b in snapshot.blocks.items():
                raw_params = b.payload.get("parameters") or []
                param_values = {
                    str(p.get("id")): "" if p.get("default") is None else str(p.get("default"))
                    for p in raw_params if p.get("id")
                }
                blocks_payload.append({
                    "block_id": bid,
                    "label": _string_value(b.payload.get("label")) or bid,
                    "categories": list(getattr(b, "category_paths", ())),
                    "parameters": [p.get("id") for p in raw_params if p.get("id")],
                    "param_values": param_values,
                    "ports": (
                        [p.get("id") for p in (b.payload.get("inputs") or []) if p.get("id")] +
                        [p.get("id") for p in (b.payload.get("outputs") or []) if p.get("id")]
                    ),
                    "documentation": _string_value(b.payload.get("documentation")) or "",
                })
            store = VectorCatalogStore(CATALOG_DB_PATH, agent._llama_server_url)
            store.ingest_if_needed(
                blocks=blocks_payload, server_url=agent._llama_server_url
            )
        except Exception as exc:
            logger.warning("Catalog vector ingest failed: %s", exc)

    if not is_catalog_db_usable(CATALOG_DB_PATH):
        return _tool_error(
            agent,
            started,
            "Catalog vector index not ready. Build with `make catalog-warmup` or restart the agent.",
            handlers,
            before_revision,
            before_dirty,
            error_type=ErrorCode.RETRIEVAL_NOT_READY,
            degraded=True,
        )

    try:
        query_vec = embed_query(agent._llama_server_url, q)
    except Exception as exc:
        return _tool_error(
            agent,
            started,
            f"Embedding backend unreachable: {exc}",
            handlers,
            before_revision,
            before_dirty,
            error_type=ErrorCode.RETRIEVAL_NOT_READY,
            degraded=True,
        )

    try:
        store = VectorCatalogStore(CATALOG_DB_PATH, agent._llama_server_url)
        neighbours = store.search(query_vec, limit)
    except Exception as exc:
        return _tool_error(
            agent,
            started,
            f"Catalog vector search failed: {exc}",
            handlers,
            before_revision,
            before_dirty,
            error_type=ErrorCode.RETRIEVAL_NOT_READY,
            degraded=True,
        )

    # Map every neighbour back to a real catalog block. The vector store is the
    # authoritative source; some neighbour rows may be stale or no longer exist.
    try:
        snapshot = get_catalog_snapshot(agent.catalog_root)
    except CatalogError as exc:
        return _tool_error(
            agent,
            started,
            f"Catalog unavailable: {exc}",
            handlers,
            before_revision,
            before_dirty,
            error_type=ErrorCode.RETRIEVAL_NOT_READY,
            degraded=True,
        )

    rows: list[dict[str, Any]] = []
    for neighbour in neighbours:
        bid = neighbour.get("block_id", "")
        block = snapshot.blocks.get(bid)
        if block is None:
            continue
        param_ids = [p.get("id") for p in (block.payload.get("parameters") or []) if p.get("id")][:3]
        rows.append({
                "id": bid,
                "params": ", ".join(param_ids) if param_ids else "",
            })

    limited = rows[:limit]
    # The vector store returns limit+1 neighbours. If we got limit+1 back,
    # there are more matches beyond what we showed.
    output_truncated = len(neighbours) > limit

    payload: dict[str, Any] = {
        "ok": True,
        "query": q,
        "results": limited,
        "output_truncated": output_truncated,
    }

    result = agent._payload_result(
        "search_blocks", payload, include_active_session=False
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


def _tool_error(
    agent: "GrcAgent",
    started: float,
    message: str,
    handlers: list[str],
    before_revision: int,
    before_dirty: bool,
    *,
    error_type: str = ErrorCode.INVALID_REQUEST,
    degraded: bool = False,
) -> "ToolResult":
    payload = {
        "ok": False,
        "query": "",
        "results": [],
        "degraded_retrieval": degraded,
        "retrieval_mode": "vector",
        "output_truncated": False,
        "message": message,
        "error_type": error_type,
    }
    result = agent._payload_result(
        "search_blocks", payload, include_active_session=False
    )
    return agent._attach_wrapper_dispatch_telemetry(
        debug=False,
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


def _string_value(value: Any) -> str | None:
    if isinstance(value, str):
        text = " ".join(value.split())
        return text or None
    return None


def _vector_why(neighbour: dict[str, Any], label: str) -> str:
    distance = float(neighbour.get("distance", 1.0))
    # Cosine distance in our calibrated range: 0.29..0.65. Same bands as docs.
    if distance < 0.35:
        band = "strong semantic match"
    elif distance < 0.50:
        band = "moderate semantic match"
    else:
        band = "weak semantic match"
    return f"{band} for {label} (cosine distance {distance:.3f})"


def _catalog_summary(
    *,
    documentation: str,
    params: list[str],
    inputs: list[str],
    outputs: list[str],
    categories: list[str],
    templates_make: str | None = None,
) -> str:
    """Compose a fallback summary when documentation is absent.

    Params/inputs/outputs are expected to be already filtered by the
    caller (essential params only, via native GRC methods). No [:N]
    caps here — the caller's filter is the authority on what's relevant.
    """
    if documentation:
        return " ".join(documentation.split())
    parts: list[str] = []
    if inputs:
        parts.append("inputs: " + ", ".join(inputs))
    if outputs:
        parts.append("outputs: " + ", ".join(outputs))
    if params:
        parts.append("params: " + "; ".join(params))
    if categories:
        parts.append("category: " + categories[0])
    if templates_make:
        usage = _string_value(templates_make)
        if usage:
            parts.append("Usage: " + usage)
    return "; ".join(parts)


def _param_categories(block_id: str) -> dict[str, str]:
    """Read GRC's native ``category`` attribute for each param.

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
        block = flow_graph.new_block(block_id)
        if block is None:
            return {}
        return {
            str(name): str(getattr(param, "category", DEFAULT_PARAM_TAB))
            for name, param in block.params.items()
        }
    except Exception:
        return {}


def _compact_catalog_details(
    block_id: str,
    param_values: dict[str, Any] | None = None,
    raw_params: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the per-result ``catalog`` payload using GRC's own evaluation.

    Applies three native GRC filters (see ``docs/GNU_NATIVE_METHODS.md``):

    1. **``hide != 'all'``** — GRC's evaluated param visibility. Drops
       params GRC itself hides at runtime (per-channel device knobs
       beyond active channels, conditional GUI grids, etc.).
    2. **``category != ADVANCED_PARAM_TAB``** — drops GRC's auto-added
       metadata (alias, affinity, comment, minoutbuf, maxoutbuf). These
       are the same for every block; they're not block-specific.
    3. **``category != 'Config'``** — drops 100%-styling params (colors,
       alphas, markers, line styles). Verified across all 564 catalog
       blocks: Config contains zero functional params.

    Remaining params are sorted by GRC prominence (``hide='none'`` first,
    then ``hide='part'``) and returned with ``id``, ``label``, ``dtype``,
    ``default`` only — no ``options``/``option_labels`` (discovery context;
    ``inspect_graph`` provides options when the model is editing a specific
    block).

    Falls back to raw ``describe_block`` output (no filtering) if the GRC
    platform is unavailable.
    """
    details = describe_block(block_id)
    if details.get("ok") is not True:
        return {}

    if raw_params is None:
        raw_params = details.get("parameters", [])
    if param_values is None:
        param_values = {}

    hides = evaluated_param_hides(block_id, param_values)

    # No GRC evaluation — return raw details, no filtering
    if not hides:
        return _raw_catalog_details(raw_params, details)

    # Get param categories from the live GRC block
    param_cats = _param_categories(block_id)

    # Filter: visible (hide != 'all') AND not Advanced AND not Config
    visible_params = [
        p for p in raw_params
        if isinstance(p, dict)
        and hides.get(str(p.get("id", "")), "all") != "all"
        and param_cats.get(str(p.get("id", "")), DEFAULT_PARAM_TAB) not in _EXCLUDED_PARAM_CATEGORIES
    ]

    # Sort by GRC prominence: hide='none' first, then 'part'
    def prominence_key(p: dict[str, Any]) -> tuple[int, str]:
        pid = str(p.get("id", ""))
        hid = hides.get(pid, "all")
        rank = 0 if hid == "none" else 1 if hid == "part" else 2
        return (rank, pid)

    visible_params.sort(key=prominence_key)

    params = [_format_param(p) for p in visible_params]
    return _build_details_payload(params, details)


def _raw_catalog_details(
    raw_params: list[dict[str, Any]],
    details: dict[str, Any],
) -> dict[str, Any]:
    """Return catalog details when GRC ``hide`` evaluation is unavailable.

    No filtering, no sorting — raw ``describe_block`` output, only the
    empty-field cleanup. Used as a fallback, never the primary path.
    """
    params = [_format_param(p) for p in raw_params if isinstance(p, dict)]
    return _build_details_payload(params, details)


def _format_param(raw_param: dict[str, Any]) -> dict[str, Any]:
    """One param dict for discovery: id/label/dtype/default only.

    No ``options``/``option_labels`` — those are for the editing context
    (``inspect_graph`` on a specific block), not for browsing catalog
    results. The ``dtype`` field (e.g. ``"enum"``) is enough signal that
    the param has a fixed set of values.
    """
    return {
        key: raw_param.get(key)
        for key in ("id", "label", "dtype", "default")
        if is_meaningful(raw_param.get(key))
    }


def _build_details_payload(
    params: list[dict[str, Any]],
    details: dict[str, Any],
) -> dict[str, Any]:
    """Assemble the final {params, inputs, outputs} dict, dropping empties."""
    payload: dict[str, Any] = {}
    if params:
        payload["params"] = params
    for direction in ("inputs", "outputs"):
        raw_ports = details.get(direction) or []
        compact_ports: list[dict[str, Any]] = []
        for raw_port in raw_ports:
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
            payload[direction] = compact_ports
    return payload
