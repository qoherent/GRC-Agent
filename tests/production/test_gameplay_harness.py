from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from tests.production.gameplay_judge import judge_artifact
from tests.production.gameplay_runner import (
    FAILURE_CATEGORIES,
    aggregate_ollama_runs,
    classify_failure,
    run_repeated_ollama_config,
    run_scenario,
)
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
OLLAMA_GAMEPLAY_CONFIG = PRODUCTION_DIR / "ollama_gameplay_config.json"

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
        ids: set[str] = set()
        for entry in entries:
            self.assertIsInstance(entry.get("id"), str)
            self.assertNotIn(entry["id"], ids)
            ids.add(entry["id"])
            self.assertIsInstance(entry.get("source_path"), str)
            self.assertIn("cop", entry.get("copied_work_path_policy", ""))
            self.assertIsInstance(entry.get("graph_type_tags"), list)
            self.assertIsInstance(entry.get("block_count"), int)
            self.assertIsInstance(entry.get("connection_count"), int)
            self.assertIsInstance(entry.get("variables_present"), list)
            self.assertIsInstance(entry.get("safe_candidate_operations"), list)
            self.assertIsInstance(entry.get("expected_delta_categories"), list)
            if entry["source_path"].startswith("tests/"):
                self.assertTrue((ROOT / entry["source_path"]).exists())

    def test_core_scenarios_validate(self) -> None:
        manifest_ids = {
            entry["id"]
            for entry in json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))["entries"]
        }
        scenario_ids = set()
        for path in sorted(SCENARIO_DIR.glob("*.json")):
            scenario = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(scenario["schema_version"], "2026-05-14.phase2-scenario-v1")
            self.assertNotIn(scenario["scenario_id"], scenario_ids)
            scenario_ids.add(scenario["scenario_id"])
            self.assertIn(scenario["graph_id"], manifest_ids)
            self.assertIsInstance(scenario.get("scripted_user_turns"), list)
            self.assertLessEqual(len(scenario["scripted_user_turns"]), int(scenario["max_turns"]))
            self.assertIsInstance(scenario.get("forbidden_events"), list)
        self.assertEqual(scenario_ids, EXPECTED_SCENARIOS)

    def test_ollama_scenarios_and_config_validate(self) -> None:
        manifest_ids = {
            entry["id"]
            for entry in json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))["entries"]
        }
        scenario_ids = set()
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

        config = json.loads(OLLAMA_GAMEPLAY_CONFIG.read_text(encoding="utf-8"))
        self.assertEqual(
            config["schema_version"],
            "2026-05-15.phase5-ollama-gameplay-config-v1",
        )
        self.assertEqual(config["provider"], "cloud")
        self.assertEqual(config["model"], "gemma3:4b")
        self.assertEqual(config["temperature"], 0.0)
        self.assertEqual(config["n_runs"], 1)
        self.assertEqual(set(config["scenarios"]), EXPECTED_OLLAMA_SCENARIOS)

    def test_runner_copies_graph_and_writes_required_trace_fields(self) -> None:
        source = ROOT / "tests/data/random_bit_generator.grc"
        before = source.read_bytes()
        with tempfile.TemporaryDirectory() as tmpdir:
            artifact_path = Path(tmpdir) / "readonly.json"
            artifact = run_scenario(
                scenario_path=SCENARIO_DIR / "read_only_explain.json",
                artifact_path=artifact_path,
            )
            loaded = json.loads(artifact_path.read_text(encoding="utf-8"))

        self.assertTrue(artifact["judge"]["passed"], artifact["judge"])
        self.assertTrue(artifact["source_integrity"]["unchanged"])
        self.assertEqual(source.read_bytes(), before)
        self.assertNotEqual(artifact["paths"]["source_path"], artifact["paths"]["work_graph_path"])
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
            "forbidden_events",
            "final_state_summary",
        ):
            self.assertIn(key, loaded)
        turn = loaded["turns"][0]
        for key in (
            "requested_tool_calls_raw",
            "normalized_args",
            "executed_tool_calls_raw",
            "executed_tools",
            "tool_results",
            "graph_snapshot_before",
            "graph_snapshot_after",
            "graph_revision_before",
            "graph_revision_after",
        ):
            self.assertIn(key, turn)

    def test_single_mutation_scenario_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            artifact = run_scenario(
                scenario_path=SCENARIO_DIR / "set_param_validate.json",
                artifact_path=Path(tmpdir) / "set_param_validate.json",
            )
        self.assertTrue(artifact["judge"]["passed"], artifact["judge"])
        self.assertTrue(artifact["source_integrity"]["unchanged"])

    def test_judge_detects_core_forbidden_events(self) -> None:
        cases = [
            (
                _minimal_artifact(
                    requested=[{"name": "change_graph", "arguments": {"dry_run": True}}],
                    executed=[{"name": "change_graph", "arguments": {"ok": True, "dry_run": True}}],
                    before_hash="a",
                    after_hash="b",
                ),
                "preview_mutation",
            ),
            (
                _minimal_artifact(
                    requested=[{"name": "apply_edit", "arguments": {}}],
                    executed=[],
                    before_hash="a",
                    after_hash="a",
                ),
                "raw_legacy_tool_call",
            ),
            (
                _minimal_artifact(
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
                ),
                "failed_validation_commit",
            ),
        ]
        for artifact, expected in cases:
            with self.subTest(expected=expected):
                result = judge_artifact(artifact)
                self.assertFalse(result["passed"])
                self.assertIn(expected, {event["event"] for event in result["forbidden_events"]})

    def test_ollama_key_redaction_and_network_disabled_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text("ollama_key=super-secret-test-value\n", encoding="utf-8")
            environ: dict[str, str] = {}
            readiness = prepare_ollama_cloud_environment(env_path=env_path, environ=environ)
            self.assertTrue(readiness["cloud_key_present"])
            self.assertEqual(environ.get("OLLAMA_API_KEY"), "super-secret-test-value")

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
            prior_conversation=[{"role": "assistant", "content": "Visible prior assistant text."}],
        )
        self.assertIn("Ask for a harmless graph explanation.", prompt)
        self.assertNotIn("expected_final_state", prompt)
        self.assertNotIn("expected_graph_delta", prompt)
        self.assertNotIn("hidden-pass-token", prompt)

    def test_ollama_disabled_artifact_is_infra_failure_not_agent_failure(self) -> None:
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

    def test_repeated_ollama_config_uses_one_run_and_redacts_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "schema_version": "2026-05-15.phase5-ollama-gameplay-config-v1",
                        "model": "gemma3:4b",
                        "provider": "cloud",
                        "temperature": 0.0,
                        "seed": 100,
                        "n_runs": 1,
                        "max_turns": 1,
                        "scenarios": ["natural_read_only_explain"],
                    }
                ),
                encoding="utf-8",
            )
            artifact_dir = Path(tmpdir) / "artifacts"
            report = run_repeated_ollama_config(
                config_path=config_path,
                artifact_dir=artifact_dir,
                enable_ollama_network=False,
            )
            text = (artifact_dir / "aggregate_report.json").read_text(encoding="utf-8")

        self.assertEqual(report["schema_version"], "2026-05-15.phase5-ollama-aggregate-v1")
        self.assertEqual(report["total_runs"], 1)
        self.assertEqual(report["failure_categories"], {"infra_failure": 1})
        self.assertNotIn("super-secret-test-value", text)
        self.assertNotIn("OLLAMA_API_KEY", text)
        self.assertNotIn("ollama_key", text)

    def test_failure_attribution_and_non_llm_judge(self) -> None:
        forbidden = _minimal_artifact(
            requested=[{"name": "apply_edit", "arguments": {}}],
            executed=[],
            before_hash="a",
            after_hash="a",
        )
        forbidden["judge"] = judge_artifact(forbidden)
        self.assertEqual(classify_failure(forbidden), "forbidden_event")

        report = aggregate_ollama_runs(
            [forbidden],
            config={"model": "test", "provider": "cloud"},
            artifact_dir=Path("/tmp/grc_agent_test_artifacts"),
        )
        self.assertEqual(report["raw_legacy_attempt_count"], 1)
        self.assertIn(classify_failure(forbidden), FAILURE_CATEGORIES)

        source = (PRODUCTION_DIR / "gameplay_judge.py").read_text(encoding="utf-8")
        self.assertNotIn("OllamaUserClient", source)
        self.assertNotIn("run_bounded_llama_turn", source)
        self.assertNotIn("LlamaServerClient", source)


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
