"""Docs-answer text formatting and source-shape helpers."""

from __future__ import annotations

import re
from typing import Any

from grc_agent.runtime.docs_answer_advisor import DocsAnswerSnippet

from .evidence import _DocsEvidenceCandidate

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
