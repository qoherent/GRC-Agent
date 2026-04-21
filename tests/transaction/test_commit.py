"""Direct tests for transaction commit payload builders."""

from pathlib import Path
import unittest

from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.transaction.commit import (
    build_apply_failure_payload,
    build_apply_success_payload,
)
from grc_agent.transaction.edit import AffectedChanges


class TransactionCommitPayloadTests(unittest.TestCase):
    """Exercise the public payload builders directly."""

    def _fixture_path(self) -> Path:
        return Path(__file__).resolve().parents[1] / "data" / "random_bit_generator.grc"

    def _load_session(self) -> FlowgraphSession:
        session = FlowgraphSession()
        session.load(self._fixture_path())
        session.last_validation_ok = True
        session.last_validation_returncode = 0
        session.last_validation_stdout = "validated"
        session.last_validation_stderr = ""
        return session

    def test_build_apply_success_payload_includes_commit_metadata(self) -> None:
        session = self._load_session()
        operations = [
            {
                "op_type": "update_params",
                "instance_name": "samp_rate",
                "params": {"value": "48000"},
            }
        ]
        warnings = [{"code": "note", "message": "minor"}]

        payload = build_apply_success_payload(
            session=session,
            normalized_operations=operations,
            warnings=warnings,
            affected_changes=AffectedChanges(
                blocks=("samp_rate",),
                connections=(("src", 0, "dst", 1),),
            ),
            state_revision_before=1,
        )

        self.assertTrue(payload["ok"])
        self.assertTrue(payload["applied"])
        self.assertTrue(payload["commit_eligible"])
        self.assertEqual(payload["warning_count"], 1)
        self.assertEqual(payload["errors"], [])
        self.assertEqual(payload["affected_blocks"], ["samp_rate"])
        self.assertEqual(
            payload["affected_connections"],
            [{"src_block": "src", "src_port": 0, "dst_block": "dst", "dst_port": 1}],
        )
        self.assertEqual(payload["normalized_operations"], operations)
        self.assertEqual(payload["validation"]["status"], "valid")
        self.assertEqual(payload["state_revision_before"], 1)
        self.assertEqual(payload["state_revision_after"], session.state_revision)

    def test_build_apply_failure_payload_omits_optional_fields_when_absent(self) -> None:
        session = self._load_session()

        payload = build_apply_failure_payload(
            session=session,
            message="Transaction failed preflight validation.",
            normalized_operations=[],
            warnings=[],
            state_revision_before=1,
        )

        self.assertFalse(payload["ok"])
        self.assertFalse(payload["applied"])
        self.assertFalse(payload["commit_eligible"])
        self.assertEqual(payload["message"], "Transaction failed preflight validation.")
        self.assertEqual(payload["warnings"], [])
        self.assertEqual(payload["errors"], [])
        self.assertNotIn("error_type", payload)
        self.assertNotIn("validation", payload)

    def test_build_apply_failure_payload_includes_optional_error_details(self) -> None:
        session = self._load_session()
        validation = {"status": "invalid", "returncode": 1}
        errors = [{"code": "bad_edge"}]

        payload = build_apply_failure_payload(
            session=session,
            message="Candidate graph failed GNU validation.",
            normalized_operations=[{"op_type": "remove_connection"}],
            warnings=[{"code": "warn"}],
            state_revision_before=1,
            error_type="gnu_validation_failed",
            errors=errors,
            validation=validation,
        )

        self.assertEqual(payload["error_type"], "gnu_validation_failed")
        self.assertEqual(payload["errors"], errors)
        self.assertEqual(payload["error_count"], 1)
        self.assertEqual(payload["validation"], validation)


if __name__ == "__main__":
    unittest.main()
