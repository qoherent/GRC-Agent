"""Frozen no-LLM regression gate for vector retrieval v1."""

from __future__ import annotations

import json
import sys
from typing import Any

from tests.retrieval_eval.vector_retrieval import run_eval


VECTOR_BASELINE_V1_THRESHOLDS: dict[str, int] = {
    "minimum_vector_top_k_hits": 276,
    "exact_id_miss_count": 0,
    "false_positive_failure_count": 0,
    "source_type_miss_count": 0,
    "minimum_safety_passes": 290,
    "minimum_provenance_passes": 290,
}


def evaluate_vector_regression(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate vector v1 protected metrics without making live model calls."""
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    miss_analysis = (
        payload.get("miss_analysis") if isinstance(payload.get("miss_analysis"), dict) else {}
    )
    failures: list[str] = []

    vector_hits = _int_value(summary.get("vector_top_k_hits"))
    if vector_hits < VECTOR_BASELINE_V1_THRESHOLDS["minimum_vector_top_k_hits"]:
        failures.append(
            "vector_top_k_hits below frozen v1 baseline: "
            f"{vector_hits} < {VECTOR_BASELINE_V1_THRESHOLDS['minimum_vector_top_k_hits']}"
        )

    exact_id_misses = _int_value(miss_analysis.get("exact_id_miss_count"))
    if exact_id_misses != VECTOR_BASELINE_V1_THRESHOLDS["exact_id_miss_count"]:
        failures.append(f"exact_id_miss_count regressed: {exact_id_misses}")

    false_positive_failures = _int_value(miss_analysis.get("false_positive_failure_count"))
    if (
        false_positive_failures
        != VECTOR_BASELINE_V1_THRESHOLDS["false_positive_failure_count"]
    ):
        failures.append(f"false_positive_failure_count regressed: {false_positive_failures}")

    source_type_misses = _int_value(miss_analysis.get("source_type_miss_count"))
    if source_type_misses != VECTOR_BASELINE_V1_THRESHOLDS["source_type_miss_count"]:
        failures.append(f"source_type_miss_count regressed: {source_type_misses}")

    safety_passes = _int_value(summary.get("safety_passes"))
    if safety_passes < VECTOR_BASELINE_V1_THRESHOLDS["minimum_safety_passes"]:
        failures.append(
            "safety_passes below frozen v1 baseline: "
            f"{safety_passes} < {VECTOR_BASELINE_V1_THRESHOLDS['minimum_safety_passes']}"
        )

    provenance_passes = _int_value(summary.get("provenance_passes"))
    if provenance_passes < VECTOR_BASELINE_V1_THRESHOLDS["minimum_provenance_passes"]:
        failures.append(
            "provenance_passes below frozen v1 baseline: "
            f"{provenance_passes} < {VECTOR_BASELINE_V1_THRESHOLDS['minimum_provenance_passes']}"
        )

    if not summary.get("deterministic_rebuild_pass"):
        failures.append("deterministic_rebuild_pass is not true")

    if not payload.get("ok"):
        failures.append("underlying vector retrieval eval returned ok=false")

    return {
        "ok": not failures,
        "thresholds": VECTOR_BASELINE_V1_THRESHOLDS,
        "summary": {
            "total_cases": summary.get("total_cases"),
            "vector_top_k_hits": vector_hits,
            "lexical_top_k_hits": summary.get("lexical_top_k_hits"),
            "safety_passes": safety_passes,
            "provenance_passes": provenance_passes,
            "deterministic_rebuild_pass": summary.get("deterministic_rebuild_pass"),
        },
        "miss_analysis": {
            "vector_miss_count": miss_analysis.get("vector_miss_count"),
            "lexical_win_count": miss_analysis.get("lexical_win_count"),
            "exact_id_miss_count": exact_id_misses,
            "false_positive_failure_count": false_positive_failures,
            "source_type_miss_count": source_type_misses,
        },
        "failures": failures,
    }


def _int_value(value: Any) -> int:
    return value if isinstance(value, int) else 0


def main() -> int:
    report = evaluate_vector_regression(run_eval())
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
