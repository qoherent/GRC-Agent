from __future__ import annotations

import io
import json
import unittest
from unittest.mock import patch

from tests.retrieval_eval import vector_regression
from tests.retrieval_eval.vector_retrieval import RetrievalEvalCase, run_eval


class VectorRegressionProgressTests(unittest.TestCase):
    def test_main_emits_progress_to_stderr_and_json_to_stdout(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()

        def fake_eval_runner(*, progress_callback: object, progress_interval: int) -> dict[str, object]:
            self.assertEqual(
                progress_interval,
                vector_regression.VECTOR_REGRESSION_PROGRESS_INTERVAL,
            )
            progress = progress_callback
            self.assertTrue(callable(progress))
            progress({"phase": "waiting_for_lock"})
            progress({"phase": "start", "total_cases": 290})
            progress(
                {
                    "phase": "case_complete",
                    "completed_cases": 25,
                    "total_cases": 290,
                    "case_name": "case_25",
                    "latency_ms": 1.5,
                }
            )
            progress({"phase": "deterministic_rebuild"})
            progress({"phase": "done", "duration_ms": 123.4, "total_cases": 290})
            return _passing_eval_payload()

        exit_code = vector_regression.main(
            eval_runner=fake_eval_runner,
            stdout=stdout,
            stderr=stderr,
        )

        self.assertEqual(exit_code, 0)
        self.assertTrue(json.loads(stdout.getvalue())["ok"])
        progress_output = stderr.getvalue()
        self.assertIn("waiting for retrieval eval lock", progress_output)
        self.assertIn("starting 290 retrieval cases", progress_output)
        self.assertIn("completed 25/290 cases", progress_output)
        self.assertIn("checking deterministic rebuild", progress_output)
        self.assertIn("finished eval loop", progress_output)

    def test_run_eval_progress_callback_reports_loop_phases(self) -> None:
        events: list[dict[str, object]] = []
        case = RetrievalEvalCase(
            "exact_blocks_head",
            "blocks_head",
            "catalog",
            ("blocks_head",),
            case_type="exact_id",
        )

        def fake_search(query: str, *, scope: str, k: int) -> dict[str, object]:
            self.assertEqual(query, "blocks_head")
            self.assertEqual(scope, "catalog")
            self.assertEqual(k, 5)
            return {
                "ok": True,
                "results": [
                    {
                        "canonical_block_id": "blocks_head",
                        "record_id": "catalog:blocks_head",
                        "title": "Head",
                        "source_type": "catalog_block",
                        "provenance": {"path": "catalog"},
                    }
                ],
            }

        with patch(
            "tests.retrieval_eval.vector_retrieval._deterministic_rebuild_pass",
            return_value=True,
        ):
            payload = run_eval(
                eval_cases=(case,),
                vector_search_fn=fake_search,
                progress_callback=events.append,
                progress_interval=1,
            )

        self.assertTrue(payload["ok"])
        self.assertEqual(
            [event["phase"] for event in events],
            [
                "waiting_for_lock",
                "start",
                "case_complete",
                "deterministic_rebuild",
                "done",
            ],
        )
        self.assertEqual(events[2]["completed_cases"], 1)
        self.assertEqual(events[2]["total_cases"], 1)


def _passing_eval_payload() -> dict[str, object]:
    return {
        "ok": True,
        "summary": {
            "total_cases": 290,
            "vector_top_k_hits": 276,
            "safety_passes": 290,
            "provenance_passes": 290,
            "deterministic_rebuild_pass": True,
        },
        "miss_analysis": {
            "vector_miss_count": 0,
            "exact_id_miss_count": 0,
            "false_positive_failure_count": 0,
            "source_type_miss_count": 0,
        },
    }


if __name__ == "__main__":
    unittest.main()
