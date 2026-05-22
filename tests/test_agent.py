"""Essential runtime-contract tests for `GrcAgent`."""

from __future__ import annotations

from pathlib import Path
import unittest

import yaml

from grc_agent.agent import GrcAgent
from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.runtime.tool_schemas import MVP_MODEL_TOOL_NAMES, PUBLIC_TOOL_NAMES


class GrcAgentTests(unittest.TestCase):
    _raw_cache: dict[Path, dict[str, object]] = {}

    def _fixture_path(self) -> Path:
        return Path(__file__).resolve().parent / "data" / "random_bit_generator.grc"

    def _load_agent(self) -> tuple[GrcAgent, FlowgraphSession]:
        path = self._fixture_path().resolve()
        raw_data = self._raw_cache.get(path)
        if raw_data is None:
            loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
            self.assertIsInstance(loaded, dict)
            raw_data = loaded
            self._raw_cache[path] = raw_data
        session = FlowgraphSession.from_raw_data(raw_data, path=path)
        return GrcAgent(session), session

    def test_runtime_internal_tool_registry_keeps_expected_primitives(self) -> None:
        agent, _session = self._load_agent()

        self.assertEqual(tuple(agent._tools), PUBLIC_TOOL_NAMES)
        self.assertNotIn("set_variable", agent._tools)
        self.assertNotIn("set_param", agent._tools)

    def test_tool_schemas_match_mvp_surface(self) -> None:
        agent, _session = self._load_agent()

        names = [schema["function"]["name"] for schema in agent.get_tool_schemas()]

        self.assertEqual(names, list(MVP_MODEL_TOOL_NAMES))
        self.assertEqual(
            names,
            [
                "inspect_graph",
                "search_blocks",
                "ask_grc_docs",
                "change_graph",
                "save_graph_explicit",
                "load_graph_explicit",
            ],
        )

    def test_inspect_graph_schema_is_small_view_targets_params_contract(self) -> None:
        agent, _session = self._load_agent()
        schema = next(
            item
            for item in agent.get_tool_schemas()
            if item["function"]["name"] == "inspect_graph"
        )

        params = schema["function"]["parameters"]

        self.assertEqual(params["required"], ["view", "targets", "params"])
        self.assertEqual(
            sorted(params["properties"]),
            ["params", "targets", "view"],
        )
        self.assertEqual(params["properties"]["view"]["enum"], ["overview", "details"])
        self.assertEqual(params["properties"]["targets"]["maxItems"], 5)
        self.assertEqual(params["properties"]["params"]["minItems"], 1)
        self.assertEqual(params["properties"]["params"]["maxItems"], 12)

    def test_change_graph_schema_requires_operation_kind(self) -> None:
        agent, _session = self._load_agent()
        schema = next(
            item
            for item in agent.get_tool_schemas()
            if item["function"]["name"] == "change_graph"
        )

        params = schema["function"]["parameters"]

        self.assertEqual(params["required"], ["dry_run", "user_goal", "operation_kind"])
        self.assertIn("expected_old_value", params["properties"])
        self.assertIn("set_param", params["properties"]["operation_kind"]["enum"])

    def test_model_facing_internal_tool_call_is_rejected(self) -> None:
        agent, _session = self._load_agent()

        result = agent.execute_tool(
            "apply_edit",
            {
                "transaction": {
                    "op_type": "update_params",
                    "instance_name": "samp_rate",
                    "params": {"value": "48000"},
                }
            },
            model_tool_call=True,
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_type"], "tool_not_allowed_for_surface")
        self.assertFalse(agent.session.is_dirty)

    def test_model_tool_schema_validation_fails_before_execution(self) -> None:
        agent, _session = self._load_agent()

        result = agent.execute_tool(
            "change_graph",
            {
                "user_goal": "Set samp_rate to 48000.",
                "operation_kind": "set_param",
                "instance_name": "samp_rate",
                "param_key": "value",
                "param_value": "48000",
            },
            model_tool_call=True,
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_type"], "tool_call_invalid")
        self.assertFalse(agent.session.is_dirty)

    def test_model_messages_include_compact_active_session(self) -> None:
        agent, _session = self._load_agent()

        messages = agent.get_model_messages()
        active_session = messages[1]["content"]

        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("Active session:", active_session)
        self.assertIn("blocks=5", active_session)
        self.assertIn("connections=3", active_session)
        self.assertIn("samp_rate=32000", active_session)

    def test_health_check_reports_core_ready_without_loaded_session_requirement(self) -> None:
        agent = GrcAgent()

        report = agent.health_check()

        self.assertTrue(report["agent_core_ready"])
        self.assertFalse(report["session_loaded"])


if __name__ == "__main__":
    unittest.main()
