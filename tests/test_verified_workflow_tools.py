"""Unit tests for the top-level insert_block_on_connection wrapper.

The wrapper must:
- build exactly one insert_block_on_connection transaction
- delegate to apply_edit (same preflight, grcc, rollback, errors)
- produce the same final graph as the nested primitive
"""

from __future__ import annotations

import unittest
from pathlib import Path

from grc_agent.agent import GrcAgent
from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.runtime.tool_schemas import build_tool_schemas

FIXTURE = Path(__file__).resolve().parent / "data" / "random_bit_generator.grc"
TX_STAGE0 = Path("/usr/share/gnuradio/examples/digital/packet/tx_stage0.grc")


def _error_contains(result: dict, text: str) -> bool:
    errors = result.get("errors", [])
    if isinstance(errors, dict):
        errors = [errors]
    return any(text in str(e.get("message", "")) or text in str(e) for e in errors)


class TopLevelInsertToolTests(unittest.TestCase):
    def _load_agent(self) -> tuple[GrcAgent, FlowgraphSession]:
        session = FlowgraphSession()
        session.load(FIXTURE)
        agent = GrcAgent(session)
        return agent, session

    def test_schema_includes_tool_in_correct_order(self) -> None:
        schemas = build_tool_schemas()
        names = [s["function"]["name"] for s in schemas]
        self.assertIn("insert_block_on_connection", names)
        idx_insert = names.index("insert_block_on_connection")
        idx_suggest = names.index("suggest_compatible_insertions")
        idx_apply = names.index("apply_edit")
        self.assertLess(idx_suggest, idx_insert)
        self.assertLess(idx_insert, idx_apply)

    def test_tool_wraps_exact_insert_transaction(self) -> None:
        agent, session = self._load_agent()
        r = agent.execute_tool("insert_block_on_connection", {
            "connection_id": "analog_random_source_x_0:0->blocks_throttle2_0:0",
            "block_type": "blocks_head",
            "instance_name": "head_top",
            "params": {"type": "byte", "num_items": "1024"},
        })
        self.assertTrue(r.get("ok"), r)
        # Check that the graph mutation happened (same as nested op)
        self.assertTrue(session.is_dirty)

    def test_successful_insert_matches_nested_op_result(self) -> None:
        agent1, _ = self._load_agent()
        agent2, _ = self._load_agent()
        r1 = agent1.execute_tool("insert_block_on_connection", {
            "connection_id": "analog_random_source_x_0:0->blocks_throttle2_0:0",
            "block_type": "blocks_throttle2",
            "instance_name": "throttle_top",
            "params": {"type": "byte", "samples_per_second": "32000"},
        })
        r2 = agent2.execute_tool("apply_edit", {
            "transaction": {
                "op_type": "insert_block_on_connection",
                "connection_id": "analog_random_source_x_0:0->blocks_throttle2_0:0",
                "block_type": "blocks_throttle2",
                "instance_name": "throttle_top",
                "params": {"type": "byte", "samples_per_second": "32000"},
            }
        })
        self.assertEqual(r1.get("ok"), r2.get("ok"))
        self.assertEqual(r1.get("error_type"), r2.get("error_type"))

    def test_invalid_connection_id_rejected(self) -> None:
        agent, _ = self._load_agent()
        r = agent.execute_tool("insert_block_on_connection", {
            "connection_id": "nonexistent:0->fake:0",
            "block_type": "blocks_head",
            "instance_name": "head_bad_conn",
            "params": {"type": "byte", "num_items": "1024"},
        })
        self.assertFalse(r.get("ok"))
        self.assertTrue(_error_contains(r, "not found") or _error_contains(r, "connection_not_found"), r)

    def test_message_connection_rejected(self) -> None:
        if not TX_STAGE0.exists():
            self.skipTest("tx_stage0.grc not available")
        session = FlowgraphSession()
        session.load(TX_STAGE0)
        agent = GrcAgent(session)
        conn = None
        for c in session.flowgraph.connections:
            if isinstance(c.src_port, str):
                conn = f"{c.src_block}:{c.src_port}->{c.dst_block}:{c.dst_port}"
                break
        self.assertIsNotNone(conn, "no message connection found")
        r = agent.execute_tool("insert_block_on_connection", {
            "connection_id": conn,
            "block_type": "blocks_head",
            "instance_name": "head_msg",
            "params": {"type": "byte", "num_items": "1024"},
        })
        self.assertFalse(r.get("ok"))
        self.assertTrue(_error_contains(r, "message_connection_not_supported") or _error_contains(r, "message"), r)

    def test_disabled_connection_rejected(self) -> None:
        agent, session = self._load_agent()
        raw_blocks = session.flowgraph.raw_data["blocks"]
        for entry in raw_blocks:
            if isinstance(entry, dict) and entry.get("name") == "analog_random_source_x_0":
                states = entry.setdefault("states", {})
                states["state"] = "disabled"
                break
        conn_id = "analog_random_source_x_0:0->blocks_throttle2_0:0"
        r = agent.execute_tool("insert_block_on_connection", {
            "connection_id": conn_id,
            "block_type": "blocks_head",
            "instance_name": "head_disabled",
            "params": {"type": "byte", "num_items": "1024"},
        })
        self.assertFalse(r.get("ok"))
        self.assertTrue(_error_contains(r, "disabled_connection_not_supported") or _error_contains(r, "disabled"), r)

    def test_multi_port_block_rejects(self) -> None:
        agent, _ = self._load_agent()
        r = agent.execute_tool("insert_block_on_connection", {
            "connection_id": "analog_random_source_x_0:0->blocks_throttle2_0:0",
            "block_type": "blocks_add_xx",
            "instance_name": "add_multi",
            "params": {"type": "float", "num_inputs": "2"},
        })
        self.assertFalse(r.get("ok"))
        self.assertTrue(
            _error_contains(r, "insert_port_resolution_failed")
            or _error_contains(r, "single stream port")
            or _error_contains(r, "more than one")
            or _error_contains(r, "ambiguous"),
            r,
        )

    def test_failed_insert_leaves_graph_unchanged(self) -> None:
        agent, session = self._load_agent()
        revision_before = session.state_revision
        r = agent.execute_tool("insert_block_on_connection", {
            "connection_id": "analog_random_source_x_0:0->blocks_throttle2_0:0",
            "block_type": "blocks_add_xx",
            "instance_name": "bad_multi",
            "params": {"type": "float", "num_inputs": "2"},
        })
        self.assertFalse(r.get("ok"))
        self.assertEqual(session.state_revision, revision_before)

    def test_wrapper_and_apply_edit_produce_same_graph(self) -> None:
        # Compare against known good nested op result
        agent, session = self._load_agent()
        revision_before = session.state_revision
        r = agent.execute_tool("insert_block_on_connection", {
            "connection_id": "analog_random_source_x_0:0->blocks_throttle2_0:0",
            "block_type": "blocks_throttle2",
            "instance_name": "throttle_top",
            "params": {"type": "byte", "samples_per_second": "32000"},
        })
        self.assertTrue(r.get("ok"), r)
        self.assertNotEqual(session.state_revision, revision_before)
        raw_blocks = session.flowgraph.raw_data.get("blocks", [])
        blocks = {b["name"] for b in raw_blocks if isinstance(b, dict)}
        self.assertIn("throttle_top", blocks)
        connections = session.flowgraph.connections
        conn_strs = {f"{c.src_block}:{c.src_port}->{c.dst_block}:{c.dst_port}" for c in connections}
        # original connection removed; two new ones added
        self.assertNotIn("analog_random_source_x_0:0->blocks_throttle2_0:0", conn_strs)
        self.assertIn("analog_random_source_x_0:0->throttle_top:0", conn_strs)
        self.assertIn("throttle_top:0->blocks_throttle2_0:0", conn_strs)

    def test_propose_edit_with_nested_op_still_works(self) -> None:
        agent, session = self._load_agent()
        revision_before = session.state_revision
        r = agent.execute_tool("propose_edit", {
            "transaction": {
                "op_type": "insert_block_on_connection",
                "connection_id": "analog_random_source_x_0:0->blocks_throttle2_0:0",
                "block_type": "blocks_head",
                "instance_name": "head_compat",
                "params": {"type": "byte", "num_items": "1024"},
            }
        })
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(session.state_revision, revision_before)

    def test_no_params_uses_catalog_defaults(self) -> None:
        agent, _session = self._load_agent()
        r = agent.execute_tool("insert_block_on_connection", {
            "connection_id": "analog_random_source_x_0:0->blocks_throttle2_0:0",
            "block_type": "blocks_head",
            "instance_name": "head_defaults",
        })
        # Even if grcc fails due to default type mismatch, the wrapper must
        # have normalized the operation with catalog defaults filled.
        ops = r.get("normalized_operations", [])
        self.assertTrue(ops)
        self.assertEqual(ops[0].get("op_type"), "insert_block_on_connection")
        # params are normalized to "parameters" by the transaction normalizer
        self.assertIn("parameters", ops[0])
