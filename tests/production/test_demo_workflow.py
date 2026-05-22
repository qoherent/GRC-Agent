from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from scripts.demo.export_demo_timeline import export_timeline
from scripts.demo.run_grc_agent_demo import (
    DEMO_ARTIFACT_SCHEMA_VERSION,
    DemoError,
    scan_secret_text,
    sha256_file,
    validate_demo_artifact,
    validate_health_ready,
    verify_source_unchanged,
)


def _sample_artifact(tmp_path: Path) -> dict[str, object]:
    graph = tmp_path / "copy.grc"
    graph.write_text("options:\n  id: demo\n", encoding="utf-8")
    return {
        "schema_version": DEMO_ARTIFACT_SCHEMA_VERSION,
        "classification": (
            "Release-validated subset + beta-validated graph operations; not production-ready"
        ),
        "health": {
            "status": "ok",
            "llama_context_verified": True,
            "llama_actual_context_tokens": 120064,
            "llama_desired_context_tokens": 120000,
            "model_facing_tools": [
                "inspect_graph",
                "search_blocks",
                "ask_grc_docs",
                "change_graph",
                "save_graph_explicit",
                "load_graph_explicit",
            ],
        },
        "paths": {
            "final_graph_path": str(graph),
            "work_graph_path": str(graph),
            "source_graph_path": str(tmp_path / "source.grc"),
        },
        "screenshots": {
            "before": {"path": None, "exists": False},
            "after": {"path": None, "exists": False},
        },
        "source_integrity": {
            "before_sha256": "a",
            "after_sha256": "a",
            "unchanged": True,
        },
        "steps": [
            {
                "label": "Inspect Graph",
                "user_prompt": "Inspect graph",
                "assistant_summary": "Inspected.",
                "requested_tool_calls_raw": [
                    {
                        "name": "inspect_graph",
                        "arguments": {
                            "view": "overview",
                            "targets": [],
                            "params": [],
                        },
                    }
                ],
                "executed_tool_calls_raw": [
                    {"name": "inspect_graph", "arguments": {"ok": True}}
                ],
                "executed_tools": ["inspect_graph"],
                "graph_delta": {},
                "mutation": False,
                "validation_results": [],
                "graph_snapshot_before": {"path": str(graph), "raw_hash": "1"},
                "graph_snapshot_after": {"path": str(graph), "raw_hash": "1"},
            },
            {
                "label": "Set Sample Rate",
                "user_prompt": "Change the sample rate to 48000.",
                "assistant_summary": "Updated.",
                "requested_tool_calls_raw": [
                    {
                        "name": "change_graph",
                        "arguments": {"operation_kind": "set_param"},
                    }
                ],
                "executed_tool_calls_raw": [
                    {
                        "name": "change_graph",
                        "arguments": {
                            "ok": True,
                            "validation_result": {"valid": True},
                        },
                    }
                ],
                "executed_tools": ["change_graph"],
                "graph_delta": {"variables": {"samp_rate": "48000"}},
                "mutation": True,
                "validation_results": [
                    {
                        "tool": "change_graph",
                        "validation_result": {"valid": True},
                    }
                ],
                "graph_snapshot_before": {"path": str(graph), "raw_hash": "1"},
                "graph_snapshot_after": {
                    "path": str(graph),
                    "raw_hash": "2",
                    "validation_status": "valid",
                },
            },
        ],
        "save_load_events": [{"tool": "save_graph_explicit", "ok": True}],
        "validation_results": [
            {
                "tool": "change_graph",
                "validation_result": {"valid": True},
            }
        ],
        "safety_requirements": {
            "original_graph_not_mutated": True,
            "validation_succeeded": True,
            "explicit_save": True,
            "raw_legacy_attempts": 0,
            "failed_validation_commits": 0,
            "debug_bundle_generated": True,
            "no_secrets_in_artifacts": True,
            "forbidden_events": [],
        },
    }


class DemoWorkflowTests(unittest.TestCase):
    def test_artifact_schema_validates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            artifact = _sample_artifact(Path(tmp))
            self.assertEqual(validate_demo_artifact(artifact), [])

    def test_original_graph_hash_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.grc"
            source.write_text("original\n", encoding="utf-8")
            before = sha256_file(source)
            integrity = verify_source_unchanged(source, before)
            self.assertTrue(integrity["unchanged"])
            source.write_text("changed\n", encoding="utf-8")
            integrity = verify_source_unchanged(source, before)
            self.assertFalse(integrity["unchanged"])

    def test_timeline_export_works(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            timeline = export_timeline(_sample_artifact(Path(tmp)))
            self.assertEqual(timeline["schema_version"], "2026-05-21.demo-timeline-v1")
            self.assertEqual(len(timeline["steps"]), 2)
            self.assertEqual(timeline["steps"][1]["operation_kind"], "set_param")
            self.assertTrue(timeline["steps"][1]["mutation"])

    def test_no_secret_strings_in_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            artifact = _sample_artifact(Path(tmp))
            artifact_text = json.dumps(artifact, sort_keys=True)
            self.assertEqual(scan_secret_text(artifact_text), [])
            self.assertIn("ollama_key", scan_secret_text("ollama_key=abc123"))
            self.assertIn("Authorization", scan_secret_text("Authorization: Bearer abc"))

    def test_runner_refuses_unhealthy_context_unless_docs_only(self) -> None:
        unhealthy = {
            "status": "not_ready",
            "status_reasons": ["llama_unreachable"],
            "llama_context_verified": False,
        }
        with self.assertRaises(DemoError):
            validate_health_ready(unhealthy)
        validate_health_ready(unhealthy, dry_run_docs_only=True)


if __name__ == "__main__":
    unittest.main()
