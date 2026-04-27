"""Deterministic UX tests for Clarification Contract v1 presentation and routing.

No LLM. Tests renderers, CLI helpers, and agent resolution.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any

from grc_agent.agent import GrcAgent
from grc_agent.runtime.clarification import (
    ClarificationOption,
    ClarificationRequest,
    CustomClarificationOption,
    render_clarification_prompt,
)


def _build_float_graph_agent() -> GrcAgent:
    """Build a minimal float graph where several blocks can validate."""
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


class RendererTests(unittest.TestCase):
    def _sample_payload(self) -> dict[str, Any]:
        req = ClarificationRequest(
            kind="choose_insert_candidate",
            question="Multiple valid options were found. Which should be inserted?",
            options=[
                ClarificationOption(
                    label="A",
                    title="Insert blocks_head",
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
                    title="Insert blocks_throttle2",
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
            clarification_id="test-id",
        )
        return req.to_dict()

    def test_renders_abc_labels(self) -> None:
        text = render_clarification_prompt(self._sample_payload())
        self.assertIn("A)", text)
        self.assertIn("B)", text)

    def test_renders_d_custom(self) -> None:
        text = render_clarification_prompt(self._sample_payload())
        self.assertIn("D) Other / custom", text)
        self.assertIn("free text", text)

    def test_renders_block_type_and_connection_id(self) -> None:
        text = render_clarification_prompt(self._sample_payload())
        self.assertIn("block_type=blocks_head", text)
        self.assertIn("connection_id=src_0:0->throttle_0:0", text)

    def test_renders_params_summary(self) -> None:
        text = render_clarification_prompt(self._sample_payload())
        self.assertIn("num_items='1024'", text)

    def test_does_not_include_raw_json_braces_dump(self) -> None:
        text = render_clarification_prompt(self._sample_payload())
        # Should not contain a raw JSON object dump like {"label": "A" ...}
        self.assertNotIn('"label": "A"', text)
        self.assertNotIn('"tool_args"', text)

    def test_renders_reply_hint(self) -> None:
        text = render_clarification_prompt(self._sample_payload())
        self.assertIn("Reply with the letter", text)


class PendingReplyRoutingTests(unittest.TestCase):
    """Test resolution routing through GrcAgent.resolve_pending_clarification."""

    agent: GrcAgent

    def setUp(self) -> None:
        self.agent = _build_float_graph_agent()

    def _trigger_clarification(self) -> dict[str, Any]:
        result = self.agent.execute_tool("auto_insert_block", {
            "goal": "insert a compatible block",
            "max_candidates": 10,
        })
        return result

    def test_user_reply_a_executes_option_a(self) -> None:
        result = self._trigger_clarification()
        if not result.get("clarification_required"):
            self.skipTest("No clarification triggered")
        before = len(self.agent.session.flowgraph.blocks)
        resolved = self.agent.resolve_pending_clarification("A")
        self.assertEqual(resolved["mode"], "executed")
        self.assertTrue(resolved["tool_result"].get("ok"))
        self.assertEqual(len(self.agent.session.flowgraph.blocks), before + 1)
        self.assertIsNone(self.agent._pending_clarification)

    def test_user_reply_b_executes_option_b(self) -> None:
        result = self._trigger_clarification()
        if not result.get("clarification_required"):
            self.skipTest("No clarification triggered")
        before = len(self.agent.session.flowgraph.blocks)
        resolved = self.agent.resolve_pending_clarification("B")
        self.assertEqual(resolved["mode"], "executed")
        self.assertTrue(resolved["tool_result"].get("ok"))
        self.assertEqual(len(self.agent.session.flowgraph.blocks), before + 1)
        self.assertIsNone(self.agent._pending_clarification)

    def test_invalid_reply_keeps_pending_and_no_mutation(self) -> None:
        result = self._trigger_clarification()
        if not result.get("clarification_required"):
            self.skipTest("No clarification triggered")
        before = [b.instance_name for b in self.agent.session.flowgraph.blocks]
        resolved = self.agent.resolve_pending_clarification("Z")
        self.assertEqual(resolved["mode"], "reminder")
        after = [b.instance_name for b in self.agent.session.flowgraph.blocks]
        self.assertEqual(before, after)
        self.assertIsNotNone(self.agent._pending_clarification)

    def test_d_custom_clears_pending_and_no_mutation(self) -> None:
        result = self._trigger_clarification()
        if not result.get("clarification_required"):
            self.skipTest("No clarification triggered")
        before = [b.instance_name for b in self.agent.session.flowgraph.blocks]
        resolved = self.agent.resolve_pending_clarification("D: use a throttle instead")
        self.assertEqual(resolved["mode"], "custom")
        after = [b.instance_name for b in self.agent.session.flowgraph.blocks]
        self.assertEqual(before, after)
        self.assertIsNone(self.agent._pending_clarification)

    def test_session_revision_mismatch_expires_pending(self) -> None:
        result = self._trigger_clarification()
        if not result.get("clarification_required"):
            self.skipTest("No clarification triggered")
        self.agent.execute_tool("apply_edit", {
            "transaction": {
                "op_type": "update_params",
                "instance_name": "src_0",
                "params": {"freq": "99999"},
            }
        })
        resolved = self.agent.resolve_pending_clarification("A")
        self.assertEqual(resolved["mode"], "expired")
        self.assertIsNone(self.agent._pending_clarification)

    def test_llama_server_has_no_clarification_logic(self) -> None:
        server_path = Path(__file__).resolve().parent.parent / "src" / "grc_agent" / "llama_server.py"
        source = server_path.read_text()
        self.assertNotIn("resolve_pending_clarification", source)
        self.assertNotIn("render_clarification_prompt", source)
        self.assertNotIn("clarification_required", source)

    def test_raw_yaml_guard_outside_clarification_flow(self) -> None:
        guard = self.agent.check_unsupported_request("directly edit the yaml")
        self.assertIsNotNone(guard)
        self.assertIn("Raw .grc YAML editing is unsupported", guard["assistant_text"])

    def test_repl_helper_detects_pending_and_returns_true(self) -> None:
        """Simulate _maybe_render_pending_clarification behavior."""
        result = self._trigger_clarification()
        if not result.get("clarification_required"):
            self.skipTest("No clarification triggered")
        # The helper checks agent._pending_clarification is not None
        self.assertIsNotNone(self.agent._pending_clarification)
        prompt = render_clarification_prompt(self.agent._pending_clarification)
        self.assertIn("A)", prompt)
        self.assertIn("D)", prompt)
        # Ensure no JSON braces dump
        self.assertNotIn('"options"', prompt)


if __name__ == "__main__":
    unittest.main()
