"""Passive contract audit tests for model-facing wrapper payload shapes."""

from __future__ import annotations

from pathlib import Path
import shutil
import tempfile
import unittest

from grc_agent._payload import audit_change_graph_result_shape
from grc_agent.agent import GrcAgent
from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.session_ops import connection_id


class RuntimeToolResultContractTests(unittest.TestCase):
    def _load_agent(self) -> GrcAgent:
        session = FlowgraphSession()
        fixture = Path(__file__).resolve().parent / "data" / "random_bit_generator.grc"
        session.load(fixture)
        return GrcAgent(session)

    def _load_temp_agent_from_path(self, source: Path) -> GrcAgent:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        destination = Path(tmp.name) / source.name
        shutil.copy2(source, destination)
        session = FlowgraphSession()
        session.load(destination)
        return GrcAgent(session)

    def test_preview_payload_has_expected_shape_and_no_mutation_delta(self) -> None:
        agent = self._load_agent()
        result = agent.execute_tool(
            "change_graph",
            {
                "dry_run": True,
                "op": "set_param",
                "user_goal": "preview samp_rate update",
                "args": {
                    "instance_name": "samp_rate",
                    "param_key": "value",
                    "param_value": "48000",
                },
            },
        )
        findings = audit_change_graph_result_shape(result)
        self.assertEqual(findings, [], {"result": result, "findings": findings})
        self.assertFalse(result["dry_run"] is False and result.get("committed") is True)
        self.assertFalse(result.get("committed"), result)
        self.assertIsInstance(result.get("state_revision"), int, result)
        self.assertNotIn("active_session", result)

    def test_refused_payload_does_not_report_success_like_delta(self) -> None:
        agent = self._load_agent()
        result = agent.execute_tool(
            "change_graph",
            {
                "dry_run": False,
                "op": "disconnect",
                "user_goal": "disconnect a missing edge",
                "args": {"connection_id": "missing_src:0->missing_dst:0"},
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
                "op": "set_param",
                "user_goal": "set samp_rate to 32000",
                "args": {
                    "instance_name": "samp_rate",
                    "param_key": "value",
                    "param_value": "32000",
                },
            },
        )
        findings = audit_change_graph_result_shape(result)
        self.assertNotIn("commit_success_missing_graph_delta", findings, result)
        self.assertTrue(result.get("ok"), result)
        self.assertTrue(result.get("committed"), result)
        self.assertNotIn("active_session", result)

    def test_add_variable_commit_reports_added_block_delta(self) -> None:
        agent = self._load_agent()
        result = agent.execute_tool(
            "change_graph",
            {
                "dry_run": False,
                "op": "add_variable",
                "user_goal": "add noise_level variable",
                "args": {
                    "variable_name": "noise_level",
                    "variable_value": "0.1",
                },
            },
        )
        findings = audit_change_graph_result_shape(result)
        self.assertNotIn("commit_success_missing_graph_delta", findings, result)
        graph_delta = result.get("graph_delta") or {}
        self.assertEqual(graph_delta.get("added_blocks"), ["noise_level"])
        self.assertEqual(graph_delta.get("validation_status"), "valid")
        self.assertEqual(graph_delta.get("validation_returncode"), 0)

    def test_add_variable_validation_failure_has_no_success_like_delta(self) -> None:
        agent = self._load_agent()
        result = agent.execute_tool(
            "change_graph",
            {
                "dry_run": False,
                "op": "add_variable",
                "user_goal": "add broken variable expression",
                "args": {
                    "variable_name": "broken_expr",
                    "variable_value": "(",
                },
            },
        )
        findings = audit_change_graph_result_shape(result)
        self.assertNotIn(
            "failed_or_refused_contains_success_like_graph_delta",
            findings,
            {"result": result, "findings": findings},
        )
        self.assertFalse(result.get("ok"), result)
        self.assertIsNone(result.get("graph_delta"))
        self.assertFalse(result.get("committed"), result)
        self.assertNotIn("active_session", result)

    def test_clarification_payload_is_explicit(self) -> None:
        agent = self._load_agent()
        result = agent.execute_tool(
            "change_graph",
            {
                "dry_run": True,
                "op": "clarify",
                "user_goal": "need clarification",
            },
        )
        findings = audit_change_graph_result_shape(result)
        self.assertEqual(findings, [], {"result": result, "findings": findings})
        self.assertFalse(result.get("ok"), result)
        options = result.get("clarification_options")
        self.assertIsInstance(options, list, result)
        self.assertTrue(options, result)

    def test_nested_operation_args_are_normalized(self) -> None:
        agent = self._load_agent()
        result = agent.execute_tool(
            "change_graph",
            {
                "dry_run": True,
                "op": "set_param",
                "user_goal": "preview samp_rate update",
                "args": {
                    "set_param": {
                        '"instance_name"': "samp_rate",
                        '"param_key"': "value",
                        '"param_value"': "48000",
                    }
                },
            },
        )

        self.assertTrue(result.get("ok"), result)
        self.assertFalse(result.get("committed"), result)
        self.assertEqual(result.get("operation_kind"), "set_param")

    def test_inspect_details_without_targets_returns_structured_error(self) -> None:
        agent = self._load_agent()
        result = agent.execute_tool(
            "inspect_graph",
            {"view": "details"},
            model_tool_call=True,
        )

        self.assertFalse(result.get("ok"), result)
        self.assertEqual(result.get("errors", [{}])[0].get("code"), "target_required")

    def test_generic_add_block_is_not_model_facing(self) -> None:
        agent = self._load_agent()
        before_revision = agent.session.state_revision
        result = agent.execute_tool(
            "change_graph",
            {
                "dry_run": False,
                "op": "add_block",
                "user_goal": "add a source and connect it to the graph",
                "args": {
                    "block_id": "analog_sig_source_x",
                    "instance_name": "analog_sig_source_x_extra",
                    "insert_params": {"type": "float", "freq": "1000"},
                },
            },
        )

        self.assertFalse(result.get("ok"), result)
        self.assertEqual(result.get("error_type"), "tool_call_invalid")
        self.assertEqual(result.get("validation_errors", [{}])[0].get("code"), "invalid_enum")
        self.assertIn("add_signal_source_to_sum", result.get("message", ""))
        self.assertEqual(agent.session.state_revision, before_revision)

    def test_insert_in_connection_missing_placement_returns_clarification(self) -> None:
        agent = self._load_agent()
        result = agent.execute_tool(
            "change_graph",
            {
                "dry_run": True,
                "op": "insert_in_connection",
                "user_goal": "preview adding a signal source into an existing connection",
                "args": {
                    "insert_in_connection": {
                        '"block_id"': "analog_sig_source_x",
                    }
                },
            },
        )

        self.assertFalse(result.get("ok"), result)
        self.assertEqual(result.get("error_type"), "clarification_required")
        self.assertIn("connection_id", result.get("message", ""))
        self.assertTrue(result.get("clarification_options"), result)

    def test_add_signal_source_to_sum_preview_and_commit_are_validated(self) -> None:
        fixture = (
            Path(__file__).resolve().parents[1]
            / "playground"
            / "grc_agent_interactive"
            / "dial_tone_interactive.grc"
        )
        agent = self._load_temp_agent_from_path(fixture)
        before_revision = agent.session.state_revision

        preview = agent.execute_tool(
            "change_graph",
            {
                "dry_run": True,
                "op": "add_signal_source_to_sum",
                "user_goal": "preview adding another signal source with frequency 1000 and connect it",
                "args": {"block_id": "analog_sig_source_x", "freq": 1000},
            },
        )
        self.assertTrue(preview.get("ok"), preview)
        self.assertFalse(preview.get("committed"), preview)
        preview_token = preview.get("preview_token")
        self.assertIsInstance(preview_token, str, preview)
        self.assertNotIn("normalized_from_preview", preview)
        self.assertEqual(agent.session.state_revision, before_revision)
        preview_text = agent._tool_history_content_as_text(preview, tool_name="change_graph")
        self.assertLess(len(preview_text), 320)
        self.assertIn("preview_token=", preview_text)
        self.assertIn("connect analog_sig_source_x_2:0->blocks_add_xx:3", preview_text)

        commit = agent.execute_tool(
            "change_graph",
            {
                "dry_run": False,
                "op": "add_signal_source_to_sum",
                "user_goal": "add another signal source with frequency 1000 and connect it",
                "state_revision": agent.session.state_revision,
                "preview_token": preview_token,
                "args": {"block_id": "analog_sig_source_x", "freq": 1000},
            },
        )

        findings = audit_change_graph_result_shape(commit)
        self.assertNotIn("commit_success_missing_graph_delta", findings, commit)
        self.assertTrue(commit.get("ok"), commit)
        self.assertTrue(commit.get("committed"), commit)
        self.assertEqual(commit.get("validation_result", {}).get("status"), "valid")
        self.assertEqual(commit.get("autosave", {}).get("ok"), True)
        assert agent.session.flowgraph is not None
        block_names = {block.instance_name for block in agent.session.flowgraph.blocks}
        self.assertIn("analog_sig_source_x_2", block_names)
        connections = {
            connection_id(c.src_block, c.src_port, c.dst_block, c.dst_port)
            for c in agent.session.flowgraph.connections
        }
        self.assertIn("analog_sig_source_x_2:0->blocks_add_xx:3", connections)

    def test_add_signal_source_to_sum_rejects_destination_as_source_block_id(self) -> None:
        fixture = (
            Path(__file__).resolve().parents[1]
            / "playground"
            / "grc_agent_interactive"
            / "dial_tone_interactive.grc"
        )
        agent = self._load_temp_agent_from_path(fixture)
        before_revision = agent.session.state_revision

        result = agent.execute_tool(
            "change_graph",
            {
                "dry_run": False,
                "op": "add_signal_source_to_sum",
                "user_goal": "add another signal source with frequency 1000",
                "state_revision": agent.session.state_revision,
                "args": {"block_id": "blocks_add_xx", "freq": 1000},
            },
        )

        self.assertFalse(result.get("ok"), result)
        self.assertFalse(result.get("committed"), result)
        self.assertEqual(result.get("error_type"), "invalid_request")
        self.assertIn("source block type", result.get("message", ""))
        self.assertIn("analog_sig_source_x", " ".join(result.get("clarification_options", [])))
        self.assertEqual(agent.session.state_revision, before_revision)

    def test_add_signal_source_to_sum_ignores_empty_nested_target_ref(self) -> None:
        fixture = (
            Path(__file__).resolve().parents[1]
            / "playground"
            / "grc_agent_interactive"
            / "dial_tone_interactive.grc"
        )
        agent = self._load_temp_agent_from_path(fixture)
        before_revision = agent.session.state_revision

        result = agent.execute_tool(
            "change_graph",
            {
                "dry_run": True,
                "op": "add_signal_source_to_sum",
                "user_goal": "preview another signal source with frequency 1000",
                "args": {
                    "block_id": "analog_sig_source_x",
                    "freq": 1000,
                    "target_ref": {},
                },
            },
        )

        self.assertTrue(result.get("ok"), result)
        self.assertFalse(result.get("committed"), result)
        self.assertEqual(agent.session.state_revision, before_revision)
        self.assertIn("blocks_add_xx.num_inputs:3->4", result.get("effects", []))

    def test_add_signal_source_to_sum_commit_requires_revision_guard(self) -> None:
        fixture = (
            Path(__file__).resolve().parents[1]
            / "playground"
            / "grc_agent_interactive"
            / "dial_tone_interactive.grc"
        )
        agent = self._load_temp_agent_from_path(fixture)
        before_revision = agent.session.state_revision

        result = agent.execute_tool(
            "change_graph",
            {
                "dry_run": False,
                "op": "add_signal_source_to_sum",
                "user_goal": "add another signal source with frequency 1000",
                "args": {"block_id": "analog_sig_source_x", "freq": 1000},
            },
        )

        self.assertFalse(result.get("ok"), result)
        self.assertFalse(result.get("committed"), result)
        self.assertEqual(result.get("error_type"), "invalid_request")
        self.assertIn("state_revision", result.get("message", ""))
        self.assertEqual(agent.session.state_revision, before_revision)

    def test_add_signal_source_to_sum_commit_requires_matching_preview_token(self) -> None:
        fixture = (
            Path(__file__).resolve().parents[1]
            / "playground"
            / "grc_agent_interactive"
            / "dial_tone_interactive.grc"
        )
        agent = self._load_temp_agent_from_path(fixture)
        before_revision = agent.session.state_revision

        result = agent.execute_tool(
            "change_graph",
            {
                "dry_run": False,
                "op": "add_signal_source_to_sum",
                "user_goal": "add another signal source with frequency 1000",
                "state_revision": agent.session.state_revision,
                "args": {"block_id": "analog_sig_source_x", "freq": 1000},
            },
        )

        self.assertFalse(result.get("ok"), result)
        self.assertFalse(result.get("committed"), result)
        self.assertEqual(result.get("error_type"), "invalid_request")
        self.assertIn("preview_token", result.get("message", ""))
        self.assertEqual(agent.session.state_revision, before_revision)

    def test_confirmed_preview_fills_structural_commit_guards_only(self) -> None:
        fixture = (
            Path(__file__).resolve().parents[1]
            / "playground"
            / "grc_agent_interactive"
            / "dial_tone_interactive.grc"
        )
        agent = self._load_temp_agent_from_path(fixture)
        preview = agent.execute_tool(
            "change_graph",
            {
                "dry_run": True,
                "op": "add_signal_source_to_sum",
                "user_goal": "preview another signal source with frequency 1000",
                "args": {"block_id": "analog_sig_source_x", "freq": 1000},
            },
        )
        self.assertTrue(preview.get("ok"), preview)
        preview_token = preview.get("preview_token")
        self.assertIsInstance(preview_token, str, preview)
        agent._turn_user_message = "commit it"

        normalized = agent.normalize_tool_call_arguments(
            "change_graph",
            {
                "dry_run": False,
                "op": "add_signal_source_to_sum",
                "user_goal": "commit the preview",
                "args": {"block_id": "analog_sig_source_x", "freq": 1000},
            },
            model_tool_call=True,
        )

        self.assertEqual(normalized.get("state_revision"), agent.session.state_revision)
        self.assertEqual(normalized.get("preview_token"), preview_token)
        self.assertEqual(normalized.get("args", {}).get("block_id"), "analog_sig_source_x")
        self.assertEqual(normalized.get("args", {}).get("freq"), 1000)

    def test_add_signal_source_to_sum_clarifies_conflicting_inherited_params(self) -> None:
        fixture = (
            Path(__file__).resolve().parents[1]
            / "playground"
            / "grc_agent_interactive"
            / "dial_tone_interactive.grc"
        )
        agent = self._load_temp_agent_from_path(fixture)
        assert agent.session.flowgraph is not None
        for block in agent.session.flowgraph.blocks:
            if block.instance_name == "analog_sig_source_x_1":
                block.params["parameters"]["waveform"] = "analog.GR_SIN_WAVE"
                break

        result = agent.execute_tool(
            "change_graph",
            {
                "dry_run": True,
                "op": "add_signal_source_to_sum",
                "user_goal": "preview another signal source with frequency 1000",
                "args": {"block_id": "analog_sig_source_x", "freq": 1000},
            },
        )

        self.assertFalse(result.get("ok"), result)
        self.assertEqual(result.get("error_type"), "clarification_required")
        self.assertIn("waveform", result.get("message", ""))
        self.assertIn("analog.GR_SIN_WAVE", " ".join(result.get("clarification_options", [])))

    def test_add_signal_source_to_sum_rejects_non_additive_destination(self) -> None:
        fixture = (
            Path(__file__).resolve().parents[1]
            / "playground"
            / "grc_agent_interactive"
            / "dial_tone_interactive.grc"
        )
        agent = self._load_temp_agent_from_path(fixture)

        result = agent.execute_tool(
            "change_graph",
            {
                "dry_run": True,
                "op": "add_signal_source_to_sum",
                "user_goal": "preview another signal source with frequency 1000",
                "args": {
                    "block_id": "analog_sig_source_x",
                    "freq": 1000,
                    "dst_block": "audio_sink",
                },
            },
        )

        self.assertFalse(result.get("ok"), result)
        self.assertEqual(result.get("error_type"), "invalid_request")
        self.assertIn("additive", result.get("message", ""))


if __name__ == "__main__":
    unittest.main()
