"""MVP model-facing tool profile wrapper tests."""

from dataclasses import replace
from pathlib import Path
import shutil
import tempfile
import unittest

from grc_agent.agent import GrcAgent
from grc_agent.config import load_app_config
from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.runtime.tool_schemas import MVP_MODEL_TOOL_NAMES
from grc_agent.runtime.tool_surface import MVP_TOOL_SURFACE, PUBLIC_TOOL_NAMES
import yaml


class MvpToolProfileTests(unittest.TestCase):
    def _fixture_path(self) -> Path:
        return Path(__file__).resolve().parent / "data" / "random_bit_generator.grc"

    def _dual_sink_fixture_path(self) -> Path:
        return (
            Path(__file__).resolve().parent / "data" / "random_bit_generator_dual_sink.grc"
        )

    def _load_agent(self) -> GrcAgent:
        session = FlowgraphSession()
        session.load(self._fixture_path())
        return GrcAgent(session)

    def _load_dual_sink_agent(self) -> GrcAgent:
        session = FlowgraphSession()
        session.load(self._dual_sink_fixture_path())
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
            "`save_graph`",
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

    def test_change_graph_set_state_commit_valid_enable_disable(self) -> None:
        agent = self._load_dual_sink_agent()

        agent.init_turn_requirements("Disable qtgui_time_sink_x_1.")
        disable = agent.execute_tool(
            "change_graph",
            {
                "dry_run": False,
                "user_goal": "Disable qtgui_time_sink_x_1.",
                "operation_kind": "set_state",
                "instance_name": "qtgui_time_sink_x_1",
                "state": "disabled",
            },
        )
        self.assertTrue(disable["ok"], disable)
        self.assertEqual(disable["validation_result"]["status"], "valid")
        self.assertEqual(disable["operation_summary"], "update_states")

        agent.init_turn_requirements("Enable qtgui_time_sink_x_1.")
        enable = agent.execute_tool(
            "change_graph",
            {
                "dry_run": False,
                "user_goal": "Enable qtgui_time_sink_x_1.",
                "operation_kind": "set_state",
                "instance_name": "qtgui_time_sink_x_1",
                "state": "enabled",
            },
        )
        self.assertTrue(enable["ok"], enable)
        self.assertEqual(enable["validation_result"]["status"], "valid")
        self.assertEqual(enable["operation_summary"], "update_states")

    def test_change_graph_set_state_preview_does_not_mutate(self) -> None:
        agent = self._load_dual_sink_agent()
        before_revision = agent.session.state_revision
        before_dirty = agent.session.is_dirty

        agent.init_turn_requirements("Preview disabling qtgui_time_sink_x_1.")
        preview = agent.execute_tool(
            "change_graph",
            {
                "dry_run": True,
                "user_goal": "Preview disabling qtgui_time_sink_x_1.",
                "operation_kind": "set_state",
                "instance_name": "qtgui_time_sink_x_1",
                "state": "disabled",
            },
        )
        self.assertTrue(preview["ok"], preview)
        self.assertEqual(preview["operation_summary"], "update_states")
        self.assertEqual(agent.session.state_revision, before_revision)
        self.assertEqual(agent.session.is_dirty, before_dirty)

    def test_change_graph_set_state_invalid_change_is_refused(self) -> None:
        agent = self._load_dual_sink_agent()
        before_revision = agent.session.state_revision
        before_dirty = agent.session.is_dirty

        agent.init_turn_requirements("Disable blocks_throttle2_0.")
        result = agent.execute_tool(
            "change_graph",
            {
                "dry_run": False,
                "user_goal": "Disable blocks_throttle2_0.",
                "operation_kind": "set_state",
                "instance_name": "blocks_throttle2_0",
                "state": "disabled",
            },
        )
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["error_type"], "gnu_validation_failed")
        self.assertEqual(result["validation_result"]["status"], "invalid")
        self.assertEqual(agent.session.state_revision, before_revision)
        self.assertEqual(agent.session.is_dirty, before_dirty)

    def test_change_graph_set_state_stale_target_ref_is_refused(self) -> None:
        agent = self._load_dual_sink_agent()
        assert agent.session.flowgraph is not None
        block = next(
            b
            for b in agent.session.flowgraph.blocks
            if b.instance_name == "qtgui_time_sink_x_1"
        )
        stale_target_ref = {
            "block_uid": block.block_uid,
            "expected_instance_name": block.instance_name,
            "expected_block_type": block.block_type,
            "base_state_revision": agent.session.state_revision,
        }

        # Mutate once so the guarded target_ref revision becomes stale.
        agent.init_turn_requirements("Set samp_rate to 48000.")
        applied = agent.execute_tool(
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
        self.assertTrue(applied["ok"], applied)

        before_revision = agent.session.state_revision
        before_dirty = agent.session.is_dirty
        agent.init_turn_requirements("Disable qtgui_time_sink_x_1.")
        stale = agent.execute_tool(
            "change_graph",
            {
                "dry_run": False,
                "user_goal": "Disable qtgui_time_sink_x_1.",
                "operation_kind": "set_state",
                "state": "disabled",
                "target_ref": stale_target_ref,
            },
        )
        self.assertFalse(stale["ok"], stale)
        self.assertEqual(stale["error_type"], "preflight_rejected")
        self.assertIn("preflight", stale.get("message", "").lower())
        self.assertEqual(agent.session.state_revision, before_revision)
        self.assertEqual(agent.session.is_dirty, before_dirty)

    def test_change_graph_set_state_duplicate_target_requires_clarification(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            fixture = self._dual_sink_fixture_path()
            duplicate_fixture = Path(tmpdir) / "duplicate_sink_name.grc"
            raw = yaml.safe_load(fixture.read_text(encoding="utf-8"))
            blocks = raw.get("blocks")
            self.assertIsInstance(blocks, list)
            assert isinstance(blocks, list)
            sink0 = next(block for block in blocks if block.get("name") == "qtgui_time_sink_x_0")
            sink1 = next(block for block in blocks if block.get("name") == "qtgui_time_sink_x_1")
            sink1["name"] = sink0["name"]
            duplicate_fixture.write_text(
                yaml.safe_dump(raw, sort_keys=False),
                encoding="utf-8",
            )

            session = FlowgraphSession()
            session.load(duplicate_fixture)
            agent = GrcAgent(session)
            before_revision = agent.session.state_revision
            before_dirty = agent.session.is_dirty

            agent.init_turn_requirements("Disable qtgui_time_sink_x_0.")
            result = agent.execute_tool(
                "change_graph",
                {
                    "dry_run": False,
                    "user_goal": "Disable qtgui_time_sink_x_0.",
                    "operation_kind": "set_state",
                    "instance_name": "qtgui_time_sink_x_0",
                    "state": "disabled",
                },
            )
            self.assertFalse(result["ok"], result)
            self.assertEqual(result["error_type"], "ambiguous_block")
            self.assertTrue(result.get("clarification_options"), result)
            self.assertEqual(agent.session.state_revision, before_revision)
            self.assertEqual(agent.session.is_dirty, before_dirty)

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

    def test_save_graph_explicit_requires_explicit_intent(self) -> None:
        agent = self._load_agent()
        result = agent.execute_tool("save_graph_explicit", {})
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["error_type"], "invalid_request")

    def test_save_graph_explicit_allows_save_after_explicit_intent(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            agent = self._load_agent()
            target = Path(tmpdir) / "saved_copy.grc"
            agent.init_turn_requirements(f"Save a copy to {target}.")
            result = agent.execute_tool(
                "save_graph_explicit",
                {"path": str(target)},
            )
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["path"], str(target))
            self.assertTrue(target.exists())
            self.assertTrue(result.get("checkpoint_id"))

    def test_save_graph_explicit_refuses_overwrite_without_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            agent = self._load_agent()
            target = Path(tmpdir) / "existing.grc"
            shutil.copy2(self._fixture_path(), target)
            agent.init_turn_requirements(f"Save a copy to {target}.")
            result = agent.execute_tool("save_graph_explicit", {"path": str(target)})
            self.assertFalse(result["ok"], result)
            self.assertEqual(result["error_type"], "save_refused")

    def test_save_graph_explicit_refuses_invalid_graph(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            agent = self._load_agent()
            flowgraph = agent.session.flowgraph
            assert flowgraph is not None
            # Force an invalid dirty state outside verified mutation tools so save wrapper
            # must refuse based on pre-save validation.
            flowgraph.connections = flowgraph.connections[:-1]
            raw_connections = flowgraph.raw_data.get("connections")
            self.assertIsInstance(raw_connections, list)
            assert isinstance(raw_connections, list)
            raw_connections[:] = raw_connections[:-1]
            agent.session.is_dirty = True
            agent.session._bump_state_revision()
            target = Path(tmpdir) / "invalid_copy.grc"
            agent.init_turn_requirements(f"Save this graph to {target}.")
            result = agent.execute_tool("save_graph_explicit", {"path": str(target)})
            self.assertFalse(result["ok"], result)
            self.assertEqual(result["error_type"], "save_refused")
            self.assertFalse(target.exists())

    def test_load_graph_explicit_requires_path_and_intent(self) -> None:
        agent = self._load_agent()
        result = agent.execute_tool("load_graph_explicit", {"path": ""})
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["error_type"], "invalid_request")

    def test_load_graph_explicit_refuses_unsafe_canonical_path(self) -> None:
        agent = self._load_agent()
        fixture = self._fixture_path()
        agent.init_turn_requirements(f"Load {fixture}.")
        result = agent.execute_tool("load_graph_explicit", {"path": str(fixture)})
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["error_type"], "file_load_error")

    def test_load_graph_explicit_allows_safe_copied_graph(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source = self._fixture_path()
            copied = Path(tmpdir) / "copied.grc"
            shutil.copy2(source, copied)
            agent = self._load_agent()
            agent.init_turn_requirements(f"Load {copied}.")
            result = agent.execute_tool("load_graph_explicit", {"path": str(copied)})
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["path"], str(copied))
            self.assertTrue(result["valid"])

    def test_load_graph_explicit_keeps_invalid_loaded_session_active(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            raw = yaml.safe_load(self._fixture_path().read_text(encoding="utf-8"))
            connections = raw.get("connections")
            self.assertIsInstance(connections, list)
            assert isinstance(connections, list)
            self.assertGreaterEqual(len(connections), 1)
            # Make one endpoint invalid so post-load validation fails deterministically.
            connections[0][0] = "missing_source_block"
            broken = Path(tmpdir) / "broken.grc"
            broken.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

            agent = self._load_agent()
            agent.init_turn_requirements(f"Load {broken}.")
            result = agent.execute_tool("load_graph_explicit", {"path": str(broken)})
            self.assertFalse(result["ok"], result)
            self.assertFalse(result["valid"])
            self.assertEqual(result["path"], str(broken))
            self.assertEqual(str(agent.session.path), str(broken))


if __name__ == "__main__":
    unittest.main()
