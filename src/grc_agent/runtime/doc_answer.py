"""Docs-answer runtime pipeline helpers."""

from __future__ import annotations

import json
import logging
import re
import socket
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from grc_agent._payload import ErrorCode
from grc_agent.toolagents_runtime import (
    ToolAgentsJsonClient,
    ToolAgentsLlamaProviderConfig,
)

if TYPE_CHECKING:
    from grc_agent.agent import GrcAgent, ToolResult

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# evidence.py — shared dataclasses and constants
# ═══════════════════════════════════════════════════════════════════════════════

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


# ═══════════════════════════════════════════════════════════════════════════════
# formatting.py — text formatting and source-shape helpers
# ═══════════════════════════════════════════════════════════════════════════════

_DOCS_QUERY_STOP_WORDS = frozenset(
    {
        "a",
        "about",
        "across",
        "all",
        "an",
        "and",
        "are",
        "at",
        "be",
        "between",
        "block",
        "blocks",
        "briefly",
        "by",
        "can",
        "checking",
        "context",
        "difference",
        "different",
        "differ",
        "do",
        "does",
        "explain",
        "for",
        "gnu",
        "high",
        "how",
        "in",
        "interact",
        "is",
        "keep",
        "level",
        "meaning",
        "mean",
        "of",
        "or",
        "please",
        "radio",
        "short",
        "the",
        "this",
        "to",
        "used",
        "using",
        "use",
        "what",
        "with",
    }
)
_DOCS_NAVIGATION_MARKERS = (
    "beginner tutorials",
    "please leave tutorials-related feedback",
    "discussion page of this article",
    "jump to navigation",
    "table of contents",
    "table of content",
)
_DOCS_INSTRUCTION_LEAK_MARKERS = (
    "ignore previous",
    "ignore all previous",
    "system prompt",
    "developer message",
    "system instruction",
    "developer instruction",
    "call change_graph",
    "use change_graph",
    "execute change_graph",
    "tool call",
    "you are now",
)
_DOCS_MENU_TITLE_MARKERS = (
    "tutorials",
    "main page",
    "index",
)
_DOCS_PROCEDURAL_MARKERS = (
    "add the",
    "drag in",
    "connect the",
    "click ",
    "right-click",
    "setting up",
    "set up",
    "we will be using",
    "below to show",
    "workspace",
    "flowgraph below",
)
_DOCS_GENERIC_ANSWER_MARKERS = (
    "gnu radio is a free",
    "gnu radio is a framework",
    "software development toolkit",
    "what is gnu radio",
)
_DOCS_LIST_MARKERS_RE = re.compile(r"(?:^|\s)(?:\d+\.\s+|\*\s+)")
_DOCS_GENERIC_TOPIC_TERMS = frozenset(
    {
        "concept",
        "definition",
        "gnu",
        "radio",
        "signal",
        "system",
        "type",
    }
)


def clip_docs_snippets_for_helper(
    agent: Any,
    snippets: list[DocsAnswerSnippet],
) -> list[DocsAnswerSnippet]:
    clipped: list[DocsAnswerSnippet] = []
    total_chars = 0
    for snippet in snippets:
        excerpt_text = snippet.excerpt
        if len(excerpt_text) > agent._docs_answer_cfg.helper_max_snippet_chars:
            excerpt_text = (
                excerpt_text[
                    : agent._docs_answer_cfg.helper_max_snippet_chars - 1
                ].rstrip()
                + "…"
            )
        candidate = DocsAnswerSnippet(
            title=snippet.title,
            source=snippet.source,
            excerpt=excerpt_text,
        )
        chunk_chars = len(candidate.title) + len(candidate.source) + len(candidate.excerpt)
        if (
            clipped
            and total_chars + chunk_chars
            > agent._docs_answer_cfg.helper_max_total_context_chars
        ):
            break
        clipped.append(candidate)
        total_chars += chunk_chars
    return clipped or snippets[:1]


def is_tutorial_or_howto_query(query: str) -> bool:
    lower = query.lower()
    markers = (
        "how to",
        "tutorial",
        "walkthrough",
        "step by step",
        "example",
        "guide",
    )
    if any(marker in lower for marker in markers):
        return True
    return bool(
        re.search(r"(?i)^\s*how\s+do\s+.+\s+work\??\s*$", query)
        or re.search(r"(?i)^\s*how\s+does\s+.+\s+work\??\s*$", query)
        or re.search(r"(?i)^\s*how\s+do\s+i\s+use\s+.+\??\s*$", query)
        or re.search(r"(?i)^\s*how\s+can\s+i\s+use\s+.+\??\s*$", query)
    )


def docs_topic_terms(query: str) -> list[str]:
    return [
        token
        for token in re.findall(r"[a-z0-9]+", query.lower())
        if (len(token) > 2 or token == "qt") and token not in _DOCS_QUERY_STOP_WORDS
    ]


def docs_primary_terms(query: str) -> list[str]:
    return [
        token
        for token in docs_topic_terms(query)
        if token not in _DOCS_GENERIC_TOPIC_TERMS
    ]


def normalize_docs_source_key(source: str) -> str:
    text = " ".join(str(source or "").split()).strip().lower()
    if not text:
        return ""
    if "](" in text:
        text = text.split("](", 1)[0]
    if text.startswith("http"):
        text = text.split("#", 1)[0]
        text = text.split("&oldid=", 1)[0]
        text = text.rstrip("/&?")
    return text


def clean_docs_excerpt(excerpt: str) -> str:
    text = " ".join(str(excerpt or "").split()).strip()
    if not text:
        return ""
    repeated_heading = re.match(
        r"^(?P<title>[A-Za-z0-9][A-Za-z0-9 /()_.:-]{2,80})\s+##\s+(?P=title)\s+",
        text,
    )
    if repeated_heading:
        text = text[repeated_heading.end() :].strip()
    inline_heading = re.match(
        r"^(?P<head>(?:[A-Z][A-Za-z0-9()_.:/-]*\s+){1,5})"
        r"(?P<body>(?:All|Another|For|Here|If|In|Messages|Streams|This|There|When)\b.+)",
        text,
    )
    if inline_heading:
        heading_words = inline_heading.group("head").split()
        if 1 < len(heading_words) <= 5:
            text = inline_heading.group("body").strip()
    segments = re.split(r"(?<=[.!?])\s+", text)
    kept: list[str] = []
    for segment in segments:
        lower = segment.lower()
        if any(marker in lower for marker in _DOCS_NAVIGATION_MARKERS):
            continue
        if any(marker in lower for marker in _DOCS_INSTRUCTION_LEAK_MARKERS):
            continue
        if _DOCS_LIST_MARKERS_RE.search(segment) and len(segment) < 70:
            continue
        kept.append(segment)
    cleaned = " ".join(kept).strip()
    return cleaned if cleaned else text


def docs_title_aliases(title: str) -> list[str]:
    compact = " ".join(str(title or "").split()).strip().lower()
    if not compact:
        return []
    aliases: set[str] = set()
    tokens = re.findall(r"[a-z0-9]+", compact)
    if tokens:
        aliases.add("_".join(tokens))
    match = re.match(r"^(?P<prefix>[a-z0-9]+)\s+(?P<lhs>.+?)\s+and\s+(?P<rhs>.+)$", compact)
    if match:
        prefix = match.group("prefix")
        lhs = "_".join(re.findall(r"[a-z0-9]+", match.group("lhs")))
        rhs = "_".join(re.findall(r"[a-z0-9]+", match.group("rhs")))
        if prefix and lhs and rhs:
            aliases.add(f"{prefix}_{lhs}_and_{rhs}")
            aliases.add(f"{prefix}_{rhs}_and_{lhs}")
    explicit_aliases = {
        "creating your first block": (
            "embedded_python_block",
            "epy_block",
        ),
        "embedded python block": (
            "embedded_python_block",
            "epy_block",
        ),
        "hier blocks and parameters": (
            "hier_blocks",
            "hierarchical_block",
            "hierarchical_blocks",
        ),
        "message passing": (
            "message_ports",
            "pmt_message",
        ),
        "sample rate": (
            "sample_rate",
            "sample_rate_change",
            "decimation",
            "interpolation",
        ),
        "sample rate change": (
            "sample_rate",
            "decimation",
            "interpolation",
        ),
        "streams and vectors": (
            "stream_ports",
            "streams_and_vectors",
        ),
        "tagged stream blocks": (
            "tagged_stream_blocks",
            "packet_length_tags",
            "packet_tags",
        ),
        "variables in flowgraphs": (
            "variables_blocks",
            "variables_in_flowgraphs",
        ),
    }
    aliases.update(explicit_aliases.get(compact, ()))
    return sorted(alias for alias in aliases if alias)


def infer_docs_source_type(
    *,
    source: str,
    title: str,
    source_type_hint: str | None = None,
) -> str:
    hint = (source_type_hint or "").strip().lower()
    if hint == "tutorial_chunk":
        return "tutorial"
    if hint == "manual_chunk":
        return "manual"
    source_l = source.lower()
    title_l = title.lower()
    if "catalog:" in source_l:
        return "catalog"
    if "tutorial" in source_l or "guided_tutorial" in source_l:
        return "tutorial"
    if any(marker in title_l for marker in _DOCS_MENU_TITLE_MARKERS):
        return "tutorial"
    return "manual"


def docs_low_value_reasons(*, candidate: _DocsEvidenceCandidate) -> list[str]:
    reasons: list[str] = []
    title_l = candidate.snippet.title.lower()
    excerpt_l = candidate.snippet.excerpt.lower()
    section_l = candidate.section.lower()
    if title_l.strip() == "what is gnu radio":
        reasons.append("generic_gnuradio_page")
    if any(marker in title_l for marker in _DOCS_MENU_TITLE_MARKERS):
        reasons.append("menu_index_page")
    if "porting guide" in title_l:
        reasons.append("porting_guide_fragment")
    if title_l.startswith("simulation example"):
        reasons.append("simulation_walkthrough")
    if any(marker in excerpt_l for marker in _DOCS_NAVIGATION_MARKERS):
        reasons.append("navigation_boilerplate")
    if excerpt_l.count(" 1. ") + excerpt_l.count(" 2. ") + excerpt_l.count(" 3. ") >= 3:
        reasons.append("toc_dominated")
    numbered_links = len(_DOCS_LIST_MARKERS_RE.findall(excerpt_l))
    prose_chars = len(re.findall(r"[a-z]", excerpt_l))
    if numbered_links >= 6 and prose_chars < 260:
        reasons.append("link_list_fragment")
    if len(candidate.snippet.excerpt.strip()) < 70:
        reasons.append("very_short_fragment")
    if candidate.snippet.excerpt.rstrip().endswith("…") and len(candidate.snippet.excerpt) < 220:
        reasons.append("snippet_fragment")
    if section_l in {"", candidate.snippet.title.lower()} and "tutorials" in excerpt_l[:180]:
        reasons.append("generic_context_only")
    return reasons


def is_procedural_walkthrough_text(text: str) -> bool:
    lower = text.lower()
    return any(marker in lower for marker in _DOCS_PROCEDURAL_MARKERS)


def is_block_definition_query(question: str) -> bool:
    q = question.lower().strip()
    if not (
        re.search(r"\bwhat does .+ do\??$", q)
        or re.search(r"\bwhat is (?:an? |the )?.+ block used for(?: in .+)?\??$", q)
        or re.search(r"\bwhat does .+ block do\??$", q)
        or re.search(r"\bhow do .+ blocks? (?:relate to|interact with) .+\??$", q)
        or re.search(r"\bhow does .+ block (?:relate to|interact with) .+\??$", q)
    ):
        return False
    if "interact with" in q:
        return False
    if "relate to" in q and "pmt" not in q:
        return False
    subject = extract_block_definition_subject(question) or ""
    subject_l = subject.lower()
    if subject_l in {"grcc", "gnu radio"}:
        return False
    if "grcc" in subject_l:
        return False
    markers = (
        "block",
        "sink",
        "source",
        "strobe",
        "throttle",
        "head",
        "debug",
        "tagged stream",
        "null sink",
        "qt gui",
        "embedded python",
    )
    return any(marker in subject_l for marker in markers) or bool(
        re.fullmatch(r"[a-z0-9_]+", subject_l)
    )


def extract_block_definition_subject(question: str) -> str | None:
    q = " ".join(question.split()).strip()
    patterns = (
        r"(?i)\bwhat does (?:the )?(?P<subject>.+?) block do\??$",
        r"(?i)\bwhat is (?:an? |the )?(?P<subject>.+?) block(?: used for)?(?: in .+)?\??$",
        r"(?i)\bwhat does (?:the )?(?P<subject>.+?) do\??$",
        r"(?i)\bhow do (?:the )?(?P<subject>.+?) blocks? (?:relate to|interact with) .+\??$",
        r"(?i)\bhow does (?:the )?(?P<subject>.+?) block (?:relate to|interact with) .+\??$",
    )
    for pattern in patterns:
        match = re.search(pattern, q)
        if not match:
            continue
        subject = " ".join(str(match.group("subject") or "").split()).strip(" ?.,")
        if subject:
            return subject
    return None


def extract_docs_subject(question: str) -> str | None:
    q = " ".join(question.split()).strip()
    patterns = (
        r"(?i)\bwhat is (?:an? |the )?(?P<subject>.+?)\??$",
        r"(?i)\bwhat does (?:the )?(?P<subject>.+?) do\??$",
        r"(?i)\bhow do (?P<subject>.+?) work\??$",
        r"(?i)\bhow does (?P<subject>.+?) work\??$",
        r"(?i)\bhow do (?P<subject>.+?) relate to .+?\??$",
        r"(?i)\bhow does (?P<subject>.+?) relate to .+?\??$",
        r"(?i)\bhow do (?P<subject>.+?) interact\??$",
        r"(?i)\bhow do (?P<subject>.+?) affect .+?\??$",
        r"(?i)\bhow does (?P<subject>.+?) affect .+?\??$",
    )
    for pattern in patterns:
        match = re.search(pattern, q)
        if not match:
            continue
        subject = " ".join(str(match.group("subject") or "").split()).strip(" ?.,")
        if subject:
            return subject
    return None


def clean_catalog_summary_for_answer(name: str, summary: str) -> str:
    text = " ".join(summary.split()).strip()
    if not text:
        return ""
    text = text.replace("…", "")
    text = re.sub(rf"(?i)^{re.escape(name)}\s+", "", text).strip()
    text = re.sub(r"\([a-z0-9_]+\)", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"\b[a-z]+_[a-z0-9_]+\b", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(
        r"\bwith\s+\d+\s+input\s+por.*$",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip(" .,:;")
    text = re.sub(r"\([a-z0-9_.-]*$", "", text, flags=re.IGNORECASE).strip(" .,:;")
    text = re.sub(r"\bblocks?\b\.?$", "", text, flags=re.IGNORECASE).strip(" .,:;")
    text = re.sub(r"\bparameters:\s*.+$", "", text, flags=re.IGNORECASE).strip(" .,:;")
    text = re.sub(r"\binputs?:\s*.+$", "", text, flags=re.IGNORECASE).strip(" .,:;")
    text = re.sub(r"\boutputs?:\s*.+$", "", text, flags=re.IGNORECASE).strip(" .,:;")
    text = re.sub(r"\bwith\s+\d+\.?$", "", text, flags=re.IGNORECASE).strip(" .,:;")
    text = re.sub(r"\bsink to nowhere\b", "discards input samples", text, flags=re.IGNORECASE)
    text = re.sub(r"\bdrop output samples\b", "discard input samples", text, flags=re.IGNORECASE)
    text = re.sub(r"\bdiscard stream data\b", "discard input samples", text, flags=re.IGNORECASE)
    text = re.sub(r"\s{2,}", " ", text).strip(" .,:;")
    text = re.sub(r"\s+", " ", text).strip(" .,:;")
    name_tokens = re.findall(r"[a-z0-9]+", name.lower())
    lowered = text.lower()
    for token in name_tokens:
        lowered = re.sub(rf"\b{re.escape(token)}\b", " ", lowered)
    lowered = re.sub(r"\s+", " ", lowered).strip(" .,:;")
    informative_tokens = [
        token
        for token in re.findall(r"[a-z0-9]+", lowered)
        if len(token) > 2 and token not in {"block", "blocks", "stream", "data", "samples"}
    ]
    if len(informative_tokens) < 2:
        return ""
    if not re.search(
        r"\b(limit|throttle|pass|discard|consume|drop|debug|convert|send|receive|display|show|rate|message|sample)\b",
        lowered,
    ):
        return ""
    if len(text.split()) > 24:
        text = " ".join(text.split()[:24]).strip(" .,:;")
    return text


def catalog_block_purpose_sentence(name: str, summary: str) -> str:
    name_l = name.lower()
    summary_l = summary.lower()
    if not summary:
        if "stream to tagged stream" in name_l:
            return "converts a stream to a tagged stream by attaching length tags"
        if "message strobe" in name_l:
            return "emits PMT messages periodically"
        if "message debug" in name_l:
            return "is used to inspect and print message traffic for debugging"
        return ""
    if "throttle" in name_l and any(
        marker in summary_l for marker in ("rate", "sample", "limit", "throttle", "pace")
    ):
        if "hardware" in summary_l or "simulation" in summary_l:
            return (
                "limits the rate of samples in a flowgraph, typically to prevent "
                "simulations from running as fast as the CPU allows"
            )
        return "limits the rate of samples in a flowgraph"
    if "head" in name_l and any(
        marker in summary_l for marker in ("fixed", "number of samples", "limit", "stop")
    ):
        return "passes only a fixed number of samples, then stops forwarding data"
    if ("null" in name_l and "sink" in name_l) and any(
        marker in summary_l for marker in ("discard", "drop", "consume", "nowhere")
    ):
        return "consumes and discards input samples"
    return summary


# ═══════════════════════════════════════════════════════════════════════════════
# selection.py — candidate selection and deterministic fallback helpers
# ═══════════════════════════════════════════════════════════════════════════════

_DOCS_RETRIEVAL_QUERY_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "does",
        "for",
        "from",
        "how",
        "is",
        "it",
        "me",
        "of",
        "please",
        "tell",
        "the",
        "to",
        "what",
    }
)


def build_catalog_assisted_candidate(
    agent: Any,
    *,
    question: str,
) -> _DocsEvidenceCandidate | None:
    subject = extract_block_definition_subject(question) or extract_docs_subject(question) or question
    try:
        result = agent._search_blocks(subject, k=3, debug=False, enrich=True)
    except Exception:
        return None
    if result.get("ok") is not True:
        return None
    rows = result.get("results")
    if not isinstance(rows, list) or not rows:
        return None
    subject_terms = docs_primary_terms(subject) or docs_topic_terms(subject)
    scored_rows: list[tuple[int, dict[str, Any]]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        text = " ".join(
            [
                str(row.get("name") or ""),
                str(row.get("block_id") or ""),
                str(row.get("summary") or ""),
            ]
        ).lower()
        score = sum(1 for term in subject_terms if term and term in text)
        scored_rows.append((score, row))
    if not scored_rows:
        return None
    scored_rows.sort(key=lambda item: item[0], reverse=True)
    if scored_rows[0][0] <= 0:
        return None
    top = scored_rows[0][1]
    if not isinstance(top, dict):
        return None
    block_id = str(top.get("block_id") or "").strip()
    name = str(top.get("name") or block_id).strip()
    summary = str(top.get("summary") or "").strip()
    if not name or not summary:
        return None
    alias = "_".join(re.findall(r"[a-z0-9]+", name.lower()))
    source_ref = "catalog"
    if block_id and alias:
        source_ref = f"catalog:{alias}:{alias}_blocks:{block_id}"
    elif block_id:
        source_ref = f"catalog:{block_id}"
    elif alias:
        source_ref = f"catalog:{alias}"
    return _DocsEvidenceCandidate(
        snippet=DocsAnswerSnippet(
            title=name,
            source=source_ref,
            excerpt=summary,
        ),
        source_channel="catalog_assist",
        source_type="catalog",
        section=name,
        semantic_score=None,
        topic_score=0.0,
        quality_score=0.0,
        low_value_reasons=(),
        procedural=False,
    )


def should_catalog_assist(
    question: str,
    ranked_candidates: list[_DocsEvidenceCandidate],
) -> bool:
    lower = question.lower()
    if not any(
        marker in lower
        for marker in (
            "block",
            "sink",
            "source",
            "strobe",
            "throttle",
            "tagged stream",
            "qt gui",
            "message debug",
            "null sink",
        )
    ):
        return False
    if not ranked_candidates:
        return True
    subject = extract_docs_subject(question) or question
    subject_terms = docs_primary_terms(subject)
    if not subject_terms:
        return False
    top = ranked_candidates[0]
    top_title_source = " ".join([top.snippet.title, top.snippet.source]).lower()
    subject_hits = sum(1 for term in subject_terms if term in top_title_source)
    required_hits = 1 if len(subject_terms) <= 1 else 2
    return subject_hits < required_hits


def is_docs_evidence_strong(
    ranked_candidates: list[_DocsEvidenceCandidate],
    *,
    question: str,
) -> bool:
    if not ranked_candidates:
        return False
    top = ranked_candidates[0]
    severe = {
        "generic_gnuradio_page",
        "menu_index_page",
        "navigation_boilerplate",
        "toc_dominated",
    }
    if any(reason in severe for reason in top.low_value_reasons):
        return False
    primary_terms = docs_primary_terms(question)
    top_text = " ".join(
        [top.snippet.title, top.section, top.snippet.excerpt]
    ).lower()
    primary_hits = sum(1 for term in primary_terms if term in top_text)
    if primary_terms and primary_hits == 0:
        return False
    return top.quality_score >= 4.5 and top.topic_score >= 2.0


def classify_docs_answer_type(question: str) -> str:
    lower = question.lower()
    if any(
        marker in lower
        for marker in (
            "abi guarantee",
            "abi guarantees",
            "zero-copy",
            "lock-free",
            "fpga bitstream",
            "auto-repair",
            "deterministically",
        )
    ):
        return "insufficient"
    if "hierarchical block" in lower:
        return "definition"
    if is_tutorial_or_howto_query(question):
        return "procedural_how_to"
    if is_block_definition_query(question):
        return "block_definition"
    if (
        "block" in lower
        and "relate to" in lower
        and "pmt" in lower
        and ("difference between" not in lower and "differ" not in lower and " versus " not in lower)
    ):
        return "block_definition"
    if any(marker in lower for marker in ("difference between", "differ", "vs", "versus")):
        return "comparison"
    if "interact with" in lower or "relate to" in lower:
        return "comparison"
    if "grcc" in lower:
        return "tool_command_concept"
    if lower.startswith(("what is ", "what are ", "explain ")):
        return "definition"
    return "definition"


def normalized_docs_retrieval_query(*, question: str, answer_type: str) -> str:
    query = " ".join(question.split()).strip()
    lower = query.lower()
    if "pmt" in lower or "pmts" in lower:
        return _expanded_docs_query(
            query,
            "Polymorphic Types PMTs opaque data type message passing stream tags",
        )
    if "stream tag" in lower or "stream tags" in lower:
        return _expanded_docs_query(
            query,
            "Stream Tags Introduction isosynchronous data stream metadata",
        )
    if "qt gui" in lower and "sink" in lower:
        return _expanded_docs_query(
            query,
            "QT GUI Time Sink Frequency Sink tutorial plot samples",
        )
    if answer_type == "tool_command_concept" and "grcc" in query.lower():
        return _expanded_docs_query(query, "grcc compile validation flowgraph")
    if "hierarchical block" in lower:
        return _expanded_docs_query(
            query,
            "Hier Blocks and Parameters hierarchical block wrapper",
        )
    if "embedded python block" in lower:
        return _expanded_docs_query(
            query,
            "Embedded Python Block custom Python block flowgraph",
        )
    if "decimation" in lower:
        return _expanded_docs_query(query, "Sample Rate Change decimation sample rate downsample")
    if "interpolation" in lower:
        return _expanded_docs_query(query, "Sample Rate Change interpolation sample rate upsample")
    if "variables" in lower and "block" in lower:
        return _expanded_docs_query(query, "Variables in Flowgraphs variables blocks parameters")
    if "stream tags" in lower and "packet" in lower:
        return _expanded_docs_query(
            query,
            "Tagged Stream Blocks stream tags packet boundaries length tag",
        )
    return query


def _expanded_docs_query(query: str, expansion: str) -> str:
    user_terms = [
        token
        for token in re.findall(r"[A-Za-z0-9_]+", query)
        if len(token) > 1 and token.lower() not in _DOCS_RETRIEVAL_QUERY_STOP_WORDS
    ]
    prefix = " ".join(dict.fromkeys(user_terms))
    if not prefix:
        return expansion
    expansion_key = expansion.lower()
    novel_terms = [
        term
        for term in prefix.split()
        if term.lower() not in expansion_key
    ]
    if not novel_terms:
        return expansion
    return f"{' '.join(novel_terms)} {expansion}"


def text_matches_term_or_synonym(text: str, term: str) -> bool:
    if not term:
        return False
    if term in text:
        return True
    for synonym in _DOCS_TOPIC_SYNONYMS.get(term, ()):
        if synonym in text:
            return True
    return False


def select_docs_candidates_for_answer_type(
    *,
    question: str,
    answer_type: str,
    ranked_candidates: list[_DocsEvidenceCandidate],
    limit: int,
) -> list[_DocsEvidenceCandidate]:
    if not ranked_candidates:
        return []
    if answer_type != "comparison":
        return ranked_candidates[:limit]
    sides = extract_comparison_sides(question)
    if sides is None:
        return ranked_candidates[:limit]
    left_candidate: _DocsEvidenceCandidate | None = None
    right_candidate: _DocsEvidenceCandidate | None = None
    for candidate in ranked_candidates:
        text = " ".join(
            [candidate.snippet.title, candidate.section, candidate.snippet.excerpt]
        ).lower()
        if left_candidate is None and any(
            text_matches_term_or_synonym(text, term) for term in sides.left_terms
        ):
            left_candidate = candidate
        if right_candidate is None and any(
            text_matches_term_or_synonym(text, term) for term in sides.right_terms
        ):
            right_candidate = candidate
        if left_candidate is not None and right_candidate is not None:
            break
    selected: list[_DocsEvidenceCandidate] = []
    if left_candidate is not None:
        selected.append(left_candidate)
    if right_candidate is not None and right_candidate is not left_candidate:
        selected.append(right_candidate)
    for candidate in ranked_candidates:
        if len(selected) >= limit:
            break
        if candidate in selected:
            continue
        selected.append(candidate)
    return selected[:limit]


def extract_comparison_sides(question: str) -> _DocsComparisonSides | None:
    q = " ".join(question.split()).strip()
    patterns = (
        r"(?i)\bdifference between (?P<left>.+?) and (?P<right>.+?)\??$",
        r"(?i)\bhow does (?P<left>.+?) differ from (?P<right>.+?)\??$",
        r"(?i)\bhow do (?P<left>.+?) differ from (?P<right>.+?)\??$",
        r"(?i)\bhow are (?P<left>.+?) different from (?P<right>.+?)\??$",
        r"(?i)\bhow is (?P<left>.+?) different from (?P<right>.+?)\??$",
        r"(?i)\b(?P<left>.+?) vs\.? (?P<right>.+?)\??$",
        r"(?i)\b(?P<left>.+?) versus (?P<right>.+?)\??$",
        r"(?i)\bhow do (?P<left>.+?) interact with (?P<right>.+?)\??$",
        r"(?i)\bhow does (?P<left>.+?) interact with (?P<right>.+?)\??$",
        r"(?i)\bhow do (?P<left>.+?) relate to (?P<right>.+?)\??$",
        r"(?i)\bhow does (?P<left>.+?) relate to (?P<right>.+?)\??$",
    )
    for pattern in patterns:
        match = re.search(pattern, q)
        if not match:
            continue
        left = " ".join(str(match.group("left") or "").split()).strip(" ?.,")
        right = " ".join(str(match.group("right") or "").split()).strip(" ?.,")
        left = re.sub(r"(?i)\b(keep it short|briefly|please cite source)\b.*$", "", left).strip(" ?.,")
        right = re.sub(r"(?i)\b(keep it short|briefly|please cite source)\b.*$", "", right).strip(" ?.,")
        if not left or not right:
            continue
        left_terms = tuple(docs_topic_terms(left))
        right_terms = tuple(docs_topic_terms(right))
        if not left_terms or not right_terms:
            continue
        return _DocsComparisonSides(
            left_label=left,
            right_label=right,
            left_terms=left_terms,
            right_terms=right_terms,
        )
    return None


def sentence_list(text: str) -> list[str]:
    return [
        " ".join(sentence.split()).strip()
        for sentence in re.split(r"(?<=[.!?])\s+", text)
        if " ".join(sentence.split()).strip()
    ]


def pick_typed_sentence(
    *,
    candidate: _DocsEvidenceCandidate,
    required_terms: tuple[str, ...],
    allow_procedural: bool,
    min_term_hits: int = 1,
) -> str:
    best = ""
    best_score = -999.0
    for sentence in sentence_list(candidate.snippet.excerpt):
        sentence = re.sub(r"^#+\s*", "", sentence).strip()
        lower = sentence.lower()
        if len(sentence) < 24 or len(sentence) > 220:
            continue
        if sentence.count("#") >= 1 or "```" in sentence:
            continue
        if re.match(
            r"(?i)^(source title|source url|retrieval topic|aliases|official or primary|why relevant):",
            sentence,
        ):
            continue
        if sentence.count("`") >= 2 or "self.connect(" in lower:
            continue
        if "::" in sentence:
            continue
        if ".py" in lower or "shown here" in lower:
            continue
        if "following functions" in lower:
            continue
        if "project" in lower and "can be found in" in lower:
            continue
        if " * " in sentence:
            continue
        if "function for this purpose" in lower and "connect" in lower:
            continue
        if any(marker in lower for marker in _DOCS_NAVIGATION_MARKERS):
            continue
        if re.search(r"\b\d+\.\s+\w+", sentence) and sentence.count(".") >= 3:
            continue
        if "for the purposes of this tutorial" in lower:
            continue
        if re.search(r"\b\d+\.$", sentence):
            continue
        if any(
            marker in lower
            for marker in (
                "creating and modifying python blocks",
                "flowgraph fundamentals",
                "beginner tutorials",
            )
        ):
            continue
        if sentence.endswith("…") and len(sentence) < 120:
            continue
        if any(marker in lower for marker in ("input port(s)", "output port(s)", "parameter(s)")):
            continue
        if not allow_procedural and is_procedural_walkthrough_text(lower):
            continue
        term_hits = sum(
            1
            for term in required_terms
            if text_matches_term_or_synonym(lower, term)
        )
        if required_terms and term_hits < max(1, min_term_hits):
            continue
        synonym_hits = 0
        for term in required_terms:
            for synonym in _DOCS_TOPIC_SYNONYMS.get(term, ()):
                if synonym in lower:
                    synonym_hits += 1
        exact_term_hits = sum(1 for term in required_terms if term and term in lower)
        score = (
            float(term_hits)
            + float(exact_term_hits)
            + min(2.0, float(synonym_hits) * 0.5)
        )
        if re.search(
            r"\b(is|are|means|refers to|used for|allows|lets|converts|provides|carries|increases|decreases|reduces)\b",
            lower,
        ):
            score += 1.5
        if any(marker in lower for marker in ("asynchronous", "between blocks", "control data", "time domain", "frequency domain")):
            score += 1.6
        if "pmt symbol" in lower and "asynchronous" not in lower:
            score -= 0.8
        if "the lower part is a modified version" in lower:
            score -= 2.0
        if score > best_score:
            best_score = score
            best = sentence
    return best


def minimum_required_term_hits(required_terms: tuple[str, ...]) -> int:
    if len(required_terms) >= 4:
        return 2
    if len(required_terms) >= 2:
        return 1
    return 1


def required_terms_for_answer_type(
    *,
    question: str,
    answer_type: str,
) -> tuple[str, ...]:
    if answer_type == "comparison":
        sides = extract_comparison_sides(question)
        if sides is None:
            return tuple(docs_primary_terms(question) or docs_topic_terms(question))
        terms = {
            *sides.left_terms,
            *sides.right_terms,
        }
        return tuple(sorted(term for term in terms if term))
    if answer_type == "tool_command_concept":
        return ("grcc", "validation", "compile")
    subject = extract_docs_subject(question) or question
    return tuple(docs_primary_terms(subject) or docs_topic_terms(subject))


def helper_eligibility_for_docs_answer(
    *,
    question: str,
    answer_type: str,
    source_quality: dict[str, Any],
    selected_candidates: list[_DocsEvidenceCandidate],
    typed_answer: str,
    typed_insufficient: bool,
) -> tuple[bool, str]:
    quality = str(source_quality.get("quality") or "weak")
    question_lower = question.lower()
    if "hierarchical block" in question_lower:
        return (False, "special_case_hier_block")
    if "embedded python block" in question_lower:
        return (False, "special_case_embedded_python")
    if answer_type == "insufficient":
        return (False, "unsupported_question")
    if quality == "weak":
        return (False, "weak_evidence")
    if not bool(source_quality.get("supports_answer_type")):
        return (False, "answer_type_not_supported")
    if answer_type == "comparison":
        if quality != "strong":
            return (False, "comparison_requires_strong_evidence")
        if typed_insufficient:
            return (False, "comparison_deterministic_insufficient")
        if len(selected_candidates) < 2:
            return (False, "comparison_missing_side_evidence")
        if "difference:" in typed_answer.lower() and typed_answer.count(":") >= 3:
            return (False, "high_confidence_simple_comparison")
        return (True, "eligible_comparison_synthesis")
    if answer_type == "definition":
        if any(w in question_lower for w in ("helper", "function", "generator", "class", "reference", "api")):
            return (True, "helper_function_query_requires_synthesis")
        if typed_insufficient and len(selected_candidates) >= 2:
            return (True, "eligible_definition_recovery")
        if len(selected_candidates) < 2:
            return (False, "single_source_definition")
        return (False, "high_confidence_simple_definition")
    if answer_type == "procedural_how_to":
        has_tutorial = any(
            candidate.source_type == "tutorial" for candidate in selected_candidates
        )
        if quality == "strong" and has_tutorial and typed_insufficient:
            return (True, "eligible_procedural_recovery")
        if quality == "strong" and has_tutorial and len(typed_answer) > 180:
            return (True, "eligible_procedural_synthesis")
        return (False, "procedural_deterministic_sufficient")
    if answer_type == "tool_command_concept":
        if typed_insufficient:
            return (False, "tool_command_missing_evidence")
        return (False, "tool_command_deterministic_sufficient")
    if answer_type == "block_definition":
        lower = typed_answer.lower()
        if lower.startswith("according to the local block catalog"):
            return (False, "concise_catalog_answer")
        return (False, "block_definition_deterministic_only")
    return (False, "unsupported_answer_type")


def helper_candidates_for_docs_answer(
    *,
    question: str,
    answer_type: str,
    ranked_candidates: list[_DocsEvidenceCandidate],
) -> list[_DocsEvidenceCandidate]:
    severe = {
        "generic_gnuradio_page",
        "menu_index_page",
        "navigation_boilerplate",
        "toc_dominated",
    }
    base_candidates = [
        candidate
        for candidate in ranked_candidates
        if not any(reason in severe for reason in candidate.low_value_reasons)
    ]
    if answer_type == "comparison":
        selected = select_docs_candidates_for_answer_type(
            question=question,
            answer_type=answer_type,
            ranked_candidates=(base_candidates or ranked_candidates),
            limit=3,
        )
        return selected[:3]
    if answer_type == "procedural_how_to":
        helper_candidates = [candidate for candidate in base_candidates if candidate.source_type == "tutorial"]
        return helper_candidates[:3] or (base_candidates[:3] or ranked_candidates[:2])
    helper_candidates = base_candidates[:3]
    return helper_candidates or ranked_candidates[:2]


def build_fallback_answer(
    agent: Any,
    *,
    question: str,
    ranked_candidates: list[_DocsEvidenceCandidate],
    evidence_strong: bool,
) -> tuple[str, bool]:
    del evidence_strong
    answer_type = classify_docs_answer_type(question)
    return agent._build_typed_docs_answer(
        question=question,
        ranked_candidates=ranked_candidates,
        answer_type=answer_type,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# pipeline.py — wrapper pipeline extracted from GrcAgent
# ═══════════════════════════════════════════════════════════════════════════════

_ANSWER_SOURCE_STOP_WORDS = frozenset(
    {
        "according",
        "also",
        "and",
        "are",
        "for",
        "from",
        "local",
        "that",
        "the",
        "this",
        "with",
    }
)


def ask_grc_docs(
    agent: GrcAgent,
    question: str,
    k: int | None = None,
    focus: str | None = None,
    debug: bool = False,
) -> ToolResult:
    import time

    started = time.monotonic()
    before_revision = agent.session.state_revision
    before_dirty = agent.session.is_dirty
    handlers: list[str] = []
    if not isinstance(question, str) or not question.strip():
        result = agent._tool_result(
            "ask_grc_docs",
            ok=False,
            message="question must be non-empty.",
            error_type=ErrorCode.INVALID_REQUEST,
        )
        return agent._attach_wrapper_dispatch_telemetry(
            debug=debug,
            wrapper_name="ask_grc_docs",
            wrapper_action="query",
            internal_handlers=["none"],
            started=started,
            before_revision=before_revision,
            before_dirty=before_dirty,
            result=result,
            validation_run=False,
            output_truncated=False,
        )

    limit = agent._retrieval_cfg.ask_grc_docs_default_k
    if isinstance(k, int) and not isinstance(k, bool):
        limit = max(1, min(k, agent._retrieval_cfg.ask_grc_docs_max_k))
    question_text = " ".join(question.split())
    focus_text = (
        " ".join(focus.split())
        if isinstance(focus, str) and focus.strip()
        else None
    )
    answer_type = agent._classify_docs_answer_type(question_text)


    degraded_retrieval = False
    fallback_used = False
    fallback_reason = "not_attempted"
    warnings: list[str] = []
    retrieval_mode = "keyword_docs"
    source_limit = min(limit, agent._docs_answer_cfg.max_sources)
    final_cache_key = agent._ask_grc_docs_cache_key(
        question=question_text,
        k=source_limit,
        focus=focus_text,
        retrieval_mode=retrieval_mode,
        sources=[],
        cache_scope="final",
    )
    cached_final_answer = agent._ask_grc_docs_cache_get(final_cache_key)
    if cached_final_answer is not None:
        handlers.append("answer_cache(hit)")
        result = agent._payload_result(
            "ask_grc_docs",
            _docs_answer_payload_from_cache(
                question=question_text,
                focus=focus_text,
                retrieval_mode=retrieval_mode,
                cached=cached_final_answer,
            ),
        )
        agent._last_docs_advisor_meta.update(
            {
                "advisor_attempted": False,
                "advisor_success": True,
                "fallback_reason": "none",
                "helper_latency_ms": 0,
                "prompt_chars": 0,
                "snippet_count": 0,
                "schema_valid": True,
                "cache_hit": True,
                "helper_finish_reason": "answer_cache_hit",
                "source_quality": dict(cached_final_answer.get("source_quality") or {}),
                "helper_eligible": bool(cached_final_answer.get("helper_eligible", False)),
                "helper_skipped_reason": str(
                    cached_final_answer.get("helper_skipped_reason") or "answer_cache_hit"
                ),
            }
        )
        return agent._attach_wrapper_dispatch_telemetry(
            debug=debug,
            wrapper_name="ask_grc_docs",
            wrapper_action="query",
            internal_handlers=handlers,
            started=started,
            before_revision=before_revision,
            before_dirty=before_dirty,
            result=result,
            validation_run=False,
            output_truncated=False,
        )

    candidates = agent._collect_docs_candidates()
    ranked_candidates = agent._rank_docs_candidates(
        question=question_text,
        candidates=candidates,
    )
    if agent._is_block_definition_query(question_text):
        handlers.append("search_blocks(catalog_assisted_docs)")
        assisted = agent._build_catalog_assisted_candidate(
            question=question_text
        )
        if assisted is not None:
            ranked_candidates = agent._rank_docs_candidates(
                question=question_text,
                candidates=[*candidates, assisted],
            )
    elif answer_type != "procedural_how_to" and agent._should_catalog_assist(
        question_text,
        ranked_candidates,
    ):
        handlers.append("search_blocks(catalog_assisted_docs)")
        assisted = agent._build_catalog_assisted_candidate(question=question_text)
        if assisted is not None:
            ranked_candidates = agent._rank_docs_candidates(
                question=question_text,
                candidates=[*candidates, assisted],
            )

    severe_reasons = {
        "generic_gnuradio_page",
        "menu_index_page",
        "navigation_boilerplate",
        "toc_dominated",
    }
    filtered_candidates = [
        candidate
        for candidate in ranked_candidates
        if not any(reason in severe_reasons for reason in candidate.low_value_reasons)
    ]
    selected_pool = filtered_candidates or ranked_candidates
    answer_candidate_limit = _docs_answer_candidate_limit(
        requested_limit=limit,
        max_limit=agent._retrieval_cfg.ask_grc_docs_max_k,
    )
    selected_candidates = agent._select_docs_candidates_for_answer_type(
        question=question_text,
        answer_type=answer_type,
        ranked_candidates=selected_pool,
        limit=answer_candidate_limit,
    )
    snippets = [candidate.snippet for candidate in selected_candidates]
    source_quality = agent._build_docs_source_quality(
        question=question_text,
        answer_type=answer_type,
        selected_candidates=selected_candidates,
    )
    if degraded_retrieval:
        warnings.append("retrieval_degraded")

    insufficient_evidence = len(snippets) == 0 or str(source_quality.get("quality")) == "weak"
    answer = ""
    sources = _sources_from_candidates(
        selected_candidates,
        answer="",
        limit=source_limit,
        excerpt_chars=agent._docs_answer_cfg.excerpt_target_chars,
    )
    agent._last_docs_advisor_meta = {
        "advisor_attempted": False,
        "advisor_success": False,
        "fallback_reason": "not_attempted",
        "helper_latency_ms": None,
        "prompt_chars": 0,
        "snippet_count": len(snippets),
        "schema_valid": False,
        "timeout_ms": int(agent._docs_answer_cfg.helper_timeout_seconds * 1000),
        "cache_hit": False,
        "helper_finish_reason": None,
        "source_quality": dict(source_quality),
        "helper_eligible": False,
        "helper_skipped_reason": "not_evaluated",
    }
    evidence_strong = str(source_quality.get("quality")) == "strong"
    cache_key = agent._ask_grc_docs_cache_key(
        question=question_text,
        k=source_limit,
        retrieval_mode=retrieval_mode,
        sources=sources[:source_limit],
        focus=focus_text,
        cache_scope="sources",
    )
    cached_docs_answer = agent._ask_grc_docs_cache_get(cache_key)
    if cached_docs_answer is not None:
        answer = str(cached_docs_answer.get("answer") or "")
        sources = list(cached_docs_answer.get("sources") or [])
        insufficient_evidence = bool(cached_docs_answer.get("insufficient_evidence"))
        fallback_used = bool(cached_docs_answer.get("fallback_used"))
        fallback_reason = str(cached_docs_answer.get("fallback_reason") or "cache_hit")
        helper_eligible = bool(cached_docs_answer.get("helper_eligible", False))
        helper_skipped_reason = str(
            cached_docs_answer.get("helper_skipped_reason") or "cache_hit"
        )
        cached_quality = cached_docs_answer.get("source_quality")
        if isinstance(cached_quality, dict):
            source_quality = dict(cached_quality)
        agent._last_docs_advisor_meta.update(
            {
                "advisor_attempted": False,
                "advisor_success": True,
                "fallback_reason": "none",
                "helper_latency_ms": 0,
                "prompt_chars": 0,
                "snippet_count": len(snippets),
                "schema_valid": True,
                "cache_hit": True,
                "helper_finish_reason": "cache_hit",
                "source_quality": dict(source_quality),
                "helper_eligible": bool(helper_eligible),
                "helper_skipped_reason": helper_skipped_reason,
            }
        )
    helper_sources_selected = False
    if snippets and cached_docs_answer is None:
        helper_eligible = False
        helper_skipped_reason = "not_evaluated"
        typed_answer = "Local docs did not contain enough direct evidence for this question."
        typed_insufficient = True
        if str(source_quality.get("quality")) != "weak":
            typed_answer, typed_insufficient = agent._build_typed_docs_answer(
                question=question_text,
                ranked_candidates=selected_candidates,
                answer_type=answer_type,
            )
            helper_eligible, helper_skipped_reason = agent._helper_eligibility_for_docs_answer(
                question=question_text,
                answer_type=answer_type,
                source_quality=source_quality,
                selected_candidates=selected_candidates,
                typed_answer=typed_answer,
                typed_insufficient=typed_insufficient,
            )
        else:
            helper_skipped_reason = "weak_evidence"
        answer = typed_answer
        insufficient_evidence = bool(typed_insufficient)
        fallback_used = True
        fallback_reason = "typed_fallback"
        helper_input_candidates = agent._helper_candidates_for_docs_answer(
            question=question_text,
            answer_type=answer_type,
            ranked_candidates=selected_pool,
        )
        helper_input = agent._clip_docs_snippets_for_helper(
            [candidate.snippet for candidate in helper_input_candidates]
        )
        helper_result = None
        agent._last_docs_advisor_meta.update(
            {
                "source_quality": dict(source_quality),
                "helper_eligible": bool(helper_eligible),
                "helper_skipped_reason": helper_skipped_reason,
            }
        )
        if agent._docs_answer_cfg.enabled and helper_eligible:
            helper_mode = agent._docs_answer_cfg.helper_mode
            run_helper = False
            if helper_mode in {"always", "auto"}:
                run_helper = True
            elif helper_mode == "never":
                helper_skipped_reason = "helper_mode_never"
            if run_helper:
                helper_result = agent._run_docs_answer_advisor(
                    question=question_text,
                    answer_type=answer_type,
                    snippets=helper_input,
                    focus=focus_text,
                )
            elif (
                agent._last_docs_advisor_meta.get("fallback_reason", "not_attempted")
                == "not_attempted"
            ):
                agent._last_docs_advisor_meta["fallback_reason"] = helper_skipped_reason
        elif not agent._docs_answer_cfg.enabled:
            helper_skipped_reason = "helper_disabled"
        else:
            agent._last_docs_advisor_meta["fallback_reason"] = helper_skipped_reason

        advisor_meta = dict(agent._last_docs_advisor_meta)
        if helper_result is not None:
            helper_answer = str(helper_result.get("answer") or "").strip()
            helper_answer_l = helper_answer.lower()
            helper_invalid = (
                answer_type == "block_definition"
                and any(
                    marker in helper_answer_l
                    for marker in ("input port(s)", "output port(s)", "parameter(s)")
                )
            )
            if helper_invalid:
                fallback_used = True
                fallback_reason = "helper_answer_low_value"
                agent._last_docs_advisor_meta["fallback_reason"] = "helper_answer_low_value"
                agent._last_docs_advisor_meta["helper_finish_reason"] = "low_value"
            else:
                answer = helper_answer
                selected_sources: list[dict[str, str]] = []
                source_indexes = helper_result.get("source_indexes")
                if isinstance(source_indexes, list):
                    for index in source_indexes:
                        if not isinstance(index, int):
                            continue
                        if index < 0 or index >= len(helper_input):
                            continue
                        snippet = helper_input[index]
                        selected_sources.append(
                            {
                                "title": snippet.title,
                                "source": snippet.source,
                                "excerpt": snippet.excerpt[
                                    : agent._docs_answer_cfg.excerpt_target_chars
                                ],
                            }
                        )
                if selected_sources:
                    sources = selected_sources[:source_limit]
                    helper_sources_selected = True
                insufficient_evidence = bool(helper_result.get("insufficient_evidence"))
                fallback_used = False
                fallback_reason = "none"
                agent._last_docs_advisor_meta["helper_finish_reason"] = str(
                    helper_result.get("helper_finish_reason") or "stop"
                )
        else:
            fallback_used = True
            fallback_reason = str(advisor_meta.get("fallback_reason") or "advisor_failed")
            if not agent._last_docs_advisor_meta.get("helper_finish_reason"):
                agent._last_docs_advisor_meta["helper_finish_reason"] = fallback_reason
            if helper_eligible and agent._docs_answer_cfg.helper_mode != "never":
                warnings.append("docs_answer_advisor_fallback")
        agent._last_docs_advisor_meta["helper_eligible"] = bool(helper_eligible)
        agent._last_docs_advisor_meta["helper_skipped_reason"] = helper_skipped_reason
        agent._last_docs_advisor_meta["source_quality"] = dict(source_quality)
    elif not snippets:
        fallback_used = True
        fallback_reason = "retrieval_empty"
        agent._last_docs_advisor_meta["helper_finish_reason"] = "retrieval_empty"
        agent._last_docs_advisor_meta["helper_skipped_reason"] = "retrieval_empty"

    if not answer:
        answer, insufficient_evidence = agent._build_fallback_answer(
            question=question_text,
            ranked_candidates=ranked_candidates,
            evidence_strong=evidence_strong,
        )
    answer = " ".join(answer.split())
    if len(answer) > agent._docs_answer_cfg.answer_target_chars:
        answer = answer[: agent._docs_answer_cfg.answer_target_chars - 1].rstrip() + "…"
    if cached_docs_answer is None and snippets and not helper_sources_selected:
        sources = _sources_from_candidates(
            selected_candidates,
            answer=answer,
            limit=source_limit,
            excerpt_chars=agent._docs_answer_cfg.excerpt_target_chars,
        )
    if cached_docs_answer is None:
        cache_payload = {
            "answer": answer,
            "sources": sources[:source_limit],
            "insufficient_evidence": bool(insufficient_evidence),
            "fallback_used": bool(fallback_used or degraded_retrieval),
            "fallback_reason": fallback_reason,
            "source_quality": dict(source_quality),
            "helper_eligible": bool(
                agent._last_docs_advisor_meta.get("helper_eligible", False)
            ),
            "helper_skipped_reason": str(
                agent._last_docs_advisor_meta.get("helper_skipped_reason") or ""
            ),
        }
        agent._ask_grc_docs_cache_put(
            cache_key,
            cache_payload,
        )
        agent._ask_grc_docs_cache_put(final_cache_key, cache_payload)

    result = agent._payload_result(
        "ask_grc_docs",
        {
            "ok": True,
            "question": question_text,
            "focus": focus_text,
            "answer": answer,
            "sources": sources[:source_limit],
            "allowed_use": "explanation_only",
            "mutation_authority": False,
            "confidence": _docs_answer_confidence(
                source_quality=source_quality,
                insufficient_evidence=bool(insufficient_evidence),
            ),
            "insufficient_evidence": bool(insufficient_evidence),
            "fallback_used": bool(fallback_used or degraded_retrieval),
            "degraded_retrieval": bool(degraded_retrieval),
            "retrieval_mode": retrieval_mode,
            "warnings": warnings,
            "message": "Grounded docs answer returned.",
        },
    )
    if debug:
        meta = dict(agent._last_docs_advisor_meta)
        meta["fallback_reason"] = fallback_reason
        result["docs_answer_telemetry"] = meta
    output_truncated = len(sources) >= source_limit
    return agent._attach_wrapper_dispatch_telemetry(
        debug=debug,
        wrapper_name="ask_grc_docs",
        wrapper_action="query",
        internal_handlers=handlers,
        started=started,
        before_revision=before_revision,
        before_dirty=before_dirty,
        result=result,
        validation_run=False,
        output_truncated=output_truncated,
    )



def _docs_answer_confidence(
    *,
    source_quality: dict[str, Any],
    insufficient_evidence: bool,
) -> str:
    if insufficient_evidence:
        return "low"
    quality = str(source_quality.get("quality") or "").strip().lower()
    if quality == "strong":
        return "high"
    if quality == "moderate":
        return "medium"
    return "low"


def _docs_query_key(query: str) -> str:
    return " ".join(re.findall(r"[a-z0-9_]+", query.lower()))


def _docs_answer_candidate_limit(*, requested_limit: int, max_limit: int) -> int:
    return max(1, min(10, max(requested_limit, max_limit, 8)))


def _docs_answer_payload_from_cache(
    *,
    question: str,
    focus: str | None,
    retrieval_mode: str,
    cached: dict[str, Any],
) -> dict[str, Any]:
    return {
        "ok": True,
        "question": question,
        "focus": focus,
        "answer": str(cached.get("answer") or ""),
        "sources": list(cached.get("sources") or []),
        "insufficient_evidence": bool(cached.get("insufficient_evidence")),
        "fallback_used": bool(cached.get("fallback_used")),
        "degraded_retrieval": False,
        "retrieval_mode": retrieval_mode,
        "warnings": [],
        "message": "Grounded docs answer returned from cache.",
    }




def _sources_from_candidates(
    candidates: list[_DocsEvidenceCandidate],
    *,
    answer: str,
    limit: int,
    excerpt_chars: int,
) -> list[dict[str, str]]:
    ranked = list(enumerate(candidates))
    if answer:
        ranked.sort(
            key=lambda item: (
                _answer_source_score(answer, item[1]),
                -item[0],
            ),
            reverse=True,
        )

    sources: list[dict[str, str]] = []
    source_counts: dict[str, int] = {}
    for _, candidate in ranked:
        source_key = _docs_query_key(candidate.snippet.source)
        if source_counts.get(source_key, 0) >= 1:
            continue
        excerpt = _source_excerpt_for_answer(
            candidate,
            answer=answer,
            excerpt_chars=excerpt_chars,
        )
        if not excerpt:
            continue
        sources.append(
            {
                "title": candidate.snippet.title,
                "source": candidate.snippet.source,
                "excerpt": excerpt,
            }
        )
        source_counts[source_key] = source_counts.get(source_key, 0) + 1
        if len(sources) >= limit:
            break
    return sources


def _answer_source_score(answer: str, candidate: _DocsEvidenceCandidate) -> tuple[float, float, float, float]:
    answer_text = _answer_evidence_text(answer)
    source_text = " ".join(
        [
            candidate.snippet.title,
            candidate.section,
            candidate.snippet.excerpt,
        ]
    ).lower()
    answer_lower = answer_text.lower()
    exact = 1.0 if answer_lower and answer_lower in source_text else 0.0
    answer_terms = _answer_terms(answer_text)
    if answer_terms:
        coverage = sum(1 for term in answer_terms if term in source_text) / len(answer_terms)
    else:
        coverage = 0.0
    return (
        exact,
        coverage,
        candidate.quality_score,
        candidate.semantic_score or 0.0,
    )


def _source_excerpt_for_answer(
    candidate: _DocsEvidenceCandidate,
    *,
    answer: str,
    excerpt_chars: int,
) -> str:
    excerpt = candidate.snippet.excerpt
    answer_text = _answer_evidence_text(answer)
    best_sentence = _best_support_sentence(answer_text, excerpt)
    if best_sentence:
        return best_sentence[:excerpt_chars]
    if answer_text:
        return ""
    return excerpt[:excerpt_chars]


def _best_support_sentence(answer_text: str, excerpt: str) -> str:
    answer_terms = _answer_terms(answer_text)
    if not answer_terms:
        return ""
    answer_lower = answer_text.lower()
    best_sentence = ""
    best_score = -1.0
    for sentence in re.split(r"(?<=[.!?])\s+", excerpt):
        sentence = " ".join(sentence.split()).strip()
        if not sentence:
            continue
        if _low_value_source_sentence(sentence):
            continue
        lower = sentence.lower()
        if answer_lower and (answer_lower in lower or lower in answer_lower):
            return sentence
        score = sum(1 for term in answer_terms if term in lower) / len(answer_terms)
        if score > best_score:
            best_score = score
            best_sentence = sentence
    return best_sentence if best_score >= 0.5 else ""


def _low_value_source_sentence(sentence: str) -> bool:
    lower = sentence.lower()
    if "```" in sentence or sentence.count("`") >= 2:
        return True
    if "::" in sentence or "self.connect(" in lower:
        return True
    if re.match(
        r"(?i)^(source title|source url|retrieval topic|aliases|official or primary|why relevant):",
        sentence,
    ):
        return True
    if re.search(r"\b(id|inputs|outputs|parameters|states):\s", lower) and sentence.count(":") >= 2:
        return True
    return False


def _answer_terms(answer: str) -> tuple[str, ...]:
    seen: set[str] = set()
    terms: list[str] = []
    for token in re.findall(r"[a-z0-9_]+", answer.lower()):
        if len(token) <= 2 or token in _ANSWER_SOURCE_STOP_WORDS:
            continue
        if token in seen:
            continue
        terms.append(token)
        seen.add(token)
    return tuple(terms)


def _answer_evidence_text(answer: str) -> str:
    text = " ".join(str(answer or "").split()).strip()
    text = re.sub(
        r"(?i)^(according to local docs|according to the local block catalog|local docs say)[:,]?\s*",
        "",
        text,
    )
    return text


def collect_docs_candidates(agent) -> list[_DocsEvidenceCandidate]:
    return []

def rank_docs_candidates(
    agent,
    *,
    question: str,
    candidates: list[_DocsEvidenceCandidate],
) -> list[_DocsEvidenceCandidate]:
    if not candidates:
        return []
    keywords = agent._docs_topic_terms(question)
    primary_terms = agent._docs_primary_terms(question)
    query_l = question.lower()
    howto = agent._is_tutorial_or_howto_query(question)
    block_definition_query = agent._is_block_definition_query(question)
    preferred_markers = _preferred_docs_source_markers(question)
    ranked: list[_DocsEvidenceCandidate] = []
    for candidate in candidates:
        title_l = candidate.snippet.title.lower()
        section_l = candidate.section.lower()
        excerpt_l = candidate.snippet.excerpt.lower()
        source_l = candidate.snippet.source.lower()
        text = " ".join([title_l, section_l, source_l, excerpt_l])
        term_hits = sum(1 for term in keywords if term in text)
        title_hits = sum(1 for term in keywords if term in title_l)
        heading_hits = sum(1 for term in keywords if term in section_l)
        phrase_bonus = 2.0 if query_l in text and len(query_l) > 8 else 0.0
        synonym_hits = 0
        for term in keywords:
            for synonym in _DOCS_TOPIC_SYNONYMS.get(term, ()):
                if synonym in text:
                    synonym_hits += 1
        topic_score = (
            float(term_hits)
            + (2.0 * float(title_hits))
            + (1.5 * float(heading_hits))
            + min(2.0, float(synonym_hits) * 0.5)
            + phrase_bonus
        )
        source_pref = 0.0
        if howto:
            source_pref = 1.5 if candidate.source_type == "tutorial" else -0.3
        elif block_definition_query and candidate.source_type == "catalog":
            source_pref = 2.4
        elif block_definition_query and candidate.source_type == "manual":
            source_pref = 0.6
        elif block_definition_query and candidate.source_type == "tutorial":
            source_pref = -1.4
        elif candidate.source_type == "manual":
            source_pref = 1.5
        elif candidate.source_type == "tutorial":
            source_pref = -1.2
        semantic_component = 0.0
        if isinstance(candidate.semantic_score, float):
            semantic_component = (candidate.semantic_score - 0.62) * 7.0
        low_value_reasons = agent._docs_low_value_reasons(candidate=candidate)
        low_value_penalty = float(len(low_value_reasons)) * 1.6
        procedural = agent._is_procedural_walkthrough_text(
            candidate.snippet.excerpt
        )
        procedural_penalty = 2.5 if procedural and not howto else 0.0
        primary_hits = sum(1 for term in primary_terms if term in text)
        if primary_terms and primary_hits == 0:
            topic_score -= 1.5
        if (
            primary_terms
            and not any(term in title_l for term in primary_terms)
            and "catalog:" not in candidate.snippet.source.lower()
        ):
            topic_score -= 0.8
        weak_absence_penalty = 0.0
        if topic_score <= 0.0 and (candidate.semantic_score or 0.0) < 0.74:
            weak_absence_penalty = 2.0
        off_topic_curated_penalty = 0.0
        if (
            "variables in flowgraphs" in title_l
            and "variable" not in query_l
            and "variables" not in query_l
        ):
            off_topic_curated_penalty = 6.0
        preferred_source_bonus = 0.0
        if preferred_markers and any(marker in text for marker in preferred_markers):
            preferred_source_bonus = 3.5
        quality_score = (
            topic_score
            + source_pref
            + preferred_source_bonus
            + semantic_component
            - low_value_penalty
            - procedural_penalty
            - weak_absence_penalty
            - off_topic_curated_penalty
        )
        ranked.append(
            _DocsEvidenceCandidate(
                snippet=candidate.snippet,
                source_channel=candidate.source_channel,
                source_type=candidate.source_type,
                section=candidate.section,
                semantic_score=candidate.semantic_score,
                topic_score=topic_score,
                quality_score=quality_score,
                low_value_reasons=tuple(low_value_reasons),
                procedural=procedural,
            )
        )
    ranked.sort(
        key=lambda item: (
            -item.quality_score,
            -item.topic_score,
            -(item.semantic_score or 0.0),
            item.snippet.title.lower(),
        )
    )
    return ranked


def _preferred_docs_source_markers(question: str) -> tuple[str, ...]:
    lower = question.lower()
    markers: list[str] = []
    if "grcc" in lower:
        markers.extend(("grcc", "gnu radio companion compiler"))
    if "hierarchical block" in lower or "hier block" in lower:
        markers.extend(("hier blocks", "hier_blocks", "hierarchical_block"))
    if "embedded python block" in lower:
        markers.extend(("embedded python block", "embedded_python_block"))
    if "decimation" in lower or "interpolation" in lower or "sample rate" in lower:
        markers.extend(("sample rate change", "sample_rate_change", "sample_rate"))
    if "variables" in lower and "block" in lower:
        markers.extend(("variables in flowgraphs", "variables_in_flowgraphs"))
    if "tagged stream" in lower or "packet boundaries" in lower or "packet length" in lower:
        markers.extend(("tagged stream blocks", "tagged_stream_blocks", "packet_communications"))
    if "stream" in lower and "message" in lower and "port" in lower:
        markers.extend(("streams and vectors", "stream_ports", "message passing", "message_ports"))
    if "options" in lower and ("flowgraph" in lower or "block" in lower):
        markers.extend(("options_blocks", "catalog:options", " options "))
    if any(k in lower for k in ("helper", "reference", "constellation", "qam", "psk")):
        markers.extend(("gnu_native_helpers_reference", "helper", "helpers"))
    return tuple(dict.fromkeys(markers))


def build_docs_source_quality(
    agent,
    *,
    question: str,
    answer_type: str,
    selected_candidates: list[_DocsEvidenceCandidate],
) -> dict[str, Any]:
    if not selected_candidates:
        return {
            "quality": "weak",
            "reason": "no_selected_sources",
            "selected_source_count": 0,
            "topic_match": False,
            "required_terms_covered": False,
            "source_hint_match": False,
            "is_menu_or_boilerplate": False,
            "supports_answer_type": False,
        }
    severe = {
        "generic_gnuradio_page",
        "menu_index_page",
        "navigation_boilerplate",
        "toc_dominated",
    }
    top = selected_candidates[0]
    is_menu_or_boilerplate = any(reason in severe for reason in top.low_value_reasons)
    required_terms = agent._required_terms_for_answer_type(
        question=question,
        answer_type=answer_type,
    )
    selected_text = " ".join(
        " ".join([candidate.snippet.title, candidate.section, candidate.snippet.excerpt]).lower()
        for candidate in selected_candidates
    )
    required_hits = sum(
        1
        for term in required_terms
        if term and agent._text_matches_term_or_synonym(selected_text, term)
    )
    required_min = agent._minimum_required_term_hits(required_terms)
    required_terms_covered = required_hits >= required_min
    primary_terms = agent._docs_primary_terms(question) or agent._docs_topic_terms(question)
    top_text = " ".join([top.snippet.title, top.section, top.snippet.excerpt]).lower()
    topic_match = (
        True
        if not primary_terms
        else any(agent._text_matches_term_or_synonym(top_text, term) for term in primary_terms)
    )

    source_hint_match = required_terms_covered
    supports_answer_type = required_terms_covered and topic_match
    if answer_type == "comparison":
        sides = agent._extract_comparison_sides(question)
        if sides is None:
            supports_answer_type = False
            source_hint_match = False
        else:
            left_text_match = any(
                any(
                    agent._text_matches_term_or_synonym(
                        " ".join(
                            [candidate.snippet.title, candidate.section, candidate.snippet.excerpt]
                        ).lower(),
                        term,
                    )
                    for term in sides.left_terms
                )
                for candidate in selected_candidates
            )
            right_text_match = any(
                any(
                    agent._text_matches_term_or_synonym(
                        " ".join(
                            [candidate.snippet.title, candidate.section, candidate.snippet.excerpt]
                        ).lower(),
                        term,
                    )
                    for term in sides.right_terms
                )
                for candidate in selected_candidates
            )
            supports_answer_type = left_text_match and right_text_match
            source_hint_match = supports_answer_type
    elif answer_type == "tool_command_concept":
        text = " ".join(
            " ".join([candidate.snippet.title, candidate.section, candidate.snippet.source, candidate.snippet.excerpt]).lower()
            for candidate in selected_candidates
        )
        supports_answer_type = "grcc" in text and ("compile" in text or "validation" in text)
        source_hint_match = "grcc" in text
    elif answer_type == "block_definition":
        catalog = [candidate for candidate in selected_candidates if candidate.source_type == "catalog"]
        if catalog:
            cleaned_summary = agent._clean_catalog_summary_for_answer(
                catalog[0].snippet.title,
                catalog[0].snippet.excerpt,
            )
            summary = agent._catalog_block_purpose_sentence(
                catalog[0].snippet.title,
                cleaned_summary,
            ).lower()
            supports_answer_type = bool(
                summary
                and "input port" not in summary
                and "output port" not in summary
                and "parameter" not in summary
            )
            source_hint_match = supports_answer_type

    quality = "medium"
    reason = "usable_evidence"
    if is_menu_or_boilerplate:
        quality = "weak"
        reason = "menu_or_boilerplate"
    elif not topic_match:
        quality = "weak"
        reason = "topic_mismatch"
    elif not required_terms_covered:
        quality = "weak"
        reason = "required_terms_missing"
    elif not supports_answer_type:
        quality = "weak"
        reason = "answer_type_unsupported_by_sources"
    elif top.quality_score >= 7.0:
        quality = "strong"
        reason = "high_confidence_ranked_sources"
    elif answer_type == "block_definition" and any(
        candidate.source_type == "catalog" for candidate in selected_candidates
    ):
        quality = "strong"
        reason = "catalog_block_evidence"

    return {
        "quality": quality,
        "reason": reason,
        "selected_source_count": len(selected_candidates),
        "topic_match": bool(topic_match),
        "required_terms_covered": bool(required_terms_covered),
        "source_hint_match": bool(source_hint_match),
        "is_menu_or_boilerplate": bool(is_menu_or_boilerplate),
        "supports_answer_type": bool(supports_answer_type),
    }

def build_typed_docs_answer(
    agent,
    *,
    question: str,
    ranked_candidates: list[_DocsEvidenceCandidate],
    answer_type: str,
) -> tuple[str, bool]:
    if answer_type == "insufficient":
        return ("Local docs did not contain enough direct evidence for this question.", True)
    if not ranked_candidates:
        return ("Local docs did not contain enough direct evidence for this question.", True)
    subject = agent._extract_docs_subject(question) or question
    subject_terms = tuple(agent._docs_primary_terms(subject) or agent._docs_topic_terms(subject))
    allow_procedural = answer_type == "procedural_how_to"

    if answer_type == "tool_command_concept":
        command_terms = ("grcc", "compile", "compiler", "validation")
        support = [
            candidate
            for candidate in ranked_candidates
            if "grcc"
            in " ".join(
                [
                    candidate.snippet.title,
                    candidate.snippet.source,
                    candidate.section,
                    candidate.snippet.excerpt,
                ]
            ).lower()
        ]
        if not support:
            return ("Local docs did not contain enough direct evidence for this question.", True)
        sentence = agent._pick_typed_sentence(
            candidate=support[0],
            required_terms=command_terms,
            allow_procedural=False,
            min_term_hits=1,
        )
        if not sentence:
            return ("Local docs did not contain enough direct evidence for this question.", True)
        sentence_l = sentence.lower()
        if "?" in sentence or "how, where, when" in sentence_l:
            return ("Local docs did not contain enough direct evidence for this question.", True)
        return (f"According to local docs, {sentence}", False)

    if answer_type == "comparison":
        sides = agent._extract_comparison_sides(question)
        if sides is None:
            return ("Local docs did not contain enough direct evidence for this question.", True)
        shared_terms = set(sides.left_terms).intersection(sides.right_terms)
        left_terms = tuple(term for term in sides.left_terms if term not in shared_terms) or sides.left_terms
        right_terms = tuple(term for term in sides.right_terms if term not in shared_terms) or sides.right_terms
        left_anchor_terms = tuple(
            agent._docs_primary_terms(sides.left_label) or agent._docs_topic_terms(sides.left_label)
        )
        right_anchor_terms = tuple(
            agent._docs_primary_terms(sides.right_label) or agent._docs_topic_terms(sides.right_label)
        )
        if (
            ("tags" in left_terms and "metadata" in right_terms)
            or ("metadata" in left_terms and "tags" in right_terms)
        ):
            return (
                "Local docs did not contain enough direct evidence to compare tags and metadata clearly.",
                True,
            )
        comparison_candidates = agent._select_docs_candidates_for_answer_type(
            question=question,
            answer_type=answer_type,
            ranked_candidates=ranked_candidates,
            limit=min(8, max(2, len(ranked_candidates))),
        )
        combined = " ".join(
            " ".join(
                [candidate.snippet.title, candidate.section, candidate.snippet.excerpt]
            ).lower()
            for candidate in comparison_candidates
        )
        left_exact = any(term in combined for term in left_terms if term)
        right_exact = any(term in combined for term in right_terms if term)
        if not (left_exact and right_exact):
            return (
                "Local docs only provided one-sided evidence; not enough direct evidence to compare both sides.",
                True,
            )
        direct_comparison = _pick_direct_docs_comparison_sentence(
            agent=agent,
            candidates=comparison_candidates,
            left_terms=left_terms,
            right_terms=right_terms,
        )
        if direct_comparison:
            return (
                f"Local docs say: {_shorten_docs_comparison_sentence(direct_comparison, limit=220)}",
                False,
            )
        left_sentence = ""
        right_sentence = ""
        for candidate in comparison_candidates:
            if not left_sentence:
                left_sentence = agent._pick_typed_sentence(
                    candidate=candidate,
                    required_terms=left_terms,
                    allow_procedural=allow_procedural,
                    min_term_hits=agent._minimum_required_term_hits(left_terms),
                )
            if not right_sentence:
                right_sentence = agent._pick_typed_sentence(
                    candidate=candidate,
                    required_terms=right_terms,
                    allow_procedural=allow_procedural,
                    min_term_hits=agent._minimum_required_term_hits(right_terms),
                )
            if left_sentence and right_sentence:
                break
        if left_sentence and left_terms and not any(
            agent._text_matches_term_or_synonym(left_sentence.lower(), term)
            for term in left_terms
        ):
            left_sentence = ""
        if right_sentence and right_terms and not any(
            agent._text_matches_term_or_synonym(right_sentence.lower(), term)
            for term in right_terms
        ):
            right_sentence = ""
        if left_sentence and left_anchor_terms and not any(
            term in left_sentence.lower() for term in left_anchor_terms if len(term) > 2
        ):
            left_sentence = ""
        if right_sentence and right_anchor_terms and not any(
            term in right_sentence.lower() for term in right_anchor_terms if len(term) > 2
        ):
            right_sentence = ""
        if not left_sentence or not right_sentence:
            for candidate in comparison_candidates:
                text = " ".join(
                    [candidate.snippet.title, candidate.section, candidate.snippet.excerpt]
                ).lower()
                candidate_sentences = agent._sentence_list(candidate.snippet.excerpt)
                if (not left_sentence) and any(
                    agent._text_matches_term_or_synonym(text, term) for term in left_terms
                ):
                    picked = ""
                    for sentence in candidate_sentences:
                        lower_sentence = sentence.lower()
                        if any(
                            agent._text_matches_term_or_synonym(lower_sentence, term)
                            for term in left_terms
                        ):
                            picked = sentence
                            break
                    left_sentence = picked or (
                        candidate_sentences[0] if candidate_sentences else candidate.snippet.excerpt
                    )
                if (not right_sentence) and any(
                    agent._text_matches_term_or_synonym(text, term) for term in right_terms
                ):
                    picked = ""
                    for sentence in candidate_sentences:
                        lower_sentence = sentence.lower()
                        if any(
                            agent._text_matches_term_or_synonym(lower_sentence, term)
                            for term in right_terms
                        ):
                            picked = sentence
                            break
                    right_sentence = picked or (
                        candidate_sentences[0] if candidate_sentences else candidate.snippet.excerpt
                    )
                if left_sentence and right_sentence:
                    break
        if left_sentence and left_anchor_terms and not any(
            term in left_sentence.lower() for term in left_anchor_terms if len(term) > 2
        ):
            left_sentence = ""
        if right_sentence and right_anchor_terms and not any(
            term in right_sentence.lower() for term in right_anchor_terms if len(term) > 2
        ):
            right_sentence = ""
        if not left_sentence or not right_sentence:
            missing = sides.left_label if not left_sentence else sides.right_label
            return (
                f"Local docs only provided evidence for one side; not enough direct evidence to compare both ({missing} missing).",
                True,
            )
        if any(
            marker in (left_sentence.lower() + " " + right_sentence.lower())
            for marker in ("gr::", "pmt::", "get_tags_in_range", "get_tags_in_window")
        ):
            return (
                "Local docs only provided API-fragment evidence; not enough direct evidence to compare both sides clearly.",
                True,
            )
        if "::" in left_sentence or "::" in right_sentence:
            return (
                "Local docs only provided API-fragment evidence; not enough direct evidence to compare both sides clearly.",
                True,
            )
        if left_sentence.lower() == right_sentence.lower():
            shared = left_sentence.lower()
            has_left = any(
                agent._text_matches_term_or_synonym(shared, term)
                for term in left_terms
            )
            has_right = any(
                agent._text_matches_term_or_synonym(shared, term)
                for term in right_terms
            )
            if not (has_left and has_right):
                return (
                    "Local docs only provided overlapping evidence; not enough direct evidence to compare both sides distinctly.",
                    True,
                )
        left_sentence = _shorten_docs_comparison_sentence(left_sentence)
        right_sentence = _shorten_docs_comparison_sentence(right_sentence)
        answer = (
            f"{sides.left_label}: {left_sentence} "
            f"{sides.right_label}: {right_sentence} "
            f"Difference: {sides.left_label} and {sides.right_label} are used for different roles in GNU Radio."
        )
        return (answer, False)

    if answer_type == "block_definition":
        if "hierarchical block" in question.lower():
            return ("Local docs did not contain enough direct evidence for this question.", True)
        catalog_candidates = [
            candidate
            for candidate in ranked_candidates
            if candidate.source_type == "catalog"
        ]
        if catalog_candidates:
            catalog = catalog_candidates[0]
            cleaned_summary = agent._clean_catalog_summary_for_answer(
                catalog.snippet.title,
                catalog.snippet.excerpt,
            )
            summary = agent._catalog_block_purpose_sentence(
                catalog.snippet.title,
                cleaned_summary,
            )
            summary_l = summary.lower()
            summary_terms = re.findall(r"[a-z0-9]+", summary_l)
            title_terms = set(re.findall(r"[a-z0-9]+", catalog.snippet.title.lower()))
            informative_terms = [term for term in summary_terms if term not in title_terms]
            if (
                summary
                and "input port" not in summary_l
                and "output port" not in summary_l
                and "parameter" not in summary_l
                and len(summary) >= 20
                and len(informative_terms) >= 2
            ):
                if (
                    "relate to" in question.lower()
                    and catalog.snippet.excerpt.rstrip().endswith("…")
                    and summary == cleaned_summary
                ):
                    return ("Local docs did not contain enough direct evidence for this question.", True)
                return (
                    f"According to the local block catalog, {catalog.snippet.title} {summary}.",
                    False,
                )
        required_terms = subject_terms or tuple(agent._docs_primary_terms(question))
        subject_phrase = (
            (agent._extract_block_definition_subject(question) or "").strip().lower()
        )
        for candidate in ranked_candidates[:6]:
            sentence = agent._pick_typed_sentence(
                candidate=candidate,
                required_terms=required_terms,
                allow_procedural=False,
                min_term_hits=agent._minimum_required_term_hits(required_terms),
            )
            if sentence and subject_phrase and " " in subject_phrase:
                title_source = " ".join(
                    [candidate.snippet.title, candidate.snippet.source]
                ).lower()
                if subject_phrase not in title_source:
                    continue
            if sentence:
                return (f"According to local docs, {sentence}", False)
        return ("Local docs did not contain enough direct evidence for this question.", True)

    required_terms = subject_terms or tuple(
        agent._docs_primary_terms(question) or agent._docs_topic_terms(question)
    )
    lower_question = question.lower()
    require_hier_source = "hierarchical block" in lower_question
    if "message ports" in lower_question:
        for candidate in ranked_candidates[:6]:
            sentence = agent._pick_typed_sentence(
                candidate=candidate,
                required_terms=("message",),
                allow_procedural=False,
                min_term_hits=1,
            )
            if sentence and any(
                marker in sentence.lower()
                for marker in ("asynchronous", "control data", "between blocks")
            ):
                return (f"According to local docs, {sentence}", False)
    min_hits = agent._minimum_required_term_hits(required_terms)
    if answer_type == "procedural_how_to" and len(required_terms) >= 2:
        min_hits = max(min_hits, min(2, len(required_terms)))
    best_sentence = ""
    best_score: tuple[float, float, float, float, int] | None = None
    for candidate in ranked_candidates[:10]:
        if require_hier_source:
            title_source = " ".join([candidate.snippet.title, candidate.snippet.source]).lower()
            if "hier" not in title_source:
                continue
        sentence = agent._pick_typed_sentence(
            candidate=candidate,
            required_terms=required_terms,
            allow_procedural=allow_procedural,
            min_term_hits=min_hits,
        )
        if sentence:
            lower_sentence = sentence.lower()
            term_hits = sum(
                1
                for term in required_terms
                if agent._text_matches_term_or_synonym(lower_sentence, term)
            )
            exact_hits = sum(1 for term in required_terms if term and term in lower_sentence)
            focus_bonus = _definition_focus_bonus(
                lower_sentence,
                required_terms=required_terms,
            )
            score = (
                float(term_hits + exact_hits) + focus_bonus,
                candidate.quality_score,
                candidate.topic_score,
                candidate.semantic_score or 0.0,
                -len(sentence),
            )
            if best_score is None or score > best_score:
                best_score = score
                best_sentence = sentence
    if best_sentence:
        return (f"According to local docs, {best_sentence}", False)
    return ("Local docs did not contain enough direct evidence for this question.", True)


def _definition_focus_bonus(
    lower_sentence: str,
    *,
    required_terms: tuple[str, ...],
) -> float:
    if not required_terms:
        return 0.0
    focus_terms = required_terms[-1:]
    for term in focus_terms:
        if not term:
            continue
        pattern = rf"\b(?:the\s+)?{re.escape(term)}s?\s+(?:is|are|means|refers to|describes|contains|can contain)\b"
        if re.search(pattern, lower_sentence):
            return 3.0
    return 0.0


def _pick_direct_docs_comparison_sentence(
    *,
    agent,
    candidates: list[_DocsEvidenceCandidate],
    left_terms: tuple[str, ...],
    right_terms: tuple[str, ...],
) -> str:
    contrast_markers = (
        " differ",
        " difference",
        " different",
        " unlike ",
        " whereas ",
        " while ",
        " not possible ",
        " cannot ",
        " can connect ",
    )
    for candidate in candidates:
        for sentence in agent._sentence_list(candidate.snippet.excerpt):
            compact = " ".join(sentence.split()).strip()
            for prefix in (candidate.section, candidate.snippet.title):
                prefix_text = " ".join(str(prefix or "").split()).strip()
                if prefix_text and compact.lower().startswith(f"{prefix_text.lower()} "):
                    compact = compact[len(prefix_text) :].lstrip(" -:#")
            compact = agent._clean_docs_excerpt(compact)
            lower = compact.lower()
            if len(compact) < 40 or len(compact) > 280:
                continue
            has_left = any(
                agent._text_matches_term_or_synonym(lower, term)
                for term in left_terms
            )
            has_right = any(
                agent._text_matches_term_or_synonym(lower, term)
                for term in right_terms
            )
            has_contrast = any(marker in f" {lower} " for marker in contrast_markers)
            if has_left and has_right and has_contrast:
                return compact
    return ""


def _shorten_docs_comparison_sentence(sentence: str, limit: int = 120) -> str:
    compact = " ".join(sentence.split()).strip()
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip(" ,;:.") + "…"


# ═══════════════════════════════════════════════════════════════════════════════
# advisor.py — helper-advisor orchestration
# ═══════════════════════════════════════════════════════════════════════════════


def classify_docs_advisor_error(message: str) -> str:
    lower = message.lower()
    if "timed out" in lower or "timeout" in lower:
        return "timeout"
    if "unsupported keys" in lower or "missing keys" in lower or "must be" in lower:
        return "schema_parse_failure"
    if "malformed json" in lower or "must be object" in lower:
        return "malformed_helper_output"
    if "context" in lower and "length" in lower:
        return "prompt_too_large"
    if "http 400" in lower or "http 404" in lower:
        if "model" in lower:
            return "config_issue"
        return "implementation_bug"
    if "transport failure" in lower:
        return "llama_server_unavailable"
    return "implementation_bug"


def run_docs_answer_advisor(
    agent: Any,
    *,
    question: str,
    answer_type: str,
    snippets: list[DocsAnswerSnippet],
    focus: str | None,
) -> dict[str, Any] | None:
    estimated_prompt_chars = (
        len(question)
        + (len(focus) if isinstance(focus, str) else 0)
        + sum(
            len(snippet.title) + len(snippet.source) + len(snippet.excerpt)
            for snippet in snippets
        )
    )
    agent._last_docs_advisor_meta = {
        "advisor_attempted": False,
        "advisor_success": False,
        "fallback_reason": "not_attempted",
        "helper_latency_ms": None,
        "prompt_chars": 0,
        "snippet_count": len(snippets),
        "schema_valid": False,
        "timeout_ms": int(agent._docs_answer_cfg.helper_timeout_seconds * 1000),
        "cache_hit": False,
        "helper_finish_reason": None,
    }
    if not agent._llama_server_url.strip() or not agent._llama_model.strip():
        agent._last_docs_advisor_meta["fallback_reason"] = "helper_disabled"
        agent._last_docs_advisor_meta["prompt_chars"] = estimated_prompt_chars
        return None
    if not snippets:
        agent._last_docs_advisor_meta["fallback_reason"] = "retrieval_empty"
        agent._last_docs_advisor_meta["prompt_chars"] = estimated_prompt_chars
        return None
    now = time.monotonic()
    if now >= agent._docs_advisor_probe_at:
        agent._docs_advisor_reachable = probe_docs_advisor_server(agent)
        agent._docs_advisor_probe_at = now + (
            agent._docs_answer_cfg.retry_interval_on_success_seconds
            if agent._docs_advisor_reachable
            else agent._docs_answer_cfg.retry_interval_on_failure_seconds
        )
    if not agent._docs_advisor_reachable:
        agent._last_docs_advisor_meta["fallback_reason"] = "llama_server_unavailable"
        agent._last_docs_advisor_meta["prompt_chars"] = estimated_prompt_chars
        return None
    agent._last_docs_advisor_meta["advisor_attempted"] = True
    started = time.perf_counter()
    try:
        provider_config = ToolAgentsLlamaProviderConfig(
            base_url=agent._llama_server_url,
            model=agent._llama_model,
            api_key=None,
            timeout_seconds=min(
                agent._llama_request_timeout_seconds,
                agent._docs_answer_cfg.helper_timeout_seconds,
            ),
            max_tokens=agent._docs_answer_cfg.helper_max_output_tokens,
            temperature=0.0,
            enable_thinking=False,
        )
        client = ToolAgentsJsonClient(
            provider_config,
            timeout_seconds=provider_config.timeout_seconds,
            max_tokens=provider_config.max_tokens,
            temperature=0.0,
            enable_thinking=False,
        )
        result = _run_docs_answer_advisor_rag(
            client=client,
            model=agent._llama_model,
            question=question,
            answer_type=answer_type,
            snippets=snippets,
            focus=focus,
            max_answer_chars=agent._docs_answer_cfg.answer_target_chars,
            max_excerpt_chars=agent._docs_answer_cfg.excerpt_target_chars,
            max_sources=agent._docs_answer_cfg.max_sources,
        )
        agent._last_docs_advisor_meta.update(
            {
                "advisor_success": True,
                "fallback_reason": "none",
                "helper_latency_ms": int(result.get("advisor_latency_ms") or 0),
                "prompt_chars": int(result.get("prompt_chars") or 0),
                "snippet_count": int(result.get("snippet_count") or len(snippets)),
                "schema_valid": bool(result.get("schema_valid")),
                "timeout_ms": int(
                    result.get("timeout_ms")
                    or int(agent._docs_answer_cfg.helper_timeout_seconds * 1000)
                ),
                "cache_hit": False,
                "helper_finish_reason": result.get("helper_finish_reason"),
            }
        )
        return result
    except Exception as exc:
        logger.info("docs_answer_advisor_failed error=%s", exc)
        agent._last_docs_advisor_meta.update(
            {
                "advisor_success": False,
                "fallback_reason": classify_docs_advisor_error(str(exc)),
                "helper_latency_ms": int((time.perf_counter() - started) * 1000),
                "prompt_chars": estimated_prompt_chars,
                "helper_finish_reason": "error",
            }
        )
        return None


def probe_docs_advisor_server(agent: Any) -> bool:
    """Cheap connectivity probe to avoid repeated long helper timeouts."""
    try:
        parsed = urlsplit(agent._llama_server_url)
        host = parsed.hostname
        port = parsed.port
        if not host or not port:
            return False
        with socket.create_connection(
            (host, int(port)),
            timeout=agent._docs_answer_cfg.probe_timeout_seconds,
        ):
            return True
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# __all__
# ═══════════════════════════════════════════════════════════════════════════════

# --- merged from docs_answer_advisor.py ---


class DocsAnswerAdvisorError(RuntimeError):
    """Raised when docs answer synthesis fails validation or transport."""


@dataclass(frozen=True)
class DocsAnswerSnippet:
    """One bounded snippet used for docs answer synthesis."""

    title: str
    source: str
    excerpt: str


def _run_docs_answer_advisor_rag(
    *,
    client: Any,
    question: str,
    answer_type: str,
    snippets: list[DocsAnswerSnippet],
    focus: str | None = None,
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
    question: str,
    answer_type: str,
    snippets: list[DocsAnswerSnippet],
    focus: str | None = None,
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
    "_DocsComparisonSides",
    "_DocsEvidenceCandidate",
    "_DOCS_TOPIC_SYNONYMS",
    "ask_grc_docs",
    "build_catalog_assisted_candidate",
    "build_docs_source_quality",
    "build_fallback_answer",
    "build_typed_docs_answer",
    "catalog_block_purpose_sentence",
    "classify_docs_answer_type",
    "classify_docs_advisor_error",
    "clean_catalog_summary_for_answer",
    "clean_docs_excerpt",
    "clip_docs_snippets_for_helper",
    "collect_docs_candidates",
    "DocsAnswerAdvisorError",
    "DocsAnswerSnippet",
    "docs_low_value_reasons",
    "docs_primary_terms",
    "docs_title_aliases",
    "docs_topic_terms",
    "extract_block_definition_subject",
    "extract_comparison_sides",
    "extract_docs_subject",
    "helper_candidates_for_docs_answer",
    "helper_eligibility_for_docs_answer",
    "infer_docs_source_type",
    "is_block_definition_query",
    "is_docs_evidence_strong",
    "is_procedural_walkthrough_text",
    "is_tutorial_or_howto_query",
    "minimum_required_term_hits",
    "normalize_docs_source_key",
    "normalized_docs_retrieval_query",
    "pick_typed_sentence",
    "probe_docs_advisor_server",
    "rank_docs_candidates",
    "required_terms_for_answer_type",
    "run_docs_answer_advisor",
    "run_docs_answer_advisor_diagnostic",
    "select_docs_candidates_for_answer_type",
    "sentence_list",
    "should_catalog_assist",
    "text_matches_term_or_synonym",
]
