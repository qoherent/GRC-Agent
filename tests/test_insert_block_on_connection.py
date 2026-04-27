"""Unit tests for the insert_block_on_connection edit primitive.

Tests the transaction layer and validation layer without requiring a live model.
"""

from __future__ import annotations

import os
import shutil
import unittest
from pathlib import Path

from grc_agent.agent import GrcAgent
from grc_agent.flowgraph_session import FlowgraphSession

FIXTURE = Path(__file__).resolve().parent / "data" / "random_bit_generator.grc"
TX_STAGE0 = Path("/usr/share/gnuradio/examples/digital/packet/tx_stage0.grc")


def _error_contains(result: dict, text: str) -> bool:
    """Check error messages and codes for text."""
    errs = result.get("errors", [])
    for e in errs:
        combined = (e.get("message", "") + " " + e.get("code", "")).lower()
        if text in combined:
            return True
    return False


class InsertBlockTests(unittest.TestCase):
    """Happy path and rejection tests for insert_block_on_connection."""

    def _load_agent(self) -> tuple[GrcAgent, FlowgraphSession]:
        session = FlowgraphSession()
        session.load(FIXTURE)
        agent = GrcAgent(session)
        return agent, session

    def test_insert_head_into_stream_succeeds(self) -> None:
        agent, _session = self._load_agent()
        r = agent.execute_tool("apply_edit", {
            "transaction": {
                "op_type": "insert_block_on_connection",
                "connection_id": "analog_random_source_x_0:0->blocks_throttle2_0:0",
                "block_type": "blocks_head",
                "instance_name": "head_0",
                "params": {"type": "byte", "num_items": "1024"},
            }
        })
        self.assertTrue(r.get("ok"), r.get("message"))
        self.assertIn("head_0", r.get("affected_blocks", []))

    def test_insert_throttle_into_stream_succeeds(self) -> None:
        agent, _session = self._load_agent()
        r = agent.execute_tool("apply_edit", {
            "transaction": {
                "op_type": "insert_block_on_connection",
                "connection_id": "analog_random_source_x_0:0->blocks_throttle2_0:0",
                "block_type": "blocks_throttle2",
                "instance_name": "throttle_insert",
                "params": {"type": "byte", "samples_per_second": "32000"},
            }
        })
        self.assertTrue(r.get("ok"), r.get("message"))

    def test_invalid_connection_id_rejects(self) -> None:
        agent, _session = self._load_agent()
        r = agent.execute_tool("apply_edit", {
            "transaction": {
                "op_type": "insert_block_on_connection",
                "connection_id": "nonexistent:0->fake:0",
                "block_type": "blocks_head",
                "instance_name": "head_0",
                "params": {"type": "float", "num_items": "1024"},
            }
        })
        self.assertFalse(r.get("ok"))
        self.assertTrue(_error_contains(r, "connection not found"), r.get("errors", []))

    def test_duplicate_instance_name_rejects(self) -> None:
        agent, _session = self._load_agent()
        r = agent.execute_tool("apply_edit", {
            "transaction": {
                "op_type": "insert_block_on_connection",
                "connection_id": "analog_random_source_x_0:0->blocks_throttle2_0:0",
                "block_type": "blocks_head",
                "instance_name": "samp_rate",
                "params": {"type": "byte", "num_items": "1024"},
            }
        })
        self.assertFalse(r.get("ok"))
        self.assertTrue(_error_contains(r, "already exists"), r.get("errors", []))

    def test_failed_grcc_leaves_graph_unchanged(self) -> None:
        agent, session = self._load_agent()
        revision_before = session.state_revision
        agent.execute_tool("propose_edit", {
            "transaction": {
                "op_type": "insert_block_on_connection",
                "connection_id": "analog_random_source_x_0:0->blocks_throttle2_0:0",
                "block_type": "blocks_head",
                "instance_name": "head_preview",
                "params": {"type": "byte", "num_items": "1024"},
            }
        })
        self.assertEqual(session.state_revision, revision_before)

    def test_preview_insert_does_not_mutate(self) -> None:
        agent, session = self._load_agent()
        revision_before = session.state_revision
        agent.execute_tool("propose_edit", {
            "transaction": {
                "op_type": "insert_block_on_connection",
                "connection_id": "analog_random_source_x_0:0->blocks_throttle2_0:0",
                "block_type": "blocks_head",
                "instance_name": "throttle_preview",
                "params": {"type": "byte", "samples_per_second": "32000"},
            }
        })
        self.assertEqual(session.state_revision, revision_before)

    def test_message_connection_rejects(self) -> None:
        if not TX_STAGE0.exists():
            self.skipTest("tx_stage0.grc not available")
        session = FlowgraphSession()
        session.load(TX_STAGE0)
        agent = GrcAgent(session)
        r = agent.execute_tool("apply_edit", {
            "transaction": {
                "op_type": "insert_block_on_connection",
                "connection_id": "blocks_message_strobe_0:strobe->pdu_random_pdu_0:generate",
                "block_type": "blocks_head",
                "instance_name": "head_msg",
                "params": {"type": "byte", "num_items": "1024"},
            }
        })
        self.assertFalse(r.get("ok"), str(r.get("message")))
        self.assertTrue(_error_contains(r, "message"), r.get("errors", []))

    def test_disabled_connection_rejects(self) -> None:
        session = FlowgraphSession()
        session.load(FIXTURE)
        raw_blocks = session.flowgraph.raw_data["blocks"]
        original_state = None
        for entry in raw_blocks:
            if isinstance(entry, dict) and entry.get("name") == "analog_random_source_x_0":
                states = entry.setdefault("states", {})
                original_state = states.get("state")
                states["state"] = "disabled"
                break
        try:
            agent = GrcAgent(session)
            r = agent.execute_tool("apply_edit", {
                "transaction": {
                    "op_type": "insert_block_on_connection",
                    "connection_id": "analog_random_source_x_0:0->blocks_throttle2_0:0",
                    "block_type": "blocks_head",
                    "instance_name": "head_disabled",
                    "params": {"type": "byte", "num_items": "1024"},
                }
            })
            self.assertFalse(r.get("ok"), str(r.get("message")))
            self.assertTrue(_error_contains(r, "disabled"), r.get("errors", []))
        finally:
            if original_state is not None:
                for entry in raw_blocks:
                    if isinstance(entry, dict) and entry.get("name") == "analog_random_source_x_0":
                        entry["states"]["state"] = original_state
                        break

    def test_multi_port_block_rejects(self) -> None:
        """Multi-input/output stream blocks must be rejected unless explicit ports given."""
        agent, _session = self._load_agent()
        r = agent.execute_tool("apply_edit", {
            "transaction": {
                "op_type": "insert_block_on_connection",
                "connection_id": "analog_random_source_x_0:0->blocks_throttle2_0:0",
                "block_type": "blocks_add_xx",
                "instance_name": "adder",
                "params": {"type": "float", "num_inputs": "2"},
            }
        })
        self.assertFalse(r.get("ok"))
        # Must contain one of the port-related error codes or messages
        self.assertTrue(
            _error_contains(r, "port")
            or _error_contains(r, "ambiguous")
            or _error_contains(r, "more than one"),
            r.get("errors", [])
        )

    def test_incompatible_type_rejects(self) -> None:
        agent, _session = self._load_agent()
        r = agent.execute_tool("apply_edit", {
            "transaction": {
                "op_type": "insert_block_on_connection",
                "connection_id": "analog_random_source_x_0:0->blocks_throttle2_0:0",
                "block_type": "blocks_head",
                "instance_name": "head_incompat",
                "params": {"type": "float", "num_items": "1024"},
            }
        })
        self.assertFalse(r.get("ok"))
        self.assertTrue(_error_contains(r, "incompatible"), r.get("errors", []))

    def test_save_roundtrip_after_insert(self) -> None:
        import tempfile
        tmpdir = tempfile.mkdtemp()
        try:
            agent, session = self._load_agent()
            r = agent.execute_tool("apply_edit", {
                "transaction": {
                    "op_type": "insert_block_on_connection",
                    "connection_id": "analog_random_source_x_0:0->blocks_throttle2_0:0",
                    "block_type": "blocks_head",
                    "instance_name": "head_rt",
                    "params": {"type": "byte", "num_items": "1024"},
                }
            })
            self.assertTrue(r.get("ok"), r.get("message"))
            save_path = os.path.join(tmpdir, "saved.grc")
            r = agent.execute_tool("save_graph", {"path": save_path})
            self.assertTrue(r.get("ok"))
            self.assertTrue(os.path.exists(save_path))
            session2 = FlowgraphSession()
            session2.load(Path(save_path))
            r2 = agent.execute_tool("validate_graph", {})
            self.assertTrue(r2.get("ok") and r2.get("valid"))
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
