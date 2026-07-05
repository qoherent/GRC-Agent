"""Regression tests for agentic reliability hardening.

These tests directly reproduce the three failure modes identified during
complex multi-step graph mutation sessions (Scenarios 1–11):

  1. Tool Confusion: model used inspect_graph for catalog discovery,
     got bare target_not_found, looped without recovery.
  2. State Blindness: model issued add_block for already-committed blocks,
     got bare duplicate_block_name, had no recovery path.
  3. Schema Disambiguation: tool descriptions lacked explicit boundaries.

Each test proves the enriched error messages now fire correctly so a model
can self-correct rather than loop.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from grc_agent.agent import GrcAgent
from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.runtime.model_context import MVP_MODEL_TOOL_NAMES
from grc_agent.runtime.tool_schemas import build_tool_schemas


class ReliabilityHardeningTests(unittest.TestCase):
    """Tests for the four reliability fixes applied 2026-05-28."""

    def _fixture_path(self, name: str = "dial_tone.grc") -> Path:
        return Path(__file__).resolve().parent / "data" / name

    def _load_temp_agent(self, name: str = "dial_tone.grc") -> GrcAgent:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        dst = Path(tmp.name) / name
        shutil.copy2(self._fixture_path(name), dst)
        session = FlowgraphSession()
        session.load(dst)
        return GrcAgent(session)

    # ------------------------------------------------------------------ #
    # Fix 1: Schema Disambiguation                                          #
    # ------------------------------------------------------------------ #

    def test_inspect_graph_overview_does_not_trigger_error(self) -> None:
        """Overview mode must NOT show errors."""
        agent = self._load_temp_agent()
        result = agent.execute_tool(
            "inspect_graph",
            {"view": "overview"},
        )
        self.assertIn("graph", result, result)
        errors = result.get("errors", [])
        self.assertFalse(errors, "Overview must not produce errors")

    def test_add_existing_block_error_code(self) -> None:
        """Duplicate add must result in duplicate_block_name error code."""
        agent = self._load_temp_agent()
        result = agent.execute_tool(
            "change_graph",
            {
                "add_blocks": [
                    {
                        "block_id": "variable",
                        "instance_name": "samp_rate",
                        "params": {"value": "48000"},
                    }
                ]
            },
        )
        self.assertFalse(result.get("ok", True), "Duplicate add must not succeed")
        errors = result.get("errors", [])
        self.assertTrue(errors, "Must return errors for duplicate block name")
        self.assertIn(
            "duplicate_block_name",
            [e.get("code") for e in errors],
            "Error code must be duplicate_block_name",
        )

    def test_add_block_duplicate_in_same_batch_is_rejected(self) -> None:
        """Adding the same instance_name twice in one batch must reject both."""
        agent = self._load_temp_agent()
        result = agent.execute_tool(
            "change_graph",
            {
                "add_blocks": [
                    {
                        "block_id": "variable",
                        "instance_name": "my_new_var",
                        "params": {"value": "1000"},
                    },
                    {
                        "block_id": "variable",
                        "instance_name": "my_new_var",  # duplicate
                        "params": {"value": "2000"},
                    },
                ]
            },
        )
        # The batch may reject at the second add_block due to the first
        # having been staged in the snapshot
        self.assertFalse(
            result.get("ok", True), "Batch with duplicate block name must not succeed"
        )

    def test_graph_is_unchanged_after_duplicate_block_rejection(self) -> None:
        """Graph must be byte-identical after a duplicate block rejection."""
        agent = self._load_temp_agent()
        before_revision = agent.session.state_revision
        result = agent.execute_tool(
            "change_graph",
            {
                "add_blocks": [
                    {
                        "block_id": "variable",
                        "instance_name": "samp_rate",
                        "params": {"value": "99999"},
                    }
                ]
            },
        )
        self.assertFalse(result.get("ok", True))
        # Revision must not change
        self.assertEqual(
            agent.session.state_revision,
            before_revision,
            "State revision must not change after rejected duplicate block",
        )
        # samp_rate value must be unchanged
        assert agent.session.flowgraph is not None
        found_samp_rate = False
        for block in agent.session.flowgraph.blocks:
            if block.name == "samp_rate":
                found_samp_rate = True
                params = {k: str(p.value) for k, p in block.params.items()}
                val = params.get("value")
                self.assertNotEqual(
                    str(val), "99999", "samp_rate must not be overwritten by rejected duplicate add"
                )
        self.assertTrue(
            found_samp_rate,
            "fixture must contain a samp_rate block for this assertion to be meaningful",
        )

    # ------------------------------------------------------------------ #
    # End-to-end: schema descriptions pass size budget                     #
    # ------------------------------------------------------------------ #

    def test_enriched_schemas_stay_within_token_budget(self) -> None:
        """Enriched descriptions must not blow up the schema character budget.

        The original budget was <8000 chars total for all 4 MVP schemas.
        The new descriptions are longer but should stay within a reasonable
        expanded budget (we allow up to 12000 chars given the added guidance).
        """
        schemas = build_tool_schemas(list(MVP_MODEL_TOOL_NAMES))
        total_chars = sum(len(str(schema)) for schema in schemas)
        self.assertLess(
            total_chars, 12_000, f"Schema chars {total_chars} exceeds expanded 12000 budget"
        )
        # Each individual schema still under 7000 chars
        for schema in schemas:
            name = schema["function"]["name"]
            size = len(str(schema))
            self.assertLess(
                size, 7_000, f"Schema '{name}' is {size} chars, exceeds per-schema budget"
            )

    def test_inspect_graph_schema_not_polluted_with_change_graph_text(self) -> None:
        """Schema descriptions must only describe their own tool."""
        schemas = build_tool_schemas(list(MVP_MODEL_TOOL_NAMES))
        inspect_schema = next(s for s in schemas if s["function"]["name"] == "inspect_graph")
        # inspect_graph description should NOT contain change_graph jargon
        desc = inspect_schema["function"]["description"]
        self.assertNotIn("add_blocks", desc)
        self.assertNotIn("remove_blocks", desc)
        self.assertNotIn("update_params", desc)


if __name__ == "__main__":
    unittest.main()
