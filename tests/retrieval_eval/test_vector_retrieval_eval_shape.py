"""Tests for vector retrieval eval result dimensions."""

import json
from pathlib import Path
import unittest

from tests.retrieval_eval.vector_retrieval import (
    EVAL_CASES,
    _build_miss_analysis,
    empty_eval_summary,
)
from tests.retrieval_eval.vector_regression import evaluate_vector_regression


class VectorRetrievalEvalShapeTests(unittest.TestCase):
    def test_eval_summary_exposes_required_dimensions(self) -> None:
        summary = empty_eval_summary()

        self.assertEqual(
            set(summary["dimensions"]),
            {
                "lexical_top_k_hit",
                "vector_top_k_hit",
                "catalog_metadata_hit",
                "manual_hit",
                "tutorial_hit",
                "semantic_paraphrase_hit",
                "exact_id_hit",
                "false_positive_pass",
                "provenance_pass",
                "safety_pass",
                "latency_ms",
                "deterministic_rebuild_pass",
            },
        )
        self.assertIn("miss_analysis", summary)
        self.assertEqual(summary["miss_analysis"], {})

    def test_miss_analysis_includes_triage_context(self) -> None:
        analysis = _build_miss_analysis(
            [
                {
                    "name": "semantic_miss",
                    "case_type": "semantic_paraphrase",
                    "query": "stabilize volume",
                    "scope": "catalog",
                    "expected_block_ids": ["analog_agc_xx"],
                    "lexical_top_k_hit": False,
                    "vector_top_k_hit": False,
                    "top_vector_results": [
                        {"id": "audio_sink", "title": "Audio Sink", "source_type": "catalog_block"}
                    ],
                    "top_lexical_results": [],
                },
                {
                    "name": "lexical_win",
                    "case_type": "semantic_paraphrase",
                    "query": "frequency sink",
                    "scope": "catalog",
                    "expected_block_ids": ["qtgui_freq_sink_x"],
                    "lexical_top_k_hit": True,
                    "vector_top_k_hit": False,
                    "top_vector_results": [{"id": "audio_sink", "title": "Audio Sink"}],
                    "top_lexical_results": [{"id": "qtgui_freq_sink_x", "label": "Frequency Sink"}],
                },
            ]
        )

        self.assertEqual(analysis["vector_miss_count"], 2)
        self.assertEqual(analysis["lexical_win_count"], 1)
        first = analysis["vector_misses"][0]
        self.assertEqual(first["expected_block_ids"], ["analog_agc_xx"])
        self.assertEqual(first["top_vector_results"][0]["id"], "audio_sink")
        self.assertEqual(
            analysis["lexical_wins_over_vector"][0]["top_lexical_results"][0]["id"],
            "qtgui_freq_sink_x",
        )

    def test_retrieval_eval_has_expanded_case_count(self) -> None:
        self.assertGreaterEqual(len(EVAL_CASES), 290)
        case_types = {case.case_type for case in EVAL_CASES}
        self.assertIn("semantic_paraphrase", case_types)
        self.assertIn("exact_id", case_types)
        self.assertIn("manual", case_types)
        self.assertIn("false_positive", case_types)

    def test_governed_metadata_baseline_report_is_frozen(self) -> None:
        report_path = (
            Path(__file__).resolve().parents[2]
            / "reports"
            / "retrieval"
            / "vector_eval_governed_metadata.json"
        )

        payload = json.loads(report_path.read_text(encoding="utf-8"))
        summary = payload["summary"]
        miss_analysis = payload["miss_analysis"]

        self.assertTrue(payload["ok"], payload)
        self.assertEqual(summary["total_cases"], 290)
        self.assertEqual(summary["vector_top_k_hits"], 276)
        self.assertEqual(summary["lexical_top_k_hits"], 168)
        self.assertEqual(miss_analysis["exact_id_miss_count"], 0)
        self.assertEqual(miss_analysis["false_positive_failure_count"], 0)
        self.assertEqual(miss_analysis["source_type_miss_count"], 0)

    def test_vector_regression_requires_protected_metrics(self) -> None:
        payload = {
            "ok": True,
            "summary": {
                "total_cases": 290,
                "vector_top_k_hits": 276,
                "lexical_top_k_hits": 168,
                "safety_passes": 290,
                "provenance_passes": 290,
                "deterministic_rebuild_pass": True,
            },
            "miss_analysis": {
                "vector_miss_count": 14,
                "lexical_win_count": 5,
                "exact_id_miss_count": 0,
                "false_positive_failure_count": 0,
                "source_type_miss_count": 0,
            },
        }

        report = evaluate_vector_regression(payload)

        self.assertTrue(report["ok"], report)
        self.assertEqual(report["summary"]["vector_top_k_hits"], 276)

    def test_vector_regression_rejects_protected_metric_regression(self) -> None:
        payload = {
            "ok": True,
            "summary": {
                "vector_top_k_hits": 275,
                "safety_passes": 290,
                "provenance_passes": 290,
                "deterministic_rebuild_pass": True,
            },
            "miss_analysis": {
                "exact_id_miss_count": 1,
                "false_positive_failure_count": 0,
                "source_type_miss_count": 0,
            },
        }

        report = evaluate_vector_regression(payload)

        self.assertFalse(report["ok"], report)
        self.assertGreaterEqual(len(report["failures"]), 2)


if __name__ == "__main__":
    unittest.main()
