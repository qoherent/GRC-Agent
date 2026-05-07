"""Evaluate ask_grc_docs wrapper behavior on a labeled local docs corpus."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
import json
from pathlib import Path
from statistics import median
import time
from typing import Any

from grc_agent.agent import GrcAgent
from grc_agent.config import default_app_config
from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.manual.search import search_manual
from grc_agent.retrieval.vector import semantic_search_grc
from tests.retrieval_eval._eval_gate_lock import acquire_retrieval_eval_lock


DEFAULT_CORPUS = Path("tests/data/grc_docs_answer_eval.jsonl")
DEFAULT_ADVISOR_REPORT = Path("reports/GRC_DOCS_ANSWER_ADVISOR_REPORT.md")
DEFAULT_RETRIEVAL_AUDIT = Path("reports/GRC_DOCS_RETRIEVAL_QUALITY_AUDIT.md")
DEFAULT_FALLBACK_AUDIT = Path("reports/GRC_DOCS_FALLBACK_ANSWER_QUALITY_AUDIT.md")
DEFAULT_EFFICIENCY_REPORT = Path("reports/GRC_DOCS_ANSWER_EFFICIENCY_REPORT.md")
DEFAULT_HELPER_TRIAGE_REPORT = Path("reports/DOCS_HELPER_TRIAGE_2026-05-04.md")

_FAILURE_REASONS = (
    "menu/index page",
    "tutorial step not conceptual answer",
    "wrong topic",
    "too generic",
    "snippet fragment",
    "no real evidence",
    "source missing exact term",
    "insufficient_evidence should be true",
)
_MENU_MARKERS = ("tutorials", "main page", "index", "what is gnu radio")


@dataclass(frozen=True)
class EvalRow:
    question: str
    expected_topic: str
    expected_source_hint: str
    answer_type: str
    required_terms: tuple[str, ...]
    required_source_hint: str
    required_source_hints: tuple[str, ...]
    expected_sides: tuple[str, ...]
    allow_catalog_assist: bool
    should_have_answer: bool
    notes: str


def _load_rows(path: Path) -> list[EvalRow]:
    rows: list[EvalRow] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        rows.append(
            EvalRow(
                question=str(payload.get("question", "")),
                expected_topic=str(payload.get("expected_topic", "")),
                expected_source_hint=str(payload.get("expected_source_hint", "")),
                answer_type=str(payload.get("answer_type", "definition")),
                required_terms=tuple(
                    str(term).strip().lower()
                    for term in payload.get("required_terms", [])
                    if str(term).strip()
                ),
                required_source_hint=str(
                    payload.get("required_source_hint", payload.get("expected_source_hint", ""))
                ),
                required_source_hints=tuple(
                    str(item).strip()
                    for item in payload.get("required_source_hints", [])
                    if str(item).strip()
                ),
                expected_sides=tuple(
                    str(item).strip().lower()
                    for item in payload.get("expected_sides", [])
                    if str(item).strip()
                ),
                allow_catalog_assist=bool(payload.get("allow_catalog_assist", False)),
                should_have_answer=bool(payload.get("should_have_answer", False)),
                notes=str(payload.get("notes", "")),
            )
        )
    return rows


def _percentile(values: list[int], pct: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    idx = int(round((len(ordered) - 1) * pct))
    return int(ordered[max(0, min(idx, len(ordered) - 1))])


def _contains_mutation_payload(value: Any) -> bool:
    forbidden = {
        "transaction",
        "params",
        "insert_tool_args",
        "save_path",
        "raw_yaml",
        "apply_edit",
    }
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).strip().lower() in forbidden:
                return True
            if _contains_mutation_payload(item):
                return True
        return False
    if isinstance(value, list):
        return any(_contains_mutation_payload(item) for item in value)
    return False


def _default_payload_bytes(result: dict[str, Any]) -> int:
    compact = dict(result)
    compact.pop("dispatch_telemetry", None)
    compact.pop("docs_answer_telemetry", None)
    return len(json.dumps(compact, ensure_ascii=False).encode("utf-8"))


def _topic_terms(topic: str) -> list[str]:
    raw = [token for token in topic.lower().replace("_", " ").split() if token]
    expanded = set(raw)
    synonyms = {
        "pmt": {"polymorphic", "types", "message"},
        "stream": {"sample", "samples", "tag"},
        "tags": {"metadata", "tagged", "length"},
        "grcc": {"compile", "compiler", "validation", "validate"},
        "hier": {"hierarchical", "wrapper", "block"},
        "ports": {"message", "stream", "queue"},
        "throttle": {"rate", "limit", "sample"},
    }
    for token in list(expanded):
        expanded.update(synonyms.get(token, set()))
    return sorted(expanded)


def _is_menu_title(title: str) -> bool:
    lower = title.lower().strip()
    return any(marker in lower for marker in _MENU_MARKERS)


def _extract_top_source(payload: dict[str, Any], *, lexical: bool) -> tuple[str, str, str]:
    rows = payload.get("results")
    if not isinstance(rows, list) or not rows:
        return ("", "", "")
    top = rows[0] if isinstance(rows[0], dict) else {}
    title = str(top.get("title") or "")
    excerpt = str(top.get("excerpt") or "")
    if lexical:
        citation = top.get("citation") if isinstance(top.get("citation"), dict) else {}
        source = str(citation.get("url") or citation.get("path") or "")
    else:
        provenance = top.get("provenance") if isinstance(top.get("provenance"), dict) else {}
        source = str(provenance.get("url") or provenance.get("path") or "")
    return (title, source, excerpt)


def _row_groundedness(answer: str, sources: list[dict[str, str]]) -> bool:
    if not answer or not sources:
        return False
    answer_tokens = [token for token in answer.lower().split() if len(token) > 3]
    if not answer_tokens:
        return False
    best_overlap = 0
    for source in sources:
        text = " ".join(
            [
                str(source.get("title") or "").lower(),
                str(source.get("excerpt") or "").lower(),
            ]
        )
        overlap = sum(1 for token in answer_tokens if token in text)
        best_overlap = max(best_overlap, overlap)
    return best_overlap >= min(3, max(1, len(answer_tokens) // 3))


def _row_relevance(
    *,
    row: EvalRow,
    answer: str,
    insufficient: bool,
    sources: list[dict[str, str]],
) -> bool:
    lower_answer = answer.lower()
    terms = list(row.required_terms) or _topic_terms(row.expected_topic)
    source_blob = " ".join(
        [
            " ".join(str(source.get("title") or "") for source in sources),
            " ".join(str(source.get("excerpt") or "") for source in sources),
        ]
    ).lower()
    term_match = any(term in lower_answer or term in source_blob for term in terms)
    hint = row.required_source_hint.strip().lower()
    hints = [item.lower() for item in row.required_source_hints if item.strip()]
    if hint and hint not in hints:
        hints.append(hint)
    hint_match = True if not hints else any(
        any(
            marker in str(source.get("title") or "").lower()
            or marker in str(source.get("source") or "").lower()
            for marker in hints
        )
        for source in sources
    )
    comparison_shape_ok = True
    if row.answer_type == "comparison":
        comparison_shape_ok = (
            "difference:" in lower_answer
            and ":" in answer
            and lower_answer.count(":") >= 3
        )
        if comparison_shape_ok and row.expected_sides:
            for side in row.expected_sides:
                if side in lower_answer:
                    continue
                side_tokens = [
                    token for token in side.split() if len(token) > 2 and token not in {"the", "and"}
                ]
                if not side_tokens:
                    continue
                token_hits = sum(1 for token in side_tokens if token in lower_answer)
                if token_hits < max(1, min(2, len(side_tokens))):
                    comparison_shape_ok = False
                    break
    block_shape_ok = True
    if row.answer_type == "block_definition":
        lower = answer.lower()
        block_shape_ok = (
            "input port(s)" not in lower
            and "output port(s)" not in lower
            and "parameter(s)" not in lower
        )

    if row.should_have_answer:
        return (
            (not insufficient)
            and term_match
            and hint_match
            and comparison_shape_ok
            and block_shape_ok
        )

    evidence_refusal = "did not contain enough direct evidence" in lower_answer
    return bool(insufficient or evidence_refusal)


def _failure_reason(
    *,
    row: EvalRow,
    relevance_pass: bool,
    insufficient: bool,
    sources: list[dict[str, str]],
    answer: str,
) -> str:
    if relevance_pass:
        return ""
    if (not row.should_have_answer) and (not insufficient):
        return "insufficient_evidence should be true"
    if not sources:
        return "no real evidence"
    top = sources[0]
    title = str(top.get("title") or "")
    excerpt = str(top.get("excerpt") or "")
    if _is_menu_title(title):
        return "menu/index page"
    if "…" in excerpt:
        return "snippet fragment"
    lower_excerpt = excerpt.lower()
    if any(marker in lower_excerpt for marker in ("add the", "connect the", "drag in", "click")):
        return "tutorial step not conceptual answer"
    terms = _topic_terms(row.expected_topic)
    if not any(term in lower_excerpt for term in terms):
        return "source missing exact term"
    lower_answer = answer.lower()
    if "gnu radio is" in lower_answer and row.expected_topic not in {"flowgraph", "what_is_gnu_radio"}:
        return "too generic"
    return "wrong topic"


def _agent_for_eval(
    *,
    helper_mode: str | None,
    helper_max_output_tokens: int | None,
    helper_timeout_seconds: float | None,
    helper_max_snippet_chars: int | None,
    helper_max_total_context_chars: int | None,
    max_sources: int | None,
) -> GrcAgent:
    config = default_app_config()
    docs_cfg = config.agent.docs_answer
    docs_cfg = replace(
        docs_cfg,
        helper_mode=(helper_mode if isinstance(helper_mode, str) else docs_cfg.helper_mode),
        helper_max_output_tokens=(
            helper_max_output_tokens
            if isinstance(helper_max_output_tokens, int)
            else docs_cfg.helper_max_output_tokens
        ),
        helper_timeout_seconds=(
            helper_timeout_seconds
            if isinstance(helper_timeout_seconds, int | float)
            else docs_cfg.helper_timeout_seconds
        ),
        helper_max_snippet_chars=(
            helper_max_snippet_chars
            if isinstance(helper_max_snippet_chars, int)
            else docs_cfg.helper_max_snippet_chars
        ),
        helper_max_total_context_chars=(
            helper_max_total_context_chars
            if isinstance(helper_max_total_context_chars, int)
            else docs_cfg.helper_max_total_context_chars
        ),
        max_sources=(max_sources if isinstance(max_sources, int) else docs_cfg.max_sources),
    )
    agent_cfg = replace(config.agent, docs_answer=docs_cfg)
    fixture = Path("tests/data/random_bit_generator.grc")
    session = FlowgraphSession()
    session.load(fixture)
    return GrcAgent(session, config=agent_cfg)


def run_eval(
    corpus: Path,
    *,
    helper_mode: str | None = None,
    helper_max_output_tokens: int | None = None,
    helper_timeout_seconds: float | None = None,
    helper_max_snippet_chars: int | None = None,
    helper_max_total_context_chars: int | None = None,
    max_sources: int | None = None,
    docs_k: int = 3,
    runs_per_question: int = 1,
) -> dict[str, Any]:
    rows = _load_rows(corpus)
    agent = _agent_for_eval(
        helper_mode=helper_mode,
        helper_max_output_tokens=helper_max_output_tokens,
        helper_timeout_seconds=helper_timeout_seconds,
        helper_max_snippet_chars=helper_max_snippet_chars,
        helper_max_total_context_chars=helper_max_total_context_chars,
        max_sources=max_sources,
    )

    answer_generated = 0
    insufficient = 0
    source_present = 0
    fallback_count = 0
    helper_success_count = 0
    helper_used_count = 0
    advisor_attempted_count = 0
    schema_valid_count = 0
    leakage_count = 0
    latencies: list[int] = []
    answer_lengths: list[int] = []
    output_bytes: list[int] = []
    helper_latencies: list[int] = []
    helper_prompt_chars: list[int] = []
    source_counts: list[int] = []
    cache_hits = 0
    cache_misses = 0
    fallback_reasons: dict[str, int] = {}
    helper_finish_reasons: dict[str, int] = {}
    retrieval_modes: dict[str, int] = {}
    source_quality_distribution: dict[str, int] = {}
    helper_skipped_reason_counts: dict[str, int] = {}
    helper_eligible_count = 0

    relevance_pass_count = 0
    groundedness_pass_count = 0
    misleading_answer_count = 0
    insufficient_correct_count = 0
    menu_index_selected_count = 0
    semantic_retrieval_used_count = 0
    awkward_fallback_count = 0
    port_param_count_as_answer_count = 0
    definition_answer_pass_count = 0
    comparison_answer_pass_count = 0
    block_definition_answer_pass_count = 0
    row_records: list[dict[str, Any]] = []

    samples: list[dict[str, Any]] = []
    for row in rows:
        for _ in range(max(1, runs_per_question)):
            lexical = search_manual(row.question, k=docs_k)
            semantic_manual = semantic_search_grc(row.question, scope="manual", k=docs_k)
            semantic_tutorial = semantic_search_grc(row.question, scope="tutorial", k=docs_k)
            lexical_top_title, lexical_top_source, _lex_excerpt = _extract_top_source(lexical, lexical=True)
            sem_manual_top_title, sem_manual_top_source, _sem_manual_excerpt = _extract_top_source(
                semantic_manual,
                lexical=False,
            )
            sem_tutorial_top_title, sem_tutorial_top_source, _sem_tutorial_excerpt = _extract_top_source(
                semantic_tutorial,
                lexical=False,
            )

            started = time.perf_counter()
            result = agent.execute_tool(
                "ask_grc_docs",
                {"question": row.question, "k": docs_k, "debug": True},
            )
            elapsed = int((time.perf_counter() - started) * 1000)
            latencies.append(elapsed)

            answer = str(result.get("answer") or "")
            if answer:
                answer_generated += 1
            lower_answer = answer.lower()
            if "(excerpt-based fallback)" in lower_answer or "###" in answer:
                awkward_fallback_count += 1
            if any(marker in lower_answer for marker in ("input port(s)", "output port(s)", "parameter(s)")):
                port_param_count_as_answer_count += 1
            answer_lengths.append(len(answer))
            output_bytes.append(_default_payload_bytes(result))

            insufficient_flag = bool(result.get("insufficient_evidence"))
            if insufficient_flag:
                insufficient += 1

            sources = result.get("sources") if isinstance(result.get("sources"), list) else []
            if sources:
                source_present += 1
            source_counts.append(len(sources))

            fallback_used = bool(result.get("fallback_used"))
            if fallback_used:
                fallback_count += 1

            telemetry = result.get("docs_answer_telemetry")
            helper_attempted = False
            helper_success = False
            helper_latency_ms: int | None = None
            helper_prompt_chars_value: int | None = None
            helper_snippet_count: int | None = None
            helper_finish_reason = ""
            helper_failure_reason = ""
            helper_skipped_reason = ""
            source_quality_payload: dict[str, Any] | None = None
            if isinstance(telemetry, dict):
                helper_attempted = bool(telemetry.get("advisor_attempted"))
                helper_success = bool(telemetry.get("advisor_success"))
                helper_skipped_reason = str(telemetry.get("helper_skipped_reason") or "")
                if helper_attempted:
                    advisor_attempted_count += 1
                if helper_success:
                    helper_success_count += 1
                if bool(telemetry.get("schema_valid")):
                    schema_valid_count += 1
                if bool(telemetry.get("cache_hit")):
                    cache_hits += 1
                else:
                    cache_misses += 1
                helper_latency = telemetry.get("helper_latency_ms")
                if isinstance(helper_latency, int):
                    helper_latencies.append(helper_latency)
                    helper_latency_ms = helper_latency
                prompt_chars = telemetry.get("prompt_chars")
                if isinstance(prompt_chars, int):
                    helper_prompt_chars.append(prompt_chars)
                    helper_prompt_chars_value = prompt_chars
                snippet_count = telemetry.get("snippet_count")
                if isinstance(snippet_count, int):
                    helper_snippet_count = snippet_count
                reason = str(telemetry.get("fallback_reason") or "unknown")
                fallback_reasons[reason] = fallback_reasons.get(reason, 0) + 1
                finish_reason = str(telemetry.get("helper_finish_reason") or "unknown")
                helper_finish_reason = finish_reason
                helper_finish_reasons[finish_reason] = (
                    helper_finish_reasons.get(finish_reason, 0) + 1
                )
                source_quality = telemetry.get("source_quality")
                if isinstance(source_quality, dict):
                    source_quality_payload = source_quality
                    label = str(source_quality.get("quality") or "unknown")
                    source_quality_distribution[label] = (
                        source_quality_distribution.get(label, 0) + 1
                    )
                if bool(telemetry.get("helper_eligible")):
                    helper_eligible_count += 1
                skip_reason = str(telemetry.get("helper_skipped_reason") or "unknown")
                helper_skipped_reason_counts[skip_reason] = (
                    helper_skipped_reason_counts.get(skip_reason, 0) + 1
                )
                if helper_attempted:
                    helper_used_count += 1
                    if not helper_success:
                        helper_failure_reason = reason

            mode = str(result.get("retrieval_mode") or "unknown")
            retrieval_modes[mode] = retrieval_modes.get(mode, 0) + 1
            if mode in {
                "lexical_plus_manual_semantic",
                "lexical_plus_tutorial_semantic",
                "lexical_plus_manual_and_tutorial_semantic",
            }:
                semantic_retrieval_used_count += 1

            if _contains_mutation_payload(result):
                leakage_count += 1

            relevance_pass = _row_relevance(
                row=row,
                answer=answer,
                insufficient=insufficient_flag,
                sources=sources,
            )
            if relevance_pass:
                relevance_pass_count += 1
                if row.answer_type == "definition":
                    definition_answer_pass_count += 1
                if row.answer_type == "comparison":
                    comparison_answer_pass_count += 1
                if row.answer_type == "block_definition":
                    block_definition_answer_pass_count += 1

            grounded_pass = _row_groundedness(answer=answer, sources=sources)
            if grounded_pass:
                groundedness_pass_count += 1
            fallback_acceptable = bool(relevance_pass and grounded_pass)

            if row.should_have_answer:
                if (not relevance_pass) and (not insufficient_flag):
                    misleading_answer_count += 1
            else:
                if not insufficient_flag:
                    misleading_answer_count += 1

            if row.should_have_answer:
                insufficient_correct = not insufficient_flag
            else:
                insufficient_correct = insufficient_flag
            if insufficient_correct:
                insufficient_correct_count += 1

            if sources and _is_menu_title(str(sources[0].get("title") or "")):
                menu_index_selected_count += 1

            failure_reason = _failure_reason(
                row=row,
                relevance_pass=relevance_pass,
                insufficient=insufficient_flag,
                sources=sources,
                answer=answer,
            )

            row_records.append(
                {
                    "query": row.question,
                    "expected_topic": row.expected_topic,
                    "expected_source_hint": row.expected_source_hint,
                    "answer_type": row.answer_type,
                    "required_terms": list(row.required_terms),
                    "required_source_hint": row.required_source_hint,
                    "required_source_hints": list(row.required_source_hints),
                    "expected_sides": list(row.expected_sides),
                    "allow_catalog_assist": row.allow_catalog_assist,
                    "should_have_answer": row.should_have_answer,
                    "notes": row.notes,
                    "retrieval_mode": mode,
                    "top_lexical_title": lexical_top_title,
                    "top_lexical_source": lexical_top_source,
                    "top_semantic_manual_title": sem_manual_top_title,
                    "top_semantic_manual_source": sem_manual_top_source,
                    "top_semantic_tutorial_title": sem_tutorial_top_title,
                    "top_semantic_tutorial_source": sem_tutorial_top_source,
                    "selected_final_sources": sources,
                    "answer": answer,
                    "relevance_pass": relevance_pass,
                    "groundedness_pass": grounded_pass,
                    "insufficient_evidence": insufficient_flag,
                    "insufficient_correct": insufficient_correct,
                    "fallback_used": fallback_used,
                    "source_quality": source_quality_payload,
                    "helper_eligible": (
                        bool(telemetry.get("helper_eligible"))
                        if isinstance(telemetry, dict)
                        else False
                    ),
                    "helper_skipped_reason": helper_skipped_reason,
                    "helper_attempted": helper_attempted,
                    "helper_success": helper_success,
                    "helper_failure_reason": helper_failure_reason,
                    "helper_finish_reason": helper_finish_reason,
                    "helper_latency_ms": helper_latency_ms,
                    "helper_prompt_chars": helper_prompt_chars_value,
                    "helper_source_count": helper_snippet_count,
                    "fallback_acceptable": fallback_acceptable,
                    "failure_reason": failure_reason,
                    "audit_notes": "",
                }
            )

            if len(samples) < 5 and not result.get("ok", False):
                samples.append(
                    {
                        "question": row.question,
                        "error_type": result.get("error_type"),
                        "message": result.get("message"),
                    }
                )

    return {
        "rows": len(rows),
        "total_turns": len(rows) * max(1, runs_per_question),
        "docs_k": docs_k,
        "runs_per_question": max(1, runs_per_question),
        "answer_generated": answer_generated,
        "insufficient_evidence": insufficient,
        "source_present": source_present,
        "advisor_attempted_count": advisor_attempted_count,
        "helper_success_count": helper_success_count,
        "helper_used_count": helper_used_count,
        "schema_valid_count": schema_valid_count,
        "fallback_count": fallback_count,
        "cache_hits": cache_hits,
        "cache_misses": cache_misses,
        "fallback_reasons": dict(sorted(fallback_reasons.items())),
        "helper_finish_reasons": dict(sorted(helper_finish_reasons.items())),
        "source_quality_distribution": dict(sorted(source_quality_distribution.items())),
        "helper_skipped_reason_counts": dict(
            sorted(helper_skipped_reason_counts.items())
        ),
        "helper_eligible_count": helper_eligible_count,
        "retrieval_modes": dict(sorted(retrieval_modes.items())),
        "mutation_payload_leakage": leakage_count,
        "latency_p50_ms": int(median(latencies)) if latencies else 0,
        "latency_p95_ms": _percentile(latencies, 0.95),
        "helper_latency_p50_ms": int(median(helper_latencies)) if helper_latencies else 0,
        "helper_latency_p95_ms": _percentile(helper_latencies, 0.95),
        "helper_prompt_chars_p50": int(median(helper_prompt_chars)) if helper_prompt_chars else 0,
        "helper_prompt_chars_p95": _percentile(helper_prompt_chars, 0.95),
        "answer_length_p50_chars": int(median(answer_lengths)) if answer_lengths else 0,
        "answer_length_p95_chars": _percentile(answer_lengths, 0.95),
        "output_bytes_p50": int(median(output_bytes)) if output_bytes else 0,
        "output_bytes_p95": _percentile(output_bytes, 0.95),
        "source_count_p50": int(median(source_counts)) if source_counts else 0,
        "source_count_p95": _percentile(source_counts, 0.95),
        "avg_answer_chars": _avg_answer_chars(rows, agent),
        "failure_samples": samples,
        "helper_max_output_tokens": helper_max_output_tokens,
        "helper_timeout_seconds": helper_timeout_seconds,
        "helper_max_snippet_chars": helper_max_snippet_chars,
        "helper_max_total_context_chars": helper_max_total_context_chars,
        "max_sources": max_sources,
        "answer_relevance_pass_count": relevance_pass_count,
        "groundedness_pass_count": groundedness_pass_count,
        "misleading_answer_count": misleading_answer_count,
        "insufficient_evidence_correct_count": insufficient_correct_count,
        "menu_index_source_selected_count": menu_index_selected_count,
        "semantic_retrieval_used_count": semantic_retrieval_used_count,
        "definition_answer_pass_count": definition_answer_pass_count,
        "comparison_answer_pass_count": comparison_answer_pass_count,
        "block_definition_answer_pass_count": block_definition_answer_pass_count,
        "awkward_fallback_count": awkward_fallback_count,
        "port_param_count_as_answer_count": port_param_count_as_answer_count,
        "row_records": row_records,
    }


def _avg_answer_chars(rows: list[EvalRow], agent: GrcAgent) -> int:
    lengths: list[int] = []
    for row in rows[: min(len(rows), 20)]:
        result = agent.execute_tool("ask_grc_docs", {"question": row.question, "k": 3})
        answer = str(result.get("answer") or "")
        lengths.append(len(answer))
    return int(sum(lengths) / len(lengths)) if lengths else 0


def _helper_triage_recommendation(row: dict[str, Any]) -> str:
    answer_type = str(row.get("answer_type") or "")
    helper_success = bool(row.get("helper_success"))
    fallback_used = bool(row.get("fallback_used"))
    source_quality = row.get("source_quality")
    quality_label = ""
    if isinstance(source_quality, dict):
        quality_label = str(source_quality.get("quality") or "")
    helper_failure = str(row.get("helper_failure_reason") or "")
    fallback_ok = bool(row.get("fallback_acceptable"))
    if quality_label == "weak":
        return "skip helper permanently"
    if answer_type == "block_definition":
        return "use deterministic answer builder instead"
    if helper_success and not fallback_used and fallback_ok:
        return "keep helper"
    if helper_failure in {"timeout", "prompt_too_large"}:
        if answer_type in {"comparison", "procedural_how_to"}:
            return "reduce prompt/source payload"
        return "use deterministic answer builder instead"
    if answer_type in {"definition", "tool_command_concept"} and fallback_ok:
        return "use deterministic answer builder instead"
    return "keep helper"


def write_advisor_report(path: Path, metrics: dict[str, Any], corpus: Path) -> None:
    lines = [
        "# GRC Docs Answer Advisor Report",
        "",
        f"- Corpus: `{corpus}`",
        f"- Total questions: {metrics['rows']}",
        f"- Total turns: {metrics['total_turns']}",
        f"- Docs k: {metrics['docs_k']}",
        f"- Runs per question: {metrics['runs_per_question']}",
        f"- Answer generated count: {metrics['answer_generated']}",
        f"- Insufficient evidence count: {metrics['insufficient_evidence']}",
        f"- Source present count: {metrics['source_present']}",
        f"- Advisor attempted count: {metrics['advisor_attempted_count']}",
        f"- Helper used count: {metrics['helper_used_count']}",
        f"- Helper success count: {metrics['helper_success_count']}",
        f"- Helper schema-valid count: {metrics['schema_valid_count']}",
        f"- Fallback count: {metrics['fallback_count']}",
        f"- Cache hits/misses: {metrics['cache_hits']}/{metrics['cache_misses']}",
        f"- Fallback reasons: `{metrics['fallback_reasons']}`",
        f"- Helper finish reasons: `{metrics['helper_finish_reasons']}`",
        f"- Source quality distribution: `{metrics['source_quality_distribution']}`",
        f"- Helper eligible count: {metrics['helper_eligible_count']}",
        f"- Helper skipped reasons: `{metrics['helper_skipped_reason_counts']}`",
        f"- Retrieval mode distribution: `{metrics['retrieval_modes']}`",
        f"- Answer relevance pass count: {metrics['answer_relevance_pass_count']}",
        f"- Groundedness pass count: {metrics['groundedness_pass_count']}",
        f"- Misleading answer count: {metrics['misleading_answer_count']}",
        f"- Insufficient evidence correctness count: {metrics['insufficient_evidence_correct_count']}",
        f"- Menu/index source selected count: {metrics['menu_index_source_selected_count']}",
        f"- Semantic retrieval used count: {metrics['semantic_retrieval_used_count']}",
        f"- Definition answer pass count: {metrics['definition_answer_pass_count']}",
        f"- Comparison answer pass count: {metrics['comparison_answer_pass_count']}",
        f"- Block-definition answer pass count: {metrics['block_definition_answer_pass_count']}",
        f"- Awkward fallback count: {metrics['awkward_fallback_count']}",
        f"- Port/param-count-as-answer count: {metrics['port_param_count_as_answer_count']}",
        f"- No mutation payload leakage: {metrics['mutation_payload_leakage'] == 0}",
        f"- Average answer chars (sampled): {metrics['avg_answer_chars']}",
        f"- Answer length p50/p95 chars: {metrics['answer_length_p50_chars']}/{metrics['answer_length_p95_chars']}",
        f"- Output bytes p50/p95: {metrics['output_bytes_p50']}/{metrics['output_bytes_p95']}",
        f"- Source count p50/p95: {metrics['source_count_p50']}/{metrics['source_count_p95']}",
        f"- Total latency p50/p95 (ms): {metrics['latency_p50_ms']}/{metrics['latency_p95_ms']}",
        f"- Helper latency p50/p95 (ms): {metrics['helper_latency_p50_ms']}/{metrics['helper_latency_p95_ms']}",
    ]
    samples = metrics.get("failure_samples") or []
    if samples:
        lines.extend(["", "## Failure Samples"])
        for sample in samples:
            lines.append(
                f"- Q: {sample.get('question')} | error_type={sample.get('error_type')} | {sample.get('message')}"
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_retrieval_quality_audit(path: Path, metrics: dict[str, Any], corpus: Path) -> None:
    rows = metrics.get("row_records") or []
    lines = [
        "# GRC Docs Retrieval Quality Audit",
        "",
        f"- Corpus: `{corpus}`",
        f"- Questions audited: {len(rows)}",
        f"- Failure taxonomy: `{_FAILURE_REASONS}`",
        "",
        "## Per-Question Audit",
    ]
    for index, row in enumerate(rows, start=1):
        selected = row.get("selected_final_sources") or []
        selected_text = " | ".join(
            f"{src.get('title')} ({src.get('source')})"
            for src in selected
            if isinstance(src, dict)
        )
        lines.extend(
            [
                "",
                f"### Q{index}: {row.get('query')}",
                f"- Expected topic: `{row.get('expected_topic')}`",
                f"- Answer type: `{row.get('answer_type')}`",
                f"- Required terms: `{row.get('required_terms')}`",
                f"- Required source hint: `{row.get('required_source_hint')}`",
                f"- Allow catalog assist: `{row.get('allow_catalog_assist')}`",
                f"- Top lexical result: {row.get('top_lexical_title')} | {row.get('top_lexical_source')}",
                f"- Top semantic manual result: {row.get('top_semantic_manual_title')} | {row.get('top_semantic_manual_source')}",
                f"- Top semantic tutorial result: {row.get('top_semantic_tutorial_title')} | {row.get('top_semantic_tutorial_source')}",
                f"- Retrieval mode: `{row.get('retrieval_mode')}`",
                f"- Source quality: `{row.get('source_quality')}`",
                f"- Helper eligible: `{row.get('helper_eligible')}`",
                f"- Helper skipped reason: `{row.get('helper_skipped_reason')}`",
                f"- Selected final sources: {selected_text or '<none>'}",
                f"- Answer: {row.get('answer')}",
                f"- Relevance pass: {row.get('relevance_pass')}",
                f"- Groundedness pass: {row.get('groundedness_pass')}",
                f"- Failure reason: {row.get('failure_reason') or '<none>'}",
                f"- Insufficient evidence flag: {row.get('insufficient_evidence')}",
                "- Audit notes: ",
            ]
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_fallback_quality_audit(path: Path, metrics: dict[str, Any]) -> None:
    rows = metrics.get("row_records") or []
    fallback_rows = [row for row in rows if bool(row.get("fallback_used"))]
    lines = [
        "# GRC Docs Fallback Answer Quality Audit",
        "",
        "## Summary",
        f"- fallback rows: {len(fallback_rows)}/{len(rows)}",
        f"- misleading answer count: {metrics.get('misleading_answer_count')}",
        f"- insufficient evidence correctness count: {metrics.get('insufficient_evidence_correct_count')}",
        f"- awkward fallback count: {metrics.get('awkward_fallback_count')}",
        f"- port/param-count-as-answer count: {metrics.get('port_param_count_as_answer_count')}",
    ]
    for index, row in enumerate(fallback_rows, start=1):
        selected = row.get("selected_final_sources") or []
        top = selected[0] if selected and isinstance(selected[0], dict) else {}
        lines.extend(
            [
                "",
                f"### F{index}: {row.get('query')}",
                f"- Retrieval mode: `{row.get('retrieval_mode')}`",
                f"- Top selected source: {top.get('title', '')} | {top.get('source', '')}",
                f"- Answer: {row.get('answer')}",
                f"- Relevance pass: {row.get('relevance_pass')}",
                f"- Groundedness pass: {row.get('groundedness_pass')}",
                f"- Failure reason: {row.get('failure_reason') or '<none>'}",
                f"- Insufficient evidence: {row.get('insufficient_evidence')}",
            ]
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_efficiency_report(path: Path, metrics: dict[str, Any]) -> None:
    lines = [
        "# GRC Docs Answer Efficiency Report",
        "",
        "## Metrics",
        f"- helper used count: {metrics.get('helper_used_count')}",
        f"- helper eligible count: {metrics.get('helper_eligible_count')}",
        f"- helper success count: {metrics.get('helper_success_count')}",
        f"- helper skipped reasons: `{metrics.get('helper_skipped_reason_counts')}`",
        f"- fallback count: {metrics.get('fallback_count')}",
        f"- source quality distribution: `{metrics.get('source_quality_distribution')}`",
        f"- semantic retrieval used count: {metrics.get('semantic_retrieval_used_count')}",
        f"- definition answer pass count: {metrics.get('definition_answer_pass_count')}",
        f"- comparison answer pass count: {metrics.get('comparison_answer_pass_count')}",
        f"- block-definition answer pass count: {metrics.get('block_definition_answer_pass_count')}",
        f"- awkward fallback count: {metrics.get('awkward_fallback_count')}",
        f"- port/param-count-as-answer count: {metrics.get('port_param_count_as_answer_count')}",
        f"- latency p50/p95 ms: {metrics.get('latency_p50_ms')}/{metrics.get('latency_p95_ms')}",
        f"- output bytes p50/p95: {metrics.get('output_bytes_p50')}/{metrics.get('output_bytes_p95')}",
        f"- source count p50/p95: {metrics.get('source_count_p50')}/{metrics.get('source_count_p95')}",
        f"- retrieval modes: `{metrics.get('retrieval_modes')}`",
        f"- mutation leakage count: {metrics.get('mutation_payload_leakage')}",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_helper_triage_report(path: Path, metrics: dict[str, Any]) -> None:
    rows = metrics.get("row_records") or []
    helper_rows = [row for row in rows if bool(row.get("helper_attempted"))]
    by_type: dict[str, int] = {}
    by_failure: dict[str, int] = {}
    by_recommendation: dict[str, int] = {}
    for row in helper_rows:
        answer_type = str(row.get("answer_type") or "unknown")
        by_type[answer_type] = by_type.get(answer_type, 0) + 1
        failure = str(row.get("helper_failure_reason") or "<none>")
        by_failure[failure] = by_failure.get(failure, 0) + 1
        recommendation = _helper_triage_recommendation(row)
        by_recommendation[recommendation] = by_recommendation.get(recommendation, 0) + 1
    lines = [
        "# Docs Helper Triage (2026-05-04)",
        "",
        "## Summary",
        f"- helper attempted rows: {len(helper_rows)}",
        f"- helper success rows: {sum(1 for row in helper_rows if bool(row.get('helper_success')))}",
        f"- helper failure reasons: `{dict(sorted(by_failure.items()))}`",
        f"- answer type distribution: `{dict(sorted(by_type.items()))}`",
        f"- recommendation distribution: `{dict(sorted(by_recommendation.items()))}`",
        "",
        "## Per-Row Triage",
    ]
    for index, row in enumerate(helper_rows, start=1):
        source_quality = row.get("source_quality")
        recommendation = _helper_triage_recommendation(row)
        lines.extend(
            [
                "",
                f"### H{index}: {row.get('query')}",
                f"- answer_type: `{row.get('answer_type')}`",
                f"- helper_eligible_reason: `{row.get('helper_skipped_reason') or '<none>'}`",
                f"- prompt_chars: {row.get('helper_prompt_chars')}",
                f"- source_count: {row.get('helper_source_count')}",
                f"- source_quality: `{source_quality}`",
                f"- helper_success: {row.get('helper_success')}",
                f"- helper_failure_reason: `{row.get('helper_failure_reason') or '<none>'}`",
                f"- helper_latency_ms: {row.get('helper_latency_ms')}",
                f"- fallback_acceptable: {row.get('fallback_acceptable')}",
                f"- recommendation: `{recommendation}`",
            ]
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--report", type=Path, default=DEFAULT_ADVISOR_REPORT)
    parser.add_argument("--retrieval-audit", type=Path, default=DEFAULT_RETRIEVAL_AUDIT)
    parser.add_argument("--fallback-audit", type=Path, default=DEFAULT_FALLBACK_AUDIT)
    parser.add_argument("--efficiency-report", type=Path, default=DEFAULT_EFFICIENCY_REPORT)
    parser.add_argument("--helper-triage-report", type=Path, default=DEFAULT_HELPER_TRIAGE_REPORT)
    parser.add_argument("--helper-mode", type=str, choices=["auto", "always", "never"], default=None)
    parser.add_argument("--helper-max-output-tokens", type=int, default=None)
    parser.add_argument("--helper-timeout-seconds", type=float, default=None)
    parser.add_argument("--helper-max-snippet-chars", type=int, default=None)
    parser.add_argument("--helper-max-total-context-chars", type=int, default=None)
    parser.add_argument("--max-sources", type=int, default=None)
    parser.add_argument("--docs-k", type=int, default=3)
    parser.add_argument("--runs-per-question", type=int, default=1)
    args = parser.parse_args(argv)

    try:
        with acquire_retrieval_eval_lock("grc_docs_answer_eval"):
            metrics = run_eval(
                args.corpus,
                helper_mode=args.helper_mode,
                helper_max_output_tokens=args.helper_max_output_tokens,
                helper_timeout_seconds=args.helper_timeout_seconds,
                helper_max_snippet_chars=args.helper_max_snippet_chars,
                helper_max_total_context_chars=args.helper_max_total_context_chars,
                max_sources=args.max_sources,
                docs_k=args.docs_k,
                runs_per_question=args.runs_per_question,
            )
    except RuntimeError as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error_type": "retrieval_eval_lock_busy",
                    "message": str(exc),
                },
                sort_keys=True,
            )
        )
        return 2
    write_advisor_report(args.report, metrics, args.corpus)
    write_retrieval_quality_audit(args.retrieval_audit, metrics, args.corpus)
    write_fallback_quality_audit(args.fallback_audit, metrics)
    write_efficiency_report(args.efficiency_report, metrics)
    write_helper_triage_report(args.helper_triage_report, metrics)
    print(json.dumps(metrics, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
