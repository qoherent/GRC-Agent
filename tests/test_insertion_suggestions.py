"""Unit tests for insertion suggestion helper.

Tests the core logic without requiring a live model.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from grc_agent.session.insertion_suggestions import (
    suggest_insertions,
    _port_compatible_for_insertion,
    _generate_instance_name,
    _is_hardware_or_external,
    _has_safe_defaults,
    _confidence,
    _rank_candidates,
    InsertionCandidate,
)
from grc_agent.catalog.schema import BlockDescription, NormalizedParameter, NormalizedPort
from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.agent import GrcAgent

FIXTURE = Path(__file__).resolve().parent / "data" / "random_bit_generator.grc"


class TestInsertionSuggestions(unittest.TestCase):

    def test_rejects_invalid_connection_id(self):
        session = FlowgraphSession()
        result = suggest_insertions(session, "not-a-connection-id")
        # Without a loaded graph, NO_GRAPH_LOADED is returned before connection parsing
        self.assertFalse(result.ok)
        self.assertEqual(result.error_type, "NO_GRAPH_LOADED")

    def test_rejects_invalid_connection_id_with_loaded_graph(self):
        session = FlowgraphSession()
        session.load(FIXTURE)
        result = suggest_insertions(session, "not-a-connection-id")
        self.assertFalse(result.ok)
        self.assertEqual(result.error_type, "INVALID_CONNECTION_ID")

    def test_rejects_no_graph_loaded(self):
        session = FlowgraphSession()
        result = suggest_insertions(session, "foo:0->bar:0")
        self.assertFalse(result.ok)
        self.assertEqual(result.error_type, "NO_GRAPH_LOADED")

    def test_rejects_missing_connection(self):
        session = FlowgraphSession()
        session.load(FIXTURE)
        result = suggest_insertions(session, "nonexistent:0->target:0")
        self.assertFalse(result.ok)
        self.assertEqual(result.error_type, "CONNECTION_NOT_FOUND")

    def test_returns_candidates_for_float_stream(self):
        session = FlowgraphSession()
        session.load(FIXTURE)
        conn = None
        for c in session.flowgraph.connections:
            src_block = next((b for b in session.flowgraph.blocks if b.instance_name == c.src_block), None)
            dst_block = next((b for b in session.flowgraph.blocks if b.instance_name == c.dst_block), None)
            if src_block and dst_block:
                conn = c
                break
        self.assertIsNotNone(conn)
        cid = f"{conn.src_block}:{conn.src_port}->{conn.dst_block}:{conn.dst_port}"
        result = suggest_insertions(session, cid, k=5)
        self.assertTrue(result.ok, msg=result.message)
        self.assertTrue(result.candidates)
        self.assertLessEqual(len(result.candidates), 5)
        for cand in result.candidates:
            self.assertTrue(cand.block_type)
            self.assertTrue(cand.reason)
            self.assertIn(cand.confidence, {"high", "medium", "low"})
            self.assertIsNotNone(cand.insert_tool_args)
            args = cand.insert_tool_args
            self.assertEqual(args.get("connection_id"), cid)
            self.assertEqual(args.get("block_type"), cand.block_type)
            self.assertIsInstance(args.get("instance_name"), str)
            self.assertTrue(args.get("instance_name"))
            self.assertIsInstance(args.get("params"), dict)

    def test_source_destination_specs_present(self):
        session = FlowgraphSession()
        session.load(FIXTURE)
        conn = session.flowgraph.connections[0]
        cid = f"{conn.src_block}:{conn.src_port}->{conn.dst_block}:{conn.dst_port}"
        result = suggest_insertions(session, cid, k=3)
        self.assertTrue(result.ok)
        self.assertIsNotNone(result.source)
        self.assertIsNotNone(result.destination)

    def test_helper_does_not_mutate_session(self):
        session = FlowgraphSession()
        session.load(FIXTURE)
        before_blocks = list(session.flowgraph.blocks)
        before_connections = list(session.flowgraph.connections)
        conn = session.flowgraph.connections[0]
        cid = f"{conn.src_block}:{conn.src_port}->{conn.dst_block}:{conn.dst_port}"
        suggest_insertions(session, cid, k=3)
        self.assertEqual(session.flowgraph.blocks, before_blocks)
        self.assertEqual(session.flowgraph.connections, before_connections)
        self.assertFalse(session.is_dirty)

    def test_insert_tool_args_succeeds_for_compatible_candidate(self):
        """At least one suggested candidate must succeed when inserted via the top-level tool."""
        dial_tone = Path("/usr/share/gnuradio/examples/audio/dial_tone.grc")
        if not dial_tone.exists():
            self.skipTest("dial_tone.grc not available")
        session = FlowgraphSession()
        session.load(dial_tone)
        conn = session.flowgraph.connections[0]
        cid = f"{conn.src_block}:{conn.src_port}->{conn.dst_block}:{conn.dst_port}"
        result = suggest_insertions(session, cid, k=10)
        self.assertTrue(result.ok, result.message or "")
        self.assertTrue(result.candidates)
        agent = GrcAgent(session)
        ok_found = False
        for c in result.candidates:
            r = agent.execute_tool("insert_block_on_connection", c.insert_tool_args)
            if r.get("ok"):
                ok_found = True
                break
        self.assertTrue(ok_found, f"No candidate succeeded; tried {len(result.candidates)} candidates")

    def test_insert_tool_args_instance_name_unique(self):
        session = FlowgraphSession()
        session.load(FIXTURE)
        existing = {b.instance_name for b in session.flowgraph.blocks}
        conn = session.flowgraph.connections[0]
        cid = f"{conn.src_block}:{conn.src_port}->{conn.dst_block}:{conn.dst_port}"
        result = suggest_insertions(session, cid, k=5)
        self.assertTrue(result.ok)
        names = set()
        for c in result.candidates:
            name = c.insert_tool_args.get("instance_name")
            self.assertNotIn(name, existing, f"{name} collides with existing block")
            self.assertNotIn(name, names, f"{name} collides with another candidate")
            names.add(name)

    def test_insert_tool_args_params_are_canonical(self):
        session = FlowgraphSession()
        session.load(FIXTURE)
        conn = session.flowgraph.connections[0]
        cid = f"{conn.src_block}:{conn.src_port}->{conn.dst_block}:{conn.dst_port}"
        result = suggest_insertions(session, cid, k=3)
        self.assertTrue(result.ok)
        for c in result.candidates:
            params = c.insert_tool_args.get("params", {})
            self.assertIsInstance(params, dict)
            # params key must match top-level tool schema
            self.assertNotIn("parameters", c.insert_tool_args)

    def test_message_connection_returns_no_insert_tool_args(self):
        tx_stage0 = Path("/usr/share/gnuradio/examples/digital/packet/tx_stage0.grc")
        if not tx_stage0.exists():
            self.skipTest("tx_stage0.grc not available")
        session = FlowgraphSession()
        session.load(tx_stage0)
        conn = None
        for c in session.flowgraph.connections:
            if isinstance(c.src_port, str):
                conn = c
                break
        self.assertIsNotNone(conn)
        cid = f"{conn.src_block}:{conn.src_port}->{conn.dst_block}:{conn.dst_port}"
        result = suggest_insertions(session, cid, k=3)
        self.assertFalse(result.ok)
        self.assertEqual(result.error_type, "MESSAGE_CONNECTION_NOT_SUPPORTED")

    def test_hardware_blocks_excluded(self):
        desc = BlockDescription(
            block_id="uhd_rfnoc_ddc",
            label="RFNoC DDC",
            category_path=["UHD", "RFNoC"],
            flags=[],
            loaded_from="test",
            parameters=[],
            inputs=[NormalizedPort(domain="stream")],
            outputs=[NormalizedPort(domain="stream")],
            asserts=[],
            documentation=None,
            doc_url=None,
            warnings=[],
            signature="",
        )
        self.assertTrue(_is_hardware_or_external(desc))

    def test_core_block_not_excluded(self):
        desc = BlockDescription(
            block_id="blocks_throttle2",
            label="Throttle",
            category_path=["Core", "General"],
            flags=[],
            loaded_from="test",
            parameters=[],
            inputs=[NormalizedPort(domain="stream")],
            outputs=[NormalizedPort(domain="stream")],
            asserts=[],
            documentation=None,
            doc_url=None,
            warnings=[],
            signature="",
        )
        self.assertFalse(_is_hardware_or_external(desc))

    def test_safe_defaults_detection(self):
        desc = BlockDescription(
            block_id="blocks_head",
            label="Head",
            category_path=["Core"],
            flags=[],
            loaded_from="test",
            parameters=[
                NormalizedParameter(id="type", default="float"),
                NormalizedParameter(id="num_items", default="1024"),
            ],
            inputs=[NormalizedPort(domain="stream")],
            outputs=[NormalizedPort(domain="stream")],
            asserts=[],
            documentation=None,
            doc_url=None,
            warnings=[],
            signature="",
        )
        ok, params, missing = _has_safe_defaults(desc)
        self.assertTrue(ok)
        self.assertFalse(missing)
        self.assertEqual(params["type"], "float")

    def test_port_compatible_template_dtype(self):
        inp = NormalizedPort(domain="stream", dtype="${type}")
        out = NormalizedPort(domain="stream", dtype="${type}")
        self.assertTrue(_port_compatible_for_insertion(inp, out, "float", None))

    def test_port_incompatible_dtype(self):
        inp = NormalizedPort(domain="stream", dtype="float")
        out = NormalizedPort(domain="stream", dtype="complex")
        self.assertFalse(_port_compatible_for_insertion(inp, out, "float", None))

    def test_confidence_high_for_simple_defaults(self):
        desc = BlockDescription(
            block_id="blocks_head",
            label="Head",
            category_path=["Core"],
            flags=[],
            loaded_from="test",
            parameters=[
                NormalizedParameter(id="type", default="float"),
            ],
            inputs=[NormalizedPort(domain="stream")],
            outputs=[NormalizedPort(domain="stream")],
            asserts=[],
            documentation=None,
            doc_url=None,
            warnings=[],
            signature="",
        )
        conf = _confidence(desc, desc.inputs[0], desc.outputs[0], True, [])
        self.assertEqual(conf, "high")

    def test_confidence_low_for_complex_multiport(self):
        desc = BlockDescription(
            block_id="some_complex_block",
            label="Complex Block",
            category_path=["Custom"],
            flags=[],
            loaded_from="test",
            parameters=[NormalizedParameter(id="p1")],
            inputs=[NormalizedPort(domain="stream"), NormalizedPort(domain="stream")],
            outputs=[NormalizedPort(domain="stream")],
            asserts=[],
            documentation=None,
            doc_url=None,
            warnings=[],
            signature="",
        )
        conf = _confidence(desc, desc.inputs[0], desc.outputs[0], False, ["p1"])
        self.assertEqual(conf, "low")

    def test_rank_candidates_is_deterministic_high_before_medium_before_low(self):
        cands = [
            InsertionCandidate(block_type="a", reason="r1", required_params={}, confidence="medium"),
            InsertionCandidate(block_type="b", reason="r2", required_params={}, confidence="high"),
            InsertionCandidate(block_type="c", reason="r3", required_params={}, confidence="low"),
        ]
        ranked = _rank_candidates(cands)
        self.assertEqual(ranked[0].block_type, "b")
        self.assertEqual(ranked[1].block_type, "a")
        self.assertEqual(ranked[2].block_type, "c")

    def test_generate_instance_name_avoids_existing(self):
        existing = {"head", "head_0", "head_1"}
        name = _generate_instance_name("blocks_head", existing)
        self.assertEqual(name, "head_2")
        existing.add("head_2")
        name2 = _generate_instance_name("blocks_head", existing)
        self.assertEqual(name2, "head_3")

    def test_generate_instance_name_fallback(self):
        existing = set()
        name = _generate_instance_name("blocks_throttle2", existing)
        self.assertEqual(name, "throttle2")

if __name__ == "__main__":
    unittest.main()
