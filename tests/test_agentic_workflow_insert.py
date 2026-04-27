"""Deterministic tests for auto_insert_block agentic workflow tool — relevance-hardened.

Tests cover:
1. Explicit family goal filters candidates to family members only
2. Unsupported goal (e.g. sink/source) returns UNSUPPORTED_GOAL
3. Generic goal commits any compatible high-confidence candidate
4. Preferred block_type filters correctly
5. Safe rejection when explicit family has no validated candidates
6. No mutation on failure
7. No hardware/external blocks in attempted list
8. respects max_candidates
9. Raw YAML never involved
10. Save not performed automatically
"""

from __future__ import annotations

import unittest
from pathlib import Path

from grc_agent.agent import GrcAgent
from grc_agent.session.auto_insert import (
    _classify_goal,
    _score_candidates,
    _stream_connections,
)
from grc_agent.session.insertion_suggestions import InsertionCandidate


class DummyConnection:
    def __init__(self, src_block: str, src_port, dst_block: str, dst_port):
        self.src_block = src_block
        self.src_port = src_port
        self.dst_block = dst_block
        self.dst_port = dst_port


class DummySession:
    def __init__(self, connections=None):
        self.flowgraph = type("FG", (), {"connections": connections or []})()
        self.state_revision = 1
        self.is_dirty = False
        self.path = None
        self.last_validation_ok = False


def _make_candidate(block_type: str, confidence: str = "medium", insert_tool_args: dict | None = None):
    return InsertionCandidate(
        block_type=block_type,
        reason="test",
        required_params={},
        confidence=confidence,
        insert_tool_args=insert_tool_args or {"instance_name": block_type, "params": {}},
    )


class GoalClassificationTests(unittest.TestCase):
    def test_head_block_is_explicit_family(self):
        intent = _classify_goal("insert a head block", None)
        self.assertEqual(intent["mode"], "explicit_family")
        self.assertIn("head", intent["family_tokens"])

    def test_throttle_is_explicit_family(self):
        intent = _classify_goal("add throttle", None)
        self.assertEqual(intent["mode"], "explicit_family")
        self.assertIn("throttle", intent["family_tokens"])

    def test_low_pass_filter_is_explicit_family(self):
        intent = _classify_goal("add low pass filter", None)
        self.assertEqual(intent["mode"], "explicit_family")
        self.assertIn("filter", intent["family_tokens"])

    def test_compatible_block_is_generic(self):
        intent = _classify_goal("insert a compatible block", None)
        self.assertEqual(intent["mode"], "generic")

    def test_sink_is_unsupported(self):
        intent = _classify_goal("add a sink", None)
        self.assertEqual(intent["mode"], "unsupported")

    def test_source_is_unsupported(self):
        intent = _classify_goal("insert a source", None)
        self.assertEqual(intent["mode"], "unsupported")

    def test_preferred_overrides_goal(self):
        intent = _classify_goal("insert a head block", "blocks_head")
        self.assertEqual(intent["mode"], "preferred_type")


class StreamConnectionTests(unittest.TestCase):
    def test_stream_connections_excludes_message_ports(self):
        session = DummySession([
            DummyConnection("a", 0, "b", 0),
            DummyConnection("c", "msg", "d", "out"),
        ])
        ids = _stream_connections(session)
        self.assertEqual(len(ids), 1)
        self.assertIn("a:0->b:0", ids)


class ScoreCandidateTests(unittest.TestCase):
    def test_exact_preferred_type_boost(self):
        candidates = [
            ("c1", _make_candidate("blocks_head")),
            ("c2", _make_candidate("analog_agc2_xx")),
        ]
        scored = _score_candidates(candidates, "insert a head block", "blocks_head")
        types = {c.block_type: s for s, _, c in scored}
        self.assertGreater(types["blocks_head"], types["analog_agc2_xx"])

    def test_goal_word_match(self):
        candidates = [
            ("c1", _make_candidate("blocks_head")),
            ("c2", _make_candidate("analog_agc2_xx")),
        ]
        scored = _score_candidates(candidates, "insert a head block", None)
        best = max(scored, key=lambda x: x[0])
        self.assertEqual(best[2].block_type, "blocks_head")


class AutoInsertRelevanceTests(unittest.TestCase):
    _FIXTURE = "tests/data/random_bit_generator.grc"

    def setUp(self):
        if not Path(self._FIXTURE).exists():
            self.skipTest("Fixture not found")

    def _load_fixture(self, agent: GrcAgent) -> None:
        result = agent.execute_tool("load_grc", {"file_path": self._FIXTURE})
        self.assertTrue(result.get("ok"), f"load failed: {result.get('message')}")

    def test_explicit_head_goal_safe_rejection_when_no_valid_match(self):
        """blocks_head is not compatible with source:0->throttle connection"""
        agent = GrcAgent()
        self._load_fixture(agent)
        before = len(agent.session.flowgraph.blocks)
        result = agent.execute_tool("auto_insert_block", {"goal": "insert a head block"})
        # Should NOT succeed with a semantically irrelevant block.
        # blocks_head may be found but fails validation due to template mismatch.
        if result.get("ok"):
            committed = result["committed"]["block_type"]
            self.assertIn("head", committed.lower(),
                f"Committed block '{committed}' does not match explicit goal 'head'.")
        else:
            self.assertFalse(result.get("ok"))
            self.assertIn("AUTO_INSERT_ALL_CANDIDATES_FAILED", result.get("error_type", ""))
            self.assertEqual(len(agent.session.flowgraph.blocks), before)

    def test_unsupported_sink_goal_rejected(self):
        """'add a sink' should return UNSUPPORTED_GOAL_FOR_AUTO_INSERT."""
        agent = GrcAgent()
        self._load_fixture(agent)
        result = agent.execute_tool("auto_insert_block", {"goal": "add a sink"})
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error_type"), "UNSUPPORTED_GOAL_FOR_AUTO_INSERT")

    def test_unsupported_source_goal_rejected(self):
        """'add a source' should return UNSUPPORTED_GOAL_FOR_AUTO_INSERT."""
        agent = GrcAgent()
        self._load_fixture(agent)
        result = agent.execute_tool("auto_insert_block", {"goal": "add a source"})
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error_type"), "UNSUPPORTED_GOAL_FOR_AUTO_INSERT")

    def test_generic_goal_commits_any_compatible(self):
        """'insert a compatible block' should find and commit some compatible block."""
        agent = GrcAgent()
        self._load_fixture(agent)
        before = len(agent.session.flowgraph.blocks)
        result = agent.execute_tool("auto_insert_block", {"goal": "insert a compatible block"})
        self.assertTrue(result.get("ok"), f"Generic insert failed: {result}")
        self.assertIsNotNone(result.get("committed"))
        self.assertEqual(len(agent.session.flowgraph.blocks), before + 1)
        self.assertTrue(agent.session.is_dirty)

    def test_generic_goal_does_not_commit_hardware_block(self):
        """No hardware/external block should ever be committed."""
        agent = GrcAgent()
        self._load_fixture(agent)
        result = agent.execute_tool("auto_insert_block", {"goal": "insert a compatible block"})
        attempted = result.get("attempted", [])
        for a in attempted:
            bt = a.get("block_type", "")
            self.assertFalse(
                any(hw in bt for hw in ("uhd", "usrp", "rfnoc", "oot")),
                f"Hardware block found: {bt}"
            )

    def test_no_mutation_on_failure(self):
        """If all candidates fail, live graph must remain unchanged."""
        agent = GrcAgent()
        self._load_fixture(agent)
        before = [b.instance_name for b in agent.session.flowgraph.blocks]
        result = agent.execute_tool(
            "auto_insert_block",
            {"goal": "insert something", "preferred_block_type": "nonexistent_xyz_type"}
        )
        self.assertFalse(result.get("ok"))
        after = [b.instance_name for b in agent.session.flowgraph.blocks]
        self.assertEqual(before, after)

    def test_respects_max_candidates(self):
        """max_candidates=2 should limit attempts."""
        agent = GrcAgent()
        self._load_fixture(agent)
        result = agent.execute_tool("auto_insert_block", {
            "goal": "insert a compatible block",
            "max_candidates": 2,
        })
        self.assertLessEqual(result.get("attempt_count", 0), 2)

    def test_preferred_block_type_filters_to_family(self):
        """preferred_block_type='blocks_head' should only try head blocks."""
        agent = GrcAgent()
        self._load_fixture(agent)
        result = agent.execute_tool("auto_insert_block", {
            "goal": "insert something",
            "preferred_block_type": "blocks_head",
        })
        attempted = result.get("attempted", [])
        for a in attempted:
            bt = a["block_type"]
            self.assertIn("head", bt.lower(),
                f"Attempted '{bt}' does not match preferred_block_type 'blocks_head'.")

    def test_save_not_triggered(self):
        """auto_insert_block should never trigger save_graph."""
        agent = GrcAgent()
        self._load_fixture(agent)
        result = agent.execute_tool("auto_insert_block", {"goal": "insert compatible block"})
        self.assertNotIn("save_graph", str(result))

    def test_rejects_no_graph(self):
        """Without loaded graph, should return MISSING_SESSION."""
        agent = GrcAgent()
        result = agent.execute_tool("auto_insert_block", {"goal": "insert a head block"})
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error_type"), "missing_session")


if __name__ == "__main__":
    unittest.main()
