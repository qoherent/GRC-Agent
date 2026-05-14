"""Tests for capability manifest bridge used by release_dashboard."""

from __future__ import annotations

import unittest

from tests.llama_eval.release_dashboard import build_release_dashboard, load_capability_manifests


class CapabilityManifestTests(unittest.TestCase):
    def test_manifest_set_covers_declared_profiles(self) -> None:
        manifests = load_capability_manifests()
        expected = {
            "R0_READ_ONLY",
            "R1_SET_PARAM_ONLY",
            "R1_SET_STATE",
            "R2_DISCONNECT",
            "R3_REWIRE",
            "R4A_INSERT",
            "R4B_REMOVE",
            "R4C_ADD_VARIABLE",
            "R5_SAVE_LOAD",
            "R7_EXACT_EXTERNAL",
            "R7_NATURAL_EXTERNAL",
            "Tier5_ADVERSARIAL",
        }
        self.assertTrue(expected.issubset(set(manifests.keys())), manifests)

    def test_manifest_required_dimensions_present(self) -> None:
        manifests = load_capability_manifests()
        for suite, manifest in manifests.items():
            with self.subTest(suite=suite):
                dims = manifest.get("required_dimensions")
                self.assertIsInstance(dims, list)
                self.assertIn("model_contract_pass", dims)
                self.assertIn("runtime_safety_pass", dims)
                self.assertIn("semantic_pass", dims)
                self.assertIn("release_gating", manifest)

    def test_dashboard_reports_manifest_dimension_gaps(self) -> None:
        store = {
            "runs": [
                {
                    "phase": 56,
                    "category": "rewire",
                    "case_name": "missing_dims",
                    "run_index": 0,
                    "status": "PASS",
                    "run_result": {
                        "status": "PASS",
                        "turn_results": [
                            {
                                "requested_tool_calls_raw": [
                                    {
                                        "name": "change_graph",
                                        "arguments": {
                                            "operation_kind": "rewire",
                                            "dry_run": True,
                                        },
                                    }
                                ],
                                "executed_tool_calls_raw": [
                                    {
                                        "name": "change_graph",
                                        "arguments": {"ok": True},
                                    }
                                ],
                            }
                        ],
                        "model_contract_pass": True,
                        "runtime_safety_pass": True,
                        "semantic_pass": True,
                    },
                    "release_metadata": {
                        "mvp_tool_profile": True,
                        "model_tool_names": [
                            "inspect_graph",
                            "search_blocks",
                            "ask_grc_docs",
                            "change_graph",
                            "save_graph_explicit",
                            "load_graph_explicit",
                        ],
                        "release_profile": "R3_REWIRE",
                    },
                }
            ]
        }
        dashboard = build_release_dashboard(
            [store],
            required_phases=(56,),
            min_runs_per_case=1,
            stability_threshold=1.0,
        )
        self.assertFalse(dashboard["release_ready"], dashboard)
        self.assertTrue(dashboard["manifest_dimension_entries"], dashboard)


if __name__ == "__main__":
    unittest.main()
