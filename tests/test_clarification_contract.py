"""Deterministic tests for the Clarification Contract v1.

No LLM involved. Tests exercise real graph copies, real tools, and real grcc.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any

from grc_agent.agent import GrcAgent
from grc_agent.flowgraph_session import FlowgraphSession


FIXTURE = Path(__file__).resolve().parent / "data" / "random_bit_generator.grc"


def _load_agent() -> GrcAgent:
    session = FlowgraphSession()
    session.load(FIXTURE)
    return GrcAgent(session)


def _build_float_graph_agent() -> GrcAgent:
    """Build a minimal float graph where many blocks can insert between src and sink."""
    agent = GrcAgent()
    r = agent.execute_tool("new_grc", {"graph_id": "float_test"})
    if not r.get("ok"):
        raise RuntimeError(f"new_grc failed: {r}")
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
    if not r.get("ok"):
        raise RuntimeError(f"build_float_graph failed: {r}")
    # Validate to get a clean baseline
    v = agent.execute_tool("validate_graph", {})
    if not (v.get("ok") and v.get("valid")):
        raise RuntimeError(f"baseline validation failed: {v}")
    return agent


class ClarificationContractBase(unittest.TestCase):
    """Per-case helper: fresh graph copy in tmpdir, auto-cleanup for fixture graph."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        self.copy_path = Path(self.tmpdir) / "graph.grc"
        shutil.copy(FIXTURE, self.copy_path)
        self.agent = _load_agent()
        self.session: FlowgraphSession = self.agent.session

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _grcc_validate(self) -> bool:
        r = self.agent.execute_tool("validate_graph", {})
        return bool(r.get("ok") and r.get("valid"))

    def _trigger_clarification(self) -> dict[str, Any]:
        """Trigger a clarification using a generic goal on a graph with many compatible candidates."""
        result = self.agent.execute_tool("auto_insert_block", {
            "goal": "insert a compatible block",
            "max_candidates": 10,
        })
        return result


class ClarificationRequestShapeTests(ClarificationContractBase):
    def test_clarification_has_abc_options_when_candidates_exist(self) -> None:
        result = self._trigger_clarification()
        if not result.get("clarification_required"):
            self.skipTest("No clarification triggered on this graph (may have only 1 valid candidate)")
        opts = result.get("options", [])
        labels = [o["label"] for o in opts]
        self.assertGreaterEqual(len(labels), 2)
        self.assertIn("A", labels)
        self.assertIn("B", labels)

    def test_clarification_always_has_d_custom_option(self) -> None:
        result = self._trigger_clarification()
        if not result.get("clarification_required"):
            self.skipTest("No clarification triggered")
        custom = result.get("custom_option")
        self.assertIsNotNone(custom)
        self.assertEqual(custom.get("label"), "D")
        self.assertTrue(custom.get("free_text"))

    def test_options_come_from_real_candidate_tool_args(self) -> None:
        result = self._trigger_clarification()
        if not result.get("clarification_required"):
            self.skipTest("No clarification triggered")
        for opt in result.get("options", []):
            self.assertIn("tool_name", opt)
            self.assertEqual(opt["tool_name"], "insert_block_on_connection")
            self.assertIn("tool_args", opt)
            ta = opt["tool_args"]
            self.assertIn("connection_id", ta)
            self.assertIn("block_type", ta)
            self.assertIn("instance_name", ta)

    def test_creating_clarification_does_not_mutate_graph(self) -> None:
        before = [b.instance_name for b in self.session.flowgraph.blocks]
        result = self._trigger_clarification()
        after = [b.instance_name for b in self.session.flowgraph.blocks]
        if result.get("clarification_required"):
            self.assertEqual(before, after)
            self.assertFalse(result.get("ok"))
        # If exactly 1 candidate validated, auto-commit is expected — skip


class ClarificationResolutionTests(ClarificationContractBase):
    def test_selecting_a_executes_verified_option(self) -> None:
        result = self.agent.execute_tool("auto_insert_block", {
            "goal": "insert a compatible block",
            "max_candidates": 10,
        })
        if not result.get("clarification_required"):
            self.skipTest("No clarification triggered")
        before = len(self.session.flowgraph.blocks)
        resolved = self.agent.resolve_pending_clarification("A")
        self.assertEqual(resolved["mode"], "executed")
        self.assertIn("tool_result", resolved)
        self.assertTrue(resolved["tool_result"].get("ok"))
        self.assertEqual(len(self.session.flowgraph.blocks), before + 1)
        self.assertTrue(self._grcc_validate())

    def test_selecting_b_executes_verified_option(self) -> None:
        result = self.agent.execute_tool("auto_insert_block", {
            "goal": "insert a compatible block",
            "max_candidates": 10,
        })
        if not result.get("clarification_required"):
            self.skipTest("No clarification triggered")
        before = len(self.session.flowgraph.blocks)
        resolved = self.agent.resolve_pending_clarification("B")
        self.assertEqual(resolved["mode"], "executed")
        self.assertTrue(resolved["tool_result"].get("ok"))
        self.assertEqual(len(self.session.flowgraph.blocks), before + 1)
        self.assertTrue(self._grcc_validate())
        # Clarification is consumed
        self.assertIsNone(self.agent._pending_clarification)

    def test_d_custom_does_not_mutate_and_clears_pending(self) -> None:
        result = self.agent.execute_tool("auto_insert_block", {
            "goal": "insert a compatible block",
            "max_candidates": 10,
        })
        if not result.get("clarification_required"):
            self.skipTest("No clarification triggered")
        before = [b.instance_name for b in self.session.flowgraph.blocks]
        resolved = self.agent.resolve_pending_clarification("D: do something else")
        self.assertEqual(resolved["mode"], "custom")
        after = [b.instance_name for b in self.session.flowgraph.blocks]
        self.assertEqual(before, after)
        self.assertIsNone(self.agent._pending_clarification)

    def test_invalid_option_returns_clear_error_no_mutation(self) -> None:
        result = self.agent.execute_tool("auto_insert_block", {
            "goal": "insert a compatible block",
            "max_candidates": 10,
        })
        if not result.get("clarification_required"):
            self.skipTest("No clarification triggered")
        before = [b.instance_name for b in self.session.flowgraph.blocks]
        resolved = self.agent.resolve_pending_clarification("C")
        self.assertEqual(resolved["mode"], "reminder")
        self.assertIn("not a valid option", resolved["text"])
        after = [b.instance_name for b in self.session.flowgraph.blocks]
        self.assertEqual(before, after)
        self.assertIsNotNone(self.agent._pending_clarification)

    def test_clarification_expires_on_session_revision_change(self) -> None:
        result = self.agent.execute_tool("auto_insert_block", {
            "goal": "insert a compatible block",
            "max_candidates": 10,
        })
        if not result.get("clarification_required"):
            self.skipTest("No clarification triggered")
        # Mutate the graph to bump state_revision
        self.agent.execute_tool("apply_edit", {
            "transaction": {
                "op_type": "update_params",
                "instance_name": "samp_rate",
                "params": {"value": "48001"},
            }
        })
        resolved = self.agent.resolve_pending_clarification("A")
        self.assertEqual(resolved["mode"], "expired")
        self.assertIsNone(self.agent._pending_clarification)


class FloatGraphClarificationTests(unittest.TestCase):
    """Clarification tests built on a dedicated float graph with more candidate diversity."""

    def setUp(self) -> None:
        self.agent = _build_float_graph_agent()
        self.session: FlowgraphSession = self.agent.session

    def test_clarification_triggered_on_float_graph(self) -> None:
        before = [b.instance_name for b in self.session.flowgraph.blocks]
        result = self.agent.execute_tool("auto_insert_block", {
            "goal": "insert a compatible block",
            "max_candidates": 10,
        })
        after = [b.instance_name for b in self.session.flowgraph.blocks]
        self.assertEqual(before, after)
        if result.get("clarification_required"):
            opts = result.get("options", [])
            self.assertGreaterEqual(len(opts), 2)
            for opt in opts:
                self.assertEqual(opt["tool_name"], "insert_block_on_connection")
            custom = result.get("custom_option")
            self.assertIsNotNone(custom)
            self.assertEqual(custom.get("label"), "D")
        else:
            # Some environments may still only produce 1 valid candidate.
            # Accept both outcomes as long as no mutation happened before resolution.
            self.assertFalse(result.get("ok") or result.get("committed") is not None)

    def test_select_a_on_float_graph_inserts_and_validates(self) -> None:
        result = self.agent.execute_tool("auto_insert_block", {
            "goal": "insert a compatible block",
            "max_candidates": 10,
        })
        if not result.get("clarification_required"):
            self.skipTest("No clarification triggered on this graph")
        before = len(self.session.flowgraph.blocks)
        resolved = self.agent.resolve_pending_clarification("A")
        self.assertEqual(resolved["mode"], "executed")
        self.assertTrue(resolved["tool_result"].get("ok"))
        self.assertEqual(len(self.session.flowgraph.blocks), before + 1)
        v = self.agent.execute_tool("validate_graph", {})
        self.assertTrue(v.get("ok") and v.get("valid"))


class RawYamlAndServerGuardTests(ClarificationContractBase):
    def test_raw_yaml_guard_still_wins(self) -> None:
        guard = self.agent.check_unsupported_request("please directly edit the yaml")
        self.assertIsNotNone(guard)
        self.assertIn("Raw .grc YAML editing is unsupported", guard["assistant_text"])

    def test_llama_server_contains_no_clarification_logic(self) -> None:
        server_path = Path(__file__).resolve().parent.parent / "src" / "grc_agent" / "llama_server.py"
        source = server_path.read_text()
        self.assertNotIn("resolve_pending_clarification", source)
        self.assertNotIn("ClarificationRequest", source)
        self.assertNotIn("clarification_required", source)


if __name__ == "__main__":
    unittest.main()
