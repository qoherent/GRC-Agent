"""Tests for persisted live-eval release dashboard aggregation."""

from __future__ import annotations

import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from contextlib import redirect_stdout

from tests.llama_eval.harness import (
    MVP_RELEASE_MODEL_TOOLS,
    build_persisted_run_entry,
)
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
            "run_result": {
                "status": status,
                "routing_pass": True,
                "argument_pass": True,
                "tool_success_pass": True,
                "semantic_pass": True,
                "runtime_safety_pass": True,
                "model_contract_pass": True,
                "end_state_pass": True,
                "turn_results": [
                    {
                        "requested_tool_calls_raw": [],
                        "executed_tool_calls_raw": [],
                    }
                ],
            },
            "release_metadata": {
                "mvp_tool_profile": True,
                "model_tool_names": sorted(MVP_RELEASE_MODEL_TOOLS),
                "release_profile": "R0_READ_ONLY" if phase == 20 else "BETA_COMPLEX_MUTATION",
            },
        }


class ReleaseDashboardTests(unittest.TestCase):
    def test_dashboard_rejects_non_mvp_profile_entries(self) -> None:
        store = {
            "runs": [
                {
                    **_entry(
                        phase=20,
                        category="edit",
                        case_name="param",
                        run_index=0,
                        status="PASS",
                    ),
                    "release_metadata": {
                        "mvp_tool_profile": False,
                        "model_tool_names": ["apply_edit"],
                    },
                }
            ]
        }
        dashboard = build_release_dashboard(
            [store],
            required_phases=(20,),
            min_runs_per_case=1,
            stability_threshold=1.0,
        )
        self.assertFalse(dashboard["release_ready"], dashboard)
        self.assertTrue(dashboard["mixed_profile_entries"])

    def test_dashboard_marks_all_required_phases_ready(self) -> None:
        store = {
            "runs": [
                _entry(phase=20, category="edit", case_name="param", run_index=0, status="PASS"),
                _entry(phase=20, category="edit", case_name="param", run_index=1, status="PASS"),
                _entry(phase=25, category="state", case_name="toggle", run_index=0, status="PASS"),
                _entry(phase=25, category="state", case_name="toggle", run_index=1, status="PASS"),
                _entry(phase=71, category="external", case_name="dial", run_index=0, status="PASS"),
                _entry(phase=71, category="external", case_name="dial", run_index=1, status="PASS"),
                _entry(phase=50, category="uncertain", case_name="vague", run_index=0, status="PASS"),
                _entry(phase=50, category="uncertain", case_name="vague", run_index=1, status="PASS"),
            ]
        }

        dashboard = build_release_dashboard(
            [store],
            required_phases=(20, 25, 71, 50),
            min_runs_per_case=2,
            stability_threshold=1.0,
        )

        self.assertTrue(dashboard["release_ready"], dashboard)
        self.assertEqual(dashboard["missing_required_phases"], [])
        self.assertEqual(dashboard["short_run_cases"], [])
        self.assertEqual(dashboard["unstable_cases"], [])
        self.assertIn("50", dashboard["phases"])
        self.assertEqual(dashboard["phases"]["50"]["name"], "tier5_adversarial")

    def test_dashboard_reports_diagnostic_statuses_as_not_release_gating(self) -> None:
        store = {
            "runs": [
                {
                    **_entry(
                        phase=71,
                        category="external",
                        case_name="exact",
                        run_index=0,
                        status="PASS",
                    ),
                    "release_metadata": {
                        "mvp_tool_profile": True,
                        "model_tool_names": sorted(MVP_RELEASE_MODEL_TOOLS),
                        "release_profile": "R7_EXACT_EXTERNAL",
                    },
                },
                {
                    **_entry(
                        phase=72,
                        category="external",
                        case_name="natural",
                        run_index=0,
                        status="PASS",
                    ),
                    "release_metadata": {
                        "mvp_tool_profile": True,
                        "model_tool_names": sorted(MVP_RELEASE_MODEL_TOOLS),
                        "release_profile": "R7_NATURAL_EXTERNAL",
                    },
                },
            ]
        }

        dashboard = build_release_dashboard(
            [store],
            required_phases=(71, 72),
            min_runs_per_case=1,
            stability_threshold=1.0,
        )

        self.assertTrue(dashboard["release_ready"], dashboard)
        self.assertEqual(
            dashboard["capability_statuses"]["diagnostic-clean"],
            ["R7_EXACT_EXTERNAL"],
        )
        self.assertEqual(
            dashboard["capability_statuses"]["diagnostic-partial"],
            ["R7_NATURAL_EXTERNAL"],
        )
        self.assertEqual(
            dashboard["capability_statuses"]["not release-gating"],
            ["R7_EXACT_EXTERNAL", "R7_NATURAL_EXTERNAL"],
        )

    def test_dashboard_defaults_require_tier5(self) -> None:
        store = {
            "runs": [
                _entry(phase=20, category="edit", case_name="param", run_index=0, status="PASS"),
                _entry(phase=20, category="edit", case_name="param", run_index=1, status="PASS"),
                _entry(phase=20, category="edit", case_name="param", run_index=2, status="PASS"),
                _entry(phase=25, category="state", case_name="toggle", run_index=0, status="PASS"),
                _entry(phase=25, category="state", case_name="toggle", run_index=1, status="PASS"),
                _entry(phase=25, category="state", case_name="toggle", run_index=2, status="PASS"),
                _entry(phase=71, category="external", case_name="dial", run_index=0, status="PASS"),
                _entry(phase=71, category="external", case_name="dial", run_index=1, status="PASS"),
                _entry(phase=71, category="external", case_name="dial", run_index=2, status="PASS"),
            ]
        }

        dashboard = build_release_dashboard([store])

        self.assertFalse(dashboard["release_ready"], dashboard)
        self.assertIn(50, dashboard["missing_required_phases"])

    def test_dashboard_reports_missing_short_and_unstable_cases(self) -> None:
        store = {
            "runs": [
                _entry(phase=20, category="edit", case_name="param", run_index=0, status="PASS"),
                _entry(phase=20, category="edit", case_name="param", run_index=1, status="FAIL"),
                _entry(phase=71, category="external", case_name="dial", run_index=0, status="PASS"),
            ]
        }

        dashboard = build_release_dashboard(
            [store],
            required_phases=(20, 25, 71),
            min_runs_per_case=2,
            stability_threshold=1.0,
        )

        self.assertFalse(dashboard["release_ready"], dashboard)
        self.assertEqual(dashboard["missing_required_phases"], [25])
        self.assertIn("r0_r1_release/edit/param", dashboard["unstable_cases"])
        self.assertIn("r7_exact_external/external/dial", dashboard["short_run_cases"])

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

    def test_dashboard_rejects_raw_legacy_tool_calls_in_mvp_runs(self) -> None:
        store = {
            "runs": [
                {
                    **_entry(
                        phase=20,
                        category="edit",
                        case_name="param",
                        run_index=0,
                        status="PASS",
                    ),
                    "run_result": {
                        "status": "PASS",
                        "turn_results": [
                            {
                                "requested_tool_calls_raw": [
                                    {"name": "apply_edit", "arguments": {}},
                                ],
                                "executed_tool_calls_raw": [
                                    {
                                        "name": "apply_edit",
                                        "arguments": {
                                            "error_type": "tool_not_allowed_for_surface",
                                        },
                                    },
                                ],
                            },
                        ],
                    },
                }
            ]
        }
        dashboard = build_release_dashboard(
            [store],
            required_phases=(20,),
            min_runs_per_case=1,
            stability_threshold=1.0,
        )
        self.assertFalse(dashboard["release_ready"], dashboard)
        self.assertTrue(dashboard["raw_legacy_tool_entries"], dashboard)

    def test_dashboard_fails_closed_on_missing_raw_tool_history(self) -> None:
        store = {
            "runs": [
                {
                    **_entry(
                        phase=20,
                        category="edit",
                        case_name="param",
                        run_index=0,
                        status="PASS",
                    ),
                    "run_result": {"status": "PASS"},
                }
            ]
        }
        dashboard = build_release_dashboard(
            [store],
            required_phases=(20,),
            min_runs_per_case=1,
            stability_threshold=1.0,
        )
        self.assertFalse(dashboard["release_ready"], dashboard)
        self.assertTrue(dashboard["raw_tool_history_entries"], dashboard)

    def test_dashboard_scans_trace_for_hidden_legacy_tool_calls(self) -> None:
        store = {
            "runs": [
                {
                    **_entry(
                        phase=20,
                        category="edit",
                        case_name="param",
                        run_index=0,
                        status="PASS",
                    ),
                    "run_result": {
                        "status": "PASS",
                        "turn_results": [
                            {
                                "requested_tool_calls_raw": [],
                                "executed_tool_calls_raw": [],
                                "trace": {
                                    "raw_requested_tool_calls": [
                                        {"name": "apply_edit", "arguments": {}}
                                    ],
                                    "executed_tools": [],
                                },
                            }
                        ],
                    },
                }
            ]
        }
        dashboard = build_release_dashboard(
            [store],
            required_phases=(20,),
            min_runs_per_case=1,
            stability_threshold=1.0,
        )
        self.assertFalse(dashboard["release_ready"], dashboard)
        self.assertTrue(dashboard["raw_legacy_tool_entries"], dashboard)

    def test_persisted_run_entry_includes_release_metadata(self) -> None:
        case = type(
            "Case",
            (),
            {
                "category": "edit",
                "name": "param",
                "prompt": "Change samp_rate to 48000.",
                "expected_tools": ["apply_edit"],
            },
        )()

        entry = build_persisted_run_entry(
            phase=20,
            case=case,
            run_index=0,
            run_result={"status": "PASS", "tools_called": ["apply_edit"]},
            backend_restart_count=0,
        )

        metadata = entry["release_metadata"]
        self.assertIn("git_commit", metadata)
        self.assertIn("prompt_version", metadata)
        self.assertIn("prompt_sha256", metadata)
        self.assertIn("tool_schema_sha256", metadata)
        self.assertIn("turn_plan_policy_version", metadata)
        self.assertIn("turn_plan_policy_sha256", metadata)
        self.assertIn("results_schema_version", metadata)
        self.assertIn("fixture", metadata)


if __name__ == "__main__":
    unittest.main()
