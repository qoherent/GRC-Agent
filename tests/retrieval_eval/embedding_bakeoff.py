"""Offline embedding-model bakeoff for read-only vector retrieval.

This module intentionally does not change runtime configuration. It builds
temporary local Qdrant indexes and compares retrieval metrics on the frozen eval
set so model changes can be justified before any runtime switch.
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from grc_agent.retrieval.vector import (
    DEFAULT_EMBEDDING_MODEL,
    build_vector_index,
    semantic_search_grc,
)

from .vector_retrieval import EVAL_CASES, RetrievalEvalCase, run_eval

BAKEOFF_MODELS: tuple[str, ...] = (
    DEFAULT_EMBEDDING_MODEL,
    "snowflake/snowflake-arctic-embed-xs",
    "snowflake/snowflake-arctic-embed-s",
    "jinaai/jina-embeddings-v2-small-en",
    "sentence-transformers/all-MiniLM-L6-v2",
    "nomic-ai/nomic-embed-text-v1.5-Q",
)
DECISION_RULES: dict[str, Any] = {
    "minimum_vector_top_k_hits": 266,
    "exact_id_miss_count": 0,
    "false_positive_failure_count": 0,
    "source_type_miss_count": 0,
    "max_mean_latency_ms": 750.0,
    "runtime_change_allowed": False,
}


def empty_bakeoff_report() -> dict[str, Any]:
    """Return the stable top-level shape for embedding bakeoff reports."""
    return {
        "ok": True,
        "runtime_changed": False,
        "models": list(BAKEOFF_MODELS),
        "decision_rules": DECISION_RULES,
        "case_count": 0,
        "results": [],
        "decision": {
            "recommended_runtime_change": False,
            "candidate_for_future_runtime_milestone": None,
            "reasons": [],
        },
    }


def run_bakeoff(
    *,
    models: tuple[str, ...] = BAKEOFF_MODELS,
    eval_cases: tuple[RetrievalEvalCase, ...] = EVAL_CASES,
) -> dict[str, Any]:
    """Run an offline model comparison using temporary local Qdrant indexes."""
    started = time.perf_counter()
    results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="grc-agent-embedding-bakeoff-") as tmpdir:
        root = Path(tmpdir)
        for model_name in models:
            index_dir = root / _safe_model_dir(model_name) / "qdrant"
            build_started = time.perf_counter()
            build_payload = build_vector_index(
                index_dir=index_dir,
                embedding_model=model_name,
            )
            build_ms = round((time.perf_counter() - build_started) * 1000, 3)

            def search_fn(query: str, *, scope: str = "all", k: int = 5) -> dict[str, Any]:
                return semantic_search_grc(
                    query,
                    scope=scope,
                    k=k,
                    index_dir=index_dir,
                    embedding_model=model_name,
                )

            eval_payload = run_eval(eval_cases=eval_cases, vector_search_fn=search_fn)
            miss_analysis = eval_payload["miss_analysis"]
            results.append(
                {
                    "model": model_name,
                    "runtime_changed": False,
                    "build_ms": build_ms,
                    "embedding_size": build_payload.get("embedding_size"),
                    "record_count": build_payload.get("record_count"),
                    "summary": {
                        **eval_payload["summary"],
                        "mean_latency_ms": _mean_latency_ms(eval_payload["cases"]),
                        "vector_miss_count": miss_analysis["vector_miss_count"],
                        "exact_id_miss_count": miss_analysis["exact_id_miss_count"],
                        "false_positive_failure_count": miss_analysis["false_positive_failure_count"],
                        "source_type_miss_count": miss_analysis["source_type_miss_count"],
                    },
                    "miss_analysis": miss_analysis,
                }
            )
    decision = _decision_summary(results)
    return {
        "ok": all(
            result["summary"]["safety_passes"] == result["summary"]["total_cases"]
            and result["summary"]["provenance_passes"] == result["summary"]["total_cases"]
            for result in results
        ),
        "runtime_changed": False,
        "models": list(models),
        "decision_rules": DECISION_RULES,
        "case_count": len(eval_cases),
        "duration_ms": round((time.perf_counter() - started) * 1000, 3),
        "results": results,
        "decision": decision,
    }


def _decision_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize bakeoff outcome without authorizing a runtime change."""
    if not results:
        return {
            "recommended_runtime_change": False,
            "candidate_for_future_runtime_milestone": None,
            "reasons": ["No bakeoff results were provided."],
        }
    baseline = results[0]
    baseline_summary = baseline["summary"]
    candidates: list[dict[str, Any]] = []
    reasons = ["This bakeoff is eval-only; runtime embedding configuration is unchanged."]
    for result in results[1:]:
        summary = result["summary"]
        exact_regression = summary["exact_id_miss_count"] != DECISION_RULES["exact_id_miss_count"]
        false_positive_regression = summary["false_positive_failure_count"] != DECISION_RULES[
            "false_positive_failure_count"
        ]
        source_type_regression = summary["source_type_miss_count"] != DECISION_RULES[
            "source_type_miss_count"
        ]
        latency_regression = summary["mean_latency_ms"] > DECISION_RULES["max_mean_latency_ms"]
        hit_threshold_pass = (
            summary["vector_top_k_hits"] >= DECISION_RULES["minimum_vector_top_k_hits"]
        )
        hit_gain = summary["vector_top_k_hits"] - baseline_summary["vector_top_k_hits"]
        if exact_regression:
            reasons.append(f"{result['model']} fails the exact-id protected metric.")
        if false_positive_regression:
            reasons.append(f"{result['model']} fails the false-positive protected metric.")
        if source_type_regression:
            reasons.append(f"{result['model']} fails the source-type protected metric.")
        if latency_regression:
            reasons.append(f"{result['model']} exceeds the latency budget.")
        if not hit_threshold_pass:
            reasons.append(
                f"{result['model']} does not beat the baseline hit threshold "
                f"({summary['vector_top_k_hits']} < {DECISION_RULES['minimum_vector_top_k_hits']})."
            )
        if (
            hit_threshold_pass
            and not exact_regression
            and not false_positive_regression
            and not source_type_regression
            and not latency_regression
        ):
            candidates.append({"model": result["model"], "hit_gain": hit_gain})
    best_candidate = max(candidates, key=lambda item: item["hit_gain"], default=None)
    if best_candidate is None:
        reasons.append("No candidate improved retrieval without protected-metric regression.")
    else:
        reasons.append(
            f"{best_candidate['model']} is a future-runtime candidate with "
            f"+{best_candidate['hit_gain']} vector hits."
        )
    return {
        "recommended_runtime_change": False,
        "candidate_for_future_runtime_milestone": best_candidate["model"]
        if best_candidate
        else None,
        "reasons": reasons,
    }


def _safe_model_dir(model_name: str) -> str:
    return model_name.replace("/", "__").replace(":", "_")


def _mean_latency_ms(cases: list[dict[str, Any]]) -> float:
    latencies = [
        float(case["latency_ms"])
        for case in cases
        if isinstance(case.get("latency_ms"), int | float)
    ]
    if not latencies:
        return 0.0
    return round(sum(latencies) / len(latencies), 3)


if __name__ == "__main__":
    report = run_bakeoff()
    print(json.dumps(report, indent=2, sort_keys=True))
    sys.exit(0 if report["ok"] else 1)
