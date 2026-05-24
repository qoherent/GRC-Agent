"""Research-only implementation audit for DocsAnswerAdvisor helper behavior.

This script does not change production defaults. It runs controlled diagnostics over
representative ask_grc_docs questions and writes a markdown audit report.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
import json
from pathlib import Path
from statistics import median
import time
from typing import Any
from urllib import request

from grc_agent.agent import GrcAgent
from grc_agent.config import default_app_config
from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.runtime.docs_answer_advisor import (
    DocsAnswerSnippet,
    run_docs_answer_advisor_diagnostic,
)
from grc_agent.toolagents_runtime import (
    ToolAgentsJsonClient,
    ToolAgentsLlamaProviderConfig,
)

DEFAULT_CORPUS = Path("tests/data/grc_docs_answer_eval.jsonl")
DEFAULT_REPORT = Path("reports/DOCS_HELPER_IMPLEMENTATION_AUDIT.md")
DEFAULT_FIXTURE = Path("tests/data/random_bit_generator.grc")

TIMEOUT_SWEEP = (3.5, 5.0, 8.0, 10.0)
MAX_TOKENS_SWEEP = (256, 512, 768)
RESPONSE_MODE_SWEEP = ("json_object", "json_schema", "plain_json")
SOURCE_COUNT_SWEEP = (1, 2, 3)


@dataclass(frozen=True)
class EvalRow:
    index: int
    question: str
    expected_topic: str
    answer_type: str
    required_terms: tuple[str, ...]
    expected_sides: tuple[str, ...]
    should_have_answer: bool


def _load_rows(path: Path) -> list[EvalRow]:
    loaded: list[EvalRow] = []
    for idx, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        payload = json.loads(line)
        loaded.append(
            EvalRow(
                index=idx,
                question=str(payload.get("question") or "").strip(),
                expected_topic=str(payload.get("expected_topic") or "").strip(),
                answer_type=str(payload.get("answer_type") or "definition").strip(),
                required_terms=tuple(
                    str(term).strip().lower()
                    for term in payload.get("required_terms", [])
                    if str(term).strip()
                ),
                expected_sides=tuple(
                    str(term).strip().lower()
                    for term in payload.get("expected_sides", [])
                    if str(term).strip()
                ),
                should_have_answer=bool(payload.get("should_have_answer", False)),
            )
        )
    return loaded


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
        "head": {"samples", "count", "stop"},
        "null": {"discard", "drop", "sink"},
    }
    for token in list(expanded):
        expanded.update(synonyms.get(token, set()))
    return sorted(expanded)


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

    comparison_shape_ok = True
    if row.answer_type == "comparison":
        comparison_shape_ok = (
            "difference:" in lower_answer
            and lower_answer.count(":") >= 3
        )
        if comparison_shape_ok and row.expected_sides:
            for side in row.expected_sides:
                if side in lower_answer:
                    continue
                side_tokens = [
                    token
                    for token in side.split()
                    if len(token) > 2 and token not in {"the", "and"}
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
        return (not insufficient) and term_match and comparison_shape_ok and block_shape_ok

    evidence_refusal = "did not contain enough direct evidence" in lower_answer
    return bool(insufficient or evidence_refusal)


def _percentile(values: list[int], pct: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    idx = int(round((len(ordered) - 1) * pct))
    return int(ordered[max(0, min(idx, len(ordered) - 1))])


def _llama_models(base_url: str, timeout_seconds: float) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}/v1/models"
    req = request.Request(url, method="GET")
    with request.urlopen(req, timeout=timeout_seconds) as response:
        return json.loads(response.read().decode("utf-8"))


def _build_agent() -> tuple[GrcAgent, Any]:
    config = default_app_config()
    docs_cfg = replace(config.agent.docs_answer, helper_mode="never")
    agent_cfg = replace(config.agent, docs_answer=docs_cfg)
    session = FlowgraphSession()
    session.load(DEFAULT_FIXTURE)
    return (GrcAgent(session, config=agent_cfg), config.llama)


def _choose_rows(rows: list[EvalRow], selected_indices: list[int]) -> list[EvalRow]:
    by_index = {row.index: row for row in rows}
    selected: list[EvalRow] = []
    for idx in selected_indices:
        row = by_index.get(idx)
        if row is not None:
            selected.append(row)
    return selected


def _to_snippets(sources: list[dict[str, Any]]) -> list[DocsAnswerSnippet]:
    snippets: list[DocsAnswerSnippet] = []
    for source in sources:
        title = " ".join(str(source.get("title") or "").split()).strip()
        origin = " ".join(str(source.get("source") or "").split()).strip()
        excerpt = " ".join(str(source.get("excerpt") or "").split()).strip()
        if title and origin and excerpt:
            snippets.append(DocsAnswerSnippet(title=title, source=origin, excerpt=excerpt))
    return snippets


def _format_mode_metrics(rows: list[dict[str, Any]], key: str) -> str:
    metrics: dict[str, dict[str, int]] = {}
    for row in rows:
        bucket = str(row.get(key) or "")
        if bucket not in metrics:
            metrics[bucket] = {"attempted": 0, "success": 0, "timeout": 0, "malformed": 0}
        metrics[bucket]["attempted"] += 1
        if bool(row.get("ok")):
            metrics[bucket]["success"] += 1
        if str(row.get("error_kind") or "") == "timeout":
            metrics[bucket]["timeout"] += 1
        if str(row.get("error_kind") or "") in {"parse_response", "parse_payload", "validation"}:
            metrics[bucket]["malformed"] += 1

    lines: list[str] = []
    for bucket in sorted(metrics):
        data = metrics[bucket]
        attempted = max(1, data["attempted"])
        success_rate = (100.0 * data["success"]) / attempted
        lines.append(
            f"- `{bucket}`: success {data['success']}/{data['attempted']} ({success_rate:.1f}%), "
            f"timeouts {data['timeout']}, malformed {data['malformed']}"
        )
    return "\n".join(lines)


def run_audit(
    corpus: Path,
    report_path: Path,
    *,
    prompt_indices: list[int],
    sweep_indices: list[int],
    docs_k: int,
) -> None:
    rows = _load_rows(corpus)
    prompt_rows = _choose_rows(rows, prompt_indices)
    sweep_rows = _choose_rows(rows, sweep_indices)
    if not prompt_rows:
        raise RuntimeError("no prompt rows found in corpus")
    if not sweep_rows:
        raise RuntimeError("no sweep rows found in corpus")
    selected_lookup = {row.index: row for row in [*prompt_rows, *sweep_rows]}
    selected_rows = [selected_lookup[idx] for idx in sorted(selected_lookup)]
    prompt_index_set = {row.index for row in prompt_rows}
    sweep_index_set = {row.index for row in sweep_rows}

    agent, llama_cfg = _build_agent()
    llama_models_error = ""
    llama_models_payload: dict[str, Any] = {}
    try:
        llama_models_payload = _llama_models(llama_cfg.server_url, timeout_seconds=3.0)
    except Exception as exc:  # pragma: no cover - depends on local runtime
        llama_models_error = str(exc)

    prompt_samples: list[dict[str, Any]] = []
    trial_rows: list[dict[str, Any]] = []
    deterministic_rows: list[dict[str, Any]] = []

    for row in selected_rows:
        result = agent.execute_tool(
            "ask_grc_docs",
            {"question": row.question, "k": docs_k, "debug": True},
        )
        deterministic_answer = str(result.get("answer") or "")
        deterministic_insufficient = bool(result.get("insufficient_evidence"))
        deterministic_sources = [
            source
            for source in (result.get("sources") if isinstance(result.get("sources"), list) else [])
            if isinstance(source, dict)
        ]
        deterministic_grounded = _row_groundedness(deterministic_answer, deterministic_sources)
        deterministic_relevance = _row_relevance(
            row=row,
            answer=deterministic_answer,
            insufficient=deterministic_insufficient,
            sources=deterministic_sources,
        )

        telemetry = result.get("docs_answer_telemetry")
        deterministic_rows.append(
            {
                "index": row.index,
                "question": row.question,
                "answer_type": row.answer_type,
                "answer": deterministic_answer,
                "insufficient_evidence": deterministic_insufficient,
                "grounded": deterministic_grounded,
                "relevance": deterministic_relevance,
                "source_count": len(deterministic_sources),
                "retrieval_mode": str(result.get("retrieval_mode") or ""),
                "source_quality": (
                    dict(telemetry.get("source_quality"))
                    if isinstance(telemetry, dict) and isinstance(telemetry.get("source_quality"), dict)
                    else {}
                ),
            }
        )

        snippets = _to_snippets(deterministic_sources)
        if not snippets:
            continue

        if row.index in prompt_index_set:
            sample_source_count = min(2, len(snippets))
            sample_client = ToolAgentsJsonClient(
                ToolAgentsLlamaProviderConfig(
                base_url=llama_cfg.server_url,
                    model=llama_cfg.model,
                timeout_seconds=5.0,
                max_tokens=512,
                temperature=0.0,
                )
            )
            sample_diag = run_docs_answer_advisor_diagnostic(
                client=sample_client,
                model=llama_cfg.model,
                question=row.question,
                answer_type=row.answer_type,
                snippets=snippets[:sample_source_count],
                max_sources=min(3, sample_source_count),
                response_mode="json_object",
            )
            prompt_samples.append(
                {
                    "index": row.index,
                    "question": row.question,
                    "answer_type": row.answer_type,
                    "prompt_chars": int(sample_diag.get("prompt_chars") or 0),
                    "messages": sample_diag.get("messages") if isinstance(sample_diag.get("messages"), list) else [],
                }
            )

        if row.index not in sweep_index_set:
            continue

        usable_source_counts = [count for count in SOURCE_COUNT_SWEEP if count <= len(snippets)]
        if not usable_source_counts:
            usable_source_counts = [1]

        for timeout_seconds in TIMEOUT_SWEEP:
            for max_tokens in MAX_TOKENS_SWEEP:
                for response_mode in RESPONSE_MODE_SWEEP:
                    for source_count in usable_source_counts:
                        subset = snippets[:source_count]
                        client = ToolAgentsJsonClient(
                            ToolAgentsLlamaProviderConfig(
                            base_url=llama_cfg.server_url,
                                model=llama_cfg.model,
                            timeout_seconds=timeout_seconds,
                            max_tokens=max_tokens,
                            temperature=0.0,
                            )
                        )
                        started = time.perf_counter()
                        diag = run_docs_answer_advisor_diagnostic(
                            client=client,
                            model=llama_cfg.model,
                            question=row.question,
                            answer_type=row.answer_type,
                            snippets=subset,
                            max_sources=min(3, source_count),
                            response_mode=response_mode,
                        )
                        wall_ms = int((time.perf_counter() - started) * 1000)

                        helper_answer = ""
                        helper_insufficient = True
                        helper_sources: list[dict[str, str]] = []
                        if bool(diag.get("ok")) and isinstance(diag.get("result"), dict):
                            helper_result = dict(diag.get("result") or {})
                            helper_answer = str(helper_result.get("answer") or "")
                            helper_insufficient = bool(helper_result.get("insufficient_evidence"))
                            source_indexes = helper_result.get("source_indexes")
                            if isinstance(source_indexes, list):
                                for index in source_indexes:
                                    if not isinstance(index, int):
                                        continue
                                    if index < 0 or index >= len(subset):
                                        continue
                                    snippet = subset[index]
                                    helper_sources.append(
                                        {
                                            "title": snippet.title,
                                            "source": snippet.source,
                                            "excerpt": snippet.excerpt,
                                        }
                                    )
                        helper_grounded = _row_groundedness(helper_answer, helper_sources)
                        helper_relevance = _row_relevance(
                            row=row,
                            answer=helper_answer,
                            insufficient=helper_insufficient,
                            sources=helper_sources,
                        )
                        helper_added_value = (
                            helper_relevance and not deterministic_relevance
                        ) or (
                            helper_relevance == deterministic_relevance
                            and helper_grounded
                            and not deterministic_grounded
                        )
                        helper_worse = (
                            deterministic_relevance and not helper_relevance
                        ) or (
                            deterministic_grounded and not helper_grounded
                        )
                        hallucinated = bool(
                            helper_answer
                            and (not helper_insufficient)
                            and (not helper_grounded)
                        )

                        trial_rows.append(
                            {
                                "question_index": row.index,
                                "question": row.question,
                                "answer_type": row.answer_type,
                                "timeout_seconds": timeout_seconds,
                                "max_tokens": max_tokens,
                                "response_mode": response_mode,
                                "source_count": source_count,
                                "subset_source_count": len(subset),
                                "ok": bool(diag.get("ok")),
                                "error_kind": str(diag.get("error_kind") or ""),
                                "error_message": str(diag.get("error_message") or ""),
                                "finish_reason": str(diag.get("finish_reason") or ""),
                                "phase_ms": dict(diag.get("phase_ms") or {}),
                                "wall_ms": wall_ms,
                                "deterministic_answer": deterministic_answer,
                                "deterministic_insufficient_evidence": deterministic_insufficient,
                                "helper_answer": helper_answer,
                                "helper_insufficient_evidence": helper_insufficient,
                                "helper_relevance": helper_relevance,
                                "helper_grounded": helper_grounded,
                                "deterministic_relevance": deterministic_relevance,
                                "deterministic_grounded": deterministic_grounded,
                                "helper_added_value": helper_added_value,
                                "helper_worse": helper_worse,
                                "hallucinated_or_unsupported": hallucinated,
                                "raw_model_output": str(diag.get("raw_model_output") or ""),
                                "raw_response_text": str(diag.get("raw_response_text") or ""),
                            }
                        )

    attempted = len(trial_rows)
    success_rows = [row for row in trial_rows if bool(row.get("ok"))]
    timeout_rows = [row for row in trial_rows if str(row.get("error_kind")) == "timeout"]
    malformed_rows = [
        row
        for row in trial_rows
        if str(row.get("error_kind")) in {"parse_response", "parse_payload", "validation"}
    ]
    transport_rows = [row for row in trial_rows if str(row.get("error_kind")) == "transport"]

    total_phase = [int((row.get("phase_ms") or {}).get("total") or 0) for row in trial_rows]
    output_valid_count = sum(
        1
        for row in success_rows
        if bool(str(row.get("helper_answer") or "").strip())
    )
    helper_added_value_count = sum(1 for row in trial_rows if bool(row.get("helper_added_value")))
    helper_worse_count = sum(1 for row in trial_rows if bool(row.get("helper_worse")))
    hallucinated_count = sum(1 for row in trial_rows if bool(row.get("hallucinated_or_unsupported")))

    per_question_best: list[dict[str, Any]] = []
    by_qidx: dict[int, list[dict[str, Any]]] = {}
    for row in trial_rows:
        by_qidx.setdefault(int(row.get("question_index") or 0), []).append(row)
    for det in deterministic_rows:
        qidx = int(det["index"])
        options = by_qidx.get(qidx, [])
        best = None
        for row in options:
            if not bool(row.get("ok")):
                continue
            if not bool(row.get("helper_relevance")):
                continue
            if best is None:
                best = row
                continue
            prev_grounded = bool(best.get("helper_grounded"))
            new_grounded = bool(row.get("helper_grounded"))
            if new_grounded and not prev_grounded:
                best = row
                continue
            prev_phase = int((best.get("phase_ms") or {}).get("total") or 10**9)
            new_phase = int((row.get("phase_ms") or {}).get("total") or 10**9)
            if new_phase < prev_phase:
                best = row
        per_question_best.append(
            {
                "index": det["index"],
                "question": det["question"],
                "answer_type": det["answer_type"],
                "deterministic_answer": det["answer"],
                "deterministic_relevance": det["relevance"],
                "deterministic_grounded": det["grounded"],
                "helper_best_answer": "" if best is None else str(best.get("helper_answer") or ""),
                "helper_best_relevance": False if best is None else bool(best.get("helper_relevance")),
                "helper_best_grounded": False if best is None else bool(best.get("helper_grounded")),
                "helper_best_mode": "" if best is None else str(best.get("response_mode") or ""),
                "helper_best_timeout": 0.0 if best is None else float(best.get("timeout_seconds") or 0.0),
                "helper_best_max_tokens": 0 if best is None else int(best.get("max_tokens") or 0),
                "helper_best_source_count": 0 if best is None else int(best.get("source_count") or 0),
                "helper_added_value": False if best is None else bool(best.get("helper_added_value")),
                "helper_hallucinated": False if best is None else bool(best.get("hallucinated_or_unsupported")),
            }
        )

    failed_samples = [
        row
        for row in trial_rows
        if not bool(row.get("ok"))
    ][:8]

    model_ids: list[str] = []
    if isinstance(llama_models_payload, dict):
        data = llama_models_payload.get("data")
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    model_id = str(item.get("id") or "").strip()
                    if model_id:
                        model_ids.append(model_id)

    success_rate = (100.0 * len(success_rows) / attempted) if attempted else 0.0
    output_valid_rate = (100.0 * output_valid_count / max(1, attempted))

    lines: list[str] = []
    lines.append("# Docs Helper Implementation Audit")
    lines.append("")
    lines.append("## Scope")
    lines.append("- This is a research-only helper audit. Production beta defaults were not changed.")
    lines.append("- `helper_mode=never` remains the runtime default.")
    lines.append("- Sweep grid: timeout `{3.5,5,8,10}` x max_tokens `{256,512,768}` x response_mode `{json_object,json_schema,plain_json}` x source_count `{1,2,3}`.")
    lines.append(f"- Prompt-sample questions: {[row.index for row in prompt_rows]}")
    lines.append(f"- Full sweep questions: {[row.index for row in sweep_rows]}")
    lines.append("")

    lines.append("## Transport And Config Verification")
    lines.append(f"- llama server URL (config): `{llama_cfg.server_url}`")
    lines.append(f"- helper model alias (config): `{llama_cfg.model}`")
    lines.append("- helper temperature: `0.0` (forced in helper client construction)")
    lines.append("- helper max tokens: configured per run and swept in this audit")
    lines.append("- timeout behavior: enforced on HTTP generation request (`urlopen(..., timeout=...)`), while prompt build/parsing/validation are measured separately")
    lines.append("- startup overhead: helper path probes/reuses active server endpoint; it does not start a new llama server process")
    if llama_models_error:
        lines.append(f"- `/v1/models` probe: failed (`{llama_models_error}`)")
    else:
        model_match = llama_cfg.model in model_ids
        lines.append(f"- `/v1/models` probe: success; model ids include configured alias: `{model_match}`")
        lines.append(f"- reported model ids: `{model_ids}`")
    lines.append("")

    lines.append("## Prompt And Schema Audit")
    lines.append("- Diagnostic helper output contract used in this audit:")
    lines.append("```json")
    lines.append('{"answer":"...","source_indexes":[0],"insufficient_evidence":false}')
    lines.append("```")
    lines.append("- Helper does not regenerate source excerpts; Python maps `source_indexes` back to selected snippets.")
    lines.append("- Schema retry was not used in this diagnostic path (single-shot per mode/setting).")
    lines.append("")
    lines.append("### Exact Prompt Samples (5)")
    for sample in prompt_samples[:5]:
        lines.append(f"#### Q{sample['index']}: {sample['question']}")
        lines.append(f"- answer_type: `{sample['answer_type']}`")
        lines.append(f"- prompt_chars: `{sample['prompt_chars']}`")
        messages = sample.get("messages") if isinstance(sample.get("messages"), list) else []
        for m_idx, message in enumerate(messages):
            role = str(message.get("role") or "") if isinstance(message, dict) else ""
            content = str(message.get("content") or "") if isinstance(message, dict) else ""
            lines.append(f"- message[{m_idx}] role=`{role}`")
            lines.append("```text")
            lines.append(content)
            lines.append("```")
        lines.append("")

    lines.append("## Sweep Results")
    lines.append(f"- total attempts: {attempted}")
    lines.append(f"- success rate: {len(success_rows)}/{attempted} ({success_rate:.1f}%)")
    lines.append(f"- timeout count: {len(timeout_rows)}")
    lines.append(f"- malformed count (parse/validation): {len(malformed_rows)}")
    lines.append(f"- transport count: {len(transport_rows)}")
    lines.append(f"- output validity: {output_valid_count}/{attempted} ({output_valid_rate:.1f}%)")
    lines.append(f"- latency p50/p95 total phase (ms): {int(median(total_phase)) if total_phase else 0}/{_percentile(total_phase, 0.95)}")
    lines.append("")

    lines.append("### By Response Mode")
    lines.append(_format_mode_metrics(trial_rows, "response_mode"))
    lines.append("")

    lines.append("### By Timeout")
    lines.append(_format_mode_metrics(trial_rows, "timeout_seconds"))
    lines.append("")

    lines.append("### By Max Tokens")
    lines.append(_format_mode_metrics(trial_rows, "max_tokens"))
    lines.append("")

    lines.append("### By Source Count")
    lines.append(_format_mode_metrics(trial_rows, "source_count"))
    lines.append("")

    lines.append("## Failure Diagnostics")
    lines.append("- Parse errors are tracked separately from timeout/transport errors via `error_kind`.")
    lines.append("- Sample failed raw outputs:")
    for idx, row in enumerate(failed_samples, start=1):
        lines.append(f"### Failure Sample {idx}")
        lines.append(f"- question: `{row['question']}`")
        lines.append(f"- mode/timeout/max_tokens/source_count: `{row['response_mode']}` / `{row['timeout_seconds']}` / `{row['max_tokens']}` / `{row['source_count']}`")
        lines.append(f"- error_kind: `{row['error_kind']}`")
        lines.append(f"- finish_reason: `{row['finish_reason']}`")
        lines.append(f"- error_message: `{row['error_message']}`")
        raw_model_output = str(row.get("raw_model_output") or "")[:1200]
        raw_response_text = str(row.get("raw_response_text") or "")[:1200]
        if raw_model_output:
            lines.append("- raw_model_output (truncated):")
            lines.append("```text")
            lines.append(raw_model_output)
            lines.append("```")
        elif raw_response_text:
            lines.append("- raw_response_text (truncated):")
            lines.append("```text")
            lines.append(raw_response_text)
            lines.append("```")
        lines.append("")

    lines.append("## Deterministic vs Helper Comparison")
    lines.append(f"- helper added value rows: {helper_added_value_count}")
    lines.append(f"- helper worse rows: {helper_worse_count}")
    lines.append(f"- helper hallucinated/unsupported rows: {hallucinated_count}")
    lines.append("")
    for row in per_question_best:
        lines.append(f"### Q{row['index']}: {row['question']}")
        lines.append(f"- answer_type: `{row['answer_type']}`")
        lines.append(f"- deterministic relevance/grounded: `{row['deterministic_relevance']}` / `{row['deterministic_grounded']}`")
        lines.append(f"- deterministic answer: {row['deterministic_answer']}")
        if row["helper_best_answer"]:
            lines.append(
                f"- helper best relevance/grounded: `{row['helper_best_relevance']}` / `{row['helper_best_grounded']}`"
            )
            lines.append(
                f"- helper best setting: mode=`{row['helper_best_mode']}`, timeout=`{row['helper_best_timeout']}`, max_tokens=`{row['helper_best_max_tokens']}`, source_count=`{row['helper_best_source_count']}`"
            )
            lines.append(f"- helper best answer: {row['helper_best_answer']}")
            lines.append(f"- helper added value: `{row['helper_added_value']}`")
            lines.append(f"- helper hallucinated: `{row['helper_hallucinated']}`")
        else:
            lines.append("- helper best answer: <none; no successful relevant helper output>")
        lines.append("")

    recommendation = "keep helper disabled for beta"
    if helper_added_value_count > helper_worse_count and len(success_rows) > 0:
        recommendation = "helper idea is useful; implementation was wrong"
    if helper_added_value_count > 0 and _percentile(total_phase, 0.95) >= 7000:
        recommendation = "helper works but only with higher latency"
    if helper_added_value_count == 0 and len(success_rows) > 0:
        recommendation = "helper does not beat deterministic on this model"
    if len(success_rows) == 0 and len(timeout_rows) == attempted and attempted > 0:
        recommendation = "helper needs a different local model"

    lines.append("## Final Recommendation")
    lines.append(f"- {recommendation}")
    lines.append("- keep helper disabled for beta")

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    details_path = report_path.with_suffix(".json")
    details_path.write_text(
        json.dumps(
            {
                "selected_rows": [row.__dict__ for row in selected_rows],
                "deterministic_rows": deterministic_rows,
                "trials": trial_rows,
                "prompt_samples": prompt_samples,
                "llama_models_payload": llama_models_payload,
                "llama_models_error": llama_models_error,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--docs-k", type=int, default=3)
    parser.add_argument(
        "--prompt-rows",
        type=str,
        default="1,4,5,8,35",
        help="1-based comma-separated row indices used for exact prompt capture",
    )
    parser.add_argument(
        "--sweep-rows",
        type=str,
        default="1,4,5",
        help="1-based comma-separated row indices used for full timeout/token/mode/source sweeps",
    )
    args = parser.parse_args()

    prompt_indices = [
        int(item.strip())
        for item in str(args.prompt_rows).split(",")
        if item.strip()
    ]
    sweep_indices = [
        int(item.strip())
        for item in str(args.sweep_rows).split(",")
        if item.strip()
    ]
    run_audit(
        corpus=args.corpus,
        report_path=args.report,
        prompt_indices=prompt_indices,
        sweep_indices=sweep_indices,
        docs_k=max(1, int(args.docs_k)),
    )


if __name__ == "__main__":
    main()
