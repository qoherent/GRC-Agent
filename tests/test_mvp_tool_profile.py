"""MVP model-facing tool profile wrapper tests."""

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from grc_agent.agent import GrcAgent
from grc_agent.config import load_app_config
from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.runtime.tool_schemas import MVP_MODEL_TOOL_NAMES
from grc_agent.runtime.tool_surface import MVP_TOOL_SURFACE, PUBLIC_TOOL_NAMES


class MvpToolProfileTests(unittest.TestCase):
    def _fixture_path(self) -> Path:
        return Path(__file__).resolve().parent / "data" / "random_bit_generator.grc"

    def _load_agent(self) -> GrcAgent:
        session = FlowgraphSession()
        session.load(self._fixture_path())
        return GrcAgent(session)

    def _load_legacy_agent(self) -> GrcAgent:
        session = FlowgraphSession()
        session.load(self._fixture_path())
        config = load_app_config()
        legacy_config = replace(
            config,
            agent=replace(config.agent, legacy_model_tool_surface=True),
        )
        return GrcAgent(session, config=legacy_config.agent)

    def test_mvp_tool_schemas_exist(self) -> None:
        agent = self._load_agent()
        names = [schema["function"]["name"] for schema in agent.get_tool_schemas()]
        for name in MVP_MODEL_TOOL_NAMES:
            self.assertIn(name, names)
        self.assertEqual(names, list(MVP_MODEL_TOOL_NAMES))
        for legacy_name in PUBLIC_TOOL_NAMES:
            self.assertNotIn(legacy_name, names)

    def test_mvp_turn_schema_narrowing_exposes_only_wrappers(self) -> None:
        agent = self._load_agent()
        narrowed = agent.get_tool_schemas_for_turn(set(MVP_MODEL_TOOL_NAMES))
        names = [schema["function"]["name"] for schema in narrowed]
        self.assertEqual(names, list(MVP_MODEL_TOOL_NAMES))

    def test_mvp_tool_surface_is_single_profile_authority(self) -> None:
        self.assertEqual(MVP_TOOL_SURFACE.model_tool_names, MVP_MODEL_TOOL_NAMES)
        self.assertFalse(MVP_TOOL_SURFACE.assistant_text_fallback_enabled)
        self.assertEqual(MVP_TOOL_SURFACE.default_max_tool_rounds, 8)

    def test_mvp_prompt_mentions_only_wrapper_tools(self) -> None:
        prompt = self._load_agent().get_system_prompt()

        for name in MVP_MODEL_TOOL_NAMES:
            self.assertIn(name, prompt)
        for legacy_name in (
            "apply_edit",
            "propose_edit",
            "save_graph",
            "semantic_search_grc",
            "search_manual",
            "validate_graph",
        ):
            self.assertNotIn(legacy_name, prompt)

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

    def test_ask_grc_docs_returns_answer_and_sources(self) -> None:
        agent = self._load_agent()
        result = agent.execute_tool("ask_grc_docs", {"question": "What are stream tags?", "k": 2})
        self.assertTrue(result["ok"], result)
        self.assertIn("answer", result)
        self.assertIn("sources", result)
        self.assertIn("insufficient_evidence", result)
        self.assertIn("fallback_used", result)
        for row in result["sources"]:
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
        docs_result = agent.execute_tool(
            "ask_grc_docs",
            {"question": "What are stream tags?", "k": 3},
        )
        self.assertTrue(inspect_result["ok"], inspect_result)
        self.assertTrue(search_blocks_result["ok"], search_blocks_result)
        self.assertTrue(docs_result["ok"], docs_result)
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
                "operation_kind": "set_param",
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
                "operation_kind": "set_param",
                "instance_name": "samp_rate",
                "param_key": "value",
                "param_value": "48000",
            },
        )
        self.assertTrue(result["ok"], result)
        self.assertFalse(result["dry_run"])
        self.assertEqual(result["operation_summary"], "update_params")
        self.assertEqual(result["operation_kind"], "set_param")
        self.assertTrue(agent.session.is_dirty)

    def test_change_graph_operation_kind_mismatch_is_rejected(self) -> None:
        agent = self._load_agent()
        result = agent.execute_tool(
            "change_graph",
            {
                "dry_run": False,
                "user_goal": "Set samp_rate to 48000.",
                "operation_kind": "disconnect",
                "instance_name": "samp_rate",
                "param_key": "value",
                "param_value": "48000",
            },
        )

        self.assertFalse(result["ok"], result)
        self.assertEqual(result["error_type"], "invalid_request")

    def test_change_graph_committed_mutation_requires_operation_kind(self) -> None:
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

        self.assertFalse(result["ok"], result)
        self.assertEqual(result["error_type"], "clarification_required")
        self.assertIn("operation_kind", result.get("message", ""))

    def test_change_graph_rejects_unsupported_operation_kind(self) -> None:
        agent = self._load_agent()
        result = agent.execute_tool(
            "change_graph",
            {
                "dry_run": False,
                "user_goal": "Set samp_rate to 48000.",
                "operation_kind": "set_frequency",
                "instance_name": "samp_rate",
                "param_key": "value",
                "param_value": "48000",
            },
        )

        self.assertFalse(result["ok"], result)
        self.assertEqual(result["error_type"], "tool_call_invalid")

    def test_change_graph_operation_kind_clarify_is_non_mutating(self) -> None:
        agent = self._load_agent()
        before_revision = agent.session.state_revision
        result = agent.execute_tool(
            "change_graph",
            {
                "dry_run": False,
                "user_goal": "Fix this graph.",
                "operation_kind": "clarify",
            },
        )

        self.assertFalse(result["ok"], result)
        self.assertEqual(result["error_type"], "clarification_required")
        self.assertEqual(agent.session.state_revision, before_revision)

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
                "operation_kind": "disconnect",
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
                "operation_kind": "set_param",
                "instance_name": "samp_rate",
                "param_key": "value",
                "param_value": "48000",
            },
        )
        self.assertTrue(result["ok"], result)
        self.assertFalse(result["dry_run"])
        self.assertEqual(result["operation_summary"], "update_params")
        self.assertTrue(result.get("checkpoint_id"))

    def test_mvp_model_driven_legacy_tool_calls_are_rejected(self) -> None:
        agent = self._load_agent()
        cases = (
            ("rewire_connection", {"old_connection_id": "a:0->b:0"}),
            ("apply_edit", {"transaction": {"op_type": "noop"}}),
            ("propose_edit", {"transaction": {"op_type": "noop"}}),
            ("remove_connection", {"connection_id": "a:0->b:0"}),
            ("save_graph", {}),
            ("validate_graph", {}),
        )
        for tool_name, kwargs in cases:
            with self.subTest(tool=tool_name):
                result = agent.execute_tool(
                    tool_name,
                    kwargs,
                    model_tool_call=True,
                )
                self.assertFalse(result["ok"], result)
                self.assertEqual(result["error_type"], "tool_not_allowed_for_surface")
                self.assertIn("TOOL_NOT_ALLOWED_FOR_SURFACE", result["message"])

    def test_legacy_model_surface_allows_legacy_tool_validation_path(self) -> None:
        agent = self._load_legacy_agent()
        result = agent.validate_tool_call(
            "validate_graph",
            {},
            model_tool_call=True,
        )
        self.assertIsNone(result)

    def test_history_restore_remains_cli_copy_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            session = FlowgraphSession()
            session.load(self._fixture_path())
            agent = GrcAgent(
                session,
                history_journal_path=Path(tmpdir) / "history.jsonl",
            )
            out = Path(tmpdir) / "copy.grc"
            result = agent.execute_tool(
                "change_graph",
                {
                    "dry_run": False,
                    "user_goal": "Set samp_rate to 48000.",
                    "operation_kind": "set_param",
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
