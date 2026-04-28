"""Bounded lexical search over the cleaned GNU Radio manual corpus."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from grc_agent._payload import ErrorCode, build_error_payload
from grc_agent.retrieval.text import expand_terms, tokenize_text

from .clean import clean_manual_page
from .schema import ManualChunk, ManualPage

DEFAULT_MANUAL_ROOT = Path(__file__).resolve().parents[3] / "docs" / "wiki_gnuradio_org"
DEFAULT_MANUAL_LIMIT = 3
MAX_MANUAL_LIMIT = 8

_MUTATION_SHAPED_KEYS = frozenset(
    {"transaction", "params", "block_id", "insert_tool_args"}
)


def search_manual(
    query: str,
    k: int = DEFAULT_MANUAL_LIMIT,
    *,
    corpus_root: str | Path | None = None,
) -> dict[str, Any]:
    """Search bundled tutorial/manual pages for explanation-only context."""
    normalized_query = " ".join(str(query).split()) if isinstance(query, str) else ""
    if not normalized_query:
        return build_error_payload(
            error_type=ErrorCode.INVALID_REQUEST,
            message="Query must be a non-empty string.",
        )
    if isinstance(k, bool) or not isinstance(k, int) or k < 1:
        return build_error_payload(
            error_type=ErrorCode.INVALID_REQUEST,
            message="k must be an integer greater than zero.",
        )
    limit = min(k, MAX_MANUAL_LIMIT)
    root = Path(corpus_root) if corpus_root is not None else DEFAULT_MANUAL_ROOT
    try:
        pages = _load_manual_pages(str(root.resolve()))
    except OSError as exc:
        return build_error_payload(
            error_type=ErrorCode.FILE_LOAD_ERROR,
            message=str(exc),
        )

    query_terms = expand_terms(tokenize_text(normalized_query))
    if not query_terms:
        return build_error_payload(
            error_type=ErrorCode.INVALID_REQUEST,
            message="Query must contain at least one searchable letter or digit.",
        )
    ranked = _rank_chunks(pages, query_terms)[:limit]
    return {
        "ok": True,
        "tool": "search_manual",
        "query": normalized_query,
        "results": [_chunk_result(chunk, page, score) for score, page, chunk in ranked],
        "warnings": [] if ranked else [f"No manual matches found for '{normalized_query}'."],
    }


@lru_cache(maxsize=2)
def _load_manual_pages(root_text: str) -> tuple[ManualPage, ...]:
    root = Path(root_text)
    if not root.is_dir():
        raise FileNotFoundError(f"Manual corpus directory not found: {root}")
    return tuple(clean_manual_page(path) for path in sorted(root.glob("*.md")))


def _rank_chunks(
    pages: tuple[ManualPage, ...],
    query_terms: tuple[str, ...],
) -> list[tuple[float, ManualPage, ManualChunk]]:
    ranked: list[tuple[float, ManualPage, ManualChunk]] = []
    for page in pages:
        title_terms = set(expand_terms(tokenize_text(page.title)))
        for chunk in page.chunks:
            text_terms = tokenize_text(chunk.text)
            term_set = set(expand_terms(text_terms))
            hits = [term for term in query_terms if term in term_set]
            if not hits:
                continue
            score = float(len(hits) * 10)
            score += sum(text_terms.count(term) for term in hits)
            score += sum(8 for term in query_terms if term in title_terms)
            score += max(0, 5 - chunk.ordinal) * 0.1
            ranked.append((score, page, chunk))
    return sorted(
        ranked,
        key=lambda item: (-item[0], item[1].title.lower(), item[2].ordinal),
    )


def _chunk_result(chunk: ManualChunk, page: ManualPage, score: float) -> dict[str, Any]:
    result = {
        "title": page.title,
        "section": " > ".join(chunk.heading_path) if chunk.heading_path else page.title,
        "excerpt": _bounded_excerpt(chunk.text),
        "score": round(score, 3),
        "citation": {
            "path": page.source_path,
            "line_start": chunk.line_start,
            "line_end": chunk.line_end,
            "url": page.source_url,
            "oldid": page.oldid,
            "last_edited": page.last_edited,
            "license": page.license,
        },
    }
    for key in _MUTATION_SHAPED_KEYS:
        result.pop(key, None)
    return result


def _bounded_excerpt(text: str, limit: int = 700) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "…"
