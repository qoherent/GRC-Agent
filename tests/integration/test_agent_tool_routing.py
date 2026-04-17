"""Integration tests for routed agent tool flows."""

from pathlib import Path
import unittest

from grc_agent.agent import GrcAgent
from grc_agent.flowgraph_session import FlowgraphSession


class AgentToolRoutingIntegrationTests(unittest.TestCase):
    """Exercise the final routed runtime surface on the canonical fixture."""

    def _fixture_path(self) -> Path:
        test_directory = Path(__file__).resolve().parents[1]
        return test_directory / "data" / "random_bit_generator.grc"

    def _load_agent(self) -> tuple[GrcAgent, FlowgraphSession]:
        session = FlowgraphSession()
        session.load(self._fixture_path())
        return GrcAgent(session), session

    def test_unloaded_session_tools_fail_clearly(self) -> None:
        agent = GrcAgent()

        propose = agent.execute_tool(
            "propose_edit",
            {"transaction": {"op_type": "remove_block", "instance_name": "samp_rate"}},
        )
        apply = agent.execute_tool(
            "apply_edit",
            {"transaction": {"op_type": "remove_block", "instance_name": "samp_rate"}},
        )
        validate = agent.execute_tool("validate_graph", {})

        self.assertEqual(propose["error_type"], "MissingSession")
        self.assertEqual(apply["error_type"], "MissingSession")
        self.assertEqual(validate["error_type"], "MissingSession")

    def test_read_only_routes_surface_structured_payloads(self) -> None:
        agent, _session = self._load_agent()

        summary = agent.execute_tool("summarize_graph", {})
        search = agent.execute_tool(
            "search_grc",
            {"query": "samp_rate", "scope": "session", "k": 5},
        )
        context = agent.execute_tool(
            "get_grc_context",
            {"node_id": "blocks_throttle2_0", "hops": 1, "max_nodes": 20},
        )
        block = agent.execute_tool("describe_block", {"block_id": "analog_agc_xx"})

        self.assertTrue(summary["ok"])
        self.assertIn("summary", summary)
        self.assertTrue(search["ok"])
        self.assertEqual(search["scope"], "session")
        self.assertTrue(context["ok"])
        self.assertEqual(context["target"]["node_id"], "blocks_throttle2_0")
        self.assertTrue(block["ok"])
        self.assertEqual(block["block_id"], "analog_agc_xx")

    def test_edit_routes_keep_orchestration_thin_and_structured(self) -> None:
        agent, session = self._load_agent()

        proposal = agent.execute_tool(
            "propose_edit",
            {
                "transaction": {
                    "op_type": "update_params",
                    "instance_name": "samp_rate",
                    "params": {"value": "48000"},
                }
            },
        )
        applied = agent.execute_tool(
            "apply_edit",
            {
                "transaction": {
                    "op_type": "update_params",
                    "instance_name": "samp_rate",
                    "params": {"value": "48000"},
                }
            },
        )

        self.assertTrue(proposal["ok"])
        self.assertFalse(proposal["commit_eligible"])
        self.assertTrue(applied["ok"])
        self.assertTrue(applied["applied"])
        self.assertEqual(applied["validation"]["status"], "valid")
        self.assertIn("samp_rate", applied["affected_blocks"])
        self.assertTrue(session.is_dirty)
