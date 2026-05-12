"""Internal read-only search_grc wrapper implementation."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from grc_agent.flowgraph_session import FlowgraphSession


SearchGrcFn = Callable[..., dict[str, Any]]


def search_result_preview(
    results: Any,
    *,
    max_items: int = 3,
    include_summary: bool = True,
) -> list[dict[str, str]]:
    if not isinstance(results, list):
        return []
    preview: list[dict[str, str]] = []
    for item in results[:max_items]:
        if not isinstance(item, dict):
            continue
        compact: dict[str, str] = {}
        keys = ["block_id", "node_id", "label"]
        if include_summary:
            keys.append("summary")
        for key in keys:
            value = item.get(key)
            if isinstance(value, str) and value:
                compact[key] = value
        if compact:
            preview.append(compact)
    return preview


def search_grc_internal(
    query: str,
    *,
    scope: str,
    k: int | None,
    session: FlowgraphSession | None,
    catalog_root: Path | None,
    search_fn: SearchGrcFn,
) -> dict[str, Any]:
    if k is None:
        payload = search_fn(
            query,
            scope=scope,
            session=session,
            catalog_root=catalog_root,
        )
    else:
        payload = search_fn(
            query,
            scope=scope,
            k=k,
            session=session,
            catalog_root=catalog_root,
        )
    if payload.get("ok") and payload.get("results"):
        payload["hint"] = (
            "Use `block_id` from block results with `describe_block`, including later follow-ups like `what does that block look like?` or requests for ports and parameters. "
            "Use `node_id` with `get_grc_context` only for loaded session blocks."
        )
    elif payload.get("ok") and scope == "session" and not payload.get("results"):
        if k is None:
            fallback = search_fn(
                query,
                scope="catalog",
                session=session,
                catalog_root=catalog_root,
            )
        else:
            fallback = search_fn(
                query,
                scope="catalog",
                k=k,
                session=session,
                catalog_root=catalog_root,
            )
        fallback_preview = search_result_preview(fallback.get("results"))
        if fallback.get("ok") and fallback_preview:
            payload["catalog_fallback_preview"] = fallback_preview
            first_block_id = next(
                (
                    item.get("block_id")
                    for item in fallback_preview
                    if isinstance(item.get("block_id"), str)
                ),
                None,
            )
            if isinstance(first_block_id, str) and first_block_id:
                payload["hint"] = (
                    "No matches in the session. Catalog fallback preview is included. "
                    f"If the user refers to the first result, call `describe_block(block_id=\"{first_block_id}\")`. "
                    'If the user still wants the search itself, rerun the same query with `scope="catalog"`.'
                )
            else:
                payload["hint"] = (
                    "No matches in the session. Catalog fallback preview is included. "
                    'Retry the same query with `scope="catalog"` before you answer or validate anything else, then use the returned `block_id`.'
                )
        else:
            payload["hint"] = (
                "No matches in the session. "
                'Do NOT call `describe_block` with the raw query text. Retry the same query with `scope="catalog"` '
                "before you answer or validate anything else, then use the returned `block_id`."
            )
    return payload
