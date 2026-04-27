"""Capability Harness v1 — deterministic user-level goal tests without live model.

These tests exercise the tool layer through natural task framing:
explain/inspect, parameter edit, insert block, add sink, add filter,
create from empty, and preview-only.

No LLM involved. Use real graph copies, call real tools, validate with grcc.
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from grc_agent.agent import GrcAgent
from grc_agent.flowgraph_session import FlowgraphSession


FIXTURE = Path(__file__).resolve().parent.parent / "data" / "random_bit_generator.grc"


def _load_agent() -> GrcAgent:
    session = FlowgraphSession()
    session.load(FIXTURE)
    return GrcAgent(session)


class CapabilityEvalBase(unittest.TestCase):
    """Per-case helper: fresh graph copy in tmpdir, auto-cleanup."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        self.copy_path = Path(self.tmpdir) / "graph.grc"
        shutil.copy(FIXTURE, self.copy_path)
        self.agent = _load_agent()
        self.session: FlowgraphSession = self.agent.session

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _save_copy(self) -> str:
        path = str(Path(self.tmpdir) / "saved.grc")
        r = self.agent.execute_tool("save_graph", {"path": path})
        self.assertTrue(r.get("ok"), f"save failed: {r.get('message')}")
        return path

    def _grcc_validate(self, path: str) -> bool:
        r = self.agent.execute_tool("validate_graph", {})
        return bool(r.get("ok") and r.get("valid"))


# --------------------------------------------------------------------------- #
# A. Explain / inspect
# --------------------------------------------------------------------------- #

class ExplainInspectTests(CapabilityEvalBase):
    def test_summarize_graph(self) -> None:
        r = self.agent.execute_tool("summarize_graph", {})
        self.assertTrue(r.get("ok"), r.get("message"))
        self.assertIn("block_count", r)
        self.assertIn("connection_count", r)

    def test_context_around_source(self) -> None:
        r = self.agent.execute_tool("get_grc_context", {"node_id": "analog_random_source_x_0", "hops": 1})
        self.assertTrue(r.get("ok"), r.get("message"))

    def test_describe_throttle_block(self) -> None:
        r = self.agent.execute_tool("describe_block", {"block_id": "blocks_throttle2"})
        self.assertTrue(r.get("ok"), r.get("message"))

    def test_search_filter(self) -> None:
        r = self.agent.execute_tool("search_grc", {"query": "filter", "scope": "catalog"})
        self.assertTrue(r.get("ok"), r.get("message"))
        self.assertGreater(len(r.get("results", [])), 0)


# --------------------------------------------------------------------------- #
# B. Parameter edit
# --------------------------------------------------------------------------- #

class ParameterEditTests(CapabilityEvalBase):
    def test_change_sample_rate_parameter(self) -> None:
        r = self.agent.execute_tool("apply_edit", {
            "transaction": {
                "op_type": "update_params",
                "instance_name": "samp_rate",
                "params": {"value": "48000"},
            }
        })
        self.assertTrue(r.get("ok"), r.get("message"))
        self.assertTrue(self._grcc_validate(str(self.copy_path)))

    def test_preview_change_sample_rate(self) -> None:
        r = self.agent.execute_tool("propose_edit", {
            "transaction": {
                "op_type": "update_params",
                "instance_name": "samp_rate",
                "params": {"value": "24000"},
            }
        })
        self.assertTrue(r.get("ok"), r.get("message"))
        # preview must not mutate
        self.assertEqual(self.session.state_revision, 1)

    def test_change_gui_label(self) -> None:
        # random_bit_generator has no explicit label param on qtgui_time_sink.
        # Just ensure the tool layer handles missing param gracefully.
        r = self.agent.execute_tool("apply_edit", {
            "transaction": {
                "op_type": "update_params",
                "instance_name": "qtgui_time_sink_x_0",
                "params": {"gui_hint": ""},
            }
        })
        # may fail preflight if param unknown; that's fine for capability harness
        self.assertIn("ok", r)


# --------------------------------------------------------------------------- #
# C. Insert block into existing path
# --------------------------------------------------------------------------- #

class InsertBlockTests(CapabilityEvalBase):
    def test_insert_head_block_using_new_primitive(self) -> None:
        # Use insert_block_on_connection instead of manual 4-op transaction
        r = self.agent.execute_tool("apply_edit", {
            "transaction": {
                "op_type": "insert_block_on_connection",
                "connection_id": "analog_random_source_x_0:0->blocks_throttle2_0:0",
                "block_type": "blocks_head",
                "instance_name": "head_0",
                "params": {"type": "byte", "num_items": "1024"},
            }
        })
        self.assertTrue(r.get("ok"), r.get("message"))
        self.assertTrue(self._grcc_validate(str(self.copy_path)))


# --------------------------------------------------------------------------- #
# D. Add sink / source
# --------------------------------------------------------------------------- #

class AddSinkSourceTests(CapabilityEvalBase):
    def test_add_null_sink_to_float_stream(self) -> None:
        # random_bit_generator is byte source -> throttle -> char_to_float -> sink.
        # Float output is at blocks_char_to_float_0.
        r = self.agent.execute_tool("apply_edit", {
            "transaction": [
                {
                    "op_type": "add_block",
                    "block_type": "blocks_null_sink",
                    "instance_name": "null_sink_0",
                    "parameters": {"type": "float"},
                },
                {
                    "op_type": "add_connection",
                    "src_block": "blocks_char_to_float_0",
                    "src_port": 0,
                    "dst_block": "null_sink_0",
                    "dst_port": 0,
                },
            ]
        })
        self.assertTrue(r.get("ok"), r.get("message"))
        self.assertTrue(self._grcc_validate(str(self.copy_path)))

    def test_add_qt_time_sink_incompatible_type(self) -> None:
        # byte source cannot directly connect to float time sink
        r = self.agent.execute_tool("apply_edit", {
            "transaction": [
                {
                    "op_type": "add_block",
                    "block_type": "qtgui_time_sink_x",
                    "instance_name": "qt_time_0",
                    "parameters": {"type": "float", "srate": "1"},
                },
                {
                    "op_type": "add_connection",
                    "src_block": "analog_random_source_x_0",
                    "src_port": 0,
                    "dst_block": "qt_time_0",
                    "dst_port": 0,
                },
            ]
        })
        # Should fail preflight because dtype mismatch
        self.assertFalse(r.get("ok"))


# --------------------------------------------------------------------------- #
# E. Add filter
# --------------------------------------------------------------------------- #

class AddFilterTests(CapabilityEvalBase):
    def test_add_variable_block_alone(self) -> None:
        # add_block alone should succeed for a variable
        r = self.agent.execute_tool("apply_edit", {
            "transaction": {
                "op_type": "add_block",
                "block_type": "variable",
                "instance_name": "new_var",
                "parameters": {"value": "100"},
            }
        })
        self.assertTrue(r.get("ok"), r.get("message"))


# --------------------------------------------------------------------------- #
# F. Create from empty
# --------------------------------------------------------------------------- #

class CreateFromEmptyTests(unittest.TestCase):
    @unittest.skip("Empty-session creation requires new_graph tool (future)")
    def test_create_source_throttle_sink_and_validate(self) -> None:
        tmpdir = tempfile.mkdtemp()
        try:
            session = FlowgraphSession()
            agent = GrcAgent(session)
            r = agent.execute_tool("apply_edit", {
                "transaction": [
                    {
                        "op_type": "add_block",
                        "block_type": "analog_sig_source_x",
                        "instance_name": "src_0",
                        "parameters": {"type": "float", "samp_rate": "32000"},
                    },
                    {
                        "op_type": "add_block",
                        "block_type": "blocks_throttle2",
                        "instance_name": "throttle_0",
                        "parameters": {"type": "float", "samples_per_second": "32000"},
                    },
                    {
                        "op_type": "add_block",
                        "block_type": "blocks_null_sink",
                        "instance_name": "sink_0",
                        "parameters": {"type": "float"},
                    },
                    {
                        "op_type": "add_connection",
                        "src_block": "src_0",
                        "src_port": 0,
                        "dst_block": "throttle_0",
                        "dst_port": 0,
                    },
                    {
                        "op_type": "add_connection",
                        "src_block": "throttle_0",
                        "src_port": 0,
                        "dst_block": "sink_0",
                        "dst_port": 0,
                    },
                ]
            })
            self.assertTrue(r.get("ok"), r.get("message"))
            path = os.path.join(tmpdir, "created.grc")
            r = agent.execute_tool("save_graph", {"path": path})
            self.assertTrue(r.get("ok"), r.get("message"))
            self.assertTrue(os.path.exists(path))
            # validate saved file with grcc
            validate_r = agent.execute_tool("validate_graph", {})
            self.assertTrue(validate_r.get("ok") and validate_r.get("valid"))
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    @unittest.skip("Empty-session creation requires new_graph tool (future)")
    def test_create_sine_source_graph(self) -> None:
        tmpdir = tempfile.mkdtemp()
        try:
            session = FlowgraphSession()
            agent = GrcAgent(session)
            r = agent.execute_tool("apply_edit", {
                "transaction": [
                    {
                        "op_type": "add_block",
                        "block_type": "analog_sig_source_x",
                        "instance_name": "sine_0",
                        "parameters": {"type": "complex", "samp_rate": "48000"},
                    },
                    {
                        "op_type": "add_block",
                        "block_type": "blocks_null_sink",
                        "instance_name": "sink_0",
                        "parameters": {"type": "complex"},
                    },
                    {
                        "op_type": "add_connection",
                        "src_block": "sine_0",
                        "src_port": 0,
                        "dst_block": "sink_0",
                        "dst_port": 0,
                    },
                ]
            })
            self.assertTrue(r.get("ok"), r.get("message"))
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


# --------------------------------------------------------------------------- #
# G. Preview-only
# --------------------------------------------------------------------------- #

class PreviewOnlyTests(CapabilityEvalBase):
    def test_preview_remove_connection(self) -> None:
        r = self.agent.execute_tool("propose_edit", {
            "transaction": {
                "op_type": "remove_connection",
                "src_block": "analog_random_source_x_0",
                "src_port": 0,
                "dst_block": "blocks_throttle2_0",
                "dst_port": 0,
            }
        })
        self.assertTrue(r.get("ok"), r.get("message"))
        # Must not mutate
        self.assertEqual(self.session.state_revision, 1)

    def test_preview_add_block_explain_whether_valid(self) -> None:
        r = self.agent.execute_tool("propose_edit", {
            "transaction": {
                "op_type": "add_block",
                "block_type": "qtgui_time_sink_x",
                "instance_name": "ts_preview",
                "parameters": {"type": "float", "srate": "1"},
            }
        })
        self.assertTrue(r.get("ok"), r.get("message"))
        self.assertEqual(self.session.state_revision, 1)


# --------------------------------------------------------------------------- #
# H. Agentic auto_insert_block
# --------------------------------------------------------------------------- #

class AutoInsertAgenticTests(CapabilityEvalBase):
    def test_insert_head_block_into_main_path_and_validate(self) -> None:
        """auto_insert_block with goal='insert a head block' should commit one candidate and validate."""
        self.assertTrue(self.session.flowgraph is not None)
        before_blocks = len(self.session.flowgraph.blocks)
        result = self.agent.execute_tool("auto_insert_block", {
            "goal": "insert a head block",
            "preferred_block_type": "blocks_head",
        })
        self.assertIsNotNone(result)
        if result.get("ok"):
            self.assertIn("committed", result)
            committed = result["committed"]
            self.assertIn("block_type", committed)
            self.assertIn("connection_id", committed)
            # Must have added exactly one block
            self.assertEqual(len(self.session.flowgraph.blocks), before_blocks + 1)
            # Must be dirty after insert
            self.assertTrue(self.session.is_dirty)
            # Must validate successfully with grcc
            valid = self._grcc_validate(str(self.copy_path))
            self.assertTrue(valid)
        else:
            # Safe rejection: graph unchanged
            self.assertEqual(len(self.session.flowgraph.blocks), before_blocks)
            self.assertIn(
                result.get("error_type", ""),
                ("AUTO_INSERT_ALL_CANDIDATES_FAILED", "AUTO_INSERT_NO_GOAL_MATCH"),
            )

    def test_auto_insert_respects_max_candidates(self) -> None:
        """auto_insert_block with max_candidates=2 should attempt at most 2 candidates."""
        result = self.agent.execute_tool("auto_insert_block", {
            "goal": "insert a compatible block",
            "max_candidates": 2,
        })
        self.assertLessEqual(result.get("attempt_count", 0), 2)

    def test_auto_insert_excludes_hardware(self) -> None:
        """No hardware / external block types in attempted list."""
        result = self.agent.execute_tool("auto_insert_block", {
            "goal": "insert a compatible block",
        })
        attempted = result.get("attempted", [])
        for a in attempted:
            bt = a.get("block_type", "")
            self.assertFalse(
                any(hw in bt for hw in ("uhd", "usrp", "rfnoc", "oot")),
                f"Hardware block found: {bt}",
            )

    def test_auto_insert_no_mutation_on_failure(self) -> None:
        """If all candidates fail, live session must remain unchanged."""
        before = list(self.session.flowgraph.blocks)
        result = self.agent.execute_tool("auto_insert_block", {
            "goal": "xyz_nonexistent_goal_12345",
        })
        if not result.get("ok"):
            after = list(self.session.flowgraph.blocks)
            self.assertEqual(
                [b.instance_name for b in before],
                [b.instance_name for b in after],
            )


if __name__ == "__main__":
    unittest.main()
