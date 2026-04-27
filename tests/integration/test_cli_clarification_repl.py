"""Integration tests for CLI REPL clarification rendering and routing.

Exercises real _run_repl_loop and _maybe_render_pending_clarification
code paths in cli.py with seeded pending clarification state.

No live llama server required. No auto_insert_block dependency.
Agent-level verified execution is covered by test_clarification_contract.
"""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import MagicMock, patch

from grc_agent.agent import GrcAgent
from grc_agent.cli import _maybe_render_pending_clarification, _run_repl_loop
from grc_agent.runtime.clarification import (
    ClarificationOption,
    ClarificationRequest,
    CustomClarificationOption,
)


def _build_float_graph_agent() -> GrcAgent:
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
    v = agent.execute_tool("validate_graph", {})
    if not (v.get("ok") and v.get("valid")):
        raise RuntimeError(f"baseline validation failed: {v}")
    return agent


def _seed_clarification(agent: GrcAgent) -> None:
    req = ClarificationRequest(
        kind="choose_insert_candidate",
        question="Which block should be inserted?",
        options=[
            ClarificationOption(
                label="A",
                title="Insert blocks_head into src_0:0->throttle_0:0",
                description="confidence: high",
                tool_name="insert_block_on_connection",
                tool_args={
                    "connection_id": "src_0:0->throttle_0:0",
                    "block_type": "blocks_head",
                    "instance_name": "head_0",
                    "params": {"type": "float", "num_items": "1024"},
                },
            ),
            ClarificationOption(
                label="B",
                title="Insert blocks_throttle2 into throttle_0:0->sink_0:0",
                description="confidence: high",
                tool_name="insert_block_on_connection",
                tool_args={
                    "connection_id": "throttle_0:0->sink_0:0",
                    "block_type": "blocks_throttle2",
                    "instance_name": "throttle_1",
                    "params": {"type": "float", "samples_per_second": "32000"},
                },
            ),
        ],
        custom_option=CustomClarificationOption(label="D", title="Other / custom"),
    )
    agent._store_pending_clarification(req.to_dict())


class ClarificationMCQRenderTests(unittest.TestCase):
    """Test _maybe_render_pending_clarification produces correct MCQ output.

    Renderer output format is also covered by test_clarification_ux.py;
    these tests verify the CLI wrapper function and its return contract.
    """

    def setUp(self) -> None:
        self.agent = _build_float_graph_agent()
        _seed_clarification(self.agent)

    def test_renders_header_and_mcq_from_seeded_payload(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            result = _maybe_render_pending_clarification(self.agent)
        text = buf.getvalue()
        self.assertTrue(result)
        self.assertIn("--- Clarification required ---", text)
        self.assertIn("A)", text)
        self.assertIn("B)", text)
        self.assertIn("D) Other / custom", text)
        self.assertIn("block_type=blocks_head", text)
        self.assertIn("connection_id=src_0:0->throttle_0:0", text)
        self.assertIn("Reply with the letter", text)
        self.assertNotIn('"tool_args"', text)
        self.assertNotIn('"options"', text)

    def test_returns_false_when_no_pending(self) -> None:
        self.agent._clear_pending_clarification()
        buf = io.StringIO()
        with redirect_stdout(buf):
            result = _maybe_render_pending_clarification(self.agent)
        self.assertFalse(result)
        self.assertEqual(buf.getvalue(), "")


class ClarificationREPLRoutingTests(unittest.TestCase):
    """Test REPL loop routing for clarification replies via _run_repl_loop."""

    def setUp(self) -> None:
        self.agent = _build_float_graph_agent()
        _seed_clarification(self.agent)

    def test_reply_a_routes_to_verified_tool_handler(self) -> None:
        """REPL routes 'A' through resolve_pending_clarification to execute_tool.

        The tool handler runs unmocked; this proves CLI routing reaches the
        verified execution path. Agent-level verified execution (grcc pass)
        is covered by test_clarification_contract.
        """
        buf = io.StringIO()
        with (
            patch("builtins.input", side_effect=["A", "/quit"]),
            redirect_stdout(buf),
        ):
            exit_code = _run_repl_loop(self.agent, MagicMock(), None)
        text = buf.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn("Executed", text)
        self.assertIsNone(self.agent._pending_clarification)

    def test_reply_invalid_keeps_pending_and_prints_reminder(self) -> None:
        buf = io.StringIO()
        with (
            patch("builtins.input", side_effect=["C", "/quit"]),
            redirect_stdout(buf),
        ):
            exit_code = _run_repl_loop(self.agent, MagicMock(), None)
        text = buf.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn("Reminder", text)
        self.assertIn("not a valid option", text)
        self.assertIsNotNone(self.agent._pending_clarification)

    def test_reply_d_custom_clears_pending_and_routes_to_model(self) -> None:
        before = [b.instance_name for b in self.agent.session.flowgraph.blocks]
        buf = io.StringIO()
        fake_model_result = {
            "ok": True,
            "assistant_text": "Understood, I will help with that.",
            "model": "test-model",
            "tool_calls_executed": 0,
            "steps": 1,
        }
        with (
            patch("builtins.input", side_effect=["D: use a different block", "/quit"]),
            patch("grc_agent.cli.run_bounded_llama_turn", return_value=fake_model_result),
            redirect_stdout(buf),
        ):
            exit_code = _run_repl_loop(self.agent, MagicMock(), None)
        text = buf.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIsNone(self.agent._pending_clarification)
        after = [b.instance_name for b in self.agent.session.flowgraph.blocks]
        self.assertEqual(before, after)
        self.assertIn("Understood", text)


if __name__ == "__main__":
    unittest.main()
