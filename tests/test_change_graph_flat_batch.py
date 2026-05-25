"""Direct-content tests for the flat change_graph batch surface."""

from __future__ import annotations

from pathlib import Path
import shutil
import tempfile
import unittest

from grc_agent.agent import GrcAgent
from grc_agent.flowgraph_session import FlowgraphSession
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
                        "expected_params": {"value": "32000"},
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
                "update_variables": [
                    {
                        "instance_name": "samp_rate",
                        "value": "48000",
                        "expected_value": "32000",
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
        self.assertIn("update_variables", repair.get("change_graph_hint", ""))
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

    def test_remove_connected_block_auto_detaches_and_force_saves_invalid_working_copy(self) -> None:
        tmp, path, agent = self._load_temp_agent()
        self.addCleanup(tmp.cleanup)

        result = agent.execute_tool(
            "change_graph",
            {
                "remove_blocks": [
                    {
                        "instance_name": "qtgui_time_sink_x_0",
                        "block_id": "qtgui_time_sink_x",
                    }
                ],
                "force": True,
            },
            model_tool_call=True,
        )

        self.assertTrue(result["ok"], result)
        self.assertTrue(result["committed"], result)
        self.assertFalse(result["validation_ok"], result)
        self.assertIn("validation error appeared", result["message"])
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


if __name__ == "__main__":
    unittest.main()
