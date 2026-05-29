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

from pathlib import Path
import shutil
import tempfile
import unittest

from grc_agent.agent import GrcAgent
from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.runtime.tool_schemas import build_tool_schemas
from grc_agent.runtime.tool_surface import MVP_MODEL_TOOL_NAMES


class ReliabilityHardeningTests(unittest.TestCase):
    """Tests for the four reliability fixes applied 2026-05-28."""

    def _fixture_path(self, name: str = "random_bit_generator.grc") -> Path:
        return Path(__file__).resolve().parent / "data" / name

    def _load_temp_agent(self, name: str = "random_bit_generator.grc") -> GrcAgent:
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

    def test_inspect_graph_schema_has_explicit_do_not_use_boundary(self) -> None:
        """inspect_graph description must explicitly forbid catalog discovery."""
        schemas = build_tool_schemas(list(MVP_MODEL_TOOL_NAMES))
        inspect_schema = next(
            s for s in schemas if s["function"]["name"] == "inspect_graph"
        )
        desc = inspect_schema["function"]["description"]
        self.assertIn("live", desc.lower(), "Must say 'live' to bound to active graph")
        self.assertIn("do not", desc.lower(), "Must have a negative boundary")
        # The key check: the description must tell the model what NOT to use it for
        self.assertIn("do not use this to discover", desc.lower(),
            "Must explicitly say not to use for catalog/block type discovery")
        # Must not have change_graph jargon bleeding into the description
        self.assertNotIn("add_blocks", desc)


    def test_change_graph_schema_mandates_inspect_first(self) -> None:
        """change_graph description must mandate inspect_graph before mutation."""
        schemas = build_tool_schemas(list(MVP_MODEL_TOOL_NAMES))
        change_schema = next(
            s for s in schemas if s["function"]["name"] == "change_graph"
        )
        desc = change_schema["function"]["description"]
        self.assertIn("inspect_graph", desc, "Must mandate inspect_graph pre-check")
        self.assertIn("never assume graph state", desc.lower())

    # ------------------------------------------------------------------ #
    # Fix 2: inspect_graph target_not_found recovery hint                  #
    # ------------------------------------------------------------------ #

    def test_inspect_graph_catalog_block_type_error_hints_search_blocks(self) -> None:
        """Failure mode 1: model searches for a catalog block type in the graph.

        When inspect_graph is called with a target that looks like a catalog
        block ID (e.g. 'analog_agc_cc', 'low_pass_filter') that is not an
        active graph instance, the error must tell the model to use search_blocks.
        """
        agent = self._load_temp_agent()
        # Try to inspect a catalog block type ID that is NOT in the graph
        result = agent.execute_tool(
            "inspect_graph",
            {"view": "details", "targets": ["analog_agc_cc"]},
        )
        self.assertFalse(result["ok"], result)
        errors = result.get("errors", [])
        self.assertTrue(errors, "Must return errors when target not found")
        error_msg = " ".join(str(e.get("message", "")) for e in errors).lower()
        self.assertIn("target_not_found".lower(), " ".join(e.get("code", "") for e in errors).lower(),
            "Error code must be target_not_found")
        self.assertIn("search_blocks", error_msg,
            "Error message must suggest using search_blocks for catalog discovery")

    def test_inspect_graph_unknown_block_type_error_hints_search_blocks(self) -> None:
        """Searching for any non-existent name hints search_blocks."""
        agent = self._load_temp_agent()
        result = agent.execute_tool(
            "inspect_graph",
            {"view": "details", "targets": ["low_pass_filter"]},
        )
        errors = result.get("errors", [])
        error_msg = " ".join(str(e.get("message", "")) for e in errors).lower()
        self.assertIn("search_blocks", error_msg,
            "Must hint search_blocks when a catalog block type is queried")

    def test_inspect_graph_existing_block_does_not_trigger_search_hint(self) -> None:
        """A valid graph target must NOT show the search_blocks hint."""
        agent = self._load_temp_agent()
        # samp_rate IS in the graph as a variable block
        result = agent.execute_tool(
            "inspect_graph",
            {"view": "details", "targets": ["samp_rate"]},
        )
        self.assertTrue(result["ok"], result)
        # No errors at all for a valid target
        errors = result.get("errors", [])
        self.assertFalse(errors, "Valid target must not produce errors")

    # ------------------------------------------------------------------ #
    # Fix 3: Duplicate block name recovery hint                             #
    # ------------------------------------------------------------------ #

    def test_add_existing_block_error_hints_inspect_graph(self) -> None:
        """Failure mode 2: model tries to add a block that already exists.

        When change_graph.add_blocks is called with an instance_name that is
        already present in the graph (state blindness scenario), the
        duplicate_block_name error must tell the model to call inspect_graph.
        """
        agent = self._load_temp_agent()
        # 'samp_rate' is already in the graph as a variable block
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
        self.assertFalse(result.get("committed", True),
            "Duplicate add must not commit")
        errors = result.get("errors", [])
        self.assertTrue(errors, "Must return errors for duplicate block name")
        error_msg = " ".join(str(e.get("message", "")) for e in errors).lower()
        codes = " ".join(e.get("code", "") for e in errors).lower()
        self.assertIn("duplicate_block_name", codes,
            "Error code must be duplicate_block_name")
        self.assertIn("inspect_graph", error_msg,
            "Error must suggest calling inspect_graph to verify current state")
        self.assertIn("previous turn", error_msg,
            "Error must mention 'previous turn' to address state blindness")

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
        self.assertFalse(result.get("committed", True),
            "Batch with duplicate block name must not commit")

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
        self.assertFalse(result.get("committed", True))
        # Revision must not change
        self.assertEqual(agent.session.state_revision, before_revision,
            "State revision must not change after rejected duplicate block")
        # samp_rate value must be unchanged
        assert agent.session.flowgraph is not None
        for block in agent.session.flowgraph.blocks:
            if block.instance_name == "samp_rate":
                params = block.params.get("parameters", {})
                val = params.get("value")
                self.assertNotEqual(str(val), "99999",
                    "samp_rate must not be overwritten by rejected duplicate add")
                break

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
        self.assertLess(total_chars, 12_000,
            f"Schema chars {total_chars} exceeds expanded 12000 budget")
        # Each individual schema still under 7000 chars
        for schema in schemas:
            name = schema["function"]["name"]
            size = len(str(schema))
            self.assertLess(size, 7_000,
                f"Schema '{name}' is {size} chars, exceeds per-schema budget")

    def test_inspect_graph_schema_not_polluted_with_change_graph_text(self) -> None:
        """Schema descriptions must only describe their own tool."""
        schemas = build_tool_schemas(list(MVP_MODEL_TOOL_NAMES))
        inspect_schema = next(
            s for s in schemas if s["function"]["name"] == "inspect_graph"
        )
        # inspect_graph description should NOT contain change_graph jargon
        desc = inspect_schema["function"]["description"]
        self.assertNotIn("add_blocks", desc)
        self.assertNotIn("remove_blocks", desc)
        self.assertNotIn("update_params", desc)


if __name__ == "__main__":
    unittest.main()
