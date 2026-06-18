"""Direct-content tests for the flat change_graph batch surface."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from grc_agent.agent import GrcAgent
from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.runtime.tool_context import tool_history_content_as_text
from grc_agent.session_ops import connection_id


class ChangeGraphFlatBatchTests(unittest.TestCase):
    def _fixture_path(self, name: str = "random_bit_generator.grc") -> Path:
        return Path(__file__).resolve().parent / "data" / name

    def _load_temp_agent(self, name: str = "random_bit_generator.grc") -> tuple[tempfile.TemporaryDirectory, Path, GrcAgent]:
        tmp = tempfile.TemporaryDirectory()
        src = self._fixture_path(name)
        dst = Path(tmp.name) / name
        shutil.copy2(src, dst)
        session = FlowgraphSession()
        session.load(dst)
        return tmp, dst, GrcAgent(session)

    @staticmethod
    def _param(session: FlowgraphSession, instance_name: str, key: str) -> str | None:
        assert session.flowgraph is not None
        for block in session.flowgraph.blocks:
            if block.instance_name == instance_name:
                params = block.params.get("parameters")
                if isinstance(params, dict) and key in params:
                    return str(params[key])
        return None

    @staticmethod
    def _block_names(session: FlowgraphSession) -> list[str]:
        assert session.flowgraph is not None
        return [block.instance_name for block in session.flowgraph.blocks]

    @staticmethod
    def _connection_ids(session: FlowgraphSession) -> list[str]:
        assert session.flowgraph is not None
        return [
            connection_id(conn.src_block, conn.src_port, conn.dst_block, conn.dst_port)
            for conn in session.flowgraph.connections
        ]

    def test_flat_update_params_commits_and_reloads_exact_values(self) -> None:
        tmp, path, agent = self._load_temp_agent()
        self.addCleanup(tmp.cleanup)

        result = agent.execute_tool(
            "change_graph",
            {
                "update_params": [
                    {
                        "instance_name": "samp_rate",
                        "params": {"value": "48000"},
                    }
                ]
            },
            model_tool_call=True,
        )

        self.assertTrue(result["ok"], result)
        self.assertTrue(result["committed"], result)
        self.assertEqual(self._param(agent.session, "samp_rate", "value"), "48000")

        reloaded = FlowgraphSession()
        reloaded.load(path)
        self.assertEqual(self._param(reloaded, "samp_rate", "value"), "48000")

    def test_flat_update_variables_commits_and_reloads_exact_values(self) -> None:
        tmp, path, agent = self._load_temp_agent()
        self.addCleanup(tmp.cleanup)

        result = agent.execute_tool(
            "change_graph",
            {
                "update_params": [
                    {
                        "instance_name": "samp_rate",
                        "params": {"value": "48000"},
                    }
                ]
            },
            model_tool_call=True,
        )

        self.assertTrue(result["ok"], result)
        self.assertTrue(result["committed"], result)
        self.assertEqual(self._param(agent.session, "samp_rate", "value"), "48000")

        reloaded = FlowgraphSession()
        reloaded.load(path)
        self.assertEqual(self._param(reloaded, "samp_rate", "value"), "48000")

    def test_bad_param_shape_is_rejected_before_mutation_with_actionable_hint(self) -> None:
        tmp, _path, agent = self._load_temp_agent()
        self.addCleanup(tmp.cleanup)
        before = self._param(agent.session, "samp_rate", "value")

        result = agent.execute_tool(
            "change_graph",
            {
                "update_params": [
                    {
                        "block_id": "control variable: samp_rate",
                        "params": {"samp_rate": 48000},
                    }
                ]
            },
            model_tool_call=True,
        )

        self.assertFalse(result["ok"], result)
        self.assertFalse(result.get("committed", False), result)
        self.assertEqual(result["error_type"], "tool_call_invalid")
        self.assertEqual(self._param(agent.session, "samp_rate", "value"), before)
        repair = result.get("schema_repair_instruction", {})
        self.assertIn("update_params[0].instance_name", repair.get("missing_fields", []))

    def test_flat_add_block_with_params_and_connection_commits_exact_graph(self) -> None:
        tmp, path, agent = self._load_temp_agent()
        self.addCleanup(tmp.cleanup)

        result = agent.execute_tool(
            "change_graph",
            {
                "add_blocks": [
                    {
                        "block_id": "blocks_null_sink",
                        "instance_name": "blocks_null_sink_0",
                        "params": {"type": "float", "vlen": "1"},
                    }
                ],
                "add_connections": [
                    {
                        "src": {"block": "blocks_char_to_float_0", "port": 0},
                        "dst": {"block": "blocks_null_sink_0", "port": 0},
                    }
                ],
            },
            model_tool_call=True,
        )

        self.assertTrue(result["ok"], result)
        self.assertTrue(result["committed"], result)
        self.assertIn("blocks_null_sink_0", self._block_names(agent.session))
        self.assertEqual(self._param(agent.session, "blocks_null_sink_0", "type"), "float")
        self.assertIn(
            "blocks_char_to_float_0:0->blocks_null_sink_0:0",
            self._connection_ids(agent.session),
        )

        reloaded = FlowgraphSession()
        reloaded.load(path)
        self.assertIn("blocks_null_sink_0", self._block_names(reloaded))
        self.assertIn(
            "blocks_char_to_float_0:0->blocks_null_sink_0:0",
            self._connection_ids(reloaded),
        )

    def test_failed_add_block_connection_returns_flat_dtype_repair_hint(self) -> None:
        tmp, _path, agent = self._load_temp_agent()
        self.addCleanup(tmp.cleanup)
        before_blocks = self._block_names(agent.session)
        before_connections = self._connection_ids(agent.session)

        result = agent.execute_tool(
            "change_graph",
            {
                "add_blocks": [
                    {
                        "block_id": "blocks_null_sink",
                        "instance_name": "blocks_null_sink_0",
                    }
                ],
                "add_connections": [
                    {
                        "src": {"block": "blocks_char_to_float_0", "port": 0},
                        "dst": {"block": "blocks_null_sink_0", "port": 0},
                    }
                ],
            },
            model_tool_call=True,
        )
        rendered = tool_history_content_as_text(
            result,
            tool_name="change_graph",
            semantic_search_result_preview=lambda _results: [],
        )

        self.assertFalse(result["ok"], result)
        self.assertFalse(result.get("committed", True), result)
        self.assertEqual(self._block_names(agent.session), before_blocks)
        self.assertEqual(self._connection_ids(agent.session), before_connections)
        self.assertTrue(result.get("hint"), result)
        self.assertIn("Source IO type", rendered)
        self.assertIn("float", rendered)
        self.assertIn("complex", rendered)

    def test_native_validation_failure_reports_unchanged_graph_facts(self) -> None:
        tmp, _path, agent = self._load_temp_agent()
        self.addCleanup(tmp.cleanup)
        before_blocks = self._block_names(agent.session)
        before_connections = self._connection_ids(agent.session)
        before_revision = agent.session.state_revision
        before_dirty = agent.session.is_dirty

        result = agent.execute_tool(
            "change_graph",
            {
                "update_states": [
                    {
                        "instance_name": "qtgui_time_sink_x_0",
                        "state": "disabled",
                    }
                ]
            },
            model_tool_call=True,
        )
        rendered = tool_history_content_as_text(
            result,
            tool_name="change_graph",
            semantic_search_result_preview=lambda _results: [],
        )

        self.assertFalse(result["ok"], result)
        self.assertFalse(result.get("committed", True), result)
        self.assertEqual(result.get("error_type"), "gnu_validation_failed")
        self.assertTrue(result.get("graph_unchanged"), result)
        self.assertEqual(result.get("rollback"), "complete")
        self.assertEqual(result.get("rejected_phase"), "native_grc_validation")
        self.assertIn("Source - out(0): Port is not connected.", result.get("native_validation_errors", []))
        self.assertEqual(self._block_names(agent.session), before_blocks)
        self.assertEqual(self._connection_ids(agent.session), before_connections)
        self.assertEqual(agent.session.state_revision, before_revision)
        self.assertEqual(agent.session.is_dirty, before_dirty)
        self.assertIn("changes not committed", rendered.lower())
        self.assertIn('"graph_unchanged":true', rendered)
        self.assertIn('"rollback":"complete"', rendered)
        self.assertIn("Source - out(0): Port is not connected.", rendered)

    def test_change_graph_render_keeps_all_non_empty_fields(self) -> None:
        payload = {
            "ok": True,
            "committed": True,
            "state_revision": 2,
            "checkpoint_id": "cp-abc",
            "graph_delta": {"added": ["samp_rate"]},
            "effect": "set samp_rate.value=96000",
        }
        rendered = tool_history_content_as_text(
            payload,
            tool_name="change_graph",
            semantic_search_result_preview=lambda _results: [],
        )
        self.assertIn("checkpoint_id", rendered)
        self.assertIn("graph_delta", rendered)
        self.assertIn("cp-abc", rendered)

    def test_same_batch_connection_can_reference_unique_added_block_type_alias(self) -> None:
        tmp, _path, agent = self._load_temp_agent()
        self.addCleanup(tmp.cleanup)

        result = agent.execute_tool(
            "change_graph",
            {
                "add_blocks": [
                    {
                        "block_id": "blocks_null_sink",
                        "instance_name": "null_sink_1",
                        "params": {"type": "float", "vlen": "1"},
                    }
                ],
                "add_connections": [
                    {
                        "src": {"block": "blocks_char_to_float_0", "port": 0},
                        "dst": {"block": "blocks_null_sink", "port": 0},
                    }
                ],
            },
            model_tool_call=True,
        )

        self.assertTrue(result["ok"], result)
        self.assertTrue(result["committed"], result)
        self.assertIn(
            "blocks_char_to_float_0:0->null_sink_1:0",
            self._connection_ids(agent.session),
        )

    def test_remove_connected_block_auto_detaches_and_force_saves_invalid_working_copy(self) -> None:
        tmp, path, agent = self._load_temp_agent()
        self.addCleanup(tmp.cleanup)

        result = agent.execute_tool(
            "change_graph",
            {
                "remove_blocks": [
                    {
                        "instance_name": "qtgui_time_sink_x_0",
                    }
                ],
                "force": True,
            },
            model_tool_call=True,
        )

        self.assertTrue(result["ok"], result)
        self.assertTrue(result["committed"], result)
        self.assertFalse(result["validation_ok"], result)
        self.assertIn("validation warnings", result["message"])
        self.assertNotIn("qtgui_time_sink_x_0", self._block_names(agent.session))
        self.assertNotIn(
            "blocks_char_to_float_0:0->qtgui_time_sink_x_0:0",
            self._connection_ids(agent.session),
        )

        reloaded = FlowgraphSession()
        reloaded.load(path)
        self.assertNotIn("qtgui_time_sink_x_0", self._block_names(reloaded))
        self.assertNotIn(
            "blocks_char_to_float_0:0->qtgui_time_sink_x_0:0",
            self._connection_ids(reloaded),
        )

    def test_force_does_not_bypass_unknown_parameter(self) -> None:
        tmp, _path, agent = self._load_temp_agent()
        self.addCleanup(tmp.cleanup)
        before = self._param(agent.session, "samp_rate", "value")

        result = agent.execute_tool(
            "change_graph",
            {
                "update_params": [
                    {
                        "instance_name": "samp_rate",
                        "params": {"not_a_real_param": "48000"},
                    }
                ],
                "force": True,
            },
            model_tool_call=True,
        )

        self.assertFalse(result["ok"], result)
        self.assertFalse(result["committed"], result)
        self.assertEqual(self._param(agent.session, "samp_rate", "value"), before)
        self.assertIn("parameter_not_found", str(result.get("errors", [])))

    def test_update_states_accepts_disabled_boolean_alias(self) -> None:
        tmp, path, agent = self._load_temp_agent()
        self.addCleanup(tmp.cleanup)

        result = agent.execute_tool(
            "change_graph",
            {
                "update_states": [
                    {
                        "instance_name": "blocks_throttle2_0",
                        "state": "disabled",
                    }
                ],
                "force": True,
            },
            model_tool_call=True,
        )

        self.assertTrue(result["ok"], result)
        self.assertTrue(result["committed"], result)
        self.assertFalse(result["validation_ok"], result)

        reloaded = FlowgraphSession()
        reloaded.load(path)
        assert reloaded.flowgraph is not None
        state_by_name = {
            block.instance_name: block.params.get("states", {}).get("state")
            for block in reloaded.flowgraph.blocks
        }
        self.assertEqual(state_by_name["blocks_throttle2_0"], "disabled")

    def test_noop_state_update_returns_already_disabled_message(self) -> None:
        tmp, path, agent = self._load_temp_agent("random_bit_generator_dual_sink_sink1_disabled.grc")
        self.addCleanup(tmp.cleanup)
        before_sha = path.read_bytes()

        result = agent.execute_tool(
            "change_graph",
            {
                "update_states": [
                    {
                        "instance_name": "qtgui_time_sink_x_1",
                        "state": "disabled",
                    }
                ]
            },
            model_tool_call=True,
        )

        self.assertTrue(result["ok"], result)
        self.assertTrue(result["committed"], result)
        self.assertFalse(result.get("dirty"), result)
        self.assertEqual(result.get("message"), "already disabled; graph unchanged.")
        self.assertIn("skipped", result.get("autosave", {}))
        self.assertTrue(result["autosave"]["skipped"])
        self.assertEqual(path.read_bytes(), before_sha)

    def test_noop_param_update_returns_already_val_message(self) -> None:
        tmp, path, agent = self._load_temp_agent("random_bit_generator.grc")
        self.addCleanup(tmp.cleanup)
        before_sha = path.read_bytes()

        result = agent.execute_tool(
            "change_graph",
            {
                "update_params": [
                    {
                        "instance_name": "samp_rate",
                        "params": {"value": "32000"},
                    }
                ]
            },
            model_tool_call=True,
        )

        self.assertTrue(result["ok"], result)
        self.assertTrue(result["committed"], result)
        self.assertFalse(result.get("dirty"), result)
        self.assertEqual(result.get("message"), "already 32000; graph unchanged.")
        self.assertIn("skipped", result.get("autosave", {}))
        self.assertTrue(result["autosave"]["skipped"])
        self.assertEqual(path.read_bytes(), before_sha)


if __name__ == "__main__":
    unittest.main()
