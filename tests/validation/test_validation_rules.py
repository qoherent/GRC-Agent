"""Ordered staged-transaction tests for preflight validation."""

from __future__ import annotations

from pathlib import Path
import unittest

from grc_agent import FlowgraphSession, preflight_transaction


class PreflightValidationRuleTests(unittest.TestCase):
    """Exercise the staged snapshot behavior Phase 5 depends on."""

    def _fixture_path(self) -> Path:
        return Path(__file__).resolve().parents[1] / "data" / "random_bit_generator.grc"

    def _load_session(self) -> FlowgraphSession:
        session = FlowgraphSession()
        session.load(self._fixture_path())
        return session

    def test_remove_block_referenced_block_is_rejected(self) -> None:
        session = self._load_session()

        payload = preflight_transaction(
            session,
            {
                "op_type": "remove_block",
                "instance_name": "samp_rate",
            },
        )

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["errors"][0]["code"], "block_still_referenced")

    def test_repaired_variable_removal_passes_on_staged_snapshot(self) -> None:
        session = self._load_session()

        payload = preflight_transaction(
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
        self.assertEqual(payload["error_count"], 0)

    def test_disconnect_then_reconnect_same_edge_passes(self) -> None:
        session = self._load_session()

        payload = preflight_transaction(
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
        self.assertEqual(payload["warning_count"], 0)

    def test_missing_connection_is_rejected(self) -> None:
        session = self._load_session()

        payload = preflight_transaction(
            session,
            {
                "op_type": "remove_connection",
                "src_block": "does_not_exist",
                "src_port": 0,
                "dst_block": "blocks_char_to_float_0",
                "dst_port": 0,
            },
        )

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["errors"][0]["code"], "connection_not_found")


if __name__ == "__main__":
    unittest.main()
