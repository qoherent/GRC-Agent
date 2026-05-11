"""Docs-answer wrapper pipeline extracted from GrcAgent."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from grc_agent._payload import ErrorCode

if TYPE_CHECKING:
    from grc_agent.agent import GrcAgent, ToolResult


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
    handlers.append("search_manual")
    lexical_payload = agent_module.search_manual(retrieval_query, k=retrieval_k)
    semantic_manual: dict[str, Any] = {"ok": False, "results": []}
    semantic_tutorial: dict[str, Any] = {"ok": False, "results": []}
    degraded_retrieval = False
    fallback_used = False
    fallback_reason = "not_attempted"
    warnings: list[str] = []
    retrieval_mode = "lexical_only"

    lexical_strong = agent._is_lexical_docs_evidence_strong(
        query=retrieval_query,
        question=question_text,
        answer_type=answer_type,
        lexical_payload=lexical_payload,
        limit=limit,
    )
    lexical_weak = not lexical_strong
    tutorial_or_howto_query = agent._is_tutorial_or_howto_query(question_text)
    run_manual_semantic = (
        agent._docs_answer_cfg.semantic_manual_enabled
        and (not agent._docs_answer_cfg.lexical_first or lexical_weak)
    )
    run_tutorial_semantic = (
        agent._docs_answer_cfg.semantic_tutorial_enabled
        and (tutorial_or_howto_query or lexical_weak)
        and (not agent._docs_answer_cfg.lexical_first or lexical_weak)
    )

    if run_manual_semantic:
        handlers.append("semantic_search_grc(manual)")
        semantic_manual = agent_module.semantic_search_grc(
            retrieval_query,
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
        semantic_tutorial = agent_module.semantic_search_grc(
            retrieval_query,
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

    if run_manual_semantic and run_tutorial_semantic:
        retrieval_mode = "lexical_plus_manual_and_tutorial_semantic"
    elif run_manual_semantic:
        retrieval_mode = "lexical_plus_manual_semantic"
    elif run_tutorial_semantic:
        retrieval_mode = "lexical_plus_tutorial_semantic"

    if degraded_retrieval:
        retrieval_mode = "lexical_fallback_missing_vector"

    candidates = agent._collect_docs_candidates(
        lexical_payload=lexical_payload,
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
    elif agent._should_catalog_assist(question_text, ranked_candidates):
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
    selected_candidates = agent._select_docs_candidates_for_answer_type(
        question=question_text,
        answer_type=answer_type,
        ranked_candidates=selected_pool,
        limit=max(1, min(limit, agent._retrieval_cfg.ask_grc_docs_max_k)),
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
    source_limit = min(limit, agent._docs_answer_cfg.max_sources)
    sources = [
        {
            "title": snippet.title,
            "source": snippet.source,
            "excerpt": snippet.excerpt[: agent._docs_answer_cfg.excerpt_target_chars],
        }
        for snippet in snippets
    ]
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
    if snippets and cached_docs_answer is None:
        helper_eligible = False
        helper_skipped_reason = "not_evaluated"
        typed_answer = "Local docs did not contain enough direct evidence for this question."
        typed_insufficient = True
        if str(source_quality.get("quality")) != "weak":
            typed_answer, typed_insufficient = agent._build_typed_docs_answer(
                question=question_text,
                ranked_candidates=ranked_candidates,
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
            if helper_eligible:
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
    if cached_docs_answer is None:
        agent._ask_grc_docs_cache_put(
            cache_key,
            {
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
            },
        )

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


def build_docs_source_quality(
    agent: "GrcAgent",
    *,
    question: str,
    answer_type: str,
    selected_candidates: list[Any],
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
    agent: "GrcAgent",
    *,
    question: str,
    ranked_candidates: list[Any],
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
        if not left_sentence:
            for candidate in comparison_candidates:
                sentence = agent._pick_typed_sentence(
                    candidate=candidate,
                    required_terms=left_anchor_terms,
                    allow_procedural=allow_procedural,
                    min_term_hits=agent._minimum_required_term_hits(left_anchor_terms),
                )
                if sentence:
                    left_sentence = sentence
                    break
        if not right_sentence:
            for candidate in comparison_candidates:
                sentence = agent._pick_typed_sentence(
                    candidate=candidate,
                    required_terms=right_anchor_terms,
                    allow_procedural=allow_procedural,
                    min_term_hits=agent._minimum_required_term_hits(right_anchor_terms),
                )
                if sentence:
                    right_sentence = sentence
                    break
        if not left_sentence or not right_sentence:
            return (
                "Local docs only provided one-sided evidence; not enough direct evidence to compare both sides.",
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
            import re
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
    if "hierarchical block" in lower_question:
        return ("Local docs did not contain enough direct evidence for this question.", True)
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
    for candidate in ranked_candidates[:6]:
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
            return (f"According to local docs, {sentence}", False)
    return ("Local docs did not contain enough direct evidence for this question.", True)
