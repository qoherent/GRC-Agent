"""Passive contract audit tests for model-facing wrapper payload shapes."""

from __future__ import annotations

from pathlib import Path
import unittest

from grc_agent._payload import audit_change_graph_result_shape
from grc_agent.agent import GrcAgent
from grc_agent.flowgraph_session import FlowgraphSession


class RuntimeToolResultContractTests(unittest.TestCase):
    def _load_agent(self) -> GrcAgent:
        session = FlowgraphSession()
        fixture = Path(__file__).resolve().parent / "data" / "random_bit_generator.grc"
        session.load(fixture)
        return GrcAgent(session)

    def test_preview_payload_has_expected_shape_and_no_mutation_delta(self) -> None:
        agent = self._load_agent()
        result = agent.execute_tool(
            "change_graph",
            {
                "dry_run": True,
                "operation_kind": "set_param",
                "user_goal": "preview samp_rate update",
                "instance_name": "samp_rate",
                "param_key": "value",
                "param_value": "48000",
            },
        )
        findings = audit_change_graph_result_shape(result)
        self.assertEqual(findings, [], {"result": result, "findings": findings})
        self.assertFalse(result["dry_run"] is False and result.get("committed") is True)

    def test_refused_payload_does_not_report_success_like_delta(self) -> None:
        agent = self._load_agent()
        result = agent.execute_tool(
            "change_graph",
            {
                "dry_run": False,
                "operation_kind": "disconnect",
                "user_goal": "disconnect a missing edge",
                "connection_id": "missing_src:0->missing_dst:0",
            },
        )
        findings = audit_change_graph_result_shape(result)
        self.assertNotIn(
            "failed_or_refused_contains_success_like_graph_delta",
            findings,
            {"result": result, "findings": findings},
        )

    def test_commit_success_has_graph_delta(self) -> None:
        agent = self._load_agent()
        result = agent.execute_tool(
            "change_graph",
            {
                "dry_run": False,
                "operation_kind": "set_param",
                "user_goal": "set samp_rate to 32000",
                "instance_name": "samp_rate",
                "param_key": "value",
                "param_value": "32000",
            },
        )
        findings = audit_change_graph_result_shape(result)
        self.assertNotIn("commit_success_missing_graph_delta", findings, result)
        self.assertTrue(result.get("ok"), result)

    def test_clarification_payload_is_explicit(self) -> None:
        agent = self._load_agent()
        result = agent.execute_tool(
            "change_graph",
            {
                "dry_run": True,
                "operation_kind": "clarify",
                "user_goal": "need clarification",
            },
        )
        findings = audit_change_graph_result_shape(result)
        self.assertEqual(findings, [], {"result": result, "findings": findings})
        self.assertFalse(result.get("ok"), result)
        options = result.get("clarification_options")
        self.assertIsInstance(options, list, result)
        self.assertTrue(options, result)


if __name__ == "__main__":
    unittest.main()
