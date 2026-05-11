"""Docs-answer candidate selection and deterministic fallback helpers."""

from __future__ import annotations

import re
from typing import Any

from grc_agent.runtime.docs_answer_advisor import DocsAnswerSnippet

from .evidence import (
    _DOCS_TOPIC_SYNONYMS,
    _DocsComparisonSides,
    _DocsEvidenceCandidate,
)
from .formatting import (
    _DOCS_NAVIGATION_MARKERS,
    docs_primary_terms,
    docs_topic_terms,
    extract_block_definition_subject,
    extract_docs_subject,
    is_block_definition_query,
    is_procedural_walkthrough_text,
    is_tutorial_or_howto_query,
)


def build_catalog_assisted_candidate(
    agent: Any,
    *,
    question: str,
) -> _DocsEvidenceCandidate | None:
    try:
        result = agent._search_blocks(question, k=3, debug=False, enrich=True)
    except Exception:
        return None
    if result.get("ok") is not True:
        return None
    rows = result.get("results")
    if not isinstance(rows, list) or not rows:
        return None
    subject = extract_block_definition_subject(question) or question
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
        lexical_score=18.0,
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
    if is_tutorial_or_howto_query(question):
        return "procedural_how_to"
    if lower.startswith(("what is ", "what are ", "explain ")):
        return "definition"
    return "definition"


def normalized_docs_retrieval_query(*, question: str, answer_type: str) -> str:
    query = " ".join(question.split()).strip()
    if answer_type == "tool_command_concept" and "grcc" in query.lower():
        return "grcc compile validation flowgraph"
    return query


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
        lower = sentence.lower()
        if len(sentence) < 24 or len(sentence) > 220:
            continue
        if sentence.count("#") >= 1 or "```" in sentence:
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
        term_hits = sum(1 for term in required_terms if term in lower)
        if required_terms and term_hits < max(1, min_term_hits):
            continue
        synonym_hits = 0
        for term in required_terms:
            for synonym in _DOCS_TOPIC_SYNONYMS.get(term, ()):
                if synonym in lower:
                    synonym_hits += 1
        score = float(term_hits) + min(2.0, float(synonym_hits) * 0.5)
        if re.search(r"\b(is|are|means|refers to|used for|allows|converts|provides|carries)\b", lower):
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


def is_lexical_docs_evidence_strong(
    *,
    query: str,
    question: str,
    answer_type: str,
    lexical_payload: dict[str, Any],
    limit: int,
) -> bool:
    if lexical_payload.get("ok") is not True:
        return False
    results = lexical_payload.get("results")
    if not isinstance(results, list) or not results:
        return False
    top = results[0] if isinstance(results[0], dict) else {}
    top_title = str(top.get("title") or "").lower()
    top_excerpt = str(top.get("excerpt") or "").lower()
    top_source = ""
    citation = top.get("citation")
    if isinstance(citation, dict):
        top_source = str(citation.get("url") or citation.get("path") or "").lower()
    from .formatting import _DOCS_MENU_TITLE_MARKERS

    if any(marker in top_title for marker in _DOCS_MENU_TITLE_MARKERS):
        return False
    if top_title.strip() == "what is gnu radio":
        return False
    if any(marker in top_excerpt for marker in _DOCS_NAVIGATION_MARKERS):
        return False
    if answer_type == "tool_command_concept":
        top_text = " ".join([top_title, top_excerpt, top_source]).lower()
        has_grcc = "grcc" in top_text
        has_tool_context = ("compile" in top_text) or ("validation" in top_text)
        return has_grcc and has_tool_context
    if answer_type == "comparison":
        sides = extract_comparison_sides(question)
        if sides is None:
            return False
        rows = [row for row in results if isinstance(row, dict)][: max(3, min(6, limit + 2))]
        left_match = False
        right_match = False
        for row in rows:
            citation = row.get("citation") if isinstance(row.get("citation"), dict) else {}
            row_source = str(citation.get("url") or citation.get("path") or "").lower()
            row_text = " ".join(
                [
                    str(row.get("title") or "").lower(),
                    str(row.get("excerpt") or "").lower(),
                    row_source,
                ]
            )
            if not left_match and any(
                text_matches_term_or_synonym(row_text, term) for term in sides.left_terms
            ):
                left_match = True
            if not right_match and any(
                text_matches_term_or_synonym(row_text, term) for term in sides.right_terms
            ):
                right_match = True
            if left_match and right_match:
                break
        if not (left_match and right_match):
            return False
    if is_procedural_walkthrough_text(top_excerpt) and not is_tutorial_or_howto_query(query):
        query_tokens = docs_primary_terms(query)
        title_hits = sum(1 for token in query_tokens if token and token in top_title)
        if title_hits == 0:
            return False
    top_score = top.get("score")
    score_value = float(top_score) if isinstance(top_score, int | float) else 0.0
    result_count = len([row for row in results if isinstance(row, dict)])
    if score_value >= 28.0:
        query_tokens = docs_primary_terms(query)
        if query_tokens and not any(
            token in top_title or token in top_excerpt or token in top_source
            for token in query_tokens
        ):
            return False
        return True
    if score_value >= 20.0 and result_count >= min(2, max(1, limit)):
        query_tokens = docs_primary_terms(query)
        if query_tokens and not any(
            token in top_title or token in top_excerpt or token in top_source
            for token in query_tokens
        ):
            return False
        return True
    query_tokens = docs_primary_terms(query) or docs_topic_terms(query)
    token_hits = sum(1 for token in query_tokens if token and token in top_excerpt)
    title_hits = sum(1 for token in query_tokens if token and token in top_title)
    if query_tokens and (token_hits + title_hits) == 0:
        return False
    if token_hits < max(1, min(3, len(query_tokens))):
        return False
    if score_value < 12.0 and token_hits < 2:
        return False
    return True
