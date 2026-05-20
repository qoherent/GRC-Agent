"""Shared docs-answer evidence dataclasses."""

from __future__ import annotations

from dataclasses import dataclass

from grc_agent.runtime.docs_answer_advisor import DocsAnswerSnippet

_DOCS_TOPIC_SYNONYMS: dict[str, tuple[str, ...]] = {
    "pmt": ("polymorphic", "types", "message"),
    "pmts": ("polymorphic", "types", "message"),
    "stream": ("sample", "samples", "data"),
    "tags": ("metadata", "length", "tag"),
    "message": ("port", "ports", "pdu", "queue"),
    "flowgraph": ("top block", "graph", "blocks"),
    "decimation": ("sample rate", "downsample", "rate change", "sample_rate_change"),
    "interpolation": ("sample rate", "upsample", "rate change", "sample_rate_change"),
    "sample": ("rate", "sps", "decimation", "interpolation"),
    "ports": ("message", "stream", "queue"),
    "throttle": ("rate", "sample", "limit", "pace"),
    "grcc": ("compiler", "compile", "validation", "validate"),
    "hierarchical": ("hier", "wrapper", "block"),
    "tagged": ("length", "packet", "pdu"),
    "variable": ("variables", "parameter", "parameters"),
    "variables": ("variable", "parameter", "parameters"),
}


@dataclass(frozen=True)
class _DocsEvidenceCandidate:
    snippet: DocsAnswerSnippet
    source_channel: str
    source_type: str
    section: str
    lexical_score: float
    semantic_score: float | None
    topic_score: float
    quality_score: float
    low_value_reasons: tuple[str, ...]
    procedural: bool


@dataclass(frozen=True)
class _DocsComparisonSides:
    left_label: str
    right_label: str
    left_terms: tuple[str, ...]
    right_terms: tuple[str, ...]
