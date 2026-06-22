"""Commit and final-validation tests for the Phase 5 transaction package."""

from __future__ import annotations

import copy
import unittest
from pathlib import Path

from grc_agent import FlowgraphSession, apply_edit


class TransactionCommitTests(unittest.TestCase):
    """Validate final GNU validation and committed live-session state."""

    def _fixture_path(self) -> Path:
        return Path(__file__).resolve().parents[1] / "data" / "random_bit_generator.grc"

    def _load_session(self) -> FlowgraphSession:
        session = FlowgraphSession()
        session.load(self._fixture_path())
        return session

    def test_apply_edit_blocks_commit_when_final_gnu_validation_fails(self) -> None:
        session = self._load_session()
        original_raw = copy.deepcopy(session.flowgraph.export_data())

        payload = apply_edit(
            session,
            {
                "op_type": "update_params",
                "instance_name": "samp_rate",
                "params": {"value": "missing_rate + 1"},
            },
        )

        self.assertFalse(payload["ok"])
        self.assertFalse(payload["applied"])
        self.assertFalse(payload["commit_eligible"])
        self.assertEqual(payload["error_type"], "gnu_validation_failed")
        self.assertEqual(payload["validation"]["status"], "invalid")
        assert session.flowgraph is not None
        self.assertEqual(session.flowgraph.export_data(), original_raw)
        self.assertIsNone(session.last_validation_ok)

    def test_apply_edit_commits_repaired_variable_removal_transaction(self) -> None:
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
                {
                    "op_type": "remove_block",
                    "instance_name": "samp_rate",
                },
            ],
        )

        self.assertTrue(payload["ok"])
        self.assertTrue(payload["applied"])
        self.assertTrue(payload["commit_eligible"])
        self.assertTrue(payload["dirty"])
        self.assertEqual(payload["validation"]["status"], "valid")
        self.assertEqual(
            payload["affected_blocks"],
            ["blocks_throttle2_0", "qtgui_time_sink_x_0", "samp_rate"],
        )
        assert session.flowgraph is not None
        self.assertNotIn("samp_rate", [block.name for block in session.flowgraph.blocks])

    def test_net_zero_rewire_keeps_clean_dirty_state(self) -> None:
        """Restored regression: a net-zero rewire (remove + re-add the same edge)
        must preserve clean dirty=False and NOT bump state_revision.
        """
        session = self._load_session()
        original_graph_id = session.graph_id()

        payload = apply_edit(
            session,
            [
                {
                    "op_type": "remove_connection",
                    "src_block": "blocks_throttle2_0",
                    "src_port": 0,
                    "dst_block": "blocks_char_to_float_0",
                    "dst_port": 0,
                },
                {
                    "op_type": "add_connection",
                    "src_block": "blocks_throttle2_0",
                    "src_port": 0,
                    "dst_block": "blocks_char_to_float_0",
                    "dst_port": 0,
                },
            ],
        )

        self.assertTrue(payload["ok"])
        self.assertTrue(payload["applied"])
        self.assertFalse(payload["dirty"])
        self.assertEqual(payload["graph_id"], original_graph_id)
        self.assertEqual(payload["state_revision_after"], payload["state_revision_before"])


if __name__ == "__main__":
    unittest.main()
