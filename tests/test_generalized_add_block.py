"""Tests proving generalized add_block supports arbitrary catalog blocks."""

from pathlib import Path
import unittest

from grc_agent.agent import GrcAgent
from grc_agent.flowgraph_session import FlowgraphSession


class ArbitraryAddBlockSessionTests(unittest.TestCase):
    """Session-level add_block for non-variable blocks uses _skip_grcc."""

    def _fixture_path(self) -> Path:
        return Path(__file__).resolve().parent / "data" / "random_bit_generator.grc"

    def _load_session(self) -> FlowgraphSession:
        session = FlowgraphSession()
        session.load(self._fixture_path())
        return session

    def test_add_char_to_float_with_skip_grcc(self) -> None:
        session = self._load_session()
        original_count = len(session.flowgraph.blocks)
        session.add_block(
            "my_converter", "blocks_char_to_float", {},
            _skip_grcc=True,
        )
        self.assertEqual(len(session.flowgraph.blocks), original_count + 1)
        block = session.flowgraph.blocks[-1]
        self.assertEqual(block.instance_name, "my_converter")
        self.assertEqual(block.block_type, "blocks_char_to_float")
        self.assertTrue(session.is_dirty)

    def test_add_throttle_with_skip_grcc(self) -> None:
        session = self._load_session()
        session.add_block(
            "my_throttle", "blocks_throttle2",
            {"type": "float", "samples_per_second": "samp_rate"},
            _skip_grcc=True,
        )
        block = session.flowgraph.blocks[-1]
        self.assertEqual(block.block_type, "blocks_throttle2")

    def test_add_throttle_auto_fills_defaults_via_preflight(self) -> None:
        session = FlowgraphSession()
        session.load(self._fixture_path())
        agent = GrcAgent(session)
        r = agent.execute_tool("apply_edit", {
            "transaction": [
                {"op_type": "add_block", "block_type": "blocks_throttle2",
                 "instance_name": "thr_0",
                 "parameters": {"type": "float", "samples_per_second": "32000"}},
                {"op_type": "add_block", "block_type": "qtgui_time_sink_x",
                 "instance_name": "sink_0",
                 "parameters": {"type": "float", "size": "1024",
                                "srate": "samp_rate", "nconnections": "1"}},
                {"op_type": "add_connection",
                 "src_block": "blocks_char_to_float_0", "src_port": 0,
                 "dst_block": "thr_0", "dst_port": 0},
                {"op_type": "add_connection",
                 "src_block": "thr_0", "src_port": 0,
                 "dst_block": "sink_0", "dst_port": 0},
            ]
        })
        self.assertTrue(r["ok"], r.get("message"))
        norm_ops = r.get("normalized_operations", [])
        throttle_op = next(
            o for o in norm_ops if o.get("instance_name") == "thr_0"
        )
        params = throttle_op.get("parameters", {})
        self.assertIn("vlen", params)
        self.assertIn("ignoretag", params)

    def test_add_block_rejects_empty_instance_name(self) -> None:
        session = self._load_session()
        with self.assertRaises(ValueError):
            session.add_block("", "variable", {"value": "0"})

    def test_add_block_rejects_empty_block_type(self) -> None:
        session = self._load_session()
        with self.assertRaises(ValueError):
            session.add_block("my_block", "", {"value": "0"})

    def test_add_block_rejects_non_dict_parameters(self) -> None:
        session = self._load_session()
        with self.assertRaises(ValueError):
            session.add_block("my_block", "variable", "not_a_dict")

    def test_add_block_rejects_duplicate_instance_name(self) -> None:
        session = self._load_session()
        with self.assertRaises(ValueError):
            session.add_block("samp_rate", "variable", {"value": "0"})

    def test_add_variable_block_still_works(self) -> None:
        session = self._load_session()
        original_count = len(session.flowgraph.blocks)
        session.add_block("debug_var", "variable", {"value": "42"})
        self.assertEqual(len(session.flowgraph.blocks), original_count + 1)

    def test_add_block_includes_bus_state_fields(self) -> None:
        session = self._load_session()
        session.add_block(
            "my_converter", "blocks_char_to_float", {},
            _skip_grcc=True,
        )
        raw_block = session.flowgraph.raw_data["blocks"][-1]
        states = raw_block["states"]
        self.assertFalse(states["bus_sink"])
        self.assertFalse(states["bus_source"])
        self.assertIsNone(states["bus_structure"])

    def test_add_block_failure_rolls_back(self) -> None:
        session = self._load_session()
        original_count = len(session.flowgraph.blocks)
        original_dirty = session.is_dirty
        with self.assertRaises(ValueError):
            session.add_block("my_throttle", "blocks_throttle2", {
                "samples_per_second": "32000",
            })
        self.assertEqual(len(session.flowgraph.blocks), original_count)
        self.assertEqual(session.is_dirty, original_dirty)


class ArbitraryAddBlockPreflightTests(unittest.TestCase):
    """Agent-level apply_edit handles arbitrary block types via preflight."""

    def _fixture_path(self) -> Path:
        return Path(__file__).resolve().parent / "data" / "random_bit_generator.grc"

    def _load_agent(self) -> GrcAgent:
        session = FlowgraphSession()
        session.load(self._fixture_path())
        return GrcAgent(session)

    def test_apply_edit_adds_variable(self) -> None:
        agent = self._load_agent()
        result = agent.execute_tool("apply_edit", {
            "transaction": {
                "op_type": "add_block",
                "block_type": "variable",
                "instance_name": "freq",
                "parameters": {"value": "1000"},
            }
        })
        self.assertTrue(result["ok"], result.get("message"))

    def test_apply_edit_rejects_missing_block_type(self) -> None:
        agent = self._load_agent()
        result = agent.execute_tool("apply_edit", {
            "transaction": {
                "op_type": "add_block",
                "instance_name": "my_block",
                "parameters": {"value": "0"},
            }
        })
        self.assertFalse(result["ok"])

    def test_apply_edit_rejects_unknown_block_type(self) -> None:
        agent = self._load_agent()
        result = agent.execute_tool("apply_edit", {
            "transaction": {
                "op_type": "add_block",
                "block_type": "nonexistent_xyz",
                "instance_name": "my_block",
                "parameters": {"value": "0"},
            }
        })
        self.assertFalse(result["ok"])

    def test_apply_edit_rejects_duplicate_instance_name(self) -> None:
        agent = self._load_agent()
        result = agent.execute_tool("apply_edit", {
            "transaction": {
                "op_type": "add_block",
                "block_type": "variable",
                "instance_name": "samp_rate",
                "parameters": {"value": "0"},
            }
        })
        self.assertFalse(result["ok"])


class CreateFromEmptyAtomicTests(unittest.TestCase):
    """Full create-from-empty with atomic transactions."""

    def _full_chain_transaction(self) -> list[dict]:
        return [
            {"op_type": "add_block", "block_type": "variable",
             "instance_name": "samp_rate", "parameters": {"value": "32000"}},
            {"op_type": "add_block", "block_type": "analog_random_source_x",
             "instance_name": "src_0",
             "parameters": {"type": "byte", "min": "0", "max": "2",
                            "num_samps": "1000", "repeat": "True"}},
            {"op_type": "add_block", "block_type": "blocks_throttle2",
             "instance_name": "thr_0",
             "parameters": {"type": "byte", "samples_per_second": "samp_rate"}},
            {"op_type": "add_block", "block_type": "blocks_char_to_float",
             "instance_name": "c2f_0",
             "parameters": {"vlen": "1", "scale": "1"}},
            {"op_type": "add_block", "block_type": "qtgui_time_sink_x",
             "instance_name": "sink_0",
             "parameters": {"type": "float", "size": "1024",
                            "srate": "samp_rate", "nconnections": "1"}},
            {"op_type": "add_connection",
             "src_block": "src_0", "src_port": 0,
             "dst_block": "thr_0", "dst_port": 0},
            {"op_type": "add_connection",
             "src_block": "thr_0", "src_port": 0,
             "dst_block": "c2f_0", "dst_port": 0},
            {"op_type": "add_connection",
             "src_block": "c2f_0", "src_port": 0,
             "dst_block": "sink_0", "dst_port": 0},
        ]

    def test_new_grc_creates_valid_empty_session(self) -> None:
        agent = GrcAgent()
        result = agent.execute_tool("new_grc", {"profile": "minimal"})
        self.assertTrue(result["ok"])
        self.assertEqual(len(agent.session.flowgraph.blocks), 0)
        self.assertEqual(len(agent.session.flowgraph.connections), 0)

    def test_new_grc_rejects_non_minimal_profile(self) -> None:
        agent = GrcAgent()
        result = agent.execute_tool("new_grc", {"profile": "audio"})
        self.assertFalse(result["ok"])

    def test_new_grc_with_graph_id(self) -> None:
        agent = GrcAgent()
        result = agent.execute_tool("new_grc", {
            "profile": "minimal", "graph_id": "test_graph",
        })
        self.assertTrue(result["ok"])
        self.assertIn("provenance", result)

    def test_create_from_empty_add_variable_and_validate(self) -> None:
        agent = GrcAgent()
        agent.execute_tool("new_grc", {"profile": "minimal"})
        r = agent.execute_tool("apply_edit", {
            "transaction": {
                "op_type": "add_block",
                "block_type": "variable",
                "instance_name": "samp_rate",
                "parameters": {"value": "32000"},
            }
        })
        self.assertTrue(r["ok"], r.get("message"))
        v = agent.execute_tool("validate_graph", {})
        self.assertTrue(v["ok"])
        self.assertTrue(v["valid"])

    def test_create_from_empty_full_pipeline(self) -> None:
        agent = GrcAgent()
        agent.execute_tool("new_grc", {"profile": "minimal"})
        r = agent.execute_tool("apply_edit", {
            "transaction": self._full_chain_transaction(),
        })
        self.assertTrue(r["ok"], r.get("message"))
        self.assertEqual(len(agent.session.flowgraph.blocks), 5)
        self.assertEqual(len(agent.session.flowgraph.connections), 3)

        v = agent.execute_tool("validate_graph", {})
        self.assertTrue(v["ok"])
        self.assertTrue(v["valid"])

    def test_save_refused_before_validation(self) -> None:
        agent = GrcAgent()
        agent.execute_tool("new_grc", {"profile": "minimal"})
        agent.execute_tool("apply_edit", {
            "transaction": {
                "op_type": "add_block",
                "block_type": "variable",
                "instance_name": "samp_rate",
                "parameters": {"value": "32000"},
            }
        })
        with self.assertRaises(Exception):
            agent.session.save()

    def test_save_succeeds_after_validation(self) -> None:
        import tempfile
        agent = GrcAgent()
        agent.execute_tool("new_grc", {"profile": "minimal"})
        agent.execute_tool("apply_edit", {
            "transaction": {
                "op_type": "add_block",
                "block_type": "variable",
                "instance_name": "samp_rate",
                "parameters": {"value": "32000"},
            }
        })
        v = agent.execute_tool("validate_graph", {})
        self.assertTrue(v["valid"])
        with tempfile.TemporaryDirectory() as tmpdir:
            save_path = str(Path(tmpdir) / "test.grc")
            save_result = agent.execute_tool("save_graph", {"path": save_path})
            self.assertTrue(save_result["ok"], save_result.get("message"))

    def test_add_two_variables_in_one_transaction(self) -> None:
        agent = GrcAgent()
        agent.execute_tool("new_grc", {"profile": "minimal"})
        r = agent.execute_tool("apply_edit", {
            "transaction": [
                {"op_type": "add_block", "block_type": "variable",
                 "instance_name": "samp_rate", "parameters": {"value": "32000"}},
                {"op_type": "add_block", "block_type": "variable",
                 "instance_name": "freq", "parameters": {"value": "1000"}},
            ]
        })
        self.assertTrue(r["ok"], r.get("message"))
        self.assertEqual(len(agent.session.flowgraph.blocks), 2)

    def test_short_chain_add_connect_validate(self) -> None:
        agent = GrcAgent()
        agent.execute_tool("new_grc", {"profile": "minimal"})
        r = agent.execute_tool("apply_edit", {
            "transaction": [
                {"op_type": "add_block", "block_type": "variable",
                 "instance_name": "samp_rate", "parameters": {"value": "32000"}},
                {"op_type": "add_block", "block_type": "analog_sig_source_x",
                 "instance_name": "src_0",
                 "parameters": {"type": "float", "samp_rate": "samp_rate",
                                "waveform": "analog.GR_COS_WAVE", "freq": "1000",
                                "amp": "1"}},
                {"op_type": "add_block", "block_type": "qtgui_time_sink_x",
                 "instance_name": "sink_0",
                 "parameters": {"type": "float", "size": "1024",
                                "srate": "samp_rate", "nconnections": "1"}},
                {"op_type": "add_connection",
                 "src_block": "src_0", "src_port": 0,
                 "dst_block": "sink_0", "dst_port": 0},
            ]
        })
        self.assertTrue(r["ok"], r.get("message"))
        self.assertEqual(len(agent.session.flowgraph.blocks), 3)
        self.assertEqual(len(agent.session.flowgraph.connections), 1)

    def test_throttle_rejects_missing_type_at_preflight(self) -> None:
        agent = GrcAgent()
        agent.execute_tool("new_grc", {"profile": "minimal"})
        r = agent.execute_tool("apply_edit", {
            "transaction": {
                "op_type": "add_block",
                "block_type": "blocks_throttle2",
                "instance_name": "thr",
                "parameters": {"samples_per_second": "32000"},
            }
        })
        self.assertFalse(r["ok"])

    def test_throttle_rejects_invalid_enum_at_preflight(self) -> None:
        agent = GrcAgent()
        agent.execute_tool("new_grc", {"profile": "minimal"})
        r = agent.execute_tool("apply_edit", {
            "transaction": {
                "op_type": "add_block",
                "block_type": "blocks_throttle2",
                "instance_name": "thr",
                "parameters": {"type": "invalid_dtype", "samples_per_second": "32000"},
            }
        })
        self.assertFalse(r["ok"])
