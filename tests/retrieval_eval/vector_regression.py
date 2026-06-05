"""Frozen no-LLM regression gate for vector retrieval v1."""

from __future__ import annotations

import json
import sys
from typing import Any, TextIO

from tests.retrieval_eval.vector_retrieval import run_eval

VECTOR_REGRESSION_PROGRESS_INTERVAL = 25
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
            "safety_passes": safety_passes,
            "provenance_passes": provenance_passes,
            "deterministic_rebuild_pass": summary.get("deterministic_rebuild_pass"),
        },
        "miss_analysis": {
            "vector_miss_count": miss_analysis.get("vector_miss_count"),
            "exact_id_miss_count": exact_id_misses,
            "false_positive_failure_count": false_positive_failures,
            "source_type_miss_count": source_type_misses,
        },
        "failures": failures,
    }


def _int_value(value: Any) -> int:
    return value if isinstance(value, int) else 0


def main(
    *,
    eval_runner: Any = run_eval,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    try:
        report = evaluate_vector_regression(
            eval_runner(
                progress_callback=lambda event: _print_progress_event(event, stderr),
                progress_interval=VECTOR_REGRESSION_PROGRESS_INTERVAL,
            )
        )
    except RuntimeError as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error_type": "retrieval_eval_lock_busy",
                    "message": str(exc),
                },
                indent=2,
                sort_keys=True,
            ),
            file=stdout,
        )
        return 2
    print(json.dumps(report, indent=2, sort_keys=True), file=stdout)
    return 0 if report["ok"] else 1


def _print_progress_event(event: dict[str, Any], stream: TextIO) -> None:
    phase = event.get("phase")
    if phase == "waiting_for_lock":
        message = "waiting for retrieval eval lock"
    elif phase == "start":
        message = f"starting {event.get('total_cases')} retrieval cases"
    elif phase == "case_complete":
        message = (
            f"completed {event.get('completed_cases')}/{event.get('total_cases')} cases "
            f"(last={event.get('case_name')}, latency_ms={event.get('latency_ms')})"
        )
    elif phase == "deterministic_rebuild":
        message = "checking deterministic rebuild"
    elif phase == "done":
        message = (
            f"finished eval loop in {event.get('duration_ms')} ms "
            f"for {event.get('total_cases')} cases"
        )
    else:
        message = f"progress event: {phase}"
    print(f"[vector_regression] {message}", file=stream, flush=True)


if __name__ == "__main__":
    sys.exit(main())
