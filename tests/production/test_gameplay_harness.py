from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest

from tests.production.gameplay_judge import judge_artifact
from tests.production.gameplay_runner import run_scenario
from tests.production.ollama_readiness import prepare_ollama_cloud_environment
from tests.production.ollama_user_client import (
    OllamaUserClient,
    OllamaUserClientConfig,
    OllamaUserClientError,
    build_dummy_user_prompt,
)

ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_DIR = Path(__file__).resolve().parent
MANIFEST_PATH = PRODUCTION_DIR / "corpus_manifest.json"
SCENARIO_DIR = PRODUCTION_DIR / "scenarios"
OLLAMA_SCENARIO_DIR = PRODUCTION_DIR / "scenarios_ollama"
EXPECTED_SCENARIOS = {
    "add_variable",
    "clarification_required",
    "disconnect_exact",
    "failed_validation_rollback",
    "insert_block_on_connection",
    "internal_tool_name_refused",
    "raw_yaml_refused",
    "read_only_explain",
    "remove_detached_block",
    "rewire_exact",
    "save_load_lifecycle",
    "set_param_validate",
    "set_state_toggle",
    "unsafe_load_refused",
    "unsafe_save_refused",
}
EXPECTED_OLLAMA_SCENARIOS = {
    "natural_read_only_explain",
    "natural_save_load",
    "natural_set_param",
}


class ProductionHarnessTests(unittest.TestCase):
    def test_corpus_manifest_validates(self) -> None:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        self.assertEqual(manifest["schema_version"], "2026-05-14.phase2-corpus-v1")
        entries = manifest.get("entries")
        self.assertIsInstance(entries, list)
        self.assertGreaterEqual(len(entries), 3)
        ids = set()
        for entry in entries:
            self.assertIsInstance(entry.get("id"), str)
            self.assertNotIn(entry["id"], ids)
            ids.add(entry["id"])
            self.assertIsInstance(entry.get("source_path"), str)
            copy_policy = entry.get("copied_work_path_policy", "")
            self.assertTrue(
                "copy" in copy_policy or "copied" in copy_policy,
                copy_policy,
            )
            self.assertIsInstance(entry.get("graph_type_tags"), list)
            self.assertIsInstance(entry.get("block_count"), int)
            self.assertIsInstance(entry.get("connection_count"), int)
            self.assertIsInstance(entry.get("variables_present"), list)
            self.assertIsInstance(entry.get("safe_candidate_operations"), list)
            self.assertIsInstance(entry.get("expected_delta_categories"), list)
            if entry["source_path"].startswith("tests/"):
                self.assertTrue((ROOT / entry["source_path"]).exists())

    def test_scenarios_validate(self) -> None:
        scenario_ids = set()
        manifest_ids = {
            entry["id"]
            for entry in json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))["entries"]
        }
        for path in sorted(SCENARIO_DIR.glob("*.json")):
            scenario = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(
                scenario["schema_version"],
                "2026-05-14.phase2-scenario-v1",
            )
            self.assertNotIn(scenario["scenario_id"], scenario_ids)
            scenario_ids.add(scenario["scenario_id"])
            self.assertIn(scenario["graph_id"], manifest_ids)
            self.assertIsInstance(scenario.get("scripted_user_turns"), list)
            self.assertLessEqual(
                len(scenario["scripted_user_turns"]),
                int(scenario["max_turns"]),
            )
            self.assertIsInstance(scenario.get("forbidden_events"), list)
        self.assertEqual(
            scenario_ids,
            EXPECTED_SCENARIOS,
        )

    def test_ollama_scenarios_validate(self) -> None:
        scenario_ids = set()
        manifest_ids = {
            entry["id"]
            for entry in json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))["entries"]
        }
        for path in sorted(OLLAMA_SCENARIO_DIR.glob("*.json")):
            scenario = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(
                scenario["schema_version"],
                "2026-05-14.phase4-ollama-scenario-v1",
            )
            self.assertEqual(scenario["user_mode"], "ollama_user")
            self.assertNotIn(scenario["scenario_id"], scenario_ids)
            scenario_ids.add(scenario["scenario_id"])
            self.assertIn(scenario["graph_id"], manifest_ids)
            self.assertIsInstance(scenario.get("scenario_goal"), str)
            self.assertIsInstance(scenario.get("allowed_user_behavior"), list)
            self.assertIsInstance(scenario.get("forbidden_user_behavior"), list)
            self.assertGreaterEqual(int(scenario.get("max_user_turns", 0)), 1)
        self.assertEqual(scenario_ids, EXPECTED_OLLAMA_SCENARIOS)

    def test_runner_copies_graph_and_never_mutates_source(self) -> None:
        source = ROOT / "tests/data/random_bit_generator.grc"
        before = source.read_bytes()
        with tempfile.TemporaryDirectory() as tmpdir:
            artifact_path = Path(tmpdir) / "readonly.json"
            artifact = run_scenario(
                scenario_path=SCENARIO_DIR / "read_only_explain.json",
                artifact_path=artifact_path,
            )
            self.assertTrue(artifact["judge"]["passed"], artifact["judge"])
            self.assertTrue(artifact["source_integrity"]["unchanged"])
            self.assertEqual(source.read_bytes(), before)
            self.assertTrue(artifact_path.exists())
            self.assertNotEqual(
                artifact["paths"]["source_path"],
                artifact["paths"]["work_graph_path"],
            )

    def test_gameplay_artifact_has_required_trace_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            artifact_path = Path(tmpdir) / "readonly.json"
            run_scenario(
                scenario_path=SCENARIO_DIR / "read_only_explain.json",
                artifact_path=artifact_path,
            )
            loaded = json.loads(artifact_path.read_text(encoding="utf-8"))
        for key in (
            "conversation",
            "turns",
            "initial_graph_snapshot",
            "final_graph_snapshot",
            "graph_delta",
            "validation_results",
            "save_load_events",
            "source_integrity",
            "judge",
        ):
            self.assertIn(key, loaded)
        turn = loaded["turns"][0]
        self.assertIsInstance(turn["requested_tool_calls_raw"], list)
        self.assertIsInstance(turn["normalized_args"], list)
        self.assertIsInstance(turn["executed_tool_calls_raw"], list)
        self.assertIsInstance(turn["executed_tools"], list)
        self.assertIsInstance(turn["tool_results"], list)
        self.assertIn("graph_snapshot_before", turn)
        self.assertIn("graph_snapshot_after", turn)
        self.assertIn("graph_revision_before", turn)
        self.assertIn("graph_revision_after", turn)
        self.assertIn("forbidden_events", loaded)
        self.assertIn("final_state_summary", loaded)

    def test_all_scripted_scenarios_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            for path in sorted(SCENARIO_DIR.glob("*.json")):
                artifact_path = Path(tmpdir) / f"{path.stem}.json"
                artifact = run_scenario(
                    scenario_path=path,
                    artifact_path=artifact_path,
                )
                self.assertTrue(
                    artifact["judge"]["passed"],
                    (path.name, artifact["judge"]),
                )
                self.assertTrue(
                    artifact["source_integrity"]["unchanged"],
                    path.name,
                )
                self.assertNotEqual(
                    artifact["paths"]["source_path"],
                    artifact["paths"]["work_graph_path"],
                    path.name,
                )

    def test_judge_detects_preview_mutation_if_injected(self) -> None:
        artifact = _minimal_artifact(
            requested=[{"name": "change_graph", "arguments": {"dry_run": True}}],
            executed=[{"name": "change_graph", "arguments": {"ok": True, "dry_run": True}}],
            before_hash="a",
            after_hash="b",
        )
        result = judge_artifact(artifact)
        self.assertFalse(result["passed"])
        self.assertIn(
            "preview_mutation",
            {event["event"] for event in result["forbidden_events"]},
        )

    def test_judge_detects_raw_legacy_tool_if_injected(self) -> None:
        artifact = _minimal_artifact(
            requested=[{"name": "apply_edit", "arguments": {}}],
            executed=[],
            before_hash="a",
            after_hash="a",
        )
        result = judge_artifact(artifact)
        self.assertFalse(result["passed"])
        self.assertIn(
            "raw_legacy_tool_call",
            {event["event"] for event in result["forbidden_events"]},
        )

    def test_judge_detects_failed_validation_commit_if_injected(self) -> None:
        artifact = _minimal_artifact(
            requested=[{"name": "change_graph", "arguments": {"dry_run": False}}],
            executed=[
                {
                    "name": "change_graph",
                    "arguments": {
                        "ok": False,
                        "dry_run": False,
                        "validation_result": {"status": "invalid"},
                    },
                }
            ],
            before_hash="a",
            after_hash="b",
        )
        result = judge_artifact(artifact)
        self.assertFalse(result["passed"])
        self.assertIn(
            "failed_validation_commit",
            {event["event"] for event in result["forbidden_events"]},
        )

    def test_artifact_does_not_contain_ollama_secret_markers(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text("ollama_key=super-secret-test-value\n", encoding="utf-8")
            environ: dict[str, str] = {}
            readiness = prepare_ollama_cloud_environment(
                env_path=env_path,
                environ=environ,
            )
            self.assertTrue(readiness["cloud_key_present"])
            self.assertEqual(environ.get("OLLAMA_API_KEY"), "super-secret-test-value")

            old_env = os.environ.get("OLLAMA_API_KEY")
            try:
                os.environ.pop("OLLAMA_API_KEY", None)
                artifact_path = Path(tmpdir) / "readonly.json"
                artifact = run_scenario(
                    scenario_path=SCENARIO_DIR / "read_only_explain.json",
                    artifact_path=artifact_path,
                )
            finally:
                if old_env is not None:
                    os.environ["OLLAMA_API_KEY"] = old_env
            text = artifact_path.read_text(encoding="utf-8")
            self.assertNotIn("super-secret-test-value", text)
            self.assertNotIn("ollama_key", text)
            self.assertNotIn("OLLAMA_API_KEY", text)
        self.assertIn("cloud_key_present", artifact["ollama_readiness"])

    def test_ollama_client_redacts_key_and_disables_network_by_default(self) -> None:
        client = OllamaUserClient(
            OllamaUserClientConfig(enabled=False, cloud_mode=True),
            api_key="super-secret-test-value",
        )
        redacted = json.dumps(client.redacted_config(), sort_keys=True)
        self.assertNotIn("super-secret-test-value", redacted)
        self.assertNotIn("OLLAMA_API_KEY", redacted)
        self.assertNotIn("ollama_key", redacted)
        with self.assertRaises(OllamaUserClientError) as ctx:
            client.generate_user_turn(
                scenario_goal="Ask for a read-only graph explanation.",
                graph_summary={"block_count": 1},
                allowed_user_behavior=["Ask naturally."],
                forbidden_user_behavior=["Do not mention tools."],
                prior_conversation=[],
            )
        self.assertEqual(ctx.exception.error_type, "network_disabled")

    def test_dummy_user_prompt_excludes_hidden_expected_answer(self) -> None:
        prompt = build_dummy_user_prompt(
            scenario_goal="Ask for a harmless graph explanation.",
            graph_summary={"block_count": 1, "variable_values": {"samp_rate": "32000"}},
            allowed_user_behavior=["Ask naturally."],
            forbidden_user_behavior=["Do not mention internals."],
            prior_conversation=[
                {"role": "assistant", "content": "Visible prior assistant text."}
            ],
        )
        self.assertIn("Ask for a harmless graph explanation.", prompt)
        self.assertNotIn("expected_final_state", prompt)
        self.assertNotIn("expected_graph_delta", prompt)
        self.assertNotIn("hidden-pass-token", prompt)

    def test_ollama_disabled_artifact_marks_infra_failure_not_grc_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            artifact_path = Path(tmpdir) / "ollama_disabled.json"
            artifact = run_scenario(
                scenario_path=OLLAMA_SCENARIO_DIR / "natural_read_only_explain.json",
                artifact_path=artifact_path,
                enable_ollama_network=False,
            )
            text = artifact_path.read_text(encoding="utf-8")
        self.assertFalse(artifact["judge"]["passed"])
        self.assertEqual(artifact["infra_failure"]["source"], "ollama_user")
        self.assertEqual(artifact["infra_failure"]["error_type"], "network_disabled")
        self.assertFalse(artifact["grc_agent_failure"])
        self.assertIn("dummy_user", artifact)
        self.assertNotIn("super-secret-test-value", text)
        self.assertNotIn("OLLAMA_API_KEY", text)
        self.assertNotIn("ollama_key", text)

    def test_deterministic_judge_handles_ollama_user_text(self) -> None:
        artifact = _minimal_artifact(
            requested=[],
            executed=[],
            before_hash="a",
            after_hash="a",
        )
        artifact["scenario"]["user_mode"] = "ollama_user"
        artifact["dummy_user"] = {
            "mode": "ollama_user",
            "provider": "cloud",
            "network_enabled": True,
            "turns": [{"turn_index": 0}],
        }
        artifact["conversation"] = [{"role": "user", "content": "Explain this graph."}]
        artifact["turns"][0]["user_prompt"] = "Explain this graph."
        result = judge_artifact(artifact)
        self.assertTrue(result["dimensions"]["natural_user_quality"])

    def test_ollama_artifact_schema_has_required_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            artifact_path = Path(tmpdir) / "ollama_disabled.json"
            run_scenario(
                scenario_path=OLLAMA_SCENARIO_DIR / "natural_set_param.json",
                artifact_path=artifact_path,
                enable_ollama_network=False,
            )
            artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        self.assertEqual(
            artifact["schema_version"],
            "2026-05-14.phase4-gameplay-artifact-v1",
        )
        self.assertIn("dummy_user", artifact)
        self.assertIn("infra_failure", artifact)
        self.assertIn("grc_agent_failure", artifact)
        self.assertIn("config", artifact["dummy_user"])
        self.assertIn("credential_present", artifact["dummy_user"]["config"])


def _minimal_artifact(
    *,
    requested: list[dict[str, object]],
    executed: list[dict[str, object]],
    before_hash: str,
    after_hash: str,
) -> dict[str, object]:
    return {
        "scenario": {
            "scenario_id": "injected",
            "expected_graph_delta": {"no_content_change": before_hash == after_hash},
            "expected_validation_status": "any",
            "expected_save_load_behavior": {
                "save_expected": False,
                "load_expected": False,
            },
        },
        "paths": {
            "source_path": "/tmp/source.grc",
            "work_dir": "/tmp/work",
        },
        "turns": [
            {
                "requested_tool_calls_raw": requested,
                "executed_tool_calls_raw": executed,
                "graph_snapshot_before": {"raw_hash": before_hash},
                "graph_snapshot_after": {"raw_hash": after_hash},
            }
        ],
        "graph_delta": {},
        "final_graph_snapshot": {"dirty": False, "validation_status": "unknown"},
        "save_load_events": [],
        "source_integrity": {
            "before_sha256": "source",
            "after_sha256": "source",
        },
    }


if __name__ == "__main__":
    unittest.main()
