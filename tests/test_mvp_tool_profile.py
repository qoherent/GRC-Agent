"""MVP model-facing tool profile wrapper tests."""

import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

from grc_agent.agent import GrcAgent
from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.runtime.tool_schemas import MVP_MODEL_TOOL_NAMES
from grc_agent.runtime.tool_surface import MVP_TOOL_SURFACE, PUBLIC_TOOL_NAMES
import yaml


class MvpToolProfileTests(unittest.TestCase):
    def _fixture_path(self) -> Path:
        return Path(__file__).resolve().parent / "data" / "random_bit_generator.grc"

    def _fixture_named(self, name: str) -> Path:
        return Path(__file__).resolve().parent / "data" / name

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

    def _load_agent_with_fixture(self, fixture_name: str) -> GrcAgent:
        session = FlowgraphSession()
        session.load(self._fixture_named(fixture_name))
        return GrcAgent(session)

    def _dual_sink_target_block(self, agent: GrcAgent) -> tuple[str, str, str, int]:
        assert agent.session.flowgraph is not None
        block = next(
            b
            for b in agent.session.flowgraph.blocks
            if b.instance_name == "qtgui_time_sink_x_1"
        )
        return (
            block.block_uid,
            block.instance_name,
            block.block_type,
            agent.session.state_revision,
        )

    def _block_target_ref(
        self,
        agent: GrcAgent,
        *,
        instance_name: str,
        block_type: str,
    ) -> dict[str, object]:
        assert agent.session.flowgraph is not None
        block = next(
            b
            for b in agent.session.flowgraph.blocks
            if b.instance_name == instance_name and b.block_type == block_type
        )
        return {
            "block_uid": block.block_uid,
            "expected_instance_name": block.instance_name,
            "expected_block_type": block.block_type,
            "base_state_revision": agent.session.state_revision,
        }


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

    def test_release_and_beta_profiles_keep_mvp_tool_surface(self) -> None:
        agent = self._load_agent()
        names = [schema["function"]["name"] for schema in agent.get_tool_schemas()]
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

        manifest_dir = Path(__file__).resolve().parent / "llama_eval" / "capability_manifests"
        expected = {
            "R0_READ_ONLY": "read_only_wrappers",
            "R1_SET_PARAM_ONLY": "set_param",
            "R1_SET_STATE": "set_state",
            "R2_DISCONNECT": "disconnect",
            "R3_REWIRE": "rewire",
            "R4A_INSERT": "insert_block",
            "R4B_REMOVE": "remove_block",
            "R4C_ADD_VARIABLE": "add_variable",
            "R5_SAVE_LOAD": "save_load_lifecycle",
            "R7_EXACT_EXTERNAL": "external_exact_examples",
            "R7_NATURAL_EXTERNAL": "external_natural_examples",
            "Tier5_ADVERSARIAL": "adversarial_safety",
        }
        forbidden_raw = {
            "apply_edit",
            "propose_edit",
            "remove_connection",
            "rewire_connection",
            "insert_block_on_connection",
            "auto_insert_block",
            "save_graph",
            "load_grc",
        }
        for suite, capability in expected.items():
            manifest = json.loads((manifest_dir / f"{suite}.json").read_text())
            self.assertEqual(manifest["suite"], suite)
            self.assertEqual(manifest["capability"], capability)
            self.assertGreaterEqual(manifest["min_runs_per_case"], 3)
            self.assertTrue(forbidden_raw.issubset(set(manifest["forbidden_raw_tools"])))

    def test_change_graph_schema_exposes_validated_operation_kinds(self) -> None:
        agent = self._load_agent()
        schema = next(
            schema
            for schema in agent.get_tool_schemas()
            if schema["function"]["name"] == "change_graph"
        )
        operation_kind = schema["function"]["parameters"]["properties"]["operation_kind"]
        self.assertIn("operation_kind", schema["function"]["parameters"]["required"])
        self.assertEqual(
            operation_kind["enum"],
            [
                "set_param",
                "set_state",
                "add_variable",
                "disconnect",
                "rewire",
                "insert_block",
                "remove_block",
                "auto_insert",
                "clarify",
                "unsupported",
            ],
        )

    def test_model_facing_schemas_do_not_expose_debug_parameters(self) -> None:
        agent = self._load_agent()
        for schema in agent.get_tool_schemas():
            with self.subTest(tool=schema["function"]["name"]):
                properties = schema["function"]["parameters"]["properties"]
                self.assertNotIn("debug", properties)

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

    def test_search_blocks_dev_debug_can_include_internal_ranking_metadata(self) -> None:
        agent = self._load_agent()
        result = agent._search_blocks(
            query="throttle",
            k=3,
            debug=True,
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
        block_uid, instance_name, block_type, base_revision = self._dual_sink_target_block(agent)
        stale_target_ref = {
            "block_uid": block_uid,
            "expected_instance_name": instance_name,
            "expected_block_type": block_type,
            "base_state_revision": base_revision,
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
        self.assertEqual(stale["error_type"], "stale_revision")
        self.assertIn("stale", stale.get("message", "").lower())
        self.assertEqual(agent.session.state_revision, before_revision)
        self.assertEqual(agent.session.is_dirty, before_dirty)

    def test_change_graph_set_state_accepts_wrapper_era_target_ref(self) -> None:
        agent = self._load_dual_sink_agent()
        block_uid, instance_name, block_type, base_revision = self._dual_sink_target_block(agent)
        agent.init_turn_requirements("Disable qtgui_time_sink_x_1.")
        result = agent._change_graph(
            dry_run=False,
            user_goal="Disable qtgui_time_sink_x_1.",
            operation_kind="set_state",
            target_ref={
                "uid": block_uid,
                "instance_name": instance_name,
                "block_type": block_type,
                "state_revision": base_revision,
            },
            state="disabled",
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["operation_summary"], "update_states")
        self.assertEqual(result["validation_result"]["status"], "valid")

    def test_change_graph_set_state_accepts_guarded_target_ref(self) -> None:
        agent = self._load_dual_sink_agent()
        block_uid, instance_name, block_type, base_revision = self._dual_sink_target_block(agent)
        agent.init_turn_requirements("Disable qtgui_time_sink_x_1.")
        result = agent.execute_tool(
            "change_graph",
            {
                "dry_run": False,
                "user_goal": "Disable qtgui_time_sink_x_1.",
                "operation_kind": "set_state",
                "target_ref": {
                    "block_uid": block_uid,
                    "expected_instance_name": instance_name,
                    "expected_block_type": block_type,
                    "base_state_revision": base_revision,
                },
                "state": "disabled",
            },
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["operation_summary"], "update_states")
        self.assertEqual(result["validation_result"]["status"], "valid")

    def test_change_graph_set_state_rejects_partial_target_ref(self) -> None:
        agent = self._load_dual_sink_agent()
        block_uid, _, _, _ = self._dual_sink_target_block(agent)
        before_revision = agent.session.state_revision
        before_dirty = agent.session.is_dirty
        agent.init_turn_requirements("Disable qtgui_time_sink_x_1.")
        result = agent._change_graph(
            dry_run=False,
            user_goal="Disable qtgui_time_sink_x_1.",
            operation_kind="set_state",
            target_ref={"uid": block_uid},
            state="disabled",
        )
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["error_type"], "invalid_request")
        self.assertIn("Missing", result.get("message", ""))
        self.assertEqual(agent.session.state_revision, before_revision)
        self.assertEqual(agent.session.is_dirty, before_dirty)

    def test_change_graph_set_state_rejects_conflicting_mixed_target_ref(self) -> None:
        agent = self._load_dual_sink_agent()
        block_uid, instance_name, block_type, base_revision = self._dual_sink_target_block(agent)
        before_revision = agent.session.state_revision
        before_dirty = agent.session.is_dirty
        agent.init_turn_requirements("Disable qtgui_time_sink_x_1.")
        result = agent._change_graph(
            dry_run=False,
            user_goal="Disable qtgui_time_sink_x_1.",
            operation_kind="set_state",
            target_ref={
                "uid": block_uid,
                "block_uid": "block:0000000000000000",
                "instance_name": instance_name,
                "block_type": block_type,
                "state_revision": base_revision,
            },
            state="disabled",
        )
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["error_type"], "invalid_request")
        self.assertIn("conflicting values", result.get("message", ""))
        self.assertEqual(agent.session.state_revision, before_revision)
        self.assertEqual(agent.session.is_dirty, before_dirty)

    def test_change_graph_set_state_accepts_safe_mixed_target_ref(self) -> None:
        agent = self._load_dual_sink_agent()
        block_uid, instance_name, block_type, base_revision = self._dual_sink_target_block(agent)
        agent.init_turn_requirements("Disable qtgui_time_sink_x_1.")
        result = agent._change_graph(
            dry_run=False,
            user_goal="Disable qtgui_time_sink_x_1.",
            operation_kind="set_state",
            target_ref={
                "uid": block_uid,
                "block_uid": block_uid,
                "instance_name": instance_name,
                "expected_instance_name": instance_name,
                "block_type": block_type,
                "expected_block_type": block_type,
                "state_revision": base_revision,
            },
            state="disabled",
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["operation_summary"], "update_states")
        self.assertEqual(result["validation_result"]["status"], "valid")

    def test_change_graph_set_state_rejects_free_form_block_uid_only(self) -> None:
        agent = self._load_dual_sink_agent()
        block_uid, _, _, _ = self._dual_sink_target_block(agent)
        before_revision = agent.session.state_revision
        before_dirty = agent.session.is_dirty
        agent.init_turn_requirements("Disable qtgui_time_sink_x_1.")
        result = agent._change_graph(
            dry_run=False,
            user_goal="Disable qtgui_time_sink_x_1.",
            operation_kind="set_state",
            target_ref={"block_uid": block_uid},
            state="disabled",
        )
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["error_type"], "invalid_request")
        self.assertIn("Missing", result.get("message", ""))
        self.assertEqual(agent.session.state_revision, before_revision)
        self.assertEqual(agent.session.is_dirty, before_dirty)

    def test_change_graph_set_state_wrong_expected_instance_rejected(self) -> None:
        agent = self._load_dual_sink_agent()
        block_uid, _, block_type, base_revision = self._dual_sink_target_block(agent)
        before_revision = agent.session.state_revision
        before_dirty = agent.session.is_dirty
        agent.init_turn_requirements("Disable qtgui_time_sink_x_1.")
        result = agent.execute_tool(
            "change_graph",
            {
                "dry_run": False,
                "user_goal": "Disable qtgui_time_sink_x_1.",
                "operation_kind": "set_state",
                "target_ref": {
                    "block_uid": block_uid,
                    "expected_instance_name": "not_qtgui_time_sink_x_1",
                    "expected_block_type": block_type,
                    "base_state_revision": base_revision,
                },
                "state": "disabled",
            },
        )
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["error_type"], "preflight_rejected")
        self.assertEqual(agent.session.state_revision, before_revision)
        self.assertEqual(agent.session.is_dirty, before_dirty)

    def test_change_graph_set_state_wrong_expected_block_type_rejected(self) -> None:
        agent = self._load_dual_sink_agent()
        block_uid, instance_name, _, base_revision = self._dual_sink_target_block(agent)
        before_revision = agent.session.state_revision
        before_dirty = agent.session.is_dirty
        agent.init_turn_requirements("Disable qtgui_time_sink_x_1.")
        result = agent.execute_tool(
            "change_graph",
            {
                "dry_run": False,
                "user_goal": "Disable qtgui_time_sink_x_1.",
                "operation_kind": "set_state",
                "target_ref": {
                    "block_uid": block_uid,
                    "expected_instance_name": instance_name,
                    "expected_block_type": "blocks_throttle2",
                    "base_state_revision": base_revision,
                },
                "state": "disabled",
            },
        )
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["error_type"], "preflight_rejected")
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
        self.assertEqual(result["error_type"], "tool_call_invalid")
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
                "operation_kind": "unsupported",
            },
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_type"], "unsupported_op")

    def test_change_graph_requires_exact_details_or_clarifies(self) -> None:
        agent = self._load_agent()
        result = agent.execute_tool(
            "change_graph",
            {"dry_run": False, "user_goal": "Fix this graph.", "operation_kind": "clarify"},
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_type"], "clarification_required")
        self.assertIn("clarification_options", result)

    def test_change_graph_missing_dry_run_is_rejected_without_mutation(self) -> None:
        agent = self._load_agent()
        before_revision = agent.session.state_revision
        before_dirty = agent.session.is_dirty

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

        self.assertFalse(result["ok"], result)
        self.assertEqual(result["error_type"], "tool_call_invalid")
        self.assertEqual(
            result["validation_errors"][0]["field"],
            "dry_run",
            result,
        )
        self.assertEqual(agent.session.state_revision, before_revision)
        self.assertEqual(agent.session.is_dirty, before_dirty)

    def test_change_graph_save_is_not_model_facing(self) -> None:
        agent = self._load_agent()
        result = agent.execute_tool(
            "change_graph",
            {
                "dry_run": False,
                "user_goal": "Save a copy to /tmp/example.grc",
                "operation_kind": "unsupported",
            },
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

    def test_change_graph_add_variable_preview_does_not_mutate(self) -> None:
        agent = self._load_agent()
        before_revision = agent.session.state_revision
        before_dirty = agent.session.is_dirty
        before_variables = {
            block.instance_name
            for block in agent.session.flowgraph.blocks
            if block.block_type == "variable"
        }
        result = agent.execute_tool(
            "change_graph",
            {
                "dry_run": True,
                "user_goal": "Preview adding noise_level.",
                "operation_kind": "add_variable",
                "variable_name": "noise_level",
                "variable_value": "0.1",
            },
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["operation_summary"], "add_variable")
        self.assertIsNone(result.get("graph_delta"))
        planned = result.get("planned_operations") or []
        self.assertEqual(len(planned), 1)
        self.assertEqual(planned[0].get("op_type"), "add_block")
        self.assertEqual(planned[0].get("block_type"), "variable")
        self.assertEqual(planned[0].get("instance_name"), "noise_level")
        after_variables = {
            block.instance_name
            for block in agent.session.flowgraph.blocks
            if block.block_type == "variable"
        }
        self.assertEqual(before_variables, after_variables)
        self.assertEqual(agent.session.state_revision, before_revision)
        self.assertEqual(agent.session.is_dirty, before_dirty)

    def test_change_graph_add_variable_commit_has_expected_delta(self) -> None:
        agent = self._load_agent()
        before_revision = agent.session.state_revision
        before_variables = {
            block.instance_name
            for block in agent.session.flowgraph.blocks
            if block.block_type == "variable"
        }
        result = agent.execute_tool(
            "change_graph",
            {
                "dry_run": False,
                "user_goal": "Add noise_level variable.",
                "operation_kind": "add_variable",
                "variable_name": "noise_level",
                "variable_value": "0.1",
            },
        )
        self.assertTrue(result["ok"], result)
        graph_delta = result.get("graph_delta") or {}
        self.assertEqual(graph_delta.get("added_blocks"), ["noise_level"])
        self.assertEqual(graph_delta.get("validation_status"), "valid")
        self.assertEqual(graph_delta.get("validation_returncode"), 0)
        after_variables = {
            block.instance_name
            for block in agent.session.flowgraph.blocks
            if block.block_type == "variable"
        }
        self.assertIn("noise_level", after_variables)
        self.assertEqual(after_variables - before_variables, {"noise_level"})
        self.assertGreater(agent.session.state_revision, before_revision)

    def test_change_graph_add_variable_duplicate_name_refused(self) -> None:
        agent = self._load_agent()
        before_revision = agent.session.state_revision
        before_dirty = agent.session.is_dirty
        result = agent.execute_tool(
            "change_graph",
            {
                "dry_run": False,
                "user_goal": "Add existing samp_rate variable.",
                "operation_kind": "add_variable",
                "variable_name": "samp_rate",
                "variable_value": "123",
            },
        )
        self.assertFalse(result["ok"], result)
        self.assertEqual(result.get("error_type"), "block_already_exists")
        self.assertIn("set_param", result.get("message", ""))
        self.assertEqual(agent.session.state_revision, before_revision)
        self.assertEqual(agent.session.is_dirty, before_dirty)

    def test_change_graph_add_variable_invalid_name_refused(self) -> None:
        agent = self._load_agent()
        before_revision = agent.session.state_revision
        before_dirty = agent.session.is_dirty
        result = agent.execute_tool(
            "change_graph",
            {
                "dry_run": False,
                "user_goal": "Add invalid variable.",
                "operation_kind": "add_variable",
                "variable_name": "9bad",
                "variable_value": "1",
            },
        )
        self.assertFalse(result["ok"], result)
        self.assertEqual(result.get("error_type"), "invalid_request")
        self.assertIn("valid identifier", result.get("message", ""))
        self.assertEqual(agent.session.state_revision, before_revision)
        self.assertEqual(agent.session.is_dirty, before_dirty)

    def test_change_graph_add_variable_invalid_expression_refused_no_commit(self) -> None:
        agent = self._load_agent()
        before_revision = agent.session.state_revision
        before_dirty = agent.session.is_dirty
        result = agent.execute_tool(
            "change_graph",
            {
                "dry_run": False,
                "user_goal": "Add broken expression variable.",
                "operation_kind": "add_variable",
                "variable_name": "broken_expr",
                "variable_value": "(",
            },
        )
        self.assertFalse(result["ok"], result)
        self.assertEqual(result.get("error_type"), "gnu_validation_failed")
        self.assertIsNone(result.get("graph_delta"))
        self.assertEqual(agent.session.state_revision, before_revision)
        self.assertEqual(agent.session.is_dirty, before_dirty)

    def test_change_graph_add_variable_missing_value_refused(self) -> None:
        agent = self._load_agent()
        before_revision = agent.session.state_revision
        before_dirty = agent.session.is_dirty
        result = agent.execute_tool(
            "change_graph",
            {
                "dry_run": False,
                "user_goal": "Add variable without value.",
                "operation_kind": "add_variable",
                "variable_name": "missing_value",
            },
        )
        self.assertFalse(result["ok"], result)
        self.assertEqual(result.get("error_type"), "invalid_request")
        self.assertIn("variable_name and variable_value", result.get("message", ""))
        self.assertEqual(agent.session.state_revision, before_revision)
        self.assertEqual(agent.session.is_dirty, before_dirty)

    def test_change_graph_add_variable_empty_value_refused(self) -> None:
        agent = self._load_agent()
        before_revision = agent.session.state_revision
        before_dirty = agent.session.is_dirty
        result = agent.execute_tool(
            "change_graph",
            {
                "dry_run": False,
                "user_goal": "Add variable with empty value.",
                "operation_kind": "add_variable",
                "variable_name": "empty_value",
                "variable_value": "   ",
            },
        )
        self.assertFalse(result["ok"], result)
        self.assertEqual(result.get("error_type"), "invalid_request")
        self.assertIn("variable_name and variable_value", result.get("message", ""))
        self.assertEqual(agent.session.state_revision, before_revision)
        self.assertEqual(agent.session.is_dirty, before_dirty)

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

    def test_change_graph_disconnect_preview_by_exact_connection_id_does_not_mutate(self) -> None:
        agent = self._load_dual_sink_agent()
        listed = agent.execute_tool("inspect_graph", {"operation": "list_connections"})
        self.assertTrue(listed["ok"], listed)
        connection_id = "blocks_char_to_float_0:0->qtgui_time_sink_x_1:0"
        self.assertIn(connection_id, listed.get("items") or [])
        before_revision = agent.session.state_revision
        before_dirty = agent.session.is_dirty
        result = agent.execute_tool(
            "change_graph",
            {
                "dry_run": True,
                "user_goal": "Preview disconnect exact connection.",
                "operation_kind": "disconnect",
                "connection_id": connection_id,
            },
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["operation_summary"], "remove_connection")
        self.assertEqual(agent.session.state_revision, before_revision)
        self.assertEqual(agent.session.is_dirty, before_dirty)

    def test_change_graph_disconnect_by_endpoint_hints_resolves_exactly_one(self) -> None:
        agent = self._load_agent_with_fixture("random_bit_generator_dual_sink_sink1_disabled.grc")
        before_connections = list(agent.execute_tool("inspect_graph", {"operation": "list_connections"})["items"])
        before_revision = agent.session.state_revision
        result = agent.execute_tool(
            "change_graph",
            {
                "dry_run": False,
                "user_goal": "Disconnect the secondary sink input.",
                "operation_kind": "disconnect",
                "src_block": "blocks_char_to_float_0",
                "src_port": 0,
                "dst_block": "qtgui_time_sink_x_1",
                "dst_port": 0,
            },
        )
        self.assertTrue(result["ok"], result)
        after_connections = list(agent.execute_tool("inspect_graph", {"operation": "list_connections"})["items"])
        self.assertNotIn("blocks_char_to_float_0:0->qtgui_time_sink_x_1:0", after_connections)
        self.assertEqual(len(before_connections) - 1, len(after_connections))
        self.assertGreater(agent.session.state_revision, before_revision)

    def test_change_graph_disconnect_ambiguous_endpoint_hints_require_clarification(self) -> None:
        agent = self._load_agent_with_fixture("rewire_stream_ambiguous.grc")
        before_revision = agent.session.state_revision
        before_dirty = agent.session.is_dirty
        result = agent.execute_tool(
            "change_graph",
            {
                "dry_run": False,
                "user_goal": "Disconnect one input to qtgui_time_sink_x_0.",
                "operation_kind": "disconnect",
                "dst_block": "qtgui_time_sink_x_0",
            },
        )
        self.assertFalse(result["ok"], result)
        self.assertEqual(result.get("error_type"), "ambiguous_connection")
        self.assertIn("clarification_options", result)
        self.assertEqual(agent.session.state_revision, before_revision)
        self.assertEqual(agent.session.is_dirty, before_dirty)

    def test_change_graph_disconnect_invalid_commit_rolls_back(self) -> None:
        agent = self._load_agent()
        target_connection = "blocks_char_to_float_0:0->qtgui_time_sink_x_0:0"
        before_connections = list(agent.execute_tool("inspect_graph", {"operation": "list_connections"})["items"])
        self.assertIn(target_connection, before_connections)
        before_revision = agent.session.state_revision
        before_dirty = agent.session.is_dirty
        result = agent.execute_tool(
            "change_graph",
            {
                "dry_run": False,
                "user_goal": "Disconnect sink edge.",
                "operation_kind": "disconnect",
                "connection_id": target_connection,
            },
        )
        self.assertFalse(result["ok"], result)
        self.assertEqual(result.get("error_type"), "gnu_validation_failed")
        self.assertIsNone(result.get("graph_delta"))
        validation_result = result.get("validation_result")
        self.assertIsInstance(validation_result, dict)
        self.assertEqual(validation_result.get("status"), "invalid")
        after_connections = list(agent.execute_tool("inspect_graph", {"operation": "list_connections"})["items"])
        self.assertEqual(before_connections, after_connections)
        self.assertEqual(agent.session.state_revision, before_revision)
        self.assertEqual(agent.session.is_dirty, before_dirty)

    def test_change_graph_disconnect_stale_connection_id_rejected(self) -> None:
        agent = self._load_agent_with_fixture("random_bit_generator_dual_sink_sink1_disabled.grc")
        target_connection = "blocks_char_to_float_0:0->qtgui_time_sink_x_1:0"
        first = agent.execute_tool(
            "change_graph",
            {
                "dry_run": False,
                "user_goal": "Disconnect secondary sink input.",
                "operation_kind": "disconnect",
                "connection_id": target_connection,
            },
        )
        self.assertTrue(first["ok"], first)
        before_revision = agent.session.state_revision
        second = agent.execute_tool(
            "change_graph",
            {
                "dry_run": False,
                "user_goal": "Disconnect the same edge again.",
                "operation_kind": "disconnect",
                "connection_id": target_connection,
            },
        )
        self.assertFalse(second["ok"], second)
        self.assertEqual(second.get("error_type"), "preflight_rejected")
        self.assertEqual(agent.session.state_revision, before_revision)

    def test_change_graph_disconnect_stale_state_revision_rejected(self) -> None:
        agent = self._load_agent_with_fixture("random_bit_generator_dual_sink_sink1_disabled.grc")
        stale_revision = agent.session.state_revision
        mutate = agent.execute_tool(
            "change_graph",
            {
                "dry_run": False,
                "user_goal": "Disable qtgui_time_sink_x_1.",
                "operation_kind": "set_state",
                "instance_name": "qtgui_time_sink_x_1",
                "state": "disabled",
            },
        )
        self.assertTrue(mutate["ok"], mutate)
        before_revision = agent.session.state_revision
        before_connections = list(agent.execute_tool("inspect_graph", {"operation": "list_connections"})["items"])
        result = agent.execute_tool(
            "change_graph",
            {
                "dry_run": False,
                "user_goal": "Disconnect secondary sink input.",
                "operation_kind": "disconnect",
                "connection_id": "blocks_char_to_float_0:0->qtgui_time_sink_x_1:0",
                "state_revision": stale_revision,
            },
        )
        self.assertFalse(result["ok"], result)
        self.assertEqual(result.get("error_type"), "stale_revision")
        self.assertEqual(agent.session.state_revision, before_revision)
        after_connections = list(agent.execute_tool("inspect_graph", {"operation": "list_connections"})["items"])
        self.assertEqual(before_connections, after_connections)

    def test_change_graph_disconnect_message_port_supported(self) -> None:
        agent = self._load_agent_with_fixture("rewire_message_ambiguous.grc")
        before_connections = list(agent.execute_tool("inspect_graph", {"operation": "list_connections"})["items"])
        target_connection = "strobe_0:strobe->debug_0:print"
        self.assertIn(target_connection, before_connections)
        result = agent.execute_tool(
            "change_graph",
            {
                "dry_run": False,
                "user_goal": "Disconnect message edge.",
                "operation_kind": "disconnect",
                "connection_id": target_connection,
            },
        )
        self.assertTrue(result["ok"], result)
        after_connections = list(agent.execute_tool("inspect_graph", {"operation": "list_connections"})["items"])
        self.assertNotIn(target_connection, after_connections)

    def test_change_graph_remove_block_detached_preview_does_not_mutate(self) -> None:
        agent = self._load_agent_with_fixture("random_bit_generator_with_unused_var.grc")
        before_revision = agent.session.state_revision
        before_dirty = agent.session.is_dirty
        before_blocks = {
            block.instance_name for block in agent.session.flowgraph.blocks
        }
        result = agent.execute_tool(
            "change_graph",
            {
                "dry_run": True,
                "user_goal": "Preview removing unused_var.",
                "operation_kind": "remove_block",
                "instance_name": "unused_var",
            },
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["operation_summary"], "remove_block")
        planned = result.get("planned_operations") or []
        self.assertTrue(planned)
        self.assertEqual(planned[-1]["op_type"], "remove_block")
        self.assertEqual(planned[-1]["instance_name"], "unused_var")
        after_blocks = {block.instance_name for block in agent.session.flowgraph.blocks}
        self.assertEqual(before_blocks, after_blocks)
        self.assertEqual(agent.session.state_revision, before_revision)
        self.assertEqual(agent.session.is_dirty, before_dirty)

    def test_change_graph_remove_block_detached_commit_has_expected_delta(self) -> None:
        agent = self._load_agent_with_fixture("random_bit_generator_with_unused_var.grc")
        before_revision = agent.session.state_revision
        before_blocks = {
            block.instance_name for block in agent.session.flowgraph.blocks
        }
        result = agent.execute_tool(
            "change_graph",
            {
                "dry_run": False,
                "user_goal": "Remove detached unused_var.",
                "operation_kind": "remove_block",
                "instance_name": "unused_var",
            },
        )
        self.assertTrue(result["ok"], result)
        graph_delta = result.get("graph_delta") or {}
        self.assertEqual(graph_delta.get("removed_blocks"), ["unused_var"])
        self.assertNotIn("removed_connections", graph_delta)
        self.assertEqual(graph_delta.get("validation_status"), "valid")
        self.assertEqual(graph_delta.get("validation_returncode"), 0)
        after_blocks = {block.instance_name for block in agent.session.flowgraph.blocks}
        self.assertNotIn("unused_var", after_blocks)
        self.assertEqual(before_blocks - after_blocks, {"unused_var"})
        self.assertGreater(agent.session.state_revision, before_revision)

    def test_change_graph_remove_block_attached_without_explicit_detach_refuses(self) -> None:
        agent = self._load_agent_with_fixture("random_bit_generator_dual_sink_sink1_disabled.grc")
        target_connection = "blocks_char_to_float_0:0->qtgui_time_sink_x_1:0"
        before_revision = agent.session.state_revision
        before_connections = list(
            agent.execute_tool("inspect_graph", {"operation": "list_connections"})["items"]
        )
        result = agent.execute_tool(
            "change_graph",
            {
                "dry_run": False,
                "user_goal": "Remove qtgui_time_sink_x_1.",
                "operation_kind": "remove_block",
                "instance_name": "qtgui_time_sink_x_1",
            },
        )
        self.assertFalse(result["ok"], result)
        self.assertEqual(result.get("error_type"), "clarification_required")
        self.assertIn(target_connection, result.get("attached_connection_ids") or [])
        planned = result.get("planned_operations") or []
        self.assertTrue(any(op.get("op_type") == "remove_connection" for op in planned))
        self.assertTrue(any(op.get("op_type") == "remove_block" for op in planned))
        after_connections = list(
            agent.execute_tool("inspect_graph", {"operation": "list_connections"})["items"]
        )
        self.assertEqual(before_connections, after_connections)
        self.assertEqual(agent.session.state_revision, before_revision)

    def test_change_graph_remove_block_attached_with_explicit_detach_commits(self) -> None:
        agent = self._load_agent_with_fixture("random_bit_generator_dual_sink_sink1_disabled.grc")
        target_connection = "blocks_char_to_float_0:0->qtgui_time_sink_x_1:0"
        result = agent.execute_tool(
            "change_graph",
            {
                "dry_run": False,
                "user_goal": "Remove qtgui_time_sink_x_1 and detach its attached connection.",
                "operation_kind": "remove_block",
                "instance_name": "qtgui_time_sink_x_1",
                "detach_connections": True,
                "detach_connection_ids": [target_connection],
            },
        )
        self.assertTrue(result["ok"], result)
        graph_delta = result.get("graph_delta") or {}
        self.assertEqual(graph_delta.get("removed_blocks"), ["qtgui_time_sink_x_1"])
        self.assertEqual(graph_delta.get("removed_connections"), [target_connection])
        connections = list(agent.execute_tool("inspect_graph", {"operation": "list_connections"})["items"])
        self.assertNotIn(target_connection, connections)
        assert agent.session.flowgraph is not None
        self.assertNotIn(
            "qtgui_time_sink_x_1",
            [block.instance_name for block in agent.session.flowgraph.blocks],
        )

    def test_change_graph_remove_block_dependency_refusal_no_mutation(self) -> None:
        agent = self._load_agent()
        before_revision = agent.session.state_revision
        before_connections = list(
            agent.execute_tool("inspect_graph", {"operation": "list_connections"})["items"]
        )
        result = agent.execute_tool(
            "change_graph",
            {
                "dry_run": False,
                "user_goal": "Remove samp_rate.",
                "operation_kind": "remove_block",
                "instance_name": "samp_rate",
            },
        )
        self.assertFalse(result["ok"], result)
        self.assertEqual(result.get("error_type"), "preflight_rejected")
        errors = result.get("errors") or []
        self.assertTrue(errors, result)
        self.assertEqual(errors[0].get("code"), "block_still_referenced")
        after_connections = list(
            agent.execute_tool("inspect_graph", {"operation": "list_connections"})["items"]
        )
        self.assertEqual(before_connections, after_connections)
        self.assertEqual(agent.session.state_revision, before_revision)

    def test_change_graph_remove_block_duplicate_target_clarifies(self) -> None:
        agent = self._load_agent_with_fixture(
            "random_bit_generator_dual_sink_duplicate_sink_name.grc"
        )
        before_revision = agent.session.state_revision
        result = agent.execute_tool(
            "change_graph",
            {
                "dry_run": False,
                "user_goal": "Remove qtgui_time_sink_x_0.",
                "operation_kind": "remove_block",
                "instance_name": "qtgui_time_sink_x_0",
            },
        )
        self.assertFalse(result["ok"], result)
        self.assertEqual(result.get("error_type"), "ambiguous_block")
        self.assertTrue(result.get("clarification_options"), result)
        self.assertEqual(agent.session.state_revision, before_revision)

    def test_change_graph_remove_block_accepts_valid_target_ref(self) -> None:
        agent = self._load_agent_with_fixture("random_bit_generator_with_unused_var.grc")
        target_ref = self._block_target_ref(
            agent,
            instance_name="unused_var",
            block_type="variable",
        )
        result = agent.execute_tool(
            "change_graph",
            {
                "dry_run": False,
                "user_goal": "Remove detached unused_var by guarded target_ref.",
                "operation_kind": "remove_block",
                "target_ref": target_ref,
            },
        )
        self.assertTrue(result["ok"], result)
        assert agent.session.flowgraph is not None
        self.assertNotIn(
            "unused_var",
            [block.instance_name for block in agent.session.flowgraph.blocks],
        )

    def test_change_graph_remove_block_rejects_stale_target_ref(self) -> None:
        agent = self._load_agent_with_fixture("random_bit_generator_with_unused_var.grc")
        target_ref = self._block_target_ref(
            agent,
            instance_name="unused_var",
            block_type="variable",
        )
        mutate = agent.execute_tool(
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
        self.assertTrue(mutate["ok"], mutate)
        before_revision = agent.session.state_revision
        result = agent.execute_tool(
            "change_graph",
            {
                "dry_run": False,
                "user_goal": "Remove stale target_ref.",
                "operation_kind": "remove_block",
                "target_ref": target_ref,
            },
        )
        self.assertFalse(result["ok"], result)
        self.assertEqual(result.get("error_type"), "stale_revision")
        self.assertEqual(agent.session.state_revision, before_revision)

    def test_change_graph_remove_block_rejects_target_ref_identity_mismatch(self) -> None:
        agent = self._load_agent_with_fixture("random_bit_generator_with_unused_var.grc")
        target_ref = self._block_target_ref(
            agent,
            instance_name="unused_var",
            block_type="variable",
        )
        before_revision = agent.session.state_revision
        wrong_name = dict(target_ref)
        wrong_name["expected_instance_name"] = "not_unused_var"
        wrong_type = dict(target_ref)
        wrong_type["expected_block_type"] = "import"
        for bad_ref, expected_code in (
            (wrong_name, "block_uid_instance_mismatch"),
            (wrong_type, "block_uid_type_mismatch"),
        ):
            with self.subTest(expected_code=expected_code):
                result = agent.execute_tool(
                    "change_graph",
                    {
                        "dry_run": False,
                        "user_goal": "Remove with wrong guarded identity.",
                        "operation_kind": "remove_block",
                        "target_ref": bad_ref,
                    },
                )
                self.assertFalse(result["ok"], result)
                self.assertEqual(result.get("error_type"), "preflight_rejected")
                errors = result.get("errors") or []
                self.assertTrue(errors, result)
                self.assertEqual(errors[0].get("code"), expected_code)
                self.assertEqual(agent.session.state_revision, before_revision)

    def test_change_graph_insert_exact_preview_does_not_mutate(self) -> None:
        agent = self._load_agent()
        target_connection = "analog_random_source_x_0:0->blocks_throttle2_0:0"
        before_connections = set(
            agent.execute_tool("inspect_graph", {"operation": "list_connections"})["items"]
        )
        before_revision = agent.session.state_revision
        before_dirty = agent.session.is_dirty
        result = agent.execute_tool(
            "change_graph",
            {
                "dry_run": True,
                "user_goal": "Preview exact insert on the selected connection.",
                "operation_kind": "insert_block",
                "connection_id": target_connection,
                "block_id": "blocks_throttle2",
                "instance_name": "preview_insert_block_0",
                "insert_params": {"type": "byte", "samples_per_second": "32000"},
            },
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["operation_summary"], "insert_block_on_connection")
        after_connections = set(
            agent.execute_tool("inspect_graph", {"operation": "list_connections"})["items"]
        )
        self.assertEqual(before_connections, after_connections)
        self.assertEqual(agent.session.state_revision, before_revision)
        self.assertEqual(agent.session.is_dirty, before_dirty)

    def test_change_graph_insert_exact_commit_has_expected_delta(self) -> None:
        agent = self._load_agent()
        target_connection = "analog_random_source_x_0:0->blocks_throttle2_0:0"
        inserted_name = "blocks_throttle2_inserted"
        expected_added_a = f"analog_random_source_x_0:0->{inserted_name}:0"
        expected_added_b = f"{inserted_name}:0->blocks_throttle2_0:0"
        assert agent.session.flowgraph is not None
        before_blocks = {block.instance_name for block in agent.session.flowgraph.blocks}
        result = agent.execute_tool(
            "change_graph",
            {
                "dry_run": False,
                "user_goal": "Insert a compatible block on this exact connection.",
                "operation_kind": "insert_block",
                "connection_id": target_connection,
                "block_id": "blocks_throttle2",
                "instance_name": inserted_name,
                "insert_params": {"type": "byte", "samples_per_second": "32000"},
            },
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["operation_summary"], "insert_block_on_connection")
        graph_delta = result.get("graph_delta") or {}
        self.assertEqual(graph_delta.get("added_blocks"), [inserted_name])
        self.assertEqual(graph_delta.get("removed_connections"), [target_connection])
        self.assertEqual(
            set(graph_delta.get("added_connections") or []),
            {expected_added_a, expected_added_b},
        )
        after_connections = set(
            agent.execute_tool("inspect_graph", {"operation": "list_connections"})["items"]
        )
        assert agent.session.flowgraph is not None
        after_blocks = {block.instance_name for block in agent.session.flowgraph.blocks}
        self.assertEqual(len(after_blocks - before_blocks), 1)
        self.assertEqual(after_blocks - before_blocks, {inserted_name})
        self.assertIn(expected_added_a, after_connections)
        self.assertIn(expected_added_b, after_connections)
        self.assertNotIn(target_connection, after_connections)

    def test_change_graph_insert_incompatible_block_refused_without_mutation(self) -> None:
        agent = self._load_agent()
        target_connection = "analog_random_source_x_0:0->blocks_throttle2_0:0"
        before_connections = set(
            agent.execute_tool("inspect_graph", {"operation": "list_connections"})["items"]
        )
        before_revision = agent.session.state_revision
        before_dirty = agent.session.is_dirty
        result = agent.execute_tool(
            "change_graph",
            {
                "dry_run": False,
                "user_goal": "Insert an incompatible block on this connection.",
                "operation_kind": "insert_block",
                "connection_id": target_connection,
                "block_id": "blocks_add_xx",
                "instance_name": "add_incompatible_0",
            },
        )
        self.assertFalse(result["ok"], result)
        self.assertEqual(result.get("error_type"), "preflight_rejected")
        after_connections = set(
            agent.execute_tool("inspect_graph", {"operation": "list_connections"})["items"]
        )
        self.assertEqual(before_connections, after_connections)
        self.assertEqual(agent.session.state_revision, before_revision)
        self.assertEqual(agent.session.is_dirty, before_dirty)

    def test_change_graph_insert_requires_block_or_candidate_id(self) -> None:
        agent = self._load_agent()
        result = agent.execute_tool(
            "change_graph",
            {
                "dry_run": False,
                "user_goal": "Insert a block on this connection.",
                "operation_kind": "insert_block",
                "connection_id": "analog_random_source_x_0:0->blocks_throttle2_0:0",
            },
        )
        self.assertFalse(result["ok"], result)
        self.assertEqual(result.get("error_type"), "invalid_request")

    def test_change_graph_insert_requires_explicit_instance_name(self) -> None:
        agent = self._load_agent()
        result = agent.execute_tool(
            "change_graph",
            {
                "dry_run": False,
                "user_goal": "Insert a block on this connection.",
                "operation_kind": "insert_block",
                "connection_id": "analog_random_source_x_0:0->blocks_throttle2_0:0",
                "block_id": "blocks_throttle2",
                "insert_params": {"type": "byte", "samples_per_second": "32000"},
            },
        )
        self.assertFalse(result["ok"], result)
        self.assertEqual(result.get("error_type"), "invalid_request")
        self.assertIn("instance_name", result.get("message", ""))

    def test_change_graph_insert_rejects_conflicting_block_and_candidate_id(self) -> None:
        agent = self._load_agent()
        result = agent.execute_tool(
            "change_graph",
            {
                "dry_run": False,
                "user_goal": "Insert a block on this connection.",
                "operation_kind": "insert_block",
                "connection_id": "analog_random_source_x_0:0->blocks_throttle2_0:0",
                "block_id": "blocks_head",
                "candidate_id": "blocks_throttle2",
            },
        )
        self.assertFalse(result["ok"], result)
        self.assertEqual(result.get("error_type"), "invalid_request")

    def test_change_graph_insert_stale_connection_id_rejected(self) -> None:
        agent = self._load_agent()
        target_connection = "analog_random_source_x_0:0->blocks_throttle2_0:0"
        first = agent.execute_tool(
            "change_graph",
            {
                "dry_run": False,
                "user_goal": "Insert a compatible block on this connection.",
                "operation_kind": "insert_block",
                "connection_id": target_connection,
                "block_id": "blocks_throttle2",
                "instance_name": "insert_once_0",
                "insert_params": {"type": "byte", "samples_per_second": "32000"},
            },
        )
        self.assertTrue(first["ok"], first)
        before_revision = agent.session.state_revision
        second = agent.execute_tool(
            "change_graph",
            {
                "dry_run": False,
                "user_goal": "Insert again on the same stale connection.",
                "operation_kind": "insert_block",
                "connection_id": target_connection,
                "block_id": "blocks_throttle2",
                "instance_name": "insert_twice_0",
            },
        )
        self.assertFalse(second["ok"], second)
        self.assertEqual(second.get("error_type"), "preflight_rejected")
        self.assertEqual(agent.session.state_revision, before_revision)

    def test_change_graph_insert_stale_state_revision_rejected(self) -> None:
        agent = self._load_agent()
        stale_revision = agent.session.state_revision
        mutate = agent.execute_tool(
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
        self.assertTrue(mutate["ok"], mutate)
        before_revision = agent.session.state_revision
        before_connections = set(
            agent.execute_tool("inspect_graph", {"operation": "list_connections"})["items"]
        )
        result = agent.execute_tool(
            "change_graph",
            {
                "dry_run": False,
                "user_goal": "Insert a compatible block on this connection.",
                "operation_kind": "insert_block",
                "connection_id": "analog_random_source_x_0:0->blocks_throttle2_0:0",
                "block_id": "blocks_throttle2",
                "instance_name": "stale_insert_0",
                "state_revision": stale_revision,
            },
        )
        self.assertFalse(result["ok"], result)
        self.assertEqual(result.get("error_type"), "stale_revision")
        self.assertEqual(agent.session.state_revision, before_revision)
        after_connections = set(
            agent.execute_tool("inspect_graph", {"operation": "list_connections"})["items"]
        )
        self.assertEqual(before_connections, after_connections)

    def test_change_graph_insert_grcc_failure_rolls_back_without_mutation(self) -> None:
        agent = self._load_agent()
        target_connection = "analog_random_source_x_0:0->blocks_throttle2_0:0"
        before_revision = agent.session.state_revision
        before_dirty = agent.session.is_dirty
        before_connections = set(
            agent.execute_tool("inspect_graph", {"operation": "list_connections"})["items"]
        )

        def _fake_invalid_grcc(_raw_data: object) -> tuple[bool, str, str, int]:
            return (False, "", "forced invalid for test", 1)

        with mock.patch.object(
            agent.session.__class__,
            "_run_grcc_validation",
            side_effect=_fake_invalid_grcc,
        ):
            result = agent.execute_tool(
                "change_graph",
                {
                    "dry_run": False,
                    "user_goal": "Insert a compatible block on this connection.",
                    "operation_kind": "insert_block",
                    "connection_id": target_connection,
                    "block_id": "blocks_throttle2",
                    "instance_name": "rollback_insert_0",
                    "insert_params": {"type": "byte", "samples_per_second": "32000"},
                },
            )

        self.assertFalse(result["ok"], result)
        self.assertEqual(result.get("error_type"), "gnu_validation_failed")
        self.assertEqual(agent.session.state_revision, before_revision)
        self.assertEqual(agent.session.is_dirty, before_dirty)
        after_connections = set(
            agent.execute_tool("inspect_graph", {"operation": "list_connections"})["items"]
        )
        self.assertEqual(before_connections, after_connections)

    def test_change_graph_insert_message_connection_refused(self) -> None:
        agent = self._load_agent_with_fixture("rewire_message_ambiguous.grc")
        before_revision = agent.session.state_revision
        before_connections = set(
            agent.execute_tool("inspect_graph", {"operation": "list_connections"})["items"]
        )
        result = agent.execute_tool(
            "change_graph",
            {
                "dry_run": False,
                "user_goal": "Insert on a message connection.",
                "operation_kind": "insert_block",
                "connection_id": "strobe_0:strobe->debug_0:print",
                "block_id": "blocks_throttle2",
                "instance_name": "msg_insert_0",
            },
        )
        self.assertFalse(result["ok"], result)
        self.assertEqual(result.get("error_type"), "preflight_rejected")
        self.assertEqual(agent.session.state_revision, before_revision)
        after_connections = set(
            agent.execute_tool("inspect_graph", {"operation": "list_connections"})["items"]
        )
        self.assertEqual(before_connections, after_connections)

    def test_change_graph_insert_block_rejects_standalone_add_block(self) -> None:
        agent = self._load_agent()
        result = agent.execute_tool(
            "change_graph",
            {
                "dry_run": False,
                "user_goal": "Add a new throttle block.",
                "operation_kind": "insert_block",
                "block_id": "blocks_throttle2",
                "instance_name": "standalone_insert_0",
            },
        )
        self.assertFalse(result["ok"], result)
        self.assertEqual(result.get("error_type"), "invalid_request")

    def test_change_graph_rewire_preview_exact_stream_does_not_mutate(self) -> None:
        agent = self._load_agent()
        old_connection = "blocks_throttle2_0:0->blocks_char_to_float_0:0"
        before_connections = set(
            agent.execute_tool("inspect_graph", {"operation": "list_connections"})["items"]
        )
        self.assertIn(old_connection, before_connections)
        before_revision = agent.session.state_revision
        before_dirty = agent.session.is_dirty
        result = agent.execute_tool(
            "change_graph",
            {
                "dry_run": True,
                "user_goal": "Preview exact rewire.",
                "operation_kind": "rewire",
                "connection_id": old_connection,
                "state_revision": before_revision,
                "new_src_block": "analog_random_source_x_0",
                "new_src_port": 0,
                "new_dst_block": "blocks_char_to_float_0",
                "new_dst_port": 0,
            },
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["operation_summary"], "rewire_connection")
        after_connections = set(
            agent.execute_tool("inspect_graph", {"operation": "list_connections"})["items"]
        )
        self.assertEqual(before_connections, after_connections)
        self.assertEqual(agent.session.state_revision, before_revision)
        self.assertEqual(agent.session.is_dirty, before_dirty)

    def test_change_graph_rewire_exact_stream_commit_has_one_removed_one_added(self) -> None:
        agent = self._load_agent()
        old_connection = "blocks_throttle2_0:0->blocks_char_to_float_0:0"
        new_connection = "analog_random_source_x_0:0->blocks_char_to_float_0:0"
        before_connections = set(
            agent.execute_tool("inspect_graph", {"operation": "list_connections"})["items"]
        )
        self.assertIn(old_connection, before_connections)
        before_revision = agent.session.state_revision
        result = agent.execute_tool(
            "change_graph",
            {
                "dry_run": False,
                "user_goal": "Rewire exact connection.",
                "operation_kind": "rewire",
                "connection_id": old_connection,
                "state_revision": before_revision,
                "new_src_block": "analog_random_source_x_0",
                "new_src_port": 0,
                "new_dst_block": "blocks_char_to_float_0",
                "new_dst_port": 0,
            },
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["operation_summary"], "rewire_connection")
        self.assertEqual(result["validation_result"]["status"], "valid")
        after_connections = set(
            agent.execute_tool("inspect_graph", {"operation": "list_connections"})["items"]
        )
        removed = before_connections - after_connections
        added = after_connections - before_connections
        self.assertEqual(removed, {old_connection})
        self.assertEqual(added, {new_connection})

    def test_change_graph_rewire_exact_message_commit(self) -> None:
        agent = self._load_agent_with_fixture("rewire_message_ambiguous.grc")
        old_connection = "strobe_0:strobe->debug_0:print"
        new_connection = "strobe_0:strobe->debug_1:print"
        before_connections = set(
            agent.execute_tool("inspect_graph", {"operation": "list_connections"})["items"]
        )
        self.assertIn(old_connection, before_connections)
        before_revision = agent.session.state_revision
        result = agent.execute_tool(
            "change_graph",
            {
                "dry_run": False,
                "user_goal": "Rewire exact message connection.",
                "operation_kind": "rewire",
                "connection_id": old_connection,
                "state_revision": before_revision,
                "new_src_block": "strobe_0",
                "new_src_port": "strobe",
                "new_dst_block": "debug_1",
                "new_dst_port": "print",
            },
        )
        self.assertTrue(result["ok"], result)
        after_connections = set(
            agent.execute_tool("inspect_graph", {"operation": "list_connections"})["items"]
        )
        self.assertNotIn(old_connection, after_connections)
        self.assertIn(new_connection, after_connections)

    def test_change_graph_rewire_invalid_new_endpoint_refused_without_mutation(self) -> None:
        agent = self._load_agent_with_fixture("rewire_message_ambiguous.grc")
        before_connections = set(
            agent.execute_tool("inspect_graph", {"operation": "list_connections"})["items"]
        )
        before_revision = agent.session.state_revision
        before_dirty = agent.session.is_dirty
        result = agent.execute_tool(
            "change_graph",
            {
                "dry_run": False,
                "user_goal": "Rewire to missing endpoint.",
                "operation_kind": "rewire",
                "connection_id": "strobe_0:strobe->debug_0:print",
                "state_revision": before_revision,
                "new_src_block": "strobe_0",
                "new_src_port": "strobe",
                "new_dst_block": "missing_debug",
                "new_dst_port": "print",
            },
        )
        self.assertFalse(result["ok"], result)
        self.assertEqual(result.get("error_type"), "preflight_rejected")
        after_connections = set(
            agent.execute_tool("inspect_graph", {"operation": "list_connections"})["items"]
        )
        self.assertEqual(before_connections, after_connections)
        self.assertEqual(agent.session.state_revision, before_revision)
        self.assertEqual(agent.session.is_dirty, before_dirty)

    def test_change_graph_rewire_stale_old_connection_rejected(self) -> None:
        agent = self._load_agent()
        old_connection = "blocks_throttle2_0:0->blocks_char_to_float_0:0"
        first_revision = agent.session.state_revision
        first = agent.execute_tool(
            "change_graph",
            {
                "dry_run": False,
                "user_goal": "Rewire exact stream connection.",
                "operation_kind": "rewire",
                "connection_id": old_connection,
                "state_revision": first_revision,
                "new_src_block": "analog_random_source_x_0",
                "new_src_port": 0,
                "new_dst_block": "blocks_char_to_float_0",
                "new_dst_port": 0,
            },
        )
        self.assertTrue(first["ok"], first)
        before_second_connections = set(
            agent.execute_tool("inspect_graph", {"operation": "list_connections"})["items"]
        )
        before_second_revision = agent.session.state_revision
        second = agent.execute_tool(
            "change_graph",
            {
                "dry_run": False,
                "user_goal": "Rewire the same old connection again.",
                "operation_kind": "rewire",
                "connection_id": old_connection,
                "state_revision": before_second_revision,
                "new_src_block": "analog_random_source_x_0",
                "new_src_port": 0,
                "new_dst_block": "qtgui_time_sink_x_0",
                "new_dst_port": 0,
            },
        )
        self.assertFalse(second["ok"], second)
        self.assertEqual(second.get("error_type"), "connection_not_found")
        after_second_connections = set(
            agent.execute_tool("inspect_graph", {"operation": "list_connections"})["items"]
        )
        self.assertEqual(before_second_connections, after_second_connections)
        self.assertEqual(agent.session.state_revision, before_second_revision)

    def test_change_graph_rewire_stale_state_revision_rejected(self) -> None:
        agent = self._load_agent()
        stale_revision = agent.session.state_revision
        mutate = agent.execute_tool(
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
        self.assertTrue(mutate["ok"], mutate)
        before_connections = set(
            agent.execute_tool("inspect_graph", {"operation": "list_connections"})["items"]
        )
        before_revision = agent.session.state_revision
        result = agent.execute_tool(
            "change_graph",
            {
                "dry_run": False,
                "user_goal": "Rewire exact stream connection.",
                "operation_kind": "rewire",
                "connection_id": "blocks_throttle2_0:0->blocks_char_to_float_0:0",
                "state_revision": stale_revision,
                "new_src_block": "analog_random_source_x_0",
                "new_src_port": 0,
                "new_dst_block": "blocks_char_to_float_0",
                "new_dst_port": 0,
            },
        )
        self.assertFalse(result["ok"], result)
        self.assertEqual(result.get("error_type"), "stale_revision")
        after_connections = set(
            agent.execute_tool("inspect_graph", {"operation": "list_connections"})["items"]
        )
        self.assertEqual(before_connections, after_connections)
        self.assertEqual(agent.session.state_revision, before_revision)

    def test_change_graph_rewire_requires_state_revision(self) -> None:
        agent = self._load_agent()
        before_connections = set(
            agent.execute_tool("inspect_graph", {"operation": "list_connections"})["items"]
        )
        before_revision = agent.session.state_revision
        result = agent.execute_tool(
            "change_graph",
            {
                "dry_run": False,
                "user_goal": "Rewire exact stream connection.",
                "operation_kind": "rewire",
                "connection_id": "blocks_throttle2_0:0->blocks_char_to_float_0:0",
                "new_src_block": "analog_random_source_x_0",
                "new_src_port": 0,
                "new_dst_block": "blocks_char_to_float_0",
                "new_dst_port": 0,
            },
        )
        self.assertFalse(result["ok"], result)
        self.assertEqual(result.get("error_type"), "invalid_request")
        self.assertEqual(agent.session.state_revision, before_revision)
        after_connections = set(
            agent.execute_tool("inspect_graph", {"operation": "list_connections"})["items"]
        )
        self.assertEqual(before_connections, after_connections)

    def test_change_graph_rewire_ambiguous_old_edge_clarifies_without_mutation(self) -> None:
        agent = self._load_agent_with_fixture("rewire_message_ambiguous.grc")
        before_connections = set(
            agent.execute_tool("inspect_graph", {"operation": "list_connections"})["items"]
        )
        before_revision = agent.session.state_revision
        before_dirty = agent.session.is_dirty
        result = agent.execute_tool(
            "change_graph",
            {
                "dry_run": False,
                "user_goal": "Rewire one strobe output edge to debug_1.",
                "operation_kind": "rewire",
                "state_revision": before_revision,
                "src_block": "strobe_0",
                "src_port": "strobe",
                "new_src_block": "strobe_0",
                "new_src_port": "strobe",
                "new_dst_block": "debug_1",
                "new_dst_port": "print",
            },
        )
        self.assertFalse(result["ok"], result)
        self.assertEqual(result.get("error_type"), "ambiguous_connection")
        self.assertIn("clarification_options", result)
        after_connections = set(
            agent.execute_tool("inspect_graph", {"operation": "list_connections"})["items"]
        )
        self.assertEqual(before_connections, after_connections)
        self.assertEqual(agent.session.state_revision, before_revision)
        self.assertEqual(agent.session.is_dirty, before_dirty)

    def test_change_graph_rewire_ambiguous_new_source_clarifies_without_mutation(self) -> None:
        agent = self._load_agent_with_fixture("rewire_message_ambiguous.grc")
        before_connections = set(
            agent.execute_tool("inspect_graph", {"operation": "list_connections"})["items"]
        )
        before_revision = agent.session.state_revision
        before_dirty = agent.session.is_dirty
        result = agent.execute_tool(
            "change_graph",
            {
                "dry_run": False,
                "user_goal": "Rewire message edge with bounded new source hints.",
                "operation_kind": "rewire",
                "connection_id": "strobe_0:strobe->debug_0:print",
                "state_revision": before_revision,
                "new_src_port": "strobe",
                "new_dst_block": "debug_1",
                "new_dst_port": "print",
            },
        )
        self.assertFalse(result["ok"], result)
        self.assertEqual(result.get("error_type"), "ambiguous_rewire_endpoint")
        self.assertIn("clarification_options", result)
        after_connections = set(
            agent.execute_tool("inspect_graph", {"operation": "list_connections"})["items"]
        )
        self.assertEqual(before_connections, after_connections)
        self.assertEqual(agent.session.state_revision, before_revision)
        self.assertEqual(agent.session.is_dirty, before_dirty)

    def test_change_graph_rewire_ambiguous_new_destination_clarifies_without_mutation(self) -> None:
        agent = self._load_agent_with_fixture("rewire_message_ambiguous.grc")
        before_connections = set(
            agent.execute_tool("inspect_graph", {"operation": "list_connections"})["items"]
        )
        before_revision = agent.session.state_revision
        before_dirty = agent.session.is_dirty
        result = agent.execute_tool(
            "change_graph",
            {
                "dry_run": False,
                "user_goal": "Rewire message edge with bounded new destination hints.",
                "operation_kind": "rewire",
                "connection_id": "strobe_0:strobe->debug_0:print",
                "state_revision": before_revision,
                "new_src_block": "strobe_0",
                "new_src_port": "strobe",
                "new_dst_port": "print",
            },
        )
        self.assertFalse(result["ok"], result)
        self.assertEqual(result.get("error_type"), "ambiguous_rewire_endpoint")
        self.assertIn("clarification_options", result)
        after_connections = set(
            agent.execute_tool("inspect_graph", {"operation": "list_connections"})["items"]
        )
        self.assertEqual(before_connections, after_connections)
        self.assertEqual(agent.session.state_revision, before_revision)
        self.assertEqual(agent.session.is_dirty, before_dirty)

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

    def test_mvp_model_driven_debug_argument_is_rejected(self) -> None:
        agent = self._load_agent()
        result = agent.execute_tool(
            "inspect_graph",
            {"operation": "summarize", "debug": True},
            model_tool_call=True,
        )
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["error_type"], "invalid_request")

    def test_model_facing_results_do_not_expose_internal_dispatch_names(self) -> None:
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
            model_tool_call=True,
        )
        self.assertTrue(result["ok"], result)
        text = json.dumps(result, sort_keys=True)
        for internal_name in (
            "internal_handler_called",
            "apply_edit",
            "propose_edit",
            "validate_graph",
            "save_graph\"",
        ):
            self.assertNotIn(internal_name, text)

    def test_change_graph_missing_operation_kind_fails_without_mutation(self) -> None:
        agent = self._load_agent()
        before_revision = agent.session.state_revision
        before_dirty = agent.session.is_dirty
        result = agent.execute_tool(
            "change_graph",
            {
                "dry_run": False,
                "user_goal": "Set samp_rate to 48000.",
                "instance_name": "samp_rate",
                "param_key": "value",
                "param_value": "48000",
            },
            model_tool_call=True,
        )
        self.assertFalse(result["ok"], result)
        self.assertEqual(agent.session.state_revision, before_revision)
        self.assertEqual(agent.session.is_dirty, before_dirty)

    def test_mvp_internal_tool_name_requests_are_not_translated_to_mutations(self) -> None:
        agent = self._load_agent()
        before_revision = agent.session.state_revision
        before_dirty = agent.session.is_dirty
        cases = (
            "Call apply_edit directly to set samp_rate to 12345. Do not use change_graph.",
            "Use rewire_connection to move blocks_throttle2_0:0->blocks_char_to_float_0:0.",
            "Invoke save_graph now instead of save_graph_explicit.",
        )
        for prompt in cases:
            with self.subTest(prompt=prompt):
                result = agent.check_unsupported_request(prompt)
                self.assertIsNotNone(result)
                assert result is not None
                self.assertIn("compatibility tool", result["assistant_text"])
                self.assertIn("will not translate", result["assistant_text"])
                for internal_name in ("apply_edit", "rewire_connection", "`save_graph`"):
                    self.assertNotIn(internal_name, result["assistant_text"])
                self.assertEqual(agent.session.state_revision, before_revision)
                self.assertEqual(agent.session.is_dirty, before_dirty)

    def test_mvp_internal_tool_name_guard_does_not_block_normal_wrapper_intent(self) -> None:
        agent = self._load_agent()
        allowed_prompts = (
            "Disconnect connection blocks_throttle2_0:0->blocks_char_to_float_0:0.",
            (
                "Rewire connection blocks_throttle2_0:0->blocks_char_to_float_0:0 "
                "to analog_random_source_x_0:0->blocks_char_to_float_0:0."
            ),
            "Save this graph to an explicit copy path.",
        )
        for prompt in allowed_prompts:
            with self.subTest(prompt=prompt):
                self.assertIsNone(agent.check_unsupported_request(prompt))


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
