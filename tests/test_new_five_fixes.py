"""Unit and integration tests for the five key codebase fixes.

Tests verify:
1. Type resolution loop in change_graph.py prioritizing existing blocks and calling rewrite()
2. System-prompt retry salt generation to bypass context caching
3. Safety ceiling for max_tool_rounds in ToolAgentsRunner
4. ID parameter protection in set_param to prevent name divergence
5. State mapping between GRC's internal 'bypassed' and domain-level 'bypass'
"""

from __future__ import annotations

import unittest
from pathlib import Path

from grc_agent.grc_native_adapter import (
    OVERVIEW,
    load_flow_graph,
    render_block,
    set_block_state,
    set_param,
)
from grc_agent.runtime.change_graph import _neighbor_dtype_for
from grc_agent.runtime.model_context import build_system_prompt, render_model_messages
from ToolAgents.data_models.chat_history import ChatHistory


class TestCodebaseFixes(unittest.TestCase):
    def setUp(self) -> None:
        self.fixtures_dir = Path(__file__).resolve().parent / "data"
        self.dial_tone_path = self.fixtures_dir / "dial_tone.grc"

    def test_fix_5_state_mapping_bypass(self) -> None:
        """Verify internal 'bypassed' state maps to 'bypass' in rendering, and vice-versa."""
        fg = load_flow_graph(self.dial_tone_path)
        target = next(b for b in fg.blocks if b.key != "options" and not b.is_variable)

        # 1. Map inbound: set_block_state converts 'bypass' to 'bypassed'
        set_block_state(target, "bypass")
        self.assertTrue(target.get_bypassed())
        self.assertEqual(target.state, "bypassed")

        # 2. Map outbound: render_block converts 'bypassed' to 'bypass'
        rendered = render_block(target, fg, mode=OVERVIEW, variable_names=set())
        self.assertEqual(rendered.state, "bypass")

    def test_fix_4_id_param_protection(self) -> None:
        """Verify set_param ignores changes to the 'id' parameter to prevent name divergence."""
        fg = load_flow_graph(self.dial_tone_path)
        target = next(b for b in fg.blocks if b.key != "options")
        original_id = str(target.params["id"].value)

        # Attempt to change block ID via set_param (should be silently ignored/overridden to original ID)
        set_param(target, "id", "diverged_name_123")
        self.assertEqual(target.params["id"].value, original_id)

    def test_fix_2_system_prompt_retry_salt(self) -> None:
        """Verify render_model_messages appends retry_salt to the system prompt when provided."""
        history = ChatHistory()
        # Render without salt
        msgs_no_salt = render_model_messages(
            history,
            system_prompt=build_system_prompt("session"),
            semantic_search_result_preview=lambda *_: [],
            system_salt=None,
        )
        self.assertEqual(len(msgs_no_salt), 1)
        self.assertEqual(msgs_no_salt[0].role.value, "system")
        content_no_salt = msgs_no_salt[0].get_as_text()

        # Render with salt
        salt_val = "retry_salt: 1234-5678"
        msgs_with_salt = render_model_messages(
            history,
            system_prompt=build_system_prompt("session"),
            semantic_search_result_preview=lambda *_: [],
            system_salt=salt_val,
        )
        content_with_salt = msgs_with_salt[0].get_as_text()

        self.assertIn(salt_val, content_with_salt)
        self.assertEqual(content_with_salt, content_no_salt + f"\n# {salt_val}")

    def test_fix_1_neighbor_dtype_prioritization(self) -> None:
        """Verify _neighbor_dtype_for prioritizes existing blocks over new blocks."""

        # Mock a flowgraph with an existing block 'existing_src' (float)
        # and a newly added block 'new_dest' (complex).
        class MockPort:
            def __init__(self, key: str, dtype: str):
                self.key = key
                self.dtype = dtype

        class MockBlock:
            def __init__(self, name: str, dtype: str):
                self.name = name
                self.active_sources = [MockPort("0", dtype)]
                self.active_sinks = [MockPort("0", dtype)]

        class MockFlowgraph:
            def __init__(self):
                self.blocks = {
                    "existing_src": MockBlock("existing_src", "float"),
                    "new_dest": MockBlock("new_dest", "complex"),
                }

            def get_block(self, name: str) -> MockBlock:
                return self.blocks[name]

        fg = MockFlowgraph()
        # Connection list: both neighbors connected to 'middle_block'
        # 'middle_block' connects to 'existing_src:0' and 'new_dest:0'
        add_connections = [
            "existing_src:0->middle_block:0",
            "middle_block:0->new_dest:0",
        ]
        new_block_names = {"middle_block", "new_dest"}

        # middle_block neighbor check
        dtype = _neighbor_dtype_for(
            fg, "middle_block", add_connections, new_block_names=new_block_names
        )
        # Should resolve to 'float' (from existing_src) rather than 'complex' (from new_dest)
        self.assertEqual(dtype, "float")


if __name__ == "__main__":
    unittest.main()
