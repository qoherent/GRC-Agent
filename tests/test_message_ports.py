"""Tests for message-port string connection support.

Validates that the agent can load, inspect, edit, and save graphs with
GNU Radio message connections (string ports like "strobe", "pdus").
"""

import os
import shutil
import tempfile
import unittest

from grc_agent.agent import GrcAgent
from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.session_ops import (
    _normalize_port,
    connection_id,
    parse_connection_id,
    parse_connections,
    raw_connection_entry,
    connection_entry_to_tuple,
)

EXAMPLES = "/usr/share/gnuradio/examples"
TX_STAGE0 = os.path.join(EXAMPLES, "digital", "packet", "tx_stage0.grc")
TX_STAGE2 = os.path.join(EXAMPLES, "digital", "packet", "tx_stage2.grc")
ZMQ_MSG = os.path.join(EXAMPLES, "zeromq", "zmq_msg.grc")


class TestNormalizePort(unittest.TestCase):
    def test_int_stays_int(self):
        self.assertEqual(_normalize_port(0), 0)
        self.assertEqual(_normalize_port(3), 3)

    def test_numeric_string_becomes_int(self):
        self.assertEqual(_normalize_port("0"), 0)
        self.assertEqual(_normalize_port("42"), 42)

    def test_non_numeric_string_stays_str(self):
        self.assertEqual(_normalize_port("strobe"), "strobe")
        self.assertEqual(_normalize_port("pdus"), "pdus")
        self.assertEqual(_normalize_port("generate"), "generate")


class TestParseConnectionsMixed(unittest.TestCase):
    def test_stream_int_ports(self):
        data = [["src_block", 0, "dst_block", 0]]
        conns = parse_connections(data)
        self.assertEqual(len(conns), 1)
        self.assertEqual(conns[0].src_port, 0)
        self.assertEqual(conns[0].dst_port, 0)

    def test_stream_string_numeric_ports(self):
        data = [["src_block", "0", "dst_block", "1"]]
        conns = parse_connections(data)
        self.assertEqual(len(conns), 1)
        self.assertEqual(conns[0].src_port, 0)
        self.assertEqual(conns[0].dst_port, 1)

    def test_message_string_ports(self):
        data = [["blocks_message_strobe_0", "strobe", "pdu_random_pdu_0", "generate"]]
        conns = parse_connections(data)
        self.assertEqual(len(conns), 1)
        self.assertIsInstance(conns[0].src_port, str)
        self.assertIsInstance(conns[0].dst_port, str)
        self.assertEqual(conns[0].src_port, "strobe")
        self.assertEqual(conns[0].dst_port, "generate")

    def test_mixed_stream_and_message(self):
        data = [
            ["src_block", "0", "dst_block", "0"],
            ["msg_strobe", "strobe", "msg_debug", "print"],
        ]
        conns = parse_connections(data)
        self.assertEqual(len(conns), 2)
        self.assertEqual(conns[0].src_port, 0)
        self.assertEqual(conns[0].dst_port, 0)
        self.assertEqual(conns[1].src_port, "strobe")
        self.assertEqual(conns[1].dst_port, "print")


class TestConnectionIdStringPorts(unittest.TestCase):
    def test_int_port_id(self):
        cid = connection_id("src", 0, "dst", 1)
        self.assertEqual(cid, "src:0->dst:1")

    def test_str_port_id(self):
        cid = connection_id("msg_strobe", "strobe", "pdu", "generate")
        self.assertEqual(cid, "msg_strobe:strobe->pdu:generate")

    def test_parse_int_port_id(self):
        result = parse_connection_id("src:0->dst:1")
        self.assertEqual(result, ("src", 0, "dst", 1))

    def test_parse_str_port_id(self):
        result = parse_connection_id("msg_strobe:strobe->pdu:generate")
        self.assertEqual(result, ("msg_strobe", "strobe", "pdu", "generate"))

    def test_parse_roundtrip_int(self):
        original = ("block_a", 0, "block_b", 1)
        cid = connection_id(*original)
        self.assertEqual(parse_connection_id(cid), original)

    def test_parse_roundtrip_str(self):
        original = ("msg_strobe", "strobe", "pdu", "pdus")
        cid = connection_id(*original)
        self.assertEqual(parse_connection_id(cid), original)

    def test_parse_invalid_returns_none(self):
        self.assertIsNone(parse_connection_id(""))
        self.assertIsNone(parse_connection_id("no-arrow"))
        self.assertIsNone(parse_connection_id("a:b->c"))


class TestRawConnectionEntryStringPorts(unittest.TestCase):
    def test_int_ports(self):
        entry = raw_connection_entry("src", 0, "dst", 1)
        self.assertEqual(entry, ["src", "0", "dst", "1"])

    def test_str_ports(self):
        entry = raw_connection_entry("msg_strobe", "strobe", "pdu", "generate")
        self.assertEqual(entry, ["msg_strobe", "strobe", "pdu", "generate"])


class TestConnectionEntryToTuple(unittest.TestCase):
    def test_int_ports(self):
        result = connection_entry_to_tuple(["src", "0", "dst", "1"])
        self.assertEqual(result, ("src", 0, "dst", 1))

    def test_str_ports(self):
        result = connection_entry_to_tuple(["msg_strobe", "strobe", "pdu", "generate"])
        self.assertEqual(result, ("msg_strobe", "strobe", "pdu", "generate"))


@unittest.skipUnless(os.path.isfile(TX_STAGE0), "GNU Radio example not installed")
class TestLoadTxStage0(unittest.TestCase):
    def test_load(self):
        sess = FlowgraphSession()
        sess.load(TX_STAGE0)
        self.assertIsNotNone(sess.flowgraph)
        self.assertGreater(len(sess.flowgraph.blocks), 0)
        self.assertGreater(len(sess.flowgraph.connections), 0)

    def test_has_string_ports(self):
        sess = FlowgraphSession()
        sess.load(TX_STAGE0)
        message_conns = [
            c for c in sess.flowgraph.connections
            if isinstance(c.src_port, str) or isinstance(c.dst_port, str)
        ]
        self.assertGreater(len(message_conns), 0, "Expected at least one message connection")

    def test_validate(self):
        sess = FlowgraphSession()
        sess.load(TX_STAGE0)
        result = sess.validate()
        self.assertTrue(result)

    def test_summarize(self):
        sess = FlowgraphSession()
        sess.load(TX_STAGE0)
        agent = GrcAgent(session=sess)
        result = agent.execute_tool("summarize_graph", {})
        self.assertTrue(result.get("ok"))
        self.assertGreater(result.get("connection_count", 0), 0)

    def test_context_on_message_block(self):
        sess = FlowgraphSession()
        sess.load(TX_STAGE0)
        agent = GrcAgent(session=sess)
        result = agent.execute_tool("get_grc_context", {"node_id": "blocks_message_strobe_0"})
        self.assertTrue(result.get("ok"))

    def test_connection_ids_include_string_ports(self):
        sess = FlowgraphSession()
        sess.load(TX_STAGE0)
        agent = GrcAgent(session=sess)
        result = agent.execute_tool("summarize_graph", {})
        conns = result.get("connections", [])
        str_port_ids = [c["connection_id"] for c in conns if ":" in c.get("connection_id", "")]
        self.assertGreater(len(str_port_ids), 0)


@unittest.skipUnless(os.path.isfile(TX_STAGE2), "GNU Radio example not installed")
class TestLoadTxStage2(unittest.TestCase):
    def test_load(self):
        sess = FlowgraphSession()
        sess.load(TX_STAGE2)
        self.assertIsNotNone(sess.flowgraph)
        self.assertGreater(len(sess.flowgraph.connections), 0)

    def test_validate(self):
        sess = FlowgraphSession()
        sess.load(TX_STAGE2)
        self.assertTrue(sess.validate())


@unittest.skipUnless(os.path.isfile(ZMQ_MSG), "GNU Radio example not installed")
class TestLoadZmqMsg(unittest.TestCase):
    def test_load(self):
        sess = FlowgraphSession()
        sess.load(ZMQ_MSG)
        self.assertIsNotNone(sess.flowgraph)
        self.assertGreater(len(sess.flowgraph.connections), 0)

    def test_validate(self):
        sess = FlowgraphSession()
        sess.load(ZMQ_MSG)
        self.assertTrue(sess.validate())

    def test_summarize(self):
        sess = FlowgraphSession()
        sess.load(ZMQ_MSG)
        agent = GrcAgent(session=sess)
        result = agent.execute_tool("summarize_graph", {})
        self.assertTrue(result.get("ok"))


@unittest.skipUnless(os.path.isfile(TX_STAGE0), "GNU Radio example not installed")
class TestSaveRoundtripMessagePorts(unittest.TestCase):
    def test_save_preserves_string_ports(self):
        sess = FlowgraphSession()
        sess.load(TX_STAGE0)

        tmp_dir = tempfile.mkdtemp()
        try:
            save_path = os.path.join(tmp_dir, "roundtrip.grc")
            sess.save(save_path)

            sess2 = FlowgraphSession()
            sess2.load(save_path)

            original_conns = [
                (c.src_block, c.src_port, c.dst_block, c.dst_port)
                for c in sess.flowgraph.connections
            ]
            reloaded_conns = [
                (c.src_block, c.src_port, c.dst_block, c.dst_port)
                for c in sess2.flowgraph.connections
            ]
            self.assertEqual(original_conns, reloaded_conns)

            has_str = any(isinstance(p, str) for c in reloaded_conns for p in (c[1], c[3]))
            self.assertTrue(has_str, "Expected string ports to survive roundtrip")
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


@unittest.skipUnless(os.path.isfile(TX_STAGE0), "GNU Radio example not installed")
class TestRemoveConnectionByStringEndpoints(unittest.TestCase):
    def test_remove_message_connection_by_endpoints(self):
        sess = FlowgraphSession()
        sess.load(TX_STAGE0)
        agent = GrcAgent(session=sess)

        summary = agent.execute_tool("summarize_graph", {})
        conns = summary.get("connections", [])
        msg_conns = [
            c for c in conns
            if isinstance(c.get("src_port"), str) or isinstance(c.get("dst_port"), str)
        ]
        self.assertGreater(len(msg_conns), 0)

        target = msg_conns[0]
        result = agent.execute_tool("apply_edit", {
            "transaction": [{
                "op_type": "remove_connection",
                "src_block": target["src_block"],
                "src_port": target["src_port"],
                "dst_block": target["dst_block"],
                "dst_port": target["dst_port"],
            }]
        })
        self.assertTrue(result.get("ok"), f"remove_connection failed: {result.get('message', '')}")

    def test_remove_message_connection_by_connection_id(self):
        sess = FlowgraphSession()
        sess.load(TX_STAGE0)
        agent = GrcAgent(session=sess)

        summary = agent.execute_tool("summarize_graph", {})
        conns = summary.get("connections", [])
        msg_conns = [
            c for c in conns
            if isinstance(c.get("src_port"), str) or isinstance(c.get("dst_port"), str)
        ]
        self.assertGreater(len(msg_conns), 0)

        cid = msg_conns[0]["connection_id"]
        result = agent.execute_tool("apply_edit", {
            "transaction": [{
                "op_type": "remove_connection",
                "connection_id": cid,
            }]
        })
        self.assertTrue(result.get("ok"), f"remove by connection_id failed: {result.get('message', '')}")


if __name__ == "__main__":
    unittest.main()
