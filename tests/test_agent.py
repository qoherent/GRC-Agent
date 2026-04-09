from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import tempfile
import unittest

from grc_agent.agent import GrcAgent
from grc_agent.cli import _run_fake_runtime
from grc_agent.flowgraph_session import FlowgraphSession


class GrcAgentTests(unittest.TestCase):
    """Tests for the narrowed model-facing runtime contract."""

    def _fixture_path(self) -> Path:
        test_directory = Path(__file__).resolve().parent
        return test_directory / "data" / "random_bit_generator.grc"

    def _load_agent(self) -> tuple[GrcAgent, FlowgraphSession]:
        session = FlowgraphSession()
        session.load(self._fixture_path())
        return GrcAgent(session), session

    def test_runtime_tool_surface_is_narrow(self) -> None:
        agent, _session = self._load_agent()

        self.assertEqual(
            set(agent._tools),
            {"summarize_graph", "set_variable", "validate_graph", "save_graph"},
        )
        self.assertNotIn("set_param", agent._tools)
        self.assertNotIn("connect", agent._tools)
        self.assertNotIn("remove_block", agent._tools)

    def test_execute_tool_unknown_name_returns_structured_error(self) -> None:
        agent, _session = self._load_agent()

        result = agent.execute_tool("set_param", {})

        self.assertFalse(result["ok"])
        self.assertEqual(result["tool"], "set_param")
        self.assertEqual(result["error_type"], "UnknownTool")

    def test_set_variable_updates_only_variable_blocks(self) -> None:
        agent, session = self._load_agent()

        result = agent.execute_tool(
            "set_variable",
            {"instance_name": "samp_rate", "value": "48000"},
        )

        self.assertTrue(result["ok"])
        self.assertTrue(session.is_dirty)
        flowgraph = session.flowgraph
        self.assertIsNotNone(flowgraph)
        assert flowgraph is not None
        variable_block = next(
            block for block in flowgraph.blocks if block.instance_name == "samp_rate"
        )
        self.assertEqual(variable_block.params["parameters"]["value"], "48000")

    def test_set_variable_rejects_non_variable_target(self) -> None:
        agent, _session = self._load_agent()

        result = agent.execute_tool(
            "set_variable",
            {"instance_name": "blocks_throttle2_0", "value": "48000"},
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_type"], "ValueError")
        self.assertIn("Unsupported variable target", result["message"])

    def test_save_graph_requires_successful_validation_of_dirty_state(self) -> None:
        agent, _session = self._load_agent()

        agent.execute_tool(
            "set_variable",
            {"instance_name": "samp_rate", "value": "48000"},
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            save_path = Path(tmpdir) / "blocked_save.grc"
            result = agent.execute_tool("save_graph", {"path": str(save_path)})

            self.assertFalse(result["ok"])
            self.assertTrue(result["requires_validation"])
            self.assertFalse(save_path.exists())

    def test_save_graph_requires_revalidation_after_second_mutation(self) -> None:
        agent, _session = self._load_agent()

        agent.execute_tool(
            "set_variable",
            {"instance_name": "samp_rate", "value": "48000"},
        )
        first_validation = agent.execute_tool("validate_graph", {})

        self.assertTrue(first_validation["ok"])
        self.assertTrue(first_validation["valid"])

        agent.execute_tool(
            "set_variable",
            {"instance_name": "samp_rate", "value": "96000"},
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            save_path = Path(tmpdir) / "stale_validation.grc"
            result = agent.execute_tool("save_graph", {"path": str(save_path)})

            self.assertFalse(result["ok"])
            self.assertTrue(result["requires_validation"])
            self.assertFalse(save_path.exists())

    def test_validate_then_save_graph_succeeds(self) -> None:
        agent, session = self._load_agent()

        agent.execute_tool(
            "set_variable",
            {"instance_name": "samp_rate", "value": "48000"},
        )
        validation = agent.execute_tool("validate_graph", {})

        self.assertTrue(validation["ok"])
        self.assertTrue(validation["valid"])
        self.assertEqual(validation["returncode"], 0)

        with tempfile.TemporaryDirectory() as tmpdir:
            save_path = Path(tmpdir) / "validated_save.grc"
            save_result = agent.execute_tool("save_graph", {"path": str(save_path)})

            self.assertTrue(save_result["ok"])
            self.assertTrue(save_path.exists())
            self.assertFalse(session.is_dirty)

    def test_fake_cli_runtime_uses_narrow_tool_names(self) -> None:
        output = StringIO()

        with redirect_stdout(output):
            exit_code = _run_fake_runtime(str(self._fixture_path()))

        rendered = output.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn("Assistant called set_variable", rendered)
        self.assertIn("Assistant called validate_graph", rendered)
        self.assertNotIn("Assistant called set_param", rendered)


if __name__ == "__main__":
    unittest.main()
