"""Runtime-contract tests for the model-facing `GrcAgent` surface."""

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import tempfile
import unittest

from grc_agent.agent import GrcAgent, PUBLIC_TOOL_NAMES
from grc_agent.cli import _run_fake_runtime
from grc_agent.flowgraph_session import FlowgraphSession


class GrcAgentTests(unittest.TestCase):
    """Tests for the final routed model-facing runtime contract."""

    def _fixture_path(self) -> Path:
        test_directory = Path(__file__).resolve().parent
        return test_directory / "data" / "random_bit_generator.grc"

    def _load_agent(self) -> tuple[GrcAgent, FlowgraphSession]:
        session = FlowgraphSession()
        session.load(self._fixture_path())
        return GrcAgent(session), session

    def _write_alt_fixture(self, directory: Path) -> Path:
        alt_path = directory / "random_bit_generator_alt.grc"
        alt_path.write_text(
            self._fixture_path()
            .read_text(encoding="utf-8")
            .replace("samp_rate", "fresh_clock_value"),
            encoding="utf-8",
        )
        return alt_path

    def test_runtime_tool_surface_matches_phase_six_contract(self) -> None:
        agent, _session = self._load_agent()

        self.assertEqual(tuple(agent._tools), PUBLIC_TOOL_NAMES)
        self.assertNotIn("set_variable", agent._tools)
        self.assertNotIn("set_param", agent._tools)

    def test_tool_schemas_match_phase_six_surface(self) -> None:
        agent, _session = self._load_agent()

        schemas = agent.get_tool_schemas()
        schema_by_name = {schema["function"]["name"]: schema for schema in schemas}

        self.assertEqual(
            [schema["function"]["name"] for schema in schemas],
            list(PUBLIC_TOOL_NAMES),
        )
        self.assertEqual(
            schema_by_name["load_grc"]["function"]["parameters"]["required"],
            ["file_path"],
        )
        self.assertEqual(
            schema_by_name["propose_edit"]["function"]["parameters"]["required"],
            ["transaction"],
        )
        self.assertEqual(
            schema_by_name["apply_edit"]["function"]["parameters"]["required"],
            ["transaction"],
        )

    def test_system_prompt_mentions_read_and_edit_routes(self) -> None:
        agent, _session = self._load_agent()

        prompt = agent.get_system_prompt()

        self.assertIn(
            "The active session context tells you which `.grc` file is loaded",
            prompt,
        )
        self.assertIn("After `search_grc`, block results include `block_id`", prompt)
        self.assertIn("ONLY use `propose_edit` when the user explicitly says", prompt)
        self.assertIn(
            "Only call `save_graph` after successful validation",
            prompt,
        )
        self.assertIn("copy the tool summary verbatim as your final answer", prompt)

    def test_summarize_tool_message_to_model_is_plain_summary_text(self) -> None:
        agent, _session = self._load_agent()

        summary_result = agent.execute_tool("summarize_graph", {})
        agent.history.append(
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "name": "summarize_graph",
                "content": summary_result,
            }
        )

        tool_message = agent.get_model_messages()[-1]

        self.assertEqual(tool_message["role"], "tool")
        self.assertEqual(tool_message["name"], "summarize_graph")
        self.assertEqual(tool_message["content"], summary_result["summary"])

    def test_model_messages_include_active_session_context(self) -> None:
        agent, _session = self._load_agent()

        messages = agent.get_model_messages()

        session_messages = [
            message
            for message in messages
            if message.get("role") == "system"
            and isinstance(message.get("content"), str)
            and message["content"].startswith("Active session:")
        ]
        self.assertEqual(len(session_messages), 1)
        self.assertIn(str(self._fixture_path()), session_messages[0]["content"])
        self.assertIn(
            "Use exact session instance names", session_messages[0]["content"]
        )
        self.assertIn("blocks_throttle2_0", session_messages[0]["content"])

    def test_execute_tool_unknown_name_returns_structured_error(self) -> None:
        agent, _session = self._load_agent()

        result = agent.execute_tool("set_variable", {})

        self.assertFalse(result["ok"])
        self.assertEqual(result["tool"], "set_variable")
        self.assertEqual(result["error_type"], "UnknownTool")

    def test_load_grc_tool_replaces_empty_session(self) -> None:
        agent = GrcAgent()

        result = agent.execute_tool(
            "load_grc", {"file_path": str(self._fixture_path())}
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["tool"], "load_grc")
        self.assertEqual(result["message"], "Graph loaded.")
        self.assertEqual(result["path"], str(self._fixture_path()))
        self.assertEqual(result["provenance"]["path"], str(self._fixture_path()))
        self.assertEqual(result["active_session"]["path"], str(self._fixture_path()))
        self.assertIsNotNone(agent.session.flowgraph)
        self.assertEqual(agent.history[-1]["role"], "session")
        self.assertEqual(
            agent.history[-1]["content"]["path"], str(self._fixture_path())
        )

    def test_search_grc_routes_session_search(self) -> None:
        agent, _session = self._load_agent()

        result = agent.execute_tool(
            "search_grc",
            {"query": "samp_rate", "scope": "session", "k": 3},
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["tool"], "search_grc")
        self.assertEqual(result["scope"], "session")
        self.assertGreaterEqual(len(result["results"]), 1)
        self.assertIn("block_id", result["results"][0])
        self.assertIn("block_id", result["hint"])
        self.assertEqual(result["active_session"]["path"], str(self._fixture_path()))

    def test_execute_tool_rejects_schema_mismatches_before_execution(self) -> None:
        agent, _session = self._load_agent()

        result = agent.execute_tool(
            "search_grc",
            {"query": "samp_rate", "scope": "session", "unexpected": True},
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_type"], "InvalidToolCall")
        self.assertEqual(result["validation_errors"][0]["code"], "unexpected_argument")
        self.assertEqual(result["validation_errors"][0]["field"], "unexpected")

    def test_load_grc_rebinds_active_session_context(self) -> None:
        agent, _session = self._load_agent()

        with tempfile.TemporaryDirectory() as tmpdir:
            alt_path = self._write_alt_fixture(Path(tmpdir))

            load_result = agent.execute_tool("load_grc", {"file_path": str(alt_path)})
            alt_search = agent.execute_tool(
                "search_grc",
                {"query": "fresh_clock_value", "scope": "session", "k": 5},
            )
            stale_search = agent.execute_tool(
                "search_grc",
                {"query": "samp_rate", "scope": "session", "k": 5},
            )

        self.assertTrue(load_result["ok"])
        self.assertEqual(load_result["active_session"]["path"], str(alt_path))
        self.assertTrue(alt_search["ok"])
        self.assertTrue(alt_search["results"])
        self.assertEqual(
            alt_search["results"][0]["node_id"], "session:block:fresh_clock_value"
        )
        self.assertEqual(alt_search["active_session"]["path"], str(alt_path))
        self.assertFalse(stale_search["results"])
        self.assertIn(
            "No session matches found for 'samp_rate'.", stale_search["warnings"]
        )
        session_entries = [
            turn for turn in agent.history if turn.get("role") == "session"
        ]
        self.assertGreaterEqual(len(session_entries), 2)
        self.assertEqual(session_entries[-1]["reason"], "load_grc")
        self.assertEqual(session_entries[-1]["content"]["path"], str(alt_path))

    def test_fake_runtime_rejects_invalid_tool_calls_before_execution(self) -> None:
        agent, _session = self._load_agent()

        with redirect_stdout(StringIO()):
            agent.run_step_fake(
                "Search the current graph.",
                [
                    {
                        "tool": "search_grc",
                        "kwargs": {
                            "query": "samp_rate",
                            "scope": "session",
                            "unexpected": True,
                        },
                    }
                ],
            )

        tool_entries = [turn for turn in agent.history if turn.get("role") == "tool"]
        self.assertEqual(len(tool_entries), 1)
        self.assertFalse(tool_entries[0]["content"]["ok"])
        self.assertEqual(tool_entries[0]["content"]["error_type"], "InvalidToolCall")
        self.assertEqual(
            tool_entries[0]["content"]["validation_errors"][0]["code"],
            "unexpected_argument",
        )

    def test_get_grc_context_routes_context_payload(self) -> None:
        agent, _session = self._load_agent()

        result = agent.execute_tool(
            "get_grc_context",
            {"node_id": "blocks_throttle2_0", "hops": 1, "max_nodes": 20},
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["tool"], "get_grc_context")
        self.assertEqual(result["node_id"], "blocks_throttle2_0")
        self.assertGreaterEqual(len(result["nodes"]), 1)

    def test_describe_block_routes_catalog_lookup(self) -> None:
        agent, _session = self._load_agent()

        result = agent.execute_tool("describe_block", {"block_id": "analog_agc_xx"})

        self.assertTrue(result["ok"])
        self.assertEqual(result["tool"], "describe_block")
        self.assertEqual(result["block_id"], "analog_agc_xx")
        self.assertIn("parameters", result)

    def test_describe_block_normalizes_catalog_prefixed_id(self) -> None:
        agent, _session = self._load_agent()

        result = agent.execute_tool(
            "describe_block", {"block_id": "catalog:block:analog_agc_xx"}
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["block_id"], "analog_agc_xx")
        self.assertEqual(result["requested_block_id"], "catalog:block:analog_agc_xx")
        self.assertEqual(result["resolved_block_id"], "analog_agc_xx")

    def test_describe_block_normalizes_session_block_instance_name(self) -> None:
        agent, _session = self._load_agent()

        result = agent.execute_tool(
            "describe_block", {"block_id": "session:block:blocks_throttle2_0"}
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["block_id"], "blocks_throttle2")
        self.assertEqual(
            result["requested_block_id"], "session:block:blocks_throttle2_0"
        )
        self.assertEqual(result["resolved_block_id"], "blocks_throttle2")

    def test_propose_edit_routes_preflight_validation(self) -> None:
        agent, _session = self._load_agent()

        result = agent.execute_tool(
            "propose_edit",
            {
                "transaction": {
                    "op_type": "update_params",
                    "instance_name": "samp_rate",
                    "params": {"value": "48000"},
                }
            },
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["tool"], "propose_edit")
        self.assertFalse(result["commit_eligible"])
        self.assertEqual(
            result["normalized_operations"][0]["instance_name"], "samp_rate"
        )

    def test_apply_edit_validates_and_allows_save_without_revalidation(self) -> None:
        agent, session = self._load_agent()

        result = agent.execute_tool(
            "apply_edit",
            {
                "transaction": {
                    "op_type": "update_params",
                    "instance_name": "samp_rate",
                    "params": {"value": "48000"},
                }
            },
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["tool"], "apply_edit")
        self.assertTrue(result["applied"])
        self.assertTrue(session.is_dirty)
        self.assertEqual(result["validation"]["status"], "valid")

        with tempfile.TemporaryDirectory() as tmpdir:
            save_path = Path(tmpdir) / "validated_apply_save.grc"
            save_result = agent.execute_tool("save_graph", {"path": str(save_path)})

            self.assertTrue(save_result["ok"])
            self.assertTrue(save_path.exists())
            self.assertFalse(session.is_dirty)

    def test_save_graph_requires_validation_after_external_dirty_change(self) -> None:
        agent, session = self._load_agent()
        session.set_param("samp_rate", "value", "48000")

        with tempfile.TemporaryDirectory() as tmpdir:
            save_path = Path(tmpdir) / "blocked_save.grc"
            result = agent.execute_tool("save_graph", {"path": str(save_path)})

            self.assertFalse(result["ok"])
            self.assertTrue(result["requires_validation"])
            self.assertTrue(session.is_dirty)
            self.assertFalse(save_path.exists())

    def test_validate_then_save_graph_succeeds_after_external_dirty_change(
        self,
    ) -> None:
        agent, session = self._load_agent()
        session.set_param("samp_rate", "value", "48000")

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

    def test_validate_graph_tool_routes_correctly(self) -> None:
        agent, session = self._load_agent()

        result = agent.execute_tool("validate_graph", {})

        self.assertTrue(result["ok"])
        self.assertEqual(result["tool"], "validate_graph")
        self.assertTrue(result["valid"])
        self.assertEqual(result["returncode"], 0)
        self.assertIn("active_session", result)

    def test_load_grc_tool_missing_file_returns_error(self) -> None:
        agent = GrcAgent()

        result = agent.execute_tool("load_grc", {"file_path": "/nonexistent.grc"})

        self.assertFalse(result["ok"])
        self.assertEqual(result["tool"], "load_grc")
        self.assertIn("error_type", result)

    def test_describe_block_unknown_returns_error(self) -> None:
        agent, _session = self._load_agent()

        result = agent.execute_tool(
            "describe_block", {"block_id": "totally_fake_block_xyz"}
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["tool"], "describe_block")

    def test_get_grc_context_unknown_node_returns_candidates(self) -> None:
        agent, _session = self._load_agent()

        result = agent.execute_tool("get_grc_context", {"node_id": "throttle"})

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_type"], "node_not_found")
        self.assertIn("candidate_nodes", result)
        self.assertIn("blocks_throttle2_0", result["candidate_nodes"])
        self.assertIn("exact session instance name", result["hint"])

    def test_fake_cli_runtime_uses_phase_six_tool_names(self) -> None:
        output = StringIO()

        with redirect_stdout(output):
            exit_code = _run_fake_runtime(str(self._fixture_path()))

        rendered = output.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn("Assistant called apply_edit", rendered)
        self.assertNotIn("Assistant called set_variable", rendered)


if __name__ == "__main__":
    unittest.main()
