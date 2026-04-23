"""Focused happy-path and direct rule tests for preflight validation."""

from __future__ import annotations

import copy
from pathlib import Path
import unittest

from grc_agent import FlowgraphSession, preflight_transaction


class PreflightTransactionTests(unittest.TestCase):
    """Validate the core Phase 4 success and failure paths."""

    def _fixture_path(self) -> Path:
        return Path(__file__).resolve().parents[1] / "data" / "random_bit_generator.grc"

    def _load_session(self) -> FlowgraphSession:
        session = FlowgraphSession()
        session.load(self._fixture_path())
        return session

    def test_valid_update_params_passes_without_mutating_live_session(self) -> None:
        session = self._load_session()
        original_raw = copy.deepcopy(session.flowgraph.raw_data)

        payload = preflight_transaction(
            session,
            {
                "op_type": "update_params",
                "instance_name": "samp_rate",
                "params": {"value": "48000"},
            },
        )

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["error_count"], 0)
        self.assertEqual(payload["warning_count"], 0)
        self.assertEqual(
            payload["normalized_operations"],
            [
                {
                    "op_type": "update_params",
                    "instance_name": "samp_rate",
                    "params": {"value": "48000"},
                }
            ],
        )
        self.assertFalse(session.is_dirty)
        assert session.flowgraph is not None
        self.assertEqual(session.flowgraph.raw_data, original_raw)

    def test_valid_update_states_passes_without_mutating_live_session(self) -> None:
        session = self._load_session()
        original_raw = copy.deepcopy(session.flowgraph.raw_data)

        payload = preflight_transaction(
            session,
            {
                "op_type": "update_states",
                "instance_name": "samp_rate",
                "state": "disabled",
            },
        )

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["normalized_operations"], [
            {
                "op_type": "update_states",
                "instance_name": "samp_rate",
                "state": "disabled",
            }
        ])
        self.assertFalse(session.is_dirty)
        assert session.flowgraph is not None
        self.assertEqual(session.flowgraph.raw_data, original_raw)

    def test_invalid_enum_value_is_rejected(self) -> None:
        session = self._load_session()

        payload = preflight_transaction(
            session,
            {
                "op_type": "update_params",
                "instance_name": "blocks_throttle2_0",
                "params": {"type": "bogus"},
            },
        )

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error_count"], 1)
        issue = payload["errors"][0]
        self.assertEqual(issue["op_index"], 0)
        self.assertEqual(issue["op_type"], "update_params")
        self.assertEqual(issue["field"], "params.type")
        self.assertEqual(issue["code"], "invalid_enum_value")
        self.assertIn("Valid values:", issue["hint"])

    def test_duplicate_connection_is_rejected(self) -> None:
        session = self._load_session()

        payload = preflight_transaction(
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
        self.assertEqual(payload["errors"][0]["code"], "duplicate_connection")

    def test_occupied_input_port_is_rejected(self) -> None:
        session = self._load_session()

        payload = preflight_transaction(
            session,
            {
                "op_type": "add_connection",
                "src_block": "analog_random_source_x_0",
                "src_port": 0,
                "dst_block": "blocks_char_to_float_0",
                "dst_port": 0,
            },
        )

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["errors"][0]["code"], "occupied_input_port")
        self.assertEqual(payload["errors"][0]["field"], "dst_port")

    def test_port_out_of_range_is_rejected(self) -> None:
        session = self._load_session()

        payload = preflight_transaction(
            session,
            {
                "op_type": "add_connection",
                "src_block": "analog_random_source_x_0",
                "src_port": 0,
                "dst_block": "qtgui_time_sink_x_0",
                "dst_port": 5,
            },
        )

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["errors"][0]["code"], "port_out_of_range")
        self.assertEqual(payload["errors"][0]["field"], "dst_port")

    def test_incompatible_dtype_is_rejected_after_sink_expansion(self) -> None:
        session = self._load_session()
        original_raw = copy.deepcopy(session.flowgraph.raw_data)

        payload = preflight_transaction(
            session,
            [
                {
                    "op_type": "update_params",
                    "instance_name": "qtgui_time_sink_x_0",
                    "params": {"nconnections": "2"},
                },
                {
                    "op_type": "add_connection",
                    "src_block": "analog_random_source_x_0",
                    "src_port": 0,
                    "dst_block": "qtgui_time_sink_x_0",
                    "dst_port": 1,
                },
            ],
        )

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["errors"][0]["op_index"], 1)
        self.assertEqual(payload["errors"][0]["code"], "incompatible_dtype")
        self.assertIn("Type Converters", payload["errors"][0]["hint"])
        self.assertFalse(session.is_dirty)
        assert session.flowgraph is not None
        self.assertEqual(session.flowgraph.raw_data, original_raw)

    def test_add_block_missing_value_is_rejected(self) -> None:
        session = self._load_session()

        payload = preflight_transaction(
            session,
            {
                "op_type": "add_block",
                "instance_name": "unused_var",
                "block_type": "variable",
                "parameters": {"comment": ""},
            },
        )

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["errors"][0]["code"], "missing_required_param")
        self.assertEqual(payload["errors"][0]["field"], "parameters.value")

    def test_valid_detached_variable_add_passes_without_mutating_live_session(self) -> None:
        session = self._load_session()
        original_raw = copy.deepcopy(session.flowgraph.raw_data)

        payload = preflight_transaction(
            session,
            {
                "op_type": "add_block",
                "instance_name": "unused_var",
                "block_type": "variable",
                "parameters": {"value": "123"},
            },
        )

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["error_count"], 0)
        self.assertFalse(session.is_dirty)
        assert session.flowgraph is not None
        self.assertEqual(session.flowgraph.raw_data, original_raw)

    def test_parameter_edit_revalidates_existing_connection_dtype(self) -> None:
        session = self._load_session()

        payload = preflight_transaction(
            session,
            {
                "op_type": "update_params",
                "instance_name": "qtgui_time_sink_x_0",
                "params": {"type": "complex"},
            },
        )

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["errors"][0]["code"], "incompatible_dtype")

    def test_parameter_edit_revalidates_existing_connection_vlen(self) -> None:
        session = self._load_session()

        payload = preflight_transaction(
            session,
            {
                "op_type": "update_params",
                "instance_name": "blocks_char_to_float_0",
                "params": {"vlen": "2"},
            },
        )

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["errors"][0]["code"], "incompatible_vlen")

    def test_parameter_edit_revalidates_block_asserts(self) -> None:
        session = self._load_session()

        payload = preflight_transaction(
            session,
            {
                "op_type": "update_params",
                "instance_name": "blocks_throttle2_0",
                "params": {"vlen": "0"},
            },
        )

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["errors"][0]["code"], "block_assert_failed")

    def test_duplicate_enabled_symbol_ids_are_rejected(self) -> None:
        session = self._load_session()
        assert session.flowgraph is not None
        session.flowgraph.raw_data["blocks"].append(
            {
                "name": "samp_rate",
                "id": "variable",
                "parameters": {"value": "123", "comment": ""},
                "states": {"state": "enabled"},
            }
        )

        payload = preflight_transaction(
            session,
            {
                "op_type": "update_params",
                "instance_name": "blocks_throttle2_0",
                "params": {"maximum": "0.2"},
            },
        )

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["errors"][0]["code"], "duplicate_enabled_symbol_id")


if __name__ == "__main__":
    unittest.main()
