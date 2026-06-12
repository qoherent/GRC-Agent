"""Bounded docs answer synthesis over local GNU Radio retrieval snippets."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


class DocsAnswerAdvisorError(RuntimeError):
    """Raised when docs answer synthesis fails validation or transport."""


@dataclass(frozen=True)
class DocsAnswerSnippet:
    """One bounded snippet used for docs answer synthesis."""

    title: str
    source: str
    excerpt: str


def run_docs_answer_advisor(
    *,
    client: Any,
    model: str,
    question: str,
    answer_type: str,
    snippets: list[DocsAnswerSnippet],
    focus: str | None = None,
    max_answer_chars: int = 420,
    max_excerpt_chars: int = 260,
    max_sources: int = 3,
) -> dict[str, Any]:
    """Synthesize a concise grounded answer from bounded snippets (RAG mode, no nested LLM)."""
    selected_snippets = snippets[:max_sources]
    lines = []
    for index, snippet in enumerate(selected_snippets):
        lines.append(f"### Source [{index}]: {snippet.title} ({snippet.source})\n{snippet.excerpt}\n")
    answer = "\n".join(lines)

    return {
        "answer": answer,
        "source_indexes": list(range(len(selected_snippets))),
        "insufficient_evidence": len(selected_snippets) == 0,
        "advisor_latency_ms": 0,
        "prompt_chars": 0,
        "snippet_count": len(snippets),
        "schema_valid": True,
        "timeout_ms": 0,
        "helper_finish_reason": "pure_rag_retrieval",
    }


def run_docs_answer_advisor_diagnostic(
    *,
    client: Any,
    model: str,
    question: str,
    answer_type: str,
    snippets: list[DocsAnswerSnippet],
    focus: str | None = None,
    max_answer_chars: int = 420,
    max_excerpt_chars: int = 260,
    max_sources: int = 3,
    response_mode: str = "json_object",
) -> dict[str, Any]:
    """Run a single diagnostic helper attempt with phase telemetry and raw output (RAG mode, no nested LLM)."""
    selected_snippets = snippets[:max_sources]
    lines = []
    for index, snippet in enumerate(selected_snippets):
        lines.append(f"### Source [{index}]: {snippet.title} ({snippet.source})\n{snippet.excerpt}\n")
    answer = "\n".join(lines)

    return {
        "ok": True,
        "response_mode": response_mode,
        "question": question,
        "answer_type": answer_type,
        "prompt_chars": 0,
        "snippet_count": len(snippets),
        "timeout_ms": 0,
        "result": {
            "answer": answer,
            "source_indexes": list(range(len(selected_snippets))),
            "insufficient_evidence": len(selected_snippets) == 0,
            "helper_finish_reason": "pure_rag_retrieval",
        },
        "raw_response_text": json.dumps({
            "answer": answer,
            "source_indexes": list(range(len(selected_snippets))),
            "insufficient_evidence": len(selected_snippets) == 0,
        }),
        "raw_model_output": "",
        "finish_reason": "stop",
        "error_kind": "",
        "error_message": "",
        "response_parse_error": "",
        "payload_parse_error": "",
        "validation_error": "",
        "phase_ms": {
            "prompt_build": 0,
            "http_request": 0,
            "generation": 0,
            "parsing": 0,
            "validation": 0,
            "total": 0,
        },
        "messages": [],
    }


__all__ = [
    "DocsAnswerAdvisorError",
    "DocsAnswerSnippet",
    "run_docs_answer_advisor_diagnostic",
    "run_docs_answer_advisor",
]
