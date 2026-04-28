"""Tests for persisted live-eval release dashboard aggregation."""

from __future__ import annotations

import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from contextlib import redirect_stdout

from tests.llama_eval.release_dashboard import build_release_dashboard, main


def _entry(
    *,
    phase: int,
    category: str,
    case_name: str,
    run_index: int,
    status: str,
) -> dict:
    return {
        "phase": phase,
        "category": category,
        "case_name": case_name,
        "run_index": run_index,
        "status": status,
        "run_result": {"status": status},
    }


class ReleaseDashboardTests(unittest.TestCase):
    def test_dashboard_marks_all_required_phases_ready(self) -> None:
        store = {
            "runs": [
                _entry(phase=20, category="edit", case_name="param", run_index=0, status="PASS"),
                _entry(phase=20, category="edit", case_name="param", run_index=1, status="PASS"),
                _entry(phase=30, category="followup", case_name="save", run_index=0, status="PASS"),
                _entry(phase=30, category="followup", case_name="save", run_index=1, status="PASS"),
                _entry(phase=40, category="external", case_name="dial", run_index=0, status="PASS"),
                _entry(phase=40, category="external", case_name="dial", run_index=1, status="PASS"),
            ]
        }

        dashboard = build_release_dashboard(
            [store],
            required_phases=(20, 30, 40),
            min_runs_per_case=2,
            stability_threshold=1.0,
        )

        self.assertTrue(dashboard["release_ready"], dashboard)
        self.assertEqual(dashboard["missing_required_phases"], [])
        self.assertEqual(dashboard["short_run_cases"], [])
        self.assertEqual(dashboard["unstable_cases"], [])

    def test_dashboard_reports_missing_short_and_unstable_cases(self) -> None:
        store = {
            "runs": [
                _entry(phase=20, category="edit", case_name="param", run_index=0, status="PASS"),
                _entry(phase=20, category="edit", case_name="param", run_index=1, status="FAIL"),
                _entry(phase=40, category="external", case_name="dial", run_index=0, status="PASS"),
            ]
        }

        dashboard = build_release_dashboard(
            [store],
            required_phases=(20, 30, 40),
            min_runs_per_case=2,
            stability_threshold=1.0,
        )

        self.assertFalse(dashboard["release_ready"], dashboard)
        self.assertEqual(dashboard["missing_required_phases"], [30])
        self.assertIn("tier2_release/edit/param", dashboard["unstable_cases"])
        self.assertIn("tier4_external_examples/external/dial", dashboard["short_run_cases"])

    def test_cli_returns_nonzero_when_dashboard_not_ready(self) -> None:
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "runs.json"
            path.write_text(
                json.dumps(
                    {
                        "runs": [
                            _entry(
                                phase=20,
                                category="edit",
                                case_name="param",
                                run_index=0,
                                status="FAIL",
                            )
                        ]
                    }
                ),
                encoding="utf-8",
            )

            with redirect_stdout(io.StringIO()):
                code = main(
                    [
                        "--results-path",
                        str(path),
                        "--required-phase",
                        "20",
                        "--min-runs-per-case",
                        "1",
                    ]
                )

        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
