"""Tests for transaction planner isolation and multi-operation coverage."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from grc_agent import FlowgraphSession, apply_edit, propose_edit


class TransactionPlannerTests(unittest.TestCase):
    """Isolated planner tests."""

    def _fixture_path(self) -> Path:
        return Path(__file__).resolve().parents[1] / "data" / "random_bit_generator.grc"

    def _load_session(self) -> FlowgraphSession:
        session = FlowgraphSession()
        session.load(self._fixture_path())
        return session

    def test_propose_edit_invalid_transaction_returns_not_ok(self) -> None:
        session = self._load_session()

        payload = propose_edit(
            session,
            {
                "op_type": "update_params",
                "instance_name": "nonexistent_block",
                "params": {"value": "1"},
            },
        )

        self.assertFalse(payload["ok"])
        self.assertFalse(payload["commit_eligible"])
        self.assertGreater(payload["error_count"], 0)

    def test_propose_edit_does_not_mutate_session(self) -> None:
        session = self._load_session()
        revision_before = session.state_revision
        dirty_before = session.is_dirty

        propose_edit(
            session,
            {
                "op_type": "update_params",
                "instance_name": "samp_rate",
                "params": {"value": "48000"},
            },
        )

        self.assertEqual(session.state_revision, revision_before)
        self.assertEqual(session.is_dirty, dirty_before)

    def test_propose_edit_includes_dirty_and_revision_state(self) -> None:
        session = self._load_session()

        payload = propose_edit(
            session,
            {
                "op_type": "update_params",
                "instance_name": "samp_rate",
                "params": {"value": "48000"},
            },
        )

        self.assertFalse(payload["dirty"])
        self.assertEqual(payload["state_revision"], session.state_revision)


class MultiOperationTransactionTests(unittest.TestCase):
    """Tests for multi-operation transactions."""

    def _fixture_path(self) -> Path:
        return Path(__file__).resolve().parents[1] / "data" / "random_bit_generator.grc"

    def _load_session(self) -> FlowgraphSession:
        session = FlowgraphSession()
        session.load(self._fixture_path())
        return session

    def test_apply_edit_multi_param_update(self) -> None:
        session = self._load_session()

        payload = apply_edit(
            session,
            [
                {
                    "op_type": "update_params",
                    "instance_name": "samp_rate",
                    "params": {"value": "48000"},
                },
                {
                    "op_type": "update_params",
                    "instance_name": "blocks_throttle2_0",
                    "params": {"maximum": "0.2"},
                },
            ],
        )

        self.assertTrue(payload["ok"])
        self.assertTrue(payload["applied"])
        self.assertEqual(payload["validation"]["status"], "valid")
        self.assertIn("samp_rate", payload["affected_blocks"])
        self.assertIn("blocks_throttle2_0", payload["affected_blocks"])

    def test_apply_edit_repaired_variable_removal(self) -> None:
        session = self._load_session()

        payload = apply_edit(
            session,
            [
                {
                    "op_type": "update_params",
                    "instance_name": "blocks_throttle2_0",
                    "params": {"samples_per_second": "32000"},
                },
                {
                    "op_type": "update_params",
                    "instance_name": "qtgui_time_sink_x_0",
                    "params": {"srate": "32000"},
                },
                {"op_type": "remove_block", "instance_name": "samp_rate"},
            ],
        )

        self.assertTrue(payload["ok"])
        self.assertTrue(payload["applied"])
        self.assertEqual(payload["validation"]["status"], "valid")


    def test_apply_edit_remove_connection_fails_on_invalid_graph(self) -> None:
        session = self._load_session()

        payload = apply_edit(
            session,
            [
                {
                    "op_type": "remove_connection",
                    "src_block": "analog_random_source_x_0",
                    "src_port": 0,
                    "dst_block": "blocks_throttle2_0",
                    "dst_port": 0,
                },
            ],
        )

        self.assertFalse(payload["ok"])
        self.assertFalse(payload["applied"])
        self.assertEqual(payload["error_type"], "gnu_validation_failed")



if __name__ == "__main__":
    unittest.main()
