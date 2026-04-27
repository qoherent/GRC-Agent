"""External Corpus Evaluation v2 — type-aware tool-chain tests.

Tests real editing capability on 10 installed GNU Radio example graphs.
All edits are type-aware: signal dtype is read from block params and the
inserted block is configured to match.  No LLM routing involved.
"""

import os
import shutil
import tempfile
import unittest

from grc_agent.agent import GrcAgent
from grc_agent.flowgraph_session import FlowgraphSession

CORPUS_DIR = "/tmp/grc_corpus"

EXAMPLES = "/usr/share/gnuradio/examples"
TX_STAGE0 = os.path.join(EXAMPLES, "digital", "packet", "tx_stage0.grc")
TX_STAGE2 = os.path.join(EXAMPLES, "digital", "packet", "tx_stage2.grc")
ZMQ_MSG = os.path.join(EXAMPLES, "zeromq", "zmq_msg.grc")

SKIP_UNSAFE = "SKIP_UNSAFE_EDIT_SELECTION"
SKIP_NO_PARAM = "SKIP_NO_SAFE_SCALAR_PARAM"
SKIP_MSG_ONLY = "SKIP_MESSAGE_ONLY_GRAPH"


def _graph_info(agent):
    blocks = agent.session.flowgraph.blocks
    conns = agent.session.flowgraph.connections
    has_msg = any(isinstance(c.src_port, str) for c in conns)
    has_stream = any(isinstance(c.src_port, int) for c in conns)
    return blocks, conns, has_msg, has_stream


def _signal_dtype(agent, instance_name):
    for b in agent.session.flowgraph.blocks:
        if b.instance_name == instance_name:
            p = b.params or {}
            if isinstance(p, dict) and "parameters" in p:
                p = p["parameters"]
            if isinstance(p, dict):
                return p.get("type")
    return None


def _find_stream_source(agent):
    for b in agent.session.flowgraph.blocks:
        if b.block_type in ("analog_sig_source_x", "analog_noise_source_x",
                             "analog_fastnoise_source_x", "blocks_vector_source_x"):
            dtype = _signal_dtype(agent, b.instance_name)
            if dtype in ("float", "complex"):
                p = b.params or {}
                if isinstance(p, dict) and "parameters" in p:
                    p = p["parameters"]
                vlen = 1
                if isinstance(p, dict):
                    try:
                        vlen = int(p.get("vlen", 1))
                    except (TypeError, ValueError):
                        pass
                return b.instance_name, b.block_type, dtype, vlen
    return None, None, None, None


def _find_safe_scalar_param(agent, instance_name):
    skip = {"comment", "alias", "affinity", "gui_hint", "_coordinate", "_rotation",
            "minoutbuf", "maxoutbuf", "states", "coordinate", "rotation",
            "type", "vlen", "id", "name", "seed"}
    for b in agent.session.flowgraph.blocks:
        if b.instance_name != instance_name:
            continue
        p = b.params or {}
        if isinstance(p, dict) and "parameters" in p:
            p = p["parameters"]
        if not isinstance(p, dict):
            return None, None
        for k, v in p.items():
            if k in skip:
                continue
            if isinstance(v, (int, float)):
                return k, v
            if isinstance(v, str):
                try:
                    float(v)
                    return k, v
                except ValueError:
                    continue
    return None, None


# ── Per-graph test classes ──────────────────────────────────────────


class CorpusV2Base:
    graph_id: str = ""
    fname: str = ""
    category: str = ""
    _missing_graphs: set[str] = set()

    @classmethod
    def setUpClass(cls):
        os.makedirs(CORPUS_DIR, exist_ok=True)
        # Copy installed GNU examples into /tmp/grc_corpus so tests are hermetic
        example_map = {
            "01_tx_stage0.grc": os.path.join(EXAMPLES, "digital", "packet", "tx_stage0.grc"),
            "02_file_meta_sink.grc": os.path.join(EXAMPLES, "grc", "file_meta_sink.grc"),
            "03_selector.grc": os.path.join(EXAMPLES, "blocks", "selector.grc"),
            "04_filter_taps.grc": os.path.join(EXAMPLES, "filter", "filter_taps.grc"),
            "05_tx_stage2.grc": os.path.join(EXAMPLES, "digital", "packet", "tx_stage2.grc"),
            "06_zmq_msg.grc": os.path.join(EXAMPLES, "zeromq", "zmq_msg.grc"),
            "07_resampler_demo.grc": os.path.join(EXAMPLES, "filter", "resampler_demo.grc"),
            "08_channel_tone.grc": os.path.join(EXAMPLES, "audio", "channel_tone.grc"),
            "09_dial_tone.grc": os.path.join(EXAMPLES, "audio", "dial_tone.grc"),
            "10_polyphase_channelizer.grc": os.path.join(EXAMPLES, "digital", "polyphase", "polyphase_channelizer.grc"),
        }
        CorpusV2Base._missing_graphs.clear()
        for dest_name, src in example_map.items():
            dest = os.path.join(CORPUS_DIR, dest_name)
            if not os.path.exists(dest):
                if os.path.exists(src):
                    shutil.copy(src, dest)
                else:
                    CorpusV2Base._missing_graphs.add(dest_name)
        # Skip classes whose graph is missing
        if cls.fname in CorpusV2Base._missing_graphs:
            raise unittest.SkipTest(f"Graph file missing: {cls.fname}")

    def _path(self):
        return os.path.join(CORPUS_DIR, self.fname)

    def _agent(self):
        sess = FlowgraphSession()
        sess.load(self._path())
        return GrcAgent(session=sess)

    # ── inspection ──

    def test_summarize(self):
        agent = self._agent()
        r = agent.execute_tool("summarize_graph", {})
        self.assertTrue(r.get("ok"), r.get("message", ""))
        self.assertGreater(r.get("block_count", 0), 0)

    def test_validate(self):
        agent = self._agent()
        r = agent.execute_tool("validate_graph", {})
        self.assertTrue(r.get("ok"))
        self.assertTrue(r.get("valid"))

    def test_save(self):
        agent = self._agent()
        tmp = tempfile.mkdtemp()
        try:
            p = os.path.join(tmp, "copy.grc")
            r = agent.execute_tool("save_graph", {"path": p})
            self.assertTrue(r.get("ok"))
            self.assertTrue(os.path.exists(p))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_describe_block(self):
        agent = self._agent()
        blocks, *_ = _graph_info(agent)
        for b in blocks:
            if b.block_type not in ("variable", "variable_qtgui_range",
                                     "variable_qtgui_label", "parameter", "options",
                                     "import"):
                r = agent.execute_tool("describe_block", {"block_id": b.block_type})
                self.assertTrue(r.get("ok"), f"describe_block({b.block_type}) failed")
                return
        self.skipTest("no describable block found")


class StreamEditMixin:
    def test_param_edit(self):
        agent = self._agent()
        src, _, dtype, _ = _find_stream_source(agent)
        if not src:
            src_block = self._find_any_editable_block(agent)
            if not src_block:
                self.skipTest(SKIP_NO_PARAM)
        else:
            src_block = src
        param, old = _find_safe_scalar_param(agent, src_block)
        if not param:
            self.skipTest(SKIP_NO_PARAM)
        new_val = str(float(old) + 1) if old is not None else "42"
        r = agent.execute_tool("apply_edit", {
            "transaction": [{"op_type": "update_params",
                             "instance_name": src_block,
                             "params": {param: new_val}}]
        })
        self.assertTrue(r.get("ok"), f"param edit failed: {r.get('message', '')}")

    def test_add_block_type_matched(self):
        agent = self._agent()
        src, _, dtype, vlen = _find_stream_source(agent)
        if not src:
            self.skipTest(SKIP_UNSAFE)
        r = agent.execute_tool("apply_edit", {
            "transaction": [
                {"op_type": "add_block", "block_type": "blocks_head",
                 "instance_name": "blocks_head_corpus_0",
                 "params": {"num_items": "1000", "type": dtype, "vlen": str(vlen)}},
                {"op_type": "add_connection",
                 "src_block": src, "src_port": 0,
                 "dst_block": "blocks_head_corpus_0", "dst_port": 0},
            ]
        })
        self.assertTrue(r.get("ok"), f"add_block failed: {r.get('message', '')[:200]}")

    def _find_any_editable_block(self, agent):
        for b in agent.session.flowgraph.blocks:
            if b.block_type.startswith(("variable", "import", "options")):
                continue
            p, _ = _find_safe_scalar_param(agent, b.instance_name)
            if p:
                return b.instance_name
        return None


class MessageEditMixin:
    def test_summarize_has_string_ports(self):
        agent = self._agent()
        r = agent.execute_tool("summarize_graph", {})
        conns = r.get("connections", [])
        msg = [c for c in conns if isinstance(c.get("src_port"), str)]
        self.assertGreater(len(msg), 0, "Expected message connections with string ports")

    def test_context_message_block(self):
        agent = self._agent()
        blocks, conns, *_ = _graph_info(agent)
        msg_blocks = set()
        for c in conns:
            if isinstance(c.src_port, str):
                msg_blocks.add(c.src_block)
                msg_blocks.add(c.dst_block)
        if not msg_blocks:
            self.skipTest("no message blocks")
        target = sorted(msg_blocks)[0]
        r = agent.execute_tool("get_grc_context", {"node_id": target})
        self.assertTrue(r.get("ok"), f"context failed: {r.get('message', '')}")

    def test_remove_message_conn_by_endpoint(self):
        agent = self._agent()
        r = agent.execute_tool("summarize_graph", {})
        conns = r.get("connections", [])
        msg = [c for c in conns if isinstance(c.get("src_port"), str)]
        if not msg:
            self.skipTest("no message connections")
        for c in msg:
            er = agent.execute_tool("apply_edit", {
                "transaction": [{"op_type": "remove_connection",
                                 "src_block": c["src_block"], "src_port": c["src_port"],
                                 "dst_block": c["dst_block"], "dst_port": c["dst_port"]}]
            })
            if er.get("ok"):
                return
            if er.get("error_type") == "gnu_validation_failed":
                continue
            self.fail(f"remove failed unexpectedly: {er.get('message', '')[:200]}")
        self.skipTest("all message removals left graph invalid (correct rejection)")

    def test_remove_message_conn_by_id(self):
        agent = self._agent()
        r = agent.execute_tool("summarize_graph", {})
        conns = r.get("connections", [])
        msg = [c for c in conns if isinstance(c.get("src_port"), str)]
        if not msg:
            self.skipTest("no message connections")
        for c in msg:
            er = agent.execute_tool("apply_edit", {
                "transaction": [{"op_type": "remove_connection",
                                 "connection_id": c["connection_id"]}]
            })
            if er.get("ok"):
                return
            if er.get("error_type") == "gnu_validation_failed":
                continue
            self.fail(f"remove by id failed unexpectedly: {er.get('message', '')[:200]}")
        self.skipTest("all message removals left graph invalid (correct rejection)")

    def test_save_roundtrip_preserves_string_ports(self):
        sess = FlowgraphSession()
        sess.load(self._path())
        original = [(c.src_block, c.src_port, c.dst_block, c.dst_port)
                     for c in sess.flowgraph.connections]
        has_str = any(isinstance(p, str) for c in original for p in (c[1], c[3]))
        self.assertTrue(has_str, "Expected string ports in original")

        tmp = tempfile.mkdtemp()
        try:
            sp = os.path.join(tmp, "rt.grc")
            sess.save(sp)
            sess2 = FlowgraphSession()
            sess2.load(sp)
            reloaded = [(c.src_block, c.src_port, c.dst_block, c.dst_port)
                         for c in sess2.flowgraph.connections]
            self.assertEqual(original, reloaded)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ── Concrete graph classes ──────────────────────────────────────────


class G01_TxStage0(CorpusV2Base, MessageEditMixin, unittest.TestCase):
    graph_id = "01"
    fname = "01_tx_stage0.grc"
    category = "Simple/No-GUI (message)"


class G02_FileMetaSink(CorpusV2Base, StreamEditMixin, unittest.TestCase):
    graph_id = "02"
    fname = "02_file_meta_sink.grc"
    category = "Simple/No-GUI"


class G03_Selector(CorpusV2Base, StreamEditMixin, unittest.TestCase):
    graph_id = "03"
    fname = "03_selector.grc"
    category = "QT GUI"


class G04_FilterTaps(CorpusV2Base, StreamEditMixin, unittest.TestCase):
    graph_id = "04"
    fname = "04_filter_taps.grc"
    category = "QT GUI"


class G05_TxStage2(CorpusV2Base, MessageEditMixin, unittest.TestCase):
    graph_id = "05"
    fname = "05_tx_stage2.grc"
    category = "No-GUI (message+dup)"

    def test_duplicate_name_detected(self):
        agent = self._agent()
        r = agent.execute_tool("get_grc_context", {"node_id": "enc"})
        self.assertFalse(r.get("ok"), "Should reject ambiguous duplicate name")
        self.assertIn("not unique", r.get("message", "").lower() + r.get("error_type", "").lower())

    def test_unique_block_edit_works(self):
        agent = self._agent()
        r = agent.execute_tool("apply_edit", {
            "transaction": [{"op_type": "update_params",
                             "instance_name": "blocks_message_strobe_0",
                             "params": {"period": "3000"}}]
        })
        self.assertTrue(r.get("ok"), f"edit on unique block failed: {r.get('message', '')}")

    def test_duplicate_name_search_fails_gracefully(self):
        agent = self._agent()
        r = agent.execute_tool("search_grc", {"query": "enc", "scope": "session"})
        self.assertFalse(r.get("ok"), "search_grc should fail on duplicate names")


class G06_ZmqMsg(CorpusV2Base, MessageEditMixin, unittest.TestCase):
    graph_id = "06"
    fname = "06_zmq_msg.grc"
    category = "No-GUI (message)"


class G07_ResamplerDemo(CorpusV2Base, StreamEditMixin, unittest.TestCase):
    graph_id = "07"
    fname = "07_resampler_demo.grc"
    category = "Digital/QT GUI"


class G08_ChannelTone(CorpusV2Base, StreamEditMixin, unittest.TestCase):
    graph_id = "08"
    fname = "08_channel_tone.grc"
    category = "Digital/QT GUI"


class G09_DialTone(CorpusV2Base, StreamEditMixin, unittest.TestCase):
    graph_id = "09"
    fname = "09_dial_tone.grc"
    category = "Audio/QT GUI"

    def test_param_edit_variable(self):
        agent = self._agent()
        r = agent.execute_tool("apply_edit", {
            "transaction": [{"op_type": "update_params",
                             "instance_name": "samp_rate",
                             "params": {"value": "48000"}}]
        })
        self.assertTrue(r.get("ok"), f"samp_rate edit failed: {r.get('message', '')}")


class G10_PolyphaseChannelizer(CorpusV2Base, StreamEditMixin, unittest.TestCase):
    graph_id = "10"
    fname = "10_polyphase_channelizer.grc"
    category = "Stress/QT GUI"


if __name__ == "__main__":
    unittest.main()
