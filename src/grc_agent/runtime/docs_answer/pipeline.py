"""Docs-answer wrapper pipeline extracted from GrcAgent."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from grc_agent._payload import ErrorCode
from grc_agent.runtime.docs_answer_advisor import DocsAnswerSnippet

from .evidence import _DOCS_TOPIC_SYNONYMS, _DocsEvidenceCandidate

if TYPE_CHECKING:
    from grc_agent.agent import GrcAgent, ToolResult

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
    agent: "GrcAgent",
    question: str,
    k: int | None = None,
    focus: str | None = None,
    debug: bool = False,
) -> "ToolResult":
    import time

    import grc_agent.agent as agent_module

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

    retrieval_k = max(
        limit,
        min(agent._retrieval_cfg.ask_grc_docs_max_k, max(limit + 2, 5)),
    )
    retrieval_query = agent._normalized_docs_retrieval_query(
        question=question_text,
        answer_type=answer_type,
    )
    retrieval_queries = _docs_retrieval_queries(question_text, retrieval_query)
    semantic_manual: dict[str, Any] = {"ok": False, "results": []}
    semantic_tutorial: dict[str, Any] = {"ok": False, "results": []}
    degraded_retrieval = False
    fallback_used = False
    fallback_reason = "not_attempted"
    warnings: list[str] = []
    retrieval_mode = "semantic_docs"
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

    run_manual_semantic = agent._docs_answer_cfg.semantic_manual_enabled
    run_tutorial_semantic = agent._docs_answer_cfg.semantic_tutorial_enabled

    if run_manual_semantic:
        handlers.append("semantic_search_grc(manual)")
        semantic_manual = _run_docs_semantic_queries(
            agent_module,
            retrieval_queries,
            scope="manual",
            k=retrieval_k,
        )
        if semantic_manual.get("ok") is not True and semantic_manual.get(
            "error_type"
        ) in {
            "missing_index",
            ErrorCode.RETRIEVAL_NOT_READY,
        }:
            degraded_retrieval = True

    if run_tutorial_semantic:
        handlers.append("semantic_search_grc(tutorial)")
        semantic_tutorial = _run_docs_semantic_queries(
            agent_module,
            retrieval_queries,
            scope="tutorial",
            k=retrieval_k,
        )
        if semantic_tutorial.get("ok") is not True and semantic_tutorial.get(
            "error_type"
        ) in {
            "missing_index",
            ErrorCode.RETRIEVAL_NOT_READY,
        }:
            degraded_retrieval = True

    enabled_semantic_payloads = [
        payload
        for enabled, payload in (
            (run_manual_semantic, semantic_manual),
            (run_tutorial_semantic, semantic_tutorial),
        )
        if enabled
    ]
    if (
        enabled_semantic_payloads
        and all(payload.get("ok") is not True for payload in enabled_semantic_payloads)
    ):
        error_type = (
            semantic_manual.get("error_type")
            or semantic_tutorial.get("error_type")
            or ErrorCode.RETRIEVAL_NOT_READY
        )
        warnings.append("vector_index_missing_or_not_ready")
        result = agent._payload_result(
            "ask_grc_docs",
            {
                "ok": False,
                "question": question_text,
                "focus": focus_text,
                "answer": "",
                "sources": [],
                "insufficient_evidence": True,
                "fallback_used": False,
                "degraded_retrieval": True,
                "retrieval_mode": "semantic_unavailable",
                "warnings": warnings,
                "message": "Docs vector retrieval is not available.",
                "error_type": error_type,
            },
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

    candidates = agent._collect_docs_candidates(
        semantic_manual=semantic_manual,
        semantic_tutorial=semantic_tutorial,
    )
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
        warnings.append("vector_index_missing_or_not_ready")

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


def _docs_retrieval_queries(question_text: str, retrieval_query: str) -> list[str]:
    raw = " ".join(question_text.split())
    expanded = " ".join(retrieval_query.split())
    if not raw:
        return [expanded] if expanded else []
    if not expanded or _docs_query_key(raw) == _docs_query_key(expanded):
        return [raw]
    return [raw, expanded]


def _docs_query_key(query: str) -> str:
    return " ".join(re.findall(r"[a-z0-9_]+", query.lower()))


def _docs_answer_candidate_limit(*, requested_limit: int, max_limit: int) -> int:
    # Keep the model-facing source count small while answer extraction can see
    # a few adjacent retrieved chunks from the same topic/page.
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


def _run_docs_semantic_queries(
    agent_module: Any,
    queries: list[str],
    *,
    scope: str,
    k: int,
) -> dict[str, Any]:
    payloads = [
        agent_module.semantic_search_grc(query, scope=scope, k=k)
        for query in queries
        if query
    ]
    if not payloads:
        return {"ok": False, "results": []}
    ok_payloads = [payload for payload in payloads if payload.get("ok") is True]
    if not ok_payloads:
        return payloads[0]

    merged_results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for payload in ok_payloads:
        for row in payload.get("results", []):
            if not isinstance(row, dict):
                continue
            result_id = _semantic_result_id(row)
            if result_id in seen:
                continue
            seen.add(result_id)
            merged_results.append(row)

    merged = dict(ok_payloads[0])
    merged["results"] = merged_results
    warnings: list[Any] = []
    for payload in ok_payloads:
        raw_warnings = payload.get("warnings")
        if isinstance(raw_warnings, list):
            warnings.extend(raw_warnings)
    merged["warnings"] = list(dict.fromkeys(warnings))
    return merged


def _semantic_result_id(row: dict[str, Any]) -> str:
    for key in ("record_id", "canonical_block_id"):
        value = row.get(key)
        if isinstance(value, str) and value:
            return value
    return repr(sorted(row.items()))


def _docs_candidate_record_key(
    *,
    record_id: Any,
    source: str,
    excerpt: str,
) -> str:
    if isinstance(record_id, str) and record_id:
        return f"record:{record_id}"
    source_key = _docs_query_key(source)
    excerpt_key = _docs_query_key(excerpt[:160])
    return f"source_excerpt:{source_key}:{excerpt_key}"


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


def collect_docs_candidates(
    agent,
    *,
    semantic_manual: dict[str, Any],
    semantic_tutorial: dict[str, Any],
) -> list[_DocsEvidenceCandidate]:
    candidates: list[_DocsEvidenceCandidate] = []
    seen_records: set[str] = set()

    def _append(
        *,
        record_id: Any,
        title: Any,
        source: Any,
        excerpt: Any,
        section: Any,
        channel: str,
        semantic_score: float | None = None,
        source_type_hint: str | None = None,
    ) -> None:
        title_text = " ".join(str(title or "").split()).strip()
        source_text = " ".join(str(source or "").split()).strip()
        excerpt_text = agent._clean_docs_excerpt(str(excerpt or ""))
        if not title_text or not source_text or not excerpt_text:
            return
        aliases = agent._docs_title_aliases(title_text)
        if aliases:
            source_text = f"{source_text} | title_aliases:{','.join(aliases)}"

        record_key = _docs_candidate_record_key(
            record_id=record_id,
            source=source_text,
            excerpt=excerpt_text,
        )
        if record_key in seen_records:
            return
        seen_records.add(record_key)

        lower_excerpt = excerpt_text.lower()
        if title_text and lower_excerpt.startswith(title_text.lower()):
            excerpt_text = excerpt_text[len(title_text):].lstrip(" -:.\n")
        elif source_text and lower_excerpt.startswith(source_text.lower()):
            excerpt_text = excerpt_text[len(source_text):].lstrip(" -:.\n")
        excerpt_text = agent._clean_docs_excerpt(excerpt_text)

        max_collected_excerpt_chars = max(
            agent._docs_answer_cfg.helper_max_total_context_chars,
            agent._docs_answer_cfg.helper_max_snippet_chars,
            agent._docs_answer_cfg.excerpt_target_chars * 2,
        )
        if len(excerpt_text) > max_collected_excerpt_chars:
            excerpt_text = excerpt_text[:max_collected_excerpt_chars].rstrip()
        candidates.append(
            _DocsEvidenceCandidate(
                snippet=DocsAnswerSnippet(
                    title=title_text,
                    source=source_text,
                    excerpt=excerpt_text,
                ),
                source_channel=channel,
                source_type=agent._infer_docs_source_type(
                    source=source_text,
                    title=title_text,
                    source_type_hint=source_type_hint,
                ),
                section=" ".join(str(section or "").split()).strip(),
                semantic_score=semantic_score,
                topic_score=0.0,
                quality_score=0.0,
                low_value_reasons=(),
                procedural=False,
            )
        )

    for payload, channel in (
        (semantic_manual, "semantic_manual"),
        (semantic_tutorial, "semantic_tutorial"),
    ):
        if payload.get("ok") is not True:
            continue
        for row in payload.get("results", []):
            if not isinstance(row, dict):
                continue
            provenance = row.get("provenance")
            source = None
            if isinstance(provenance, dict):
                source = provenance.get("url") or provenance.get("path")
            semantic_raw = row.get("vector_score_raw")
            semantic_score = (
                float(semantic_raw)
                if isinstance(semantic_raw, int | float)
                else None
            )
            _append(
                record_id=row.get("record_id"),
                title=row.get("title"),
                source=source,
                excerpt=row.get("excerpt"),
                section=row.get("section"),
                channel=channel,
                semantic_score=semantic_score,
                source_type_hint=str(row.get("source_type") or ""),
            )
    return candidates

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
