"""Rollback and unchanged-live-session tests for the Phase 5 transaction package."""

from __future__ import annotations

import copy
import unittest
from pathlib import Path
from unittest import mock

from grc_agent import FlowgraphSession, apply_edit
from grc_agent.transaction import capture_session_state, restore_session_state


class TransactionRollbackTests(unittest.TestCase):
    """Validate rollback helpers and atomic unchanged-live-session behavior."""

    def _fixture_path(self) -> Path:
        return Path(__file__).resolve().parents[1] / "data" / "random_bit_generator.grc"

    def _load_session(self) -> FlowgraphSession:
        session = FlowgraphSession()
        session.load(self._fixture_path())
        return session

    def test_rollback_helpers_restore_previous_live_state(self) -> None:
        session = self._load_session()
        snapshot = capture_session_state(session)

        session.set_param("samp_rate", "value", "48000")
        self.assertTrue(session.is_dirty)

        restore_session_state(session, snapshot)

        self.assertFalse(session.is_dirty)
        assert session.flowgraph is not None
        block = next(block for block in session.flowgraph.blocks if block.name == "samp_rate")
        self.assertEqual(block.params["parameters"]["value"], "32000")

    def test_apply_edit_preflight_failure_leaves_live_state_unchanged(self) -> None:
        session = self._load_session()
        original_raw = copy.deepcopy(session.flowgraph.export_data())

        payload = apply_edit(
            session,
            {
                "op_type": "add_connection",
                "src_block": "blocks_throttle2_0",
                "src_port": 0,
                "dst_block": "blocks_char_to_float_0",
                "dst_port": 0,
            },
        )

        self.assertFalse(payload["ok"])
        self.assertFalse(payload["applied"])
        self.assertEqual(payload["error_type"], "preflight_rejected")
        self.assertEqual(payload["errors"][0]["code"], "duplicate_connection")
        assert session.flowgraph is not None
        self.assertEqual(session.flowgraph.export_data(), original_raw)
        self.assertIsNone(session.last_validation_ok)

    def test_apply_edit_commit_swap_failure_leaves_live_state_unchanged(self) -> None:
        session = self._load_session()
        assert session.flowgraph is not None
        original_raw = copy.deepcopy(session.flowgraph.export_data())
        original_revision = session.state_revision
        original_dirty = session.is_dirty

        with mock.patch(
            "grc_agent.transaction.commit_candidate_session",
            side_effect=RuntimeError("injected commit swap failure"),
        ):
            payload = apply_edit(
                session,
                {
                    "op_type": "update_params",
                    "instance_name": "samp_rate",
                    "params": {"value": "48000"},
                },
            )

        self.assertFalse(payload["ok"])
        self.assertFalse(payload["applied"])
        self.assertEqual(payload["error_type"], "internal_error")
        self.assertIn("injected commit swap failure", payload["message"])
        self.assertEqual(payload["state_revision_after"], original_revision)
        self.assertEqual(session.state_revision, original_revision)
        self.assertEqual(session.is_dirty, original_dirty)
        assert session.flowgraph is not None
        self.assertEqual(session.flowgraph.export_data(), original_raw)


if __name__ == "__main__":
    unittest.main()
