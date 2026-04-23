"""Apply/proposal tests for the Phase 5 transaction package."""

from __future__ import annotations

import copy
from pathlib import Path
import unittest

from grc_agent import FlowgraphSession, apply_edit, propose_edit


class TransactionApplyTests(unittest.TestCase):
    """Validate transaction proposal and successful apply behavior."""

    def _fixture_path(self) -> Path:
        return Path(__file__).resolve().parents[1] / "data" / "random_bit_generator.grc"

    def _load_session(self) -> FlowgraphSession:
        session = FlowgraphSession()
        session.load(self._fixture_path())
        return session

    def test_propose_edit_returns_preflight_plan(self) -> None:
        session = self._load_session()

        payload = propose_edit(
            session,
            {"op_type": "update_params", "instance_name": "samp_rate", "params": {"value": "48000"}},
        )

        self.assertTrue(payload["ok"])
        self.assertFalse(payload["commit_eligible"])
        self.assertEqual(payload["error_count"], 0)
        self.assertEqual(payload["warning_count"], 0)
        self.assertEqual(payload["planned_operations"], payload["normalized_operations"])

    def test_apply_edit_updates_live_session_after_final_validation(self) -> None:
        session = self._load_session()

        payload = apply_edit(
            session,
            {"op_type": "update_params", "instance_name": "samp_rate", "params": {"value": "48000"}},
        )

        self.assertTrue(payload["ok"])
        self.assertTrue(payload["applied"])
        self.assertTrue(payload["commit_eligible"])
        self.assertTrue(payload["dirty"])
        self.assertEqual(payload["validation"]["status"], "valid")
        self.assertEqual(payload["affected_blocks"], ["samp_rate"])
        self.assertEqual(payload["affected_connections"], [])
        self.assertGreater(payload["state_revision_after"], payload["state_revision_before"])
        assert session.flowgraph is not None
        block = next(block for block in session.flowgraph.blocks if block.instance_name == "samp_rate")
        self.assertEqual(block.params["parameters"]["value"], "48000")
        self.assertTrue(session.last_validation_ok)

    def test_apply_edit_can_add_detached_variable_block(self) -> None:
        session = self._load_session()
        original_raw = copy.deepcopy(session.flowgraph.raw_data)

        payload = apply_edit(
            session,
            {
                "op_type": "add_block",
                "instance_name": "unused_var",
                "block_type": "variable",
                "parameters": {"value": "123"},
            },
        )

        self.assertTrue(payload["ok"])
        self.assertTrue(payload["applied"])
        self.assertTrue(payload["dirty"])
        self.assertEqual(payload["validation"]["status"], "valid")
        self.assertEqual(payload["affected_blocks"], ["unused_var"])
        assert session.flowgraph is not None
        self.assertIn("unused_var", [block.instance_name for block in session.flowgraph.blocks])
        self.assertNotEqual(session.flowgraph.raw_data, original_raw)

    def test_apply_edit_can_disable_detached_variable_block(self) -> None:
        session = self._load_session()
        session.add_block("unused_var", "variable", {"value": "123"})

        payload = apply_edit(
            session,
            {
                "op_type": "update_states",
                "instance_name": "unused_var",
                "state": "disabled",
            },
        )

        self.assertTrue(payload["ok"])
        self.assertTrue(payload["applied"])
        self.assertEqual(payload["affected_blocks"], ["unused_var"])
        assert session.flowgraph is not None
        block = next(block for block in session.flowgraph.blocks if block.instance_name == "unused_var")
        self.assertEqual(block.params["states"]["state"], "disabled")
        self.assertTrue(session.last_validation_ok)


if __name__ == "__main__":
    unittest.main()
