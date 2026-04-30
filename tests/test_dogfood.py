import json
from pathlib import Path
import tempfile
import unittest

from grc_agent.dogfood import (
    record_dogfood_case,
    summarize_dogfood_cases,
)


class DogfoodIntakeTests(unittest.TestCase):
    def test_record_sanitizes_all_user_text_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            intake_path = Path(tmpdir) / "dogfood.jsonl"
            payload = record_dogfood_case(
                prompt="Edit /home/me/radio/private_flow.grc then save private_flow.grc",
                graph="/home/me/radio/private_flow.grc",
                source="user_graph",
                task_type="param_edit",
                failure_category="routing_failure",
                severity="medium",
                expected="change block in /home/me/radio/private_flow.grc",
                actual="looked at private_flow.grc and failed",
                actual_tools=["apply_edit", "bad tool!"],
                graph_delta="delta from /home/me/radio/private_flow.grc",
                validation_state="invalid in private_flow.grc",
                save_state="saved to /tmp/private_flow.grc",
                notes="Observed while using /home/me/radio/private_flow.grc",
                intake_path=intake_path,
            )

            self.assertTrue(payload["ok"], payload)
            record = payload["record"]
            serialized = json.dumps(record, sort_keys=True)

        self.assertEqual(record["graph_ref"], "<user_graph>")
        self.assertNotIn("/home/me", serialized)
        self.assertNotIn("private_flow.grc", serialized)
        self.assertIn("<path>", serialized)
        self.assertIn("<grc_file>", serialized)
        self.assertEqual(record["actual_tools"], ["apply_edit", "badtool"])

    def test_invalid_enum_returns_error_payload_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            intake_path = Path(tmpdir) / "dogfood.jsonl"
            payload = record_dogfood_case(
                prompt="try task",
                source="webhook",
                intake_path=intake_path,
            )

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error_type"], "invalid_dogfood_source")
        self.assertFalse(intake_path.exists())

    def test_empty_report_is_safe(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            payload = summarize_dogfood_cases(intake_path=Path(tmpdir) / "missing.jsonl")

        self.assertTrue(payload["ok"], payload)
        self.assertEqual(payload["total_records"], 0)
        self.assertEqual(payload["cluster_count"], 0)
        self.assertEqual(payload["warnings"], ["dogfood_intake_empty"])

    def test_report_requires_repeated_or_cross_source_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            intake_path = Path(tmpdir) / "dogfood.jsonl"
            record_dogfood_case(
                prompt="change cutoff fails",
                source="real_user",
                task_type="param_edit",
                failure_category="argument_copying_failure",
                intake_path=intake_path,
            )
            one_record = summarize_dogfood_cases(intake_path=intake_path)
            record_dogfood_case(
                prompt="change cutoff fails",
                source="manual_review",
                task_type="param_edit",
                failure_category="argument_copying_failure",
                intake_path=intake_path,
            )
            two_sources = summarize_dogfood_cases(intake_path=intake_path)

        self.assertEqual(one_record["clusters"][0]["recommendation"], "needs_more_evidence")
        self.assertEqual(two_sources["clusters"][0]["recommendation"], "candidate_generic_gap")

    def test_same_expected_target_does_not_merge_unrelated_wording(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            intake_path = Path(tmpdir) / "dogfood.jsonl"
            record_dogfood_case(
                prompt="waveform viewer clarification was confusing",
                source="real_user",
                task_type="clarification",
                failure_category="confusing_clarification",
                expected="qtgui_time_sink_x",
                intake_path=intake_path,
            )
            record_dogfood_case(
                prompt="signal level edit copied wrong argument",
                source="real_user",
                task_type="clarification",
                failure_category="confusing_clarification",
                expected="qtgui_time_sink_x",
                intake_path=intake_path,
            )
            payload = summarize_dogfood_cases(intake_path=intake_path)

        self.assertEqual(payload["total_records"], 2)
        self.assertEqual(payload["cluster_count"], 2)

    def test_stop_the_line_cluster_blocks_normal_recommendation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            intake_path = Path(tmpdir) / "dogfood.jsonl"
            record_dogfood_case(
                prompt="preview mutated graph",
                source="manual_review",
                task_type="param_edit",
                failure_category="unsafe_mutation_risk",
                severity="stop_the_line",
                intake_path=intake_path,
            )
            payload = summarize_dogfood_cases(intake_path=intake_path)

        self.assertEqual(payload["clusters"][0]["recommendation"], "stop_and_investigate")


if __name__ == "__main__":
    unittest.main()
