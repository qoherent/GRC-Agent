"""Error-envelope and shape-validation tests for preflight validation."""

from __future__ import annotations

from pathlib import Path
import unittest

from grc_agent import FlowgraphSession, preflight_transaction


class PreflightValidationErrorTests(unittest.TestCase):
    """Validate stable blocking error payloads."""

    def _fixture_path(self) -> Path:
        return Path(__file__).resolve().parents[1] / "data" / "random_bit_generator.grc"

    def _load_session(self) -> FlowgraphSession:
        session = FlowgraphSession()
        session.load(self._fixture_path())
        return session

    def test_preflight_requires_loaded_session(self) -> None:
        session = FlowgraphSession()

        payload = preflight_transaction(
            session,
            {
                "op_type": "update_params",
                "instance_name": "samp_rate",
                "params": {"value": "48000"},
            },
        )

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["errors"][0]["code"], "no_flowgraph_loaded")

    def test_preflight_rejects_invalid_operations_container(self) -> None:
        session = self._load_session()

        payload = preflight_transaction(session, "not a transaction")

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["errors"][0]["op_type"], "transaction")
        self.assertEqual(payload["errors"][0]["code"], "invalid_operations")

    def test_preflight_rejects_unknown_block_id_for_add_block(self) -> None:
        session = self._load_session()

        payload = preflight_transaction(
            session,
            {
                "op_type": "add_block",
                "instance_name": "unused_var",
                "block_type": "definitely_not_a_real_block",
                "parameters": {"value": "123"},
            },
        )

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["errors"][0]["field"], "block_type")
        self.assertEqual(payload["errors"][0]["code"], "unknown_block_id")

    def test_preflight_accepts_positional_catalog_root_argument(self) -> None:
        session = self._load_session()

        payload = preflight_transaction(
            session,
            {
                "op_type": "update_params",
                "instance_name": "samp_rate",
                "params": {"value": "48000"},
            },
            None,
        )

        self.assertTrue(payload["ok"])

    def test_preflight_rejects_unknown_parameter(self) -> None:
        session = self._load_session()

        payload = preflight_transaction(
            session,
            {
                "op_type": "update_params",
                "instance_name": "samp_rate",
                "params": {"does_not_exist": "123"},
            },
        )

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["errors"][0]["code"], "parameter_not_found")
        self.assertEqual(payload["errors"][0]["field"], "params.does_not_exist")

    def test_invalid_enum_issue_shape_is_stable(self) -> None:
        session = self._load_session()

        payload = preflight_transaction(
            session,
            {
                "op_type": "update_params",
                "instance_name": "blocks_throttle2_0",
                "params": {"type": "bogus"},
            },
        )

        self.assertEqual(
            set(payload["errors"][0]),
            {"op_index", "op_type", "field", "code", "message", "hint"},
        )
        self.assertEqual(payload["warnings"], [])
        self.assertEqual(payload["warning_count"], 0)


if __name__ == "__main__":
    unittest.main()
