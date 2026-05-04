"""MVP model-facing tool profile wrapper tests."""

from pathlib import Path
import tempfile
import unittest

from grc_agent.agent import GrcAgent
from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.runtime.tool_schemas import MVP_MODEL_TOOL_NAMES


class MvpToolProfileTests(unittest.TestCase):
    def _fixture_path(self) -> Path:
        return Path(__file__).resolve().parent / "data" / "random_bit_generator.grc"

    def _load_agent(self) -> GrcAgent:
        session = FlowgraphSession()
        session.load(self._fixture_path())
        return GrcAgent(session)

    def test_mvp_tool_schemas_exist(self) -> None:
        agent = self._load_agent()
        names = [schema["function"]["name"] for schema in agent.get_tool_schemas()]
        for name in MVP_MODEL_TOOL_NAMES:
            self.assertIn(name, names)

    def test_mvp_turn_schema_narrowing_exposes_only_wrappers(self) -> None:
        agent = self._load_agent()
        narrowed = agent.get_tool_schemas_for_turn(set(MVP_MODEL_TOOL_NAMES))
        names = [schema["function"]["name"] for schema in narrowed]
        self.assertEqual(names, list(MVP_MODEL_TOOL_NAMES))

    def test_inspect_graph_summarize_returns_compact_payload(self) -> None:
        agent = self._load_agent()
        result = agent.execute_tool("inspect_graph", {"operation": "summarize"})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["tool"], "inspect_graph")
        self.assertEqual(result["operation"], "summarize")
        self.assertIn("summary", result)

    def test_search_blocks_returns_minimal_candidates(self) -> None:
        agent = self._load_agent()
        result = agent.execute_tool("search_blocks", {"query": "throttle", "k": 3})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["tool"], "search_blocks")
        for row in result["results"]:
            self.assertEqual(sorted(row.keys()), ["block_id", "name", "summary"])

    def test_search_blocks_debug_can_include_internal_ranking_metadata(self) -> None:
        agent = self._load_agent()
        result = agent.execute_tool(
            "search_blocks", {"query": "throttle", "k": 3, "debug": True}
        )
        self.assertTrue(result["ok"], result)
        for row in result["results"]:
            self.assertIn("block_id", row)
            self.assertIn("name", row)
            self.assertIn("summary", row)

    def test_search_help_returns_explanation_only_fields(self) -> None:
        agent = self._load_agent()
        result = agent.execute_tool("search_help", {"query": "stream tags", "k": 2})
        self.assertTrue(result["ok"], result)
        for row in result["results"]:
            self.assertEqual(sorted(row.keys()), ["excerpt", "source", "title"])

    def test_inspect_and_search_tools_are_read_only(self) -> None:
        agent = self._load_agent()
        before_revision = agent.session.state_revision
        before_dirty = agent.session.is_dirty
        inspect_result = agent.execute_tool("inspect_graph", {"operation": "list_blocks"})
        search_blocks_result = agent.execute_tool(
            "search_blocks",
            {"query": "throttle", "k": 5},
        )
        search_help_result = agent.execute_tool(
            "search_help",
            {"query": "stream tags", "k": 3},
        )
        self.assertTrue(inspect_result["ok"], inspect_result)
        self.assertTrue(search_blocks_result["ok"], search_blocks_result)
        self.assertTrue(search_help_result["ok"], search_help_result)
        self.assertEqual(agent.session.state_revision, before_revision)
        self.assertEqual(agent.session.is_dirty, before_dirty)

    def test_change_graph_preview_does_not_mutate(self) -> None:
        agent = self._load_agent()
        before_revision = agent.session.state_revision
        before_dirty = agent.session.is_dirty
        result = agent.execute_tool(
            "change_graph",
            {
                "dry_run": True,
                "user_goal": "Preview setting samp_rate to 48000.",
                "instance_name": "samp_rate",
                "param_key": "value",
                "param_value": "48000",
            },
        )
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["dry_run"])
        self.assertEqual(agent.session.state_revision, before_revision)
        self.assertEqual(agent.session.is_dirty, before_dirty)

    def test_change_graph_apply_mutates_and_validates(self) -> None:
        agent = self._load_agent()
        result = agent.execute_tool(
            "change_graph",
            {
                "dry_run": False,
                "user_goal": "Set samp_rate to 48000.",
                "instance_name": "samp_rate",
                "param_key": "value",
                "param_value": "48000",
            },
        )
        self.assertTrue(result["ok"], result)
        self.assertFalse(result["dry_run"])
        self.assertEqual(result["operation_summary"], "update_params")
        self.assertTrue(agent.session.is_dirty)

    def test_change_graph_unsupported_workflow_is_rejected(self) -> None:
        agent = self._load_agent()
        result = agent.execute_tool(
            "change_graph",
            {
                "dry_run": False,
                "user_goal": "Edit raw YAML source text.",
            },
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_type"], "unsupported_op")

    def test_change_graph_requires_exact_details_or_clarifies(self) -> None:
        agent = self._load_agent()
        result = agent.execute_tool(
            "change_graph",
            {"dry_run": False, "user_goal": "Fix this graph."},
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_type"], "clarification_required")
        self.assertIn("clarification_options", result)

    def test_change_graph_save_is_not_model_facing(self) -> None:
        agent = self._load_agent()
        result = agent.execute_tool(
            "change_graph",
            {"dry_run": False, "user_goal": "Save a copy to /tmp/example.grc"},
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_type"], "unsupported_op")

    def test_change_graph_rejects_raw_transaction_payload(self) -> None:
        agent = self._load_agent()
        result = agent.execute_tool(
            "change_graph",
            {
                "dry_run": False,
                "user_goal": "Set samp_rate to 48000.",
                "transaction": {
                    "op_type": "update_params",
                    "instance_name": "samp_rate",
                    "params": {"value": "48000"},
                },
            },
        )
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["error_type"], "tool_call_invalid")

    def test_change_graph_disconnect_by_exact_connection_id(self) -> None:
        agent = self._load_agent()
        listed = agent.execute_tool("inspect_graph", {"operation": "list_connections"})
        self.assertTrue(listed["ok"])
        connection_ids = listed.get("items") or []
        self.assertTrue(connection_ids)
        result = agent.execute_tool(
            "change_graph",
            {
                "dry_run": True,
                "user_goal": "Preview disconnect exact connection.",
                "connection_id": connection_ids[0],
            },
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["operation_summary"], "remove_connection")

    def test_change_graph_committed_edit_returns_checkpoint_id(self) -> None:
        agent = self._load_agent()
        result = agent.execute_tool(
            "change_graph",
            {
                "dry_run": False,
                "user_goal": "Set samp_rate to 48000.",
                "instance_name": "samp_rate",
                "param_key": "value",
                "param_value": "48000",
            },
        )
        self.assertTrue(result["ok"], result)
        self.assertFalse(result["dry_run"])
        self.assertEqual(result["operation_summary"], "update_params")
        self.assertTrue(result.get("checkpoint_id"))

    def test_history_restore_remains_cli_copy_only(self) -> None:
        agent = self._load_agent()
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "copy.grc"
            result = agent.execute_tool(
                "change_graph",
                {
                    "dry_run": False,
                    "user_goal": "Set samp_rate to 48000.",
                    "instance_name": "samp_rate",
                    "param_key": "value",
                    "param_value": "48000",
                },
            )
            self.assertTrue(result["ok"], result)
            records = agent._history_journal.list_records(accepted_only=True)
            self.assertGreaterEqual(len(records), 1)
            restored = agent._history_journal.restore_record(records[-1]["id"], out)
            self.assertTrue(restored["ok"])
            self.assertTrue(out.exists())


if __name__ == "__main__":
    unittest.main()
