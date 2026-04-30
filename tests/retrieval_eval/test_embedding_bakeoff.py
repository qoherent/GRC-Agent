"""Tests for offline embedding model bakeoff reporting."""

import unittest

from tests.retrieval_eval.embedding_bakeoff import (
    BAKEOFF_MODELS,
    DECISION_RULES,
    _decision_summary,
    empty_bakeoff_report,
)


class EmbeddingBakeoffTests(unittest.TestCase):
    def test_bakeoff_models_are_eval_only(self) -> None:
        self.assertEqual(
            BAKEOFF_MODELS,
            (
                "BAAI/bge-small-en-v1.5",
                "snowflake/snowflake-arctic-embed-xs",
                "snowflake/snowflake-arctic-embed-s",
                "jinaai/jina-embeddings-v2-small-en",
                "sentence-transformers/all-MiniLM-L6-v2",
                "nomic-ai/nomic-embed-text-v1.5-Q",
            ),
        )

    def test_empty_bakeoff_report_shape(self) -> None:
        report = empty_bakeoff_report()

        self.assertTrue(report["ok"])
        self.assertEqual(report["runtime_changed"], False)
        self.assertEqual(report["models"], list(BAKEOFF_MODELS))
        self.assertEqual(report["decision_rules"], DECISION_RULES)
        self.assertEqual(report["results"], [])
        self.assertIn("decision", report)

    def test_decision_summary_does_not_recommend_on_protected_regression(self) -> None:
        decision = _decision_summary(
            [
                {
                    "model": "BAAI/bge-small-en-v1.5",
                    "summary": {
                        "vector_top_k_hits": 100,
                        "source_type_miss_count": 0,
                        "exact_id_miss_count": 0,
                        "false_positive_failure_count": 0,
                        "mean_latency_ms": 10,
                    },
                },
                {
                    "model": "snowflake/snowflake-arctic-embed-xs",
                    "summary": {
                        "vector_top_k_hits": 110,
                        "source_type_miss_count": 0,
                        "exact_id_miss_count": 1,
                        "false_positive_failure_count": 0,
                        "mean_latency_ms": 10,
                    },
                },
            ]
        )

        self.assertEqual(decision["recommended_runtime_change"], False)
        self.assertIn("exact-id", " ".join(decision["reasons"]))

    def test_decision_summary_requires_hit_gain_over_baseline_threshold(self) -> None:
        decision = _decision_summary(
            [
                {
                    "model": "BAAI/bge-small-en-v1.5",
                    "summary": {
                        "vector_top_k_hits": 265,
                        "source_type_miss_count": 0,
                        "exact_id_miss_count": 0,
                        "false_positive_failure_count": 0,
                        "mean_latency_ms": 10,
                    },
                },
                {
                    "model": "snowflake/snowflake-arctic-embed-s",
                    "summary": {
                        "vector_top_k_hits": 265,
                        "source_type_miss_count": 0,
                        "exact_id_miss_count": 0,
                        "false_positive_failure_count": 0,
                        "mean_latency_ms": 10,
                    },
                },
            ]
        )

        self.assertEqual(decision["recommended_runtime_change"], False)
        self.assertIsNone(decision["candidate_for_future_runtime_milestone"])

    def test_decision_summary_marks_clean_winner_as_future_candidate_only(self) -> None:
        decision = _decision_summary(
            [
                {
                    "model": "BAAI/bge-small-en-v1.5",
                    "summary": {
                        "vector_top_k_hits": 265,
                        "source_type_miss_count": 0,
                        "exact_id_miss_count": 0,
                        "false_positive_failure_count": 0,
                        "mean_latency_ms": 10,
                    },
                },
                {
                    "model": "nomic-ai/nomic-embed-text-v1.5-Q",
                    "summary": {
                        "vector_top_k_hits": 270,
                        "source_type_miss_count": 0,
                        "exact_id_miss_count": 0,
                        "false_positive_failure_count": 0,
                        "mean_latency_ms": 10,
                    },
                },
            ]
        )

        self.assertEqual(decision["recommended_runtime_change"], False)
        self.assertEqual(
            decision["candidate_for_future_runtime_milestone"],
            "nomic-ai/nomic-embed-text-v1.5-Q",
        )


if __name__ == "__main__":
    unittest.main()
